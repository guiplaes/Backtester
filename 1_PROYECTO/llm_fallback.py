#!/usr/bin/env python3
"""LLM fallback: detect Claude limits → switch role to DeepSeek → auto-restore.

Hook from _call_claude after each attempt:

    from llm_fallback import detect_and_switch, check_restore
    ...
    if r.returncode != 0:
        if detect_and_switch(role, r.stderr, r.stdout, r.returncode):
            log.warning(f"[{role}] Claude limit hit → fallback to DeepSeek active")

Call check_restore() once per main-loop iteration so the override auto-expires.

Fallback matrix (maps Claude role+model → DeepSeek config):
    opus   → {provider: deepseek, model: reasoner}
    sonnet → {provider: deepseek, model: reasoner}
    haiku  → {provider: deepseek, model: chat}
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

COMMON = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files")
CONFIG_FILE = COMMON / "brain_llm_config.json"
FALLBACK_STATE_FILE = COMMON / "brain_llm_fallback.json"

FALLBACK_DURATION_MIN = 60          # default: 1h before trying Claude again
CHECK_RESTORE_PROBE_SECS = 60       # do not restore more than once per minute

log = logging.getLogger("brain")
_lock = threading.Lock()

# Auth errors — token expired/invalid, cannot recover without manual
# `claude setup-token`. Handled with 24h backoff + one-shot notification.
_AUTH_PATTERNS = [
    r"authentication_error", r"invalid[_\s]+credentials", r"\bunauthorized\b",
    r"\bforbidden\b", r"\b403\b",
]
# Note: bare "\b401\b" or "api_error" alone can appear in transient rate_limit
# responses too, so we require the explicit auth strings above.
_AUTH_REGEX = re.compile("|".join(_AUTH_PATTERNS), re.IGNORECASE)

# Anthropic-side server overload — distinct from user quota issues.
# 529 status, "overloaded_error", "overloaded" message string.
_OVERLOADED_PATTERNS = [
    r"\b529\b", r"overloaded[_\s]error", r"\boverloaded\b",
]
_OVERLOADED_REGEX = re.compile("|".join(_OVERLOADED_PATTERNS), re.IGNORECASE)

# Transient rate/quota limits — self-recovering after backoff.
_LIMIT_PATTERNS = [
    r"\brate\s*limit", r"\b429\b", r"too many requests",
    r"\bquota\b", r"\bexceed(ed|s)?\b", r"\bexhaust(ed)?\b",
    r"\busage\s*limit", r"\bcredit(s)?\b", r"\bbalance\b.*\b(low|insufficient)",
    r"limit reached", r"out of (credits|quota)",
    r"hit\s+(your|the)\s+limit", r"resets?\s+\d+(am|pm)", r"reaches?\s+(your|the)?\s*limit",
    r"\b401\b", r"api[_\s]error",
    # CLI / OS issues that can't be recovered with a retry (e.g. Windows arg-length)
    r"WinError\s*206", r"filename or extension is too long",
]
_LIMIT_REGEX = re.compile("|".join(_LIMIT_PATTERNS), re.IGNORECASE)


def _is_overloaded_error(rc: int, stderr: str, stdout: str) -> bool:
    """Distinct check ABANS de _is_limit_error — 529 overloaded
    apareix com 'api_error' al regex de limit, però és Anthropic-side,
    no quota d'usuari."""
    if rc == 0:
        return False
    return bool(_OVERLOADED_REGEX.search(f"{stderr or ''}\n{stdout or ''}"))


def _is_auth_error(rc: int, stderr: str, stdout: str) -> bool:
    if rc == 0:
        return False
    return bool(_AUTH_REGEX.search(f"{stderr or ''}\n{stdout or ''}"))


def _load_cfg() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cfg(cfg: dict) -> None:
    try:
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.error(f"[LLM-FALLBACK] save config failed: {e}")


def _load_state() -> dict:
    try:
        return json.loads(FALLBACK_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"overrides": {}}


def _save_state(s: dict) -> None:
    try:
        FALLBACK_STATE_FILE.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _deepseek_for(claude_model: str) -> dict:
    m = (claude_model or "").lower()
    # Note: trader_brain._call_deepseek now resolves these to V4-Pro/Flash
    # via its own mapping (reasoning=True → v4-pro, reasoning=False → v4-flash).
    # We keep the abstract names ("chat"/"reasoner") here for compatibility with
    # brain_llm_config.json — they are tier hints, not literal model IDs.
    if m == "haiku":
        return {"provider": "deepseek", "model": "chat"}      # → deepseek-v4-flash
    # opus / sonnet / anything else → highest tier fallback
    return {"provider": "deepseek", "model": "reasoner"}      # → deepseek-v4-pro


# Map the `label` used inside trader_brain._call_claude to the role key in
# brain_llm_config.json. Any unknown label is normalized via .lower().
_LABEL_TO_ROLE = {
    "INDICATOR": "indicator",
    "EXECUTOR": "executor",
    "ZONE_REVIEWER": "reviewer",
    "INTERPRETER": "interpreter",
    "FILTER": "filter",  # not in matrix, but safe
}


def _role_key(label: str) -> str:
    if not label:
        return ""
    return _LABEL_TO_ROLE.get(label, label.lower())


def is_limit_error(rc: int, stderr: str, stdout: str) -> bool:
    """True if the process output suggests Claude hit a quota/rate/credit limit."""
    if rc == 0:
        return False
    blob = f"{stderr or ''}\n{stdout or ''}"
    return bool(_LIMIT_REGEX.search(blob))


def classify_failure(rc: int, stderr: str, stdout: str, timeout: bool = False,
                     empty_response: bool = False) -> str | None:
    """Classify a Claude CLI failure. Returns the failure reason or None if OK.

    Categories (any of these triggers a fallback to DeepSeek):
      · 'rate_limit'     — regex match on quota/credit patterns
      · 'timeout'        — subprocess.TimeoutExpired raised
      · 'empty_response' — rc=0 but no parsable JSON
      · 'cli_error'      — rc != 0 with stderr present
    """
    if timeout:
        return "timeout"
    if _is_auth_error(rc, stderr, stdout):
        return "auth_error"
    # 2026-05-06: distingir 529 overloaded (servidors Anthropic saturats)
    # de rate_limit (quota d'usuari). Mateix tractament (fallback DeepSeek)
    # però missatge més clar al log/TG.
    if _is_overloaded_error(rc, stderr, stdout):
        return "overloaded"
    if is_limit_error(rc, stderr, stdout):
        return "rate_limit"
    if rc != 0:
        return "cli_error"
    if empty_response:
        return "empty_response"
    return None


def mark_success(role: str) -> None:
    """Reset the soft-failure counter for a role after a successful response.
    Prevents stale failures from triggering fallback after Claude has clearly
    recovered. Called from _call_claude when a response parses successfully.
    """
    role = _role_key(role)
    if not role:
        return
    try:
        with _lock:
            state = _load_state()
            soft = state.get("soft_counters", {})
            if role in soft and soft[role].get("count", 0) > 0:
                soft[role]["count"] = 0
                state["soft_counters"] = soft
                _save_state(state)
    except Exception:
        pass


def detect_and_switch(role: str, stderr: str = "", stdout: str = "", rc: int = 0,
                      timeout: bool = False, empty_response: bool = False,
                      duration_min: int = FALLBACK_DURATION_MIN) -> bool:
    """Switch `role` to DeepSeek on ANY Claude failure, with exponential backoff
    and TG-spam silencing for repeated failures.

    Returns True if a NEW (notified) switch happened.

    Anti-spam rules:
      · If this role has a failure_history with a recent (<4h) entry, treat
        this as a RECURRING failure: extend the fallback duration (exponential
        backoff) AND skip the TG notification.
      · If already in active fallback: silent refresh only.

    MASTER KILL-SWITCH: if brain_llm_config.json has `auto_fallback: false`,
    this function is a no-op. The user's configured engine is respected
    verbatim — failures bubble up as errors, no auto-switching, no TG spam.
    """
    cfg_check = _load_cfg()
    if cfg_check.get("auto_fallback") is False:
        return False
    reason = classify_failure(rc, stderr, stdout, timeout=timeout, empty_response=empty_response)
    if not reason:
        return False

    # ── TIMEOUTS NEVER TRIGGER FALLBACK (2026-04-27) ──
    # Claude Opus 4.6 with high effort + 9KB system prompt + reasoning tokens
    # legitimately takes 60-300s. Treating that as "broken" and downgrading
    # to DeepSeek is wrong — the model IS working, just thinking. The
    # heartbeat thread (logs every 30s "still working...") proves life.
    # Fallback should happen ONLY for REAL errors: auth invalid, rate limit
    # exceeded, malformed CLI response (rc!=0 with actual error message).
    if reason == "timeout":
        log.info(f"[LLM-FALLBACK] timeout — not a failure, just slow. Will retry next cycle.")
        return False

    role = _role_key(role)
    if not role:
        return False
    with _lock:
        cfg = _load_cfg()
        state = _load_state()
        cur = cfg.get(role) or {}
        overrides = state.setdefault("overrides", {})
        history = state.setdefault("history", {})
        already_fallbacked = role in overrides

        # ── SOFT FAILURE COUNTER (2026-04-27) ──
        # Empty response or CLI error: a single hiccup might be a transient
        # flake (Claude API momentary glitch, network blip). Only fallback
        # after 3 CONSECUTIVE soft failures within 10 min — most blips
        # recover by themselves. Hard errors (auth_error, rate_limit) bypass
        # this and fallback immediately since the user has to fix something.
        SOFT_REASONS = ("empty_response", "cli_error")
        if reason in SOFT_REASONS and not already_fallbacked:
            soft_counters = state.setdefault("soft_counters", {})
            cnt_state = soft_counters.setdefault(role, {"count": 0, "first_ts": 0.0})
            now = time.time()
            # Reset if last failure was >10 min ago (transient flake recovered)
            if now - cnt_state.get("first_ts", 0) > 600:
                cnt_state["count"] = 0
                cnt_state["first_ts"] = now
            cnt_state["count"] = int(cnt_state.get("count", 0)) + 1
            cnt_state["last_ts"] = now
            cnt_state["last_reason"] = reason
            soft_counters[role] = cnt_state
            _save_state(state)
            if cnt_state["count"] < 3:
                log.info(
                    f"[LLM-FALLBACK] {role}: soft failure #{cnt_state['count']}/3 ({reason}) "
                    f"— letting Claude retry, won't fallback yet"
                )
                return False
            # 3+ consecutive soft failures within 10 min → trigger fallback
            log.warning(
                f"[LLM-FALLBACK] {role}: 3 consecutive soft failures ({reason}) "
                f"in 10 min — triggering fallback"
            )

        if cur.get("provider") != "claude" and not already_fallbacked:
            return False
        fb = _deepseek_for(cur.get("model") if cur.get("provider") == "claude"
                            else overrides.get(role, {}).get("original", {}).get("model", "opus"))
        # If already fallbacked, keep the ORIGINAL config (don't overwrite) but
        # refresh the timestamp so it keeps running. Silent update, no TG notif.
        if already_fallbacked:
            overrides[role]["last_retry_failure_ts"] = time.time()
            overrides[role]["last_retry_failure_reason"] = reason
            _save_state(state)
            log.debug(f"[LLM-FALLBACK] {role}: still in fallback (new failure: {reason}) — no re-notify")
            return False

        # ── Backoff logic: look at recent fallback history for this role ──
        now = time.time()
        role_hist = history.setdefault(role, [])
        # Auth errors are NOT self-recovering — the user must run `claude
        # setup-token`. Use a long backoff (24h) and notify at most once every
        # 6h globally (not per-role) to avoid spam across 3 roles.
        if reason == "auth_error":
            # Short window (10min): the watchdog injects .env on respawn, so
            # auth-failures self-heal within 1-2 watchdog cycles. The user's
            # config is sacred — we never want a transient auth blip to lock
            # us into DeepSeek for hours.
            eff_duration_min = 10
            last_auth_notif = state.get("last_auth_notif_ts", 0)
            silent = (now - last_auth_notif) < 6 * 3600
            if not silent:
                state["last_auth_notif_ts"] = now
            recent_count = 0
        else:
            RECENT_WIN_S = 4 * 3600  # 4h — wider window to silence TG spam:
            # only the FIRST fallback in 4h triggers a notification; repeated
            # failures within 4h stay silent (still fallback, just no TG).
            recent_count = sum(1 for h in role_hist if (now - h.get("ts", 0)) <= RECENT_WIN_S)
            # Exponential: 60m → 120m → 240m → 480m → 960m (16h max)
            BACKOFF_LADDER = [60, 120, 240, 480, 960]
            eff_duration_min = BACKOFF_LADDER[min(recent_count, len(BACKOFF_LADDER) - 1)]
            silent = recent_count > 0  # recurring failure → no TG spam

        overrides[role] = {
            "original": cur,
            "fallback": fb,
            "activated_ts": now,
            "restore_after_ts": now + eff_duration_min * 60,
            "failure_reason": reason,
            "reason_excerpt": (stderr or stdout or f"[{reason}]")[:240],
            "backoff_step": recent_count,
            "silent": silent,
        }
        # Record this failure in history (keep last 10 per role, within 24h)
        role_hist.append({"ts": now, "reason": reason})
        role_hist = [h for h in role_hist[-10:] if (now - h.get("ts", 0)) <= 86400]
        history[role] = role_hist

        # NEVER mutate the user's config (cfg). Overrides live in `state`
        # only — _get_llm_config in trader_brain.py reads them first. When
        # the override expires the user's choice is back in effect with no
        # extra step. Sacred-config policy added 2026-04-29.
        _save_state(state)
        log.warning(
            f"[LLM-FALLBACK] {role}: Claude {cur.get('model')} → DeepSeek "
            f"{fb['model']} for {eff_duration_min}m (reason: {reason} · "
            f"backoff_step={recent_count}/{len(BACKOFF_LADDER)-1} · "
            f"silent={silent} · {overrides[role]['reason_excerpt'][:60]})"
        )
        if not silent:
            try:
                from trader_brain import notify
                if reason == "auth_error":
                    msg = (f"🔑 Claude AUTH invàlid (tots els rols fallback a DeepSeek 24h). "
                           f"Cal executar `claude setup-token` a la consola. "
                           f"Fins que ho facis, el sistema opera amb DeepSeek.")
                elif reason == "overloaded":
                    msg = (f"☁️ Servidors d'Anthropic saturats (529 overloaded) — "
                           f"{role} fallback a DeepSeek {fb['model']} {eff_duration_min}min. "
                           f"NO és problema de quota/crèdit teu — és Anthropic que té sobrecàrrega global.")
                else:
                    msg = (f"⚠️ LLM fallback: {role} Claude {cur.get('model')} → "
                           f"DeepSeek {fb['model']} ({eff_duration_min}m). Motiu: {reason}.")
                notify("dd_alert", msg)
            except Exception:
                pass
        return not silent


_last_restore_check = 0.0


def check_restore() -> list[str]:
    """Restore any expired role overrides. Called from the main loop; throttled
    to once per minute. Returns list of roles restored this call.

    No-op when auto_fallback is disabled — there's nothing to restore because
    detect_and_switch refused to override anything.
    """
    global _last_restore_check
    now = time.time()
    cfg_check = _load_cfg()
    if cfg_check.get("auto_fallback") is False:
        return []
    if now - _last_restore_check < CHECK_RESTORE_PROBE_SECS:
        return []
    _last_restore_check = now
    restored: list[str] = []
    restored_notify: list[str] = []  # only roles whose fallback wasn't silent
    with _lock:
        state = _load_state()
        overrides = state.get("overrides", {})
        if not overrides:
            return []
        for role, ov in list(overrides.items()):
            if now >= float(ov.get("restore_after_ts", 0)):
                # Override expired — just remove it. The user's config in
                # brain_llm_config.json is intact (sacred-config policy) so
                # _get_llm_config will return it automatically next call.
                del overrides[role]
                restored.append(role)
                if not ov.get("silent", False) and ov.get("failure_reason") != "auth_error":
                    restored_notify.append(role)
                log.info(f"[LLM-FALLBACK] {role}: override expired, user config back in effect "
                         f"(was {ov.get('fallback', {}).get('provider', '?')}/{ov.get('fallback', {}).get('model', '?')}, "
                         f"backoff_step={ov.get('backoff_step', 0)})")
        if restored:
            state["overrides"] = overrides
            _save_state(state)
            # Only notify TG for restorations of non-silent fallbacks (skip the
            # backoff-induced quiet ones — otherwise we'd spam on every cycle).
            if restored_notify:
                try:
                    from trader_brain import notify
                    notify("dd_alert",
                           f"✅ LLM restaurat a Claude per: {', '.join(restored_notify)}")
                except Exception:
                    pass
    return restored


def status() -> dict:
    """Current fallback state for dashboards / debug."""
    state = _load_state()
    return state.get("overrides", {})
