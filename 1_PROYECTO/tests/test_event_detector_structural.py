"""Tests for v3.3 structural profit-capture events."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from event_detector import (
    EVENT_LIQUIDITY_SWEPT,
    EVENT_REACHED_CONTRARY_ZONE,
    EVENT_STRUCTURE_BROKEN_AGAINST,
    EventDetector,
)


DEFAULT_CFG = {
    "cooldown_seconds": 60,
    "heartbeat_interval_seconds": 300,
    "approaching_zone_atr_multiplier": 0.5,
    "approaching_zone_reset_atr_multiplier": 1.0,
    "approaching_zone_min_strength": "MODERATE",
    "rejection_volume_min_ratio": 1.0,
    "momentum_break_bars": 3,
    "momentum_break_volume_rising": True,
    "dd_thresholds_pct": [1.5, 2.5],
    "profit_capture_trigger_pct": 0.8,
    "session_london_open_utc": "07:00",
    "session_ny_open_utc": "13:00",
    "session_ny_close_utc": "21:00",
}


def _bar(o, h, l, c, v=100, t=None):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v, "time": t or 0}


def _zone(price, ztype="SUPPORT", strength="STRONG", bounce="BUY", zid="z1"):
    return {
        "id": zid,
        "price": price,
        "type": ztype,
        "strength": strength,
        "bounce_direction": bounce,
        "status": "ACTIVE",
    }


def _base_context(trade_id="BUY_4810", direction="BUY", price=4830,
                  atr=2.3, balance=60000, floating=100, zones=None, bars=None,
                  market_state=None):
    """Helper: build a tick context with profit over floor by default."""
    return {
        "trade_id": trade_id,
        "signal": {"direction": direction, "breakeven_set": False, "flag_closing": False},
        "price": price,
        "atr": atr,
        "bars_m5": bars if bars is not None else [_bar(4800, 4810, 4795, 4805)] * 25,
        "zones": zones or [],
        "account": {"balance": balance, "equity": balance + floating,
                    "dd_pct": 0.0, "floating_profit": floating},
        "market_state": market_state,
        "tg_messages": [],
    }


class TestReachedContraryZone(unittest.TestCase):
    def setUp(self):
        self.d = EventDetector(DEFAULT_CFG)

    def test_buy_trade_reaches_resistance_with_rejection(self):
        # BUY trade, contrary zone = RESISTANCE above. Price must be within
        # atr*0.3 of zone (ATR=2.3 → ~0.7$). Rejection bar is bars[-2] (closed).
        # Rejection = upper_wick > body*1.2 AND close < open (bearish).
        zones = [_zone(4835, ztype="RESISTANCE", strength="STRONG", zid="r1")]
        bars = [_bar(4800, 4810, 4795, 4805)] * 23 + [
            _bar(o=4834.9, h=4836, l=4834, c=4834.3),   # CLOSED: upper wick, bearish close
            _bar(o=4834.3, h=4835, l=4834.2, c=4834.7), # live near zone
        ]
        ctx = _base_context(price=4834.7, zones=zones, bars=bars, floating=100)
        self.d.tick(ctx)
        kinds = [e.get("type") for e in self.d.queue]
        self.assertIn(EVENT_REACHED_CONTRARY_ZONE, kinds)

    def test_no_event_when_no_rejection(self):
        zones = [_zone(4835, ztype="RESISTANCE", strength="STRONG", zid="r1")]
        bars = [_bar(4800, 4810, 4795, 4805)] * 23 + [
            _bar(o=4830, h=4836, l=4829, c=4835.5),  # CLOSED: just tagged, no rejection
            _bar(o=4835.5, h=4836, l=4834, c=4834.8),  # live
        ]
        ctx = _base_context(price=4834.8, zones=zones, bars=bars, floating=100)
        self.d.tick(ctx)
        kinds = [e.get("type") for e in self.d.queue]
        self.assertNotIn(EVENT_REACHED_CONTRARY_ZONE, kinds)

    def test_no_event_when_profit_below_floor(self):
        zones = [_zone(4835, ztype="RESISTANCE", strength="STRONG", zid="r1")]
        bars = [_bar(4800, 4810, 4795, 4805)] * 23 + [
            _bar(o=4830, h=4836, l=4828, c=4829),
            _bar(o=4829, h=4830, l=4828, c=4829.5),
        ]
        ctx = _base_context(price=4829.5, zones=zones, bars=bars, floating=10)
        self.d.tick(ctx)
        kinds = [e.get("type") for e in self.d.queue]
        self.assertNotIn(EVENT_REACHED_CONTRARY_ZONE, kinds)

    def test_no_event_when_zone_weak(self):
        zones = [_zone(4835, ztype="RESISTANCE", strength="WEAK", zid="r1")]
        bars = [_bar(4800, 4810, 4795, 4805)] * 23 + [
            _bar(o=4830, h=4836, l=4828, c=4829),
            _bar(o=4829, h=4830, l=4828, c=4829.5),
        ]
        ctx = _base_context(price=4829.5, zones=zones, bars=bars, floating=100)
        self.d.tick(ctx)
        kinds = [e.get("type") for e in self.d.queue]
        self.assertNotIn(EVENT_REACHED_CONTRARY_ZONE, kinds)

    def test_sell_trade_reaches_support_with_rejection(self):
        # SELL: rejection = lower_wick > body*1.2 AND close > open (bullish).
        zones = [_zone(4785, ztype="SUPPORT", strength="MODERATE", zid="s1")]
        bars = [_bar(4810, 4815, 4805, 4810)] * 23 + [
            _bar(o=4785.1, h=4786, l=4784, c=4785.7),   # CLOSED: lower wick, bullish close
            _bar(o=4785.7, h=4786, l=4785.3, c=4785.4), # live near zone
        ]
        ctx = _base_context(trade_id="SELL_4810", direction="SELL",
                             price=4785.4, zones=zones, bars=bars, floating=100)
        self.d.tick(ctx)
        kinds = [e.get("type") for e in self.d.queue]
        self.assertIn(EVENT_REACHED_CONTRARY_ZONE, kinds)

    def test_cooldown_prevents_refire(self):
        zones = [_zone(4835, ztype="RESISTANCE", strength="STRONG", zid="r1")]
        bars = [_bar(4800, 4810, 4795, 4805)] * 23 + [
            _bar(o=4830, h=4836, l=4828, c=4829),
            _bar(o=4829, h=4830, l=4828, c=4829.5),
        ]
        ctx = _base_context(price=4829.5, zones=zones, bars=bars, floating=100)
        self.d.tick(ctx)
        q1 = [e.get("type") for e in self.d.queue].count(EVENT_REACHED_CONTRARY_ZONE)
        self.d.tick(ctx)
        q2 = [e.get("type") for e in self.d.queue].count(EVENT_REACHED_CONTRARY_ZONE)
        self.assertEqual(q1, q2)


class TestStructureBrokenAgainst(unittest.TestCase):
    def setUp(self):
        self.d = EventDetector(DEFAULT_CFG)

    def test_buy_trade_bearish_bos_with_volume(self):
        # Volume check uses CLOSED bar (bars[-2]); append a live bar after.
        bars = [_bar(4800, 4810, 4795, 4805, v=100)] * 23 + [
            _bar(4805, 4806, 4795, 4796, v=200),  # CLOSED: high volume
            _bar(4796, 4797, 4795, 4796, v=30),   # live bar after
        ]
        ms = {"structure": {"last_bos": {"type": "bearish", "price": 4798, "age_bars": 1}}}
        ctx = _base_context(bars=bars, floating=100, market_state=ms)
        self.d.tick(ctx)
        kinds = [e.get("type") for e in self.d.queue]
        self.assertIn(EVENT_STRUCTURE_BROKEN_AGAINST, kinds)

    def test_no_event_when_bos_old(self):
        bars = [_bar(4800, 4810, 4795, 4805, v=100)] * 23 + [
            _bar(4805, 4806, 4795, 4796, v=200),
            _bar(4796, 4797, 4795, 4796, v=30),
        ]
        ms = {"structure": {"last_bos": {"type": "bearish", "price": 4798, "age_bars": 10}}}
        ctx = _base_context(bars=bars, floating=100, market_state=ms)
        self.d.tick(ctx)
        self.assertNotIn(EVENT_STRUCTURE_BROKEN_AGAINST,
                         [e.get("type") for e in self.d.queue])

    def test_no_event_when_bos_aligned(self):
        bars = [_bar(4800, 4810, 4795, 4805, v=100)] * 23 + [
            _bar(4805, 4815, 4805, 4815, v=200),
            _bar(4815, 4816, 4814, 4815, v=30),
        ]
        ms = {"structure": {"last_bos": {"type": "bullish", "price": 4812, "age_bars": 1}}}
        ctx = _base_context(bars=bars, floating=100, market_state=ms)
        self.d.tick(ctx)
        self.assertNotIn(EVENT_STRUCTURE_BROKEN_AGAINST,
                         [e.get("type") for e in self.d.queue])

    def test_no_event_when_volume_low(self):
        bars = [_bar(4800, 4810, 4795, 4805, v=100)] * 23 + [
            _bar(4805, 4806, 4795, 4796, v=100),  # closed bar: NO high volume
            _bar(4796, 4797, 4795, 4796, v=30),
        ]
        ms = {"structure": {"last_bos": {"type": "bearish", "price": 4798, "age_bars": 1}}}
        ctx = _base_context(bars=bars, floating=100, market_state=ms)
        self.d.tick(ctx)
        self.assertNotIn(EVENT_STRUCTURE_BROKEN_AGAINST,
                         [e.get("type") for e in self.d.queue])


class TestLiquiditySwept(unittest.TestCase):
    def setUp(self):
        self.d = EventDetector(DEFAULT_CFG)

    def test_buy_trade_pool_above_swept(self):
        # BUY trade, pool at 4845, last bars show sweep + return
        bars = [_bar(4800, 4810, 4795, 4805)] * 22 + [
            _bar(4830, 4847, 4829, 4844),  # spike to 4847 (> 4845 pool), closed back at 4844
            _bar(4844, 4845, 4835, 4840),
            _bar(4840, 4842, 4835, 4838),
        ]
        ms = {"liquidity": {"pools_above": [4845], "pools_below": []}}
        ctx = _base_context(bars=bars, floating=100, market_state=ms)
        self.d.tick(ctx)
        kinds = [e.get("type") for e in self.d.queue]
        self.assertIn(EVENT_LIQUIDITY_SWEPT, kinds)

    def test_sell_trade_pool_below_swept(self):
        bars = [_bar(4820, 4825, 4815, 4820)] * 22 + [
            _bar(4800, 4805, 4783, 4802),  # low to 4783, closed back above 4785
            _bar(4802, 4810, 4800, 4808),
            _bar(4808, 4812, 4803, 4810),
        ]
        ms = {"liquidity": {"pools_below": [4785], "pools_above": []}}
        ctx = _base_context(trade_id="SELL_4810", direction="SELL",
                             bars=bars, floating=100, market_state=ms)
        self.d.tick(ctx)
        kinds = [e.get("type") for e in self.d.queue]
        self.assertIn(EVENT_LIQUIDITY_SWEPT, kinds)

    def test_no_event_when_no_pool(self):
        bars = [_bar(4800, 4810, 4795, 4805)] * 25
        ms = {"liquidity": {"pools_above": [], "pools_below": []}}
        ctx = _base_context(bars=bars, floating=100, market_state=ms)
        self.d.tick(ctx)
        self.assertNotIn(EVENT_LIQUIDITY_SWEPT,
                         [e.get("type") for e in self.d.queue])

    def test_no_event_when_just_touched_not_rejected(self):
        # Price went through pool but CLOSED above (no rejection)
        bars = [_bar(4800, 4810, 4795, 4805)] * 23 + [
            _bar(4830, 4848, 4829, 4847),  # high 4848, close 4847 — still above pool 4845
        ]
        ms = {"liquidity": {"pools_above": [4845], "pools_below": []}}
        ctx = _base_context(bars=bars, floating=100, market_state=ms)
        self.d.tick(ctx)
        self.assertNotIn(EVENT_LIQUIDITY_SWEPT,
                         [e.get("type") for e in self.d.queue])


class TestProfitFloor(unittest.TestCase):
    def setUp(self):
        self.d = EventDetector(DEFAULT_CFG)

    def test_floor_blocks_all_three_events(self):
        # Profit below floor → none of the 3 fire
        zones = [_zone(4835, ztype="RESISTANCE", strength="STRONG", zid="r1")]
        bars = [_bar(4800, 4810, 4795, 4805, v=100)] * 24 + [
            _bar(o=4830, h=4836, l=4828, c=4829, v=200)
        ]
        ms = {
            "structure": {"last_bos": {"type": "bearish", "price": 4798, "age_bars": 1}},
            "liquidity": {"pools_above": [4836], "pools_below": []},
        }
        ctx = _base_context(price=4834.8, zones=zones, bars=bars,
                             floating=5, market_state=ms)  # tiny profit
        self.d.tick(ctx)
        kinds = [e.get("type") for e in self.d.queue]
        self.assertNotIn(EVENT_REACHED_CONTRARY_ZONE, kinds)
        self.assertNotIn(EVENT_STRUCTURE_BROKEN_AGAINST, kinds)
        self.assertNotIn(EVENT_LIQUIDITY_SWEPT, kinds)


if __name__ == "__main__":
    unittest.main()
