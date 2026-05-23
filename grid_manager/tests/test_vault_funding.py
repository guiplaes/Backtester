"""
Test suite per al waterfall de funding.

Mocks get_inventory() per controlar l'estat del vault. NO toca Neon real.
NO toca Pionex. Pure logic verification.

Casos coberts:
  1. Vault buit + USDT just → P2 cobreix
  2. Vault buit + USDT < target → NOT feasible
  3. Vault amb 1 asset en profit + USDT → P1 + P2 cobreixen
  4. Vault amb 1 asset just al marge (0.4% < 0.5%) → no es considera profit
  5. Múltiples assets en profit → ranked per profit_ratio DESC
  6. Vault tots en pèrdua + USDT insuficient → P3 (excloent asset_being_funded) + P4
  7. P3 ordering: menor pèrdua primer
  8. P4 com a últim recurs (no abans de P1, P2, P3)
  9. Combinació parcial P1 + P2 + P3
  10. Asset being funded = 'USDT' → no P4 possible
  11. Edge: target_usdt = 0
  12. Edge: vault assolutament buit, target > 0 → NOT feasible
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vault.funding import compute_funding_plan, FundingPlan, FundingStep


def _mock_inventory(spec: dict) -> dict:
    """Helper: build inventory dict in the get_inventory() format.

    spec: {asset: (qty, avg_cost)} → format inv compatible amb funding.
    """
    inv = {}
    for asset, t in spec.items():
        qty, avg = t
        cost = qty * avg if avg else 0
        inv[asset] = {
            "qty": qty,
            "cost_total_usdt": cost,
            "avg_cost": (cost / qty) if qty > 0 else None,
            "updated_at": None,
            "notes": None,
        }
    return inv


class TestFundingWaterfall(unittest.TestCase):

    def setUp(self):
        # Preus per defecte (pots overrideejar a cada test)
        self.prices = {
            "BTC": 75000.0, "ETH": 2000.0, "PAXG": 4500.0,
            "SOL": 80.0, "USOX": 145.0, "SPYX": 740.0,
        }

    def _run(self, target, asset, vault_spec, prices=None):
        with patch("vault.funding.get_inventory",
                   return_value=_mock_inventory(vault_spec)):
            return compute_funding_plan(
                target_usdt=target, asset_being_funded=asset,
                prices=prices or self.prices,
            )

    # ── Casos bàsics ────────────────────────────────────────────

    def test_01_target_zero(self):
        plan = self._run(0, "BTC", {"USDT": (10, None)})
        self.assertTrue(plan.feasible)
        self.assertEqual(len(plan.steps), 0)

    def test_02_p2_only_just_enough(self):
        plan = self._run(50, "BTC", {"USDT": (50, None)})
        self.assertTrue(plan.feasible)
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].priority, "P2")
        self.assertAlmostEqual(plan.total_raised, 50, places=2)

    def test_03_no_funding_available(self):
        plan = self._run(100, "BTC", {"USDT": (0, None)})
        self.assertFalse(plan.feasible)
        self.assertAlmostEqual(plan.shortfall, 100, places=2)

    def test_04_p2_insufficient(self):
        plan = self._run(100, "BTC", {"USDT": (50, None)})
        self.assertFalse(plan.feasible)
        self.assertAlmostEqual(plan.shortfall, 50, places=2)

    # ── P1: vault profit ────────────────────────────────────────

    def test_05_p1_single_profit_asset(self):
        # PAXG amb avg=$4000, preu actual $4500 → +12.5% profit
        plan = self._run(50, "BTC", {
            "USDT": (0, None),
            "PAXG": (0.020, 4000),  # qty=0.020 PAXG, avg_cost=$4000
        })
        self.assertTrue(plan.feasible)
        self.assertEqual(plan.steps[0].priority, "P1")
        self.assertEqual(plan.steps[0].asset, "PAXG")
        self.assertGreater(plan.steps[0].realized_pnl, 0)  # profit realitzat

    def test_06_p1_below_margin_not_profitable(self):
        # PAXG amb avg=$4490, preu $4500 → +0.22% (sota margin 0.5%)
        # P1 NO ha d'activar-se (no és prou profit), però P3 SÍ
        # (zona break-even acceptada com a "venda quasi sense pèrdua")
        plan = self._run(50, "BTC", {
            "USDT": (10, None),
            "PAXG": (0.020, 4490),
        })
        priorities = [s.priority for s in plan.steps]
        self.assertNotIn("P1", priorities)   # NO P1 (sota margin)
        # P2 cobreix els primers $10, P3 (PAXG ~$90 disponibles) cobreix resta → feasible
        self.assertIn("P2", priorities)
        self.assertIn("P3", priorities)
        self.assertTrue(plan.feasible)

    def test_07_p1_multiple_ranked_by_profit(self):
        # PAXG +12.5%, ETH +15% → ETH primer
        plan = self._run(200, "BTC", {
            "USDT": (0, None),
            "PAXG": (0.030, 4000),  # +12.5%
            "ETH":  (0.100, 1739),  # +15.0%
        })
        self.assertTrue(plan.feasible)
        p1_steps = [s for s in plan.steps if s.priority == "P1"]
        # ETH ha de ser el primer P1
        self.assertEqual(p1_steps[0].asset, "ETH")

    # ── P3: vault loss ─────────────────────────────────────────

    def test_08_p3_excludes_own_asset(self):
        # Tot en pèrdua. asset_being_funded=BTC → P3 NO inclou BTC
        plan = self._run(50, "BTC", {
            "USDT": (0, None),
            "BTC":  (0.001, 80000),   # pèrdua
            "ETH":  (0.030, 2100),    # pèrdua
            "PAXG": (0.020, 4700),    # pèrdua
        })
        p3_assets = [s.asset for s in plan.steps if s.priority == "P3"]
        self.assertNotIn("BTC", p3_assets)  # NO ha de vendre BTC al P3

    def test_09_p3_orders_smallest_loss_first(self):
        # ETH -4.76%, PAXG -4.26%, SOL -8.0% → PAXG primer (menor pèrdua)
        plan = self._run(50, "BTC", {
            "USDT": (0, None),
            "ETH":  (0.030, 2100),    # 2000/2100 = -4.76%
            "PAXG": (0.020, 4700),    # 4500/4700 = -4.26%
            "SOL":  (0.300, 87),      # 80/87 = -8.05%
        })
        p3_steps = [s for s in plan.steps if s.priority == "P3"]
        # Primer ha de ser el de menor % loss (PAXG)
        self.assertEqual(p3_steps[0].asset, "PAXG")

    def test_10_p4_last_resort(self):
        # Tot en pèrdua i el BTC mateix és l'únic que pot cobrir target
        plan = self._run(50, "BTC", {
            "USDT": (0, None),
            "BTC":  (0.001, 80000),  # cost $80, current $75 → val $75 (~$74.96 net)
        })
        self.assertTrue(plan.feasible)
        # Última step ha de ser P4
        self.assertEqual(plan.steps[-1].priority, "P4")
        self.assertEqual(plan.steps[-1].asset, "BTC")

    def test_11_p4_partial(self):
        # P3 no cobreix tot, P4 complementa
        plan = self._run(100, "BTC", {
            "USDT": (0, None),
            "ETH":  (0.010, 2100),     # ~$20 disponibles a pèrdua
            "BTC":  (0.002, 80000),    # ~$150 disponibles a P4
        })
        self.assertTrue(plan.feasible)
        priorities = [s.priority for s in plan.steps]
        self.assertIn("P3", priorities)
        self.assertIn("P4", priorities)

    # ── Combinacions ────────────────────────────────────────────

    def test_12_combination_p1_p2_p3(self):
        plan = self._run(200, "BTC", {
            "USDT": (40, None),
            "PAXG": (0.015, 4000),    # P1: +12.5% → ~$67 disponibles
            "ETH":  (0.060, 2100),    # P3: -4.76% → ~$120 disponibles
            "SOL":  (0.500, 80),      # P3 segon (preu = avg, no es ven)
        })
        self.assertTrue(plan.feasible)
        priorities = sorted(set(s.priority for s in plan.steps))
        # Esperem P1, P2, P3 (no P4 perquè asset funded és BTC i no en tenim)
        self.assertEqual(priorities, ["P1", "P2", "P3"])

    def test_13_only_p2_when_others_unavailable(self):
        plan = self._run(50, "BTC", {
            "USDT": (60, None),
            "PAXG": (0, None),  # qty=0, no P1
        })
        self.assertTrue(plan.feasible)
        # P2 cobreix tot, no necessita P1
        self.assertEqual([s.priority for s in plan.steps], ["P2"])

    # ── Edge cases ──────────────────────────────────────────────

    def test_14_asset_being_funded_is_usdt(self):
        # Edge cas (no hauria de passar): P4 = 'USDT' no té sentit
        plan = self._run(50, "USDT", {"USDT": (60, None)})
        self.assertTrue(plan.feasible)
        # No hi ha cap P4 (asset is USDT)
        self.assertNotIn("P4", [s.priority for s in plan.steps])

    def test_15_zero_price_skipped(self):
        # Si get_current_price retorna 0 (oblidat al dict), aquell asset es skipped
        prices = {"BTC": 75000, "ETH": 0, "PAXG": 4500, "SOL": 80, "USOX": 145, "SPYX": 740}
        plan = self._run(50, "BTC", {
            "USDT": (0, None),
            "ETH":  (1.0, 1000),  # seria profit gran si preu vàlid, però preu=0 → skip
            "PAXG": (0.020, 4000),
        }, prices=prices)
        # Hauria d'usar PAXG (preu vàlid) i no ETH (preu=0)
        used_assets = [s.asset for s in plan.steps]
        self.assertNotIn("ETH", used_assets)

    def test_16_idempotency_of_compute(self):
        # Running compute twice with same inputs must produce same plan
        spec = {"USDT": (10, None), "PAXG": (0.010, 4000)}
        p1 = self._run(50, "BTC", spec)
        p2 = self._run(50, "BTC", spec)
        self.assertEqual(len(p1.steps), len(p2.steps))
        self.assertAlmostEqual(p1.total_raised, p2.total_raised, places=4)

    def test_17_fee_rate_applied(self):
        # Verifica que les fees descompten l'USDT obtingut
        plan = self._run(100, "BTC", {
            "USDT": (0, None),
            "PAXG": (1.0, 4000),  # excés de PAXG
        })
        # Per obtenir $100 USDT, ha de vendre una mica més de $100 worth
        p1 = plan.steps[0]
        # 0.022222 PAXG @ $4500 = $100, però amb 0.05% fee: 0.02223 ~ $100
        gross_value = p1.qty_to_sell * 4500
        self.assertAlmostEqual(gross_value * 0.9995, 100, places=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
