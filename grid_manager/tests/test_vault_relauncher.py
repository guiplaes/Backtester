"""
Test del flow closer + relauncher amb mocks de Pionex.

Cobreix:
  1. Dry-run amb funding feasible → no execution, plan correctament calculat
  2. Dry-run amb funding NOT feasible → abort, base romandria al vault
  3. Snapshot pre-close lectura correcta del bot
  4. Càlcul correcte del target_value (base × price + quote)
  5. Càlcul correcte del nou rang (centrat al preu actual, ±width/2)
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vault import relauncher
from vault.funding import compute_funding_plan


class TestRelauncherDryRun(unittest.TestCase):

    def setUp(self):
        self.cfg = {
            "id": "test-bot-id-uuid",
            "symbol": "PAXG_USDT",
            "base": "PAXG",
            "quote": "USDT",
            "width_pct": 0.032,
            "rows": 8,
        }
        # Mock bot state pre-close
        self.pre_state = {
            "base_amount": 0.020,
            "quote_amount": 50.0,
            "price": 4500.0,
            "average_cost": 4400.0,
            "grid_profit": 5.0,
            "cycles": 30,
            "raw": {},
        }

    def _mock_bot_state(self):
        return patch("vault.relauncher._read_bot_state", return_value=self.pre_state)

    def _mock_prices(self):
        return patch("pionex_client.get_current_price",
                     side_effect=lambda sym: {
                         "PAXG_USDT": 4500, "BTC_USDT": 75000,
                         "ETH_USDT": 2000, "SOL_USDT": 80,
                         "USOX_USDT": 145, "SPYX_USDT": 740,
                     }.get(sym, 0))

    def _mock_inventory(self, vault_spec):
        from tests.test_vault_funding import _mock_inventory
        return patch("vault.funding.get_inventory",
                     return_value=_mock_inventory(vault_spec))

    def test_01_dry_run_funding_feasible(self):
        # Pre: 0.020 PAXG @ avg 4400, current 4500 → value = 90 + 50 = 140
        # Target = $140. Recovered USDT = $50. Shortfall = $90.
        # Mock vault amb USDT $100 → P2 cobreix
        with self._mock_bot_state(), self._mock_prices(), \
             self._mock_inventory({"USDT": (100, None)}):
            out = relauncher.relaunch_after_breakout(
                bot_name="PAXG_USDT", bot_id="test-id", cfg=self.cfg,
                price_at_breakout=4500.0, dry_run=True,
            )

        self.assertTrue(out["ok"])
        self.assertTrue(out["dry_run"])
        self.assertAlmostEqual(out["target_value"], 140.0, places=2)
        self.assertAlmostEqual(out["shortfall_usdt"], 90.0, places=2)
        # Funding plan summary contains 'feasible'
        self.assertIn("feasible", out["funding_plan_summary"].lower())
        # New range centered at 4500 with width 3.2% = ±$72
        self.assertAlmostEqual(out["new_range"]["bottom"], 4500 - (4500*0.032/2), places=2)
        self.assertAlmostEqual(out["new_range"]["top"], 4500 + (4500*0.032/2), places=2)
        # No new_bot_id in dry_run
        self.assertNotIn("new_bot_id", out)

    def test_02_dry_run_funding_not_feasible(self):
        # Mock vault amb gairebé res → shortfall no cobert
        with self._mock_bot_state(), self._mock_prices(), \
             self._mock_inventory({"USDT": (5, None)}):
            out = relauncher.relaunch_after_breakout(
                bot_name="PAXG_USDT", bot_id="test-id", cfg=self.cfg,
                price_at_breakout=4500.0, dry_run=True,
            )

        self.assertFalse(out["ok"])
        self.assertTrue(any("NOT feasible" in e for e in out["errors"]))

    def test_03_no_funding_needed(self):
        # Bot tenia molt USDT → close recupera prou per recrear
        self.pre_state["quote_amount"] = 200.0  # > target_value
        self.pre_state["base_amount"] = 0.001   # poc base
        with self._mock_bot_state(), self._mock_prices():
            out = relauncher.relaunch_after_breakout(
                bot_name="PAXG_USDT", bot_id="test-id", cfg=self.cfg,
                price_at_breakout=4500.0, dry_run=True,
            )

        self.assertTrue(out["ok"])
        # Target = 0.001*4500 + 200 = 204.5. Recovered USDT = 200. Shortfall = 4.5
        self.assertLess(out["shortfall_usdt"], 5)

    def test_04_new_range_calculation(self):
        bottom, top, rows = relauncher._compute_new_range(
            price=100.0, width_pct=0.10, rows=8,
        )
        # width 10% → ±5%
        self.assertAlmostEqual(bottom, 95.0)
        self.assertAlmostEqual(top, 105.0)
        self.assertEqual(rows, 8)

    def test_05_target_value_calculation(self):
        # Verify que out["target_value"] = base*price + quote
        with self._mock_bot_state(), self._mock_prices(), \
             self._mock_inventory({"USDT": (200, None)}):
            out = relauncher.relaunch_after_breakout(
                bot_name="PAXG_USDT", bot_id="test-id", cfg=self.cfg,
                price_at_breakout=4500.0, dry_run=True,
            )
        # 0.020 PAXG × 4500 + 50 USDT = 140
        self.assertAlmostEqual(out["target_value"], 140.0, places=2)


class TestRelaunchWithProfitVault(unittest.TestCase):
    """Test que P1 funciona correctament — vault PAXG en profit serveix per
    finançar nou bot d'altre asset (ex: BTC)."""

    def test_p1_funds_btc_with_paxg_profit(self):
        cfg = {
            "id": "btc-uuid", "symbol": "BTC_USDT",
            "base": "BTC", "quote": "USDT",
            "width_pct": 0.0516, "rows": 12,
        }
        # BTC bot pre: 0.003 BTC + $30 USDT @ price $74000 → target = $252
        pre = {
            "base_amount": 0.003, "quote_amount": 30.0,
            "price": 74000.0, "average_cost": 78000.0,
            "grid_profit": 4.0, "cycles": 20, "raw": {},
        }
        # Vault té PAXG en profit (avg $4000, current $4500 → +12.5%)
        with patch("vault.relauncher._read_bot_state", return_value=pre), \
             patch("pionex_client.get_current_price",
                   side_effect=lambda s: {"BTC_USDT": 74000, "PAXG_USDT": 4500,
                                           "ETH_USDT": 2000, "SOL_USDT": 80,
                                           "USOX_USDT": 145, "SPYX_USDT": 740}.get(s, 0)), \
             patch("vault.funding.get_inventory", return_value={
                 "USDT": {"qty": 10, "cost_total_usdt": 10, "avg_cost": 1, "updated_at": None, "notes": None},
                 "PAXG": {"qty": 0.060, "cost_total_usdt": 240, "avg_cost": 4000, "updated_at": None, "notes": None},
                 "BTC": {"qty": 0, "cost_total_usdt": 0, "avg_cost": None, "updated_at": None, "notes": None},
                 "ETH": {"qty": 0, "cost_total_usdt": 0, "avg_cost": None, "updated_at": None, "notes": None},
                 "SOL": {"qty": 0, "cost_total_usdt": 0, "avg_cost": None, "updated_at": None, "notes": None},
                 "USOX": {"qty": 0, "cost_total_usdt": 0, "avg_cost": None, "updated_at": None, "notes": None},
                 "SPYX": {"qty": 0, "cost_total_usdt": 0, "avg_cost": None, "updated_at": None, "notes": None},
             }):
            out = relauncher.relaunch_after_breakout(
                bot_name="BTC_USDT", bot_id="btc-uuid", cfg=cfg,
                price_at_breakout=74000.0, dry_run=True,
            )
        self.assertTrue(out["ok"])
        self.assertAlmostEqual(out["target_value"], 252.0, places=1)
        # Shortfall = 252 - 30 = 222
        self.assertAlmostEqual(out["shortfall_usdt"], 222, places=1)
        # Funding plan should include P1 PAXG
        self.assertIn("P1", out["funding_plan_summary"])
        self.assertIn("PAXG", out["funding_plan_summary"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
