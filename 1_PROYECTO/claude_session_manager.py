"""Claude CLI session manager — multi-turn via `--session-id` + `--resume`.

The Claude Code CLI supports persistent sessions in -p (print) mode. By
keeping a session per role, every follow-up call only sends the new user
turn; the entire system_prompt + conversation history hits the prompt
cache (cache_read at ~10% of normal input cost). Verified empirically
2026-05-02: cache_read=14188 stable across calls within the same session.

Session lifecycle (caller's responsibility):
  - get_or_create(role)  → returns session_id, marks `is_first` flag
  - mark_used(role)      → after a successful call, flips `is_first=False`
  - reset(role)          → wipe; next get_or_create creates fresh UUID
  - reset_all_stale()    → housekeeping for sessions older than 4h

Trade-aware roles (EXECUTOR) reset on trade close.
Indicator-style roles reset daily at 00:00 UTC or on regime change.
"""
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone

COMMON = r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files"
SESSIONS_FILE = os.path.join(COMMON, 'brain_claude_sessions.json')

# After this many seconds without use, treat session as stale (cache likely
# expired anyway and we don't want infinite-growing context). Force fresh
# session on next call.
STALE_TIMEOUT_S = 4 * 3600  # 4 hours

_lock = threading.Lock()


def _load() -> dict:
    if not os.path.exists(SESSIONS_FILE):
        return {}
    try:
        with open(SESSIONS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save(state: dict):
    with _lock:
        try:
            with open(SESSIONS_FILE, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass


def get_or_create(role: str) -> tuple[str, bool]:
    """Return (session_id, is_first_turn).

    is_first_turn is True iff this is a brand-new session (no prior calls).
    Caller should send a FULL snapshot in that case; otherwise a delta is
    enough since the prior conversation is in the session.
    """
    role = (role or 'unknown').upper()
    state = _load()
    entry = state.get(role)
    now = time.time()
    if entry:
        last_used = float(entry.get('last_used_ts', 0) or 0)
        if last_used and (now - last_used) < STALE_TIMEOUT_S:
            return entry['session_id'], not entry.get('used', False)
        # stale — fall through to recreate
    new_id = str(uuid.uuid4())
    state[role] = {
        'session_id': new_id,
        'created_ts': now,
        'last_used_ts': now,
        'used': False,
        'turn_count': 0,
    }
    _save(state)
    return new_id, True


def mark_used(role: str):
    """Flip used=True after a successful call. Increment turn count."""
    role = (role or 'unknown').upper()
    state = _load()
    entry = state.get(role)
    if not entry:
        return
    entry['used'] = True
    entry['last_used_ts'] = time.time()
    entry['turn_count'] = int(entry.get('turn_count', 0) or 0) + 1
    state[role] = entry
    _save(state)


def reset(role: str):
    """Wipe session for one role. Next call creates a fresh UUID."""
    role = (role or 'unknown').upper()
    state = _load()
    if role in state:
        del state[role]
        _save(state)


def reset_all():
    """Wipe every role's session (e.g. on prompt-file change or daily reset)."""
    _save({})


def reset_stale():
    """Drop entries older than STALE_TIMEOUT_S. Returns count dropped."""
    state = _load()
    now = time.time()
    fresh = {
        role: e for role, e in state.items()
        if (now - float(e.get('last_used_ts', 0) or 0)) < STALE_TIMEOUT_S
    }
    dropped = len(state) - len(fresh)
    if dropped > 0:
        _save(fresh)
    return dropped


def stats() -> dict:
    """Inspection helper for the dashboard / debug."""
    state = _load()
    out = {}
    now = time.time()
    for role, e in state.items():
        out[role] = {
            'session_id': e.get('session_id'),
            'turns': e.get('turn_count', 0),
            'age_min': round((now - float(e.get('created_ts', 0) or 0)) / 60, 1),
            'idle_min': round((now - float(e.get('last_used_ts', 0) or 0)) / 60, 1),
            'used': e.get('used', False),
        }
    return out


def is_first_turn(role: str) -> bool:
    """Convenience — does NOT create a session, just checks state."""
    role = (role or 'unknown').upper()
    state = _load()
    entry = state.get(role)
    if not entry:
        return True
    last_used = float(entry.get('last_used_ts', 0) or 0)
    if last_used and (time.time() - last_used) >= STALE_TIMEOUT_S:
        return True
    return not entry.get('used', False)
