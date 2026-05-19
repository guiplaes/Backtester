"""Trade lifecycle finite state machine.

Problem this solves
───────────────────
Prior to v3.3 the trade "state" was a bag of flags on brain_signal_state.json:
  active, direction, entry_price, breakeven_set, closing, ...

Flags-as-state is error-prone:
  - Race conditions (two TG messages arriving while OPENING)
  - Silent inconsistencies ("active=false, but positions still open")
  - Ambiguous semantics (is closing=True during a partial, or only full close?)

This module introduces an explicit FSM:

    IDLE ──open──▶ OPENING ──filled──▶ OPEN ──event──▶ MANAGING
                                         ▲               │
                                         └───idle────────┘
                                         │
                                         └──closing flag──▶ CLOSING ──closed──▶ CLOSED ──reset──▶ IDLE

Each trade gets a UUID `trade_id` at OPENING that persists through its life.
State transitions are validated: invalid attempts raise InvalidTransitionError,
caller must handle. No silent mutation.

Backwards compat
────────────────
This module is ADDITIVE. signal_state.py continues to have the legacy fields
(active, direction, etc.) — the FSM adds `state` and `trade_id` on top. Old
call sites keep working; new code uses FSM transitions for safety.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class TradeState(str, Enum):
    """The 6 states a trade can be in. String-valued so JSON serializable."""
    IDLE = "IDLE"              # no trade, system monitoring
    OPENING = "OPENING"        # order sent, awaiting fill
    OPEN = "OPEN"              # filled, base position exists, nothing else happening
    MANAGING = "MANAGING"      # averaging or partial in progress (transient; back to OPEN after)
    CLOSING = "CLOSING"        # TG "cerramos" received; only partials/waits allowed
    CLOSED = "CLOSED"          # all positions closed; terminal before RESET


# Valid transitions. Any attempt not in this map → raise.
_TRANSITIONS: dict[TradeState, set[TradeState]] = {
    TradeState.IDLE: {TradeState.OPENING},
    TradeState.OPENING: {TradeState.OPEN, TradeState.CLOSED},  # open succeeds, or aborted
    TradeState.OPEN: {TradeState.MANAGING, TradeState.CLOSING, TradeState.CLOSED},
    TradeState.MANAGING: {TradeState.OPEN, TradeState.CLOSING, TradeState.CLOSED},
    TradeState.CLOSING: {TradeState.CLOSED},
    TradeState.CLOSED: {TradeState.IDLE},  # reset
}


class InvalidTransitionError(ValueError):
    """Raised when an attempted state transition is not valid."""


def can_transition(current: TradeState, target: TradeState) -> bool:
    return target in _TRANSITIONS.get(current, set())


def new_trade_id() -> str:
    """Generate a fresh trade_id. Short UUID prefix for readability."""
    return "t_" + uuid.uuid4().hex[:12]


@dataclass
class TradeFSMSnapshot:
    """Serializable snapshot — persisted to brain_signal_state.json as
    extra fields alongside the legacy flags."""
    state: TradeState = TradeState.IDLE
    trade_id: Optional[str] = None
    opened_at_iso: Optional[str] = None
    last_transition_iso: Optional[str] = None
    last_transition_from: Optional[str] = None
    last_transition_to: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "trade_id": self.trade_id,
            "opened_at_iso": self.opened_at_iso,
            "last_transition_iso": self.last_transition_iso,
            "last_transition_from": self.last_transition_from,
            "last_transition_to": self.last_transition_to,
        }

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "TradeFSMSnapshot":
        if not d or not isinstance(d, dict):
            return cls()
        try:
            st = TradeState(d.get("state", TradeState.IDLE.value))
        except ValueError:
            st = TradeState.IDLE
        return cls(
            state=st,
            trade_id=d.get("trade_id"),
            opened_at_iso=d.get("opened_at_iso"),
            last_transition_iso=d.get("last_transition_iso"),
            last_transition_from=d.get("last_transition_from"),
            last_transition_to=d.get("last_transition_to"),
        )


class TradeFSM:
    """Facade over a TradeFSMSnapshot with transition validation.

    Usage:
        fsm = TradeFSM.from_dict(persisted_data)
        fsm.transition(TradeState.OPENING)           # raises if invalid
        fsm.on_open_filled()                         # helper
        fsm.on_closing_requested()
        persist(fsm.to_dict())
    """

    def __init__(self, snap: Optional[TradeFSMSnapshot] = None):
        self._snap = snap or TradeFSMSnapshot()

    # ── accessors ──
    @property
    def state(self) -> TradeState:
        return self._snap.state

    @property
    def trade_id(self) -> Optional[str]:
        return self._snap.trade_id

    @property
    def is_active(self) -> bool:
        return self._snap.state not in (TradeState.IDLE, TradeState.CLOSED)

    def to_dict(self) -> dict:
        return self._snap.to_dict()

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "TradeFSM":
        return cls(TradeFSMSnapshot.from_dict(d))

    # ── transitions ──
    def transition(self, target: TradeState, now_iso: Optional[str] = None) -> None:
        """Move to `target` state or raise InvalidTransitionError."""
        if not can_transition(self._snap.state, target):
            raise InvalidTransitionError(
                f"{self._snap.state.value} → {target.value} not allowed "
                f"(valid: {sorted(s.value for s in _TRANSITIONS.get(self._snap.state, set()))})"
            )
        now_iso = now_iso or datetime.now(timezone.utc).isoformat()
        self._snap.last_transition_from = self._snap.state.value
        self._snap.last_transition_to = target.value
        self._snap.last_transition_iso = now_iso
        self._snap.state = target

    # ── convenience helpers (named for clarity at call sites) ──
    def on_open_requested(self, now_iso: Optional[str] = None) -> str:
        """IDLE → OPENING. Generates + assigns a new trade_id.

        Returns the new trade_id for the caller to attach to the order.
        """
        self.transition(TradeState.OPENING, now_iso=now_iso)
        self._snap.trade_id = new_trade_id()
        self._snap.opened_at_iso = now_iso or datetime.now(timezone.utc).isoformat()
        return self._snap.trade_id

    def on_open_filled(self, now_iso: Optional[str] = None) -> None:
        """OPENING → OPEN after broker confirms fill."""
        self.transition(TradeState.OPEN, now_iso=now_iso)

    def on_manage_start(self, now_iso: Optional[str] = None) -> None:
        """OPEN → MANAGING while an averaging/partial is in flight."""
        self.transition(TradeState.MANAGING, now_iso=now_iso)

    def on_manage_done(self, now_iso: Optional[str] = None) -> None:
        """MANAGING → OPEN after averaging/partial completes."""
        self.transition(TradeState.OPEN, now_iso=now_iso)

    def on_closing_requested(self, now_iso: Optional[str] = None) -> None:
        """OPEN or MANAGING → CLOSING (TG 'cerramos' received)."""
        self.transition(TradeState.CLOSING, now_iso=now_iso)

    def on_all_closed(self, now_iso: Optional[str] = None) -> None:
        """CLOSING → CLOSED (all positions confirmed closed)."""
        self.transition(TradeState.CLOSED, now_iso=now_iso)

    def on_aborted(self, now_iso: Optional[str] = None) -> None:
        """OPENING → CLOSED (open failed, no position ever filled)."""
        self.transition(TradeState.CLOSED, now_iso=now_iso)

    def reset_to_idle(self, now_iso: Optional[str] = None) -> None:
        """CLOSED → IDLE. Clears trade_id. Ready for next signal."""
        self.transition(TradeState.IDLE, now_iso=now_iso)
        self._snap.trade_id = None
        self._snap.opened_at_iso = None
