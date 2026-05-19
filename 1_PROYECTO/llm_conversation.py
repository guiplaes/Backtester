"""Multi-turn conversation manager for cost-optimised DeepSeek calls.

Each role (HUNTER, ZONE_REVIEWER, …) keeps its own rolling chat history
on disk. The point: re-using `[system, user_1, assistant_1, …, user_N]`
across calls means everything before the new turn is cache-readable
(DeepSeek auto-cache works on identical prefix). Only the new user turn
costs full input price.

For this to actually save money the new user turn must be SMALL — i.e.
the caller passes a "delta" payload (just what changed since last call)
rather than the full state. Sending full state each turn would COST MORE
than single-turn (we measured: full-state multi-turn is ~35% pricier
than current single-turn for HUNTER; delta multi-turn is ~50% cheaper).

Reset triggers (caller's responsibility — call reset(role) when):
  - Active signal closes (EXECUTOR conversation should restart fresh)
  - System prompt or model changes
  - Long inactivity (handled here automatically — see INACTIVITY_RESET_S)
"""
import json
import os
import threading
import time
from datetime import datetime, timezone

COMMON = r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files"
CONV_DIR = os.path.join(COMMON, 'brain_llm_conversations')

# Sliding window: keep at most this many user/assistant TURN PAIRS
WINDOW_TURNS = 5

# After this many seconds without a call, treat as fresh start (DeepSeek
# cache TTL is "hours to days" but trade context after 30min of silence
# is rarely useful — and inactivity often means a regime change).
INACTIVITY_RESET_S = 1800

_lock = threading.Lock()


def _conv_path(role: str) -> str:
    os.makedirs(CONV_DIR, exist_ok=True)
    safe = (role or 'unknown').lower().replace('/', '_').replace('\\', '_')
    return os.path.join(CONV_DIR, f'{safe}.json')


def load(role: str) -> list:
    """Return the recent message list for `role`, or [] if empty/expired.

    Schema per message: {role: 'user'|'assistant', content: str, ts: float}
    """
    path = _conv_path(role)
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        last_ts = float(data.get('last_ts', 0) or 0)
        if last_ts and (time.time() - last_ts) > INACTIVITY_RESET_S:
            return []
        return data.get('messages', []) or []
    except Exception:
        return []


def _trim(messages: list, max_turns: int = WINDOW_TURNS) -> list:
    """Drop oldest turn pairs so we keep at most `max_turns` user turns."""
    user_idx = [i for i, m in enumerate(messages) if m.get('role') == 'user']
    if len(user_idx) <= max_turns:
        return messages
    cut = user_idx[-max_turns]
    return messages[cut:]


def save(role: str, messages: list):
    """Persist trimmed messages to disk."""
    trimmed = _trim(messages)
    with _lock:
        try:
            with open(_conv_path(role), 'w', encoding='utf-8') as f:
                json.dump({
                    'role': role,
                    'updated_at': datetime.now(timezone.utc).isoformat(),
                    'last_ts': time.time(),
                    'messages': trimmed,
                }, f, indent=2, ensure_ascii=False)
        except Exception:
            pass


def append_pair(role: str, user_content: str, assistant_content: str):
    """Append a (user, assistant) turn pair. Trims and saves."""
    history = load(role)
    now = time.time()
    history.append({'role': 'user', 'content': user_content, 'ts': now})
    history.append({'role': 'assistant', 'content': assistant_content, 'ts': now})
    save(role, history)


def reset(role: str):
    """Wipe history for one role."""
    path = _conv_path(role)
    if os.path.exists(path):
        try:
            os.unlink(path)
        except Exception:
            pass


def reset_all():
    """Wipe every role's history (e.g. on prompt-file change)."""
    if os.path.isdir(CONV_DIR):
        for fname in os.listdir(CONV_DIR):
            try:
                os.unlink(os.path.join(CONV_DIR, fname))
            except Exception:
                pass


def is_first_turn(role: str) -> bool:
    """True if there's no prior context — caller should send a FULL snapshot
    so the LLM has the baseline."""
    return len(load(role)) == 0


def turns_count(role: str) -> int:
    """Number of user turns in the rolling window."""
    return sum(1 for m in load(role) if m.get('role') == 'user')


def stats() -> dict:
    """Inspection helper for the dashboard."""
    out = {}
    if not os.path.isdir(CONV_DIR):
        return out
    for fname in os.listdir(CONV_DIR):
        if not fname.endswith('.json'):
            continue
        role = fname[:-5]
        msgs = load(role)
        out[role] = {
            'turns': sum(1 for m in msgs if m.get('role') == 'user'),
            'last_ts': max((m.get('ts', 0) for m in msgs), default=0),
            'total_chars': sum(len(m.get('content', '')) for m in msgs),
        }
    return out
