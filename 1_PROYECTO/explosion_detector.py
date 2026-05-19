"""
explosion_detector — Detect explosive market regime where structural levels
break with abnormal context.

Pure functional state: ON when conditions current, OFF when they clear. No
time minimums, no latch.

EXPLOSION_ON when ALL true:
  1. Recent STRONG zone clean-break adverse to signal direction (within 30min)
  2. At least one anomaly indicator currently active:
     · Last 3 M5 same direction with vol_avg ≥ 1.8× normal
     · Last M5 range ≥ 2× ATR_M15
     · Velocity (3-bar M5 close-to-close cumulative) ≥ 3× session typical

When detector active:
  · Snipers freeze (no fire even on touch)
  · FAST continues normal (its own confirmation gate is enough)
  · Executor receives `explosion_state` in payload — proposes levels with
    confirmation_required=high, no snipers

Persistence:
  · brain_explosion_breaks.json — rolling list of STRONG break events used
    to evaluate criterion #1. Pruned to last 30 min on each read.

Public API:
  record_strong_break(zone_price, signal_direction, vol_ratio, close_beyond_usd)
  evaluate(bars_m5, atr_m15, signal_direction=None) -> dict
  is_active() -> bool   # convenience for gate callers
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

COMMON = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files")
BREAKS_FILE = COMMON / "brain_explosion_breaks.json"
STATE_FILE = COMMON / "brain_explosion_state.json"  # last computed state, for status snapshot

_lock = threading.Lock()

# ── Tunable thresholds ────────────────────────────────────────────────
WINDOW_MIN = 30                   # rolling window for STRONG breaks
ANOMALY_VOL_RATIO = 1.8           # last 3 M5 vol_avg >= this × normal
ANOMALY_RANGE_VS_ATR = 2.0        # last M5 range >= this × ATR_M15
ANOMALY_VELOCITY_X = 3.0          # 3-bar velocity vs session typical
NORMAL_VELOCITY_USD_PER_BAR = 5.0 # baseline M5 close-to-close magnitude (XAU)


# ── Persistence ───────────────────────────────────────────────────────
def _load_breaks() -> list[dict]:
    if not BREAKS_FILE.exists():
        return []
    try:
        with open(BREAKS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("breaks", []) or []
    except Exception:
        return []


def _save_breaks(breaks: list[dict]) -> None:
    try:
        BREAKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(BREAKS_FILE, "w", encoding="utf-8") as f:
            json.dump({"breaks": breaks}, f, indent=2)
    except Exception:
        pass


def _prune(breaks: list[dict], window_min: int = WINDOW_MIN) -> list[dict]:
    cutoff = time.time() - window_min * 60
    return [b for b in breaks if b.get("ts", 0) >= cutoff]


def _save_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


# ── Public: record a structural break ─────────────────────────────────
def record_strong_break(zone_price: float, signal_direction: Optional[str],
                        vol_ratio: float, close_beyond_usd: float) -> None:
    """Called by zone_lifecycle when a STRONG zone is clean-broken.

    `signal_direction` is the direction of the active signal at the time of
    the break, used later to filter "adverse" breaks. Pass None if no active
    signal — in that case the break is recorded but won't trigger explosion
    (sniper gating is per-signal anyway).
    """
    event = {
        "ts": time.time(),
        "utc": datetime.now(timezone.utc).isoformat(),
        "zone_price": round(float(zone_price), 2),
        "signal_direction": signal_direction or None,
        "vol_ratio": round(float(vol_ratio), 2),
        "close_beyond_usd": round(float(close_beyond_usd), 2),
    }
    with _lock:
        breaks = _prune(_load_breaks())
        breaks.append(event)
        _save_breaks(breaks)


# ── Anomaly indicators (computed from current bars) ───────────────────
def _last3_consecutive_same_dir_with_vol(bars_m5: list, vol_avg_lookback: int = 20) -> Optional[str]:
    """Return 'up' / 'down' / None depending on whether last 3 closed M5 bars
    move same direction with vol_avg >= ANOMALY_VOL_RATIO × N-bar avg."""
    if len(bars_m5) < max(4, vol_avg_lookback + 3):
        return None
    last3 = bars_m5[-3:]
    # Direction: each bar close vs open
    dirs = [(1 if b.get("close", 0) > b.get("open", 0) else -1) for b in last3]
    if not (all(d == 1 for d in dirs) or all(d == -1 for d in dirs)):
        return None
    # Volume check vs last N before last3
    earlier = bars_m5[-(vol_avg_lookback + 3):-3]
    if not earlier:
        return None
    vols_earlier = [b.get("volume", 0) for b in earlier]
    avg_v = sum(vols_earlier) / len(vols_earlier) if vols_earlier else 0
    if avg_v <= 0:
        return None
    vols_last3 = [b.get("volume", 0) for b in last3]
    avg_last3 = sum(vols_last3) / 3
    if avg_last3 < ANOMALY_VOL_RATIO * avg_v:
        return None
    return "up" if dirs[0] == 1 else "down"


def _last_m5_extreme_range(bars_m5: list, atr_m15: float) -> bool:
    if not bars_m5 or atr_m15 <= 0:
        return False
    last = bars_m5[-1]
    rng = (last.get("high", 0) - last.get("low", 0))
    return rng >= ANOMALY_RANGE_VS_ATR * atr_m15


def _velocity_score(bars_m5: list) -> float:
    """Return last-3-bar cumulative |close-to-close| in USD divided by
    NORMAL_VELOCITY_USD_PER_BAR × 3. Score >= ANOMALY_VELOCITY_X = anomalous."""
    if len(bars_m5) < 4:
        return 0.0
    cum = 0.0
    for i in range(-3, 0):
        cum += abs(bars_m5[i].get("close", 0) - bars_m5[i - 1].get("close", 0))
    return cum / (NORMAL_VELOCITY_USD_PER_BAR * 3)


# ── Main evaluator ────────────────────────────────────────────────────
def evaluate(bars_m5: list, atr_m15: float,
             signal_direction: Optional[str] = None) -> dict:
    """Evaluate current explosion state. Pure function over current data.

    Returns a dict with `active`, the reasons each criterion fired (or didn't),
    and an `adverse_count_30min` so callers can show context.

    The state dict is also persisted to brain_explosion_state.json so that
    log_status_snapshot and the dashboard can read it cheaply.
    """
    now = time.time()

    # ── Criterion 1: recent adverse STRONG break ──
    breaks = _prune(_load_breaks())
    if signal_direction:
        # "Adverse" = break against signal direction. A break beyond a SUPPORT
        # is bearish; beyond RESISTANCE bullish. We don't store type, only that
        # break happened; signal_direction at time of break is the proxy: if
        # the active trade was BUY when its support broke, that's adverse.
        adverse_breaks = [b for b in breaks
                          if b.get("signal_direction") == signal_direction]
    else:
        adverse_breaks = breaks
    crit1_active = len(adverse_breaks) >= 1

    # ── Criterion 2: any anomaly active now ──
    anomaly_dir = _last3_consecutive_same_dir_with_vol(bars_m5)
    anomaly_3bars = anomaly_dir is not None
    anomaly_range = _last_m5_extreme_range(bars_m5, atr_m15)
    velocity = _velocity_score(bars_m5)
    anomaly_velocity = velocity >= ANOMALY_VELOCITY_X
    crit2_active = anomaly_3bars or anomaly_range or anomaly_velocity

    active = crit1_active and crit2_active

    state = {
        "active": active,
        "evaluated_at": now,
        "evaluated_utc": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        "signal_direction": signal_direction,
        "criteria": {
            "recent_adverse_strong_break": {
                "fired": crit1_active,
                "adverse_count_30min": len(adverse_breaks),
                "total_breaks_30min": len(breaks),
                "most_recent_break": adverse_breaks[-1] if adverse_breaks else None,
            },
            "anomaly_active": {
                "fired": crit2_active,
                "consecutive_3bar_same_dir": anomaly_3bars,
                "consecutive_dir": anomaly_dir,
                "last_m5_extreme_range": anomaly_range,
                "velocity_x": round(velocity, 2),
                "velocity_anomalous": anomaly_velocity,
            },
        },
    }

    # Persist for status snapshot / dashboard
    _save_state(state)
    return state


def is_active(bars_m5: list = None, atr_m15: float = 0,
              signal_direction: Optional[str] = None) -> bool:
    """Convenience: returns just the active bool. If args missing, reads the
    last-saved state (cheap). Otherwise computes fresh."""
    if bars_m5 is None:
        try:
            if STATE_FILE.exists():
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    return bool(json.load(f).get("active"))
        except Exception:
            pass
        return False
    return evaluate(bars_m5, atr_m15, signal_direction).get("active", False)


def last_state() -> dict:
    """Read last-evaluated state from disk. Empty dict if missing."""
    try:
        if STATE_FILE.exists():
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}
