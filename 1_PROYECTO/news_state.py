"""
news_state — Pending high-impact news tracker for the Brain.

Receives 🚨 events from FX Markets Telegram data channel and exposes:
  - high_impact_within(minutes): whether a HIGH event is imminent (gate new entries)
  - pending_summary(): human string for the status snapshot console line
  - parse_fx_message(text, dt): regex parser for FX Markets 🚨 format

State persisted to brain_news_state.json so a Brain restart doesn't lose
events that were received minutes ago and are still active.

Importance classification (XAUUSD-tuned, more aggressive than v21 R-coef):
  HIGH: NFP/empleo, CPI, FOMC/Fed/Powell/tipos, PIB/GDP, PCE, ECB, BoE
  MED:  Michigan, PMI, ISM, retail/ventas, pedidos
  LOW:  everything else flagged with 🚨

Block window for HIGH = receipt_time .. (event_time + 30 min).
"""

from __future__ import annotations
import json
import os
import re
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

COMMON = r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files"
STATE_FILE = os.path.join(COMMON, 'brain_news_state.json')

_lock = threading.Lock()
_events: list[dict] = []  # each: {received_at, event_time, importance, text, key}
_loaded = False

HIGH_KW = (
    'nfp', 'nonfarm', 'non-farm', 'non farm',
    'cpi', 'inflation', 'inflación',
    'fomc', 'fed', 'powell', 'tipos', 'interest rate', 'rate decision',
    'pib', 'gdp', 'pce',
    'ecb', 'bce', 'lagarde',
    'boe', 'bailey',
    'empleo', 'desempleo', 'nóminas', 'nominas',
)
MED_KW = (
    'michigan', 'pmi', 'ism',
    'pedidos', 'duraderos',
    'ventas', 'retail',
    'confianza', 'consumer',
)


def _classify(text: str) -> str:
    t = (text or '').lower()
    if any(k in t for k in HIGH_KW):
        return 'HIGH'
    if any(k in t for k in MED_KW):
        return 'MED'
    return 'LOW'


def _save_locked():
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        payload = {
            'events': [
                {
                    'received_at': e['received_at'].isoformat(),
                    'event_time': e['event_time'].isoformat(),
                    'importance': e['importance'],
                    'text': e['text'],
                    'key': e['key'],
                }
                for e in _events
            ],
        }
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass


def _load_once():
    global _loaded
    if _loaded:
        return
    _loaded = True
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for e in data.get('events', []):
            _events.append({
                'received_at': datetime.fromisoformat(e['received_at']),
                'event_time': datetime.fromisoformat(e['event_time']),
                'importance': e.get('importance', 'LOW'),
                'text': e.get('text', ''),
                'key': e.get('key', ''),
            })
    except Exception:
        pass


def _prune_locked(now: datetime):
    """Drop events whose post-window (+30 min) has passed."""
    global _events
    cutoff = now - timedelta(minutes=30)
    _events = [e for e in _events if e['event_time'] > cutoff]


def parse_fx_message(text: str, msg_dt: datetime) -> Optional[dict]:
    """If text is a 🚨 FX Markets news warning, return event dict; else None."""
    if not text or '\U0001f6a8' not in text:
        return None
    m = re.search(r'(\d+)\s*minut', text.lower())
    if not m:
        return None
    try:
        minutes = int(m.group(1))
    except Exception:
        return None
    if minutes <= 0 or minutes > 240:
        return None
    if msg_dt.tzinfo is None:
        msg_dt = msg_dt.replace(tzinfo=timezone.utc)
    event_time = msg_dt + timedelta(minutes=minutes)
    importance = _classify(text)
    return {
        'received_at': msg_dt,
        'event_time': event_time,
        'importance': importance,
        'text': text.strip()[:200],
        'key': f"{int(event_time.timestamp())}:{importance}",
    }


def add_event(event: dict) -> bool:
    """Add a parsed event. Idempotent on `key`. Returns True if new."""
    if not event:
        return False
    with _lock:
        _load_once()
        _prune_locked(datetime.now(timezone.utc))
        if any(e['key'] == event['key'] for e in _events):
            return False
        _events.append(event)
        _save_locked()
    return True


def high_impact_within(minutes: int = 30) -> Optional[dict]:
    """Return the most imminent HIGH event whose event_time is in [now, now+minutes],
    or that is currently in its post-window (event passed less than 30min ago).
    Returns None if clear."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(minutes=minutes)
    with _lock:
        _load_once()
        _prune_locked(now)
        candidates = [
            e for e in _events
            if e['importance'] == 'HIGH'
            and (now <= e['event_time'] <= horizon
                 or (e['event_time'] <= now <= e['event_time'] + timedelta(minutes=30)))
        ]
    if not candidates:
        return None
    candidates.sort(key=lambda e: e['event_time'])
    return candidates[0]


def get_active_events() -> list[dict]:
    """All non-expired events (any importance)."""
    now = datetime.now(timezone.utc)
    with _lock:
        _load_once()
        _prune_locked(now)
        return list(_events)


def pending_summary() -> str:
    """One-line summary for the console header. e.g.:
       'NEWS: clear'
       'NEWS: NFP HIGH in 12min ⛔'
       'NEWS: PMI MED in 4min · live'
    """
    now = datetime.now(timezone.utc)
    with _lock:
        _load_once()
        _prune_locked(now)
        if not _events:
            return 'NEWS: clear'
        upcoming = sorted(_events, key=lambda e: e['event_time'])
        e = upcoming[0]
    delta_min = (e['event_time'] - now).total_seconds() / 60.0
    tag = '⛔' if e['importance'] == 'HIGH' else '·'
    if delta_min >= 0:
        when = f"in {int(delta_min)}min"
    else:
        when = f"live ({int(-delta_min)}min ago)"
    short = re.sub(r'\s+', ' ', e['text'])[:48]
    return f"NEWS: {e['importance']} {when} {tag} {short}"


def session_label(now_utc: Optional[datetime] = None) -> str:
    """UTC-hour-based session label aligned with market_context._session_name."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    h = now_utc.hour
    if 0 <= h < 7:
        return 'ASIA'
    if 7 <= h < 13:
        return 'LONDON'
    if 13 <= h < 16:
        return 'OVERLAP'
    if 16 <= h < 21:
        return 'NY'
    return 'DEAD'


# ── Session character (from session_anatomy analysis 2026-04-25) ──────
#
# Range H1 mediana per session in USD (XAUUSD, ~17 days April 2026):
#   OVERLAP $27.5 · LONDON $17.4 · ASIA $16.1 · DEAD $14.5 · NY $14.2
#
# session_factor scales R-unit (= 2 × ATR_M5) so partial hints (1R, 2R)
# match the typical reversion magnitude in each session. The baseline is
# LONDON (factor 1.00). Other sessions scaled relative to their median
# range vs LONDON, with operational caveats:
#   · OVERLAP gets boosted to 1.50 — 100% of hours have ≥15$ range, can
#     stretch targets when momentum is alive.
#   · NY discounted to 0.85 — fade in late hours (19-21 UTC) drags median
#     below the structural number.
#   · DEAD also 0.85 — wider than expected in current sample but
#     liquidity caveats remain.
#
# Hour overrides catch specific dead spots even within "live" sessions:
#   03-04 UTC (Tokyo lunch, $11-12 range) and 19-20 UTC (NY fade) get a
#   forced 0.75 to dampen R targets there.
SESSION_FACTORS = {
    'ASIA':    0.92,
    'LONDON':  1.00,
    'OVERLAP': 1.50,
    'NY':      0.85,
    'DEAD':    0.85,
}

HOUR_OVERRIDES = {
    3:  0.75,   # Tokyo lunch (median range $11.9)
    4:  0.75,   # Tokyo lunch trough ($10.9)
    19: 0.80,   # NY fade begins ($12.3)
    20: 0.75,   # NY late fade ($11.9)
}


def session_factor(now_utc: Optional[datetime] = None) -> float:
    """Return the R-scaling factor for the current session/hour.

    Used to scale `r_unit = atr * 2.0 * session_factor()` so partial hints
    (1R, 2R) reflect the realistic reversion magnitude available in the
    current market window.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    h = now_utc.hour
    if h in HOUR_OVERRIDES:
        return HOUR_OVERRIDES[h]
    return SESSION_FACTORS.get(session_label(now_utc), 1.00)


def session_context() -> dict:
    """Compact dict for injecting into LLM prompts and console snapshots."""
    now = datetime.now(timezone.utc)
    label = session_label(now)
    factor = session_factor(now)
    enabled_map = _load_sessions_enabled()
    current_enabled = bool(enabled_map.get(label, True))
    sessions_on = [s for s in ('ASIA','LONDON','OVERLAP','NY','DEAD') if enabled_map.get(s)]
    return {
        'label': label,
        'hour_utc': now.hour,
        'r_factor': factor,
        'is_overlap': label == 'OVERLAP',
        'is_dead_or_late': label == 'DEAD' or now.hour in HOUR_OVERRIDES,
        # 2026-05-06: visibilitat REAL del estat de sessions perquè el LLM
        # no al·lucini "sessions OFF" quan en realitat estan ON.
        'current_enabled_for_new_entries': current_enabled,
        'sessions_enabled_for_new_entries': sessions_on,
    }


# ── Session enable/disable for new entries ────────────────────────────
# Defaults match config.yaml `sessions_enabled` block. If config can't be
# loaded for any reason, fall back to "all sessions enabled" (most permissive,
# preserves historical behaviour pre-2026-04-26).
DEFAULT_SESSIONS_ENABLED = {
    'ASIA': True, 'LONDON': True, 'OVERLAP': True, 'NY': True, 'DEAD': True,
}


def _load_sessions_enabled() -> dict:
    """Read sessions_enabled from config.yaml (cached per process). On any
    error returns DEFAULT (all True) — safer to over-trade than to silently
    block all entries due to a config typo."""
    try:
        import yaml, os
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.yaml')
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        sess = cfg.get('sessions_enabled') or {}
        out = dict(DEFAULT_SESSIONS_ENABLED)
        for k, v in sess.items():
            ku = str(k).upper()
            if ku in out:
                out[ku] = bool(v)
        return out
    except Exception:
        return dict(DEFAULT_SESSIONS_ENABLED)


def is_session_enabled(now_utc: Optional[datetime] = None) -> bool:
    """Return True if the current session is enabled in config.yaml for new
    trades. Always re-reads config so toggles take effect without restart."""
    label = session_label(now_utc)
    enabled = _load_sessions_enabled()
    return bool(enabled.get(label, True))


def sessions_enabled_summary() -> str:
    """For status header: 'sessions: LONDON+NY+OVERLAP' (only enabled ones)."""
    enabled = _load_sessions_enabled()
    on = [s for s in ('ASIA','LONDON','OVERLAP','NY','DEAD') if enabled.get(s)]
    return '+'.join(on) if on else 'NONE'
