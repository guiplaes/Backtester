"""
Grid Engine v2 — Model Pionex Spot Grid FIDEL.

Mecànica real Pionex (https://www.pionex.com/blog/pionex-grid-trading-bot/):
  - N cells indexed [0..rows-1] entre bottom i top, equiespaiades
  - step = (top - bottom) / (rows - 1)
  - Inicialment:
      cells[i] < initial_price -> BUY pending (esperem preu baixi)
      cells[i] >= initial_price -> SELL pending (tenim inventari per vendre)
  - Quan BUY pendent a cell i s'executa (preu baixa fins cells[i]):
      cells[i] queda INACTIVE
      cells[i+1] passa a SELL pending (preu necessita pujar 1 step per vendre)
      open_positions[i+1] = cells[i]  (cost de la compra)
  - Quan SELL pendent a cell j s'executa (preu puja fins cells[j]):
      cells[j] queda INACTIVE
      cells[j-1] passa a BUY pending (preu necessita baixar 1 step per recomprar)
      cycle_profit = (cells[j] - open_positions[j]) × vol_per_cell − fee  ⇒ +step × vol − fee
      paired_cycles += 1

Resultat: cada cycle complet (BUY a i + SELL a i+1) genera step × vol_per_cell.

Per processar una barra M1:
  - Identifiquem cells dins [bar.low, bar.high]
  - Apliquem ordre intra-minut realista:
      Up bar:   bar va O -> L -> H. PRIMER BUYs entre [low, open] des prop d'open;
                                     DESPRÉS SELLs entre [open, high] des prop d'open.
      Down bar: bar va O -> H -> L. PRIMER SELLs entre [open, high];
                                     DESPRÉS BUYs entre [low, open].
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from fee_model import TRADING_FEE_RATE


@dataclass
class GridFill:
    ts_ms: int
    cell_index: int
    cell_price: float
    side: str            # "BUY" o "SELL"
    volume_base: float
    fee_quote: float
    cycle_profit: float  # 0 si no és el segon costat d'un cycle


@dataclass
class GridState:
    """Estat complet d'un grid simulat (model Pionex fidel)."""
    name: str
    symbol: str
    top: float
    bottom: float
    rows: int

    # Cells: llista de preus indexed per int
    cells: list = field(default_factory=list)
    step: float = 0.0
    vol_per_cell: float = 0.0  # quantitat de base per cell (fixed at init)

    # Estat per cell index: "BUY" | "SELL" | "INACTIVE"
    pending: dict = field(default_factory=dict)

    # Inventari TOTAL del bot
    base_inventory: float = 0.0
    quote_inventory: float = 0.0

    # Cost de compra registrat per cell index (per calcular cycle profit a SELLs)
    # Si cell i té open_positions[i] = X, vol dir que esperem vendre a cells[i] amb cost X
    open_positions: dict = field(default_factory=dict)

    # Profit + costs
    grid_profit_realized: float = 0.0
    fees_paid_total: float = 0.0
    paired_cycles: int = 0
    fills_count: int = 0

    fills_history: list = field(default_factory=list)


def cell_prices(top: float, bottom: float, rows: int) -> list[float]:
    if rows < 2 or top <= bottom:
        return []
    step = (top - bottom) / (rows - 1)
    return [bottom + i * step for i in range(rows)]


def init_grid(
    name: str,
    symbol: str,
    top: float,
    bottom: float,
    rows: int,
    capital_quote: float,
    initial_price: float,
) -> GridState:
    """Inicialitza un grid amb capital donat (en quote = USDT).

    MODEL PIONEX FIDEL:
      cycle_profit = step × vol − 2×fees (SEMPRE POSITIU)
      No usem "cost mig de l'inventari" per calcular cycle profit — això és MTM,
      no grid alpha. Pionex els separa.
    """
    cells = cell_prices(top, bottom, rows)
    if not cells:
        raise ValueError(f"Invalid grid params: top={top}, bottom={bottom}, rows={rows}")

    step = (top - bottom) / (rows - 1)

    pending = {}
    n_sells = 0
    n_buys = 0
    for i, p in enumerate(cells):
        if p < initial_price:
            pending[i] = "BUY"
            n_buys += 1
        else:
            pending[i] = "SELL"
            n_sells += 1

    capital_per_cell = capital_quote / rows
    avg_price = (top + bottom) / 2
    vol_per_cell = capital_per_cell / avg_price

    base_inv = vol_per_cell * n_sells
    buy_cells = [cells[i] for i in range(rows) if pending[i] == "BUY"]
    quote_inv = sum(buy_cells) * vol_per_cell

    state = GridState(
        name=name, symbol=symbol,
        top=top, bottom=bottom, rows=rows,
        cells=cells, step=step, vol_per_cell=vol_per_cell,
        pending=pending,
        base_inventory=base_inv,
        quote_inventory=quote_inv,
    )

    # FIX BUG CRÍTIC: les SELLs pendents inicials representen inventari que es vendrà.
    # Quan es venen, el cycle_profit que Pionex registra és step × vol - fees
    # (el preu de la "BUY teòrica" precedent a aquesta SELL = cell[i-1] = cell[i] - step).
    # NO usem initial_price com a cost — això confondria grid alpha amb MTM.
    for i, side in pending.items():
        if side == "SELL" and i > 0:
            # Marquem com si la BUY precedent estigués a cell[i-1] (step inferior)
            state.open_positions[i] = cells[i - 1]
        elif side == "SELL" and i == 0:
            # Cas edge: cell 0 SELL (impossible normalment), no comptabilitzem cycle
            pass

    return state


def process_bar(state: GridState, bar: dict, ts_ms: int) -> list[GridFill]:
    """Processa una barra M1 sobre el grid model Pionex fidel."""
    fills = []
    bar_open = bar["open"]
    bar_high = bar["high"]
    bar_low = bar["low"]
    bar_close = bar["close"]

    # Cells dins el rang del bar amb pending state
    candidates = []
    for i, p in enumerate(state.cells):
        if bar_low <= p <= bar_high and state.pending.get(i) in ("BUY", "SELL"):
            candidates.append((i, p, state.pending[i]))

    if not candidates:
        return fills

    # Ordre d'execució intra-minut (simulació del path)
    if bar_close >= bar_open:
        # Up bar: O -> L -> H
        # Phase 1: anar de open a low -> fillem BUYs entre [low, open]
        buys_p1 = sorted(
            [(i, p, s) for (i, p, s) in candidates if s == "BUY" and p <= bar_open],
            key=lambda x: -x[1],   # des de prop d'open descendint cap a low
        )
        # Phase 2: anar de low a high -> fillem SELLs entre [open, high]
        sells_p2 = sorted(
            [(i, p, s) for (i, p, s) in candidates if s == "SELL" and p >= bar_open],
            key=lambda x: x[1],    # des de prop d'open ascendint cap a high
        )
        order = buys_p1 + sells_p2
    else:
        # Down bar: O -> H -> L
        sells_p1 = sorted(
            [(i, p, s) for (i, p, s) in candidates if s == "SELL" and p >= bar_open],
            key=lambda x: x[1],
        )
        buys_p2 = sorted(
            [(i, p, s) for (i, p, s) in candidates if s == "BUY" and p <= bar_open],
            key=lambda x: -x[1],
        )
        order = sells_p1 + buys_p2

    vol = state.vol_per_cell

    for idx, cell_price, side in order:
        if vol <= 0:
            continue
        if state.pending.get(idx) != side:
            continue  # ja l'hem fillat aquest minut, evitem doble
        notional = cell_price * vol
        fee = notional * TRADING_FEE_RATE
        cycle_profit = 0.0

        if side == "BUY":
            cost = notional + fee
            if state.quote_inventory < cost:
                continue
            state.quote_inventory -= cost
            state.base_inventory += vol
            state.pending[idx] = "INACTIVE"

            # Crea SELL pending a cell idx+1 (si existeix)
            # IMPORTANT (FIX #6): guardem TANT el cost_price com la buy_fee per quan
            # vingui el SELL del cycle i poder descomptar ambdues fees.
            if idx + 1 < state.rows:
                state.pending[idx + 1] = "SELL"
                state.open_positions[idx + 1] = cell_price  # cost mig per cycle
                # Guardem buy_fee separat per descomptar quan SELL tanqui cycle
                state._pending_buy_fees = getattr(state, '_pending_buy_fees', {})
                state._pending_buy_fees[idx + 1] = fee

            state.fees_paid_total += fee
            state.fills_count += 1
            fills.append(GridFill(ts_ms, idx, cell_price, "BUY", vol, fee, 0.0))

        elif side == "SELL":
            if state.base_inventory < vol:
                continue
            state.base_inventory -= vol
            proceeds = notional - fee
            state.quote_inventory += proceeds
            state.pending[idx] = "INACTIVE"

            # Calcular cycle profit
            buy_cost = state.open_positions.pop(idx, None)
            if buy_cost is not None:
                # FIX #6: descomptar TANT la BUY fee com la SELL fee
                pending_buy_fees = getattr(state, '_pending_buy_fees', {})
                buy_fee = pending_buy_fees.pop(idx, 0.0)
                cycle_profit = (cell_price - buy_cost) * vol - fee - buy_fee
                state.grid_profit_realized += cycle_profit
                state.paired_cycles += 1

            # Crea BUY pending a cell idx-1 (si existeix)
            if idx - 1 >= 0:
                state.pending[idx - 1] = "BUY"

            state.fees_paid_total += fee
            state.fills_count += 1
            fills.append(GridFill(ts_ms, idx, cell_price, "SELL", vol, fee, cycle_profit))

    state.fills_history.extend(fills)
    return fills


def get_total_value_usdt(state: GridState, current_price: float) -> float:
    return state.base_inventory * current_price + state.quote_inventory


def get_inventory_mtm(state: GridState, current_price: float, initial_capital: float) -> float:
    total_value = get_total_value_usdt(state, current_price)
    return (total_value - initial_capital) - state.grid_profit_realized


if __name__ == "__main__":
    # Smoke test v2
    state = init_grid(
        name="BTC_USDT", symbol="BTC_USDT",
        top=82000, bottom=78000, rows=12,
        capital_quote=200, initial_price=80000,
    )
    print(f"Grid v2 creat: rows={state.rows}, step=${state.step:.2f}, vol_per_cell={state.vol_per_cell:.6f}")
    print(f"  Base: {state.base_inventory:.6f}, Quote: ${state.quote_inventory:.2f}")
    print(f"  Total value: ${get_total_value_usdt(state, 80000):.2f}")
    print(f"  Initial pending: {sum(1 for v in state.pending.values() if v=='BUY')} BUYs, {sum(1 for v in state.pending.values() if v=='SELL')} SELLs")

    # Barra que travessa cells (up bar, low<80k, high>80k)
    print("\nBar 1: 80000 -> 79500 -> 80500 -> 80200 (up bar):")
    bar = {"open": 80000, "high": 80500, "low": 79500, "close": 80200, "volume": 100}
    fills = process_bar(state, bar, ts_ms=0)
    for f in fills:
        cp = f.cycle_profit
        print(f"  {f.side} cell[{f.cell_index}] @ {f.cell_price:.0f} vol={f.volume_base:.6f} fee=${f.fee_quote:.4f} cycle_profit=${cp:+.4f}")

    print(f"\nFinal: base={state.base_inventory:.6f}, quote=${state.quote_inventory:.2f}")
    print(f"  Grid profit realized: ${state.grid_profit_realized:.4f}")
    print(f"  Paired cycles: {state.paired_cycles}")
    print(f"  Fills count: {state.fills_count}")

    # Bar 2: preu puja més (haurien de fillar SELLS amb profit)
    print("\nBar 2: 80200 -> 80000 -> 80800 -> 80700 (up bar):")
    bar2 = {"open": 80200, "high": 80800, "low": 80000, "close": 80700, "volume": 100}
    fills = process_bar(state, bar2, ts_ms=60000)
    for f in fills:
        print(f"  {f.side} cell[{f.cell_index}] @ {f.cell_price:.0f} cycle_profit=${f.cycle_profit:+.4f}")
    print(f"  Grid profit realized: ${state.grid_profit_realized:.4f}")
    print(f"  Paired cycles: {state.paired_cycles}")
