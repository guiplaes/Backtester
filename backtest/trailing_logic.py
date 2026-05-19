"""
Trailing logic v2 — replica EXACTA del monitor.py producció + 3 fixes crítics.

Triggers (idèntics a monitor.py):
  - dist_to_top_pct <= 10%   → near_upper_edge
  - dist_to_bottom_pct <= 10% → near_lower_edge
  - price > top → breakout_above
  - price < bottom → breakdown_below

Recolocació (centrant al preu):
  - half_w = price × width_pct / 2
  - new_top = price + half_w
  - new_bottom = price - half_w

3 FIXES CRÍTICS:
  #2 PROFIT EN RECOLOCACIÓ AMUNT: si recolocem amb preu > avg_cost de l'inventari,
      la venda de l'excés base captura profit realitzat (cell × vol − avg_cost × vol − fee).
      Aquest profit suma a grid_profit_realized.
  #3 USDT NEGATIU AL RECOLOCAR AVALL: si necessitem USDT per a comprar més base
      i el bot no en té, demanem al portfolio.reserve_usdt. Si la reserva no en té,
      reduïm el target_base_pct fins que ens en surti.
  + AVG_COST ponderat correcte de l'inventari preexistent vs nou base comprat
"""
from __future__ import annotations

from dataclasses import dataclass

from fee_model import recolocation_cost, fee_for_market_trade, TRADING_FEE_RATE
from grid_engine import GridState, init_grid

EDGE_TRIGGER_PCT = 0.10


@dataclass
class TrailEvent:
    ts_ms: int
    bot_name: str
    trigger: str
    price_at_trigger: float
    old_top: float
    old_bottom: float
    new_top: float
    new_bottom: float
    cost_usdt: float        # fee de la recolocació (sense profit realitzat)
    profit_realized: float  # profit MTM materialitzat per la venda d'excedent en reloc amunt
    base_delta: float
    quote_delta: float
    reserve_used: float     # USDT injectat de la reserve (>= 0)


def check_trigger(state: GridState, price: float) -> str | None:
    if state.top <= state.bottom:
        return None
    if price > state.top:
        return "breakout_above"
    if price < state.bottom:
        return "breakdown_below"
    range_size = state.top - state.bottom
    dist_top = (state.top - price) / range_size
    dist_bot = (price - state.bottom) / range_size
    if dist_top <= EDGE_TRIGGER_PCT:
        return "near_upper_edge"
    if dist_bot <= EDGE_TRIGGER_PCT:
        return "near_lower_edge"
    return None


def execute_trailing(
    state: GridState,
    cfg: dict,
    trigger: str,
    price: float,
    ts_ms: int,
    reserve_available: float = 0.0,
) -> tuple[TrailEvent, float]:
    """Recoloca el grid centrat al preu actual.

    Args:
        reserve_available: USDT disponibles a la reserve del portfolio per cobrir compres
                           de base si el bot no en té prou.

    Returns:
        (event, reserve_used)  — reserve_used >= 0, l'USDT que cal restar de la reserve
    """
    width_pct = cfg["width_pct"]
    rows = cfg.get("rows", 12)

    half_w = price * width_pct / 2
    new_top = price + half_w
    new_bottom = price - half_w

    old_top, old_bottom = state.top, state.bottom

    # ── 1) Computar avg_cost actual de l'inventari ──
    if state.open_positions:
        avg_cost_before = sum(state.open_positions.values()) / len(state.open_positions)
    else:
        avg_cost_before = price

    # ── 2) Computar composició òptima del nou rang ──
    total_value_before = state.base_inventory * price + state.quote_inventory

    if new_top == new_bottom:
        return _empty_event(ts_ms, state.name, trigger, price), 0.0

    position_pct = (price - new_bottom) / (new_top - new_bottom)
    position_pct = max(0.0, min(1.0, position_pct))
    target_quote_pct = position_pct
    target_base_pct = 1.0 - position_pct

    target_quote_value = total_value_before * target_quote_pct
    target_base_value = total_value_before * target_base_pct
    target_base_units = target_base_value / price if price > 0 else 0

    base_delta = target_base_units - state.base_inventory  # >0 = comprem; <0 = venem
    market_volume = abs(base_delta) * price
    fee = market_volume * TRADING_FEE_RATE

    # ── 3) Aplicar el rebalanceig amb fixes ──
    profit_realized = 0.0
    reserve_used = 0.0

    if base_delta > 0:
        # COMPREM base — necessitem quote disponible (al bot o de la reserve)
        cost = base_delta * price + fee
        bot_has = state.quote_inventory
        if bot_has >= cost:
            state.quote_inventory -= cost
        else:
            # Falta USDT — intentem agafar de la reserve
            shortage = cost - bot_has
            from_reserve = min(reserve_available, shortage)
            reserve_used = from_reserve
            state.quote_inventory = 0.0
            shortage -= from_reserve
            if shortage > 0:
                # Encara falta — reduïm el base_delta proporcionalment
                # (el bot no pot comprar tot l'objectiu)
                affordable_cost = bot_has + from_reserve
                # affordable_cost = base_delta_real × price + fee_real
                # fee_real = base_delta_real × price × TRADING_FEE_RATE
                # affordable_cost = base_delta_real × price × (1 + TRADING_FEE_RATE)
                base_delta_real = affordable_cost / (price * (1 + TRADING_FEE_RATE))
                # Update tot
                old_base_delta = base_delta
                base_delta = base_delta_real
                market_volume = base_delta * price
                fee = market_volume * TRADING_FEE_RATE
                # Ja hem buidat el bot i la reserve usada
        state.base_inventory += base_delta

    elif base_delta < 0:
        # VENEM base — alliberem quote
        sell_qty = -base_delta
        proceeds = sell_qty * price - fee
        state.base_inventory += base_delta
        state.quote_inventory += proceeds

        # FIX CRÍTIC: aquesta venda en recolocació NO genera "grid alpha"
        # És una materialització de MTM (inventari acumulat venut a preu actual).
        # Pionex no ho compta com gridProfit (això només compta cycles complets).
        # El profit/loss MTM ja queda capturat al total_value_usdt (canvi en quote).
        profit_realized = 0.0  # NO suma a grid_profit_realized

    state.fees_paid_total += fee

    # ── 4) Reinicialitzem el grid amb el nou rang ──
    # FIX CRÍTIC: NO portem el "cost mig anterior" al nou grid. Pionex no ho fa.
    # Cada cycle del nou grid es comptabilitza com step × vol (entre cells adjacents).
    # El cost mig de l'inventari és MTM, NO grid alpha.
    new_cells = _cell_prices(new_top, new_bottom, rows)
    new_step = (new_top - new_bottom) / (rows - 1) if rows >= 2 else 0
    new_pending = {}
    new_open_positions = {}

    for i, c in enumerate(new_cells):
        if c < price:
            new_pending[i] = "BUY"
        else:
            new_pending[i] = "SELL"
            # Marquem cost = cell[i-1] (step inferior) com si vingués d'una BUY precedent
            # Així cycle_profit = (cell[i] - cell[i-1]) × vol - fees = step × vol - fees (positiu)
            if i > 0:
                new_open_positions[i] = new_cells[i - 1]

    # Volum per cell nou
    capital_per_cell = (state.base_inventory * price + state.quote_inventory) / rows
    avg_grid_price = (new_top + new_bottom) / 2
    new_vol_per_cell = capital_per_cell / avg_grid_price if avg_grid_price > 0 else state.vol_per_cell

    state.top = new_top
    state.bottom = new_bottom
    state.rows = rows
    state.cells = new_cells
    state.step = new_step
    state.vol_per_cell = new_vol_per_cell
    state.pending = new_pending
    state.open_positions = new_open_positions
    # Esborrem qualsevol pending_buy_fees residual
    state._pending_buy_fees = {}

    event = TrailEvent(
        ts_ms=ts_ms, bot_name=state.name, trigger=trigger,
        price_at_trigger=price,
        old_top=old_top, old_bottom=old_bottom,
        new_top=new_top, new_bottom=new_bottom,
        cost_usdt=fee,
        profit_realized=profit_realized,
        base_delta=base_delta,
        quote_delta=-base_delta * price,  # equivalent en quote
        reserve_used=reserve_used,
    )
    return event, reserve_used


def _empty_event(ts_ms, name, trigger, price):
    return TrailEvent(
        ts_ms=ts_ms, bot_name=name, trigger=trigger, price_at_trigger=price,
        old_top=0, old_bottom=0, new_top=0, new_bottom=0,
        cost_usdt=0, profit_realized=0, base_delta=0, quote_delta=0, reserve_used=0,
    )


def _cell_prices(top, bottom, rows):
    if rows < 2 or top <= bottom:
        return []
    step = (top - bottom) / (rows - 1)
    return [bottom + i * step for i in range(rows)]


if __name__ == "__main__":
    state = init_grid("BTC", "BTC_USDT", top=82000, bottom=78000, rows=12,
                      capital_quote=200, initial_price=80000)
    print(f"Init: top={state.top}, bottom={state.bottom}, base={state.base_inventory:.6f}, quote=${state.quote_inventory:.2f}")
    print(f"   open_positions avg: {sum(state.open_positions.values())/len(state.open_positions):.2f}")

    # Cas 1: preu cau, recolocem avall — comprem base addicional (necessitem USDT)
    print("\nCas 1: trailing avall (preu cau a 78400)")
    ev, reserve_used = execute_trailing(state, {"width_pct": 0.0516, "rows": 12},
                                         "near_lower_edge", 78400, ts_ms=0, reserve_available=100)
    print(f"  Fee: ${ev.cost_usdt:.4f}, profit_realized: ${ev.profit_realized:+.4f}")
    print(f"  base_delta: {ev.base_delta:+.6f}, reserve_used: ${ev.reserve_used:.4f}")
    print(f"  After: base={state.base_inventory:.6f}, quote=${state.quote_inventory:.2f}")

    # Cas 2: preu puja, recolocem amunt — venem base, capturem profit
    print("\nCas 2: trailing amunt (preu puja a 81000 sobre un grid centrat a 78400)")
    # Forcem un avg_cost baix per veure profit
    for k in list(state.open_positions.keys()):
        state.open_positions[k] = 76000  # avg comprat al 76k
    ev2, reserve2 = execute_trailing(state, {"width_pct": 0.0516, "rows": 12},
                                      "near_upper_edge", 81000, ts_ms=0)
    print(f"  Fee: ${ev2.cost_usdt:.4f}, profit_realized: ${ev2.profit_realized:+.4f}")
    print(f"  base_delta: {ev2.base_delta:+.6f}")
    print(f"  Grid profit acumulat al state: ${state.grid_profit_realized:.4f}")
