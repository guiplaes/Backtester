"""Sniper orders — pre-placed averaging triggers.

The Executor LLM proposes one or more specific price levels where — if the
price touches — it wants an immediate MARKET averaging fired WITHOUT waiting
for candle confirmation. Useful for high-conviction reversal levels where a
fast spike could escape the FastEngine's bar-close + pattern check.

Philosophy (user request):
  · The LLM decides which levels. No deterministic confluence rules in code.
  · No SL. The 3.5% EA auto-close is the only safety net.
  · Lot = multiplier × base_lot, same formula as in-zone AVG.
  · Uncapped (MAX_AVG_PER_SIGNAL sentinel is 999).

Lifecycle:
  · Executor response carries `pre_place_orders: [{direction, price, multiplier, reason}]`
  · Snipers are persisted to brain_snipers.json and rendered on chart.
  · Every FastEngine tick: if active signal + price touches a sniper → fire MARKET
    immediately, respecting global cooldown (MIN_AVG_COOLDOWN) and DD projection.
  · Auto-cancel on: signal close, BE set, direction flip, sniper fires once.
  · TTL 45 min (if price hasn't touched, context may have shifted).
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

COMMON = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files")
FILE = COMMON / "brain_snipers.json"
DEFAULT_TTL_S = 45 * 60  # 45 minutes
DEFAULT_TOLERANCE_USD = 0.5

_lock = threading.Lock()


def _read():
    try:
        with open(FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("snipers", []) or []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _write(snipers):
    try:
        with open(FILE, "w", encoding="utf-8") as f:
            json.dump({"updated_at": time.time(), "snipers": snipers},
                      f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def load():
    """DESACTIVAT 2026-05-04 — Mode Recorregut Institucional NO usa snipers
    (filosofia single-trade, no averaging). Sempre retorna [] perquè cap
    integració dispari snipers. La resta del mòdul queda com a no-op."""
    return []


def replace_for_signal(new_snipers, signal_direction, signal_id=None):
    """Replace the sniper list atomically.

    Called after an Executor response: whatever the LLM proposed this cycle
    becomes the authoritative list. Legacy snipers from prior cycles with the
    same `signal_id` are dropped; ones for a different signal_id are retained
    (edge case: shouldn't happen, but defensive).

    NO-SIGNAL GUARD (2026-04-27): snipers exist for AVERAGING into an active
    trade. Without an active signal there is no position to average — a
    sniper firing would OPEN a counter-trend BUY/SELL with zero context. Any
    proposal received without `signal_direction` or `signal_id` is rejected
    AND the entire active sniper list is wiped (in case stale ones survived).

    FIRED DEDUP (2026-04-24): a sniper is one-shot per signal. If the Executor
    re-proposes the same price within FIRED_DEDUP_TOLERANCE_USD of a
    previously-fired sniper in the same signal, the new proposal is silently
    dropped. The price level has already done its job. If the system wants
    to re-enter there, it must come through a different path (in-zone AVG,
    new signal, etc.) — not a fresh sniper.
    """
    # No-signal guard: must have BOTH direction and id from active signal.
    if not signal_direction or not signal_id:
        with _lock:
            existing = _read()
            now = time.time()
            wiped = 0
            for s in existing:
                if not s.get("_fired") and not s.get("_cancelled"):
                    s["_cancelled"] = True
                    s["_cancelled_at"] = now
                    s["_cancel_reason"] = "no_active_signal"
                    wiped += 1
            _write(existing)
        return 0, 0, len(new_snipers or []) + wiped  # all proposals rejected, plus wiped count
    FIRED_DEDUP_TOLERANCE_USD = 2.0  # pts — price match window for dedup
    # STRUCTURAL GATE (2026-04-24): a sniper is meant for "high-conviction
    # reversal at a fast spike" — it MUST coincide with a STRONG zone from
    # the indicator brain's active map whose bounce_direction matches the
    # sniper direction. If the Executor proposes a level with no structural
    # backing within STRUCT_GATE_TOLERANCE_USD, reject it.
    # Rationale: the Executor was proposing levels without zone confluence,
    # leading to averaging at random prices and deep DD. The TP assigner and
    # BE path already require STRONG confluence — snipers must too.
    STRUCT_GATE_TOLERANCE_USD = 3.0  # pts — distance to a STRONG zone
    # Load current STRONG zones once so we can gate every proposal.
    strong_zones = []
    try:
        from zone_store import read_state, active_zones
        _zst = read_state(COMMON.parent)  # COMMON is the Files dir
        for z in active_zones(_zst):
            if (z.get("strength") or "").upper() != "STRONG":
                continue
            zp = float(z.get("price") or 0)
            bd = (z.get("bounce_direction") or "").upper()
            if zp > 0 and bd in ("BUY", "SELL"):
                strong_zones.append({"price": zp, "bounce": bd})
    except Exception:
        strong_zones = []

    with _lock:
        existing = _read()
        now = time.time()

        # Collect already-fired snipers in THIS signal — these act as
        # dedup anchors for the new proposals.
        fired_this_signal = [
            s for s in existing
            if s.get("_fired")
            and signal_id and s.get("signal_id") == signal_id
        ]

        # Normalize new proposals, skipping any that fail the gates.
        normalized = []
        skipped_dedup = 0
        skipped_no_struct = 0
        for s in (new_snipers or []):
            direction = (s.get("direction") or "").upper()
            price = s.get("price")
            if direction not in ("BUY", "SELL") or not price:
                continue
            # Snipers can only match the current signal's direction
            # (averaging-into adds to the same position).
            if signal_direction and direction != signal_direction:
                continue
            price_f = round(float(price), 2)
            # Dedup: if we already fired a sniper at this price in this signal,
            # drop this re-proposal. One-shot per level per signal.
            if any(abs(price_f - float(f.get("price") or 0)) <= FIRED_DEDUP_TOLERANCE_USD
                   for f in fired_this_signal):
                skipped_dedup += 1
                continue
            # Structural gate: require a STRONG zone with matching bounce
            # direction within tolerance. If no zone store available, fall
            # back to accepting (fail-open) so we don't break trades during
            # zone_store hiccups — log will flag this when it occurs.
            if strong_zones:
                has_confluence = any(
                    abs(price_f - z["price"]) <= STRUCT_GATE_TOLERANCE_USD
                    and z["bounce"] == direction
                    for z in strong_zones
                )
                if not has_confluence:
                    skipped_no_struct += 1
                    continue
            mult = float(s.get("multiplier") or 1.0)
            normalized.append({
                "id": s.get("id") or f"snp_{uuid.uuid4().hex[:8]}",
                "signal_id": signal_id,
                "direction": direction,
                "price": price_f,
                "multiplier": mult,
                "reason": (s.get("reason") or "").strip()[:300],
                "thesis": (s.get("thesis") or "").strip()[:240],
                "tolerance_usd": float(s.get("tolerance_usd") or DEFAULT_TOLERANCE_USD),
                "ttl_s": float(s.get("ttl_s") or DEFAULT_TTL_S),
                "placed_at": now,
            })

        # ── Cluster suppression on snipers (2026-04-27) ──
        # Two snipers within CLUSTER_RADIUS_USD on the same direction = same
        # structural block. Treating them as separate would re-enact the
        # 4711+4709 problem one layer up. Keep ONLY the one with the highest
        # multiplier (LLM's conviction signal). Tie-break: the one closer
        # to a STRONG zone center.
        CLUSTER_RADIUS_USD = 5.0
        if len(normalized) > 1:
            # Sort by multiplier desc — first wins each cluster.
            normalized.sort(key=lambda s: (s.get("multiplier", 1.0), len(s.get("reason") or "")), reverse=True)
            survivors = []
            dropped_cluster = 0
            for s in normalized:
                p_s = float(s.get("price") or 0)
                d_s = (s.get("direction") or "").upper()
                clustered = any(
                    d_s == (k.get("direction") or "").upper()
                    and abs(p_s - float(k.get("price") or 0)) <= CLUSTER_RADIUS_USD
                    for k in survivors
                )
                if clustered:
                    dropped_cluster += 1
                    continue
                survivors.append(s)
            if dropped_cluster:
                # Replace normalized with deduped list; surface the count to caller
                # via the third return value (skipped_no_struct) — slightly abuses
                # the field name but keeps the signature stable. Caller logs both.
                skipped_no_struct += dropped_cluster
            normalized = survivors
        # Keep: (a) snipers from DIFFERENT signals, (b) FIRED snipers of this
        # signal (they act as dedup history — preserved for the life of the
        # signal so future proposals at the same price are correctly skipped).
        kept = [s for s in existing
                if (signal_id and s.get("signal_id") not in (None, signal_id)
                    and not s.get("_fired") and not s.get("_cancelled"))
                or (s.get("_fired") and s.get("signal_id") == signal_id)]
        _write(kept + normalized)
        return len(normalized), skipped_dedup, skipped_no_struct


def mark_fired(sniper_id, fire_ts=None):
    """Mark a sniper as fired (consumed)."""
    with _lock:
        snipers = _read()
        for s in snipers:
            if s.get("id") == sniper_id:
                s["_fired"] = True
                s["_fired_at"] = fire_ts or time.time()
                break
        _write(snipers)


def cancel_all(reason="signal_close"):
    """Mark ALL snipers cancelled. Called on signal close / BE / flip."""
    with _lock:
        snipers = _read()
        now = time.time()
        n = 0
        for s in snipers:
            if not s.get("_fired") and not s.get("_cancelled"):
                s["_cancelled"] = True
                s["_cancelled_at"] = now
                s["_cancel_reason"] = reason
                n += 1
        _write(snipers)
        return n


def find_triggered(current_price, bar_high=None, bar_low=None):
    """Return the first sniper triggered by current price OR by the live bar's
    high/low (intrabar wick capture).

    For SELL snipers (target above price entry): trigger if `bar_high` crossed the
    sniper price — this captures fast wicks up into resistance that close back
    below within the tick window.
    For BUY snipers: trigger if `bar_low` crossed down to the sniper price.
    Falls back to `current_price` if bar high/low not provided.

    Explosion gate: when explosion_detector.is_active() returns True (STRONG
    break adverse + anomaly), snipers are frozen — the system waits for new
    zones from Indicator instead of firing into a structurally-broken map.
    """
    try:
        import explosion_detector
        if explosion_detector.is_active():
            return None
    except Exception:
        pass

    for s in load():
        try:
            p = float(s.get("price") or 0)
            tol = float(s.get("tolerance_usd") or DEFAULT_TOLERANCE_USD)
            if p <= 0:
                continue
            direction = (s.get("direction") or "").upper()
            # Close-based check (always applied)
            if abs(current_price - p) <= tol:
                return s
            # Intrabar wick-based check — captures fast spikes that retrace
            if bar_high is not None and direction == "SELL":
                if bar_high >= p - tol:
                    return s
            if bar_low is not None and direction == "BUY":
                if bar_low <= p + tol:
                    return s
        except (TypeError, ValueError):
            continue
    return None


def clear_all():
    """Wipe the sniper list entirely (debug / reset)."""
    with _lock:
        _write([])
