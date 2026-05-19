"""Tests for broker-authoritative signal reconciliation."""

from __future__ import annotations

import os
import tempfile
import time
import unittest

import signal_state


class TestSignalStateBrokerSync(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_state_file = signal_state.STATE_FILE
        signal_state.STATE_FILE = os.path.join(self._tmp.name, "brain_signal_state.json")

    def tearDown(self):
        signal_state.STATE_FILE = self._old_state_file
        self._tmp.cleanup()

    def _new_state(self):
        return signal_state.SignalState()

    def test_reconcile_uses_broker_entry_and_balance_delta(self):
        st = self._new_state()
        st.open_signal("BUY", 4499.0, "TG", 0.06, start_balance=1000.0)
        positions = [
            {"type": "BUY", "volume": 0.02, "price_open": 4501.0, "sl": 0.0, "tp": 0.0},
            {"type": "BUY", "volume": 0.04, "price_open": 4503.0, "sl": 0.0, "tp": 0.0},
        ]

        changed = st.reconcile_with_broker(positions, balance=1005.5)

        self.assertTrue(changed)
        self.assertEqual(st.get("direction"), "BUY")
        self.assertEqual(st.get("entry_price"), 4502.33)
        self.assertEqual(st.get("total_lots"), 0.06)
        self.assertEqual(st.get("realized_profit"), 5.5)

    def test_breakeven_is_confirmed_only_after_broker_protects_trade(self):
        st = self._new_state()
        st.open_signal("BUY", 4500.0, "TG", 0.06, start_balance=1000.0)
        st.request_breakeven()

        self.assertTrue(st.is_breakeven())
        self.assertFalse(st.get("breakeven_set"))
        self.assertTrue(st.get("breakeven_pending"))

        positions = [
            {"type": "BUY", "volume": 0.03, "price_open": 4500.0, "sl": 4500.0, "tp": 4510.0},
            {"type": "BUY", "volume": 0.03, "price_open": 4500.0, "sl": 4500.0, "tp": 4510.0},
        ]
        st.reconcile_with_broker(positions, balance=1000.0)

        self.assertTrue(st.get("breakeven_set"))
        self.assertFalse(st.get("breakeven_pending"))
        self.assertEqual(st.get("sl_price"), 4500.0)
        self.assertEqual(st.get("tp_price"), 4510.0)

    def test_pending_breakeven_expires_if_broker_never_confirms(self):
        st = self._new_state()
        st.open_signal("SELL", 4500.0, "TG", 0.06, start_balance=1000.0)
        st.request_breakeven()
        st._data["breakeven_pending_since"] = time.time() - 30

        positions = [
            {"type": "SELL", "volume": 0.06, "price_open": 4500.0, "sl": 0.0, "tp": 0.0},
        ]
        st.reconcile_with_broker(positions, balance=1000.0)

        self.assertFalse(st.get("breakeven_set"))
        self.assertFalse(st.get("breakeven_pending"))


if __name__ == "__main__":
    unittest.main()
