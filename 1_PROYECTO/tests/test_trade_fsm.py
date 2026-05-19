"""Tests for trade_fsm — state machine transitions + UUID trade_id."""
from __future__ import annotations

import unittest
from trade_fsm import (
    TradeFSM, TradeState, InvalidTransitionError,
    can_transition, new_trade_id,
)


class TestCanTransition(unittest.TestCase):
    def test_idle_to_opening(self):
        self.assertTrue(can_transition(TradeState.IDLE, TradeState.OPENING))

    def test_idle_to_open_not_allowed(self):
        # Must go through OPENING first
        self.assertFalse(can_transition(TradeState.IDLE, TradeState.OPEN))

    def test_opening_to_open(self):
        self.assertTrue(can_transition(TradeState.OPENING, TradeState.OPEN))

    def test_opening_to_closed_abort(self):
        self.assertTrue(can_transition(TradeState.OPENING, TradeState.CLOSED))

    def test_opening_to_closing_not_allowed(self):
        self.assertFalse(can_transition(TradeState.OPENING, TradeState.CLOSING))

    def test_open_to_managing_and_back(self):
        self.assertTrue(can_transition(TradeState.OPEN, TradeState.MANAGING))
        self.assertTrue(can_transition(TradeState.MANAGING, TradeState.OPEN))

    def test_open_to_closing(self):
        self.assertTrue(can_transition(TradeState.OPEN, TradeState.CLOSING))

    def test_closing_to_closed_only(self):
        self.assertTrue(can_transition(TradeState.CLOSING, TradeState.CLOSED))
        self.assertFalse(can_transition(TradeState.CLOSING, TradeState.OPEN))

    def test_closed_to_idle(self):
        self.assertTrue(can_transition(TradeState.CLOSED, TradeState.IDLE))


class TestTradeFSM(unittest.TestCase):
    def test_starts_idle(self):
        f = TradeFSM()
        self.assertEqual(f.state, TradeState.IDLE)
        self.assertIsNone(f.trade_id)
        self.assertFalse(f.is_active)

    def test_open_flow(self):
        f = TradeFSM()
        tid = f.on_open_requested()
        self.assertEqual(f.state, TradeState.OPENING)
        self.assertIsNotNone(tid)
        self.assertTrue(tid.startswith("t_"))
        self.assertEqual(f.trade_id, tid)
        self.assertTrue(f.is_active)

        f.on_open_filled()
        self.assertEqual(f.state, TradeState.OPEN)
        self.assertEqual(f.trade_id, tid)  # preserved across transitions

    def test_manage_cycle(self):
        f = TradeFSM()
        f.on_open_requested()
        f.on_open_filled()
        f.on_manage_start()
        self.assertEqual(f.state, TradeState.MANAGING)
        f.on_manage_done()
        self.assertEqual(f.state, TradeState.OPEN)

    def test_close_flow(self):
        f = TradeFSM()
        f.on_open_requested()
        f.on_open_filled()
        f.on_closing_requested()
        self.assertEqual(f.state, TradeState.CLOSING)
        f.on_all_closed()
        self.assertEqual(f.state, TradeState.CLOSED)

    def test_reset_clears_trade_id(self):
        f = TradeFSM()
        f.on_open_requested()
        f.on_open_filled()
        f.on_closing_requested()
        f.on_all_closed()
        self.assertIsNotNone(f.trade_id)
        f.reset_to_idle()
        self.assertEqual(f.state, TradeState.IDLE)
        self.assertIsNone(f.trade_id)
        self.assertFalse(f.is_active)

    def test_invalid_transition_raises(self):
        f = TradeFSM()
        # IDLE → OPEN (skipping OPENING) not allowed
        with self.assertRaises(InvalidTransitionError):
            f.transition(TradeState.OPEN)
        # Still in IDLE after failed transition
        self.assertEqual(f.state, TradeState.IDLE)

    def test_invalid_double_open(self):
        f = TradeFSM()
        f.on_open_requested()
        with self.assertRaises(InvalidTransitionError):
            f.on_open_requested()  # Already OPENING, can't open again

    def test_aborted_open(self):
        f = TradeFSM()
        f.on_open_requested()
        f.on_aborted()  # OPENING → CLOSED
        self.assertEqual(f.state, TradeState.CLOSED)

    def test_closed_can_only_reset(self):
        f = TradeFSM()
        f.on_open_requested()
        f.on_open_filled()
        f.on_closing_requested()
        f.on_all_closed()
        # CLOSED → OPENING not allowed, must reset first
        with self.assertRaises(InvalidTransitionError):
            f.transition(TradeState.OPENING)
        f.reset_to_idle()
        # Now new trade can start
        tid2 = f.on_open_requested()
        self.assertTrue(tid2.startswith("t_"))

    def test_trade_id_unique(self):
        ids = {new_trade_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)


class TestPersistence(unittest.TestCase):
    def test_roundtrip_idle(self):
        f = TradeFSM()
        d = f.to_dict()
        f2 = TradeFSM.from_dict(d)
        self.assertEqual(f2.state, TradeState.IDLE)
        self.assertIsNone(f2.trade_id)

    def test_roundtrip_open(self):
        f = TradeFSM()
        tid = f.on_open_requested()
        f.on_open_filled()
        d = f.to_dict()
        f2 = TradeFSM.from_dict(d)
        self.assertEqual(f2.state, TradeState.OPEN)
        self.assertEqual(f2.trade_id, tid)

    def test_roundtrip_malformed_falls_back_to_idle(self):
        f = TradeFSM.from_dict({"state": "GARBAGE"})
        self.assertEqual(f.state, TradeState.IDLE)

    def test_roundtrip_none(self):
        f = TradeFSM.from_dict(None)
        self.assertEqual(f.state, TradeState.IDLE)


if __name__ == "__main__":
    unittest.main()
