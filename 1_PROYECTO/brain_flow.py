"""Brain Flow - live architecture diagram for the Trader Brain.

Serves an SSE stream of activity events + state snapshots, plus a single-page
SVG dashboard at http://localhost:5858/.

Sources of truth (read-only, never writes):
  - logs/trader_brain.log          (tailed; regex-detects LLM phase transitions)
  - Common/Files/brain_zone_state.json
  - Common/Files/brain_signal_state.json
  - Common/Files/brain_ea_heartbeat.json
  - Common/Files/brain_trade_history.json
  - Common/Files/brain_executor_decisions.jsonl
  - Common/Files/brain_events_log.jsonl
  - active_signal.json (1_PROYECTO)

No Flask, no external deps: just stdlib.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────
HOST = "127.0.0.1"
PORT = 5858
HERE = Path(__file__).parent
COMMON = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files")
LOG_FILE = HERE / "logs" / "trader_brain.log"
HTML_FILE = HERE / "brain_trader.html"
HTML_FLOW_FILE = HERE / "brain_flow.html"
HTML_NARRATIVES_FILE = HERE / "brain_narratives.html"

# ──────────────────────────────────────────────────────────────────────
# Pub-sub
# ──────────────────────────────────────────────────────────────────────
_subs: list[queue.Queue] = []
_subs_lock = threading.Lock()


def broadcast(ev: dict) -> None:
    msg = "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
    with _subs_lock:
        dead = []
        for q in _subs:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            try:
                _subs.remove(q)
            except ValueError:
                pass


# ──────────────────────────────────────────────────────────────────────
# Log tailer — detects LLM phases + emits component/edge pulse events
# ──────────────────────────────────────────────────────────────────────
def _events_from_log(line: str) -> list[dict]:
    """Map a log line to a list of stream events."""
    out: list[dict] = []

    # INDICATOR lifecycle — match any provider (Claude/DeepSeek/etc.)
    if "[INDICATOR] Calling" in line or "[INDICATOR] Running" in line:
        out.append({"type": "component", "id": "indicator", "state": "working"})
        out.append({"type": "edge", "from": "tradingview", "to": "indicator"})
    elif "[INDICATOR]" in line and "still working" in line:
        # Heartbeat — keep the node lit while LLM is processing
        out.append({"type": "component", "id": "indicator", "state": "working"})
    elif "[INDICATOR]" in line and "responded" in line:
        out.append({"type": "component", "id": "indicator", "state": "done"})
        out.append({"type": "edge", "from": "indicator", "to": "reviewer"})
    elif "[INDICATOR]" in line and "post-review" in line:
        m = re.search(r"(\d+)\s+active zones", line)
        if m:
            out.append({"type": "kpi_hint", "zones_count": int(m.group(1))})

    # ZONE_REVIEWER lifecycle — match any provider (Claude/DeepSeek/etc.)
    elif "[ZONE_REVIEWER] Calling" in line:
        out.append({"type": "component", "id": "reviewer", "state": "working"})
    elif "[ZONE_REVIEWER]" in line and "responded" in line:
        out.append({"type": "component", "id": "reviewer", "state": "done"})
        out.append({"type": "edge", "from": "reviewer", "to": "zone_state"})
    elif "[ZONE_REVIEWER] regime change detected" in line:
        out.append({"type": "flash", "id": "reviewer", "kind": "regime_change"})
    elif "[ZONE_REVIEWER] coverage_gap=" in line:
        out.append({"type": "flash", "id": "reviewer", "kind": "coverage_gap"})

    # EXECUTOR lifecycle — match any provider (Claude/DeepSeek/etc.)
    elif "[EXECUTOR] Calling" in line:
        out.append({"type": "component", "id": "executor", "state": "working"})
        out.append({"type": "edge", "from": "event_detector", "to": "executor"})
    elif "[EXECUTOR]" in line and "still working" in line:
        out.append({"type": "component", "id": "executor", "state": "working"})
    elif "[EXECUTOR]" in line and "responded" in line:
        out.append({"type": "component", "id": "executor", "state": "done"})
    elif re.search(r"\[EXECUTOR\]\s+(AVERAGE|PARTIAL_CLOSE|MOVE_SL_BE|WAIT|ALERT)", line):
        m = re.search(r"\[EXECUTOR\]\s+(\w+)\s+\(conf=(\d+)%?\)", line)
        if m:
            out.append({"type": "executor_action", "action": m.group(1), "conf": int(m.group(2))})
        if "AVERAGE" in line or "PARTIAL_CLOSE" in line or "MOVE_SL_BE" in line:
            out.append({"type": "edge", "from": "executor", "to": "validator"})

    # VALIDATOR
    elif "[VALIDATOR] REJECTED" in line:
        out.append({"type": "flash", "id": "validator", "kind": "reject"})
        out.append({"type": "edge", "from": "validator", "to": "executor", "kind": "reject"})
    elif "[VALIDATOR]" in line:
        out.append({"type": "component", "id": "validator", "state": "working"})
        out.append({"type": "component", "id": "validator", "state": "done"})

    # TG
    elif re.search(r"\[TG\].+Connected to channel", line):
        out.append({"type": "component", "id": "telegram", "state": "connected"})
    elif re.search(r"\[TG\].+(new signal|signal detected|NUEVA SEÑAL|señal)", line, re.I):
        out.append({"type": "edge", "from": "telegram", "to": "signal_state"})
        out.append({"type": "flash", "id": "telegram", "kind": "signal"})
    elif re.search(r"\[TG\].+cerramos", line, re.I):
        out.append({"type": "edge", "from": "telegram", "to": "signal_state"})
        out.append({"type": "flash", "id": "signal_state", "kind": "closing"})

    # Zones
    elif "Zones updated:" in line:
        out.append({"type": "edge", "from": "reviewer", "to": "zone_state"})
    elif "Drew" in line and "shapes on chart" in line:
        out.append({"type": "edge", "from": "zone_state", "to": "tradingview"})

    # Zone lifecycle
    elif "[ZONE_LIFECYCLE]" in line or "touched" in line.lower() and "zone" in line.lower():
        out.append({"type": "component", "id": "zone_lifecycle", "state": "working"})
        out.append({"type": "component", "id": "zone_lifecycle", "state": "done"})

    # EA heartbeat & orders
    elif "send_market" in line or "[OPEN]" in line:
        out.append({"type": "edge", "from": "validator", "to": "ea_mt5"})
        out.append({"type": "flash", "id": "ea_mt5", "kind": "order"})
    elif "close_ticket" in line or "[CLOSE]" in line:
        out.append({"type": "edge", "from": "executor", "to": "ea_mt5"})

    # TV degraded
    elif "tv(" in line and "timeout" in line:
        out.append({"type": "flash", "id": "tradingview", "kind": "timeout"})

    # Hunter
    elif "[HUNTER]" in line and "Calling" in line:
        out.append({"type": "component", "id": "hunter", "state": "working"})
    elif "[HUNTER]" in line and "still working" in line:
        out.append({"type": "component", "id": "hunter", "state": "working"})
    elif "[HUNTER]" in line and "responded" in line:
        out.append({"type": "component", "id": "hunter", "state": "done"})

    return out


def _replay_recent_log(subscribers_q: queue.Queue | None = None, n: int = 120) -> None:
    """Read the last ~n lines of the log and broadcast the events they imply,
    so a client connecting now sees 'what just happened' immediately."""
    if not LOG_FILE.exists():
        return
    try:
        with open(LOG_FILE, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk = min(size, 64 * 1024)
            f.seek(size - chunk)
            data = f.read().decode("utf-8", errors="replace")
        lines = data.splitlines()[-n:]
        for line in lines:
            for ev in _events_from_log(line):
                # Mark as replay so UI can optionally handle differently
                ev["replay"] = True
                if subscribers_q is not None:
                    try:
                        subscribers_q.put_nowait("data: " + json.dumps(ev) + "\n\n")
                    except queue.Full:
                        pass
                else:
                    broadcast(ev)
    except Exception:
        pass


def tail_log_loop() -> None:
    if not LOG_FILE.exists():
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.touch()
    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
        f.seek(0, 2)  # end
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.3)
                continue
            line = line.rstrip("\n")
            for ev in _events_from_log(line):
                broadcast(ev)
            # Activity log — keep raw line for the scrolling footer
            ts_match = re.match(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", line)
            ts = ts_match.group(1)[-8:] if ts_match else ""
            # Strip timestamp+level from the line for readability
            clean = re.sub(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d+\s+\[\w+\]\s+", "", line)
            broadcast({"type": "log", "ts": ts, "line": clean[:220]})


# ──────────────────────────────────────────────────────────────────────
# State poller — reads JSON files, emits compact KPI snapshots
# ──────────────────────────────────────────────────────────────────────
def _safe_json(path: Path) -> dict | None:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _last_jsonl(path: Path) -> dict | None:
    """Return the last valid JSON object from a JSONL file (newest line).

    Adaptive chunking: starts with 64KB and doubles up to 2MB until a
    parseable line is found. Executor decisions can carry 10-20KB of
    reasoning text per entry, so a fixed 4KB tail (the previous default)
    routinely cut entries mid-line and made the dashboard show "Cap decisió
    encara" even when fresh decisions existed.
    """
    try:
        if not path.exists():
            return None
        size = path.stat().st_size
        if size <= 0:
            return None
        for chunk in (65536, 262144, 1048576, 2097152):
            with open(path, "rb") as f:
                f.seek(max(0, size - chunk))
                data = f.read().decode("utf-8", errors="replace")
            for ln in reversed(data.strip().split("\n")):
                ln = ln.strip()
                if not ln or not ln.startswith("{"):
                    continue
                try:
                    return json.loads(ln)
                except Exception:
                    continue
            # Couldn't find a parseable line in this window; widen.
            if chunk >= size:
                break
    except Exception:
        return None
    return None


def _iso_age_sec(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        if iso.endswith("Z"):
            iso = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return None


def _file_age_s(path: Path) -> float | None:
    """Age in seconds since the file was last written (real UTC epoch).

    The EA writes heartbeat/positions with `ts = TimeCurrent()` which is broker
    server time (often UTC+3), so `time.time() - ts` yields a bogus TZ-shifted
    age. File mtime is set by the OS in real UTC epoch and is the only reliable
    freshness signal across writer-reader TZ differences.
    """
    try:
        if not path.exists():
            return None
        return max(0.0, time.time() - path.stat().st_mtime)
    except Exception:
        return None


def build_snapshot() -> dict:
    snap: dict = {}

    zs = _safe_json(COMMON / "brain_zone_state.json")
    if zs:
        # Only ACTIVE zones — STALE/INVALIDATED should not appear in the dashboard
        # (chart also skips them via draw_reasoning filter). Keeping parity.
        active_only = [z for z in zs.get("zones", []) if z.get("status") == "ACTIVE"]
        snap["zones"] = {
            "count": len(active_only),
            "bias": zs.get("bias"),
            "regime": zs.get("regime"),
            "coverage": zs.get("coverage"),
            "coverage_gap": zs.get("coverage_gap"),
            "updated_age_s": _iso_age_sec(zs.get("updated_at")),
            "zones_preview": [
                {
                    "price": z.get("price"),
                    "type": z.get("type"),
                    "strength": z.get("strength"),
                    "region": z.get("region"),
                    "bounce": z.get("bounce_direction") or z.get("bounce"),
                    "touches": z.get("touches", 0),
                }
                for z in active_only[:10]  # up to 10 shown (supports new 5+5 coverage)
            ],
            # NEW 2026-05-04: expose new INDICATOR fields for dashboard display
            "directional_commitment": zs.get("directional_commitment"),
            "working_range": zs.get("working_range"),
            "asymmetric_risk": zs.get("asymmetric_risk"),
        }

    ss = _safe_json(COMMON / "brain_signal_state.json")
    if ss:
        # Expose Executor's planned BE trigger (price + reasoning) so the
        # dashboard can show the level even before BE actually fires.
        _ep_for_signal = ss.get("executor_plan") or {}
        _be_trigger = _ep_for_signal.get("breakeven_trigger") if isinstance(_ep_for_signal, dict) else None
        snap["signal"] = {
            "active": bool(ss.get("active", ss.get("direction"))),
            "direction": ss.get("direction"),
            "entry_price": ss.get("entry_price"),
            "breakeven_set": ss.get("breakeven_set"),
            "breakeven_pending": ss.get("breakeven_pending"),
            "breakeven_trigger": _be_trigger,
            "closing": ss.get("closing") or ss.get("flag_closing"),
            "channel": ss.get("channel"),
            "avg_count": ss.get("avg_count", 0),
            "zones_averaged": [
                {"price": z.get("price"), "lot": z.get("lot")}
                for z in (ss.get("zones_averaged") or [])
            ],
            # 2026-05-04: Mode Recorregut Institucional — exposar auto_close_conditions
            # i tesis qualitatives perquè el dashboard les mostri en viu.
            "executor_plan": {
                "mode":                  _ep_for_signal.get("mode") if isinstance(_ep_for_signal, dict) else None,
                "trap_thesis":           _ep_for_signal.get("trap_thesis") if isinstance(_ep_for_signal, dict) else None,
                "tp_thesis":             _ep_for_signal.get("tp_thesis") if isinstance(_ep_for_signal, dict) else None,
                "invalidation_thesis":   _ep_for_signal.get("invalidation_thesis") if isinstance(_ep_for_signal, dict) else None,
                "tp_target":             _ep_for_signal.get("tp_target") if isinstance(_ep_for_signal, dict) else None,
                "auto_close_conditions": _ep_for_signal.get("auto_close_conditions") if isinstance(_ep_for_signal, dict) else None,
            } if isinstance(_ep_for_signal, dict) else None,
        }
        # Zones consumed by profit capture this signal — for dashboard.
        # Read FastEngine state file (persists per-signal via _load_state).
        try:
            import json as _j
            _fe = _j.load(open(COMMON / "brain_fastengine_state.json", "r", encoding="utf-8"))
            snap["signal"]["zones_partialed"] = [
                {"price": float(k), "ts": v}
                for k, v in (_fe.get("zones_partialed") or {}).items()
            ]
        except Exception:
            snap["signal"]["zones_partialed"] = []

    hb_path = COMMON / "brain_ea_heartbeat.json"
    hb = _safe_json(hb_path)
    hb_age = _file_age_s(hb_path)

    # Positions + account from brain_positions.json is the source of truth for
    # current price, blend, lots, floating P&L. Heartbeat has balance/equity
    # but not per-position detail.
    pos_file = _safe_json(COMMON / "brain_positions.json")
    positions = pos_file.get("positions", []) if pos_file else []
    account_pos = pos_file.get("account", {}) if pos_file else {}

    # Compute blend / total_lots / current_price / floating from positions
    total_lot = 0.0
    weighted_sum = 0.0
    total_floating = 0.0
    current_price = None
    for p in positions:
        vol = float(p.get("volume", 0) or 0)
        open_p = float(p.get("price_open", 0) or 0)
        profit = float(p.get("profit", 0) or 0)
        cur = float(p.get("price_current", 0) or 0)
        total_lot += vol
        weighted_sum += vol * open_p
        total_floating += profit
        if cur > 0 and current_price is None:
            current_price = cur  # all tickets share the same quote
    blend = (weighted_sum / total_lot) if total_lot > 0 else None

    if hb or positions:
        snap["ea"] = {
            "timestamp": datetime.fromtimestamp(hb_path.stat().st_mtime, timezone.utc).isoformat()
                         if hb and hb_age is not None else None,
            "age_s": hb_age,
            "balance": (hb.get("balance") if hb else None) or account_pos.get("balance"),
            "equity": (hb.get("equity") if hb else None) or account_pos.get("equity"),
            "dd_pct": (hb.get("dd_current_pct") if hb else None) or (hb.get("dd_pct") if hb else None) or account_pos.get("dd_pct"),
            "floating": total_floating if positions else (hb.get("floating_profit") if hb else None),
            "positions": len(positions),
            "price": current_price or (hb.get("price") if hb else None) or (hb.get("bid") if hb else None),
            "closing": hb.get("closing") if hb else None,
            # New fields for accurate trade view
            "blend": round(blend, 2) if blend else None,
            "total_lots": round(total_lot, 3) if total_lot else None,
            "tickets": [
                {
                    "ticket": p.get("ticket"),
                    "type": p.get("type"),
                    "volume": float(p.get("volume", 0) or 0),
                    "price_open": float(p.get("price_open", 0) or 0),
                    "profit": float(p.get("profit", 0) or 0),
                }
                for p in positions
            ],
        }

    exec_last = _last_jsonl(COMMON / "brain_executor_decisions.jsonl")
    if exec_last:
        _order = exec_last.get("order") or {}
        _mult = _order.get("multiplier") if isinstance(_order, dict) else None
        _raw = exec_last.get("response_raw") or {}
        snap["executor_last"] = {
            "action": exec_last.get("action"),
            "confidence": exec_last.get("confidence"),
            "mental_state": exec_last.get("mental_state"),
            "ts_age_s": (time.time() - exec_last.get("ts", 0)) if exec_last.get("ts") else None,
            "validator_rejection": _raw.get("validator_rejection") if isinstance(_raw, dict) else None,
            # v3.2 fields for dashboard display
            "multiplier": _mult,
            "thesis": exec_last.get("thesis") or (_raw.get("thesis") if isinstance(_raw, dict) else None),
            # Full reasoning for the dashboard story
            "reasoning": exec_last.get("reasoning") or (_raw.get("reasoning") if isinstance(_raw, dict) else None),
            "next_plan": exec_last.get("next_plan") or (_raw.get("next_plan") if isinstance(_raw, dict) else None),
            "invalidation": ((exec_last.get("invalidation_condition") or {}) or {}).get("text") if isinstance(exec_last.get("invalidation_condition"), dict) else None,
            "trigger_events": exec_last.get("trigger_events") or (_raw.get("trigger_events") if isinstance(_raw, dict) else None),
        }

    # Indicator context / narrative — read from zone_state which is where
    # the Indicator writes its "context" field after each cycle.
    if zs:
        snap["indicator_last"] = {
            "bias": zs.get("bias"),
            "regime": zs.get("regime"),
            "context": zs.get("context"),     # 1-2 sentence narrative
            "notes": zs.get("notes"),          # optional — gaps, anomalies
            "coverage": zs.get("coverage"),
            "updated_age_s": _iso_age_sec(zs.get("updated_at")),
        }

    # Recent events (last 10) for the dashboard activity feed
    try:
        events_path = COMMON / "brain_events_log.jsonl"
        if events_path.exists():
            with open(events_path, 'r', encoding='utf-8') as _ef:
                lines = _ef.readlines()
            recent = []
            now_ts = time.time()
            for ln in lines[-15:]:
                try:
                    ev = json.loads(ln)
                except Exception:
                    continue
                recent.append({
                    "type": ev.get("type"),
                    "ts_age_s": (now_ts - ev.get("ts", 0)) if ev.get("ts") else None,
                    "invoked_executor": ev.get("invoked_executor"),
                    "details": ev.get("details") or {},
                })
            snap["events_recent"] = recent
    except Exception:
        pass

    # 2026-05-04: staged blacklist — zones recentment invalidades en cooldown.
    # Es passa al dashboard perquè l'usuari vegi què està bloquejat ara mateix.
    try:
        import sys, os as _os
        _here = _os.path.dirname(_os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        import staged_setups as _ss_bl
        snap['staged_blacklist'] = _ss_bl.get_active_blacklist()
    except Exception:
        snap['staged_blacklist'] = []

    # Autotrade state — DOS flags separats (2026-05-04: explicitats per evitar confusió):
    #   autonomous_staging_enabled (config.yaml) → EXECUTOR pot PLANIFICAR setups
    #   autonomous_mode (brain_controls.json)    → FastEngine pot DISPARAR setups
    # Per a "autotrade ON real" cal que els DOS estiguin a true.
    try:
        with open(os.path.join(HERE, 'config.yaml'), 'r', encoding='utf-8') as _fcfg:
            _cfgtxt = _fcfg.read()
        import re as _re
        m = _re.search(r'autonomous_staging_enabled:\s*(true|false)', _cfgtxt)
        _staging_on = (m and m.group(1).lower() == 'true')
        # Llegir flag de fire del brain_controls.json
        _firing_on = False
        try:
            with open(COMMON / 'brain_controls.json', 'r', encoding='utf-8-sig') as _fctl:
                _ctldata = _safe_json_load(_fctl) if False else json.load(_fctl)
                _firing_on = bool(_ctldata.get('autonomous_mode', False))
        except Exception:
            _firing_on = False
        snap['autotrade_staging'] = _staging_on
        snap['autotrade_firing'] = _firing_on
        # Compat: 'autotrade_enabled' = TOTS DOS actius
        snap['autotrade_enabled'] = bool(_staging_on and _firing_on)
    except Exception:
        snap['autotrade_enabled'] = False

    # TG follow state — read from brain_controls.json (default ON if unset).
    # When OFF, trader_brain ignores OPEN signals from TG channels; MOVE_SL
    # and CLOSE keep processing so existing positions remain manageable.
    try:
        _ctrl = _safe_json(COMMON / "brain_controls.json") or {}
        snap['tg_follow_enabled'] = bool(_ctrl.get('tg_follow_enabled', True))
    except Exception:
        snap['tg_follow_enabled'] = True

    # Sessions enabled — read directly from config.yaml so the dashboard
    # reflects whatever the Brain reads (single source of truth).
    try:
        import sys
        _proj_dir = str(Path(__file__).resolve().parent)
        if _proj_dir not in sys.path:
            sys.path.insert(0, _proj_dir)
        import news_state
        snap['sessions_enabled'] = news_state._load_sessions_enabled()
        snap['current_session'] = news_state.session_label()
    except Exception:
        snap['sessions_enabled'] = {'ASIA':True,'LONDON':True,'OVERLAP':True,'NY':True,'DEAD':True}
        snap['current_session'] = '?'

    # Staging LLM reasoning — narrative of "why these setups" or "why no setups now"
    try:
        staging_log = COMMON / "brain_staging_last.json"
        if staging_log.exists():
            with open(staging_log, 'r', encoding='utf-8') as _sl:
                _sld = json.load(_sl)
            snap['staging_last'] = {
                'action': _sld.get('action'),
                'reasoning': _sld.get('reasoning', ''),
                'ts_age_s': (time.time() - _sld.get('ts', 0)) if _sld.get('ts') else None,
                'setups_count': len(_sld.get('staged_setups') or []),
            }
    except Exception:
        pass

    # Staged setups — what the LLM has pre-defined as potential entries
    try:
        staged_file = COMMON / "brain_staged_setups.json"
        if staged_file.exists():
            with open(staged_file, 'r', encoding='utf-8') as _sf:
                _sd = json.load(_sf)
            setups = _sd.get('setups') or []
            now_ts = time.time()
            enriched = []
            for s in setups:
                staged_at = float(s.get('staged_at', now_ts))
                exp_min = float(s.get('expiration_minutes', 30))
                elapsed_min = (now_ts - staged_at) / 60
                remaining_min = max(0, exp_min - elapsed_min)
                enriched.append({
                    'id': s.get('id'),
                    'direction': s.get('direction'),
                    'zone_price': s.get('zone_price'),
                    'tolerance_atr': s.get('tolerance_atr', 0.5),
                    'confirmations_needed': s.get('confirmations_needed', []),
                    'confirmations_min': s.get('confirmations_min', 1),
                    'invalidation_price': s.get('invalidation_price'),
                    'confidence': s.get('confidence', 0),
                    'thesis': s.get('thesis', ''),
                    'play_type': s.get('play_type'),  # None si no set; UI no mostra badge si null
                    'averaging_zones': s.get('averaging_zones', []),
                    'profit_targets': s.get('profit_targets', []),
                    'tactical_plan': s.get('tactical_plan', ''),
                    # 2026-05-04: camps Mode Recorregut Institucional
                    'trigger_zone':           s.get('trigger_zone'),
                    'tp_target':              s.get('tp_target') or s.get('profit_target'),
                    'trap_thesis':            s.get('trap_thesis', ''),
                    'tp_thesis':              s.get('tp_thesis', ''),
                    'invalidation_thesis':    s.get('invalidation_thesis', ''),
                    'auto_close_conditions':  s.get('auto_close_conditions', []),
                    'rationale':              s.get('rationale', ''),
                    'source':                 s.get('source', 'executor'),
                    # 2026-05-05: setup_type (reversion vs breakout)
                    'setup_type':             s.get('setup_type', 'reversion'),
                    'breakout_tf':            s.get('breakout_tf'),
                    'breakout_vol_min':       s.get('breakout_vol_min'),
                    'breakout_buffer_usd':    s.get('breakout_buffer_usd'),
                    'remaining_min': round(remaining_min, 1),
                    'expired': remaining_min <= 0,
                })
            snap['staged_setups'] = enriched
    except Exception:
        pass

    # NEW 2026-05-04: approach_states — live institutional flow per zone (LLM-free).
    # Llegim del journal (no és JSON file dedicat). Si no, llegim de l'snapshot
    # més recent del executor (té payload.approach_states).
    try:
        snap_fp = COMMON / 'brain_executor_snapshots.jsonl'
        if snap_fp.exists():
            with open(snap_fp, encoding='utf-8') as f:
                # Read last 5 lines, find most recent valid
                lines = f.readlines()[-5:]
            for line in reversed(lines):
                try:
                    d = json.loads(line)
                    if d.get('iso'):
                        p = d.get('payload', {})
                        if isinstance(p, dict):
                            ap = p.get('approach_states') if d.get('delta_mode') else (
                                (p.get('zones', {}) or {}).get('approach_states') or p.get('approach_states')
                            )
                            if ap:
                                snap['approach_states'] = ap
                                snap['approach_states_age_s'] = _iso_age_sec(d.get('iso'))
                            break
                except Exception:
                    continue
    except Exception:
        pass

    # NEW 2026-05-04: latest [LEVEL] monitor lines from log (live FastEngine status).
    try:
        log_fp = HERE / 'logs' / 'trader_brain.log'
        if log_fp.exists():
            with open(log_fp, encoding='utf-8', errors='replace') as f:
                # Read last 200 lines (efficient for level monitor)
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 32000))  # last ~32KB
                tail = f.read()
            level_lines = []
            for ln in tail.splitlines():
                if '[LEVEL]' in ln:
                    level_lines.append(ln.strip())
            snap['level_monitor'] = level_lines[-10:]  # last 10
    except Exception:
        pass

    # News calendar — compact summary for dashboard widget.
    try:
        import sys as _sys_nc
        if str(HERE) not in _sys_nc.path:
            _sys_nc.path.insert(0, str(HERE))
        import news_calendar as _nc
        today = _nc.events_today()
        upcoming_4h = _nc.events_upcoming(4)
        next_high = next((e for e in _nc.events_week() if e.get("impact") == "HIGH"), None)
        snap['news_calendar'] = {
            "n_today": len(today),
            "n_high_today": sum(1 for e in today if e.get("impact") == "HIGH"),
            "today": today,
            "upcoming_4h": upcoming_4h,
            "next_high": next_high,
            "freshness_age_s": _nc.freshness_age_s(),
        }
    except Exception:
        snap['news_calendar'] = None

    # Post-mortem summary — last 30 days at a glance, plus latest weekly report meta.
    try:
        import sys as _sys_pm
        if str(HERE) not in _sys_pm.path:
            _sys_pm.path.insert(0, str(HERE))
        import trade_postmortem_llm as _pm
        cutoff_30 = time.time() - 30 * 86400
        recent_pms = [p for p in _pm._read_jsonl(_pm.POSTMORTEMS_LOG) if (p.get('ts') or 0) >= cutoff_30]
        # Latest weekly report file (if any)
        latest_weekly = None
        if _pm.WEEKLY_REPORTS_DIR.exists():
            candidates = sorted(_pm.WEEKLY_REPORTS_DIR.glob("postmortem_weekly_*.json"))
            if candidates:
                latest_weekly = {
                    "path_md": str(candidates[-1]).replace(".json", ".md"),
                    "path_json": str(candidates[-1]),
                    "mtime_age_s": round(time.time() - candidates[-1].stat().st_mtime, 0),
                }
        # Top 3 lessons from recent postmortems
        from collections import Counter as _Counter
        tag_counter = _Counter()
        for p in recent_pms:
            for tag in (p.get('pattern_tags') or []):
                tag_counter[tag] += 1
        snap['postmortem_summary'] = {
            "n_recent_30d": len(recent_pms),
            "top_patterns": [{"tag": t, "frequency": c} for t, c in tag_counter.most_common(5)],
            "latest_postmortem_age_s": (round(time.time() - recent_pms[-1]['ts'], 0) if recent_pms else None),
            "latest_weekly_report": latest_weekly,
        }
    except Exception:
        snap['postmortem_summary'] = None

    # LLM in-flight state (which roles are currently calling Claude/DeepSeek).
    # Dashboard uses this to backlight the corresponding tab while the request
    # is in flight, providing live feedback that "the system is thinking".
    try:
        inflight_file = COMMON / "brain_llm_inflight.json"
        if inflight_file.exists():
            with open(inflight_file, 'r', encoding='utf-8') as _if:
                _inflight = json.load(_if)
            # Add elapsed_s for currently-calling entries so the dashboard
            # can render "thinking 90s" in the badge.
            now = time.time()
            for k, v in (_inflight or {}).items():
                if isinstance(v, dict) and v.get('calling'):
                    v['elapsed_s'] = round(now - float(v.get('started_ts') or now), 1)
            snap['llm_inflight'] = _inflight
        else:
            snap['llm_inflight'] = {}
    except Exception:
        snap['llm_inflight'] = {}

    # Executor ladder — the LLM's tactical exit plan with per-level close_pct.
    try:
        ladder_file = COMMON / "brain_executor_ladder.json"
        if ladder_file.exists():
            with open(ladder_file, 'r', encoding='utf-8') as _lf:
                _ld = json.load(_lf)
            snap['executor_ladder'] = {
                'direction': _ld.get('direction'),
                'created_ts': _ld.get('created_ts'),
                'levels': _ld.get('levels') or [],
            }
        else:
            snap['executor_ladder'] = None
    except Exception:
        snap['executor_ladder'] = None

    # Broker-side trade plan — TPs the system has assigned per ticket (geometric
    # zone fallback when no LLM ladder, or honors LLM targets via apply_trade_plan).
    # Shown alongside the ladder so the user sees BOTH layers.
    try:
        plan_file = COMMON / "brain_trade_plan.json"
        if plan_file.exists():
            with open(plan_file, 'r', encoding='utf-8') as _pf:
                _pl = json.load(_pf)
            snap['trade_plan'] = {
                'direction':    _pl.get('direction'),
                'blend_price':  _pl.get('blend_price'),
                'reason':       _pl.get('reason'),
                'assignments':  _pl.get('assignments') or [],
            }
        else:
            snap['trade_plan'] = None
    except Exception:
        snap['trade_plan'] = None

    # Daily P&L — broker balance delta + documented anomaly compensation.
    # When today has a phantom_cleanup, daily_pnl reflects the trader's actual
    # decisions (broker accidents excluded). Both daily_pnl and the trade-list
    # total_pnl include the same adjustment so the two views stay coherent.
    # daily_pnl_raw preserves the unadjusted broker delta for transparency.
    try:
        daily_file = COMMON / "brain_daily.json"
        if daily_file.exists():
            with open(daily_file, 'r', encoding='utf-8') as _df:
                _da = json.load(_df)
            anchor = float(_da.get('start_balance') or _da.get('balance_anchor') or 0)
            balance_now = 0
            if snap.get("ea"):
                balance_now = float(snap["ea"].get("balance") or 0)
            if anchor > 0 and balance_now > 0:
                _raw_pnl = round(balance_now - anchor, 2)
                _adj = 0.0
                _adj_note = None
                _adj_verified = False
                try:
                    _led_path = COMMON / "brain_daily_ledger.json"
                    if _led_path.exists():
                        with open(_led_path, 'r', encoding='utf-8') as _lf:
                            _led = json.load(_lf)
                        _today_key = datetime.now(timezone.utc).strftime('%Y-%m-%d')
                        _today_row = (_led.get('days') or {}).get(_today_key) or {}
                        _ph = _today_row.get('_phantom_cleanup')
                        if isinstance(_ph, dict) and 'removed_pnl_delta' in _ph:
                            _adj = -float(_ph.get('removed_pnl_delta') or 0)
                            _adj_note = str(_ph.get('note') or 'anomaly cleanup')
                            _adj_verified = bool(_ph.get('broker_verified', False))
                except Exception:
                    pass
                snap["daily_pnl_raw"] = _raw_pnl
                snap["daily_pnl"] = round(_raw_pnl + _adj, 2)
                snap["daily_anchor"] = anchor
                if _adj:
                    snap["daily_pnl_adjustment"] = {
                        "amount": round(_adj, 2),
                        "note": _adj_note,
                        "broker_verified": _adj_verified,
                    }
            # Accumulated P&L + today reconciliation from the persistent ledger
            try:
                import daily_ledger
                snap["ledger"] = {
                    "accumulated": daily_ledger.accumulated(),
                    "today_reconcile": daily_ledger.reconcile(),
                }
            except Exception:
                pass
    except Exception:
        pass

    # ── Staged setups (grouped by source: hunter vs executor) ──
    try:
        import sys
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import staged_setups as _ss
        all_setups = _ss.load() or []
        h_setups = [s for s in all_setups if (s.get('source') or 'executor').lower() == 'hunter']
        e_setups = [s for s in all_setups if (s.get('source') or 'executor').lower() == 'executor']
        snap["staged"] = {
            "hunter": [{
                "id": s.get("id"),
                "direction": s.get("direction"),
                "zone_price": s.get("zone_price"),
                "profit_target": s.get("profit_target"),
                "invalidation": s.get("invalidation_price"),
                "confidence": s.get("confidence"),
                "target_distance_usd": s.get("target_distance_usd"),
                "stop_distance_usd": s.get("stop_distance_usd"),
                "post_close": bool(s.get("post_close")),
                "regime_at_stage": s.get("regime_at_stage"),
                "confluences": s.get("confluences") or [],
                "thesis": (s.get("thesis") or "")[:200],
                "staged_at": s.get("staged_at"),
                "expiration_minutes": s.get("expiration_minutes"),
            } for s in h_setups],
            "executor": [{
                "id": s.get("id"),
                "direction": s.get("direction"),
                "zone_price": s.get("zone_price"),
                "confidence": s.get("confidence"),
                "play_type": s.get("play_type"),
                "thesis": (s.get("thesis") or "")[:200],
                "staged_at": s.get("staged_at"),
            } for s in e_setups],
            "count_hunter": len(h_setups),
            "count_executor": len(e_setups),
        }
    except Exception:
        pass

    # ── Hunter stats (win rate, by regime, losing streak) ──
    try:
        import hunter_stats as _hs
        snap["hunter_stats"] = _hs.summary(days=30)
    except Exception:
        pass

    # Active snipers — Executor-proposed pre-placed averagings (fire INSTANTLY
    # on price touch, NO candle confirmation). Dashboard shows these separately
    # from FAST-engine zones so trader can distinguish "confirmation-required"
    # vs "instant on touch".
    try:
        import snipers as _snp
        _alive = _snp.load()
        snap["active_snipers"] = [{
            "id": s.get("id"),
            "direction": s.get("direction"),
            "price": s.get("price"),
            "multiplier": s.get("multiplier"),
            "reason": (s.get("reason") or "")[:140],
            "tolerance_usd": s.get("tolerance_usd"),
            "placed_at": s.get("placed_at"),
        } for s in _alive]
    except Exception:
        snap["active_snipers"] = []

    # Trade story — last N trade history events (openings, avgs, partials, closes)
    try:
        th_full = _safe_json(COMMON / "brain_trade_history.json")
        if th_full and isinstance(th_full, dict):
            evs = th_full.get("events", [])
            recent_trade = []
            now_ts = time.time()
            for e in evs[-12:]:
                recent_trade.append({
                    "type": e.get("type"),
                    "direction": e.get("direction"),
                    "price": e.get("price"),
                    "lot": e.get("lot"),
                    "pnl_delta": e.get("pnl_delta"),
                    "reasoning": (e.get("reasoning") or "")[:140],
                    "source": e.get("source"),
                    "ts_age_s": (now_ts - e.get("ts", 0)) if e.get("ts") else None,
                })
            snap["trade_story"] = recent_trade
    except Exception:
        pass

    # Market context enrichment — read the latest status file if brain wrote
    # the external + market_state sections. Brain persists these via status.json
    # or we fall back to computing locally (not done here to avoid double load).
    bs_path = COMMON / "brain_status.json"
    bs = _safe_json(bs_path)
    bs_age = _file_age_s(bs_path)
    # If trader_brain has died, brain_status.json grows stale. Skip its
    # live_price so the dashboard doesn't show a 3h-old frozen price.
    bs_fresh = bs_age is not None and bs_age < 120
    if bs and isinstance(bs, dict):
        if bs.get("external"):
            snap["external"] = bs["external"]
        if bs.get("market_state"):
            snap["market_state"] = bs["market_state"]
        if bs.get("htf"):
            snap["htf"] = bs["htf"]
        if bs.get("feed"):
            snap["feed"] = bs["feed"]
        live_price = bs.get("price") if bs_fresh else None
        if live_price:
            snap.setdefault("ea", {})
            if not snap["ea"].get("price"):
                snap["ea"]["price"] = live_price
            snap["live_price"] = live_price
            snap["live_price_ts_age_s"] = bs_age
    snap["brain_status_age_s"] = bs_age

    # Fast tick — overrides live_price if fresher than brain_status. Prefer the
    # EA-authored market snapshot, then fall back to the legacy Python tick.
    try:
        tick_path = COMMON / "brain_market_tick.json"
        if not tick_path.exists():
            tick_path = COMMON / "brain_tick.json"
        if tick_path.exists():
            tick = _safe_json(tick_path)
            tick_age = _file_age_s(tick_path)
            tick_price = None
            if tick:
                if tick.get("bid") and tick.get("ask"):
                    tick_price = (float(tick["bid"]) + float(tick["ask"])) / 2.0
                elif tick.get("price"):
                    tick_price = float(tick["price"])
            if tick_price and tick_age is not None and tick_age < 30:
                tick_age_status = snap.get("live_price_ts_age_s")
                # Use tick price if status is stale OR tick is fresher
                if (tick_age_status is None) or (tick_age < tick_age_status):
                    snap["live_price"] = tick_price
                    snap["live_price_ts_age_s"] = round(tick_age, 2)
                    snap["live_price_source"] = "market_tick" if tick_path.name == "brain_market_tick.json" else "tick"
                    snap.setdefault("ea", {})["price"] = tick_price
    except Exception:
        pass

    try:
        broker_deals = _safe_json(COMMON / "brain_broker_deals.json")
        if broker_deals and isinstance(broker_deals, dict):
            deals = broker_deals.get("deals", [])
            now_ts = time.time()
            snap["broker_deals_recent"] = [{
                "deal": d.get("deal"),
                "position_id": d.get("position_id"),
                "type": d.get("type"),
                "entry": d.get("entry"),
                "price": d.get("price"),
                "volume": d.get("volume"),
                "net": d.get("net"),
                "profit": d.get("profit"),
                "swap": d.get("swap"),
                "commission": d.get("commission"),
                "fee": d.get("fee"),
                "ts_age_s": (now_ts - d.get("time", 0)) if d.get("time") else None,
            } for d in deals[-8:]]
    except Exception:
        pass

    # Sizing snapshot (base_lot, multipliers) so the dashboard can render the
    # implied risk of any action without re-reading config.yaml in the browser.
    try:
        import yaml as _yaml
        with open(os.path.join(os.path.dirname(__file__), 'config.yaml'), 'r', encoding='utf-8') as _fcfg:
            _cfg = _yaml.safe_load(_fcfg) or {}
        _sz = _cfg.get('sizing', {}) or {}
        _rc = _cfg.get('risk_control', {}) or {}
        snap["sizing"] = {
            "base_lot": float(_sz.get('base_lot', 0.03)),
            "max_multiplier": int(_sz.get('max_multiplier', 5)),
            "initial_multiplier": int(_sz.get('initial_multiplier', 2)),
            "fast_engine_multipliers": dict(_sz.get('fast_engine_multipliers', {})),
            "dd_hard_pct": float(_rc.get('dd_hard_pct', 3.5)),
            "dd_soft_pct": float(_rc.get('dd_soft_pct', 3.4)),
            # 2026-04-30: averaging cap eliminat (només DD del 3.5% limita).
            # Sentinel 999 = unlimited; dashboard hauria d'ignorar-ho.
            "max_avg_per_signal": 999,
            "max_partials_per_signal": 3,
            "min_avg_strength": "MODERATE",
        }
    except Exception:
        pass

    ev_last = _last_jsonl(COMMON / "brain_events_log.jsonl")
    if ev_last:
        snap["event_last"] = {
            "type": ev_last.get("type"),
            "ts_age_s": (time.time() - ev_last.get("ts", 0)) if ev_last.get("ts") else None,
            "invoked_executor": ev_last.get("invoked_executor"),
        }

    th = _safe_json(COMMON / "brain_trade_history.json")
    if th and isinstance(th, dict):
        events = th.get("events", [])
        if events:
            last = events[-1]
            snap["trade_last"] = {
                "type": last.get("type"),
                "direction": last.get("direction"),
                "ticket": last.get("ticket"),
                "pnl_delta": last.get("pnl_delta"),
                "ts_age_s": (time.time() - last.get("ts", 0)) if last.get("ts") else None,
            }

    # Trade Monitor (Haiku/DeepSeek guardian) — 2026-05-07
    try:
        # Llegeix l'últim entry del monitor log JSONL
        mon_log = Path(__file__).parent / "logs" / "trade_monitor.log"
        if mon_log.exists():
            last_mon = _last_jsonl(mon_log)
            if last_mon:
                age_s = time.time() - float(last_mon.get('ts', 0))
                # Active si última crida < 180s (3min) i hi ha trade.
                # Nota: el monitor fa calls cada ~60-90s perquè el main loop
                # comparteix temps amb INDICATOR/EXECUTOR/FastEngine.
                signal_active = bool(snap.get("signal", {}).get("active"))
                snap["monitor"] = {
                    "active": signal_active and age_s < 180,
                    "last_action": (last_mon.get('decision') or {}).get('action'),
                    "last_confidence": (last_mon.get('decision') or {}).get('confidence'),
                    "last_reason": (last_mon.get('decision') or {}).get('reason'),
                    "last_call_age_s": int(age_s),
                    "turn_count": last_mon.get('turn'),
                    "mode": last_mon.get('mode'),
                    "haiku_response_s": (last_mon.get('context') or {}).get('haiku_response_s'),
                }
    except Exception:
        pass

    return snap


def state_poll_loop() -> None:
    """Poll + push loop.

    Two modes combined:
      1. Fast file-change detector — every 200ms, watch mtime of key state
         files (positions, signal_state, heartbeat, events_log). If ANY has
         changed, push snapshot IMMEDIATELY with `instant=True` and a
         `change_hint` so the UI can flash.
      2. Heartbeat poll — every 1s, push anyway so dashboards track age
         fields (ts_age_s) accurately even during quiet markets.
    """
    watched = [
        ("positions", COMMON / "brain_positions.json"),
        ("signal", COMMON / "brain_signal_state.json"),
        ("heartbeat", COMMON / "brain_ea_heartbeat.json"),
        ("events", COMMON / "brain_events_log.jsonl"),
        ("trade_history", COMMON / "brain_trade_history.json"),
        ("broker_deals", COMMON / "brain_broker_deals.json"),
        ("market_tick", COMMON / "brain_market_tick.json"),
        ("tick", COMMON / "brain_tick.json"),
    ]
    last_mtime = {name: 0.0 for name, _ in watched}
    last_heartbeat_push = 0.0
    while True:
        try:
            change_hints = []
            for name, p in watched:
                try:
                    m = p.stat().st_mtime if p.exists() else 0.0
                except Exception:
                    m = 0.0
                if m and m > last_mtime[name]:
                    if last_mtime[name] > 0:
                        change_hints.append(name)
                    last_mtime[name] = m
            now = time.time()
            should_push = bool(change_hints) or (now - last_heartbeat_push) >= 1.0
            if should_push:
                snap = build_snapshot()
                payload = {"type": "state", "data": snap, "now": now}
                if change_hints:
                    payload["instant"] = True
                    payload["change_hint"] = change_hints
                broadcast(payload)
                last_heartbeat_push = now
        except Exception as e:
            broadcast({"type": "state_error", "error": str(e)})
        # 200ms granularity for fast detection; only pushes when needed
        time.sleep(0.2)


# ──────────────────────────────────────────────────────────────────────
# HTTP handler
# ──────────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs):
        pass  # silence default access log

    def do_GET(self):
        if self.path == "/trades":
            self._serve_trades_library()
            return
        if self.path.startswith("/api/trades"):
            self._serve_api_trades()
            return
        if self.path.startswith("/api/control/"):
            self._serve_api_control()
            return
        if self.path.startswith("/api/ledger"):
            self._serve_api_ledger()
            return
        if self.path.startswith("/api/hunter"):
            self._serve_api_hunter()
            return
        if self.path.startswith("/api/staged"):
            self._serve_api_staged()
            return
        if self.path.startswith("/api/narrative"):
            self._serve_api_narratives()
            return
        if self.path.startswith("/api/snipers"):
            self._serve_api_snipers()
            return
        if self.path.startswith("/api/postmortem"):
            self._serve_api_postmortem()
            return
        if self.path.startswith("/api/news"):
            self._serve_api_news()
            return
        if self.path == "/":
            self._serve_html(HTML_FILE)  # trader-friendly view (default)
        elif self.path == "/flow":
            self._serve_html(HTML_FLOW_FILE)  # dev flow diagram
        elif self.path == "/narratives":
            self._serve_html(HTML_NARRATIVES_FILE)  # per-trade narratives
        elif self.path == "/executor":
            self._serve_html(HERE / "executor_prompt_view.html")  # full Executor prompt
        elif self.path == "/docs":
            self._serve_html(HERE / "system_documentation.html")  # interactive docs
        elif self.path == "/stream":
            self._serve_stream()
        elif self.path == "/snapshot":
            snap = build_snapshot()
            body = json.dumps(snap, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def _serve_api_control(self):
        """Control endpoints: toggle autotrade, force indicator/executor refresh.
        Writes to brain_controls.json which trader_brain polls each tick.
        """
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        action = self.path.split('/api/control/', 1)[1].split('?', 1)[0]
        ctrl_file = COMMON / "brain_controls.json"
        current = {}
        try:
            if ctrl_file.exists():
                with open(ctrl_file, 'r', encoding='utf-8') as f:
                    current = json.load(f)
        except Exception:
            current = {}
        result = {"action": action, "ok": True}
        try:
            if action == 'autotrade':
                enabled = (q.get('enabled', ['false'])[0]).lower() in ('true', '1', 'yes')
                # Update config.yaml directly
                cfg_path = os.path.join(HERE, 'config.yaml')
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    cfg_text = f.read()
                import re as _re
                new_text = _re.sub(
                    r'(autonomous_staging_enabled:\s*)(true|false)',
                    lambda m: m.group(1) + ('true' if enabled else 'false'),
                    cfg_text, count=1
                )
                with open(cfg_path, 'w', encoding='utf-8') as f:
                    f.write(new_text)
                current['autotrade_enabled'] = enabled
                current['autotrade_updated_at'] = time.time()
                result['enabled'] = enabled
                result['note'] = "Config updated. Brain needs to re-read config (automatic within seconds)."
            elif action == 'tg_follow':
                enabled = (q.get('enabled', ['true'])[0]).lower() in ('true', '1', 'yes')
                current['tg_follow_enabled'] = enabled
                current['tg_follow_updated_at'] = time.time()
                result['enabled'] = enabled
                result['note'] = ("Seguint senyals TG (OPEN actius)" if enabled
                                  else "Senyals TG OPEN ignorades — MOVE_SL i CLOSE segueixen actius")
            elif action == 'session':
                # Toggle a session ON/OFF. Edits config.yaml directly so the
                # Brain (which re-reads on every is_session_enabled call) picks
                # up the change without restart.
                session = (q.get('session', [''])[0]).upper()
                enabled = (q.get('enabled', ['true'])[0]).lower() in ('true', '1', 'yes')
                if session not in ('ASIA', 'LONDON', 'OVERLAP', 'NY', 'DEAD'):
                    result['ok'] = False
                    result['note'] = f"Session invàlida: {session}"
                else:
                    cfg_path = Path(__file__).resolve().parent / "config.yaml"
                    try:
                        with open(cfg_path, 'r', encoding='utf-8') as f:
                            cfg_text = f.read()
                        # Replace the line `  SESSION:    bool` inside sessions_enabled.
                        # Pattern allows variable whitespace.
                        pattern = re.compile(
                            rf'(^\s+{session}:\s*)(true|false)(\s*$)',
                            re.MULTILINE | re.IGNORECASE,
                        )
                        new_text, n = pattern.subn(
                            lambda m: f"{m.group(1)}{'true' if enabled else 'false'}{m.group(3)}",
                            cfg_text, count=1,
                        )
                        if n != 1:
                            result['ok'] = False
                            result['note'] = f"Pattern no trobat per {session} a config.yaml"
                        else:
                            with open(cfg_path, 'w', encoding='utf-8') as f:
                                f.write(new_text)
                            result['enabled'] = enabled
                            result['session'] = session
                            result['note'] = f"Session {session} {'enabled' if enabled else 'disabled'} (aplicat en calent)"
                    except Exception as _ce:
                        result['ok'] = False
                        result['note'] = f"Error escrivint config: {_ce}"
            elif action == 'force_indicator':
                current['force_indicator'] = True
                current['force_indicator_ts'] = time.time()
                result['note'] = "Indicator will run on next tick."
            elif action == 'force_executor':
                current['force_executor'] = True
                current['force_executor_ts'] = time.time()
                result['note'] = "Executor will be invoked on next tick."
            elif action == 'force_staging':
                current['force_staging'] = True
                current['force_staging_ts'] = time.time()
                result['note'] = "Staging Executor will be invoked on next tick (if IDLE)."
            elif action == 'force_hunter':
                current['force_hunter'] = True
                current['force_hunter_ts'] = time.time()
                result['note'] = "Hunter (reversion scanner) will be invoked on next tick."
            elif action == 'reduce_risk':
                # Manual risk reduction: signal the brain main loop to close
                # `pct%` of EVERY open ticket proportionally. Default 25%.
                #
                # Writes a control flag to brain_controls.json. The brain
                # main loop reads it each tick and executes the partials
                # through write_order (which uses _order_lock + EA-write
                # coordination). This avoids the race condition that would
                # occur if brain_flow wrote directly to brain_orders.json
                # (the brain could overwrite the manual order before the EA
                # reads it). Incident 2026-04-27: user clicked Reduce Risk
                # but the order was overwritten by a brain MODIFY_TP write.
                try:
                    pct = int(q.get('pct', ['25'])[0])
                except Exception:
                    pct = 25
                if pct < 1 or pct > 99:
                    result['ok'] = False
                    result['note'] = f"pct invàlid: {pct} (ha de ser 1-99)"
                else:
                    current['reduce_risk_pct'] = pct
                    current['reduce_risk_ts'] = time.time()
                    result['pct'] = pct
                    result['note'] = f"Senyal enviada al brain: reduir {pct}% de cada ticket. S'executarà al pròxim tick (≤2s)."
            elif action == 'llm':
                # Set LLM provider+model for a specific role (indicator, reviewer, executor)
                role = q.get('role', [''])[0].lower()
                provider = q.get('provider', ['deepseek'])[0].lower()
                model = q.get('model', [''])[0].lower()
                if role not in ('indicator', 'reviewer', 'executor', 'interpreter', 'hunter'):
                    result['ok'] = False
                    result['error'] = f"Invalid role: {role}"
                else:
                    llm_path = COMMON / "brain_llm_config.json"
                    llm_cfg = {}
                    try:
                        if llm_path.exists():
                            with open(llm_path, 'r', encoding='utf-8') as f:
                                llm_cfg = json.load(f)
                    except Exception:
                        llm_cfg = {}
                    llm_cfg[role] = {'provider': provider, 'model': model}
                    llm_cfg['_updated_at'] = time.time()
                    with open(llm_path, 'w', encoding='utf-8') as f:
                        json.dump(llm_cfg, f, indent=2)
                    result['role'] = role
                    result['provider'] = provider
                    result['model'] = model
                    result['note'] = f"{role} → {provider}/{model}"
            elif action == 'llm_status':
                # Return current config
                llm_path = COMMON / "brain_llm_config.json"
                try:
                    if llm_path.exists():
                        with open(llm_path, 'r', encoding='utf-8') as f:
                            result['config'] = json.load(f)
                    else:
                        result['config'] = {}
                except Exception as e:
                    result['config'] = {}
                    result['error'] = str(e)
            else:
                result['ok'] = False
                result['error'] = f"Unknown action: {action}"
            with open(ctrl_file, 'w', encoding='utf-8') as f:
                json.dump(current, f, indent=2)
        except Exception as e:
            result['ok'] = False
            result['error'] = str(e)
        body = json.dumps(result).encode('utf-8')
        self.send_response(200 if result.get('ok') else 400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_trades_library(self):
        html_path = HERE / "brain_trades.html"
        self._serve_html(html_path)

    def _serve_api_trades(self):
        """JSON API for trade library. Query ?view=today|week|list&days=N&detail=N"""
        from urllib.parse import urlparse, parse_qs
        import trade_library
        q = parse_qs(urlparse(self.path).query)
        view = (q.get('view', ['list'])[0]).lower()
        try:
            if view == 'today':
                data = trade_library.daily_summary()
            elif view == 'week':
                data = trade_library.weekly_summary()
            elif view == 'day':
                data = trade_library.daily_summary(day=q.get('day', [None])[0])
            elif view == 'detail':
                idx = int(q.get('idx', ['0'])[0])
                data = trade_library.get_trade_detail(idx) or {}
            else:
                days = int(q.get('days', ['7'])[0])
                limit = int(q.get('limit', ['50'])[0])
                data = {'trades': trade_library.list_trades(since_days=days, limit=limit)}
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            err = json.dumps({"error": str(e)}).encode('utf-8')
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)

    def _serve_api_ledger(self):
        """JSON API for persistent ledger.
        Query ?days=N&weeks=N&months=N (each defaults to a sane value).
        Returns: {accumulated, today_reconcile, history, weeks, months}"""
        from urllib.parse import urlparse, parse_qs
        import daily_ledger
        q = parse_qs(urlparse(self.path).query)
        try:
            days_back = int(q.get('days', ['30'])[0])
            weeks_back = int(q.get('weeks', ['12'])[0])
            months_back = int(q.get('months', ['12'])[0])
            data = {
                'accumulated': daily_ledger.accumulated(),
                'today_reconcile': daily_ledger.reconcile(),
                'history': daily_ledger.history(days_back),
                'weeks': daily_ledger.weeks(weeks_back),
                'months': daily_ledger.months(months_back),
            }
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            err = json.dumps({"error": str(e)}).encode('utf-8')
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)

    def _serve_api_hunter(self):
        """JSON API: Hunter stats (stages, trades, win rate, by regime) + active Hunter setups.
        Query: ?days=N (default 30)"""
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        try:
            days_back = int(q.get('days', ['30'])[0])
            # Stats
            try:
                import sys
                if str(HERE) not in sys.path:
                    sys.path.insert(0, str(HERE))
                import hunter_stats
                stats = hunter_stats.summary(days=days_back)
            except Exception as e:
                stats = {"error": str(e)}
            # Active Hunter setups
            try:
                import staged_setups
                all_setups = staged_setups.load() or []
            except Exception:
                all_setups = []
            hunter_setups = [s for s in all_setups if (s.get('source') or 'executor').lower() == 'hunter']
            data = {
                "stats": stats,
                "active_setups": hunter_setups,
                "count_active": len(hunter_setups),
            }
            body = json.dumps(data, ensure_ascii=False, default=str).encode('utf-8')
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            err = json.dumps({"error": str(e)}).encode('utf-8')
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)

    def _serve_api_staged(self):
        """JSON API: staged setups grouped by source (hunter/executor) for dashboard panels."""
        try:
            import sys
            if str(HERE) not in sys.path:
                sys.path.insert(0, str(HERE))
            import staged_setups
            all_setups = staged_setups.load() or []
            grouped = {"hunter": [], "executor": [], "other": []}
            for s in all_setups:
                src = (s.get('source') or 'executor').lower()
                bucket = grouped.get(src, grouped["other"])
                bucket.append(s)
            data = {
                "total": len(all_setups),
                "by_source": grouped,
                "count_hunter": len(grouped["hunter"]),
                "count_executor": len(grouped["executor"]),
            }
            body = json.dumps(data, ensure_ascii=False, default=str).encode('utf-8')
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            err = json.dumps({"error": str(e)}).encode('utf-8')
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)

    def _serve_api_narratives(self):
        """JSON API: per-trade narratives (what was done and why).

        GET /api/narratives              → newest 50 narratives (summary list)
        GET /api/narratives?id=t_abcd    → one full narrative by trade_id
        GET /api/narratives?limit=20     → limit count
        """
        try:
            import sys
            from urllib.parse import urlparse, parse_qs
            if str(HERE) not in sys.path:
                sys.path.insert(0, str(HERE))
            import trade_narrative
            q = parse_qs(urlparse(self.path).query)
            tid = (q.get('id') or [None])[0]
            limit = int((q.get('limit') or ['50'])[0])
            if tid:
                all_n = trade_narrative.read_all_narratives(limit=500)
                match = next((n for n in all_n if n.get('trade_id') == tid), None)
                data = match or {"error": "not found", "trade_id": tid}
            else:
                data = {
                    "narratives": trade_narrative.read_all_narratives(limit=limit),
                }
            body = json.dumps(data, ensure_ascii=False, default=str).encode('utf-8')
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            err = json.dumps({"error": str(e)}).encode('utf-8')
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)

    def _serve_api_postmortem(self):
        """JSON API: trade post-mortems (LLM-driven trade reviews).

        GET /api/postmortem/list?days=30   → recent post-mortems (summary list)
        GET /api/postmortem/weekly         → last weekly aggregate (cached file)
        GET /api/postmortem/run-weekly     → trigger fresh weekly aggregate+report
        GET /api/postmortem/trade?id=tid   → full post-mortem for one trade_id
        """
        try:
            import sys
            from urllib.parse import urlparse, parse_qs
            if str(HERE) not in sys.path:
                sys.path.insert(0, str(HERE))
            import trade_postmortem_llm as pm
            sub = self.path.split('/api/postmortem/', 1)[-1].split('?', 1)[0].rstrip('/')
            q = parse_qs(urlparse(self.path).query)

            if sub == "run-weekly":
                days = int((q.get('days') or ['7'])[0])
                agg = pm.weekly_aggregate(days=days)
                md, js = pm.write_weekly_report(agg)
                data = {
                    "ok": True,
                    "report_md": str(md),
                    "report_json": str(js),
                    "n_trades": agg.get("n_trades"),
                    "warning": agg.get("warning"),
                    "mean_process_score": agg.get("mean_process_score"),
                    "foreseeable_loss_rate_pct": agg.get("foreseeable_loss_rate_pct"),
                }
            elif sub == "weekly":
                # Return the most recent cached aggregate JSON.
                weekly_dir = pm.WEEKLY_REPORTS_DIR
                latest = None
                if weekly_dir.exists():
                    candidates = sorted(weekly_dir.glob("postmortem_weekly_*.json"))
                    if candidates:
                        latest = candidates[-1]
                if latest:
                    with open(latest, 'r', encoding='utf-8') as _f:
                        data = json.load(_f)
                    data['_source'] = str(latest)
                else:
                    data = {"n_trades": 0, "warning": "No weekly report yet — call /run-weekly first"}
            elif sub == "trade":
                tid = (q.get('id') or [None])[0]
                if not tid:
                    raise ValueError("missing ?id=trade_id")
                pms = [p for p in pm._read_jsonl(pm.POSTMORTEMS_LOG) if p.get('trade_id') == tid]
                data = pms[-1] if pms else {"error": "not found", "trade_id": tid}
            else:  # default: list
                days = int((q.get('days') or ['30'])[0])
                cutoff = time.time() - days * 86400
                pms = [p for p in pm._read_jsonl(pm.POSTMORTEMS_LOG) if (p.get('ts') or 0) >= cutoff]
                data = {
                    "days": days,
                    "n": len(pms),
                    "postmortems": [
                        {
                            "ts": p.get("ts"),
                            "iso": p.get("iso"),
                            "trade_id": p.get("trade_id"),
                            "verdict": p.get("verdict"),
                            "process_score": p.get("process_score"),
                            "broker_pnl_usd": p.get("broker_pnl_usd"),
                            "lesson": p.get("lesson"),
                        }
                        for p in sorted(pms, key=lambda x: x.get("ts", 0), reverse=True)
                    ],
                }

            body = json.dumps(data, ensure_ascii=False, default=str).encode('utf-8')
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            err = json.dumps({"error": str(e)}).encode('utf-8')
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)

    def _serve_api_news(self):
        """JSON API: economic news calendar (Forex Factory weekly).

        GET /api/news/today              → today's events (UTC day window)
        GET /api/news/week               → next 7 days
        GET /api/news/upcoming?h=4       → next N hours
        GET /api/news/refresh            → force fetch (ignore freshness)
        GET /api/news/info               → meta: fetched_ts, n_events, file path
        """
        try:
            import sys
            from urllib.parse import urlparse, parse_qs
            if str(HERE) not in sys.path:
                sys.path.insert(0, str(HERE))
            import news_calendar as _nc
            sub = self.path.split('/api/news/', 1)[-1].split('?', 1)[0].rstrip('/')
            q = parse_qs(urlparse(self.path).query)

            if sub == "refresh":
                r = _nc.fetch_and_persist()
                data = {
                    "ok": r.get("ok", False),
                    "n_kept": r.get("n_kept"),
                    "n_filtered": r.get("n_filtered"),
                    "fetched_ts": r.get("fetched_ts"),
                    "error": r.get("error"),
                }
            elif sub == "today":
                data = {"events": _nc.events_today()}
            elif sub == "week":
                data = {"events": _nc.events_week()}
            elif sub == "upcoming":
                hours = int((q.get('h') or ['4'])[0])
                data = {"hours": hours, "events": _nc.events_upcoming(hours)}
            elif sub == "info":
                cal = _nc.load()
                data = {
                    "ok": cal.get("ok", False),
                    "fetched_ts": cal.get("fetched_ts"),
                    "fetched_iso": cal.get("fetched_iso"),
                    "freshness_age_s": _nc.freshness_age_s(),
                    "n_events": len(cal.get("events") or []),
                    "source": cal.get("source"),
                }
            else:
                # Default: list (today)
                data = {"events": _nc.events_today()}

            body = json.dumps(data, ensure_ascii=False, default=str).encode('utf-8')
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            err = json.dumps({"error": str(e)}).encode('utf-8')
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)

    def _serve_html(self, html_path):
        try:
            body = html_path.read_bytes()
        except FileNotFoundError:
            body = b"<h1>HTML file missing</h1>"
            self.send_response(500)
        else:
            self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q: queue.Queue = queue.Queue(maxsize=500)
        with _subs_lock:
            _subs.append(q)
        try:
            # send initial hello + immediate state
            self.wfile.write(b"retry: 2000\n\n")
            self.wfile.write(b"data: " + json.dumps({"type": "hello"}).encode() + b"\n\n")
            self.wfile.flush()
            # push current snapshot immediately
            snap = build_snapshot()
            self.wfile.write(b"data: " + json.dumps({"type": "state", "data": snap, "now": time.time()}).encode() + b"\n\n")
            self.wfile.flush()
            # Replay recent log events so the client sees "what just happened"
            _replay_recent_log(q, n=120)
            while True:
                try:
                    msg = q.get(timeout=10)
                except queue.Empty:
                    msg = "data: " + json.dumps({"type": "ping"}) + "\n\n"
                self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            with _subs_lock:
                try:
                    _subs.remove(q)
                except ValueError:
                    pass


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────
def main() -> None:
    threading.Thread(target=tail_log_loop, daemon=True).start()
    threading.Thread(target=state_poll_loop, daemon=True).start()
    import sys as _sys
    try:
        _sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(f"Brain Flow -> http://{HOST}:{PORT}/")
    print(f"  Log tail: {LOG_FILE}")
    print(f"  Common:   {COMMON}")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
