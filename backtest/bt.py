"""
Backtest FINAL — model net, validat, sense bugs.

Regla d'or: cycle compta NOMÉS si hi ha una BUY real precedent al mateix grid
(sense recolocació entremig). Tota la resta és MTM, NO grid alpha.

Components:
  - GridBot: estat per bot (inventari + cells + pending)
  - process_bar: BUY/SELL fills al minut, cycle profit només si parell real
  - trail: recoloca rang, INVENTARI heretat NO crea cycles ficticis
  - rebalance: reduce_bot ven base real al market, invest_in compra base real
  - Portfolio: 4 bots + reserve + tracking

Output separat:
  - GridAlpha (només cycles REALS BUY->SELL en mateix grid)
  - InventoryMTM (preu inventari final − cost mig acumulat)
  - HoldRebalancer (què valdria amb només rebalanceig sense grid)
  - P&L_total (real, suma sense doblets)
"""
from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"

FEE = 0.0005  # 0.05% per fill

# Config bots producció
BOTS_CFG = {
    "PAXG_USDT": {"width_pct": 0.032,  "rows": 8,  "weight": 0.40},
    "BTC_USDT":  {"width_pct": 0.0516, "rows": 12, "weight": 0.30},
    "ETH_USDT":  {"width_pct": 0.067,  "rows": 12, "weight": 0.20},
    "SOL_USDT":  {"width_pct": 0.070,  "rows": 9,  "weight": 0.10},
}
EDGE_TRIGGER = 0.10
REBAL_THRESHOLD = 0.05
MIN_REBAL = 10.0
REBAL_COOLDOWN_MIN = 30


@dataclass
class GridBot:
    name: str
    top: float
    bottom: float
    rows: int
    cells: list = field(default_factory=list)
    step: float = 0
    vol_per_cell: float = 0
    # Per cell index: "BUY" | "SELL" | "INACTIVE"
    pending: dict = field(default_factory=dict)
    # Cell index -> buy_price REAL (quan ha hagut un BUY real precedent al mateix grid)
    # Si una SELL es fillla i open[i] és None -> NO és cycle, és venda d'inventari heretat
    open: dict = field(default_factory=dict)
    base: float = 0
    quote: float = 0
    # Cost total acumulat de base inventory (per calcular MTM)
    cost_base_acquired: float = 0  # diners gastats acumulats en BUYs (no compreses inicialment)
    base_acquired: float = 0       # base unitats acquired (no inicial)
    # Grid alpha real
    grid_alpha: float = 0
    cycles: int = 0
    fills: int = 0
    fees: float = 0
    # Costos recolocacions
    reloc_cost: float = 0
    reloc_count: int = 0


def make_bot(name: str, top: float, bot: float, rows: int, capital: float, price: float) -> GridBot:
    cells = [bot + i * (top - bot) / (rows - 1) for i in range(rows)]
    step = (top - bot) / (rows - 1)
    # Capital reparteix base + quote segons posició del preu al rang
    n_sells = sum(1 for c in cells if c >= price)
    n_buys = sum(1 for c in cells if c < price)
    vol_per_cell = capital / rows / ((top + bot) / 2)
    # Inventari inicial cobreix sells pending
    base = vol_per_cell * n_sells
    # Quote inicial cobreix buys pending
    buy_prices = [c for c in cells if c < price]
    quote = sum(buy_prices) * vol_per_cell
    pending = {}
    for i, c in enumerate(cells):
        pending[i] = "BUY" if c < price else "SELL"
    # IMPORTANT: les SELLs inicials NO tenen "open" (no és cycle, és inventari heretat)
    return GridBot(
        name=name, top=top, bottom=bot, rows=rows,
        cells=cells, step=step, vol_per_cell=vol_per_cell,
        pending=pending, base=base, quote=quote,
    )


def process_bar(bot: GridBot, bar: dict):
    """Processa una barra M1. Cycle profit només si BUY real precedent."""
    bo = bar["open"]; bh = bar["high"]; bl = bar["low"]; bc = bar["close"]
    # Cells dins el rang del bar
    candidates = []
    for i, p in enumerate(bot.cells):
        if bl <= p <= bh and bot.pending.get(i) in ("BUY", "SELL"):
            candidates.append((i, p, bot.pending[i]))
    if not candidates:
        return
    # Ordre intra-minut: up bar O->L->H, down bar O->H->L
    if bc >= bo:
        # buys primer (low side), després sells (high side)
        buys = sorted([(i,p,s) for (i,p,s) in candidates if s=="BUY" and p<=bo], key=lambda x: -x[1])
        sells = sorted([(i,p,s) for (i,p,s) in candidates if s=="SELL" and p>=bo], key=lambda x: x[1])
        order = buys + sells
    else:
        sells = sorted([(i,p,s) for (i,p,s) in candidates if s=="SELL" and p>=bo], key=lambda x: x[1])
        buys = sorted([(i,p,s) for (i,p,s) in candidates if s=="BUY" and p<=bo], key=lambda x: -x[1])
        order = sells + buys

    vol = bot.vol_per_cell
    for idx, cell_price, side in order:
        if bot.pending.get(idx) != side:
            continue
        notional = cell_price * vol
        fee = notional * FEE
        if side == "BUY":
            cost = notional + fee
            if bot.quote < cost:
                continue
            bot.quote -= cost
            bot.base += vol
            bot.cost_base_acquired += notional  # cost net (sense fee, per MTM cleanup)
            bot.base_acquired += vol
            bot.pending[idx] = "INACTIVE"
            # Crea SELL pending a cell idx+1 i marca open (cycle real possible)
            if idx + 1 < bot.rows:
                bot.pending[idx + 1] = "SELL"
                bot.open[idx + 1] = cell_price  # marca BUY real -> propera SELL serà cycle
            bot.fees += fee
            bot.fills += 1
        elif side == "SELL":
            if bot.base < vol:
                continue
            bot.base -= vol
            proceeds = notional - fee
            bot.quote += proceeds
            bot.pending[idx] = "INACTIVE"
            # Cycle complet NOMÉS si hi ha una BUY real precedent al mateix grid
            buy_price = bot.open.pop(idx, None)
            if buy_price is not None:
                # cycle profit = step × vol − 2 fees (1 buy + 1 sell)
                buy_fee = buy_price * vol * FEE
                cycle_profit = (cell_price - buy_price) * vol - fee - buy_fee
                bot.grid_alpha += cycle_profit
                bot.cycles += 1
            # else: venda d'inventari inicial/heretat — NO és cycle, és MTM
            if idx - 1 >= 0:
                bot.pending[idx - 1] = "BUY"
            bot.fees += fee
            bot.fills += 1


def trail(bot: GridBot, cfg: dict, price: float, reserve: float) -> tuple[float, float]:
    """Recoloca centrant al preu. Retorna (cost_fee, reserve_consumida).
    IMPORTANT: NO crea cycles ficticis. L'inventari heretat es manté com a "inventari sense cycle font"."""
    width = cfg["width_pct"]
    rows = cfg["rows"]
    half = price * width / 2
    new_top = price + half
    new_bot = price - half

    # Composició òptima al nou rang
    total_val = bot.base * price + bot.quote
    if new_top == new_bot:
        return 0, 0
    pos_pct = max(0, min(1, (price - new_bot) / (new_top - new_bot)))
    target_quote = total_val * pos_pct
    target_base = (total_val - target_quote) / price if price > 0 else 0
    base_delta = target_base - bot.base
    market_vol = abs(base_delta) * price
    fee = market_vol * FEE
    reserve_used = 0

    if base_delta > 0:
        # Cal comprar base: paguem del quote_inventory (o del reserve)
        cost = base_delta * price + fee
        if bot.quote >= cost:
            bot.quote -= cost
            bot.base += base_delta
            bot.cost_base_acquired += base_delta * price
            bot.base_acquired += base_delta
        else:
            shortage = cost - bot.quote
            from_reserve = min(reserve, shortage)
            reserve_used = from_reserve
            avail = bot.quote + from_reserve
            # base_delta_real = avail / (price * (1+FEE))
            real_delta = avail / (price * (1 + FEE))
            bot.quote = 0
            bot.base += real_delta
            bot.cost_base_acquired += real_delta * price
            bot.base_acquired += real_delta
            base_delta = real_delta
            fee = real_delta * price * FEE
    elif base_delta < 0:
        # Vendre base: alliberem quote. NO és cycle (és materialització MTM)
        sell_qty = -base_delta
        proceeds = sell_qty * price - fee
        bot.base += base_delta
        bot.quote += proceeds
        # NO sumem a grid_alpha — això és MTM realization
        # Ajustem cost_base_acquired proporcionalment (FIFO simplificat)
        if bot.base_acquired > 0:
            ratio_sold = min(1, sell_qty / bot.base_acquired)
            bot.cost_base_acquired *= (1 - ratio_sold)
            bot.base_acquired *= (1 - ratio_sold)

    bot.fees += fee
    bot.reloc_cost += fee
    bot.reloc_count += 1

    # Recrea cells amb nou rang
    new_cells = [new_bot + i * (new_top - new_bot) / (rows - 1) for i in range(rows)]
    new_step = (new_top - new_bot) / (rows - 1)
    new_pending = {}
    for i, c in enumerate(new_cells):
        new_pending[i] = "BUY" if c < price else "SELL"
    # IMPORTANT: nou grid NO té "open" — totes les SELLs inicials són inventari heretat
    # Cap cycle fictici. Si una SELL es fillla sense BUY real precedent -> no compta cycle.
    bot.top = new_top; bot.bottom = new_bot; bot.cells = new_cells; bot.step = new_step
    bot.pending = new_pending
    bot.open = {}  # reset opens — heretat no compta
    new_capital_per_cell = total_val / rows
    bot.vol_per_cell = new_capital_per_cell / ((new_top + new_bot) / 2)

    return fee, reserve_used


def check_trigger(bot: GridBot, price: float) -> bool:
    if bot.top <= bot.bottom:
        return False
    if price > bot.top or price < bot.bottom:
        return True
    rng = bot.top - bot.bottom
    if (bot.top - price) / rng <= EDGE_TRIGGER:
        return True
    if (price - bot.bottom) / rng <= EDGE_TRIGGER:
        return True
    return False


def total_value(bot: GridBot, price: float) -> float:
    return bot.base * price + bot.quote


# ─── Tests bàsics ────────────────────────────────────────────────────
def test_no_movement():
    """Bot sense moviment -> grid_alpha = 0."""
    bot = make_bot("BTC", 82000, 78000, 12, capital=200, price=80000)
    for _ in range(100):
        bar = {"open": 80000, "high": 80050, "low": 79950, "close": 80000, "volume": 1}
        process_bar(bot, bar)
    assert bot.grid_alpha == 0, f"FAIL: grid_alpha={bot.grid_alpha}"
    assert bot.cycles == 0, f"FAIL: cycles={bot.cycles}"
    print(f"TEST 1 OK: sense moviment -> grid_alpha=$0, cycles=0")


def test_simple_cycle():
    """Una BUY a cell i, després SELL a cell i+1 -> 1 cycle, profit = step×vol − 2 fees."""
    bot = make_bot("BTC", 82000, 78000, 12, capital=200, price=80000)
    # Bar 1: preu baixa fins una BUY cell
    # cell 5 = 78000 + 5*363.636 = 79818.18
    bar1 = {"open": 80000, "high": 80000, "low": 79800, "close": 79850, "volume": 1}
    process_bar(bot, bar1)
    # Bar 2: preu puja fins la SELL del cycle (cell 6)
    bar2 = {"open": 79850, "high": 80250, "low": 79850, "close": 80250, "volume": 1}
    process_bar(bot, bar2)
    print(f"TEST 2: cycles={bot.cycles} grid_alpha=${bot.grid_alpha:.4f}")
    # Esperat: ~$0.05 (step × vol - 2 fees)
    expected_profit = bot.step * bot.vol_per_cell - 2 * 80000 * bot.vol_per_cell * FEE
    assert bot.cycles == 1, f"FAIL: esperat 1 cycle, got {bot.cycles}"
    assert abs(bot.grid_alpha - expected_profit) < 0.01, f"FAIL: grid_alpha={bot.grid_alpha}, esperat {expected_profit}"
    print(f"TEST 2 OK: 1 cycle real -> grid_alpha=${bot.grid_alpha:.4f} (esperat ${expected_profit:.4f})")


def test_initial_sell_no_cycle():
    """SELL inicial (cell pendent inicial sense BUY real precedent) -> NO suma a grid_alpha."""
    bot = make_bot("BTC", 82000, 78000, 12, capital=200, price=80000)
    # Preu puja directament fins cell 7 (sense BUY abans)
    bar = {"open": 80000, "high": 80600, "low": 80000, "close": 80600, "volume": 1}
    process_bar(bot, bar)
    # S'han fillat sells inicials però grid_alpha hauria de ser 0 (no és cycle real)
    print(f"TEST 3: fills={bot.fills} cycles={bot.cycles} grid_alpha=${bot.grid_alpha:.4f}")
    assert bot.cycles == 0, f"FAIL: sells inicials NO han de comptar com cycles. cycles={bot.cycles}"
    assert bot.grid_alpha == 0, f"FAIL: grid_alpha esperat 0, got {bot.grid_alpha}"
    print(f"TEST 3 OK: SELLs inicials no compten cycle (materialització MTM)")


def test_reloc_no_fake_cycles():
    """Després de recolocació, sells del nou grid NO compten cycle (no hi ha BUY real al nou grid)."""
    bot = make_bot("BTC", 82000, 78000, 12, capital=200, price=80000)
    trail(bot, {"width_pct": 0.0516, "rows": 12}, price=78400, reserve=100)
    gp_before = bot.grid_alpha
    # Preu puja molt sobre el nou top
    new_price = bot.top + 100
    bar = {"open": 78400, "high": new_price, "low": 78400, "close": new_price, "volume": 1}
    process_bar(bot, bar)
    print(f"TEST 4: after reloc + sells, cycles={bot.cycles} grid_alpha=${bot.grid_alpha:.4f}")
    assert bot.grid_alpha == gp_before, f"FAIL: recoloc crea cycles ficticis: {bot.grid_alpha}"
    print(f"TEST 4 OK: recolocació no infla grid_alpha")


# ─── Portfolio + Rebalance + Runner ───────────────────────────────────
@dataclass
class Portfolio:
    bots: dict  # name → GridBot
    reserve: float = 0
    last_rebal_ts: dict = field(default_factory=dict)
    rebal_count: int = 0
    rebal_fees: float = 0
    ext_deposits: float = 0
    ext_deposits_events: list = field(default_factory=list)


def load_bars(symbol: str) -> dict:
    path = DATA_DIR / f"m1_{symbol}.csv"
    bars = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts = int(row["open_time_ms"])
            bars[ts] = {
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
    return bars


def init_portfolio(initial_capital: float, reserve_pct: float, initial_prices: dict) -> Portfolio:
    reserve = initial_capital * reserve_pct
    invest = initial_capital - reserve
    bots = {}
    for name, cfg in BOTS_CFG.items():
        cap = invest * cfg["weight"]
        price = initial_prices[name]
        half = price * cfg["width_pct"] / 2
        bots[name] = make_bot(name, price + half, price - half, cfg["rows"], cap, price)
    return Portfolio(bots=bots, reserve=reserve)


def rebalance_check(p: Portfolio, prices: dict, ts_ms: int):
    """Rebal real: reduce ven base, invest_in compra base."""
    values = {n: total_value(b, prices[n]) for n, b in p.bots.items()}
    total = sum(values.values()) + p.reserve
    if total <= 0:
        return
    overs = []
    unders = []
    for n in p.bots:
        target = BOTS_CFG[n]["weight"] * (sum(values.values()) / total)  # weight relatiu als bots
        # Mès simple: target sobre total inclou reserve fix
        target_abs = BOTS_CFG[n]["weight"] * (total)
        dev = values[n] - target_abs
        dev_pct = dev / total
        if dev_pct >= REBAL_THRESHOLD:
            overs.append((n, dev))
        elif dev_pct <= -REBAL_THRESHOLD:
            unders.append((n, -dev))
    if not unders:
        return

    overs.sort(key=lambda x: -x[1])
    unders.sort(key=lambda x: -x[1])

    for under_name, under_amt in unders:
        if under_amt < MIN_REBAL:
            continue
        last = p.last_rebal_ts.get(under_name, 0)
        if (ts_ms - last) / 60_000 < REBAL_COOLDOWN_MIN:
            continue
        remaining = under_amt
        # Reserve primer
        if p.reserve > 0:
            use = min(p.reserve, remaining)
            if use >= MIN_REBAL / 2:
                p.reserve -= use
                # Invest in: comprem base per al target bot
                fee = use * FEE
                qty = (use - fee) / prices[under_name]
                p.bots[under_name].base += qty
                p.bots[under_name].cost_base_acquired += use - fee
                p.bots[under_name].base_acquired += qty
                p.rebal_fees += fee
                remaining -= use
        # Overs després
        for i, (over_name, over_amt) in enumerate(overs):
            if remaining < MIN_REBAL / 2:
                break
            use = min(over_amt, remaining)
            if use < MIN_REBAL / 2:
                continue
            # Reduce: venem base de l'over bot
            qty_to_sell = use / prices[over_name]
            over_bot = p.bots[over_name]
            if over_bot.base >= qty_to_sell:
                over_bot.base -= qty_to_sell
                proceeds = use - use * FEE
                # Ajustem cost FIFO
                if over_bot.base_acquired > 0:
                    ratio = min(1, qty_to_sell / over_bot.base_acquired)
                    over_bot.cost_base_acquired *= (1 - ratio)
                    over_bot.base_acquired *= (1 - ratio)
                # Invest in al target
                fee_buy = use * FEE
                qty_buy = (use - fee_buy) / prices[under_name]
                p.bots[under_name].base += qty_buy
                p.bots[under_name].cost_base_acquired += use - fee_buy
                p.bots[under_name].base_acquired += qty_buy
                p.rebal_fees += (use * FEE + fee_buy)
                remaining -= use
                overs[i] = (over_name, over_amt - use)

        if remaining < under_amt:
            p.last_rebal_ts[under_name] = ts_ms
            p.rebal_count += 1
            # External deposit si encara falta
            if remaining >= MIN_REBAL / 2:
                p.ext_deposits += remaining
                p.ext_deposits_events.append({"ts": ts_ms, "amount": remaining, "bot": under_name})


def hold_with_rebal_baseline(symbols: list, initial_capital: float, reserve_pct: float) -> dict:
    """Baseline: hold + rebalance entre actius, sense grid."""
    reserve = initial_capital * reserve_pct
    invest = initial_capital - reserve
    bars_data = {s: load_bars(s) for s in symbols}
    sorted_ts = sorted(set().union(*[set(b.keys()) for b in bars_data.values()]))
    first_ts = sorted_ts[0]
    last_ts = sorted_ts[-1]

    holdings = {}
    for s in symbols:
        first_price = bars_data[s][first_ts]["open"]
        cap = invest * BOTS_CFG[s]["weight"]
        holdings[s] = cap / first_price

    last_check = 0
    rebal_count = 0
    fees_paid = 0
    for ts in sorted_ts:
        if (ts // 60_000) - last_check < 2:
            continue
        last_check = ts // 60_000
        bars_now = {s: bars_data[s].get(ts) for s in symbols}
        if any(b is None for b in bars_now.values()):
            continue
        prices = {s: bars_now[s]["close"] for s in symbols}
        values = {s: holdings[s] * prices[s] for s in symbols}
        total = sum(values.values()) + reserve
        if total <= 0:
            continue
        # Rebal check
        for s in symbols:
            target = BOTS_CFG[s]["weight"] * total
            dev = values[s] - target
            if abs(dev) / total >= REBAL_THRESHOLD and abs(dev) >= MIN_REBAL:
                if dev > 0:
                    # Vendre base de s
                    qty = (dev) / prices[s]
                    if holdings[s] >= qty:
                        holdings[s] -= qty
                        reserve += dev - dev * FEE
                        fees_paid += dev * FEE
                        rebal_count += 1
                else:
                    # Comprar base de s amb reserve
                    need = -dev
                    if reserve >= need:
                        reserve -= need
                        qty = (need - need * FEE) / prices[s]
                        holdings[s] += qty
                        fees_paid += need * FEE
                        rebal_count += 1

    final_prices = {s: bars_data[s][last_ts]["close"] for s in symbols}
    final_value = sum(holdings[s] * final_prices[s] for s in symbols) + reserve
    return {
        "final": final_value,
        "pnl": final_value - initial_capital,
        "pnl_pct": (final_value - initial_capital) / initial_capital * 100,
        "rebal_count": rebal_count,
        "fees": fees_paid,
    }


def run_full_backtest(initial_capital: float = 1000.0, reserve_pct: float = 0.05):
    print(f"Carregant CSVs...")
    symbols = list(BOTS_CFG.keys())
    bars_data = {s: load_bars(s) for s in symbols}
    sorted_ts = sorted(set().union(*[set(b.keys()) for b in bars_data.values()]))
    print(f"  Total minuts: {len(sorted_ts):,}")
    print(f"  Rang: {sorted_ts[0]} -> {sorted_ts[-1]}")

    # Init portfolio
    first_ts = sorted_ts[0]
    first_prices = {s: bars_data[s][first_ts]["open"] for s in symbols}
    p = init_portfolio(initial_capital, reserve_pct, first_prices)
    print(f"\nPortfolio inicialitzat. Capital ${initial_capital}, reserve ${p.reserve:.2f}")
    for n, b in p.bots.items():
        print(f"  {n}: range [{b.bottom:.2f}, {b.top:.2f}] base={b.base:.6f} quote=${b.quote:.2f}")

    # Loop
    t0 = time.time()
    last_progress = t0
    bar_count = 0
    for ts in sorted_ts:
        bars_now = {s: bars_data[s].get(ts) for s in symbols}
        if any(b is None for b in bars_now.values()):
            continue
        bar_count += 1
        # Process bars + trail
        for n, bot in p.bots.items():
            bar = bars_now[n]
            process_bar(bot, bar)
            price = bar["close"]
            if check_trigger(bot, price):
                cost, ru = trail(bot, BOTS_CFG[n], price, p.reserve)
                if ru > 0:
                    p.reserve -= ru
        # Rebalance check cada 2 min
        if (ts // 60_000) % 2 == 0:
            prices = {s: bars_now[s]["close"] for s in symbols}
            rebalance_check(p, prices, ts)
        # Progress
        if time.time() - last_progress > 30:
            done = bar_count / len(sorted_ts) * 100
            ga = sum(b.grid_alpha for b in p.bots.values())
            print(f"  {done:.1f}% · grid_alpha=${ga:.2f} · reloc={sum(b.reloc_count for b in p.bots.values())} · rebal={p.rebal_count}")
            last_progress = time.time()

    elapsed = time.time() - t0
    print(f"\nLoop completat en {elapsed:.1f}s ({bar_count:,} bars)")

    # Final
    last_ts = sorted_ts[-1]
    last_prices = {s: bars_data[s][last_ts]["close"] for s in symbols}
    final_per_bot = {n: total_value(b, last_prices[n]) for n, b in p.bots.items()}
    final_total = sum(final_per_bot.values()) + p.reserve

    grid_alpha = sum(b.grid_alpha for b in p.bots.values())
    reloc_cost = sum(b.reloc_cost for b in p.bots.values())
    fees_fills = sum(b.fees for b in p.bots.values()) - reloc_cost  # fees només dels fills

    # MTM inventari: per cada bot, valor del base inventory final menys el cost mig
    mtm_inventory = 0
    for n, b in p.bots.items():
        if b.base_acquired > 0:
            avg_cost = b.cost_base_acquired / b.base_acquired
            mtm_inventory += (last_prices[n] - avg_cost) * b.base
        else:
            # Tot l'inventari final és l'inicial (no s'ha "acquired" cap)
            pass

    return {
        "period_days": (sorted_ts[-1] - sorted_ts[0]) / 1000 / 86400,
        "initial_capital": initial_capital,
        "final_total_value": final_total,
        "total_pnl": final_total - initial_capital - p.ext_deposits,
        "total_pnl_pct": (final_total - initial_capital - p.ext_deposits) / initial_capital * 100,
        "grid_alpha_net": grid_alpha - reloc_cost - p.rebal_fees,
        "grid_alpha_brut": grid_alpha,
        "reloc_cost": reloc_cost,
        "rebal_fees": p.rebal_fees,
        "fees_fills": fees_fills,
        "mtm_inventory": mtm_inventory,
        "reloc_count": sum(b.reloc_count for b in p.bots.values()),
        "rebal_count": p.rebal_count,
        "ext_deposits": p.ext_deposits,
        "final_reserve": p.reserve,
        "per_bot": {
            n: {
                "final_value": final_per_bot[n],
                "grid_alpha": b.grid_alpha,
                "cycles": b.cycles,
                "fills": b.fills,
                "reloc_count": b.reloc_count,
                "base_final": b.base,
                "quote_final": b.quote,
                "cost_base_acquired": b.cost_base_acquired,
                "base_acquired": b.base_acquired,
                "final_price": last_prices[n],
            }
            for n, b in p.bots.items()
        },
    }


if __name__ == "__main__":
    print("=" * 60)
    print("TESTS UNITARIS")
    print("=" * 60)
    print()
    test_no_movement()
    test_simple_cycle()
    test_initial_sell_no_cycle()
    test_reloc_no_fake_cycles()
    print()
    print("=" * 60)
    print("HOLD + REBALANCE BASELINE")
    print("=" * 60)
    holdb = hold_with_rebal_baseline(list(BOTS_CFG.keys()), 1000, 0.05)
    print(f"Final: ${holdb['final']:.2f} P&L: ${holdb['pnl']:+.2f} ({holdb['pnl_pct']:+.2f}%)")
    print(f"Rebal count: {holdb['rebal_count']}  Fees: ${holdb['fees']:.2f}")
    print()
    print("=" * 60)
    print("BACKTEST GRID 12 MESOS")
    print("=" * 60)
    result = run_full_backtest(1000, 0.05)
    print()
    print(json.dumps(result, indent=2))
    # Save
    out = BASE / "results" / "bt_final.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nSaved: {out}")
