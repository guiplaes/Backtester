"""
vault/funding.py — Waterfall de finançament P1 → P2 → P3 → P4.

PLAN (compute_funding_plan): pure logic, NO execució. Retorna el pla detallat
de què caldria vendre/treure per obtenir X USDT, ranked per la regla del
waterfall. Aquest pla és testable, loggable, i visible a TG abans d'executar.

EXECUTE (execute_funding_plan): aplica el pla.
  - Per cada step, crida l'API de Pionex per vendre vault base via market order
    (pionex_orders_new_order) o simplement treu USDT del vault
  - Actualitza vault_inventory amb les funcions del mòdul inventory

REGLES (recordatori):
  P1: vendre vault de QUALSEVOL asset en profit (preu actual > avg_cost+margin)
      ranked per profit_ratio desc, parcial OK
  P2: USDT del vault_inventory (asset='USDT')
  P3: vendre vault en pèrdua (ranked per loss% asc — el de MENOR pèrdua primer),
      EXCLOENT l'asset que estem finançant (es reserva per P4)
  P4: vendre base de l'asset que estem finançant (últim recurs, realitza pèrdua)

Combinacions parcials permeses: P1 pot aportar $50, P2 $80, P3 $40, P4 $30
fins arribar al target. Si fins i tot P4 no cobreix → abort.

PROFIT_MARGIN: 0.5% per defecte (0.005). Cobreix fees compra+venda (~0.1%)
i slippage. Si un asset té profit_ratio < margin, NO comptem com profitable.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vault.inventory import get_inventory, add_usdt, remove_usdt, remove_base

log = logging.getLogger("vault.funding")

# Marge de seguretat per considerar un vault "profitable" (cobreix fees+slippage)
DEFAULT_PROFIT_MARGIN = 0.005  # 0.5%


@dataclass
class FundingStep:
    """Un step del waterfall — quantitat a obtenir d'una font concreta."""
    priority: str           # 'P1', 'P2', 'P3', 'P4'
    source_type: str        # 'vault_profit' | 'usdt_reserve' | 'vault_loss' | 'own_asset'
    asset: str              # 'BTC', 'ETH', 'USDT', etc.
    qty_to_sell: float      # quantitat de base (o USDT amount per P2)
    expected_usdt: float    # USDT que esperem obtenir (post-fees aprox)
    avg_cost: float | None  # avg_cost de la porció
    price_now: float        # preu actual usat per la simulació
    profit_pct: float       # % profit/loss vs avg_cost (positiu=profit)
    realized_pnl: float     # pèrdua o guany que realitzarà aquest step

    def __repr__(self):
        sign = "+" if self.realized_pnl >= 0 else ""
        return (f"<{self.priority} {self.source_type} {self.asset}: "
                f"sell {self.qty_to_sell:.6f} → ${self.expected_usdt:.2f} "
                f"({sign}{self.realized_pnl:.2f} pnl, {self.profit_pct:+.2f}%)>")


@dataclass
class FundingPlan:
    """El pla complet — múltiples steps que sumen target_usdt."""
    target_usdt: float
    asset_being_funded: str
    steps: list[FundingStep] = field(default_factory=list)
    total_raised: float = 0.0
    feasible: bool = False
    shortfall: float = 0.0

    def summary(self) -> str:
        if not self.steps:
            return f"Empty plan (target ${self.target_usdt:.2f})"
        lines = [
            f"Target: ${self.target_usdt:.2f} per finançar {self.asset_being_funded}",
            f"Raised: ${self.total_raised:.2f} ({'✓ feasible' if self.feasible else f'✗ shortfall ${self.shortfall:.2f}'})",
            "Steps:",
        ]
        for s in self.steps:
            lines.append(f"  {s}")
        return "\n".join(lines)


def compute_funding_plan(target_usdt: float, asset_being_funded: str,
                         prices: dict[str, float],
                         profit_margin: float = DEFAULT_PROFIT_MARGIN,
                         fee_rate: float = 0.0005) -> FundingPlan:
    """Calcula el pla de finançament aplicant el waterfall P1→P2→P3→P4.

    Args:
        target_usdt: import necessari
        asset_being_funded: asset per al qual creem nou bot (excloent de P3)
        prices: {asset: preu_actual_usdt}
        profit_margin: marge per considerar profitable (default 0.5%)
        fee_rate: fee per trade (default 0.05% Pionex)

    Returns:
        FundingPlan amb steps, total_raised, feasible.
    """
    plan = FundingPlan(target_usdt=target_usdt, asset_being_funded=asset_being_funded)
    if target_usdt <= 0:
        plan.feasible = True
        return plan

    inv = get_inventory()
    remaining = target_usdt

    # ── P1: vault profit (qualsevol asset) ───────────────────────
    p1_candidates = []
    for asset, d in inv.items():
        if asset == "USDT" or d["qty"] <= 0 or d["avg_cost"] is None:
            continue
        price = prices.get(asset, 0)
        if price <= 0:
            continue
        avg = d["avg_cost"]
        profit_ratio = (price - avg) / avg
        if profit_ratio > profit_margin:
            p1_candidates.append((profit_ratio, asset, d, price))
    p1_candidates.sort(key=lambda x: -x[0])  # més profit primer

    for ratio, asset, d, price in p1_candidates:
        if remaining <= 0:
            break
        max_usdt = d["qty"] * price * (1 - fee_rate)
        take_usdt = min(remaining, max_usdt)
        qty_to_sell = take_usdt / (price * (1 - fee_rate))
        realized = qty_to_sell * (price - d["avg_cost"]) - qty_to_sell * price * fee_rate
        plan.steps.append(FundingStep(
            priority="P1", source_type="vault_profit", asset=asset,
            qty_to_sell=qty_to_sell, expected_usdt=take_usdt,
            avg_cost=d["avg_cost"], price_now=price,
            profit_pct=ratio * 100, realized_pnl=realized,
        ))
        plan.total_raised += take_usdt
        remaining -= take_usdt

    # ── P2: USDT inventory ──────────────────────────────────────
    if remaining > 0:
        usdt_avail = inv.get("USDT", {}).get("qty", 0)
        if usdt_avail > 0:
            take = min(remaining, usdt_avail)
            plan.steps.append(FundingStep(
                priority="P2", source_type="usdt_reserve", asset="USDT",
                qty_to_sell=take, expected_usdt=take,
                avg_cost=1.0, price_now=1.0,
                profit_pct=0, realized_pnl=0,
            ))
            plan.total_raised += take
            remaining -= take

    # ── P3: vault en pèrdua (smallest loss% asc, excloent asset_being_funded) ─
    if remaining > 0:
        p3_candidates = []
        for asset, d in inv.items():
            if asset in ("USDT", asset_being_funded):
                continue
            if d["qty"] <= 0 or d["avg_cost"] is None:
                continue
            price = prices.get(asset, 0)
            if price <= 0:
                continue
            avg = d["avg_cost"]
            loss_ratio = (price - avg) / avg  # negatiu si pèrdua
            # Acceptem fins el marge negatiu (no profitable)
            if loss_ratio <= profit_margin:
                p3_candidates.append((loss_ratio, asset, d, price))
        # Ordenem ASC per loss (el menys negatiu primer = menor pèrdua %)
        p3_candidates.sort(key=lambda x: -x[0])  # més proper a 0 primer

        for loss, asset, d, price in p3_candidates:
            if remaining <= 0:
                break
            max_usdt = d["qty"] * price * (1 - fee_rate)
            take_usdt = min(remaining, max_usdt)
            qty_to_sell = take_usdt / (price * (1 - fee_rate))
            realized = qty_to_sell * (price - d["avg_cost"]) - qty_to_sell * price * fee_rate
            plan.steps.append(FundingStep(
                priority="P3", source_type="vault_loss", asset=asset,
                qty_to_sell=qty_to_sell, expected_usdt=take_usdt,
                avg_cost=d["avg_cost"], price_now=price,
                profit_pct=loss * 100, realized_pnl=realized,
            ))
            plan.total_raised += take_usdt
            remaining -= take_usdt

    # ── P4: vendre base de l'asset que estem finançant (últim recurs) ──
    if remaining > 0 and asset_being_funded != "USDT":
        d = inv.get(asset_being_funded)
        if d and d["qty"] > 0 and d["avg_cost"] is not None:
            price = prices.get(asset_being_funded, 0)
            if price > 0:
                max_usdt = d["qty"] * price * (1 - fee_rate)
                take_usdt = min(remaining, max_usdt)
                qty_to_sell = take_usdt / (price * (1 - fee_rate))
                ratio = (price - d["avg_cost"]) / d["avg_cost"]
                realized = qty_to_sell * (price - d["avg_cost"]) - qty_to_sell * price * fee_rate
                plan.steps.append(FundingStep(
                    priority="P4", source_type="own_asset", asset=asset_being_funded,
                    qty_to_sell=qty_to_sell, expected_usdt=take_usdt,
                    avg_cost=d["avg_cost"], price_now=price,
                    profit_pct=ratio * 100, realized_pnl=realized,
                ))
                plan.total_raised += take_usdt
                remaining -= take_usdt

    plan.shortfall = max(0, remaining)
    plan.feasible = plan.shortfall < 0.01  # tolerància $0.01

    return plan


def execute_funding_plan(plan: FundingPlan, idempotency_prefix: str,
                         dry_run: bool = False) -> dict:
    """Executa un FundingPlan reixit.

    Per cada step:
      - P1, P3, P4: vendre base via Pionex market order, afegir USDT al vault
      - P2: només moure des de vault USDT (no toca Pionex)

    Args:
        plan: pla calculat amb compute_funding_plan
        idempotency_prefix: per generar keys úniques (ex: 'fund_BTC_20260523_1530')
        dry_run: True per simular sense executar

    Returns:
        {ok, total_usdt_raised, errors: list, executions: list}
    """
    if not plan.feasible:
        return {"ok": False, "error": "plan not feasible", "shortfall": plan.shortfall}

    executions = []
    errors = []
    total_raised = 0.0

    # Importem aquí per evitar cicle si pionex_client falla
    try:
        from pionex_client import _post_signed, get_current_price
    except Exception as e:
        return {"ok": False, "error": f"pionex_client import: {e}"}

    for step in plan.steps:
        idem_key = f"{idempotency_prefix}_{step.priority}_{step.asset}"
        log.info(f"Executing {step}")
        if dry_run:
            executions.append({"step": str(step), "result": "DRY_RUN"})
            total_raised += step.expected_usdt
            continue

        try:
            if step.priority == "P2":
                # USDT inventory: només actualitza vault_inventory
                ok = remove_usdt(
                    amount=step.expected_usdt,
                    source=f"funding/{idempotency_prefix}",
                    idempotency_key=idem_key,
                    notes=f"P2 USDT reserve → finançament {plan.asset_being_funded}",
                )
                if ok:
                    total_raised += step.expected_usdt
                    executions.append({"step": str(step), "result": "ok"})
                else:
                    errors.append(f"{step.priority} {step.asset}: remove_usdt failed")
            else:
                # P1/P3/P4: market sell base via Pionex
                symbol = f"{step.asset}_USDT"
                body = {
                    "symbol": symbol,
                    "side": "SELL",
                    "type": "MARKET",
                    "size": f"{step.qty_to_sell:.8f}",
                }
                # Crida Pionex market order
                resp = _post_signed("/api/v1/trade/order", body)
                if not resp.get("result"):
                    errors.append(f"{step.priority} {step.asset}: Pionex SELL failed: {resp}")
                    continue
                # Llegir preu real de fill (potser diferent del price_now si slippage)
                # Per simplicitat usem step.expected_usdt; el reconcile diari ajustarà
                ok1 = remove_base(
                    asset=step.asset, qty=step.qty_to_sell,
                    source=f"funding/{idempotency_prefix}",
                    idempotency_key=idem_key + "_rmbase",
                    notes=f"{step.priority} sell at ${step.price_now:.4f}",
                )
                ok2 = add_usdt(
                    amount=step.expected_usdt,
                    source=f"funding/{idempotency_prefix}/{step.asset}",
                    idempotency_key=idem_key + "_addusdt",
                    notes=f"Proceeds from {step.priority} sell of {step.asset}",
                )
                if ok1 and ok2:
                    total_raised += step.expected_usdt
                    executions.append({"step": str(step), "result": "ok", "pionex": resp})
                else:
                    errors.append(f"{step.priority} {step.asset}: vault update failed (ok1={ok1} ok2={ok2})")
        except Exception as e:
            errors.append(f"{step.priority} {step.asset}: exception {e}")
            log.error(f"  {step}: {e}")

    return {
        "ok": len(errors) == 0,
        "total_usdt_raised": total_raised,
        "executions": executions,
        "errors": errors,
    }
