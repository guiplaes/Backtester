"""Rich context builder for the Indicator LLM prompt.

Gathers multi-timeframe structure, volume profile (Python + TradingView Pine
HVN), liquidity pools, extended cross-asset correlations, and advanced
technical state into a single compact text block.

All block builders are defensive — any failure yields an empty string so the
Indicator never gets blocked by a missing data source.

Used by `trader_brain.build_indicator_prompt()`.
"""
from __future__ import annotations

import time
import threading
from datetime import datetime, timezone, timedelta


# ═════════════════════════════════════════════════════════════════════════
# BLOC 1 — MULTI-TIMEFRAME STRUCTURE
# ═════════════════════════════════════════════════════════════════════════

def _aggregate(bars_m5, factor):
    """Aggregate M5 bars into higher TF. Local copy to avoid cross-import."""
    if not bars_m5 or factor <= 1:
        return list(bars_m5 or [])
    out = []
    for i in range(0, len(bars_m5), factor):
        chunk = bars_m5[i:i + factor]
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


def _find_pivots(bars, window=3, kind="high"):
    """Return list of (index, price) pivots. window=3 → pivot-3-3."""
    if not bars or len(bars) < window * 2 + 1:
        return []
    key = "high" if kind == "high" else "low"
    op = max if kind == "high" else min
    pivots = []
    for i in range(window, len(bars) - window):
        v = bars[i].get(key, 0)
        neighbourhood = [b.get(key, 0) for b in bars[i - window:i + window + 1]]
        if op(neighbourhood) == v:
            pivots.append((i, v))
    return pivots


def _detect_bos(bars, window=3):
    """Simplified BOS detection used across TFs — close beyond last opposite pivot."""
    if not bars or len(bars) < window * 2 + 3:
        return None
    highs = _find_pivots(bars, window, "high")
    lows = _find_pivots(bars, window, "low")
    for i in range(len(bars) - 1, max(0, len(bars) - 30), -1):
        c = bars[i].get("close", 0)
        prev_highs = [p for p in highs if p[0] < i]
        prev_lows = [p for p in lows if p[0] < i]
        if prev_highs and c > prev_highs[-1][1]:
            return {"type": "bullish", "price": round(prev_highs[-1][1], 2),
                    "age_bars": len(bars) - 1 - i}
        if prev_lows and c < prev_lows[-1][1]:
            return {"type": "bearish", "price": round(prev_lows[-1][1], 2),
                    "age_bars": len(bars) - 1 - i}
    return None


def _detect_range(bars, window=3, min_touches=3, recent_n=40):
    """Detect a range over the last `recent_n` bars if extremes tested ≥ min_touches."""
    if not bars or len(bars) < recent_n:
        return None
    recent = bars[-recent_n:]
    highs = [b.get("high", 0) for b in recent]
    lows = [b.get("low", 0) for b in recent]
    hi = max(highs)
    lo = min(lows)
    rng = hi - lo
    if rng <= 0:
        return None
    tol = max(rng * 0.05, 0.5)  # 5% tolerance (at least $0.5)
    hi_touches = sum(1 for h in highs if h >= hi - tol)
    lo_touches = sum(1 for l in lows if l <= lo + tol)
    if hi_touches >= min_touches and lo_touches >= min_touches:
        return {
            "high": round(hi, 2),
            "low": round(lo, 2),
            "width_usd": round(rng, 1),
            "high_touches": hi_touches,
            "low_touches": lo_touches,
            "bars_scanned": recent_n,
        }
    return None


def _detect_fvg(bars, max_gaps=5):
    """Fair Value Gap: three consecutive bars where bar[i-1].high < bar[i+1].low
    (bullish FVG) or bar[i-1].low > bar[i+1].high (bearish FVG). The FVG is the
    gap (middle bar body crosses it). Returns unfilled FVGs — those whose gap
    hasn't been revisited by later price action.
    """
    if not bars or len(bars) < 3:
        return []
    gaps = []
    for i in range(1, len(bars) - 1):
        prev_h = bars[i - 1].get("high", 0)
        prev_l = bars[i - 1].get("low", 0)
        next_h = bars[i + 1].get("high", 0)
        next_l = bars[i + 1].get("low", 0)
        # Bullish FVG: previous high < next low → gap between prev_h and next_l
        if prev_h < next_l:
            gap_lo, gap_hi = prev_h, next_l
            # Check if gap already filled by later bars
            filled = any((b.get("low", float("inf")) <= gap_hi and
                          b.get("high", 0) >= gap_lo)
                         for b in bars[i + 2:])
            if not filled:
                gaps.append({"type": "bullish", "low": round(gap_lo, 2),
                             "high": round(gap_hi, 2),
                             "age_bars": len(bars) - 1 - i})
        elif prev_l > next_h:
            gap_lo, gap_hi = next_h, prev_l
            filled = any((b.get("low", float("inf")) <= gap_hi and
                          b.get("high", 0) >= gap_lo)
                         for b in bars[i + 2:])
            if not filled:
                gaps.append({"type": "bearish", "low": round(gap_lo, 2),
                             "high": round(gap_hi, 2),
                             "age_bars": len(bars) - 1 - i})
    # Newest first, limit
    gaps.sort(key=lambda g: g["age_bars"])
    return gaps[:max_gaps]


def _fmt_bar_compact(b, tz=timezone.utc):
    try:
        t = datetime.fromtimestamp(b.get("time", 0), tz=tz).strftime("%d/%H:%M")
    except Exception:
        t = "??"
    return (f"{t} O={b.get('open',0):.1f} H={b.get('high',0):.1f} "
            f"L={b.get('low',0):.1f} C={b.get('close',0):.1f} "
            f"V={int(b.get('volume',0))}")


def build_mtf_structure_block(bars_m5, bars_m15=None, bars_h1=None, bars_h4=None, bars_d1=None):
    """Emit MTF structure block: bars + swings + BOS + ranges + FVG.

    Accepts dedicated bars per TF. If bars_h4/bars_d1 are passed (real H4/D1
    fetched directly from the chart), use them — otherwise aggregate from
    H1 (legacy behavior, ~10d coverage). Real H4/D1 give 30d+ context which
    is critical when price breaks to new multi-week lows (otherwise the
    Indicator has no support zones to propose below).

    If bars_m15/bars_h1 are not provided (legacy call), falls back to
    aggregating from bars_m5 — still produces something but covers less
    historical range.
    """
    try:
        if not bars_m5 or len(bars_m5) < 20:
            return ""
        # If dedicated higher-TF bars provided, use them. Otherwise aggregate
        # from M5 as before (legacy fallback, narrower coverage).
        if bars_m15 is None or len(bars_m15) < 10:
            bars_m15 = _aggregate(bars_m5, 3)   # M5×3 = M15
        if bars_h1 is None or len(bars_h1) < 10:
            bars_h1 = _aggregate(bars_m5, 12)   # M5×12 = H1
        # H4: real fetch if provided (180 bars = 30d), else aggregate from H1.
        if bars_h4 is None or len(bars_h4) < 10:
            bars_h4 = _aggregate(bars_h1, 4)
        # D1: real fetch if provided (30 bars = 30d), else aggregate from H1.
        if bars_d1 is None or len(bars_d1) < 5:
            bars_d1 = _aggregate(bars_h1, 24)

        lines = ["", "═══ ESTRUCTURA MULTI-TF ═══"]

        # Bar samples (compact, trailing)
        def fmt_bars(bars, n, label):
            slice_ = bars[-n:] if len(bars) >= n else bars
            if not slice_:
                return f"  {label}: (sense dades)"
            return f"  {label} (últimes {len(slice_)}):\n" + "\n".join(
                "    " + _fmt_bar_compact(b) for b in slice_)

        lines.append(fmt_bars(bars_m5, 20, "M5"))
        lines.append(fmt_bars(bars_m15, 15, "M15"))
        lines.append(fmt_bars(bars_h1, 12, "H1"))
        lines.append(fmt_bars(bars_h4, 8, "H4"))
        if bars_d1:
            lines.append(fmt_bars(bars_d1, 5, "D1"))

        # Swings — last 3 highs and lows per TF
        def fmt_swings(bars, label, w=3):
            highs = _find_pivots(bars, w, "high")[-3:]
            lows = _find_pivots(bars, w, "low")[-3:]
            parts = []
            for idx, v in highs[::-1]:
                age = len(bars) - 1 - idx
                parts.append(f"SW_H@{v:.1f}(fa {age}b)")
            for idx, v in lows[::-1]:
                age = len(bars) - 1 - idx
                parts.append(f"SW_L@{v:.1f}(fa {age}b)")
            return f"  Swings {label}: " + (", ".join(parts) if parts else "(cap)")

        lines.append("")
        lines.append(fmt_swings(bars_m15, "M15"))
        lines.append(fmt_swings(bars_h1, "H1"))
        lines.append(fmt_swings(bars_h4, "H4"))

        # BOS per TF
        bos_m15 = _detect_bos(bars_m15)
        bos_h1 = _detect_bos(bars_h1)
        bos_h4 = _detect_bos(bars_h4)
        lines.append("")
        lines.append("  BOS recents:")
        for label, bos in (("M15", bos_m15), ("H1", bos_h1), ("H4", bos_h4)):
            if bos:
                lines.append(f"    {label}: {bos['type']} @ {bos['price']} "
                             f"(fa {bos['age_bars']} bars)")
            else:
                lines.append(f"    {label}: cap detectat")

        # Ranges
        rng_m15 = _detect_range(bars_m15)
        rng_h1 = _detect_range(bars_h1)
        lines.append("")
        lines.append("  Rangs:")
        for label, r in (("M15", rng_m15), ("H1", rng_h1)):
            if r:
                lines.append(f"    {label}: {r['low']}↔{r['high']} "
                             f"(width ${r['width_usd']}, "
                             f"hi×{r['high_touches']}/lo×{r['low_touches']})")
            else:
                lines.append(f"    {label}: sense rang clar")

        # FVG
        fvg_m5 = _detect_fvg(bars_m5[-30:])
        fvg_m15 = _detect_fvg(bars_m15[-20:])
        lines.append("")
        lines.append("  FVG unfilled (imants de preu):")
        for label, gaps in (("M5", fvg_m5), ("M15", fvg_m15)):
            if gaps:
                txt = ", ".join(f"{g['type'][:4]} [{g['low']}-{g['high']}] "
                                f"fa {g['age_bars']}b" for g in gaps)
                lines.append(f"    {label}: {txt}")
            else:
                lines.append(f"    {label}: cap")

        return "\n".join(lines)
    except Exception as e:
        return f"\n═══ ESTRUCTURA MULTI-TF ═══\n  (error: {e})"


# ═════════════════════════════════════════════════════════════════════════
# BLOC 2 — VOLUME PROFILE PROFESSIONAL
# ═════════════════════════════════════════════════════════════════════════

def _poc_vah_val_of_bars(bars, bucket_usd=0.5):
    """Compute POC, VAH, VAL from a set of bars using bucketed volume profile.

    Returns {poc, vah, val, total_volume} or None if no volume data.
    """
    if not bars:
        return None
    total_vol = sum(b.get("volume", 0) for b in bars)
    if total_vol < 10:
        return None
    # Bucketize — split each bar's volume proportionally across its H-L range
    buckets = {}
    for b in bars:
        hi = b.get("high", 0)
        lo = b.get("low", 0)
        vol = b.get("volume", 0)
        if vol <= 0 or hi <= lo:
            continue
        n_buckets = max(1, int((hi - lo) / bucket_usd) + 1)
        per_bucket = vol / n_buckets
        for i in range(n_buckets):
            price = lo + i * bucket_usd
            key = round(price / bucket_usd) * bucket_usd
            buckets[key] = buckets.get(key, 0) + per_bucket
    if not buckets:
        return None
    # POC = bucket with max volume
    poc = max(buckets.items(), key=lambda kv: kv[1])
    poc_price = poc[0]
    # VAH/VAL = expand from POC outward until 70% of volume is covered
    sorted_by_price = sorted(buckets.items())
    poc_idx = next((i for i, (p, _) in enumerate(sorted_by_price)
                    if abs(p - poc_price) < 0.01), 0)
    covered = sorted_by_price[poc_idx][1]
    target = total_vol * 0.70
    lo_idx = hi_idx = poc_idx
    while covered < target and (lo_idx > 0 or hi_idx < len(sorted_by_price) - 1):
        below_vol = sorted_by_price[lo_idx - 1][1] if lo_idx > 0 else 0
        above_vol = sorted_by_price[hi_idx + 1][1] if hi_idx < len(sorted_by_price) - 1 else 0
        if above_vol >= below_vol and hi_idx < len(sorted_by_price) - 1:
            hi_idx += 1
            covered += above_vol
        elif lo_idx > 0:
            lo_idx -= 1
            covered += below_vol
        else:
            break
    return {
        "poc": round(poc_price, 1),
        "vah": round(sorted_by_price[hi_idx][0], 1),
        "val": round(sorted_by_price[lo_idx][0], 1),
        "total_volume": int(total_vol),
    }


def _poc_per_day(bars_m5, n_days=5):
    """Compute POC/VAH/VAL for each of the last n_days (UTC days)."""
    if not bars_m5:
        return []
    today = datetime.now(timezone.utc).date()
    results = []
    for d_offset in range(n_days):
        day = today - timedelta(days=d_offset)
        day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()
        day_end = day_start + 86400
        day_bars = [b for b in bars_m5
                    if day_start <= b.get("time", 0) < day_end]
        if not day_bars:
            continue
        vp = _poc_vah_val_of_bars(day_bars)
        if vp:
            vp["date"] = day.strftime("%Y-%m-%d")
            vp["label"] = "avui" if d_offset == 0 else f"-{d_offset}d"
            results.append(vp)
    return results


def _naked_pocs(daily_pocs, bars_m5):
    """A POC is naked if NO later bar's high/low has touched it."""
    if not daily_pocs or not bars_m5:
        return []
    naked = []
    today = datetime.now(timezone.utc).date()
    today_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc).timestamp()
    for p in daily_pocs:
        if p.get("label") == "avui":
            continue  # current day POC can't be naked yet
        poc_price = p["poc"]
        day_end = (datetime.strptime(p["date"], "%Y-%m-%d")
                   .replace(tzinfo=timezone.utc) + timedelta(days=1)).timestamp()
        # Check all bars AFTER that day's end
        touched = any(
            b.get("low", float("inf")) <= poc_price <= b.get("high", 0)
            for b in bars_m5
            if b.get("time", 0) >= day_end
        )
        if not touched:
            naked.append(p)
    return naked


def _poc_per_session(bars_m5):
    """POCs for Asia/London/NY of the CURRENT day."""
    if not bars_m5:
        return {}
    now = datetime.now(timezone.utc)
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp()
    sessions = {
        "ASIA": (day_start, day_start + 7 * 3600),
        "LONDON": (day_start + 7 * 3600, day_start + 13 * 3600),
        "NY": (day_start + 13 * 3600, day_start + 21 * 3600),
    }
    out = {}
    for name, (s, e) in sessions.items():
        sess_bars = [b for b in bars_m5 if s <= b.get("time", 0) < e]
        if not sess_bars:
            continue
        vp = _poc_vah_val_of_bars(sess_bars)
        if vp:
            out[name] = vp
    return out


_TV_PINE_CACHE = {"ts": 0.0, "levels": [], "boxes": []}
_TV_PINE_LOCK = threading.Lock()


def _fetch_tv_session_volume_profile(tv_helper, cache_seconds=120):
    """Fetch HVN levels and zone boxes from TradingView's Session Volume Profile
    indicator (must be present on any XAUUSD chart tab).

    Returns {levels: [price, ...], boxes: [{high, low}, ...]}. Cached in-process.
    Safe: returns {levels: [], boxes: []} if TV is not accessible.
    """
    now = time.time()
    with _TV_PINE_LOCK:
        if (now - _TV_PINE_CACHE["ts"]) < cache_seconds and _TV_PINE_CACHE["levels"]:
            return {"levels": _TV_PINE_CACHE["levels"],
                    "boxes": _TV_PINE_CACHE["boxes"]}
    levels = []
    boxes = []
    try:
        r_lines = tv_helper("pine-lines", "Session Volume", timeout=10)
        if r_lines and r_lines.get("studies"):
            for study in r_lines["studies"]:
                levels.extend(study.get("horizontal_levels") or [])
    except Exception:
        pass
    try:
        r_boxes = tv_helper("pine-boxes", "Session Volume", timeout=10)
        if r_boxes and r_boxes.get("studies"):
            for study in r_boxes["studies"]:
                for z in (study.get("zones") or []):
                    if z.get("high") is not None and z.get("low") is not None:
                        boxes.append({"high": z["high"], "low": z["low"]})
    except Exception:
        pass
    # Only update cache if we got something (keep last good snapshot otherwise)
    if levels or boxes:
        with _TV_PINE_LOCK:
            _TV_PINE_CACHE["ts"] = now
            _TV_PINE_CACHE["levels"] = levels
            _TV_PINE_CACHE["boxes"] = boxes
    return {"levels": levels, "boxes": boxes}


def build_volume_profile_block(bars_m5, tv_helper):
    """Emit volume profile block: daily POC/VAH/VAL + naked POCs + session POCs
    + TV Pine HVN levels (if reachable)."""
    try:
        lines = ["", "═══ VOLUME PROFILE (institucional) ═══"]
        current_price = bars_m5[-1].get("close", 0) if bars_m5 else 0

        # Daily POCs (last 5 days)
        daily = _poc_per_day(bars_m5, n_days=5)
        if daily:
            lines.append("  POC/VAH/VAL diaris (últims 5 dies):")
            for p in daily:
                lines.append(f"    {p['label']:>5s} {p['date']}  "
                             f"POC={p['poc']:.1f}  VAH={p['vah']:.1f}  VAL={p['val']:.1f}")

        # Naked POCs
        naked = _naked_pocs(daily, bars_m5)
        if naked:
            lines.append("")
            lines.append("  ⭐ Naked POCs (NO tocats, imants magnètics):")
            for p in naked:
                dist = p["poc"] - current_price
                lines.append(f"    POC {p['poc']:.1f}  ({p['label']}, {p['date']})  "
                             f"dist {dist:+.1f}$")
        else:
            lines.append("  Naked POCs: cap")

        # Per-session POC of current day
        sess = _poc_per_session(bars_m5)
        if sess:
            lines.append("")
            lines.append("  POCs per sessió (avui):")
            for name in ("ASIA", "LONDON", "NY"):
                if name in sess:
                    p = sess[name]
                    lines.append(f"    {name:>7s}  POC={p['poc']:.1f}  "
                                 f"VAH={p['vah']:.1f}  VAL={p['val']:.1f}")

        # TradingView Pine HVN levels
        if tv_helper is not None:
            tvp = _fetch_tv_session_volume_profile(tv_helper)
            pine_levels = tvp.get("levels") or []
            if pine_levels:
                # Keep those within ±80$ of current price, deduplicated to $1
                nearby = sorted({round(l) for l in pine_levels
                                 if abs(l - current_price) <= 80})
                # Separate above / below price
                above = sorted([l for l in nearby if l > current_price])[:10]
                below = sorted([l for l in nearby if l < current_price],
                               reverse=True)[:10]
                lines.append("")
                lines.append(f"  HVN del Pine (TV Session Volume Profile, "
                             f"{len(pine_levels)} totals):")
                if above:
                    lines.append("    ↑ sobre preu: " +
                                 ", ".join(f"{l}" for l in above))
                if below:
                    lines.append("    ↓ sota preu:  " +
                                 ", ".join(f"{l}" for l in below))
            else:
                lines.append("")
                lines.append("  HVN del Pine: (no accessible — el tab amb "
                             "'Session Volume Profile' ha d'estar actiu a TV)")

        return "\n".join(lines)
    except Exception as e:
        return f"\n═══ VOLUME PROFILE ═══\n  (error: {e})"


def build_futures_volume_profile_block(gc_m5, gc_h4=None, gc_d1=None):
    """Volume Profile institucional construït amb bars de COMEX:GC1!.

    Mateix algoritme que `build_volume_profile_block` (POC diaris, naked,
    per-sessió) però sobre VOLUM REAL en contractes — no tick-volume.
    Conseqüència: els POCs/VAH/VAL reflecteixen on han canviat de mans
    contractes reals (acumulació institucional veritable), no on s'han
    succedit ticks retail.

    Args:
        gc_m5: bars M5 GC1! (288=24h ideal) — POCs diaris i per sessió
        gc_h4: optional bars H4 GC1! (180=30d ideal) — POCs setmanals
                (mid-term institucional)
        gc_d1: optional bars D1 GC1! (30=30d ideal) — POCs mensuals
                (Naked POCs aquí són imants forts multi-setmanals)

    Si `gc_m5` ve buit (mercat CME tancat o fetch fallit), retorna "".
    """
    if not gc_m5 or len(gc_m5) < 24:
        return ""
    try:
        lines = ["", "═══ VOLUME PROFILE FUTURES (COMEX:GC1! — contractes reals, institucional) ═══"]
        current_gc_price = gc_m5[-1].get("close", 0) if gc_m5 else 0

        # POCs diaris últims 5 dies (futures, des de M5)
        daily = _poc_per_day(gc_m5, n_days=5)
        if daily:
            lines.append("  POC/VAH/VAL diaris GC1! (últims 5 dies, volum institucional):")
            for p in daily:
                lines.append(
                    f"    {p['label']:>5s} {p['date']}  "
                    f"POC={p['poc']:.1f}  VAH={p['vah']:.1f}  VAL={p['val']:.1f}  "
                    f"({p['total_volume']:,} contractes)"
                )

        # Naked POCs (futures) — imants institucionals
        naked = _naked_pocs(daily, gc_m5)
        if naked:
            lines.append("")
            lines.append("  ⭐ Naked POCs GC1! (NO tocats — imants institucionals forts):")
            for p in naked:
                dist_gc = p["poc"] - current_gc_price
                lines.append(
                    f"    POC GC1! {p['poc']:.1f}  ({p['label']}, {p['date']})  "
                    f"dist {dist_gc:+.1f}$ (vs preu futures {current_gc_price:.1f})"
                )
        else:
            lines.append("  Naked POCs GC1!: cap")

        # POCs per sessió (avui, futures)
        sess = _poc_per_session(gc_m5)
        if sess:
            lines.append("")
            lines.append("  POCs per sessió GC1! (avui — quan han actuat institucionals):")
            for name in ("ASIA", "LONDON", "NY"):
                if name in sess:
                    p = sess[name]
                    lines.append(
                        f"    {name:>7s}  POC={p['poc']:.1f}  "
                        f"VAH={p['vah']:.1f}  VAL={p['val']:.1f}  "
                        f"({p['total_volume']:,} contractes)"
                    )

        # NEW 2026-05-04: POCs setmanals/multi-setmanals via H4 GC1!
        # Cada "dia" de _poc_per_day amb gc_h4 representa 1 dia de bars
        # H4 (6 bars/dia) → estructura institucional de mitjà termini.
        # Naked POCs en H4 són MOLT més magnètics que els diaris perquè
        # representen on els institucionals van comprometre capital
        # durant setmanes senceres sense que el preu hi tornés.
        if gc_h4 and len(gc_h4) >= 30:
            try:
                # Process aggregating bars per week (~30 bars H4 = ~5 dies)
                # _poc_per_day amb dies=30 i bars H4 dóna POCs per dia (cada
                # dia té 6 bars H4). Capturem les últimes 4 setmanes.
                weekly = _poc_per_day(gc_h4, n_days=20)
                if weekly:
                    lines.append("")
                    lines.append("  POC diaris H4 GC1! (últims 20 dies, agrupació setmanal):")
                    # Mostrem només els 10 més recents per limitar mida
                    for p in weekly[-10:]:
                        lines.append(
                            f"    {p['date']}  POC={p['poc']:.1f}  "
                            f"VAH={p['vah']:.1f}  VAL={p['val']:.1f}  "
                            f"({p['total_volume']:,} contractes)"
                        )
                    # Naked POCs H4 — imants institucionals MOLT forts
                    naked_h4 = _naked_pocs(weekly, gc_h4)
                    if naked_h4:
                        lines.append("")
                        lines.append("  ⭐⭐ Naked POCs H4 GC1! (multi-setmanals — imants extremament forts):")
                        for p in naked_h4[:8]:
                            dist_gc = p["poc"] - current_gc_price
                            lines.append(
                                f"    POC H4 GC1! {p['poc']:.1f}  ({p['date']})  "
                                f"dist {dist_gc:+.1f}$"
                            )
            except Exception as e:
                lines.append(f"  (H4 POCs error: {e})")

        # NEW 2026-05-04: POCs mensuals via D1 GC1!
        # 30 bars D1 = 30 dies. Agreguem en grups setmanals per claretat.
        if gc_d1 and len(gc_d1) >= 5:
            try:
                # Compute global POC over all D1 bars (1 month aggregate)
                # using existing _compute_volume_profile algorithm
                from collections import defaultdict
                buckets = defaultdict(float)
                for b in gc_d1:
                    h, l = b.get("high", 0), b.get("low", 0)
                    v = b.get("volume", 0)
                    if h <= l or v <= 0:
                        continue
                    # Distribuïm volum entre bucket de $1
                    span = h - l
                    if span <= 0:
                        buckets[round(h)] += v
                        continue
                    bucket_count = max(1, int(span))
                    vol_per_bucket = v / bucket_count
                    price = l
                    while price < h:
                        buckets[round(price)] += vol_per_bucket
                        price += 1
                if buckets:
                    poc_30d = max(buckets.items(), key=lambda kv: kv[1])
                    total_vol = sum(buckets.values())
                    # VAH/VAL per a value area 70%
                    sorted_buckets = sorted(buckets.items(), key=lambda kv: kv[1], reverse=True)
                    cum = 0
                    va_prices = []
                    for price, vol in sorted_buckets:
                        cum += vol
                        va_prices.append(price)
                        if cum >= 0.70 * total_vol:
                            break
                    vah_30d = max(va_prices) if va_prices else poc_30d[0]
                    val_30d = min(va_prices) if va_prices else poc_30d[0]
                    lines.append("")
                    lines.append("  ⭐⭐⭐ POC AGREGAT 30 DIES D1 GC1! (commit institucional mensual):")
                    lines.append(
                        f"    POC={poc_30d[0]}  VAH={vah_30d}  VAL={val_30d}  "
                        f"(total {int(total_vol):,} contractes en 30 dies)"
                    )
                    dist = poc_30d[0] - current_gc_price
                    lines.append(
                        f"    dist al preu actual: {dist:+.1f}$ — "
                        f"{'ABOVE' if dist > 0 else 'BELOW'} preu, "
                        f"{'magnet alcista' if dist > 0 else 'magnet baixista'} si trencament"
                    )
            except Exception as e:
                lines.append(f"  (D1 POC 30d error: {e})")

        return "\n".join(lines)
    except Exception as e:
        return f"\n═══ VOLUME PROFILE FUTURES ═══\n  (error: {e})"


# ═════════════════════════════════════════════════════════════════════════
# BLOC 3 — LIQUIDITY
# ═════════════════════════════════════════════════════════════════════════

def _equal_extremes(bars, lookback=50, tolerance=1.0, min_count=3):
    """Cluster bars with equal highs or equal lows within tolerance."""
    if not bars or len(bars) < lookback:
        return {"eq_highs": [], "eq_lows": []}
    recent = bars[-lookback:]

    def _cluster(values):
        used = [False] * len(values)
        out = []
        for i, v in enumerate(values):
            if used[i]:
                continue
            group = [v]
            used[i] = True
            for j in range(i + 1, len(values)):
                if not used[j] and abs(values[j] - v) <= tolerance:
                    group.append(values[j])
                    used[j] = True
            if len(group) >= min_count:
                out.append((round(sum(group) / len(group), 1), len(group)))
        return out

    return {
        "eq_highs": _cluster([b.get("high", 0) for b in recent]),
        "eq_lows": _cluster([b.get("low", 0) for b in recent]),
    }


def _detect_sweeps(bars, pool_prices, lookback=20, tolerance=0.5):
    """Detect if any pool in `pool_prices` has been swept (high/low beyond pool,
    close returns) within the last `lookback` bars."""
    if not bars or not pool_prices:
        return []
    recent = bars[-lookback:]
    sweeps = []
    for pool in pool_prices:
        for b in recent:
            hi = b.get("high", 0)
            lo = b.get("low", float("inf"))
            c = b.get("close", 0)
            if hi > pool + tolerance and c < pool:
                sweeps.append({"pool": pool, "direction": "above",
                               "age_bars": len(bars) - 1 - bars.index(b)})
                break
            if lo < pool - tolerance and c > pool:
                sweeps.append({"pool": pool, "direction": "below",
                               "age_bars": len(bars) - 1 - bars.index(b)})
                break
    return sweeps


def build_liquidity_block(bars_m5, current_price):
    """Emit liquidity block: pools + equal extremes + sweeps."""
    try:
        from market_context import find_liquidity_pools
        pools = find_liquidity_pools(bars_m5, current_price,
                                     tolerance_usd=1.0, lookback=80,
                                     max_per_side=4)
        eq = _equal_extremes(bars_m5, lookback=50, tolerance=1.0, min_count=3)
        all_pools = list(pools.get("pools_above") or []) + \
                    list(pools.get("pools_below") or [])
        sweeps = _detect_sweeps(bars_m5, all_pools, lookback=20, tolerance=0.5)

        lines = ["", "═══ LIQUIDITY ═══"]
        # Pools
        above = pools.get("pools_above") or []
        below = pools.get("pools_below") or []
        if above or below:
            lines.append(f"  Stop pools (clusters d'stops):")
            if above:
                lines.append(f"    ↑ sobre: " +
                             ", ".join(f"{p}" for p in above))
            if below:
                lines.append(f"    ↓ sota:  " +
                             ", ".join(f"{p}" for p in below))
        else:
            lines.append("  Stop pools: cap detectat")

        # Equal extremes
        if eq["eq_highs"] or eq["eq_lows"]:
            lines.append("")
            lines.append("  Equal Highs/Lows (stop-run targets):")
            for price, count in eq["eq_highs"]:
                lines.append(f"    EQ_HIGH {price} (×{count} touches)")
            for price, count in eq["eq_lows"]:
                lines.append(f"    EQ_LOW  {price} (×{count} touches)")

        # Sweeps
        if sweeps:
            lines.append("")
            lines.append("  Sweeps recents (pool superat + recuperat):")
            for s in sweeps:
                lines.append(f"    {s['pool']} ({s['direction']}) "
                             f"fa {s['age_bars']}b")

        # ASIMETRIA DE LIQUIDITAT — NEW: agrega quants pools/equal-extremes
        # hi ha a cada costat del preu i a quina distància mitjana. Quan un
        # costat té molta més liquiditat que l'altre, el preu sol anar
        # primer a buscar-la (caça de stops natural).
        eq_highs_list = eq.get("eq_highs", []) or []
        eq_lows_list  = eq.get("eq_lows", []) or []
        n_above = len(above) + len(eq_highs_list)
        n_below = len(below) + len(eq_lows_list)
        if n_above > 0 or n_below > 0:
            # Distàncies mitjanes
            dists_above = []
            for p in above:
                try:
                    dists_above.append(float(p) - current_price)
                except (TypeError, ValueError):
                    pass
            for price_eq, _ in eq_highs_list:
                try:
                    dists_above.append(float(price_eq) - current_price)
                except (TypeError, ValueError):
                    pass
            dists_below = []
            for p in below:
                try:
                    dists_below.append(current_price - float(p))
                except (TypeError, ValueError):
                    pass
            for price_eq, _ in eq_lows_list:
                try:
                    dists_below.append(current_price - float(price_eq))
                except (TypeError, ValueError):
                    pass
            avg_above = sum(dists_above) / len(dists_above) if dists_above else None
            avg_below = sum(dists_below) / len(dists_below) if dists_below else None
            min_above = min(dists_above) if dists_above else None
            min_below = min(dists_below) if dists_below else None

            ratio_above = n_above / max(n_below, 1)
            asym_label = "balanced"
            if ratio_above >= 2.0:
                asym_label = "asymmetric_above"  # més pools/EQ_HIGHs sobre = stops a sobre
            elif ratio_above <= 0.5:
                asym_label = "asymmetric_below"  # més pools/EQ_LOWs sota = stops a sota

            lines.append("")
            lines.append(
                f"  Asimetria liquiditat: {asym_label} "
                f"({n_above} sobre / {n_below} sota)"
            )
            if dists_above:
                lines.append(
                    f"    sobre: més proper +${min_above:.1f}, mitjà +${avg_above:.1f}"
                )
            if dists_below:
                lines.append(
                    f"    sota:  més proper -${min_below:.1f}, mitjà -${avg_below:.1f}"
                )
            lines.append(
                "    (el preu sol anar primer cap on hi ha més liquiditat acumulada)"
            )

        return "\n".join(lines)
    except Exception as e:
        return f"\n═══ LIQUIDITY ═══\n  (error: {e})"


# ═════════════════════════════════════════════════════════════════════════
# BLOC 4 — CONTEXT EXTERN AMPLIAT (correlations)
# ═════════════════════════════════════════════════════════════════════════

_EXTERNAL_CACHE = {}  # symbol → {ts, data}


def _fetch_correlated_snapshot(tv_helper, symbol, cache_seconds=900):
    """Fetch an external symbol's last price + short trend. Same pattern as DXY.

    Default cache 15min — SPX/NDX/Oil change slowly enough that 15min staleness
    is perfectly fine for cross-asset context, and dramatically reduces chart-
    swap frequency (each fetch swaps the active TV chart).

    SAFETY NET 2026-05-04: explicit chart_set_symbol(XAUUSD) post-fetch
    perquè ohlcv-sym ocasionalment falla restoring → chart stuck.
    """
    now = time.time()
    entry = _EXTERNAL_CACHE.get(symbol)
    if entry and (now - entry["ts"]) < cache_seconds:
        return entry["data"]
    try:
        resp = tv_helper("ohlcv-sym", symbol, 60, timeout=12)
    except Exception:
        resp = None
    # Safety: força el restore a XAUUSD M5
    try:
        tv_helper("symbol", "OANDA:XAUUSD", timeout=15)
        tv_helper("timeframe", "5", timeout=10)
    except Exception:
        pass
    if not resp or not resp.get("success") or not resp.get("bars"):
        _EXTERNAL_CACHE[symbol] = {"ts": now, "data": None}
        return None
    bars = resp["bars"]
    if len(bars) < 10:
        return None
    closes = [b.get("close", 0) for b in bars]
    last = closes[-1]
    prev_20 = closes[-21] if len(closes) >= 21 else closes[0]
    change_pct = round(100.0 * (last - prev_20) / prev_20, 3) if prev_20 else 0.0
    # Trend: compare last to SMA20
    sma = sum(closes[-20:]) / min(20, len(closes))
    if last > sma * 1.001:
        trend = "UP"
    elif last < sma * 0.999:
        trend = "DOWN"
    else:
        trend = "FLAT"
    data = {"price": round(last, 3), "change_20b_pct": change_pct,
            "trend_m15": trend, "bars": bars}
    _EXTERNAL_CACHE[symbol] = {"ts": now, "data": data}
    return data


def _rolling_correlation(xs, ys, window=30):
    """Pearson correlation of last `window` values. Returns in [-1, +1]."""
    if len(xs) < window or len(ys) < window:
        return None
    xs = xs[-window:]
    ys = ys[-window:]
    mx = sum(xs) / window
    my = sum(ys) / window
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = (sum((x - mx) ** 2 for x in xs)) ** 0.5
    den_y = (sum((y - my) ** 2 for y in ys)) ** 0.5
    if den_x == 0 or den_y == 0:
        return None
    return round(num / (den_x * den_y), 2)


def build_extended_context_block(tv_helper, bars_m5):
    """Emit extended cross-asset block: SPX, NDX, Oil + correlacions.

    Re-habilitat 2026-05-04 per no perdre qualitat de context macro.
    SERIALITZAT: max_workers=1 perquè els 3 ohlcv-sym fan swap del chart
    i les fetches paral·leles competien causant race conditions. Cada
    call té cache 900s (15min) → el cold-start storm només passa 1 cop.
    El primary feed del FastEngine (cycles 2s) restaura el chart si
    queda stuck. Risk residual: chart pot estar 1-2s en SPX entre fetch
    i restore — acceptable.
    """
    if tv_helper is None or not bars_m5:
        return ""
    try:
        import concurrent.futures as _cf
        xau_closes = [b.get("close", 0) for b in bars_m5]

        symbols = [
            ("SP:SPX", "SPX"),
            ("NASDAQ:IXIC", "NDX"),
            ("NYMEX:CL1!", "WTI Oil"),
        ]
        results = []
        with _cf.ThreadPoolExecutor(max_workers=1) as pool:
            futures = {pool.submit(_fetch_correlated_snapshot, tv_helper, sym): (sym, label)
                       for sym, label in symbols}
            for fut in _cf.as_completed(futures, timeout=60):
                sym, label = futures[fut]
                try:
                    snap = fut.result()
                except Exception:
                    snap = None
                if snap:
                    other_closes = [b.get("close", 0) for b in snap["bars"]]
                    corr = _rolling_correlation(xau_closes, other_closes, window=30)
                    results.append((label, sym, snap, corr))

        if not results:
            return ""

        lines = ["", "═══ CORRELACIONS (cross-asset) ═══"]
        for label, sym, snap, corr in results:
            corr_txt = f"corr 30b={corr:+.2f}" if corr is not None else "corr=N/A"
            lines.append(f"  {label:>8s} ({sym}): {snap['price']}  "
                         f"{snap['trend_m15']}  Δ20b {snap['change_20b_pct']:+.2f}%  "
                         f"{corr_txt}")
        return "\n".join(lines)
    except Exception as e:
        return f"\n═══ CORRELACIONS ═══\n  (error: {e})"


# ═════════════════════════════════════════════════════════════════════════
# BLOC 6 — TÈCNIC MILLORAT (divergences, EMA ribbon, Bollinger, ATR H1/D1)
# ═════════════════════════════════════════════════════════════════════════

def _rsi_series(closes, n=14):
    """Rolling RSI series. Returns list of same length as closes (first n items None)."""
    if len(closes) < n + 1:
        return [None] * len(closes)
    out = [None] * n
    gains_sum = sum(max(closes[i] - closes[i - 1], 0) for i in range(1, n + 1))
    losses_sum = sum(max(closes[i - 1] - closes[i], 0) for i in range(1, n + 1))
    avg_g = gains_sum / n
    avg_l = losses_sum / n
    rs = avg_g / avg_l if avg_l else 1e9
    out.append(round(100 - 100 / (1 + rs), 1))
    for i in range(n + 1, len(closes)):
        g = max(closes[i] - closes[i - 1], 0)
        l = max(closes[i - 1] - closes[i], 0)
        avg_g = (avg_g * (n - 1) + g) / n
        avg_l = (avg_l * (n - 1) + l) / n
        rs = avg_g / avg_l if avg_l else 1e9
        out.append(round(100 - 100 / (1 + rs), 1))
    return out


def _detect_divergence(bars, closes, rsis, window=30):
    """Return 'bullish' | 'bearish' | None.

    Bullish div: price made LL vs prior low, but RSI made HL.
    Bearish div: price made HH vs prior high, but RSI made LH.
    """
    if len(closes) < window + 5 or len([r for r in rsis if r is not None]) < window:
        return None
    recent = closes[-window:]
    rs = rsis[-window:]
    # Find two most recent significant pivots
    lows_idx = []
    highs_idx = []
    for i in range(3, len(recent) - 3):
        if recent[i] < min(recent[i - 3:i]) and recent[i] < min(recent[i + 1:i + 4]):
            lows_idx.append(i)
        if recent[i] > max(recent[i - 3:i]) and recent[i] > max(recent[i + 1:i + 4]):
            highs_idx.append(i)
    if len(lows_idx) >= 2:
        i1, i2 = lows_idx[-2], lows_idx[-1]
        if recent[i2] < recent[i1] and rs[i2] and rs[i1] and rs[i2] > rs[i1]:
            return "bullish"
    if len(highs_idx) >= 2:
        i1, i2 = highs_idx[-2], highs_idx[-1]
        if recent[i2] > recent[i1] and rs[i2] and rs[i1] and rs[i2] < rs[i1]:
            return "bearish"
    return None


def _ema_series(closes, n):
    """Compute single EMA(n) trailing — returns final value or None."""
    if len(closes) < n:
        return None
    k = 2 / (n + 1)
    e = sum(closes[:n]) / n
    for v in closes[n:]:
        e = v * k + e * (1 - k)
    return round(e, 2)


def _bollinger_status(closes, n=20, mult=2.0):
    """Compute BB state — width vs avg, price position (%)."""
    if len(closes) < n * 2:
        return None
    # Window of last n closes
    current = closes[-n:]
    mean = sum(current) / n
    var = sum((c - mean) ** 2 for c in current) / n
    std = var ** 0.5
    upper = mean + mult * std
    lower = mean - mult * std
    width = upper - lower
    # Rolling width avg over the last n bars
    widths = []
    for i in range(n, len(closes) + 1):
        w = closes[i - n:i]
        m = sum(w) / n
        v = sum((c - m) ** 2 for c in w) / n
        widths.append(2 * mult * (v ** 0.5))
    avg_w = sum(widths[-n:]) / min(n, len(widths))
    # Position of price within bands (%)
    last = closes[-1]
    pos = 100.0 * (last - lower) / (upper - lower) if upper > lower else 50
    status = "normal"
    if width < avg_w * 0.6:
        status = "squeeze"
    elif width > avg_w * 1.5:
        status = "expanding"
    return {"mean": round(mean, 2), "upper": round(upper, 2),
            "lower": round(lower, 2), "width": round(width, 2),
            "avg_width": round(avg_w, 2), "status": status,
            "price_pct_in_band": round(pos, 1)}


def build_technical_block(bars_m5):
    """Emit technical block: divergences + EMA ribbon + Bollinger + ATR percentiles."""
    try:
        if not bars_m5 or len(bars_m5) < 50:
            return ""
        bars_m15 = _aggregate(bars_m5, 3)
        closes_m5 = [b.get("close", 0) for b in bars_m5]
        closes_m15 = [b.get("close", 0) for b in bars_m15]

        lines = ["", "═══ TÈCNIC AVANÇAT ═══"]

        # Divergences
        rsi_m5 = _rsi_series(closes_m5, 14)
        rsi_m15 = _rsi_series(closes_m15, 14)
        div_m5 = _detect_divergence(bars_m5, closes_m5, rsi_m5, window=30)
        div_m15 = _detect_divergence(bars_m15, closes_m15, rsi_m15, window=30)
        lines.append(f"  RSI Divergences: "
                     f"M5={div_m5 or 'cap'} · M15={div_m15 or 'cap'}")

        # EMA ribbon M15
        e8 = _ema_series(closes_m15, 8)
        e21 = _ema_series(closes_m15, 21)
        e50 = _ema_series(closes_m15, 50)
        e200 = _ema_series(closes_m15, 200)
        if all(v is not None for v in (e8, e21, e50, e200)):
            # Stack order
            if e8 > e21 > e50 > e200:
                stack = "BULLISH_STACK (8>21>50>200)"
            elif e8 < e21 < e50 < e200:
                stack = "BEARISH_STACK (8<21<50<200)"
            else:
                stack = "MIXED"
            lines.append(f"  EMA Ribbon M15: 8={e8} 21={e21} 50={e50} 200={e200} → {stack}")
        elif e8 and e21 and e50:
            if e8 > e21 > e50:
                stack = "BULLISH (curt termini)"
            elif e8 < e21 < e50:
                stack = "BEARISH (curt termini)"
            else:
                stack = "MIXED"
            lines.append(f"  EMA Ribbon M15: 8={e8} 21={e21} 50={e50} → {stack}")

        # Bollinger M15
        bb = _bollinger_status(closes_m15, n=20, mult=2.0)
        if bb:
            lines.append(f"  Bollinger M15 (20,2): mean={bb['mean']} "
                         f"[{bb['lower']} – {bb['upper']}]  "
                         f"width={bb['width']} vs avg {bb['avg_width']} → "
                         f"{bb['status']}  price_in_band={bb['price_pct_in_band']}%")

        # ATR percentiles H1 + D1 (rough — reuse market_context helper if possible)
        try:
            from market_context import compute_atr_percentile
            bars_h1 = _aggregate(bars_m5, 12)
            # ATR H1
            if len(bars_h1) > 15:
                trs = [max(bars_h1[i].get("high", 0) - bars_h1[i].get("low", 0),
                           abs(bars_h1[i].get("high", 0) - bars_h1[i - 1].get("close", 0)),
                           abs(bars_h1[i].get("low", 0) - bars_h1[i - 1].get("close", 0)))
                       for i in range(1, len(bars_h1))]
                atr_h1 = sum(trs[-14:]) / 14
                p_h1 = compute_atr_percentile(bars_h1, atr_h1, lookback_days=20)
                lines.append(f"  ATR H1={p_h1.get('atr_current')}  "
                             f"percentil 20d={p_h1.get('percentile_20d')}"
                             f"{' ⚠ anòmal' if p_h1.get('is_anomalous') else ''}")
        except Exception:
            pass

        return "\n".join(lines)
    except Exception as e:
        return f"\n═══ TÈCNIC AVANÇAT ═══\n  (error: {e})"


# ═════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR
# ═════════════════════════════════════════════════════════════════════════

def build_for_executor(bars_m5, tv_helper, gc_m5=None):
    """Focused rich-context subset for the Executor role.

    Skips the full MTF bar tables (Executor already has its own bars in the JSON
    payload) and the correlations block (tactical trade management doesn't need
    SPX/NDX/Oil). Keeps the pieces that most improve AVERAGE / PARTIAL_CLOSE /
    TP-placement judgement: volume profile, liquidity, technical state.

    Args:
        gc_m5: optional COMEX:GC1! M5 bars per al volume profile institucional.
            Si None o buit, només es mostra el spot. La valoració d'averaging
            i targets millora moltíssim coneixent on hi ha CONTRACTES REALS
            acumulats (Naked POCs futures = imants institucionals forts).
    """
    parts = []
    try:
        parts.append(build_volume_profile_block(bars_m5, tv_helper))
    except Exception:
        pass
    # Volume Profile FUTURES — NEW: dóna a l'EXECUTOR el mateix mapa
    # institucional que té l'INDICATOR. Naked POCs futures són imants
    # forts per a targets de profit i decisions d'averaging.
    try:
        if gc_m5:
            parts.append(build_futures_volume_profile_block(gc_m5))
    except Exception:
        pass
    try:
        price = bars_m5[-1].get("close", 0) if bars_m5 else 0
        parts.append(build_liquidity_block(bars_m5, price))
    except Exception:
        pass
    try:
        parts.append(build_technical_block(bars_m5))
    except Exception:
        pass
    return "\n".join(p for p in parts if p)


def build_for_hunter(bars_m5, tv_helper):
    """Focused rich-context subset for the Hunter role.

    Hunter scans for new reversal setups when no trade is open — it benefits
    most from volume profile (POC/HVN/naked POCs = high-probability reversal
    spots), liquidity (stop-run traps), technical state (divergences,
    Bollinger), and the MTF structure summary. Skips cross-asset correlations
    to save tokens.
    """
    parts = []
    try:
        parts.append(build_mtf_structure_block(bars_m5))
    except Exception:
        pass
    try:
        parts.append(build_volume_profile_block(bars_m5, tv_helper))
    except Exception:
        pass
    try:
        price = bars_m5[-1].get("close", 0) if bars_m5 else 0
        parts.append(build_liquidity_block(bars_m5, price))
    except Exception:
        pass
    try:
        parts.append(build_technical_block(bars_m5))
    except Exception:
        pass
    return "\n".join(p for p in parts if p)


def build_all(bars_m5, account, tv_helper, atr_m15=None, now_utc=None,
              bars_m15=None, bars_h1=None, bars_h4=None, bars_d1=None,
              gc_m5=None, gc_h4=None, gc_d1=None):
    """Build the full rich context block for the Indicator prompt.

    Args:
        bars_m5: list of M5 bar dicts (>=100 preferable, ideally 288=24h)
        account: dict with at least balance, equity, dd, positions (kept for
                 signature compatibility)
        tv_helper: trader_brain.tv function (can be None for offline tests)
        atr_m15: optional ATR M15 for context
        now_utc: optional datetime override (for tests)
        bars_m15: optional dedicated M15 bars (288=72h ideal). Used by MTF
                 structure block + volume profile so swings/BOS/POC reflect
                 actual M15 candles, not M5×3 aggregations.
        bars_h1: optional dedicated H1 bars (168=7d ideal). H4 and D1 are
                 derived from H1 by aggregation (×4 and ×24).
        gc_m5: optional COMEX:GC1! M5 bars (288=24h ideal) for futures
                 volume profile institucional. Si None o buit, el bloc
                 futures simplement no s'imprimeix.
        gc_h4: optional COMEX:GC1! H4 bars (180=30d ideal) per a POCs
                 institucionals setmanals/multi-setmanals.
        gc_d1: optional COMEX:GC1! D1 bars (30=30d ideal) per a POCs
                 institucionals mensuals — Naked POCs aquí són imants
                 multi-setmanals d'altíssima rellevància.

    Returns a single concatenated text block. Always returns a string, even on
    partial failure.
    """
    parts = []
    try:
        parts.append(build_mtf_structure_block(bars_m5, bars_m15=bars_m15, bars_h1=bars_h1, bars_h4=bars_h4, bars_d1=bars_d1))
    except Exception:
        pass
    try:
        # Volume profile now also benefits from M15 if available — wider
        # coverage = more representative POC/HVN/VAH/VAL.
        parts.append(build_volume_profile_block(bars_m5, tv_helper))
    except Exception:
        pass
    # NEW: Volume profile FUTURES (COMEX:GC1!) — només si tenim bars de gc.
    # Volum institucional real, complement (no substitut) del POC spot.
    try:
        if gc_m5:
            parts.append(build_futures_volume_profile_block(gc_m5,
                                                            gc_h4=gc_h4,
                                                            gc_d1=gc_d1))
    except Exception:
        pass
    try:
        price = bars_m5[-1].get("close", 0) if bars_m5 else 0
        parts.append(build_liquidity_block(bars_m5, price))
    except Exception:
        pass
    try:
        parts.append(build_extended_context_block(tv_helper, bars_m5))
    except Exception:
        pass
    try:
        parts.append(build_technical_block(bars_m5))
    except Exception:
        pass
    # Multi-day H1 swings + volume nodes block (NEW 2026-04-27)
    try:
        if bars_h1 and len(bars_h1) >= 24:
            parts.append(_build_multi_day_h1_block(bars_h1))
    except Exception:
        pass
    return "\n".join(p for p in parts if p)


def _build_multi_day_h1_block(bars_h1):
    """Emit a multi-day H1 swing + volume node block. Helps the Indicator
    identify zones where the price reacted days ago (not just last 24h)."""
    if not bars_h1 or len(bars_h1) < 24:
        return ""
    lines = ["", "═══ ESTRUCTURA H1 MULTI-DIA (últimes setmanes) ═══"]

    # Significant swings on H1 (last 7 days)
    highs = _find_pivots(bars_h1, 5, "high")[-8:]
    lows = _find_pivots(bars_h1, 5, "low")[-8:]
    if highs:
        lines.append("  Swing highs H1 recents:")
        for idx, v in highs[::-1]:
            age_h = len(bars_h1) - 1 - idx
            lines.append(f"    {v:.2f}  (fa {age_h}h ≈ {age_h/24:.1f}d)")
    if lows:
        lines.append("  Swing lows H1 recents:")
        for idx, v in lows[::-1]:
            age_h = len(bars_h1) - 1 - idx
            lines.append(f"    {v:.2f}  (fa {age_h}h ≈ {age_h/24:.1f}d)")

    # Volume nodes on H1: bucket by ~$2 and find top 8 highest-volume buckets
    try:
        total_vol = sum(b.get("volume", 0) for b in bars_h1)
        if total_vol > 0:
            bucket_size = 2.0
            buckets = {}
            for b in bars_h1:
                mid = (b.get("high", 0) + b.get("low", 0)) / 2
                if mid <= 0:
                    continue
                key = round(mid / bucket_size) * bucket_size
                buckets[key] = buckets.get(key, 0) + b.get("volume", 0)
            top = sorted(buckets.items(), key=lambda kv: kv[1], reverse=True)[:8]
            top.sort(key=lambda kv: kv[0])  # back to price order for readability
            if top:
                lines.append("  Zones d'alt volum H1 (institucional, 7d):")
                for price_bucket, vol in top:
                    lines.append(f"    ${price_bucket:.1f}  vol={int(vol):,}")
    except Exception:
        pass

    # Recent rejection candles on H1 (engulfing / pin bar / shooting-star equivalents)
    try:
        rejections = []
        for i in range(max(0, len(bars_h1) - 60), len(bars_h1)):
            b = bars_h1[i]
            o, c, h, l = (b.get("open", 0), b.get("close", 0),
                          b.get("high", 0), b.get("low", 0))
            if o == 0 or c == 0 or h == l:
                continue
            body = abs(c - o)
            up_wick = h - max(o, c)
            dn_wick = min(o, c) - l
            rng = h - l
            if rng <= 0:
                continue
            # Pin bar criteria
            if dn_wick > 2 * body and dn_wick > rng * 0.5 and c > o:
                rejections.append(("BULL_PIN", i, l, h))
            elif up_wick > 2 * body and up_wick > rng * 0.5 and c < o:
                rejections.append(("BEAR_PIN", i, l, h))
        if rejections:
            lines.append("  Rebuigs H1 recents (pin bars):")
            for kind, i, low, high in rejections[-6:][::-1]:
                age_h = len(bars_h1) - 1 - i
                if kind == "BULL_PIN":
                    lines.append(f"    BULL_PIN low={low:.2f} (fa {age_h}h)")
                else:
                    lines.append(f"    BEAR_PIN high={high:.2f} (fa {age_h}h)")
    except Exception:
        pass

    return "\n".join(lines)
