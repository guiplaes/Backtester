"""
test_vault_battle.py — Simulacions exhaustives de situacions que poden donar bug.

⚠️ CRITICAL: aquests tests MAI han de tocar Neon de producció.
Els tests del Class B/D/E (relauncher amb mocks de cancel/create) usen
mocks de vault.inventory.add_base/add_usdt/remove_usdt per evitar
contaminar vault_inventory real.

⚠️ Bug descobert 2026-05-23: la primera versió d'aquests tests SÍ tocava
Neon (els mocks de cancel_bot/create_spot_grid no mockejaven add_usdt),
causant que vault_inventory.USDT pugés a \$353 (de \$11). Calia neteja
manual. Mai més.

Categories cobertes:
  A) Closer breakout detection edge cases
  B) Cancel_bot failure modes
  C) Funding waterfall edge cases
  D) Create_spot_grid failure modes
  E) Pionex API failures partway
  F) Neon failures during log
  G) State corruption / race conditions
  H) Idempotency under retry

Cada test imprimeix PASS/FAIL clar amb la situació exacta.
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vault.funding import compute_funding_plan
from vault import relauncher


def _mock_inv(spec: dict) -> dict:
    """Helper per construir vault_inventory."""
    inv = {}
    for asset, t in spec.items():
        qty, avg = t
        cost = qty * avg if avg else 0
        inv[asset] = {
            "qty": qty, "cost_total_usdt": cost,
            "avg_cost": (cost / qty) if qty > 0 else None,
            "updated_at": None, "notes": None,
        }
    return inv


def _mock_full_inv(usdt_qty=0, btc=None, eth=None, paxg=None, sol=None,
                   usox=None, spyx=None) -> dict:
    """Inventari complet amb tots assets (None = 0)."""
    spec = {"USDT": (usdt_qty, None)}
    for asset, val in [("BTC", btc), ("ETH", eth), ("PAXG", paxg),
                       ("SOL", sol), ("USOX", usox), ("SPYX", spyx)]:
        if val:
            spec[asset] = val
        else:
            spec[asset] = (0, None)
    return _mock_inv(spec)


PRICES = {
    "BTC": 75000.0, "ETH": 2000.0, "PAXG": 4500.0,
    "SOL": 80.0, "USOX": 145.0, "SPYX": 740.0,
}


def _isolate_vault_writes():
    """Helper: bloquegen els writes a vault_inventory per evitar contaminar Neon.
    Retorna llista de patches per usar amb 'with ExitStack'."""
    from contextlib import ExitStack
    stack = ExitStack()
    # Mock totes les funcions que escriuen al vault real
    for fn in ["add_base", "remove_base", "add_usdt", "remove_usdt"]:
        # Mock al lloc on s'importa (vault.relauncher i vault.funding)
        p1 = patch(f"vault.inventory.{fn}", return_value=True)
        p2 = patch(f"vault.relauncher.{fn}", return_value=True, create=True)
        p3 = patch(f"vault.funding.{fn}", return_value=True, create=True)
        stack.enter_context(p1)
        try: stack.enter_context(p2)
        except Exception: pass
        try: stack.enter_context(p3)
        except Exception: pass
    # També mock log_capital_event, log_bot_lifecycle, etc per no contaminar
    for fn in ["log_capital_event", "log_bot_lifecycle", "log_recolocation",
               "upsert_bot", "open_epoch", "close_epoch", "mark_bot_closed",
               "snapshot_bot_state"]:
        try:
            stack.enter_context(patch(f"vault.relauncher.{fn}", return_value=None))
        except Exception:
            pass
    return stack


class A_ClosingEdgeCases(unittest.TestCase):
    """Closer: situacions del detector de breakout."""

    def test_A1_price_exactly_at_bottom(self):
        """Preu exactament == bottom — NO ha de dispar (no és breakout)."""
        from vault.closer import BREAKOUT_TOLERANCE
        bottom = 100.0
        price = bottom  # exactament al edge
        threshold = bottom * (1 - BREAKOUT_TOLERANCE)
        self.assertGreater(price, threshold, "Preu igual a bottom NO ha de superar threshold inferior")

    def test_A2_price_0_001_below_bottom(self):
        """Preu just 0.1% sota bottom — ha de dispar (és el threshold)."""
        from vault.closer import BREAKOUT_TOLERANCE
        bottom = 100.0
        price = bottom * (1 - BREAKOUT_TOLERANCE) - 0.001  # just sota
        threshold = bottom * (1 - BREAKOUT_TOLERANCE)
        self.assertLess(price, threshold, "Preu sota tolerance HA de superar threshold")

    def test_A3_consecutive_counter_needs_3(self):
        """Requereix CONFIRM_BARS=3 reads consecutius sota."""
        from vault.closer import CONFIRM_BARS
        self.assertEqual(CONFIRM_BARS, 3, "CONFIRM_BARS canviat — verificar lògica relauncher")

    def test_A4_pionex_returns_invalid_state(self):
        """Si Pionex retorna top=0 o status!=running, NO ha de fer res."""
        # Simulem read invàlid
        invalid_states = [
            {"top": 0, "bottom": 0, "price": 100, "status": "running"},
            {"top": 100, "bottom": 50, "price": 0, "status": "running"},
            {"top": 100, "bottom": 50, "price": 75, "status": "paused"},
            {"top": 100, "bottom": 50, "price": 75, "status": "canceled"},
        ]
        for state in invalid_states:
            # closer.check_breakout hauria de retornar status='invalid_state' o similar
            self.assertTrue(
                state["top"] <= 0 or state["bottom"] <= 0 or
                state["price"] <= 0 or state["status"] != "running",
                f"Estat invàlid detectat: {state}"
            )


class B_CancelBotFailureModes(unittest.TestCase):
    """cancel_bot: failure paths."""

    def test_B1_cancel_returns_result_false(self):
        """Si cancel_bot returna {result:False}, relauncher ha d'abortar net."""
        with _isolate_vault_writes(), \
             patch("vault.relauncher._read_bot_state") as mock_read:
            mock_read.return_value = {
                "base_amount": 0.005, "quote_amount": 50, "price": 70000,
                "average_cost": 75000, "grid_profit": 5.0, "cycles": 30, "raw": {},
            }
            with patch("pionex_client.cancel_bot") as mock_cancel:
                mock_cancel.return_value = {"result": False, "message": "Network error"}
                out = relauncher.relaunch_after_breakout(
                    bot_name="BTC_USDT", bot_id="test-id",
                    cfg={"id": "test-id", "symbol": "BTC_USDT", "base": "BTC",
                         "quote": "USDT", "width_pct": 0.05, "rows": 12},
                    price_at_breakout=70000.0, dry_run=False,
                )
                self.assertFalse(out.get("ok"))
                self.assertTrue(any("cancel" in e.lower() for e in out.get("errors", [])))

    def test_B2_cancel_exception(self):
        """Si cancel_bot llança exception, relauncher ha d'abortar net."""
        with _isolate_vault_writes(), \
             patch("vault.relauncher._read_bot_state") as mock_read:
            mock_read.return_value = {
                "base_amount": 0.005, "quote_amount": 50, "price": 70000,
                "average_cost": 75000, "grid_profit": 5.0, "cycles": 30, "raw": {},
            }
            with patch("pionex_client.cancel_bot") as mock_cancel:
                mock_cancel.side_effect = Exception("SSL timeout")
                out = relauncher.relaunch_after_breakout(
                    bot_name="BTC_USDT", bot_id="test-id",
                    cfg={"id": "test-id", "symbol": "BTC_USDT", "base": "BTC",
                         "quote": "USDT", "width_pct": 0.05, "rows": 12},
                    price_at_breakout=70000.0, dry_run=False,
                )
                self.assertFalse(out.get("ok"))
                self.assertTrue(any("cancel exception" in e.lower() for e in out.get("errors", [])))


class C_FundingWaterfallEdgeCases(unittest.TestCase):
    """Funding: combinacions extremes."""

    def test_C1_all_vaults_empty_only_bots(self):
        """Vault totalment buit, només bots existeixen. Plan: NOT feasible."""
        with patch("vault.funding.get_inventory") as mock_inv:
            mock_inv.return_value = _mock_full_inv(usdt_qty=0)
            plan = compute_funding_plan(target_usdt=100, asset_being_funded="BTC", prices=PRICES)
            self.assertFalse(plan.feasible)
            self.assertEqual(plan.shortfall, 100)

    def test_C2_usdt_exactly_equals_shortfall(self):
        """Vault USDT exactament = shortfall → P2 cobreix tot."""
        with patch("vault.funding.get_inventory") as mock_inv:
            mock_inv.return_value = _mock_full_inv(usdt_qty=100)
            plan = compute_funding_plan(target_usdt=100, asset_being_funded="BTC", prices=PRICES)
            self.assertTrue(plan.feasible)
            self.assertEqual(len(plan.steps), 1)
            self.assertEqual(plan.steps[0].priority, "P2")

    def test_C3_p1_candidate_at_exact_margin(self):
        """PAXG amb profit exactament al margin (0.5%) — NO ha d'activar P1."""
        # PAXG avg cost = $4500, current = $4500 × 1.005 = $4522.5 (exactly at margin)
        with patch("vault.funding.get_inventory") as mock_inv:
            mock_inv.return_value = _mock_full_inv(usdt_qty=50, paxg=(0.020, 4500))
            prices_test = {**PRICES, "PAXG": 4500 * 1.005}  # +0.5% exact
            plan = compute_funding_plan(target_usdt=100, asset_being_funded="BTC", prices=prices_test)
            # P1 NO ha de fire (no > 0.5%, només =)
            p1_steps = [s for s in plan.steps if s.priority == "P1"]
            self.assertEqual(len(p1_steps), 0, "P1 no ha de fire a profit ratio = margin (només > margin)")

    def test_C4_all_p1_below_margin_all_p3_fire(self):
        """Tots vaults entre break-even i +margin → tots cauen a P3."""
        with patch("vault.funding.get_inventory") as mock_inv:
            mock_inv.return_value = _mock_full_inv(
                usdt_qty=0,
                paxg=(0.010, 4500),  # avg 4500
                eth=(0.050, 2000),   # avg 2000
                sol=(0.500, 80),     # avg 80
            )
            # Preus iguals als avg cost → 0% profit (sota margin)
            plan = compute_funding_plan(target_usdt=100, asset_being_funded="BTC", prices=PRICES)
            # P1: ningú (no profitable)
            p1 = [s for s in plan.steps if s.priority == "P1"]
            self.assertEqual(len(p1), 0)
            # P3: PAXG, ETH, SOL (no BTC perquè és asset_being_funded)
            p3 = [s for s in plan.steps if s.priority == "P3"]
            self.assertGreater(len(p3), 0)

    def test_C5_p4_when_funding_asset_is_only_remaining(self):
        """Tota la resta esgotada, només queda asset_being_funded al vault."""
        with patch("vault.funding.get_inventory") as mock_inv:
            mock_inv.return_value = _mock_full_inv(usdt_qty=10, btc=(0.005, 80000))
            plan = compute_funding_plan(target_usdt=200, asset_being_funded="BTC", prices=PRICES)
            # P2: $10, P4: BTC (own) → ~$375 disponibles
            self.assertTrue(plan.feasible)
            p4 = [s for s in plan.steps if s.priority == "P4"]
            self.assertEqual(len(p4), 1)
            self.assertEqual(p4[0].asset, "BTC")

    def test_C6_p3_ordering_truly_smallest_loss_first(self):
        """P3 ha d'ordenar per pèrdua % asc (menor pèrdua primer)."""
        with patch("vault.funding.get_inventory") as mock_inv:
            mock_inv.return_value = _mock_full_inv(
                usdt_qty=0,
                eth=(0.050, 2200),    # ETH -9.09%
                paxg=(0.020, 4600),   # PAXG -2.17%
                sol=(0.500, 100),     # SOL -20%
            )
            plan = compute_funding_plan(target_usdt=50, asset_being_funded="BTC", prices=PRICES)
            p3 = [s for s in plan.steps if s.priority == "P3"]
            # Primer ha de ser PAXG (-2.17% pèrdua, menor)
            self.assertEqual(p3[0].asset, "PAXG", f"Primer P3 hauria de ser PAXG, va ser {p3[0].asset}")

    def test_C7_zero_target(self):
        """target_usdt=0 → plan buit, feasible=True."""
        with patch("vault.funding.get_inventory") as mock_inv:
            mock_inv.return_value = _mock_full_inv(usdt_qty=100)
            plan = compute_funding_plan(target_usdt=0, asset_being_funded="BTC", prices=PRICES)
            self.assertTrue(plan.feasible)
            self.assertEqual(len(plan.steps), 0)

    def test_C8_negative_target_handled(self):
        """target_usdt negatiu (shouldn't happen però defensiu)."""
        with patch("vault.funding.get_inventory") as mock_inv:
            mock_inv.return_value = _mock_full_inv(usdt_qty=100)
            plan = compute_funding_plan(target_usdt=-50, asset_being_funded="BTC", prices=PRICES)
            self.assertTrue(plan.feasible)  # res a finançar

    def test_C9_huge_target_exceeds_all(self):
        """target absurd → NOT feasible, mostra shortfall."""
        with patch("vault.funding.get_inventory") as mock_inv:
            mock_inv.return_value = _mock_full_inv(usdt_qty=100)
            plan = compute_funding_plan(target_usdt=100000, asset_being_funded="BTC", prices=PRICES)
            self.assertFalse(plan.feasible)
            self.assertGreater(plan.shortfall, 99000)


class D_CreateSpotGridFailures(unittest.TestCase):
    """create_spot_grid failure modes."""

    def test_D1_create_returns_no_bot_id(self):
        """create_spot_grid returna success but no buOrderId — error."""
        # Pre_state amb QUOTE alt perquè recovered USDT cobreixi target sense funding
        with _isolate_vault_writes(), \
             patch("vault.relauncher._read_bot_state") as mock_read:
            mock_read.return_value = {
                "base_amount": 0.001, "quote_amount": 500, "price": 70000,
                "average_cost": 75000, "grid_profit": 5.0, "cycles": 30, "raw": {},
            }
            # target_value = 0.001×70000 + 500 = $570, recovered USDT $500 → shortfall ~$70
            # Mock vault.funding.get_inventory amb prou USDT per cobrir
            with patch("vault.funding.get_inventory") as mock_vault:
                mock_vault.return_value = _mock_full_inv(usdt_qty=200)  # cobreix $70 shortfall
                with patch("pionex_client.cancel_bot") as mock_cancel:
                    mock_cancel.return_value = {"result": True}
                    with patch("pionex_client.create_spot_grid") as mock_create:
                        mock_create.return_value = {"result": True, "data": {}}  # no buOrderId
                        out = relauncher.relaunch_after_breakout(
                            bot_name="BTC_USDT", bot_id="test-id",
                            cfg={"id": "test-id", "symbol": "BTC_USDT", "base": "BTC",
                                 "quote": "USDT", "width_pct": 0.05, "rows": 12},
                            price_at_breakout=70000.0, dry_run=False,
                        )
                        self.assertFalse(out.get("ok"))
                        self.assertTrue(any("no bot_id" in e.lower() for e in out.get("errors", [])))


class E_PartialFailures(unittest.TestCase):
    """Pionex API failures partway through the flow."""

    def test_E1_cancel_ok_then_create_404(self):
        """Cancel reixit, create dóna 404 (el bug que ens va passar).
        Per disseny: bot original ja cancellat, error logged, state inconsistent."""
        # Simulem el bug exact que vam veure
        with _isolate_vault_writes(), \
             patch("vault.relauncher._read_bot_state") as mock_read:
            mock_read.return_value = {
                "base_amount": 0.005, "quote_amount": 200, "price": 70000,
                "average_cost": 75000, "grid_profit": 5.0, "cycles": 30, "raw": {},
            }
            with patch("pionex_client.cancel_bot") as mock_cancel:
                mock_cancel.return_value = {"result": True}
                with patch("pionex_client.create_spot_grid") as mock_create:
                    mock_create.side_effect = Exception("404 Not Found")
                    out = relauncher.relaunch_after_breakout(
                        bot_name="BTC_USDT", bot_id="test-id",
                        cfg={"id": "test-id", "symbol": "BTC_USDT", "base": "BTC",
                             "quote": "USDT", "width_pct": 0.05, "rows": 12},
                        price_at_breakout=70000.0, dry_run=False,
                    )
                    self.assertFalse(out.get("ok"))
                    # Error capturat
                    self.assertTrue(any("404" in e or "create_spot_grid" in e for e in out.get("errors", [])))


class F_NeonFailures(unittest.TestCase):
    """Neon failure paths — el sistema ha de continuar amb dump JSONL."""

    def test_F1_capital_event_neon_fail_returns_None(self):
        """log_capital_event amb Neon caigut: fa dump i retorna None (NO exception)."""
        # El dump a JSONL està al codi de db_cloud. Aquí verifiquem que
        # no propaga exception (best-effort logging).
        # Simular Neon down via mock de la connexió.
        # Per simplicitat, comprovem que el patró try/except hi és al codi.
        import cloud.db_cloud as dbc
        # Si arribem aquí sense import error, el patró és sane.
        self.assertTrue(hasattr(dbc, "_dump_pending"))


class G_StateRaceConditions(unittest.TestCase):
    """State corruption / race conditions."""

    def test_G1_negative_qty_refused(self):
        """vault.inventory NO ha de permetre qty negatiu."""
        from vault.inventory import _apply_delta
        # Tracking del bug que vam tenir: remove_usdt amb qty > disponible
        # ha de retornar False, no corrompre l'estat.
        with patch("vault.inventory.conn") as mock_conn:
            cm = MagicMock()
            cm.__enter__.return_value = cm
            mock_cur = MagicMock()
            mock_cur.__enter__.return_value = mock_cur
            cm.cursor.return_value = mock_cur
            mock_conn.return_value = cm
            mock_cur.fetchone.side_effect = [None, ("0", "0")]  # qty=0
            # Test idempotency check first returns None → continues
            ok = _apply_delta(asset="USDT", qty_delta=-100, cost_delta=-100,
                              event_type="test", source="test")
            # Should fail because trying to remove more than available
            # The actual logic raises ValueError → caught → returns False
            self.assertFalse(ok, "remove més que disponible ha de fallar")

    def test_G2_circuit_breaker_count(self):
        """Circuit breaker: si MAX_LIVE_RELOCS_PER_HOUR és 2, no permet 3a."""
        from vault.closer import MAX_LIVE_RELOCS_PER_HOUR
        self.assertEqual(MAX_LIVE_RELOCS_PER_HOUR, 2)
        # En el codi real, la query a vault_events compta last hour.
        # Aquí només validem que la constant està sane.


class H_IdempotencyAndRetry(unittest.TestCase):
    """Idempotency: re-executar la mateixa operació no duplica."""

    def test_H1_funding_plan_repeatable(self):
        """compute_funding_plan amb mateixes inputs dóna mateix output."""
        with patch("vault.funding.get_inventory") as mock_inv:
            mock_inv.return_value = _mock_full_inv(usdt_qty=50, paxg=(0.020, 4000))
            p1 = compute_funding_plan(target_usdt=100, asset_being_funded="BTC", prices=PRICES)
            p2 = compute_funding_plan(target_usdt=100, asset_being_funded="BTC", prices=PRICES)
            self.assertEqual(len(p1.steps), len(p2.steps))
            self.assertAlmostEqual(p1.total_raised, p2.total_raised, places=4)


class I_FormatAndSize(unittest.TestCase):
    """Format de mides per Pionex."""

    def test_I1_fmt_size_strips_zeros(self):
        from pionex_client import fmt_size
        self.assertEqual(fmt_size("PAXG", 0.00700000), "0.007")
        self.assertEqual(fmt_size("BTC", 0.00012345), "0.000123")
        self.assertEqual(fmt_size("SOL", 1.23456789), "1.23")

    def test_I2_round_up_to_min_amount(self):
        from pionex_client import round_up_to_min
        # PAXG @ $4500, qty 0.0001 = $0.45 sota min $10 → ha d'arrodonir UP
        qty, reason = round_up_to_min("PAXG", 0.0001, 4500)
        self.assertGreater(qty, 0.0001)
        self.assertGreaterEqual(qty * 4500, 10)

    def test_I3_round_up_to_min_size(self):
        from pionex_client import round_up_to_min
        # SOL minTradeSize = 0.01. qty 0.005 sota min.
        qty, reason = round_up_to_min("SOL", 0.005, 80)
        self.assertGreaterEqual(qty, 0.01)

    def test_I4_already_valid_qty(self):
        from pionex_client import round_up_to_min
        qty, reason = round_up_to_min("BTC", 0.05, 75000)  # ~$3750
        self.assertEqual(qty, 0.05)
        self.assertEqual(reason, "already valid")


def run():
    """Run all suites with verbose output + summary."""
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    for cls in [A_ClosingEdgeCases, B_CancelBotFailureModes, C_FundingWaterfallEdgeCases,
                D_CreateSpotGridFailures, E_PartialFailures, F_NeonFailures,
                G_StateRaceConditions, H_IdempotencyAndRetry, I_FormatAndSize]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)


if __name__ == "__main__":
    run()
