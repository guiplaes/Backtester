"""Market context assembler for Indicator + Executor prompts.

Builds a structured snapshot of everything beyond M5/M15/H1 XAUUSD that a human
trader would have on-screen: DXY, 10Y yield, D1 levels, session + VWAP, ATR
percentile, last BOS, liquidity pools.

All fetch functions are defensive — they return None / empty dicts on failure.
`build_market_context()` orchestrates everything and gives Indicator vs Executor
tailored views.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from typing import Optional


# ── Simple in-process caches ──────────────────────────────────────────
_DXY_CACHE = {"ts": 0.0, "data": None}
_YIELD_CACHE = {"ts": 0.0, "data": None}
_D1_CACHE = {"ts": 0.0, "data": None}


def _trend_from_closes(closes):
    """Tiny trend classifier — compares last close to SMA(20) + slope of last 5."""
    if not closes or len(closes) < 10:
        return "FLAT"
    last = closes[-1]
    sma = sum(closes[-20:]) / min(20, len(closes))
    recent_slope = closes[-1] - closes[-5]
    if last > sma and recent_slope > 0:
        return "UP"
    if last < sma and recent_slope < 0:
        return "DOWN"
    return "FLAT"


def _detect_recent_break(bars, lookback=30):
    """Did the last close break a local support/resistance?

    Simplified: compares last close vs rolling max/min of the prior `lookback` bars.
    Returns 'support' | 'resistance' | 'none'.
    """
    if not bars or len(bars) < lookback + 2:
        return "none"
    prev = bars[-(lookback + 1):-1]
    hi = max(b.get("high", 0) for b in prev)
    lo = min(b.get("low", float("inf")) for b in prev)
    last_close = bars[-1].get("close", 0)
    if last_close > hi:
        return "resistance"
    if last_close < lo:
        return "support"
    return "none"


def _aggregate(bars, factor):
    """Aggregate M5 bars into higher TF (copy of trader_brain.aggregate_bars)."""
    if not bars or factor <= 1:
        return list(bars or [])
    out = []
    for i in range(0, len(bars), factor):
        chunk = bars[i:i + factor]
        if not chunk:
            continue
        out.append({
            "time": chunk[0].get("time"),
            "open": chunk[0].get("open", 0),
            "high": max(b.get("high", 0) for b in chunk),
            "low": min(b.get("low", float("inf")) for b in chunk),
            "close": chunk[-1].get("close", 0),
            "volume": sum(b.get("volume", 0) for b in chunk),
        })
    return out


# ── DXY / 10Y yield ────────────────────────────────────────────────────
# Símbol primari del brain — usat al safety restore post-fetch.
_PRIMARY_SYMBOL = "OANDA:XAUUSD"


def _ensure_chart_restored(tv_helper):
    """Defensive restore: si el chart ha quedat stuck a un símbol auxiliar
    després d'un ohlcv-sym, força el restore a XAUUSD.

    Cap cost si ja està a XAUUSD (chart-set-symbol és idempotent).
    """
    try:
        tv_helper("symbol", _PRIMARY_SYMBOL, timeout=15)
        tv_helper("timeframe", "5", timeout=10)
    except Exception:
        pass  # best-effort — la primary feed del FastEngine farà el restore


def fetch_dxy_snapshot(tv_helper, symbol="TVC:DXY", cache_seconds=600):
    """Fetch DXY M5 via tv ohlcv-sym. Cached in-process.

    Returns {price, trend_m5, trend_h1, last_break} or None on failure.
    """
    now = time.time()
    if (now - _DXY_CACHE["ts"]) < cache_seconds and _DXY_CACHE["data"] is not None:
        return _DXY_CACHE["data"]
    try:
        resp = tv_helper("ohlcv-sym", symbol, 150, timeout=12)
    except Exception:
        resp = None
    # Safety net: restore chart explicitly (ohlcv-sym should auto-restore
    # but ocasionalment falla → chart stuck a TVC:DXY)
    _ensure_chart_restored(tv_helper)
    if not resp or not resp.get("success") or not resp.get("bars"):
        _DXY_CACHE["ts"] = now
        _DXY_CACHE["data"] = None
        return None
    bars = resp["bars"]
    closes_m5 = [b.get("close", 0) for b in bars]
    bars_h1 = _aggregate(bars, 12)
    closes_h1 = [b.get("close", 0) for b in bars_h1]
    data = {
        "price": round(bars[-1].get("close", 0), 3),
        "trend_m5": _trend_from_closes(closes_m5),
        "trend_h1": _trend_from_closes(closes_h1),
        "last_break": _detect_recent_break(bars, lookback=30),
    }
    _DXY_CACHE["ts"] = now
    _DXY_CACHE["data"] = data
    return data


def fetch_yield_snapshot(tv_helper, symbol="TVC:US10Y", cache_seconds=900):
    """Fetch US 10Y yield. Same pattern as DXY but we only need trend_m15."""
    now = time.time()
    if (now - _YIELD_CACHE["ts"]) < cache_seconds and _YIELD_CACHE["data"] is not None:
        return _YIELD_CACHE["data"]
    try:
        resp = tv_helper("ohlcv-sym", symbol, 60, timeout=12)
    except Exception:
        resp = None
    # Safety net: explicit restore
    _ensure_chart_restored(tv_helper)
    if not resp or not resp.get("success") or not resp.get("bars"):
        _YIELD_CACHE["ts"] = now
        _YIELD_CACHE["data"] = None
        return None
    bars = resp["bars"]
    bars_m15 = _aggregate(bars, 3)
    closes = [b.get("close", 0) for b in bars_m15]
    data = {
        "price": round(bars[-1].get("close", 0), 3),
        "trend_m15": _trend_from_closes(closes),
    }
    _YIELD_CACHE["ts"] = now
    _YIELD_CACHE["data"] = data
    return data


# ── D1 context ────────────────────────────────────────────────────────
def fetch_d1_context(tv_helper, cache_seconds=3600):
    """D1 H/L/C of yesterday + weekly open + nearest round level.

    For XAUUSD round numbers = nearest multiple of $10 (4800, 4810, 4820, …).
    """
    now = time.time()
    if (now - _D1_CACHE["ts"]) < cache_seconds and _D1_CACHE["data"] is not None:
        return _D1_CACHE["data"]
    try:
        # Re-use current chart's symbol (XAU) by changing timeframe temporarily
        # would disrupt flow. Safer: compute from in-memory M5 bars passed by caller.
        # So this function is a placeholder: caller passes M5 bars, we aggregate.
        # Left here for future if we want to fetch directly via tv-sym with period=D.
        _D1_CACHE["ts"] = now
        _D1_CACHE["data"] = None
        return None
    except Exception:
        return None


def compute_d1_context_from_m5(bars_m5, current_price):
    """Compute yesterday D1 H/L/C + weekly open + nearest round from M5 cache.

    Uses bar times (unix seconds UTC). Falls back gracefully if there are not
    enough bars to cover yesterday.
    """
    if not bars_m5 or len(bars_m5) < 50:
        return None

    now_utc = datetime.now(timezone.utc)
    today_start = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc)
    yesterday_start = today_start - timedelta(days=1)

    # Bars with time in [yesterday_start, today_start)
    yest_bars = [b for b in bars_m5
                 if yesterday_start.timestamp() <= b.get("time", 0) < today_start.timestamp()]

    d1_h = d1_l = d1_c = None
    if yest_bars:
        d1_h = max(b.get("high", 0) for b in yest_bars)
        d1_l = min(b.get("low", float("inf")) for b in yest_bars)
        d1_c = yest_bars[-1].get("close", 0)

    # Weekly open: Monday 00:00 UTC of current ISO week
    week_start = today_start - timedelta(days=today_start.weekday())
    week_bars = [b for b in bars_m5 if b.get("time", 0) >= week_start.timestamp()]
    weekly_open = week_bars[0].get("open") if week_bars else None

    # Nearest round level ($10 for XAU)
    nearest_round = round(current_price / 10.0) * 10 if current_price else None

    return {
        "d1_high": round(d1_h, 2) if d1_h is not None else None,
        "d1_low": round(d1_l, 2) if d1_l is not None else None,
        "d1_close": round(d1_c, 2) if d1_c is not None else None,
        "weekly_open": round(weekly_open, 2) if weekly_open is not None else None,
        "nearest_round": nearest_round,
    }


# ── Session + VWAP ────────────────────────────────────────────────────
def _session_name(now_utc):
    """Map UTC hour to session name. Rough but operator-intuitive."""
    h = now_utc.hour
    if 0 <= h < 7:
        return "ASIA"
    if 7 <= h < 13:
        return "LONDON"
    if 13 <= h < 16:
        return "OVERLAP"  # London/NY
    if 16 <= h < 21:
        return "NY"
    return "DEAD"


def _session_open_utc(now_utc, name):
    """Start of the current session in UTC."""
    d = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc)
    if name == "ASIA":
        return d
    if name == "LONDON":
        return d + timedelta(hours=7)
    if name == "OVERLAP":
        return d + timedelta(hours=13)
    if name == "NY":
        return d + timedelta(hours=16)
    return d + timedelta(hours=21)


def compute_session_context(now_utc, bars_m5):
    """Return session name + minutes since its open + VWAP from session open."""
    name = _session_name(now_utc)
    session_start = _session_open_utc(now_utc, name)
    minutes_since = int((now_utc - session_start).total_seconds() / 60)

    # VWAP = sum(price × vol) / sum(vol) over bars since session_start
    session_bars = [b for b in (bars_m5 or [])
                    if b.get("time", 0) >= session_start.timestamp()]
    vwap = None
    if session_bars:
        total_vol = sum(b.get("volume", 0) for b in session_bars)
        if total_vol > 0:
            typical = sum(((b.get("high", 0) + b.get("low", 0) + b.get("close", 0)) / 3.0)
                          * b.get("volume", 0) for b in session_bars)
            vwap = round(typical / total_vol, 2)

    current_price = bars_m5[-1].get("close") if bars_m5 else None
    distance = None
    if vwap is not None and current_price is not None:
        distance = round(current_price - vwap, 2)

    # 2026-05-06: visibilitat REAL del estat de sessions per al LLM
    # (evita al·lucinacions tipus "sessions OFF" quan són ON al config).
    sessions_on = []
    current_enabled = True
    try:
        import news_state as _ns
        enabled_map = _ns._load_sessions_enabled()
        sessions_on = [s for s in ('ASIA','LONDON','OVERLAP','NY','DEAD') if enabled_map.get(s)]
        current_enabled = bool(enabled_map.get(name, True))
    except Exception:
        pass

    return {
        "name": name,
        "minutes_since_open": minutes_since,
        "vwap": vwap,
        "distance_from_vwap_usd": distance,
        "current_enabled_for_new_entries": current_enabled,
        "sessions_enabled_for_new_entries": sessions_on,
    }


# ── ATR percentile ─────────────────────────────────────────────────────
def compute_atr_percentile(bars_m5, atr_current, lookback_days=20):
    """Rank current ATR within last N days of rolling ATR(14) values.

    Returns {atr_current, percentile_20d, is_anomalous (p > 80 or p < 20)}.
    """
    if atr_current is None or not bars_m5 or len(bars_m5) < 50:
        return {"atr_current": atr_current, "percentile_20d": None, "is_anomalous": False}
    # Rolling ATR(14): rough TR = high - low (simplification — fast enough)
    trs = []
    for i in range(1, len(bars_m5)):
        hi = bars_m5[i].get("high", 0)
        lo = bars_m5[i].get("low", 0)
        pc = bars_m5[i - 1].get("close", 0)
        tr = max(hi - lo, abs(hi - pc), abs(lo - pc))
        trs.append(tr)
    # Sample ATR values at each 14-bar window end
    atr_samples = []
    for i in range(14, len(trs), 3):  # step 3 = sample every ~15 min
        atr_samples.append(sum(trs[i - 14:i]) / 14.0)
    if not atr_samples:
        return {"atr_current": atr_current, "percentile_20d": None, "is_anomalous": False}
    # Percentile
    below = sum(1 for a in atr_samples if a <= atr_current)
    pct = round(100.0 * below / len(atr_samples), 1)
    return {
        "atr_current": round(atr_current, 2),
        "percentile_20d": pct,
        "is_anomalous": pct > 80 or pct < 20,
    }


# ── Market structure: last BOS ─────────────────────────────────────────
def detect_last_bos(bars, pivot_window=5):
    """Detect last Break Of Structure on the given bars (M5 or M15).

    Uses pivot highs/lows: a pivot-high is a bar whose high exceeds all bars
    within ±pivot_window. BOS = close beyond the previous pivot of opposite type.

    Returns {type: 'bullish|bearish', price, age_bars} or None.
    """
    if not bars or len(bars) < pivot_window * 2 + 3:
        return None

    def _pivots(seq, attr):
        ps = []
        for i in range(pivot_window, len(seq) - pivot_window):
            v = seq[i].get(attr, 0)
            left = all(v >= seq[j].get(attr, 0) for j in range(i - pivot_window, i)) if attr == "high" \
                else all(v <= seq[j].get(attr, 0) for j in range(i - pivot_window, i))
            right = all(v >= seq[j].get(attr, 0) for j in range(i + 1, i + pivot_window + 1)) if attr == "high" \
                else all(v <= seq[j].get(attr, 0) for j in range(i + 1, i + pivot_window + 1))
            if left and right:
                ps.append((i, v))
        return ps

    pivot_highs = _pivots(bars, "high")
    pivot_lows = _pivots(bars, "low")

    if not pivot_highs and not pivot_lows:
        return None

    # Find latest BOS: walk bars from end back and check if close > last pivot_high
    # (bullish BOS) or < last pivot_low (bearish BOS)
    last_bos = None
    for i in range(len(bars) - 1, max(0, len(bars) - 50), -1):
        c = bars[i].get("close", 0)
        past_highs = [(idx, v) for idx, v in pivot_highs if idx < i]
        past_lows = [(idx, v) for idx, v in pivot_lows if idx < i]
        if past_highs and c > past_highs[-1][1]:
            last_bos = {"type": "bullish", "price": round(past_highs[-1][1], 2),
                        "age_bars": len(bars) - 1 - i}
            break
        if past_lows and c < past_lows[-1][1]:
            last_bos = {"type": "bearish", "price": round(past_lows[-1][1], 2),
                        "age_bars": len(bars) - 1 - i}
            break
    return last_bos


# ── Liquidity pools (equal highs/lows) ─────────────────────────────────
def find_liquidity_pools(bars_m5, current_price, tolerance_usd=1.0, lookback=80, max_per_side=3):
    """Detect clusters of equal highs (above current) and equal lows (below).

    A "pool" = ≥2 bars within `tolerance_usd` at a local extreme. Useful as
    liquidity magnets (retail stops clustered there).

    Returns {pools_below: [prices], pools_above: [prices]}.
    """
    if not bars_m5 or len(bars_m5) < 10 or current_price is None:
        return {"pools_below": [], "pools_above": []}
    recent = bars_m5[-lookback:]
    highs = [b.get("high", 0) for b in recent]
    lows = [b.get("low", float("inf")) for b in recent]

    def _cluster(values):
        clusters = []
        used = [False] * len(values)
        for i in range(len(values)):
            if used[i]:
                continue
            group = [values[i]]
            used[i] = True
            for j in range(i + 1, len(values)):
                if used[j]:
                    continue
                if abs(values[j] - values[i]) <= tolerance_usd:
                    group.append(values[j])
                    used[j] = True
            if len(group) >= 2:
                clusters.append(sum(group) / len(group))
        return clusters

    high_clusters = _cluster(highs)
    low_clusters = _cluster(lows)

    # Round to nearest $1 to stabilize pool identity across ticks (otherwise the
    # tick-by-tick recomputation shifts the price and the per-pool cooldown in
    # event_detector fails — see sim finding 2026-04-19).
    pools_above = sorted(set(round(c) for c in high_clusters if c > current_price))[:max_per_side]
    pools_below = sorted(set(round(c) for c in low_clusters if c < current_price),
                         reverse=True)[:max_per_side]
    return {"pools_below": pools_below, "pools_above": pools_above}


# ── Orchestrator ──────────────────────────────────────────────────────
def build_market_context(bars_m5, account, tv_helper, now_utc, *,
                         for_executor: bool = True,
                         atr_m5: Optional[float] = None,
                         config: Optional[dict] = None):
    """Assemble the full context dict for injection into Indicator/Executor prompts.

    Args:
        bars_m5: list of M5 bar dicts (300 typical)
        account: unused today; reserved for future context (balance/risk snapshots)
        tv_helper: the trader_brain.tv function (or equivalent signature)
        now_utc: datetime in UTC
        for_executor: True → versió tàctica (session + structure M5 més pes).
                      False → versió Indicator (HTF + regime més pes).
        atr_m5: optional current ATR(14) in USD. If provided, used for percentile.
        config: optional config dict with sub-sections 'external_data' and
                'market_context'. Falls back to sensible defaults.

    Returns a dict with keys {external, market_state, htf}. Always a dict;
    individual sub-keys may be None if fetches fail.
    """
    cfg = config or {}
    ext_cfg = (cfg.get("external_data") or {})
    mc_cfg = (cfg.get("market_context") or {})

    current_price = bars_m5[-1].get("close") if bars_m5 else None

    # External feeds — only if tv_helper available AND ext_cfg.enabled is true.
    # Disabled by default since 2026-04-27: each fetch swaps the chart to DXY/
    # US10Y for ~4-6s. Under load tv.js times out mid-swap and leaves the
    # chart stuck on the auxiliary symbol → brain stops getting XAU bars.
    # Set external_data.enabled: true in config.yaml to re-enable.
    dxy = None
    yield_10y = None
    if tv_helper is not None and ext_cfg.get("enabled", False):
        try:
            # Cache TTLs estesos 2026-05-04 per minimitzar chart flicker.
            # DXY: 10min (canvia lent), US10Y: 15min (canvia molt lent).
            # config.yaml fetch_interval_seconds només s'usa com mínim ara.
            _dxy_ttl = max(600, int(ext_cfg.get("fetch_interval_seconds", 600)))
            _yld_ttl = max(900, int(ext_cfg.get("fetch_interval_seconds", 900)))
            dxy = fetch_dxy_snapshot(
                tv_helper,
                symbol=ext_cfg.get("dxy_symbol", "TVC:DXY"),
                cache_seconds=_dxy_ttl,
            )
        except Exception:
            dxy = None
        try:
            yield_10y = fetch_yield_snapshot(
                tv_helper,
                symbol=ext_cfg.get("yield_10y_symbol", "TVC:US10Y"),
                cache_seconds=_yld_ttl,
            )
        except Exception:
            yield_10y = None

    # HTF derived from bars_m5 (D1 + weekly open + round)
    htf = compute_d1_context_from_m5(bars_m5, current_price)

    # Session + VWAP
    session = compute_session_context(now_utc, bars_m5)

    # ATR percentile
    vol = compute_atr_percentile(
        bars_m5, atr_m5,
        lookback_days=int(mc_cfg.get("atr_percentile_lookback_days", 20)),
    )

    # Structure — use M15 for Indicator (bigger picture), M5 for Executor (tactical)
    if for_executor:
        struct_bars = bars_m5
    else:
        struct_bars = _aggregate(bars_m5, 3)
    last_bos = detect_last_bos(
        struct_bars,
        pivot_window=int(mc_cfg.get("bos_pivot_window", 5)),
    )

    # Liquidity pools — only for Executor (tactical)
    liquidity = None
    if for_executor:
        liquidity = find_liquidity_pools(
            bars_m5, current_price,
            tolerance_usd=float(mc_cfg.get("liquidity_tolerance_usd", 1.0)),
            lookback=int(mc_cfg.get("liquidity_lookback_bars", 80)),
        )

    return {
        "external": {
            "dxy": dxy,
            "yield_10y": yield_10y,
        },
        "htf": htf,
        "market_state": {
            "session": session,
            "volatility": vol,
            "structure": {"last_bos": last_bos},
            "liquidity": liquidity,
        },
    }
