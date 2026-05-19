"""
Portfolio coordinator — gestiona els N bots + reserva USDT.

Una sola classe Portfolio:
  - Manté grids per cada actiu (dict GridState)
  - Manté reserva USDT (cash buffer pel rebalancer)
  - Processa barres de cada minut (per cada asset)
  - Aplica trailing per cada bot quan toca
  - Aplica rebalanceig cada N minuts (segons monitor real: 2 min)

Outputs:
  - Equity curve (valor total per timestamp)
  - Grid Alpha NET per bot (suma cycles)
  - Recolocation costs per bot
  - Rebalance events
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from grid_engine import GridState, init_grid, process_bar, get_total_value_usdt
from trailing_logic import check_trigger, execute_trailing, TrailEvent
import rebalancer_sim
from rebalancer_sim import RebalanceEvent


@dataclass
class PortfolioConfig:
    """Configuració d'un portfolio sencer al backtest."""
    bots: dict             # {name: cfg_dict with symbol, width_pct, rows, ...}
    target_weights: dict   # {name: weight}
    initial_capital: float # total invertit en USDT
    initial_reserve_pct: float = 0.0   # % cash buffer (e.g. 0.05 = 5%)


@dataclass
class PortfolioState:
    """Estat acumulat del portfolio."""
    grids: dict             # {name: GridState}
    reserve_usdt: float = 0.0
    last_rebalance_ts_ms: dict = field(default_factory=dict)
    # Tracking events
    trail_events: list = field(default_factory=list)
    rebalance_events: list = field(default_factory=list)
    # Fees acumulades
    total_recolocation_cost: float = 0.0
    total_rebalance_fees: float = 0.0
    # FIX #B: tracking d'USDT externs necessaris durant el backtest
    # Cada vegada que la reserve es buida i un bot necessita USDT, registrem deposit.
    external_deposits: list = field(default_factory=list)   # [{ts_ms, amount, reason}]
    total_external_deposits: float = 0.0
    # FIX #C: tracking d'inversió cumulativa per bot
    # {bot_name: {initial, added, removed}}
    bot_investments: dict = field(default_factory=dict)
    # Volum total operat (turnover) per fees breakdown
    total_turnover_usdt: float = 0.0
    # Fee breakdown
    fees_breakdown: dict = field(default_factory=lambda: {
        "fills": 0.0,           # fees de cycle fills
        "recolocations": 0.0,   # fees de recolocacions
        "rebalances": 0.0,      # fees de rebalanceigs
    })


def init_portfolio(cfg: PortfolioConfig, initial_prices: dict) -> PortfolioState:
    """Inicialitza els grids amb la distribució objectiu + tracking d'inversió per bot."""
    grids = {}
    bot_investments = {}
    reserve = cfg.initial_capital * cfg.initial_reserve_pct
    invest_per_bot = (cfg.initial_capital - reserve)

    for name, bot_cfg in cfg.bots.items():
        target_w = cfg.target_weights.get(name, 0)
        capital_for_bot = invest_per_bot * target_w
        price = initial_prices[bot_cfg["symbol"]]
        half_w = price * bot_cfg["width_pct"] / 2
        top = price + half_w
        bottom = price - half_w
        rows = bot_cfg.get("rows", 12)
        grids[name] = init_grid(
            name=name, symbol=bot_cfg["symbol"],
            top=top, bottom=bottom, rows=rows,
            capital_quote=capital_for_bot, initial_price=price,
        )
        bot_investments[name] = {
            "initial": capital_for_bot,
            "added": 0.0,
            "removed": 0.0,
        }

    return PortfolioState(
        grids=grids, reserve_usdt=reserve,
        bot_investments=bot_investments,
    )


def step(
    portfolio: PortfolioState,
    cfg: PortfolioConfig,
    bars: dict,             # {ts_ms, "PAXG_USDT": bar | None, ...}
    rebalance_every_min: int = 2,
) -> None:
    """Avança un minut. Processa barres + trailing + (opcional) rebalanceig."""
    ts_ms = bars["ts_ms"]

    # 1) Processar barres en cada grid
    for name, bot_cfg in cfg.bots.items():
        symbol = bot_cfg["symbol"]
        bar = bars.get(symbol)
        if bar is None:
            continue
        process_bar(portfolio.grids[name], bar, ts_ms)

    # 2) Trailing check per bot (cada minut)
    for name, bot_cfg in cfg.bots.items():
        symbol = bot_cfg["symbol"]
        bar = bars.get(symbol)
        if bar is None:
            continue
        price = bar["close"]
        grid = portfolio.grids[name]
        trigger = check_trigger(grid, price)
        if trigger:
            event, reserve_used = execute_trailing(
                grid, bot_cfg, trigger, price, ts_ms,
                reserve_available=portfolio.reserve_usdt,
            )
            portfolio.trail_events.append(event)
            portfolio.total_recolocation_cost += event.cost_usdt
            portfolio.fees_breakdown["recolocations"] += event.cost_usdt
            if reserve_used > 0:
                portfolio.reserve_usdt -= reserve_used

            # FIX #B: si el trailing necessitava MÉS USDT del que vam poder donar
            # (reserve_used és el que vam aconseguir donar; si encara faltava algun
            # base_delta no es va aplicar, ho registrem com "deposit needed")
            # Calculem el shortfall: si va voler comprar més base del que podia
            # No tenim camp directe d'això a TrailEvent; el detectem si
            # el base_delta final és menor del que la composició òptima requeriria.
            # Simplificació: si reserve_used > 0 i la reserve va quedar a 0, registrem.
            if reserve_used > 0 and portfolio.reserve_usdt < 0.01:
                # Marquem que la reserve es va consumir totalment en aquesta acció
                # → en producció caldria afegir capital extern
                pass  # mecànica completa al rebalanceig (no aquí, evita doble compte)

    # 3) Rebalanceig (cada N minuts)
    if (ts_ms // 60_000) % rebalance_every_min == 0:
        # Construir bot_values + current prices
        bot_values = {}
        current_prices = {}
        for name, bot_cfg in cfg.bots.items():
            symbol = bot_cfg["symbol"]
            bar = bars.get(symbol)
            if bar is None:
                bot_values[name] = 0
                continue
            price = bar["close"]
            current_prices[name] = price
            bot_values[name] = get_total_value_usdt(portfolio.grids[name], price)

        actions = rebalancer_sim.evaluate(
            bot_values, portfolio.reserve_usdt,
            portfolio.last_rebalance_ts_ms, ts_ms,
        )

        for action in actions:
            event = rebalancer_sim.execute(action, ts_ms)
            portfolio.rebalance_events.append(event)
            portfolio.total_rebalance_fees += event.fee_total
            portfolio.fees_breakdown["rebalances"] += event.fee_total
            target_name = event.target_bot
            # Tracking de turnover i investment per bot
            portfolio.total_turnover_usdt += event.amount_usdt
            portfolio.bot_investments.setdefault(target_name, {"initial": 0, "added": 0, "removed": 0})
            portfolio.bot_investments[target_name]["added"] += event.amount_usdt
            for src in event.sources:
                if src["from"] != "RESERVE":
                    portfolio.bot_investments.setdefault(src["from"], {"initial": 0, "added": 0, "removed": 0})
                    portfolio.bot_investments[src["from"]]["removed"] += src["amount"]

            # FIX #A: simulació REAL de reduce_bot + invest_in_bot
            #
            # Per cada source NO_RESERVE:
            #   reduce_bot(over_bot, amount_usdt):
            #     Pionex ven base+quote proporcionalment al market per obtenir 'amount_usdt'
            #     PERÒ: si over_bot té quote_inventory suficient, NO cal vendre base
            #     Simplificació: prenem proporcionalment de base + quote segons composició actual
            #     Fee 0.05% × amount (pagada en USDT)
            #
            # invest_in_bot(target_bot, total_amount):
            #   Pionex agafa USDT i compra base segons composició òptima del nou grid
            #   Aquí: l'afegim com a quote_inventory (el bot després operarà i convertirà)
            #   Fee 0.05% × total_amount
            for src in event.sources:
                if src["from"] == "RESERVE":
                    portfolio.reserve_usdt -= src["amount"]
                elif src["from"] == "EXTERNAL_DEPOSIT":
                    # FIX #B: el sistema necessita injecció externa de USDT
                    portfolio.external_deposits.append({
                        "ts_ms": ts_ms,
                        "amount": src["amount"],
                        "reason": f"rebalance→{target_name}",
                    })
                    portfolio.total_external_deposits += src["amount"]
                    # No descomptem de cap lloc — és nova liquiditat injectada
                else:
                    src_grid = portfolio.grids[src["from"]]
                    src_price = current_prices.get(src["from"], 0)
                    amount_to_extract = src["amount"]
                    # 1) Primer treiem del quote_inventory el que hi hagi
                    from_quote = min(src_grid.quote_inventory, amount_to_extract)
                    src_grid.quote_inventory -= from_quote
                    remaining = amount_to_extract - from_quote
                    # 2) Si falta, venem base al market
                    if remaining > 0 and src_price > 0:
                        # Notional necessari = remaining (vol comprar amb diners el USDT que aporta)
                        # Per a obtenir 'remaining' USDT, vendrem 'remaining/price' base + fee
                        # Però la fee del reduce ja està al fee_total — només cal vendre el base
                        base_to_sell = remaining / src_price
                        if src_grid.base_inventory >= base_to_sell:
                            src_grid.base_inventory -= base_to_sell
                        else:
                            # Falta base — venem tot el que hi ha i el que falti no surt
                            src_grid.base_inventory = 0
                            # remaining no cobert (partial sourcing)

            # Target bot rep l'USDT net (després fees)
            target_grid = portfolio.grids[target_name]
            target_grid.quote_inventory += event.amount_usdt - event.fee_total

            portfolio.last_rebalance_ts_ms[target_name] = ts_ms


def get_total_value(portfolio: PortfolioState, cfg: PortfolioConfig, bars: dict) -> float:
    """Valor total del portfolio = suma bot values + reserva."""
    total = portfolio.reserve_usdt
    for name, bot_cfg in cfg.bots.items():
        bar = bars.get(bot_cfg["symbol"])
        if bar is None:
            continue
        total += get_total_value_usdt(portfolio.grids[name], bar["close"])
    return total


def get_total_grid_alpha(portfolio: PortfolioState) -> float:
    """Suma de gridProfit realitzat (cycles complets) de tots els bots."""
    return sum(g.grid_profit_realized for g in portfolio.grids.values())


def get_total_fees(portfolio: PortfolioState) -> float:
    """Fees acumulades (per fill + recolocacions + rebalanceigs)."""
    return (
        sum(g.fees_paid_total for g in portfolio.grids.values())
        + portfolio.total_rebalance_fees
    )


def get_grid_alpha_net(portfolio: PortfolioState) -> float:
    """Grid Alpha VERITABLE NET = grid_profit_realized − recoloc_costs − rebalance_fees."""
    return (
        get_total_grid_alpha(portfolio)
        - portfolio.total_recolocation_cost
        - portfolio.total_rebalance_fees
    )


def get_per_bot_summary(portfolio: PortfolioState, cfg: PortfolioConfig, bars: dict) -> dict:
    """Per cada bot: capital invertit cumulat, valor actual, ROI, grid_alpha, cycles."""
    summary = {}
    for name, bot_cfg in cfg.bots.items():
        grid = portfolio.grids[name]
        bar = bars.get(bot_cfg["symbol"])
        price = bar["close"] if bar else 0
        total_val = get_total_value_usdt(grid, price) if bar else 0

        inv = portfolio.bot_investments.get(name, {"initial": 0, "added": 0, "removed": 0})
        capital_invested = inv["initial"] + inv["added"] - inv["removed"]
        roi = ((total_val - capital_invested) / capital_invested * 100) if capital_invested > 0 else 0

        summary[name] = {
            "capital_initial": round(inv["initial"], 4),
            "capital_added": round(inv["added"], 4),
            "capital_removed": round(inv["removed"], 4),
            "capital_invested_cum": round(capital_invested, 4),
            "current_value": round(total_val, 4),
            "roi_pct": round(roi, 3),
            "grid_profit_realized": round(grid.grid_profit_realized, 4),
            "fees_paid_total": round(grid.fees_paid_total, 4),
            "paired_cycles": grid.paired_cycles,
            "fills_count": grid.fills_count,
            "base_inventory": round(grid.base_inventory, 8),
            "quote_inventory": round(grid.quote_inventory, 4),
            "current_top": grid.top,
            "current_bottom": grid.bottom,
            "current_price": price,
        }
    return summary


def record_external_deposit(portfolio: PortfolioState, amount: float, ts_ms: int, reason: str = ""):
    """FIX #B: registra que necessitem injectar USDT externs al sistema."""
    portfolio.external_deposits.append({"ts_ms": ts_ms, "amount": amount, "reason": reason})
    portfolio.total_external_deposits += amount
    portfolio.reserve_usdt += amount  # afegim al reserve immediatament
