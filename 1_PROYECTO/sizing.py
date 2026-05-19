"""Lot sizing primitives.

Single source of truth for base_lot × multiplier calculation used by both
the Executor response parser and the FastEngine reflex path.
"""
from __future__ import annotations
from typing import Optional


class SizingError(ValueError):
    """Raised when a malformed multiplier reaches the sizing layer."""


def validate_multiplier(value, max_multiplier: int) -> int:
    """Strict: must be an int (not bool) within [1, max_multiplier]."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise SizingError(f"multiplier must be int, got {type(value).__name__}={value!r}")
    if value < 1 or value > max_multiplier:
        raise SizingError(f"multiplier {value} out of range [1, {max_multiplier}]")
    return value


def lot_from_multiplier(base_lot: float, multiplier: int, *, max_multiplier: int = 5) -> float:
    """Compute lot = base_lot × multiplier, rounded to broker precision."""
    m = validate_multiplier(multiplier, max_multiplier)
    return round(base_lot * m, 2)


def fast_engine_lot(
    base_lot: float,
    strength: str,
    fast_multipliers: dict,
) -> Optional[float]:
    """Reflex lot for FastEngine. Returns None if strength is disabled (0)."""
    m = int(fast_multipliers.get(strength, 0) or 0)
    if m <= 0:
        return None
    return round(base_lot * m, 2)
