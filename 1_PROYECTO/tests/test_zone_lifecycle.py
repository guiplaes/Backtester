"""Tests for zone_lifecycle — touch, rejection, clean break, stale, reactivation."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from zone_lifecycle import (
    INVALIDATION_REASON_CLEAN_BREAK,
    REJECTION_PATTERN_ENGULFING,
    REJECTION_PATTERN_PIN_BAR,
    is_engulfing,
    is_pin_bar,
    tick,
)
from zone_store import (
    ZONE_STATUS_ACTIVE,
    ZONE_STATUS_INVALIDATED,
    ZONE_STATUS_STALE,
    ZONE_STRENGTH_STRONG,
    ZONE_TYPE_RESISTANCE,
    ZONE_TYPE_SUPPORT,
    build_zone,
    read_state,
    write_state,
)


# ── Helpers ──

def _bar(open_, high, low, close, volume=100):
    return {"open": open_, "high": high, "low": low, "close": close, "volume": volume}


def _avg_bars(close_price=4800.0, vol=100, n=20):
    """Helper: n flat bars centered around close_price to establish avg volume."""
    return [_bar(close_price - 0.5, close_price + 0.5, close_price - 0.5, close_price, vol) for _ in range(n)]


DEFAULT_CFG = {
    "touch_dist_usd": 0.5,
    "breakout_volume_ratio": 1.5,
    "breakout_close_distance_usd": 1.0,
    "stale_hours": 8,
}


class TestPinBar(unittest.TestCase):
    def test_bullish_pin_detected(self):
        # Long lower wick, small body at top
        b = _bar(open_=4800, high=4801, low=4790, close=4800.7)
        self.assertTrue(is_pin_bar(b, "BUY"))

    def test_bearish_pin_detected(self):
        b = _bar(open_=4800, high=4810, low=4799, close=4799.3)
        self.assertTrue(is_pin_bar(b, "SELL"))

    def test_big_body_rejects_pin(self):
        b = _bar(open_=4790, high=4800, low=4789, close=4799)
        self.assertFalse(is_pin_bar(b, "BUY"))

    def test_wrong_direction_rejects(self):
        b = _bar(open_=4800, high=4801, low=4790, close=4800.7)
        self.assertFalse(is_pin_bar(b, "SELL"))


class TestEngulfing(unittest.TestCase):
    def test_bullish_engulfing(self):
        prev = _bar(open_=4800, high=4801, low=4795, close=4796)
        cur = _bar(open_=4795, high=4806, low=4794, close=4805)
        self.assertTrue(is_engulfing(prev, cur, "BUY"))

    def test_bearish_engulfing(self):
        prev = _bar(open_=4795, high=4800, low=4794, close=4799)
        cur = _bar(open_=4800, high=4801, low=4790, close=4791)
        self.assertTrue(is_engulfing(prev, cur, "SELL"))

    def test_no_engulfing_when_same_direction(self):
        prev = _bar(open_=4795, high=4798, low=4794, close=4797)
        cur = _bar(open_=4797, high=4800, low=4796, close=4799)
        self.assertFalse(is_engulfing(prev, cur, "BUY"))


class TestTick(unittest.TestCase):
    def _setup_state(self, common_dir, zones):
        write_state(common_dir, {"bias": "NEUTRAL", "context": "", "zones": zones})

    def test_no_bars_returns_zeros(self):
        with tempfile.TemporaryDirectory() as d:
            z = build_zone(4800, ZONE_TYPE_SUPPORT, ZONE_STRENGTH_STRONG, "BUY")
            self._setup_state(d, [z])
            r = tick(d, [], DEFAULT_CFG)
            self.assertEqual(r, {"touched": 0, "invalidated": 0, "stale": 0, "rejected": 0})

    def test_touch_increments_counter(self):
        with tempfile.TemporaryDirectory() as d:
            z = build_zone(4800, ZONE_TYPE_SUPPORT, ZONE_STRENGTH_STRONG, "BUY")
            self._setup_state(d, [z])
            # Normal bar that dips into support without breaking
            bars = _avg_bars(4802, vol=100) + [_bar(4802, 4803, 4799.8, 4802, volume=100)]
            r = tick(d, bars, DEFAULT_CFG)
            self.assertEqual(r["touched"], 1)
            reloaded = read_state(d)
            self.assertEqual(reloaded["zones"][0]["touches"], 1)

    def test_rejection_on_touch_with_pin_bar(self):
        with tempfile.TemporaryDirectory() as d:
            z = build_zone(4800, ZONE_TYPE_SUPPORT, ZONE_STRENGTH_STRONG, "BUY")
            self._setup_state(d, [z])
            # Pin bar bounces off support
            bars = _avg_bars(4802, vol=100) + [_bar(open_=4801, high=4802, low=4799.5, close=4801.7, volume=150)]
            r = tick(d, bars, DEFAULT_CFG)
            self.assertEqual(r["touched"], 1)
            self.assertEqual(r["rejected"], 1)
            reloaded = read_state(d)
            self.assertEqual(reloaded["zones"][0]["rejections"], 1)

    def test_clean_break_invalidates_support(self):
        with tempfile.TemporaryDirectory() as d:
            z = build_zone(4800, ZONE_TYPE_SUPPORT, ZONE_STRENGTH_STRONG, "BUY")
            self._setup_state(d, [z])
            bars = _avg_bars(4802, vol=100) + [_bar(open_=4800, high=4800.5, low=4798, close=4798.5, volume=200)]
            r = tick(d, bars, DEFAULT_CFG)
            self.assertEqual(r["invalidated"], 1)
            reloaded = read_state(d)
            self.assertEqual(reloaded["zones"][0]["status"], ZONE_STATUS_INVALIDATED)
            self.assertEqual(reloaded["zones"][0]["invalidated_reason"], INVALIDATION_REASON_CLEAN_BREAK)

    def test_clean_break_invalidates_resistance(self):
        with tempfile.TemporaryDirectory() as d:
            z = build_zone(4800, ZONE_TYPE_RESISTANCE, ZONE_STRENGTH_STRONG, "SELL")
            self._setup_state(d, [z])
            bars = _avg_bars(4798, vol=100) + [_bar(open_=4800, high=4802, low=4799.5, close=4801.5, volume=200)]
            r = tick(d, bars, DEFAULT_CFG)
            self.assertEqual(r["invalidated"], 1)

    def test_break_requires_volume(self):
        with tempfile.TemporaryDirectory() as d:
            z = build_zone(4800, ZONE_TYPE_SUPPORT, ZONE_STRENGTH_STRONG, "BUY")
            self._setup_state(d, [z])
            # Weak volume: break rejected, counts as touch (since low penetrated)
            bars = _avg_bars(4802, vol=100) + [_bar(open_=4800, high=4800.5, low=4798, close=4798.5, volume=50)]
            r = tick(d, bars, DEFAULT_CFG)
            self.assertEqual(r["invalidated"], 0)
            self.assertEqual(r["touched"], 1)

    def test_stale_after_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            z = build_zone(4800, ZONE_TYPE_SUPPORT, ZONE_STRENGTH_STRONG, "BUY")
            # Backdate
            old = datetime.now(timezone.utc) - timedelta(hours=10)
            z["last_validated_at"] = old.isoformat()
            self._setup_state(d, [z])
            # Bar that does NOT touch the zone
            bars = _avg_bars(4900, vol=100) + [_bar(4900, 4901, 4899, 4900.5, volume=100)]
            r = tick(d, bars, DEFAULT_CFG, now=datetime.now(timezone.utc))
            self.assertEqual(r["stale"], 1)
            reloaded = read_state(d)
            self.assertEqual(reloaded["zones"][0]["status"], ZONE_STATUS_STALE)

    def test_touch_reactivates_stale(self):
        with tempfile.TemporaryDirectory() as d:
            z = build_zone(4800, ZONE_TYPE_SUPPORT, ZONE_STRENGTH_STRONG, "BUY")
            z["status"] = ZONE_STATUS_STALE
            self._setup_state(d, [z])
            bars = _avg_bars(4802, vol=100) + [_bar(4802, 4803, 4799.8, 4802, volume=100)]
            tick(d, bars, DEFAULT_CFG)
            reloaded = read_state(d)
            self.assertEqual(reloaded["zones"][0]["status"], ZONE_STATUS_ACTIVE)

    def test_invalidated_zone_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            z = build_zone(4800, ZONE_TYPE_SUPPORT, ZONE_STRENGTH_STRONG, "BUY")
            z["status"] = ZONE_STATUS_INVALIDATED
            z["invalidated_at"] = datetime.now(timezone.utc).isoformat()
            z["invalidated_reason"] = "manual"
            self._setup_state(d, [z])
            bars = _avg_bars(4802, vol=100) + [_bar(4802, 4803, 4799.8, 4802, volume=100)]
            r = tick(d, bars, DEFAULT_CFG)
            self.assertEqual(r["touched"], 0)
            reloaded = read_state(d)
            self.assertEqual(reloaded["zones"][0]["status"], ZONE_STATUS_INVALIDATED)

    def test_strength_never_modified(self):
        with tempfile.TemporaryDirectory() as d:
            z = build_zone(4800, ZONE_TYPE_SUPPORT, ZONE_STRENGTH_STRONG, "BUY")
            self._setup_state(d, [z])
            bars = _avg_bars(4802, vol=100) + [_bar(4802, 4803, 4799.8, 4802, volume=100)]
            tick(d, bars, DEFAULT_CFG)
            reloaded = read_state(d)
            self.assertEqual(reloaded["zones"][0]["strength"], ZONE_STRENGTH_STRONG)


if __name__ == "__main__":
    unittest.main()
