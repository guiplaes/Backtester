"""Dual-Frame Momentum Oscillator (DFMO) — Python port.

EXACT match to the MT5 indicator at MT5/MQL5/Indicators/DFMO_DualFrameMomentumOscillator.mq5
so Python decisions align 1:1 with the arrows shown on the user's chart.

Logic (verbatim from MT5 OnCalculate):

    SlowStochK = SMA(StochSmoothing) of raw_stoch_k(StochKPeriod)
    SlowStochD = SMA(StochDPeriod) of SlowStochK
    FastRSI    = iRSI(RSIPeriod, PRICE_CLOSE)

    prevBarOB = (StochK[prev] > OB) AND (RSI[prev] > OB)      # both above
    currBarOB = (StochK[curr] > OB) AND (RSI[curr] > OB)
    zoneEndOB = prevBarOB && !currBarOB                        # was in confluence, now broken

    (Same symmetric for OS with < instead of >)

Defaults match MT5 indicator: OB=80, OS=20, Stoch(25,4,4), RSI(3).

Operates on CLOSED bars: caller passes `bars[:-1]` (drop the live bar) so that
bar index -1 of this list is the last CLOSED bar (MQL5's shift=1), and index -2
is the bar before (MQL5's shift=2).
"""
from __future__ import annotations
from typing import Optional

__all__ = ["compute_dfmo", "dfmo_zone_end"]


def _raw_stoch_k(bars: list[dict], end_idx: int, period: int) -> Optional[float]:
    """Raw %K for the window ENDING at bars[end_idx] (inclusive), looking back
    `period` bars. Returns None if insufficient data.
    """
    start = end_idx - period + 1
    if start < 0 or end_idx >= len(bars):
        return None
    window = bars[start:end_idx + 1]
    highs = [float(b.get("high", 0) or 0) for b in window]
    lows = [float(b.get("low", 0) or 0) for b in window]
    close = float(window[-1].get("close", 0) or 0)
    hh = max(highs)
    ll = min(lows)
    rng = hh - ll
    if rng <= 0:
        return 50.0
    return (close - ll) / rng * 100.0


def _stoch_k_smoothed(bars: list[dict], end_idx: int, period: int, smoothing: int) -> Optional[float]:
    """SMA(smoothing) over the last `smoothing` raw_k values ending at end_idx."""
    vals: list[float] = []
    for i in range(end_idx - smoothing + 1, end_idx + 1):
        v = _raw_stoch_k(bars, i, period)
        if v is None:
            return None
        vals.append(v)
    return sum(vals) / len(vals)


def _rsi(closes: list[float], end_idx: int, period: int) -> Optional[float]:
    """Wilder RSI at bars[end_idx] (exclusive of later bars). Uses simple mean
    of gains/losses over the last `period` deltas — close enough to MT5 iRSI
    behavior for our detection purposes.
    """
    if end_idx < period:
        return None
    gains: list[float] = []
    losses: list[float] = []
    start = max(1, end_idx - period + 1)
    for i in range(start, end_idx + 1):
        d = closes[i] - closes[i - 1]
        gains.append(max(0.0, d))
        losses.append(max(0.0, -d))
    if not gains:
        return None
    avg_g = sum(gains) / len(gains)
    avg_l = sum(losses) / len(losses)
    if avg_l == 0:
        return 100.0 if avg_g > 0 else 50.0
    rs = avg_g / avg_l
    return 100.0 - (100.0 / (1.0 + rs))


def compute_dfmo(
    bars: list[dict],
    stoch_period: int = 25,
    k_smooth: int = 4,
    d_smooth: int = 4,
    rsi_period: int = 3,
) -> Optional[dict]:
    """Compute DFMO values for the LAST bar in `bars` and the one before it.

    Caller must pass CLOSED bars only (use bars[:-1] if the tail is a live bar).

    Returns:
      { k_curr, k_prev, d_curr, rsi_curr, rsi_prev }  or None if insufficient bars.
    """
    min_bars = stoch_period + k_smooth + d_smooth + 2
    if len(bars) < min_bars:
        return None

    closes = [float(b.get("close", 0) or 0) for b in bars]
    last = len(bars) - 1
    prev = last - 1

    k_curr = _stoch_k_smoothed(bars, last, stoch_period, k_smooth)
    k_prev = _stoch_k_smoothed(bars, prev, stoch_period, k_smooth)
    if k_curr is None or k_prev is None:
        return None

    # D = SMA(d_smooth) of K
    d_vals: list[float] = []
    for i in range(last - d_smooth + 1, last + 1):
        v = _stoch_k_smoothed(bars, i, stoch_period, k_smooth)
        if v is None:
            return None
        d_vals.append(v)
    d_curr = sum(d_vals) / len(d_vals)

    rsi_curr = _rsi(closes, last, rsi_period)
    rsi_prev = _rsi(closes, prev, rsi_period)
    if rsi_curr is None or rsi_prev is None:
        return None

    return {
        "k_curr": round(k_curr, 2),
        "k_prev": round(k_prev, 2),
        "d_curr": round(d_curr, 2),
        "rsi_curr": round(rsi_curr, 2),
        "rsi_prev": round(rsi_prev, 2),
    }


def dfmo_zone_end(
    bars: list[dict],
    direction: str,
    ob: float = 80.0,
    os_: float = 20.0,
    stoch_period: int = 25,
    k_smooth: int = 4,
    d_smooth: int = 4,
    rsi_period: int = 3,
) -> Optional[dict]:
    """Detect a DFMO zone-END signal on the LAST bar of `bars`.

    For a SELL averaging we want an OB zone-END: previous closed bar had BOTH
    Stoch K AND RSI above `ob`, current closed bar does NOT have both above
    `ob` anymore. This is the MT5 indicator's definition exactly.

    For a BUY averaging, symmetric: previous both below `os_`, current not
    both below `os_`.

    Caller must pass closed bars (drop live bar before calling). Returns the
    signal metadata or None.
    """
    if direction not in ("BUY", "SELL"):
        return None
    d = compute_dfmo(bars, stoch_period, k_smooth, d_smooth, rsi_period)
    if d is None:
        return None

    k_p = d["k_prev"]
    k_c = d["k_curr"]
    r_p = d["rsi_prev"]
    r_c = d["rsi_curr"]

    if direction == "SELL":
        prev_in_OB = (k_p > ob) and (r_p > ob)
        curr_in_OB = (k_c > ob) and (r_c > ob)
        if prev_in_OB and not curr_in_OB:
            return {
                "direction": "SELL",
                "zone": "OB",
                "k_prev": k_p, "k_curr": k_c,
                "rsi_prev": r_p, "rsi_curr": r_c,
            }
    else:  # BUY
        prev_in_OS = (k_p < os_) and (r_p < os_)
        curr_in_OS = (k_c < os_) and (r_c < os_)
        if prev_in_OS and not curr_in_OS:
            return {
                "direction": "BUY",
                "zone": "OS",
                "k_prev": k_p, "k_curr": k_c,
                "rsi_prev": r_p, "rsi_curr": r_c,
            }
    return None
