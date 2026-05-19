"""
Model de fees Pionex Spot Grid — exacte segons documentació.

Fees:
  - Trading fee: 0.05% (TAKER) per fill (cada compra/venda)
  - No maker rebate per a bots grid (les ordres dels bots compten com taker)
  - Fee es paga en la "output currency" de cada trade

Per simplicitat al backtest:
  - Sumem fees per separat (no implementem el reserve mechanism — equivalent net)
  - El gridProfit acumulat = (sum sell_price × vol) − (sum buy_price × vol) − fees
"""
from __future__ import annotations

from dataclasses import dataclass

# Pionex spot fees (https://www.pionex.com/blog/pionex-fees/)
TRADING_FEE_RATE = 0.0005  # 0.05% per fill


@dataclass
class FillCost:
    """Cost d'un fill individual."""
    notional: float       # preu × volum
    fee: float            # fee absoluta en quote currency (USDT)


def fee_for_fill(price: float, volume_base: float) -> FillCost:
    """Fee d'un fill (qualsevol direcció), expressada en quote currency."""
    notional = price * volume_base
    fee = notional * TRADING_FEE_RATE
    return FillCost(notional=notional, fee=fee)


def fee_for_market_trade(notional_usdt: float) -> float:
    """Fee per un trade market sense saber preu/volum individuals.
    Útil per recolocacions on només sabem el notional total a rebalancejar."""
    return notional_usdt * TRADING_FEE_RATE


def recolocation_cost(
    old_inventory_base: float,
    old_inventory_quote: float,
    old_price: float,
    new_top: float,
    new_bottom: float,
    new_rows: int,
    new_price: float,
) -> tuple[float, float, float]:
    """Estima el cost en USDT d'una recolocació de grid.

    Pionex internament:
      1. Cancel·la pending orders antics (free)
      2. Calcula composició òptima d'inventari per al NOU rang
      3. Compra/ven al market per ajustar inventari (paga 0.05% del volum rebalancejat)
      4. Crea pending orders nous al rang nou (free)

    Composició òptima: linealment proporcional a la posició del preu al rang.
      preu prop bottom → 100% base
      preu prop top → 100% quote

    Returns:
        (fee_usdt, base_delta, quote_delta)
    """
    total_value = old_inventory_base * old_price + old_inventory_quote

    if new_top == new_bottom:
        return 0.0, 0.0, 0.0

    position_pct = (new_price - new_bottom) / (new_top - new_bottom)
    position_pct = max(0.0, min(1.0, position_pct))

    target_quote_pct = position_pct
    target_base_pct = 1.0 - position_pct

    target_quote_value = total_value * target_quote_pct
    target_base_value = total_value * target_base_pct
    target_base_units = target_base_value / new_price if new_price > 0 else 0

    base_delta = target_base_units - old_inventory_base
    quote_delta = target_quote_value - old_inventory_quote

    market_volume = abs(base_delta) * new_price
    fee = fee_for_market_trade(market_volume)

    return fee, base_delta, quote_delta


if __name__ == "__main__":
    c = fee_for_fill(price=80_000, volume_base=0.001)
    print(f"Fill BTC 0.001 @ $80k: notional=${c.notional:.2f}, fee=${c.fee:.4f}")
    assert abs(c.fee - 0.04) < 1e-6
    fee, db, dq = recolocation_cost(
        old_inventory_base=0.005, old_inventory_quote=200,
        old_price=80_000, new_top=82_000, new_bottom=78_000, new_rows=12, new_price=80_000,
    )
    print(f"Reloc center: fee=${fee:.4f}, base_delta={db:.6f}, quote_delta={dq:.2f}")
    fee, db, dq = recolocation_cost(
        old_inventory_base=0.005, old_inventory_quote=200,
        old_price=80_000, new_top=78_000, new_bottom=74_000, new_rows=12, new_price=74_500,
    )
    print(f"Reloc near bottom: fee=${fee:.4f}, base_delta={db:.6f}, quote_delta={dq:.2f}")
    print("\nfee_model OK")
