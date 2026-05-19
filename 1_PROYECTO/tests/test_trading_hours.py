"""Tests for is_within_trading_hours gate."""
from datetime import datetime, timezone
import unittest
import trader_brain as tb


def cfg(start="06:00", end="20:00", enabled=True):
    return {"trading_hours": {"start_utc": start, "end_utc": end, "enabled": enabled}}


def at(hour, minute=0):
    return datetime(2026, 4, 20, hour, minute, tzinfo=timezone.utc)


class TestWithinHours(unittest.TestCase):
    def test_inside_window(self):
        self.assertTrue(tb.is_within_trading_hours(at(10, 0), cfg()))
        self.assertTrue(tb.is_within_trading_hours(at(6, 0), cfg()))
        self.assertTrue(tb.is_within_trading_hours(at(19, 59), cfg()))

    def test_outside_window(self):
        self.assertFalse(tb.is_within_trading_hours(at(5, 59), cfg()))
        self.assertFalse(tb.is_within_trading_hours(at(20, 0), cfg()))
        self.assertFalse(tb.is_within_trading_hours(at(3, 0), cfg()))
        self.assertFalse(tb.is_within_trading_hours(at(23, 59), cfg()))

    def test_disabled(self):
        self.assertTrue(tb.is_within_trading_hours(at(3, 0), cfg(enabled=False)))

    def test_wrap_around(self):
        # Overnight window 22:00-06:00 → inside at 02:00, outside at 10:00
        c = cfg(start="22:00", end="06:00")
        self.assertTrue(tb.is_within_trading_hours(at(2, 0), c))
        self.assertTrue(tb.is_within_trading_hours(at(23, 30), c))
        self.assertFalse(tb.is_within_trading_hours(at(10, 0), c))

    def test_malformed_config(self):
        c = {"trading_hours": {"start_utc": "not-a-time"}}
        self.assertTrue(tb.is_within_trading_hours(at(10, 0), c))  # fail-open


if __name__ == "__main__":
    unittest.main()
