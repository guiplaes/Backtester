"""Tests for validator v3.2 — multiplier-based order + soft DD 3.4%."""

from __future__ import annotations

import unittest

from validator import (
    FORCED_FALLBACK_ACTION,
    REJECT_AVERAGE_AFTER_BREAKEVEN,
    REJECT_DD_SOFT_STOP,
    REJECT_DIRECTION_MISMATCH,
    REJECT_ILLEGAL_ACTION_WHILE_CLOSING,
    REJECT_LOT_OUT_OF_RANGE,
    REJECT_MULTIPLIER_INVALID,
    check,
)


DEFAULT_CFG = {
    "lot_min": 0.01,
    "lot_max": 0.5,
    "base_lot": 0.03,
    "max_multiplier": 5,
    "dd_soft_pct": 3.4,
    "validator_adverse_atr_factor": 1.5,
}


def acc(**kw):
    """Build a minimal account dict with ATR defaulted."""
    base = {"balance": 60000, "dd_used": 0, "dd_pct": 0.0, "atr_m5": 2.0}
    base.update(kw)
    return base


class TestAllow(unittest.TestCase):
    def test_wait_always_allowed(self):
        ok, rej = check({"action": "WAIT"}, {"direction": "BUY"}, acc(), DEFAULT_CFG)
        self.assertTrue(ok)
        self.assertIsNone(rej)

    def test_alert_always_allowed(self):
        ok, rej = check({"action": "ALERT"}, {"direction": "BUY"}, acc(), DEFAULT_CFG)
        self.assertTrue(ok)

    def test_none_response_allowed(self):
        ok, rej = check(None, {"direction": "BUY"}, acc(), DEFAULT_CFG)
        self.assertTrue(ok)

    def test_valid_average_allowed(self):
        resp = {"action": "AVERAGE", "order": {"type": "BUY", "multiplier": 1}}
        ok, rej = check(resp, {"direction": "BUY", "breakeven_set": False}, acc(), DEFAULT_CFG)
        self.assertTrue(ok)
        self.assertIsNone(rej)

    def test_partial_close_during_closing_allowed(self):
        resp = {"action": "PARTIAL_CLOSE", "close_ticket": 123, "close_pct": 50}
        ok, rej = check(resp, {"direction": "BUY", "flag_closing": True}, acc(), DEFAULT_CFG)
        self.assertTrue(ok)


class TestDirectionMismatch(unittest.TestCase):
    def test_reject_opposite_direction(self):
        resp = {"action": "AVERAGE", "order": {"type": "SELL", "multiplier": 1}}
        ok, rej = check(resp, {"direction": "BUY"}, acc(), DEFAULT_CFG)
        self.assertFalse(ok)
        self.assertEqual(rej["code"], REJECT_DIRECTION_MISMATCH)
        self.assertEqual(rej["action"], FORCED_FALLBACK_ACTION)


class TestMultiplierInvalid(unittest.TestCase):
    def test_reject_zero(self):
        resp = {"action": "AVERAGE", "order": {"type": "BUY", "multiplier": 0}}
        ok, rej = check(resp, {"direction": "BUY"}, acc(), DEFAULT_CFG)
        self.assertFalse(ok)
        self.assertEqual(rej["code"], REJECT_MULTIPLIER_INVALID)

    def test_reject_over_max(self):
        resp = {"action": "AVERAGE", "order": {"type": "BUY", "multiplier": 6}}
        ok, rej = check(resp, {"direction": "BUY"}, acc(), DEFAULT_CFG)
        self.assertFalse(ok)
        self.assertEqual(rej["code"], REJECT_MULTIPLIER_INVALID)

    def test_reject_float(self):
        resp = {"action": "AVERAGE", "order": {"type": "BUY", "multiplier": 2.5}}
        ok, rej = check(resp, {"direction": "BUY"}, acc(), DEFAULT_CFG)
        self.assertFalse(ok)
        self.assertEqual(rej["code"], REJECT_MULTIPLIER_INVALID)

    def test_reject_none(self):
        resp = {"action": "AVERAGE", "order": {"type": "BUY"}}
        ok, rej = check(resp, {"direction": "BUY"}, acc(), DEFAULT_CFG)
        self.assertFalse(ok)
        self.assertEqual(rej["code"], REJECT_MULTIPLIER_INVALID)

    def test_reject_string(self):
        resp = {"action": "AVERAGE", "order": {"type": "BUY", "multiplier": "2"}}
        ok, rej = check(resp, {"direction": "BUY"}, acc(), DEFAULT_CFG)
        self.assertFalse(ok)
        self.assertEqual(rej["code"], REJECT_MULTIPLIER_INVALID)


class TestLotOutOfRange(unittest.TestCase):
    def test_reject_computed_lot_above_max(self):
        # base=0.20, mult=5 → 1.0 > 0.5 max
        cfg = dict(DEFAULT_CFG, base_lot=0.20)
        resp = {"action": "AVERAGE", "order": {"type": "BUY", "multiplier": 5}}
        ok, rej = check(resp, {"direction": "BUY"}, acc(), cfg)
        self.assertFalse(ok)
        self.assertEqual(rej["code"], REJECT_LOT_OUT_OF_RANGE)


class TestDDSoftStop(unittest.TestCase):
    def test_reject_when_projected_near_hard(self):
        # balance 50k → dd_soft_pct=3.4 → soft limit USD = 1700
        # dd_used=1550 → remaining 150 to soft. lot=0.15 (mult=5, base=0.03), ATR=2 × 1.5 = $3 adverse
        # extra_loss = 0.15 × 3 × 100 = $45 → post = 1595 → 3.19% < 3.4% → should PASS
        resp = {"action": "AVERAGE", "order": {"type": "BUY", "multiplier": 5}}
        ok, _ = check(resp, {"direction": "BUY"}, acc(balance=50000, dd_used=1550, atr_m5=2.0), DEFAULT_CFG)
        self.assertTrue(ok)

        # Same but with bigger ATR → $15 adverse × 0.15 × 100 = $225 extra → post=1775 → 3.55% > 3.4% → REJECT
        resp = {"action": "AVERAGE", "order": {"type": "BUY", "multiplier": 5}}
        ok, rej = check(resp, {"direction": "BUY"}, acc(balance=50000, dd_used=1550, atr_m5=10.0), DEFAULT_CFG)
        self.assertFalse(ok)
        self.assertEqual(rej["code"], REJECT_DD_SOFT_STOP)

    def test_ok_when_well_under_limit(self):
        resp = {"action": "AVERAGE", "order": {"type": "BUY", "multiplier": 1}}
        ok, rej = check(resp, {"direction": "BUY"}, acc(dd_used=300), DEFAULT_CFG)
        self.assertTrue(ok)

    def test_allows_operation_up_to_near_hard(self):
        # Key: user wants system to KEEP OPERATING even at ~2.5-3% DD.
        # balance 60k, dd_used=1800 (3.0%), small mult=1 lot=0.03, ATR=2 → adverse=3
        # extra=0.03*3*100=$9 → post=1809 → 3.015% < 3.4% → PASS (system stays active)
        resp = {"action": "AVERAGE", "order": {"type": "BUY", "multiplier": 1}}
        ok, _ = check(resp, {"direction": "BUY"}, acc(balance=60000, dd_used=1800, atr_m5=2.0), DEFAULT_CFG)
        self.assertTrue(ok)


class TestBreakeven(unittest.TestCase):
    def test_reject_average_after_breakeven(self):
        resp = {"action": "AVERAGE", "order": {"type": "BUY", "multiplier": 1}}
        ok, rej = check(resp, {"direction": "BUY", "breakeven_set": True}, acc(), DEFAULT_CFG)
        self.assertFalse(ok)
        self.assertEqual(rej["code"], REJECT_AVERAGE_AFTER_BREAKEVEN)

    def test_partial_close_allowed_after_breakeven(self):
        resp = {"action": "PARTIAL_CLOSE", "close_ticket": 1, "close_pct": 50}
        ok, rej = check(resp, {"direction": "BUY", "breakeven_set": True}, acc(), DEFAULT_CFG)
        self.assertTrue(ok)


class TestClosing(unittest.TestCase):
    def test_reject_average_during_closing(self):
        resp = {"action": "AVERAGE", "order": {"type": "BUY", "multiplier": 1}}
        ok, rej = check(resp, {"direction": "BUY", "flag_closing": True}, acc(), DEFAULT_CFG)
        self.assertFalse(ok)
        self.assertEqual(rej["code"], REJECT_ILLEGAL_ACTION_WHILE_CLOSING)


class TestPrecedence(unittest.TestCase):
    def test_closing_blocks_even_when_direction_ok(self):
        resp = {"action": "AVERAGE", "order": {"type": "BUY", "multiplier": 1}}
        ok, rej = check(resp, {"direction": "BUY", "flag_closing": True}, acc(), DEFAULT_CFG)
        self.assertFalse(ok)
        self.assertEqual(rej["code"], REJECT_ILLEGAL_ACTION_WHILE_CLOSING)

    def test_direction_before_multiplier_check(self):
        resp = {"action": "AVERAGE", "order": {"type": "SELL", "multiplier": 99}}
        ok, rej = check(resp, {"direction": "BUY"}, acc(), DEFAULT_CFG)
        self.assertFalse(ok)
        self.assertEqual(rej["code"], REJECT_DIRECTION_MISMATCH)


if __name__ == "__main__":
    unittest.main()
