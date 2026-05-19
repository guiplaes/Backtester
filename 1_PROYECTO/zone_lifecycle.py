"""Zone lifecycle — codi pur, sense LLM.

Executat a cada tick del fast loop (~3s). Manté viu l'estat de les zones
persistides per zone_store: detecta tocs, rebuigs, trencaments, i marca
zones com STALE quan fa hores sense visita.

Llindars configurables sota `zone_lifecycle:` a config.yaml.

Transicions de status possibles
───────────────────────────────
ACTIVE ─toc─> ACTIVE (touches++, last_validated_at actualitzat)
ACTIVE ─toc + vela rebuig─> ACTIVE (rejections++)
ACTIVE ─trencament net (M5 close > dist amb volum > ratio)─> INVALIDATED
ACTIVE ─sense tocs en STALE_HOURS─> STALE
STALE ─toc─> ACTIVE  (reactivació)
INVALIDATED ─ (terminal, no torna enrere) ─

IMPORTANT: aquest mòdul NO modifica mai `strength`. Aquesta decisió és
privilegi exclusiu del Zone Reviewer (capa 1).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from zone_store import (
    STATE_LOCK,
    ZONE_STATUS_ACTIVE,
    ZONE_STATUS_INVALIDATED,
    ZONE_STATUS_STALE,
    ZONE_TYPE_RESISTANCE,
    ZONE_TYPE_SUPPORT,
    mark_invalidated,
    mark_stale,
    read_state,
    record_rejection,
    record_touch,
    write_state,
)

# ── Raons d'invalidació (text fixat per facilitar telemetria) ──
INVALIDATION_REASON_CLEAN_BREAK = "clean_break_with_volume"
INVALIDATION_REASON_STALE_TIMEOUT = "stale_timeout"

# ── Tipus de vela de rebuig reconeguts ──
REJECTION_PATTERN_PIN_BAR = "pin_bar"
REJECTION_PATTERN_ENGULFING = "engulfing"


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _avg_volume(bars: list[dict], n: int = 20) -> float:
    """Mean volume of the last `n` bars (excluding the last one, which is 'current')."""
    if len(bars) < 2:
        return 0.0
    window = bars[-(n + 1) : -1] if len(bars) > n else bars[:-1]
    if not window:
        return 0.0
    total = sum(b.get("volume", 0) for b in window)
    return total / len(window) if window else 0.0


def is_pin_bar(bar: dict, direction: str) -> bool:
    """Pin bar towards `direction`: long wick on the opposite side.

    direction == "BUY" → bullish pin (long lower wick), close in upper third.
    direction == "SELL" → bearish pin (long upper wick), close in lower third.
    """
    o, h, l, c = (
        bar.get("open", 0),
        bar.get("high", 0),
        bar.get("low", 0),
        bar.get("close", 0),
    )
    rng = h - l
    if rng <= 0:
        return False
    body = abs(c - o)
    if body > rng * 0.4:  # body too big for a pin
        return False
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    if direction == "BUY":
        return lower_wick >= rng * 0.6 and c >= (l + rng * 0.66)
    if direction == "SELL":
        return upper_wick >= rng * 0.6 and c <= (l + rng * 0.33)
    return False


def is_engulfing(prev: dict, cur: dict, direction: str) -> bool:
    """Bullish/bearish engulfing of `prev` by `cur`, matching `direction`."""
    po, pc = prev.get("open", 0), prev.get("close", 0)
    co, cc = cur.get("open", 0), cur.get("close", 0)
    if direction == "BUY":
        # prev bearish, cur bullish, cur body engulfs prev body
        return pc < po and cc > co and co <= pc and cc >= po
    if direction == "SELL":
        return pc > po and cc < co and co >= pc and cc <= po
    return False


def _rejection_pattern(bars: list[dict], direction: str) -> str | None:
    """Detect rejection on the last closed bar matching `direction`."""
    if not bars:
        return None
    last = bars[-1]
    if is_pin_bar(last, direction):
        return REJECTION_PATTERN_PIN_BAR
    if len(bars) >= 2 and is_engulfing(bars[-2], last, direction):
        return REJECTION_PATTERN_ENGULFING
    return None


def _touches_zone(bar: dict, zone: dict, touch_dist_usd: float) -> bool:
    """Whether this M5 bar has penetrated the zone's tolerance band."""
    price = zone.get("price", 0)
    if zone.get("type") == ZONE_TYPE_SUPPORT:
        return bar.get("low", price) <= price + touch_dist_usd
    if zone.get("type") == ZONE_TYPE_RESISTANCE:
        return bar.get("high", price) >= price - touch_dist_usd
    return False


def _is_clean_break(
    bar: dict, zone: dict, avg_vol: float, cfg: dict
) -> bool:
    """Whether the M5 close has cleanly broken the zone with volume confirmation."""
    price = zone.get("price", 0)
    close = bar.get("close", 0)
    vol = bar.get("volume", 0)
    vol_ratio_min = float(cfg.get("breakout_volume_ratio", 1.5))
    dist_min = float(cfg.get("breakout_close_distance_usd", 1.0))
    if avg_vol > 0 and (vol / avg_vol) < vol_ratio_min:
        return False
    if zone.get("type") == ZONE_TYPE_SUPPORT:
        return close <= price - dist_min
    if zone.get("type") == ZONE_TYPE_RESISTANCE:
        return close >= price + dist_min
    return False


def tick(common_dir: str, bars_m5: list[dict], cfg: dict, now: datetime | None = None) -> dict:
    """Run one lifecycle pass over the current zone state.

    Reads brain_zone_state.json, applies transitions based on the last
    closed M5 bar + its context, and writes the state back if anything
    changed. Returns a small summary for logging: {touched, invalidated, stale, rejected}.

    Call once per fast tick (~3s). Idempotent: if no new M5 has closed,
    repeated calls are effectively no-ops (touches only increment when the
    low/high crosses the tolerance band on the *latest* bar — so calling
    multiple times per the same bar will count multiple touches only if
    the caller actually passes a new latest bar). Callers are expected to
    pass a fresh bars_m5 slice from cache.
    """
    if not bars_m5:
        return {"touched": 0, "invalidated": 0, "stale": 0, "rejected": 0}

    now = now or datetime.now(timezone.utc)
    # Hold STATE_LOCK across the full read→modify→write cycle to prevent
    # interleaved writers (Reviewer, other ticks) from silently clobbering state.
    with STATE_LOCK:
        state = read_state(common_dir)
        zones = state.get("zones", [])
        if not zones:
            return {"touched": 0, "invalidated": 0, "stale": 0, "rejected": 0}

        last_bar = bars_m5[-1]
        avg_vol = _avg_volume(bars_m5, n=20)
        touch_dist = float(cfg.get("touch_dist_usd", 0.5))
        stale_hours = float(cfg.get("stale_hours", 8))
        stale_delta_s = stale_hours * 3600.0

        changed = False
        summary = {"touched": 0, "invalidated": 0, "stale": 0, "rejected": 0}

        for zone in zones:
            status = zone.get("status")
            if status == ZONE_STATUS_INVALIDATED:
                continue

            if _touches_zone(last_bar, zone, touch_dist):
                if _is_clean_break(last_bar, zone, avg_vol, cfg):
                    mark_invalidated(zone, INVALIDATION_REASON_CLEAN_BREAK)
                    summary["invalidated"] += 1
                    changed = True
                    # Notify explosion detector for STRONG breaks — feeds the
                    # 30-min adverse-break window. Best-effort, never raises.
                    try:
                        if (zone.get("strength") or "").upper() == "STRONG":
                            import explosion_detector
                            zone_price = float(zone.get("price") or 0)
                            close_beyond = abs(float(last_bar.get("close", 0)) - zone_price)
                            vol_ratio = (float(last_bar.get("volume", 0)) / avg_vol) if avg_vol > 0 else 0
                            # signal_direction passed via cfg (caller threads it)
                            explosion_detector.record_strong_break(
                                zone_price=zone_price,
                                signal_direction=cfg.get("signal_direction"),
                                vol_ratio=vol_ratio,
                                close_beyond_usd=close_beyond,
                            )
                    except Exception:
                        pass
                    continue

                record_touch(zone)
                summary["touched"] += 1
                changed = True

                pattern = _rejection_pattern(bars_m5, zone.get("bounce_direction", ""))
                if pattern:
                    record_rejection(zone)
                    summary["rejected"] += 1
                continue

            if status == ZONE_STATUS_ACTIVE:
                last_ts = _parse_iso(zone.get("last_validated_at", ""))
                if last_ts is not None and (now - last_ts).total_seconds() >= stale_delta_s:
                    mark_stale(zone)
                    summary["stale"] += 1
                    changed = True

        if changed:
            write_state(common_dir, state)
        return summary
