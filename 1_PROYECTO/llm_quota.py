"""Claude subscription quota tracker — protects against rate-limit hits.

Anthropic Claude Code subscriptions (Pro / Max 5× / Max 20×) cap usage
in 5-hour rolling windows. Hitting the cap returns 429s and the brain
loses Claude availability mid-trade — we want to fall back to DeepSeek
BEFORE that happens.

Strategy:
  1. After each Claude call, log (ts, tokens_charged) into a sliding
     5-hour window file.
  2. Before each Claude call, check what % of the tier limit we've used.
  3. If above SAFE_THRESHOLD (default 0.80), the dispatcher routes the
     call to DeepSeek instead of Claude (graceful downgrade).

Token accounting:
  - cache_read tokens count at ~0.10× of base input (per Anthropic).
  - cache_write 5min counts at ~1.25× of base input.
  - We use the RAW total billed = input + cache_creation + cache_read
    for the simplest defensive metric (overestimates impact, which is
    safer than underestimating and getting cut off).

Tier limits (rough — Anthropic doesn't publish exact token caps; these
are conservative estimates from observed behavior):
  - Pro       :  5M input-equivalent tokens / 5h
  - Max 5x    : 25M / 5h
  - Max 20x   : 100M / 5h
Override via env CLAUDE_TIER_LIMIT_TOKENS_5H if needed.
"""
import json
import os
import threading
import time
from datetime import datetime, timezone

COMMON = r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files"
QUOTA_FILE = os.path.join(COMMON, 'brain_claude_quota.json')

WINDOW_S = 5 * 3600  # 5 hours
SAFE_THRESHOLD = 0.80  # fall back to DeepSeek above this fraction

# Conservative token caps per 5h window. Override via env.
_TIER_DEFAULTS = {
    'pro':      5_000_000,
    'max5':    25_000_000,
    'max5x':   25_000_000,
    'max20':  100_000_000,
    'max20x': 100_000_000,
}


def _tier_limit() -> int:
    override = os.environ.get('CLAUDE_TIER_LIMIT_TOKENS_5H')
    if override:
        try:
            return int(override)
        except Exception:
            pass
    tier = (os.environ.get('CLAUDE_CODE_RATE_LIMIT_TIER') or '').strip().lower()
    # Normalise common variations that Anthropic emits in env
    # ('default_claude_max_5x', 'claude_max_20x', etc.)
    if 'max_20' in tier or 'max20' in tier:
        return _TIER_DEFAULTS['max20x']
    if 'max_5' in tier or 'max5' in tier:
        return _TIER_DEFAULTS['max5x']
    if 'pro' in tier:
        return _TIER_DEFAULTS['pro']
    return _TIER_DEFAULTS.get(tier, 25_000_000)  # default Max 5×


_lock = threading.Lock()


def _load() -> dict:
    if not os.path.exists(QUOTA_FILE):
        return {'events': []}
    try:
        with open(QUOTA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f) or {'events': []}
    except Exception:
        return {'events': []}


def _save(state: dict):
    with _lock:
        try:
            with open(QUOTA_FILE, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass


def _prune(state: dict):
    """Drop events older than the rolling window."""
    cutoff = time.time() - WINDOW_S
    state['events'] = [e for e in state.get('events', []) if e.get('ts', 0) >= cutoff]


def record(role: str, tokens: int, model: str = ''):
    """Log a Claude call's billable tokens against the rolling window."""
    state = _load()
    _prune(state)
    state['events'].append({
        'ts': time.time(),
        'role': role,
        'model': model,
        'tokens': int(max(0, tokens)),
    })
    _save(state)


def used_5h() -> int:
    """Sum of tokens spent against quota in the last 5 hours."""
    state = _load()
    _prune(state)
    return sum(int(e.get('tokens', 0) or 0) for e in state.get('events', []))


def usage_pct() -> float:
    """Returns 0.0-1.0 (or higher if over) of tier limit consumed."""
    limit = _tier_limit()
    if limit <= 0:
        return 0.0
    return used_5h() / limit


def should_fallback(role: str = '', threshold: float | None = None) -> bool:
    """True iff we should route THIS call away from Claude for quota safety."""
    pct = usage_pct()
    th = threshold if threshold is not None else SAFE_THRESHOLD
    return pct >= th


def status() -> dict:
    """Inspection helper."""
    pct = usage_pct()
    used = used_5h()
    limit = _tier_limit()
    state = _load()
    return {
        'tier': os.environ.get('CLAUDE_CODE_RATE_LIMIT_TIER') or 'unknown',
        'limit_5h_tokens': limit,
        'used_5h_tokens': used,
        'pct': round(pct * 100, 1),
        'safe_threshold_pct': SAFE_THRESHOLD * 100,
        'fallback_active': pct >= SAFE_THRESHOLD,
        'events_in_window': len(state.get('events', [])),
    }
