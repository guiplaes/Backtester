"""
E2E test: cycle complet contra Neon REAL, sense Pionex.

Verifica el flow operatiu:
  1. Insert a injection_queue → consume → vault_inventory s'actualitza
  2. add_base directe (simulant bot close)
  3. Funding plan amb dades reals del vault
  4. remove_base directe (simulant venda P1)
  5. Reset clean al final

Toca Neon real (vault_inventory, vault_events, injection_queue). Tot dins
de una transacció lògica amb cleanup al final.

NO toca cap bot real. NO crida Pionex.
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cloud.db_cloud import conn
from vault.inventory import (
    get_inventory, add_base, remove_base, add_usdt, remove_usdt,
)
from vault import consume_injections
from vault.funding import compute_funding_plan


PRICES = {
    "BTC": 75000.0, "ETH": 2000.0, "PAXG": 4500.0,
    "SOL": 80.0, "USOX": 145.0, "SPYX": 740.0,
}


def _reset_vault():
    """Restaurar estat inicial: USDT=$20.25, resta=0, events/queue net."""
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            UPDATE vault_inventory
            SET qty = CASE WHEN asset='USDT' THEN 20.25 ELSE 0 END,
                cost_total_usdt = CASE WHEN asset='USDT' THEN 20.25 ELSE 0 END,
                updated_at = NOW()
        """)
        cur.execute("DELETE FROM vault_events WHERE source LIKE 'E2E_TEST%'")
        cur.execute("DELETE FROM injection_queue WHERE note LIKE 'E2E_TEST%'")
        c.commit()


class TestVaultE2E(unittest.TestCase):

    def setUp(self):
        _reset_vault()

    def tearDown(self):
        _reset_vault()

    def test_01_injection_flow(self):
        """Insert injection → consume → verify vault USDT updated."""
        # Insert injection
        with conn() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO injection_queue (amount_usdt, note) VALUES (10, 'E2E_TEST inj1') RETURNING id"
            )
            inj_id = cur.fetchone()[0]
            c.commit()
        # Run consumer
        result = consume_injections.consume_all()
        self.assertEqual(result["consumed"], 1)
        self.assertEqual(result["failed"], 0)
        # Verify vault USDT now 30.25
        inv = get_inventory()
        self.assertAlmostEqual(inv["USDT"]["qty"], 30.25, places=2)

    def test_02_add_base_simulating_bot_close(self):
        """add_base simulant que un bot ha tancat i ha retornat base."""
        ok = add_base(
            asset="PAXG", qty=0.020, cost_usdt=88.0,
            source="E2E_TEST/closer", idempotency_key="e2e_addbase_paxg_1",
            notes="Simulating PAXG bot close",
        )
        self.assertTrue(ok)
        inv = get_inventory()
        self.assertAlmostEqual(inv["PAXG"]["qty"], 0.020, places=6)
        self.assertAlmostEqual(inv["PAXG"]["avg_cost"], 4400.0, places=2)

    def test_03_add_base_idempotency(self):
        """Running add_base twice amb mateix key → només aplica una vegada."""
        add_base(asset="PAXG", qty=0.010, cost_usdt=44.0,
                 source="E2E_TEST", idempotency_key="e2e_idem_paxg_2")
        add_base(asset="PAXG", qty=0.010, cost_usdt=44.0,
                 source="E2E_TEST", idempotency_key="e2e_idem_paxg_2")  # SAME key
        inv = get_inventory()
        self.assertAlmostEqual(inv["PAXG"]["qty"], 0.010, places=6)  # Només 1x

    def test_04_remove_base_proportional_cost(self):
        """remove_base ha de descomptar cost proporcionalment al qty venut."""
        # Setup: 0.020 PAXG @ avg $4400 → cost total $88
        add_base(asset="PAXG", qty=0.020, cost_usdt=88.0,
                 source="E2E_TEST", idempotency_key="e2e_rem_setup")
        # Venem la meitat (0.010)
        remove_base(asset="PAXG", qty=0.010,
                    source="E2E_TEST", idempotency_key="e2e_rem_half")
        inv = get_inventory()
        # Quedaria 0.010 amb cost $44, avg encara $4400
        self.assertAlmostEqual(inv["PAXG"]["qty"], 0.010, places=6)
        self.assertAlmostEqual(inv["PAXG"]["cost_total_usdt"], 44.0, places=2)
        self.assertAlmostEqual(inv["PAXG"]["avg_cost"], 4400.0, places=2)

    def test_05_funding_plan_with_real_vault(self):
        """Computar funding plan llegint vault real (no mocks)."""
        # Setup vault: USDT 50, PAXG 0.020 @ 4400 (current 4500 = +2.27% profit)
        with conn() as c, c.cursor() as cur:
            cur.execute("UPDATE vault_inventory SET qty=50, cost_total_usdt=50 WHERE asset='USDT'")
            c.commit()
        add_base(asset="PAXG", qty=0.020, cost_usdt=88.0,
                 source="E2E_TEST", idempotency_key="e2e_fund_paxg")

        plan = compute_funding_plan(
            target_usdt=100, asset_being_funded="BTC", prices=PRICES,
        )
        self.assertTrue(plan.feasible)
        # P1 PAXG (profit +2.27% > 0.5% margin) + P2 USDT
        priorities = [s.priority for s in plan.steps]
        self.assertIn("P1", priorities)
        self.assertIn("P2", priorities)
        self.assertAlmostEqual(plan.total_raised, 100, places=1)

    def test_06_complete_cycle_close_fund_simulate(self):
        """Simula tot el cycle: bot close → vault add base+usdt → funding plan
        per recrear amb target_value preservat."""
        # Simulació: BTC bot tenia 0.003 BTC @ avg 78k + $30 USDT @ price $74k
        # → value = 0.003*74000 + 30 = $252
        target_value = 252.0
        recovered_base_qty = 0.003
        recovered_base_cost = 0.003 * 78000  # $234
        recovered_usdt = 30.0

        # Step 1: bot close → add to vault
        add_base(asset="BTC", qty=recovered_base_qty,
                 cost_usdt=recovered_base_cost,
                 source="E2E_TEST/close", idempotency_key="e2e_cycle_addbase")
        add_usdt(amount=recovered_usdt,
                 source="E2E_TEST/close", idempotency_key="e2e_cycle_addusdt")

        # Verify vault state
        inv = get_inventory()
        self.assertAlmostEqual(inv["BTC"]["qty"], 0.003, places=6)
        self.assertAlmostEqual(inv["BTC"]["avg_cost"], 78000.0, places=2)
        usdt_in_vault = inv["USDT"]["qty"]  # 20.25 inicial + 30 recovered = 50.25
        self.assertAlmostEqual(usdt_in_vault, 50.25, places=2)

        # Step 2: shortfall = target - recovered USDT = 252 - 30 = 222
        shortfall = target_value - recovered_usdt
        self.assertAlmostEqual(shortfall, 222, places=2)

        # Step 3: compute funding plan (asset_being_funded='BTC' → exclou BTC de P3)
        plan = compute_funding_plan(
            target_usdt=shortfall, asset_being_funded="BTC", prices=PRICES,
        )
        # No tenim altres assets profitable. Només USDT $20.25 i BTC mateix (P4).
        # P2: $20.25, P4 BTC: 0.003 BTC @ 75k = $224.55. Total ~$244 → feasible.
        self.assertTrue(plan.feasible)
        # P4 (own asset) HA d'estar present
        p4_steps = [s for s in plan.steps if s.priority == "P4"]
        self.assertEqual(len(p4_steps), 1)
        self.assertEqual(p4_steps[0].asset, "BTC")
        # I ha de ser DEPRÉS de P2
        priority_order = [s.priority for s in plan.steps]
        p2_idx = priority_order.index("P2") if "P2" in priority_order else -1
        p4_idx = priority_order.index("P4")
        self.assertGreater(p4_idx, p2_idx)


if __name__ == "__main__":
    unittest.main(verbosity=2)
