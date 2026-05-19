#!/usr/bin/env python3
"""Executor Ladder — fires PARTIAL_CLOSE_PCT at the LLM's profit_targets.

Independent from broker-level TPs. The Executor's tactical plan is a list of
(price, close_pct) levels that together cover 100% of position. When price
touches a level (in trade direction), this module fires the corresponding
PARTIAL_CLOSE_PCT to the EA.

State persisted to Common/Files/brain_executor_ladder.json — survives brain
restarts. Cleared on signal close.

Schema expected at init (from staged_setup.profit_targets):
    [
      {"price": 4615.0, "close_pct": 50, "reasoning": "POC + first reaction"},
      {"price": 4605.0, "close_pct": 30, "reasoning": "VWAP setmanal"},
      {"price": 4595.0, "close_pct": 20, "reasoning": "extrem rang M15"}
    ]
Backwards-compat: plain list[float] is auto-distributed (50/50, 33/33/34, etc).

The fire condition is "price touched the level in profit direction":
    SELL: bar_low <= level (price went DOWN past the level)
    BUY:  bar_high >= level (price went UP past the level)
This avoids missing intrabar wicks.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

COMMON = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files")
LADDER_FILE = COMMON / "brain_executor_ladder.json"

_log = logging.getLogger("brain")


# ─────────────────────────────────────────────────────────────────
# Schema normalization
# ─────────────────────────────────────────────────────────────────

def _normalize_targets(profit_targets: list, direction: str) -> list[dict]:
    """Coerce profit_targets into a uniform list of {price, close_pct, reasoning}.

    Accepts:
      A) list of floats:   [4615.0, 4605.0, 4595.0]
         → close_pct distributed as evenly as possible, summing to 100
      B) list of dicts:    [{"price": ..., "close_pct": ..., "reasoning": ...}]
         → validated; sum normalized to 100 if off
    Filters out items on the wrong side of natural progression for `direction`
    (SELL: prices must descend; BUY: must ascend) — this is just an order check,
    not a trade-sense check (caller already validated profit-side of price).

    Returns [] on any unrecoverable input.
    """
    if not profit_targets:
        return []

    norm: list[dict] = []
    for item in profit_targets:
        if isinstance(item, (int, float)):
            norm.append({'price': float(item), 'close_pct': None, 'reasoning': ''})
        elif isinstance(item, dict):
            try:
                price = float(item.get('price'))
            except (TypeError, ValueError):
                continue
            cp = item.get('close_pct')
            try:
                cp_f = float(cp) if cp is not None else None
            except (TypeError, ValueError):
                cp_f = None
            norm.append({
                'price': price,
                'close_pct': cp_f,
                'reasoning': str(item.get('reasoning') or ''),
            })
    if not norm:
        return []

    # Order: closest-first along trade direction. SELL prefers descending prices;
    # BUY prefers ascending. Caller passes them already ordered usually, but we
    # enforce here so the ladder hits in the right sequence.
    if direction == 'SELL':
        norm.sort(key=lambda d: -d['price'])  # highest first (closest to entry)
    else:
        norm.sort(key=lambda d: d['price'])

    # Distribute / normalize close_pct so sum == 100 with values ∈ {25,50,75,100}.
    n = len(norm)
    has_explicit = any(d['close_pct'] is not None for d in norm)

    if has_explicit:
        # Fill missing values evenly so sum reaches 100 across remaining levels.
        explicit_sum = sum(d['close_pct'] for d in norm if d['close_pct'] is not None)
        missing = [d for d in norm if d['close_pct'] is None]
        if missing:
            remainder = max(0.0, 100.0 - explicit_sum)
            per = remainder / len(missing) if missing else 0.0
            for d in missing:
                d['close_pct'] = per
        # If sum != 100, rescale proportionally first.
        total = sum(d['close_pct'] for d in norm) or 1.0
        if abs(total - 100.0) > 0.5:
            for d in norm:
                d['close_pct'] = d['close_pct'] * 100.0 / total

    # ── Snap each level to {25, 50, 75, 100} and reconcile sum to 100 ──
    # Constraint: every close_pct ∈ {25,50,75,100} AND sum == 100.
    # Strategy:
    #   1. Convert each pct to integer 25-units (round to nearest, min 1 unit)
    #   2. Sum the units; total must equal 4 (= 100/25)
    #   3. If sum != 4, redistribute by removing/adding 1-unit blocks from
    #      the smallest/largest level until total hits 4
    #   4. Reject any level that ends up with 0 units (caller should drop it)
    if n == 1:
        # Single level → 100% (only valid choice)
        norm[0]['close_pct'] = 100
        return norm

    units = []  # list of integers in {1, 2, 3, 4} representing 25/50/75/100
    for d in norm:
        if d['close_pct'] is None:
            d['close_pct'] = 0.0
        u = round(float(d['close_pct']) / 25.0)
        u = max(0, min(4, u))
        units.append(u)

    target_units = 4  # 4 × 25 = 100
    drift = sum(units) - target_units

    # If a unit is 0, bump it to 1 (we accepted this level — it deserves a slice)
    for i, u in enumerate(units):
        if u == 0:
            units[i] = 1
    drift = sum(units) - target_units

    # Trim or grow to hit exactly target_units. Trim from the largest first
    # (preserves smaller "decision" levels). Grow on the smallest first
    # (the closest level usually carries most weight in a front-loaded plan).
    while drift > 0:
        idx = max(range(n), key=lambda i: units[i])
        if units[idx] > 1:
            units[idx] -= 1
            drift -= 1
        else:
            # Cannot reduce a 1 — pop a level entirely. Caller's intent loss
            # is acceptable: with too many levels we couldn't fit anyway.
            del norm[idx]
            del units[idx]
            n -= 1
            drift = sum(units) - target_units
    while drift < 0:
        idx = min(range(n), key=lambda i: units[i])
        if units[idx] < 4:
            units[idx] += 1
            drift += 1
        else:
            # All levels at max 100 — impossible, but break to avoid infinite.
            break

    for d, u in zip(norm, units):
        d['close_pct'] = int(u * 25)

    return norm


# ─────────────────────────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        if LADDER_FILE.exists():
            return json.loads(LADDER_FILE.read_text(encoding='utf-8'))
    except Exception as e:
        _log.warning(f"[LADDER] load failed: {e}")
    return {}


def _save_state(state: dict) -> None:
    try:
        LADDER_FILE.write_text(
            json.dumps(state, indent=2, ensure_ascii=False),
            encoding='utf-8',
        )
    except Exception as e:
        _log.warning(f"[LADDER] save failed: {e}")


def clear() -> None:
    """Wipe ladder state (call on signal close)."""
    try:
        if LADDER_FILE.exists():
            LADDER_FILE.unlink()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────
# Init from a fresh signal
# ─────────────────────────────────────────────────────────────────

def init_from_signal(profit_targets: list, direction: str,
                     signal_id: str | None = None,
                     entry_price: float | None = None) -> dict:
    """Build a ladder from the LLM's profit_targets and persist it.

    Returns the normalized ladder dict, or {} if no usable targets.

    2026-05-04: `entry_price` afegit per evitar el bug de "ladder fires immediately
    on trade open" — sense això, el bar high/low del minut actual pot ja haver
    creuat el target abans que el trade s'obrís, fent que el ladder dispari
    al moment d'entrar.
    """
    levels = _normalize_targets(profit_targets, direction)
    if not levels:
        clear()
        return {}
    state = {
        'signal_id': signal_id or '',
        'direction': direction,
        'entry_price': float(entry_price) if entry_price is not None else None,
        'created_ts': time.time(),
        'levels': [
            {**lv, 'hit': False, 'hit_ts': None, 'sent_ok': False}
            for lv in levels
        ],
    }
    _save_state(state)
    _log.info(
        f"[LADDER] Initialized {direction} with {len(levels)} levels: "
        + ", ".join(f"{lv['price']:.2f}@{lv['close_pct']:.0f}%" for lv in levels)
        + (f" — entry @ {entry_price:.2f}" if entry_price is not None else "")
    )
    return state


def refresh_preserving_hits(profit_targets: list, direction: str,
                            signal_id: str | None = None,
                            entry_price: float | None = None,
                            match_tolerance_usd: float = 0.5) -> dict:
    """2026-05-06: Refresh el ladder mantenint l'estat 'hit' dels nivells ja disparats.

    Quan l'EXECUTOR fa MANAGE i retorna profit_targets, NO podem cridar
    `init_from_signal` directament perquè això reseteja tots els nivells a
    hit=False — fent que un TP1 ja disparat torni a disparar quan el preu
    encara és prop.

    Aquesta funció:
    - Carrega l'estat actual del ladder (si n'hi ha)
    - Per a cada nivell del nou plan, busca si coincideix amb un de l'antic
      (mateix preu ±match_tolerance_usd)
    - Si coincideix i l'antic estava `hit=True`, conserva l'estat fired
    - Si és un nivell nou, l'afegeix amb hit=False

    Si no hi ha estat previ, equivalent a `init_from_signal`.
    """
    new_levels = _normalize_targets(profit_targets, direction)
    if not new_levels:
        clear()
        return {}

    prev_state = _load_state() or {}
    prev_levels = prev_state.get('levels') or []

    merged = []
    for nl in new_levels:
        merged_lv = {**nl, 'hit': False, 'hit_ts': None, 'sent_ok': False}
        # Buscar match al ladder antic per preu (mateixa direcció implicada)
        for pl in prev_levels:
            try:
                if abs(float(pl.get('price', 0)) - float(nl['price'])) <= match_tolerance_usd:
                    if pl.get('hit'):
                        merged_lv['hit'] = True
                        merged_lv['hit_ts'] = pl.get('hit_ts')
                        merged_lv['sent_ok'] = pl.get('sent_ok', False)
                    break
            except Exception:
                continue
        merged.append(merged_lv)

    state = {
        'signal_id': signal_id or prev_state.get('signal_id', ''),
        'direction': direction,
        'entry_price': float(entry_price) if entry_price is not None else prev_state.get('entry_price'),
        'created_ts': prev_state.get('created_ts') or time.time(),
        'levels': merged,
    }
    _save_state(state)
    _hit_count = sum(1 for lv in merged if lv['hit'])
    _log.info(
        f"[LADDER] Refreshed (preserving hits) {direction} with {len(merged)} levels "
        f"({_hit_count} ja fired): "
        + ", ".join(
            f"{lv['price']:.2f}@{lv['close_pct']:.0f}%{'✓' if lv['hit'] else ''}"
            for lv in merged
        )
    )
    return state


# ─────────────────────────────────────────────────────────────────
# Tick — find levels hit by current bar
# ─────────────────────────────────────────────────────────────────

def tick(direction: str, bar_high: float, bar_low: float, buffer_usd: float = 0.0,
         current_price: float | None = None) -> list[dict]:
    """Check if any unhit level was reached on the current bar.

    Returns a list of fired level dicts (with 'price', 'close_pct', 'reasoning').
    Updates state on disk to mark hit levels. Caller is responsible for sending
    PARTIAL_CLOSE_PCT to the EA per fired level (one PARTIAL per level — the
    pct applies to whatever volume remains at that moment).

    Multiple levels can fire in the same tick if the bar gaps through more
    than one. They are returned in trade-direction order so the closest level
    fires first.

    `buffer_usd` (≥0): tolerance to fire the level when price is *within*
    that distance of the target (in profit direction). Avoids missing fills
    when the wick reaches e.g. 4565.05 but the LLM target was 4565.00.
    Caller typically passes 0.15 × ATR_M1.

    `current_price` (recommended): preu del moment del tick. Si es passa, el
    ladder NOMÉS dispara si el preu actual ja és al costat de profit del nivell.
    Això evita el bug de "ladder fires immediately on entry" — el bar high/low
    pot incloure ticks d'abans que el trade s'obrís.
    """
    state = _load_state()
    if not state or state.get('direction') != direction:
        return []
    levels = state.get('levels') or []
    if not levels:
        return []

    # 2026-05-04: protecció anti-fire-on-open. Si tenim entry_price, ignorem
    # nivells que ja estaven al costat de profit en el moment d'entrar
    # (haurien d'haver-se filtrat com profit_targets vàlids al setup, però
    # per defensa també ho validem aquí).
    entry_price = state.get('entry_price')
    if entry_price is not None:
        try:
            entry_price = float(entry_price)
        except Exception:
            entry_price = None

    buffer_usd = max(0.0, float(buffer_usd or 0))
    fired: list[dict] = []
    changed = False
    for lv in levels:
        if lv.get('hit'):
            continue
        price = float(lv.get('price', 0) or 0)
        if price <= 0:
            continue
        # Validació entry_price: per SELL, target ha de ser per sota d'entry;
        # per BUY, per sobre. Si no ho és, el target era invàlid des del primer dia
        # — saltem (NO disparar mai).
        if entry_price is not None and entry_price > 0:
            if direction == 'SELL' and price >= entry_price:
                continue
            if direction == 'BUY' and price <= entry_price:
                continue
        # Direction-aware touch check with profit-side buffer:
        # SELL: fire when bar_low <= price + buffer (we close before exact touch)
        # BUY:  fire when bar_high >= price - buffer (idem on the way up)
        # NOVA SALVAGUARDA: current_price també ha d'estar al costat profit.
        # Sense això, un wick antic dins el bar M1 fa fire encara que el preu
        # ARA estigui lluny del target. Aquell tick va passar abans que el
        # trade s'obrís → no s'ha de comptar com a hit real.
        touched = False
        if direction == 'SELL' and bar_low is not None and bar_low <= price + buffer_usd:
            if current_price is None or current_price <= price + buffer_usd:
                touched = True
        elif direction == 'BUY' and bar_high is not None and bar_high >= price - buffer_usd:
            if current_price is None or current_price >= price - buffer_usd:
                touched = True
        if touched:
            lv['hit'] = True
            lv['hit_ts'] = time.time()
            fired.append(dict(lv))  # snapshot
            changed = True

    if changed:
        _save_state(state)
    return fired


# ─────────────────────────────────────────────────────────────────
# Status (dashboard / debug)
# ─────────────────────────────────────────────────────────────────

def status() -> dict:
    """Return current ladder state for read-only consumers."""
    return _load_state()
