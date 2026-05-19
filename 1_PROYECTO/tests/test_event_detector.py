"""Tests for event_detector — 8 events + HEARTBEAT + contradiction rule."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

from event_detector import (
    CONTRADICTION_RULE,
    EVENT_DD_THRESHOLD_CROSSED,
    EVENT_HEARTBEAT,
    EVENT_MOMENTUM_BREAK,
    EVENT_PRICE_APPROACHING_ZONE,
    EVENT_PROFIT_CAPTURE_TRIGGER,
    EVENT_REJECTION_CANDLE_AT_ZONE,
    EVENT_SESSION_TRANSITION,
    EVENT_TG_MESSAGE,
    EVENT_TRADE_OPENED,
    EVENTS_LOG_FILE,
    EventDetector,
    _apply_contradiction_rule,
    _make_event,
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
    return {"open": o, "high": h, "low": l, "close": c, "volume": v, "time": t if t is not None else 0}


def _zone(price, ztype="SUPPORT", strength="STRONG", bounce="BUY", zid="z1"):
    return {
        "id": zid,
        "price": price,
        "type": ztype,
        "strength": strength,
        "bounce_direction": bounce,
        "status": "ACTIVE",
    }


class TestTradeOpened(unittest.TestCase):
    def test_trade_opened_on_new_trade_id(self):
        det = EventDetector(DEFAULT_CFG)
        det.tick({"trade_id": "t1", "price": 4800, "atr": 3})
        events = det.drain_pending(current_ts=1000)
        types = [e["type"] for e in events]
        self.assertIn(EVENT_TRADE_OPENED, types)

    def test_no_trade_opened_if_same_trade_id(self):
        det = EventDetector(DEFAULT_CFG)
        det.tick({"trade_id": "t1", "price": 4800, "atr": 3})
        det.drain_pending(current_ts=1000)
        det.mark_executor_called(1000)
        det.tick({"trade_id": "t1", "price": 4800, "atr": 3})
        events = [e for e in det.drain_pending(current_ts=1001) if e["type"] == EVENT_TRADE_OPENED]
        self.assertEqual(events, [])


class TestPriceApproachingZone(unittest.TestCase):
    def test_fires_when_within_half_atr(self):
        det = EventDetector(DEFAULT_CFG)
        z = _zone(4800, strength="STRONG")
        det.tick({"trade_id": "t1", "price": 4801.0, "atr": 3.0, "zones": [z]})
        events = [e for e in det.drain_pending(1000) if e["type"] == EVENT_PRICE_APPROACHING_ZONE]
        self.assertEqual(len(events), 1)

    def test_does_not_fire_for_weak_zone(self):
        det = EventDetector(DEFAULT_CFG)
        z = _zone(4800, strength="WEAK")
        det.tick({"trade_id": "t1", "price": 4801.0, "atr": 3.0, "zones": [z]})
        events = [e for e in det.drain_pending(1000) if e["type"] == EVENT_PRICE_APPROACHING_ZONE]
        self.assertEqual(len(events), 0)

    def test_debounce_no_refire_until_price_moves_away(self):
        det = EventDetector(DEFAULT_CFG)
        z = _zone(4800, strength="STRONG")
        det.tick({"trade_id": "t1", "price": 4801.0, "atr": 3.0, "zones": [z]})
        det.drain_pending(1000)
        det.mark_executor_called(1000)
        # Still near — no refire
        det.tick({"trade_id": "t1", "price": 4801.2, "atr": 3.0, "zones": [z]})
        events = [e for e in det.drain_pending(1001) if e["type"] == EVENT_PRICE_APPROACHING_ZONE]
        self.assertEqual(len(events), 0)

    def test_refires_after_reset_distance(self):
        det = EventDetector(DEFAULT_CFG)
        z = _zone(4800, strength="STRONG")
        det.tick({"trade_id": "t1", "price": 4801.0, "atr": 3.0, "zones": [z]})
        det.drain_pending(1000)
        # Move far: 4 away with ATR 3 → > 1.0 * ATR
        det.tick({"trade_id": "t1", "price": 4804.0, "atr": 3.0, "zones": [z]})
        det.drain_pending(1001)
        # Come back
        det.tick({"trade_id": "t1", "price": 4801.2, "atr": 3.0, "zones": [z]})
        events = [e for e in det.drain_pending(1002) if e["type"] == EVENT_PRICE_APPROACHING_ZONE]
        self.assertEqual(len(events), 1)


class TestRejectionCandle(unittest.TestCase):
    def test_pin_bar_at_support_emits(self):
        det = EventDetector(DEFAULT_CFG)
        z = _zone(4800, ztype="SUPPORT", bounce="BUY")
        # Average volume bars
        prior = [_bar(4805, 4806, 4803, 4805, v=100, t=1000 + i) for i in range(5)]
        pin = _bar(o=4801, h=4802, l=4799.5, c=4801.7, v=150, t=2000)  # closed bar
        live = _bar(o=4801.7, h=4802.3, l=4801.5, c=4802, v=30, t=2001)  # live bar after
        det.tick({
            "trade_id": "t1",
            "price": 4802.0,
            "atr": 3.0,
            "zones": [z],
            "bars_m5": prior + [pin, live],  # pin is bars[-2] = CLOSED
            "touch_dist_usd": 0.5,
        })
        events = [e for e in det.drain_pending(3000) if e["type"] == EVENT_REJECTION_CANDLE_AT_ZONE]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["details"]["pattern"], "pin_bar")

    def test_not_emits_twice_for_same_bar(self):
        det = EventDetector(DEFAULT_CFG)
        z = _zone(4800, ztype="SUPPORT", bounce="BUY")
        prior = [_bar(4805, 4806, 4803, 4805, v=100, t=1000 + i) for i in range(5)]
        pin = _bar(o=4801, h=4802, l=4799.5, c=4801.7, v=150, t=2000)
        live = _bar(o=4801.7, h=4802, l=4801.5, c=4801.8, v=30, t=2001)
        ctx = {
            "trade_id": "t1",
            "price": 4801.8,
            "atr": 3.0,
            "zones": [z],
            "bars_m5": prior + [pin, live],
            "touch_dist_usd": 0.5,
        }
        det.tick(ctx)
        det.drain_pending(3000)
        det.tick(ctx)
        events = [e for e in det.drain_pending(3001) if e["type"] == EVENT_REJECTION_CANDLE_AT_ZONE]
        self.assertEqual(len(events), 0)


class TestMomentumBreak(unittest.TestCase):
    def test_fires_against_buy_direction(self):
        det = EventDetector(DEFAULT_CFG)
        # 3 bearish bars with rising volume against a BUY trade
        bars = [
            _bar(4805, 4806, 4803, 4805, v=100, t=1000),
            _bar(4805, 4806, 4801, 4801.5, v=120, t=1060),
            _bar(4801, 4802, 4797, 4797.5, v=140, t=1120),
            _bar(4797, 4798, 4793, 4793.5, v=160, t=1180),
        ]
        det.tick({"trade_id": "t1", "signal": {"direction": "BUY"}, "price": 4793.5, "atr": 3.0, "bars_m5": bars})
        events = [e for e in det.drain_pending(2000) if e["type"] == EVENT_MOMENTUM_BREAK]
        self.assertEqual(len(events), 1)

    def test_no_fire_if_volume_not_rising(self):
        det = EventDetector(DEFAULT_CFG)
        bars = [
            _bar(4805, 4806, 4803, 4805, v=100, t=1000),
            _bar(4805, 4806, 4801, 4801.5, v=160, t=1060),
            _bar(4801, 4802, 4797, 4797.5, v=120, t=1120),
            _bar(4797, 4798, 4793, 4793.5, v=100, t=1180),
        ]
        det.tick({"trade_id": "t1", "signal": {"direction": "BUY"}, "price": 4793.5, "atr": 3.0, "bars_m5": bars})
        events = [e for e in det.drain_pending(2000) if e["type"] == EVENT_MOMENTUM_BREAK]
        self.assertEqual(len(events), 0)

    def test_dedupe_per_bar(self):
        det = EventDetector(DEFAULT_CFG)
        bars = [
            _bar(4805, 4806, 4803, 4805, v=100, t=1000),
            _bar(4805, 4806, 4801, 4801.5, v=120, t=1060),
            _bar(4801, 4802, 4797, 4797.5, v=140, t=1120),
            _bar(4797, 4798, 4793, 4793.5, v=160, t=1180),
        ]
        ctx = {"trade_id": "t1", "signal": {"direction": "BUY"}, "price": 4793.5, "atr": 3.0, "bars_m5": bars}
        det.tick(ctx)
        det.drain_pending(2000)
        det.tick(ctx)
        events = [e for e in det.drain_pending(2001) if e["type"] == EVENT_MOMENTUM_BREAK]
        self.assertEqual(len(events), 0)


class TestDDThreshold(unittest.TestCase):
    def test_fires_first_threshold(self):
        det = EventDetector(DEFAULT_CFG)
        det.tick({"trade_id": "t1", "price": 4800, "atr": 3, "account": {"balance": 50000, "dd_pct": 1.6}})
        events = [e for e in det.drain_pending(1000) if e["type"] == EVENT_DD_THRESHOLD_CROSSED]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["details"]["threshold_pct"], 1.5)

    def test_fires_once_per_threshold(self):
        det = EventDetector(DEFAULT_CFG)
        det.tick({"trade_id": "t1", "price": 4800, "atr": 3, "account": {"balance": 50000, "dd_pct": 1.7}})
        det.drain_pending(1000)
        det.tick({"trade_id": "t1", "price": 4800, "atr": 3, "account": {"balance": 50000, "dd_pct": 1.8}})
        events = [e for e in det.drain_pending(1001) if e["type"] == EVENT_DD_THRESHOLD_CROSSED]
        self.assertEqual(len(events), 0)

    def test_fires_higher_threshold_too(self):
        det = EventDetector(DEFAULT_CFG)
        det.tick({"trade_id": "t1", "price": 4800, "atr": 3, "account": {"balance": 50000, "dd_pct": 2.6}})
        events = [e for e in det.drain_pending(1000) if e["type"] == EVENT_DD_THRESHOLD_CROSSED]
        self.assertEqual(len(events), 2)


class TestProfitCapture(unittest.TestCase):
    def test_fires_when_profit_reaches_pct(self):
        det = EventDetector(DEFAULT_CFG)
        det.tick({"trade_id": "t1", "price": 4800, "atr": 3, "account": {"balance": 60000, "floating_profit": 500}})
        events = [e for e in det.drain_pending(1000) if e["type"] == EVENT_PROFIT_CAPTURE_TRIGGER]
        self.assertEqual(len(events), 1)

    def test_fires_only_once_per_trade(self):
        det = EventDetector(DEFAULT_CFG)
        det.tick({"trade_id": "t1", "price": 4800, "atr": 3, "account": {"balance": 60000, "floating_profit": 500}})
        det.drain_pending(1000)
        det.tick({"trade_id": "t1", "price": 4800, "atr": 3, "account": {"balance": 60000, "floating_profit": 800}})
        events = [e for e in det.drain_pending(1001) if e["type"] == EVENT_PROFIT_CAPTURE_TRIGGER]
        self.assertEqual(len(events), 0)

    def test_resets_on_new_trade(self):
        det = EventDetector(DEFAULT_CFG)
        det.tick({"trade_id": "t1", "price": 4800, "atr": 3, "account": {"balance": 60000, "floating_profit": 500}})
        det.drain_pending(1000)
        det.reset_for_new_trade("t1")
        det.tick({"trade_id": "t2", "price": 4800, "atr": 3, "account": {"balance": 60000, "floating_profit": 500}})
        events = [e for e in det.drain_pending(1001) if e["type"] == EVENT_PROFIT_CAPTURE_TRIGGER]
        self.assertEqual(len(events), 1)


class TestTgMessage(unittest.TestCase):
    def test_enqueued(self):
        det = EventDetector(DEFAULT_CFG)
        det.tick({
            "trade_id": "t1",
            "price": 4800,
            "atr": 3,
            "tg_messages": [{"text": "buena entrada", "channel": "TrueTrading", "id": 1}],
        })
        events = [e for e in det.drain_pending(1000) if e["type"] == EVENT_TG_MESSAGE]
        self.assertEqual(len(events), 1)


class TestSessionTransition(unittest.TestCase):
    def test_fires_once_per_day(self):
        det = EventDetector(DEFAULT_CFG)
        # 08:00 UTC > London open 07:00
        t = datetime(2026, 4, 18, 8, 0, tzinfo=timezone.utc)
        det.tick({"trade_id": "t1", "price": 4800, "atr": 3, "now": t})
        first = [e for e in det.drain_pending(1000) if e["type"] == EVENT_SESSION_TRANSITION]
        self.assertEqual(len(first), 1)
        # Same day again → no refire
        det.tick({"trade_id": "t1", "price": 4800, "atr": 3, "now": t})
        second = [e for e in det.drain_pending(1001) if e["type"] == EVENT_SESSION_TRANSITION]
        self.assertEqual(len(second), 0)

    def test_all_three_transitions_fire_through_day(self):
        det = EventDetector(DEFAULT_CFG)
        for hour in (8, 14, 22):
            t = datetime(2026, 4, 18, hour, 0, tzinfo=timezone.utc)
            det.tick({"trade_id": "t1", "price": 4800, "atr": 3, "now": t})
        events = [e for e in det.drain_pending(1000) if e["type"] == EVENT_SESSION_TRANSITION]
        self.assertEqual(len(events), 3)
        names = {e["details"]["transition"] for e in events}
        self.assertSetEqual(names, {"london_open", "ny_open", "ny_close"})


class TestHeartbeat(unittest.TestCase):
    def test_emitted_when_executor_silent(self):
        det = EventDetector(DEFAULT_CFG)
        # Register a trade first — heartbeat only fires with an active trade
        det.tick({"trade_id": "t1", "price": 4800, "atr": 3})
        det.drain_pending(current_ts=0.0)  # consume TRADE_OPENED
        det.mark_executor_called(0.0)
        # No more events, no Executor called → heartbeat after 300s
        events = det.drain_pending(current_ts=400.0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], EVENT_HEARTBEAT)

    def test_no_heartbeat_without_open_trade(self):
        det = EventDetector(DEFAULT_CFG)
        events = det.drain_pending(current_ts=10000.0)
        self.assertEqual(events, [])

    def test_suppressed_when_executor_was_called_recently(self):
        det = EventDetector(DEFAULT_CFG)
        det.tick({"trade_id": "t1", "price": 4800, "atr": 3})
        det.drain_pending(current_ts=0.0)
        det.mark_executor_called(100.0)
        events = det.drain_pending(current_ts=200.0)
        self.assertEqual(events, [])

    def test_heartbeat_never_grouped_with_real_events(self):
        det = EventDetector(DEFAULT_CFG)
        # Enqueue real event
        det.tick({"trade_id": "t1", "price": 4800, "atr": 3, "account": {"balance": 50000, "dd_pct": 1.6}})
        events = det.drain_pending(current_ts=1000.0)
        types = [e["type"] for e in events]
        self.assertNotIn(EVENT_HEARTBEAT, types)


class TestContradictionRule(unittest.TestCase):
    def test_constant_exists(self):
        self.assertEqual(CONTRADICTION_RULE, "most_recent_event_is_principal_others_are_context")

    def test_no_reorder_without_contradiction(self):
        a = _make_event(EVENT_PRICE_APPROACHING_ZONE, ts=1.0)
        b = _make_event(EVENT_TG_MESSAGE, ts=2.0)
        out = _apply_contradiction_rule([a, b])
        self.assertEqual(out, [a, b])

    def test_reorder_with_contradiction_most_recent_first(self):
        older_rejection = _make_event(EVENT_REJECTION_CANDLE_AT_ZONE, ts=1.0)
        newer_momentum = _make_event(EVENT_MOMENTUM_BREAK, ts=2.0)
        out = _apply_contradiction_rule([older_rejection, newer_momentum])
        self.assertEqual(out[0]["type"], EVENT_MOMENTUM_BREAK)
        self.assertEqual(out[1]["type"], EVENT_REJECTION_CANDLE_AT_ZONE)


class TestEventLog(unittest.TestCase):
    def test_events_append_to_log(self):
        with tempfile.TemporaryDirectory() as d:
            det = EventDetector(DEFAULT_CFG, common_dir=d)
            det.tick({"trade_id": "t1", "price": 4800, "atr": 3, "account": {"balance": 50000, "dd_pct": 1.6}})
            path = os.path.join(d, EVENTS_LOG_FILE)
            self.assertTrue(os.path.exists(path))
            with open(path) as f:
                rows = [json.loads(l) for l in f if l.strip()]
            types = [r["type"] for r in rows]
            self.assertIn(EVENT_TRADE_OPENED, types)
            self.assertIn(EVENT_DD_THRESHOLD_CROSSED, types)
            # Initial enqueue → invoked_executor=False
            for r in rows:
                self.assertFalse(r["invoked_executor"])


if __name__ == "__main__":
    unittest.main()
