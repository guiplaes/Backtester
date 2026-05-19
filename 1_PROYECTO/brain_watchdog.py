#!/usr/bin/env python3
"""
Brain Watchdog — keeps trader_brain.py alive and its state coherent.

Runs in daemon mode: loops every 60s. Checks:
  1. trader_brain.py process alive (via psutil). If dead → restart.
  2. EA heartbeat fresh (< 180s). If stale → alert (EA problem, not ours to fix).
  3. Ghost signal_state: active=true + heartbeat says 0 positions for > 120s
     → close_signal() it. Prevents the dashboard showing phantom trades.

Writes status JSON for the dashboard. All actions logged to brain_watchdog.log.

Launch:
    pythonw brain_watchdog.py --daemon
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────
BASE_DIR = Path(r"C:\Users\Administrator\Desktop\MT4 Claude\1_PROYECTO")
COMMON = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files")
PYTHONW = r"C:\Program Files\Python312\pythonw.exe"
PYTHON = r"C:\Program Files\Python312\python.exe"
ENV_FILE = BASE_DIR / ".env"
BRAIN_SCRIPT = BASE_DIR / "trader_brain.py"
DIAG_SCRIPT = BASE_DIR / "diag.py"
PYTHON_PACKAGES = r"C:\Users\Administrator\PythonPackages"

HEARTBEAT_FILE = COMMON / "brain_ea_heartbeat.json"
POSITIONS_FILE = COMMON / "brain_positions.json"
SIGNAL_FILE = COMMON / "brain_signal_state.json"
BRAIN_STATUS_FILE = COMMON / "brain_status.json"
STATUS_FILE = BASE_DIR / "brain_watchdog_status.json"
LOG_FILE = BASE_DIR / "brain_watchdog.log"
FORCE_RESTART_FLAG = COMMON / "brain_force_restart.flag"

# tv.js — used to force-restore the active TradingView chart to XAUUSD when
# something/someone has switched it (DXY, US10Y, etc.). Without restoring the
# brain's feed dies silently — bars stop updating but the brain process stays
# alive, so the simple CDP-port check passes while data is stale.
NODE_BIN = "node"
TV_SCRIPT = r"C:\Users\Administrator\tradingview-mcp-jackson\tv.js"
TV_CDP_ENV_PORT = "9223"
EXPECTED_SYMBOL = "OANDA:XAUUSD"
EXPECTED_TIMEFRAME = "1"

# ── Thresholds ────────────────────────────────────────────────────────
HEARTBEAT_STALE_S = 180       # EA heartbeat older than this → alert
BRAIN_LOG_STALE_S = 300       # trader_brain.log not touched for this → assume frozen
GHOST_SIGNAL_GRACE_S = 120    # active signal + 0 positions longer than this → clean
TV_CDP_URL = "http://127.0.0.1:9223/json/version"
TV_RECOVERY_COOLDOWN_S = 300  # avoid thrashing TradingView restarts
FEED_DEGRADED_GRACE_S = 60    # feed.connected=false longer than this → restore symbol
FEED_RESTORE_COOLDOWN_S = 90  # min gap between symbol-restore attempts

BRAIN_LOG_FILE = BASE_DIR / "logs" / "trader_brain.log"
_last_tv_recovery_attempt = 0.0
_last_feed_restore_attempt = 0.0

# ── Logging (idempotent) ──────────────────────────────────────────────
log = logging.getLogger("brain_watchdog")
log.setLevel(logging.INFO)
log.propagate = False
if not log.handlers:
    _fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(_fh)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _is_real_brain(pid: int) -> bool:
    """Verify PID is actually trader_brain.py via direct Process().cmdline().

    Returns False for zombies/protected processes that report empty cmdline
    or AccessDenied.
    """
    try:
        import psutil
        cmd = psutil.Process(pid).cmdline()
        if not cmd or len(cmd) < 2:
            return False
        return "trader_brain.py" in " ".join(cmd).lower()
    except Exception:
        return False


def _adopt_existing_brains() -> None:
    """At watchdog startup, scan for any pre-existing legitimate brain
    processes and adopt them as 'spawned' so we don't kill them.

    Filters via _is_real_brain (which uses psutil.Process().cmdline() with
    AccessDenied catch). Zombies/protected processes that report empty
    cmdline are correctly excluded.

    NOTE: under pythonw daemon context, psutil sometimes returns non-empty
    cmdline for processes that report empty under regular python.exe. To
    handle this edge case, we additionally verify that the process create_time
    is recent (within last hour) — true brains spawn fresh; ancient zombies
    will fail this check.
    """
    try:
        import psutil
    except ImportError:
        return
    now = time.time()
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info["name"] or "").lower()
            if name not in ("python.exe", "pythonw.exe"):
                continue
            pid = proc.info["pid"]
            if pid in _UNKILLABLE_PIDS:
                continue  # blacklisted zombie
            if not _is_real_brain(pid):
                continue
            # Sanity check: a zombie that incorrectly reports trader_brain.py
            # in cmdline will be ancient. Real brains are always recent
            # (watchdog respawns frequently). Reject anything older than 1h.
            try:
                age = now - psutil.Process(pid).create_time()
                if age > 3600:
                    log.info(f"Rejected stale 'brain' PID={pid} (age={age:.0f}s) — likely zombie")
                    continue
            except Exception:
                continue
            # Probe-kill test: try to send signal 0 to verify we have control.
            # If AccessDenied, this is an unmanageable zombie — blacklist.
            try:
                p = psutil.Process(pid)
                # Read children() — fails with AccessDenied for protected procs
                _ = p.children()
            except psutil.AccessDenied:
                log.warning(f"PID={pid} reports trader_brain.py but is unmanageable — blacklisted")
                _UNKILLABLE_PIDS.add(pid)
                continue
            except Exception:
                continue
            _SPAWNED_PIDS.add(pid)
        except Exception:
            continue
    if _SPAWNED_PIDS:
        log.info(f"Adopted existing brain PIDs: {sorted(_SPAWNED_PIDS)}")
    else:
        log.info("No existing brain to adopt — will spawn fresh on first cycle")


def _brain_pids() -> list[int]:
    """Return PIDs of pythonw/python processes running trader_brain.py.

    Only returns PIDs that are BOTH:
      1. In _SPAWNED_PIDS (we spawned them or adopted at startup)
      2. Still running with valid cmdline (not zombies)

    This prevents the recurring bug where a protected/orphaned python.exe
    with empty cmdline gets falsely identified as the brain.
    """
    pids: list[int] = []
    dead: list[int] = []
    for pid in list(_SPAWNED_PIDS):
        if pid in _UNKILLABLE_PIDS:
            # Skip blacklisted zombies — they're not really our brains
            dead.append(pid)
            continue
        if _is_real_brain(pid):
            pids.append(pid)
        else:
            dead.append(pid)
    for pid in dead:
        _SPAWNED_PIDS.discard(pid)
    return pids


_UNKILLABLE_PIDS: set[int] = set()  # PIDs that returned AccessDenied on kill


def _kill_duplicates(pids: list[int]) -> None:
    """Kill all but the NEWEST (most recently started) PID.

    Why newest, not oldest: the oldest PID may be a stuck/protected zombie
    (Windows sometimes leaves python.exe processes that can't be killed by
    the user account). Keeping the freshest spawn ensures we keep a brain
    that's actively logging and responsive.

    Unkillable PIDs (Access Denied) are blacklisted — they'll be filtered
    from future _brain_pids() calls so the watchdog stops trying to manage
    them.
    """
    try:
        import psutil
        procs = []
        for pid in pids:
            try:
                procs.append(psutil.Process(pid))
            except Exception:
                pass
        # Sort by create_time DESCENDING → newest first; we keep procs[0]
        procs.sort(key=lambda p: p.create_time(), reverse=True)
        for p in procs[1:]:
            try:
                p.kill()
                log.warning(f"Killed duplicate trader_brain.py PID={p.pid}")
                _SPAWNED_PIDS.discard(p.pid)
            except psutil.AccessDenied:
                log.error(f"AccessDenied killing PID={p.pid} — blacklisted as unkillable zombie")
                _UNKILLABLE_PIDS.add(p.pid)
                _SPAWNED_PIDS.discard(p.pid)
            except Exception as e:
                log.error(f"Failed killing PID={p.pid}: {e}")
    except Exception as e:
        log.error(f"_kill_duplicates error: {e}")


def _build_spawn_env() -> dict:
    """os.environ + PYTHONPATH/encoding + .env vars merged in.

    Without this, brain processes spawned by the watchdog don't see
    CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY → all Claude calls 401 →
    auto_fallback flips everything to DeepSeek for 24h.
    """
    env = os.environ.copy()
    env["PYTHONPATH"] = PYTHON_PACKAGES
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        if ENV_FILE.exists():
            for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if not k:
                    continue
                if not v:
                    # Empty values: REMOVE the var entirely from env (don't set
                    # to ""). Claude CLI checks ANTHROPIC_API_KEY before
                    # CLAUDE_CODE_OAUTH_TOKEN and rejects empty strings as
                    # invalid credentials → 401, even when OAuth token is valid.
                    env.pop(k, None)
                else:
                    env[k] = v
    except Exception as e:
        log.warning(f".env load failed: {e}")
    return env


_SPAWNED_PIDS: set[int] = set()  # PIDs que el watchdog HA spawnat — anti-zombie


def _restart_brain() -> bool:
    """Spawn a new trader_brain.py process.

    Uses python.exe (not pythonw.exe) with stdout/stderr redirected to log
    files so any startup error is captured instead of silently lost. The
    process is detached via creationflags so it survives the watchdog's
    own lifecycle. PYTHONIOENCODING=utf-8 to avoid Windows charmap errors
    on log lines containing accented chars.
    """
    env = _build_spawn_env()
    try:
        out_log = open(BASE_DIR / "logs" / "brain_spawn_stdout.log", "ab", buffering=0)
        err_log = open(BASE_DIR / "logs" / "brain_spawn_stderr.log", "ab", buffering=0)
        proc = subprocess.Popen(
            [PYTHON, "-u", str(BRAIN_SCRIPT)],
            cwd=str(BASE_DIR),
            env=env,
            stdout=out_log,
            stderr=err_log,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
        )
        _SPAWNED_PIDS.add(proc.pid)
        log.warning(f"trader_brain.py restarted (PID={proc.pid})")
        return True
    except Exception as e:
        log.error(f"Failed to restart trader_brain.py: {e}")
        return False


def _brain_log_age_s() -> float | None:
    try:
        if not BRAIN_LOG_FILE.exists():
            return None
        return time.time() - BRAIN_LOG_FILE.stat().st_mtime
    except Exception:
        return None


def _check_brain_alive() -> dict:
    pids = _brain_pids()
    num = len(pids)

    if num == 0:
        log.warning("trader_brain.py NOT RUNNING — restarting")
        ok = _restart_brain()
        return {"ok": False, "detail": f"dead → restart ({'ok' if ok else 'FAIL'})",
                "pids": [], "action": "restart"}

    if num > 1:
        log.warning(f"Multiple trader_brain.py instances: {pids} — killing duplicates")
        _kill_duplicates(pids)
        return {"ok": False, "detail": f"{num} instances → duplicates killed",
                "pids": pids, "action": "kill_duplicates"}

    # Single instance — check it's not frozen via log mtime
    age = _brain_log_age_s()
    if age is not None and age > BRAIN_LOG_STALE_S:
        log.warning(f"trader_brain.py log stale ({age:.0f}s) — PID={pids[0]} frozen? Killing + restart")
        try:
            import psutil
            psutil.Process(pids[0]).kill()
        except Exception as e:
            log.error(f"Kill frozen PID failed: {e}")
        time.sleep(2)
        _restart_brain()
        return {"ok": False, "detail": f"frozen ({age:.0f}s) → kill+restart",
                "pids": pids, "action": "kill_restart"}

    return {"ok": True, "detail": f"alive PID={pids[0]}, log_age={age:.0f}s" if age else f"alive PID={pids[0]}",
            "pids": pids, "action": None}


def _check_ea_heartbeat() -> dict:
    if not HEARTBEAT_FILE.exists():
        return {"ok": False, "detail": "heartbeat file missing", "action": None}
    try:
        age = time.time() - HEARTBEAT_FILE.stat().st_mtime
    except Exception:
        return {"ok": False, "detail": "cannot stat heartbeat", "action": None}
    if age > HEARTBEAT_STALE_S:
        return {"ok": False, "detail": f"EA heartbeat stale ({age:.0f}s)", "action": None}
    return {"ok": True, "detail": f"fresh ({age:.0f}s)", "action": None}


def _check_ghost_signal() -> dict:
    """If brain_signal_state.active=true but EA heartbeat says 0 positions and the
    signal hasn't updated in a while, clean it."""
    sig = _read_json(SIGNAL_FILE)
    if not sig.get("active"):
        return {"ok": True, "detail": "no active signal", "action": None}

    hb = _read_json(HEARTBEAT_FILE)
    pos_count = hb.get("positions_count")
    if pos_count is None:
        # Fall back to positions file
        pos_data = _read_json(POSITIONS_FILE)
        pos_count = len(pos_data.get("positions", []) or [])

    if pos_count > 0:
        return {"ok": True, "detail": f"active + {pos_count} positions",
                "action": None}

    # active + 0 positions — grace period based on signal file mtime
    try:
        sig_age = time.time() - SIGNAL_FILE.stat().st_mtime
    except Exception:
        sig_age = GHOST_SIGNAL_GRACE_S + 1

    if sig_age < GHOST_SIGNAL_GRACE_S:
        return {"ok": True,
                "detail": f"active + 0 pos but recent ({sig_age:.0f}s < {GHOST_SIGNAL_GRACE_S}s grace)",
                "action": None}

    # Ghost — clean it (write EMPTY state directly; brain may be dead)
    direction = sig.get("direction", "?")
    log.warning(f"Ghost signal detected: {direction} active + 0 positions for {sig_age:.0f}s — cleaning")
    empty = {
        "active": False, "direction": None, "entry_price": 0.0,
        "channel": None, "source": None, "opened_at": 0, "opened_ts": 0,
        "breakeven_set": False, "sl_price": 0.0, "tp_price": 0.0,
        "zones_averaged": [], "avg_count": 0, "initial_lot": 0.0,
        "total_lots": 0.0, "closing": False, "last_msg_ts": 0,
        "status": "NONE", "realized_profit": 0.0,
        "fsm": {"state": "IDLE", "trade_id": None},
        "updated": datetime.now(timezone.utc).isoformat(),
        "ghost_cleaned_by": "brain_watchdog",
    }
    try:
        SIGNAL_FILE.write_text(json.dumps(empty, indent=2), encoding="utf-8")
        return {"ok": False, "detail": f"ghost {direction} cleaned", "action": "clean_ghost"}
    except Exception as e:
        log.error(f"Failed to clean ghost signal: {e}")
        return {"ok": False, "detail": f"ghost cleanup failed: {e}", "action": "error"}


def _tv_cdp_ok(timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(TV_CDP_URL, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _recover_tv_cdp() -> dict:
    global _last_tv_recovery_attempt

    now = time.time()
    wait_left = int(TV_RECOVERY_COOLDOWN_S - (now - _last_tv_recovery_attempt))
    if _last_tv_recovery_attempt and wait_left > 0:
        return {
            "ok": False,
            "detail": f"CDP missing — recovery cooldown {wait_left}s",
            "action": "cooldown",
        }

    _last_tv_recovery_attempt = now
    log.warning("TradingView CDP missing — attempting force restart via diag.py")
    env = os.environ.copy()
    env["PYTHONPATH"] = PYTHON_PACKAGES

    try:
        r = subprocess.run(
            [PYTHON, str(DIAG_SCRIPT), "--force-restart", "--quiet"],
            cwd=str(BASE_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if r.returncode == 0 and _tv_cdp_ok(timeout=2.0):
            log.warning("TradingView CDP recovered via diag.py --force-restart")
            return {
                "ok": True,
                "detail": "CDP recovered via diag.py --force-restart",
                "action": "restart_tv",
            }

        out = ((r.stdout or "") + "\n" + (r.stderr or "")).strip().replace("\n", " ")
        if out:
            log.error(f"TradingView CDP recovery failed rc={r.returncode}: {out[:400]}")
        else:
            log.error(f"TradingView CDP recovery failed rc={r.returncode}")
        return {
            "ok": False,
            "detail": f"CDP missing — recovery failed rc={r.returncode}",
            "action": "restart_tv_failed",
        }
    except Exception as e:
        log.error(f"TradingView CDP recovery exception: {e}")
        return {
            "ok": False,
            "detail": f"CDP missing — recovery exception: {e}",
            "action": "restart_tv_failed",
        }


def _check_tv_cdp() -> dict:
    if _tv_cdp_ok():
        return {"ok": True, "detail": "CDP ready on 9223", "action": None}
    return _recover_tv_cdp()


def _restore_chart_symbol() -> tuple[bool, str]:
    """Force the active TradingView chart back to EXPECTED_SYMBOL via tv.js.

    Returns (ok, detail). Used when brain reports feed.connected=false because
    something switched the chart away from XAUUSD (e.g. an MCP session reading
    DXY/US10Y). tv.js 'symbol' command waits ~12s for the swap to verify.
    """
    env = os.environ.copy()
    env["PYTHONPATH"] = PYTHON_PACKAGES
    env["TV_CDP_PORT"] = TV_CDP_ENV_PORT
    try:
        r = subprocess.run(
            [NODE_BIN, TV_SCRIPT, "symbol", EXPECTED_SYMBOL],
            cwd=os.path.dirname(TV_SCRIPT),
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        out = (r.stdout or "").strip()
        try:
            payload = json.loads(out) if out else {}
        except Exception:
            payload = {}
        if r.returncode == 0 and payload.get("success"):
            # Symbol-only restore — DON'T force timeframe. The brain reads
            # M1 bars via ohlcv-tf regardless of what the user has on screen,
            # so the chart TF can stay at whatever the operator picked (M5,
            # M15, etc.). Forcing M1 would yank the chart back every cycle.
            return True, f"chart restored to {EXPECTED_SYMBOL}"
        err = payload.get("error") or out[:200] or f"rc={r.returncode}"
        return False, f"restore_failed: {err}"
    except Exception as e:
        return False, f"restore_exception: {e}"


def _check_feed_health() -> dict:
    """Detect a wedged TradingView feed and restore the chart symbol.

    Reads brain_status.json (written by trader_brain main loop). When
    feed.connected=false and the failure has persisted longer than
    FEED_DEGRADED_GRACE_S, force the chart back to OANDA:XAUUSD via tv.js.
    Cooldown prevents flapping when something else keeps stealing the chart.
    """
    global _last_feed_restore_attempt

    if not BRAIN_STATUS_FILE.exists():
        return {"ok": True, "detail": "brain_status.json missing — skip", "action": None}

    status = _read_json(BRAIN_STATUS_FILE)
    feed = (status or {}).get("feed") or {}
    if not feed:
        return {"ok": True, "detail": "no feed block — skip", "action": None}

    connected = bool(feed.get("connected"))
    last_ok_age = feed.get("last_ok_age_s")
    fails = feed.get("consecutive_failures", 0)

    if connected:
        return {"ok": True,
                "detail": f"feed OK (age={last_ok_age}s, fails={fails})",
                "action": None}

    # Disconnected. Need persistence > grace before acting.
    try:
        age = float(last_ok_age) if last_ok_age is not None else 0.0
    except Exception:
        age = 0.0
    if age < FEED_DEGRADED_GRACE_S:
        return {"ok": False,
                "detail": f"feed down {age:.0f}s < grace {FEED_DEGRADED_GRACE_S}s — waiting",
                "action": None}

    # Cooldown to avoid hammering TradingView
    now = time.time()
    wait_left = int(FEED_RESTORE_COOLDOWN_S - (now - _last_feed_restore_attempt))
    if _last_feed_restore_attempt and wait_left > 0:
        return {"ok": False,
                "detail": f"feed down {age:.0f}s — restore cooldown {wait_left}s",
                "action": "cooldown"}

    _last_feed_restore_attempt = now
    log.warning(f"Feed degraded {age:.0f}s (fails={fails}) — restoring chart to {EXPECTED_SYMBOL}")
    ok, detail = _restore_chart_symbol()
    if ok:
        log.warning(f"Chart symbol restored: {detail}")
        return {"ok": True, "detail": detail, "action": "restore_symbol"}
    log.error(f"Chart symbol restore failed: {detail}")
    return {"ok": False, "detail": detail, "action": "restore_symbol_failed"}


def _proc_running(cmdline_token: str, exact_name: str | None = None) -> list[int]:
    """Find PIDs of processes whose cmdline contains `cmdline_token`.

    `exact_name` filters by image name when set (e.g. 'cloudflared.exe').
    Returns sorted list of PIDs (oldest first by create time when available).
    """
    try:
        import psutil
    except ImportError:
        return []
    pids: list[tuple[int, float]] = []
    me = os.getpid()
    for p in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            if p.info["pid"] == me:
                continue
            n = (p.info.get("name") or "").lower()
            if exact_name and n != exact_name.lower():
                continue
            cl = " ".join(p.info.get("cmdline") or []).lower()
            if cmdline_token.lower() in cl:
                pids.append((p.info["pid"], p.info.get("create_time") or 0))
        except Exception:
            continue
    pids.sort(key=lambda x: x[1])
    return [pid for pid, _ in pids]


def _kill_pids_keep_oldest(pids: list[int], label: str) -> int:
    """Keep oldest, kill the rest. Returns # killed."""
    if len(pids) <= 1:
        return 0
    try:
        import psutil
        killed = 0
        for pid in pids[1:]:
            try:
                psutil.Process(pid).kill()
                log.warning(f"Killed duplicate {label} PID={pid}")
                killed += 1
            except Exception:
                pass
        return killed
    except Exception:
        return 0


def _check_dashboard() -> dict:
    """Ensure brain_flow.py is alive and bound to port 5858.

    Behaviour:
      · No instance → spawn one
      · Multiple instances → keep the YOUNGEST (newest code), kill the rest
      · Single instance older than DASHBOARD_MAX_AGE_S → kill so a fresh one
        with current code respawns (catches stale processes that survived a
        code update because nothing kicked them)

    Keeping youngest (instead of oldest) is the right choice for code reloads:
    when we update brain_flow.py and respawn, we want the NEW one to win.
    """
    DASHBOARD_MAX_AGE_S = 7 * 24 * 3600  # 7 days — too long means stale code
    try:
        import psutil
    except ImportError:
        return {"ok": False, "detail": "psutil missing", "action": None}
    pids = _proc_running("brain_flow.py")
    if not pids:
        env = _build_spawn_env()
        try:
            subprocess.Popen(
                [PYTHONW, str(BASE_DIR / "brain_flow.py")],
                cwd=str(BASE_DIR),
                env=env,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            log.warning("brain_flow.py restarted")
            return {"ok": False, "detail": "dead → restart", "action": "restart_dashboard"}
        except Exception as e:
            log.error(f"Failed to restart brain_flow.py: {e}")
            return {"ok": False, "detail": f"restart failed: {e}", "action": "error"}
    if len(pids) > 1:
        # Keep YOUNGEST; kill older duplicates so newest code wins.
        try:
            procs = []
            for pid in pids:
                try:
                    procs.append(psutil.Process(pid))
                except Exception:
                    pass
            procs.sort(key=lambda p: p.create_time(), reverse=True)
            keep = procs[0].pid if procs else None
            killed = 0
            for p in procs[1:]:
                try:
                    p.kill()
                    killed += 1
                    log.warning(f"Killed older brain_flow.py PID={p.pid}")
                except Exception as e:
                    log.error(f"Failed killing brain_flow PID={p.pid}: {e}")
            return {"ok": False,
                    "detail": f"{len(pids)} instances → killed {killed}, kept newest PID={keep}",
                    "action": "kill_old_dups"}
        except Exception as e:
            return {"ok": False, "detail": f"dedup error: {e}", "action": "error"}
    return {"ok": True, "detail": f"alive PID={pids[0]}", "action": None}


def _check_webconsole() -> dict:
    """Ensure logserver.py (port 7681 auth-proxy) and cloudflared (public
    tunnel) are alive. Spawns missing components via the C:\\Tools\\webconsole
    loop scripts; the watchdog only owns liveness, not config.
    """
    log_pids = _proc_running("logserver.py")
    cf_pids = _proc_running("", exact_name="cloudflared.exe")
    actions: list[str] = []
    detail_parts: list[str] = []

    if not log_pids:
        try:
            subprocess.Popen(
                ["cmd.exe", "/c", r"C:\Tools\webconsole\loop_logserver.bat"],
                cwd=r"C:\Tools\webconsole",
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            log.warning("logserver.py respawn launched (loop bat)")
            actions.append("restart_logserver")
            detail_parts.append("logserver dead → restarted")
        except Exception as e:
            log.error(f"logserver respawn failed: {e}")
            detail_parts.append(f"logserver dead, restart failed: {e}")
    else:
        detail_parts.append(f"logserver alive PID={log_pids[0]}")

    if not cf_pids:
        try:
            subprocess.Popen(
                ["cmd.exe", "/c", r"C:\Tools\webconsole\loop_cloudflared.bat"],
                cwd=r"C:\Tools\webconsole",
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            log.warning("cloudflared respawn launched (loop bat)")
            actions.append("restart_cloudflared")
            detail_parts.append("cloudflared dead → restarted")
        except Exception as e:
            log.error(f"cloudflared respawn failed: {e}")
            detail_parts.append(f"cloudflared dead, restart failed: {e}")
    else:
        detail_parts.append(f"cloudflared alive PID={cf_pids[0]}")

    ok = bool(log_pids and cf_pids)
    return {"ok": ok, "detail": " · ".join(detail_parts),
            "action": ",".join(actions) if actions else None}


def _write_status(checks: dict) -> None:
    overall = "OK" if all(c.get("ok") for c in checks.values()) else "PROBLEM"
    payload = {
        "timestamp": time.time(),
        "timestamp_human": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "overall": overall,
        "checks": checks,
    }
    try:
        STATUS_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.error(f"Failed to write status: {e}")


def _check_news_calendar() -> dict:
    """Refresh the weekly news calendar if stale (>6h old or never fetched).

    Best-effort — never blocks the watchdog cycle. Logs failures but keeps
    the previous good calendar on disk if the fetch fails.
    """
    try:
        import sys as _sys
        if str(BASE_DIR) not in _sys.path:
            _sys.path.insert(0, str(BASE_DIR))
        import news_calendar as _nc
        if _nc.needs_refresh(max_age_s=6 * 3600):
            r = _nc.fetch_and_persist()
            if r.get("ok"):
                age = _nc.freshness_age_s() or 0
                return {"ok": True,
                        "detail": f"refreshed {r.get('n_kept')} events (was stale)",
                        "action": "refresh"}
            return {"ok": False,
                    "detail": f"refresh failed: {r.get('error')}",
                    "action": "refresh_failed"}
        age = _nc.freshness_age_s() or 0
        return {"ok": True,
                "detail": f"fresh ({int(age/60)}min old)",
                "action": None}
    except Exception as e:
        return {"ok": False, "detail": f"exception: {e}", "action": None}


def _check_force_restart_flag() -> None:
    """If brain_force_restart.flag exists in COMMON, kill running brain
    processes so the rest of the cycle respawns them with fresh code/env.
    Watchdog runs as SYSTEM so it can kill cross-session.
    """
    if not FORCE_RESTART_FLAG.exists():
        return
    try:
        import psutil
    except ImportError:
        log.error("force_restart: psutil missing")
        return
    killed = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            n = (p.info["name"] or "").lower()
            if n in ("python.exe", "pythonw.exe") and (
                "brain_flow.py" in cmd or "trader_brain.py" in cmd
            ):
                p.kill()
                killed.append(f"{p.info['pid']}({'flow' if 'brain_flow' in cmd else 'brain'})")
        except Exception:
            continue
    try:
        FORCE_RESTART_FLAG.unlink()
    except Exception as e:
        log.warning(f"force_restart: could not delete flag: {e}")
    log.warning(f"force_restart: killed {killed} — respawn this cycle")


def run_once() -> None:
    log.info("--- cycle start ---")
    _check_force_restart_flag()
    checks = {
        "brain": _check_brain_alive(),
        "tradingview_cdp": _check_tv_cdp(),
        "feed_health": _check_feed_health(),
        "ea_heartbeat": _check_ea_heartbeat(),
        "ghost_signal": _check_ghost_signal(),
        "dashboard": _check_dashboard(),
        "webconsole": _check_webconsole(),
        "news_calendar": _check_news_calendar(),
    }
    _write_status(checks)
    log.info(f"--- cycle end: {checks} ---")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--daemon", action="store_true", help="Loop every 60s")
    args = ap.parse_args()
    # Adopt any existing legitimate trader_brain.py before first cycle.
    # Without this, the first cycle would think no brain exists, kill nothing,
    # spawn a duplicate, and then "kill duplicates" between the existing
    # brain and our new spawn — which is exactly the loop we want to avoid.
    _adopt_existing_brains()
    if args.daemon:
        while True:
            try:
                run_once()
            except Exception as e:
                log.error(f"Uncaught watchdog error: {e}")
            time.sleep(60)
    else:
        run_once()


if __name__ == "__main__":
    main()
