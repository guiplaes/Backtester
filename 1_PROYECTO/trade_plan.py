#!/usr/bin/env python3
"""Trade Plan — zone-based TP assignment for aggregate trades with averaging.

Rules enforced:
  1. One TP per ticket (no volume splitting). Tickets are atomic.
  2. NEVER assign a TP that would close the ticket at a loss (buffer respected).
  3. Close WORST-positioned tickets first, keep the BEST as runner.
     · For SELL: worst = lowest entry price, best = highest.
     · For BUY:  worst = highest entry price, best = lowest.
  4. At least 1 ticket remains as runner (no TP, managed by FastEngine trailing).
  5. Re-plan on every averaging: blend shifts, tickets change, zones rechecked.
  6. Executor LLM can override plan via explicit MODIFY_TP / PARTIAL_CLOSE orders.

The caller (trader_brain) wires this into sig_state.open_signal() and averaging
paths, then sends MODIFY_TP per ticket via the EA.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

COMMON = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files")
PLAN_FILE = COMMON / "brain_trade_plan.json"

_log = logging.getLogger("brain")

# Minimum profit buffer (USD/pip on XAUUSD) to consider a TP "safely profitable".
# Prevents setting a TP so close to entry that spread+slippage would flip it negative.
TP_BUFFER_USD = 0.5

# Max TP levels to place per plan (rest becomes runners).
MAX_TP_LEVELS = 3

# Minimum distance a zone must be from blend (in ATR_M15 multiples) to be a TP candidate.
MIN_ZONE_DIST_ATR = 0.5
# Maximum distance (in R units) a zone can be from blend to be considered for TP.
MAX_ZONE_DIST_R = 3.0

# Desired % of total volume to close per zone strength (ticket granularity may differ).
STRENGTH_PCT = {"STRONG": 40, "MODERATE": 30, "WEAK": 20}


@dataclass
class TPAssignment:
    ticket: int
    entry_price: float
    volume: float
    tp_price: float          # 0.0 means no TP (runner)
    zone_price: float | None
    zone_strength: str | None
    role: str                # 'TP' | 'RUNNER'
    status: str = 'PLANNED'  # PLANNED, SENT_OK, SEND_FAIL, EXECUTED


@dataclass
class TradePlan:
    direction: str
    blend_price: float
    total_volume: float
    r_usd: float
    atr_m15: float
    assignments: list[TPAssignment] = field(default_factory=list)
    created_ts: float = field(default_factory=time.time)
    reason: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["created_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.created_ts))
        return d


def _compute_blend(tickets: list[dict]) -> tuple[float, float]:
    """Return (blend_price, total_volume) from a list of {entry_price, volume}."""
    total = 0.0
    weighted = 0.0
    for t in tickets:
        v = float(t.get('volume', 0) or 0)
        p = float(t.get('entry_price', 0) or 0)
        total += v
        weighted += v * p
    blend = (weighted / total) if total > 0 else 0.0
    return blend, total


def _is_opposite_zone(zone: dict, direction: str, blend: float) -> bool:
    """A TP zone must be on the profitable side of blend."""
    zp = float(zone.get('price', 0) or 0)
    if direction == 'SELL':
        return zp < blend
    return zp > blend


def _zone_distance_ok(zone_price: float, blend: float, atr_m15: float, r_usd: float,
                       total_volume: float) -> bool:
    """Zone must be far enough (> 0.5×ATR_M15) and not too far (within 3R of blend)."""
    dist_usd = abs(zone_price - blend)
    if dist_usd < MIN_ZONE_DIST_ATR * atr_m15:
        return False
    # Convert R (USD per full position) to price distance: R_usd / (total_volume * $100/pip for gold)
    # For XAUUSD: $1 price move × 1 lot = $100. So $1 × volume = $100 * volume.
    # dist_usd (price) × total_volume × 100 = profit if we closed here.
    profit_if_hit = dist_usd * total_volume * 100
    if r_usd > 0 and profit_if_hit > MAX_ZONE_DIST_R * r_usd:
        return False
    return True


def _can_profit(entry_price: float, tp_price: float, direction: str) -> bool:
    """True if closing this ticket at tp_price would yield at least TP_BUFFER_USD profit."""
    if direction == 'SELL':
        return tp_price < (entry_price - TP_BUFFER_USD)
    return tp_price > (entry_price + TP_BUFFER_USD)


def _build_plan_from_executor_targets(
    plan: TradePlan,
    tickets_sorted: list[dict],
    targets: list[float],
    direction: str,
) -> TradePlan:
    """Assign TPs from the Executor's explicit profit_targets list.

    The Executor reasons about TPs with full situational context (session,
    momentum, ATR percentile, structure, news). When it has spoken, we honor
    its decision instead of the geometric zone filter. Only safety net kept:
    `_can_profit()` so we never close a ticket at a loss.

    Convention from the prompt schema:
      · targets[0] = first/closest profit (conservative) → worst ticket
      · targets[-1] = last/furthest profit (range fade) → less-bad tickets
      · Best ticket stays as RUNNER when ≥2 tickets are open
    """
    blend = plan.blend_price

    # Filter targets to the profitable side of blend.
    # Accepts both `[float, float]` (legacy) and `[{price, close_pct, ...}, ...]`
    # (new ladder schema). We only consume the price here — the close_pct is
    # applied by the executor_ladder module, not by broker-side TPs.
    valid_targets: list[float] = []
    for t in targets:
        if isinstance(t, dict):
            t = t.get('price')
        try:
            tp = float(t) if t is not None else None
        except (TypeError, ValueError):
            continue
        if tp is None:
            continue
        if direction == 'SELL' and tp >= blend:
            continue
        if direction == 'BUY' and tp <= blend:
            continue
        valid_targets.append(tp)
    # Closest-first ordering matches "primer parcial al primer rebuig".
    valid_targets.sort(key=lambda tp: abs(tp - blend))

    if not valid_targets:
        # Nothing usable — caller falls back to geometric path.
        return None  # type: ignore[return-value]

    if len(tickets_sorted) > 1:
        runner = tickets_sorted[-1]
        eligible = tickets_sorted[:-1]
    else:
        runner = None
        eligible = tickets_sorted

    used_ticket_ids: set[int] = set()
    assigned: list[TPAssignment] = []

    # Greedy: each target (closest → farthest) consumes the worst
    # still-eligible ticket that can profit at that price.
    for tp_price in valid_targets[:MAX_TP_LEVELS]:
        for t in eligible:
            tid = int(t.get('ticket', 0) or 0)
            if tid in used_ticket_ids:
                continue
            entry = float(t.get('entry_price', 0) or 0)
            if not _can_profit(entry, tp_price, direction):
                continue
            assigned.append(TPAssignment(
                ticket=tid,
                entry_price=entry,
                volume=float(t.get('volume', 0) or 0),
                tp_price=tp_price,
                zone_price=tp_price,
                zone_strength='EXECUTOR',
                role='TP',
            ))
            used_ticket_ids.add(tid)
            break

    # Remaining eligible tickets — try farthest profitable target, else runner.
    single_ticket_mode = (runner is None and len(eligible) == 1)
    for t in eligible:
        tid = int(t.get('ticket', 0) or 0)
        if tid in used_ticket_ids:
            continue
        entry = float(t.get('entry_price', 0) or 0)
        best_tp = None
        for tp in reversed(valid_targets):
            if _can_profit(entry, tp, direction):
                best_tp = tp
                break
        if best_tp is not None:
            assigned.append(TPAssignment(
                ticket=tid,
                entry_price=entry,
                volume=float(t.get('volume', 0) or 0),
                tp_price=best_tp,
                zone_price=best_tp,
                zone_strength='EXECUTOR',
                role='TP',
            ))
            used_ticket_ids.add(tid)
        elif single_ticket_mode:
            assigned.append(TPAssignment(
                ticket=tid,
                entry_price=entry,
                volume=float(t.get('volume', 0) or 0),
                tp_price=None, zone_price=None, zone_strength=None,
                role='KEEP_TP',
            ))
        else:
            assigned.append(TPAssignment(
                ticket=tid,
                entry_price=entry,
                volume=float(t.get('volume', 0) or 0),
                tp_price=0.0, zone_price=None, zone_strength=None,
                role='RUNNER',
            ))

    if runner is not None:
        assigned.append(TPAssignment(
            ticket=int(runner.get('ticket', 0) or 0),
            entry_price=float(runner.get('entry_price', 0) or 0),
            volume=float(runner.get('volume', 0) or 0),
            tp_price=0.0, zone_price=None, zone_strength=None, role='RUNNER',
        ))

    plan.assignments = assigned
    plan.reason = (plan.reason + " · executor-targets") if plan.reason else "executor-targets"
    return plan


def build_plan(tickets: list[dict], zones: list[dict], direction: str,
               atr_m15: float, reason: str = "",
               executor_targets: list[float] | None = None) -> TradePlan:
    """Compute TP assignments per ticket.

    `tickets`: list of {ticket, entry_price, volume}
    `zones`:   list of active zones {price, strength, bounce_direction, ...}
    `direction`: 'BUY' or 'SELL'
    `atr_m15`: current ATR_M15 for distance gate
    `executor_targets`: optional list of TP prices set by the Executor LLM
                        when it staged this trade. When provided AND at least
                        one is profitable, they override the geometric zone
                        filter — the LLM's situational tactical plan beats
                        deterministic zone geometry. Geometric path remains
                        as the fallback for legacy/adopted trades.
    """
    blend, total_vol = _compute_blend(tickets)
    # R in USD = 1 ATR movement with full current volume (XAUUSD: $1×lot = $100)
    r_usd = atr_m15 * total_vol * 100 if (atr_m15 and total_vol) else 0.0

    plan = TradePlan(direction=direction, blend_price=blend, total_volume=total_vol,
                     r_usd=r_usd, atr_m15=atr_m15, reason=reason)

    if not tickets:
        return plan

    # 1. Sort tickets worst→best
    if direction == 'SELL':
        tickets_sorted = sorted(tickets, key=lambda t: float(t.get('entry_price', 0) or 0))
        # worst = lowest entry, best = highest
    else:  # BUY
        tickets_sorted = sorted(tickets, key=lambda t: -float(t.get('entry_price', 0) or 0))
        # worst = highest entry, best = lowest

    # 1b. Executor-driven path: if the LLM provided explicit profit_targets when
    # staging the trade, prefer them over geometric zone filtering. Falls back
    # to the geometric path silently if no target is profitable from blend.
    if executor_targets:
        result = _build_plan_from_executor_targets(
            plan, tickets_sorted, executor_targets, direction
        )
        if result is not None:
            return result

    # 2. Reserve best ticket as runner (only if >1 ticket)
    if len(tickets_sorted) > 1:
        runner = tickets_sorted[-1]
        eligible = tickets_sorted[:-1]
    else:
        runner = None
        eligible = tickets_sorted

    # 3. Filter candidate zones: opposite side, distance ok
    candidates = []
    for z in zones or []:
        if not _is_opposite_zone(z, direction, blend):
            continue
        zp = float(z.get('price', 0) or 0)
        if not _zone_distance_ok(zp, blend, atr_m15, r_usd, total_vol):
            continue
        candidates.append(z)
    # Sort by distance from blend (closest first)
    candidates.sort(key=lambda z: abs(float(z.get('price', 0) or 0) - blend))

    # 4. Greedy assignment: closest zone → worst eligible ticket that can profit
    used_ticket_ids = set()
    assigned: list[TPAssignment] = []
    for zone in candidates:
        if len(assigned) >= MAX_TP_LEVELS:
            break
        zp = float(zone.get('price', 0) or 0)
        # Find worst (first in sorted list) eligible ticket not yet used
        for t in eligible:
            tid = int(t.get('ticket', 0) or 0)
            if tid in used_ticket_ids:
                continue
            entry = float(t.get('entry_price', 0) or 0)
            if not _can_profit(entry, zp, direction):
                continue
            assigned.append(TPAssignment(
                ticket=tid,
                entry_price=entry,
                volume=float(t.get('volume', 0) or 0),
                tp_price=zp,
                zone_price=zp,
                zone_strength=zone.get('strength'),
                role='TP',
            ))
            used_ticket_ids.add(tid)
            break

    # 5. Remaining tickets (not assigned). If a ticket is the ONLY one in the
    # trade, it can't be a runner (no other ticket to trail alongside). Try to
    # assign it to the FARTHEST profitable zone so it still has a broker-level
    # TP rather than sitting naked with tp=0.
    single_ticket_mode = (runner is None and len(eligible) == 1)
    for t in eligible:
        tid = int(t.get('ticket', 0) or 0)
        if tid in used_ticket_ids:
            continue
        entry = float(t.get('entry_price', 0) or 0)
        # Try farthest zone this ticket can profit from (best reward)
        best_zone = None
        for zone in reversed(candidates):
            zp = float(zone.get('price', 0) or 0)
            if _can_profit(entry, zp, direction):
                best_zone = zone
                break
        if best_zone is not None:
            zp = float(best_zone.get('price', 0) or 0)
            assigned.append(TPAssignment(
                ticket=tid, entry_price=entry,
                volume=float(t.get('volume', 0) or 0),
                tp_price=zp, zone_price=zp,
                zone_strength=best_zone.get('strength'),
                role='TP',
            ))
            used_ticket_ids.add(tid)
        elif single_ticket_mode:
            # Single ticket + no zone reachable in profit → keep whatever TP
            # the broker already has (signal tp_price=None so apply_trade_plan
            # skips MODIFY_TP and doesn't wipe existing broker-level TP).
            assigned.append(TPAssignment(
                ticket=tid, entry_price=entry,
                volume=float(t.get('volume', 0) or 0),
                tp_price=None, zone_price=None, zone_strength=None,
                role='KEEP_TP',
            ))
        else:
            # Multi-ticket trade with spare tickets → actual runner for trailing
            assigned.append(TPAssignment(
                ticket=tid, entry_price=entry,
                volume=float(t.get('volume', 0) or 0),
                tp_price=0.0, zone_price=None, zone_strength=None,
                role='RUNNER',
            ))
    if runner is not None:
        assigned.append(TPAssignment(
            ticket=int(runner.get('ticket', 0) or 0),
            entry_price=float(runner.get('entry_price', 0) or 0),
            volume=float(runner.get('volume', 0) or 0),
            tp_price=0.0, zone_price=None, zone_strength=None, role='RUNNER',
        ))

    plan.assignments = assigned
    return plan


def save_plan(plan: TradePlan) -> None:
    try:
        PLAN_FILE.write_text(
            json.dumps(plan.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        _log.warning(f"[TRADE_PLAN] save failed: {e}")


def load_plan() -> dict | None:
    try:
        if PLAN_FILE.exists():
            return json.loads(PLAN_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def clear_plan() -> None:
    try:
        if PLAN_FILE.exists():
            PLAN_FILE.unlink()
    except Exception:
        pass


def plan_summary(plan: TradePlan) -> str:
    """One-line human summary for logging."""
    tp_parts = []
    runner_count = 0
    for a in plan.assignments:
        if a.role == 'TP':
            tp_parts.append(f"tk{a.ticket}@{a.entry_price:.1f}→TP{a.tp_price:.1f}[{a.zone_strength or '?'}]")
        else:
            runner_count += 1
    runner_str = f" · runner×{runner_count}" if runner_count else ""
    return f"{plan.direction} blend={plan.blend_price:.1f} vol={plan.total_volume:.2f} R=${plan.r_usd:.1f}: " + \
           (", ".join(tp_parts) or "NO_TPS") + runner_str
