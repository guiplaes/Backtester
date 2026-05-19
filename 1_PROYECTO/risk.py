"""Risk projection helpers for the Executor.

Exposes `project_safety_net_price` and `build_dd_projection` used to inject
the dd_projection array into the Executor prompt so the LLM can reason about
where each averaging multiplier would leave the 3.5% DD safety net.
"""
from __future__ import annotations
from typing import Optional, List, Dict


def project_safety_net_price(
    current_price: float,
    direction: str,
    current_total_lots: float,
    new_lot: float,
    dd_usd_used: float,
    dd_limit_usd: float,
    contract_value_per_usd: float = 100.0,
) -> Optional[float]:
    """Price where the 3.5% DD safety net would trigger if `new_lot` is opened now.

    Assumes a single-symbol aggregate where every $1 move against the weighted
    position costs `total_lots × contract_value_per_usd` dollars.

    Returns None when the math degenerates (no lots, or already past the limit).
    """
    dd_remaining_usd = dd_limit_usd - dd_usd_used
    if dd_remaining_usd <= 0:
        return current_price  # already at/past hard stop
    total_lots_after = current_total_lots + new_lot
    if total_lots_after <= 0:
        return None
    adverse_move_usd = dd_remaining_usd / (total_lots_after * contract_value_per_usd)
    if direction == "BUY":
        return current_price - adverse_move_usd
    if direction == "SELL":
        return current_price + adverse_move_usd
    return None


def build_dd_projection(
    *,
    current_price: float,
    direction: str,
    current_total_lots: float,
    base_lot: float,
    max_multiplier: int,
    dd_usd_used: float,
    dd_limit_usd: float,
    contract_value_per_usd: float = 100.0,
) -> List[Dict]:
    """Array of {multiplier, new_lot, safety_net_price} for multipliers 1..max."""
    out: List[Dict] = []
    for m in range(1, max_multiplier + 1):
        new_lot = round(base_lot * m, 2)
        sn = project_safety_net_price(
            current_price=current_price,
            direction=direction,
            current_total_lots=current_total_lots,
            new_lot=new_lot,
            dd_usd_used=dd_usd_used,
            dd_limit_usd=dd_limit_usd,
            contract_value_per_usd=contract_value_per_usd,
        )
        out.append({
            "multiplier": m,
            "new_lot": new_lot,
            "safety_net_price": round(sn, 2) if sn is not None else None,
        })
    return out


def find_next_adverse_zone(
    *,
    current_price: float,
    direction: str,
    zones: List[Dict],
    min_strength_rank: int = 0,
) -> Optional[Dict]:
    """Nearest zone the price would traverse if the trade goes adversely.

    - BUY trade adverse = price falls → look for SUPPORT below current_price.
    - SELL trade adverse = price rises → look for RESISTANCE above current_price.

    Returns {price, strength, type, distance_usd} or None.
    """
    rank = {"WEAK": 0, "MODERATE": 1, "STRONG": 2}
    if direction == "BUY":
        candidates = [
            z for z in zones
            if z.get("type") == "SUPPORT"
            and float(z.get("price", 0)) < current_price
            and rank.get(z.get("strength", "WEAK"), 0) >= min_strength_rank
        ]
        if not candidates:
            return None
        z = max(candidates, key=lambda x: float(x.get("price", 0)))
    elif direction == "SELL":
        candidates = [
            z for z in zones
            if z.get("type") == "RESISTANCE"
            and float(z.get("price", 0)) > current_price
            and rank.get(z.get("strength", "WEAK"), 0) >= min_strength_rank
        ]
        if not candidates:
            return None
        z = min(candidates, key=lambda x: float(x.get("price", 0)))
    else:
        return None
    zp = float(z.get("price", 0))
    return {
        "price": round(zp, 2),
        "strength": z.get("strength"),
        "type": z.get("type"),
        "distance_usd": round(abs(zp - current_price), 2),
    }
