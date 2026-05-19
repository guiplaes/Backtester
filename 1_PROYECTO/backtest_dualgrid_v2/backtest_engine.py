"""
Motor de backtest per DualGridEA_v2_Reset.

Simula la logica del v2:
  - Grid bidireccional: BUY+SELL pendents a cada nivell al voltant del preu
  - Pendents replenished quan position closes (al mateix nivell)
  - Cada posicio te TP individual a entry +/- TP$
  - Posicio ancora (reset): tanca tot un costat, reobre 1 anchor consolidada al market
  - Reset trigger (model definitiu):
      captured = balance - cycle_start_balance
      threshold = cycle_start_balance × X%
      LONG_metric = captured + LONG_flotant
      SHORT_metric = captured + SHORT_flotant
      Trigger LONG: LONG_flot < 0 AND LONG_metric > threshold
      Trigger SHORT: SHORT_flot < 0 AND SHORT_metric > threshold
  - Kill switch: equity < start_balance × (1 - max_dd_pct)

Optional valves:
  - harvest_side_pct: si flotant_costat > balance × X%, tanca aquell costat (locking)
  - harvest_balanced_pct: si NET flotant > balance × X%, tanca TOT, recolocacio
  - drift_usd / drift_min: si preu drift > X$ durant Y min, recolocacio forçada

Simulacio per-bar M1 (OHLC). Aproximacio: dins una candela el preu fa low-high-close
en aquest ordre. No es captura intra-bar fluctuation real.
"""
import json
import math
from dataclasses import dataclass, field, asdict
from typing import List, Optional


# Configuració del simulador
@dataclass
class GridConfig:
    name: str = "default"
    # Grid
    lot: float = 0.01
    spacing: float = 1.0       # USD entre nivells
    levels: int = 5            # per costat
    tp_usd: float = 1.0        # TP de cada posicio
    # Reset
    reset_pct: float = 0.25    # threshold %
    # Safety
    max_dd_pct: float = 20.0   # kill switch
    spread: float = 0.30       # spread aplicat
    # Valves (0 = OFF)
    harvest_side_pct: float = 0.0
    harvest_balanced_pct: float = 0.0
    drift_usd: float = 0.0
    drift_min: int = 60


@dataclass
class Position:
    side: str          # 'BUY' or 'SELL'
    entry: float
    volume: float      # lot
    tp: float = 0.0    # 0 = no TP (anchor)
    is_anchor: bool = False
    open_bar: int = 0


@dataclass
class BacktestResult:
    config: dict
    final_balance: float = 0.0
    final_equity: float = 0.0
    initial_balance: float = 0.0
    profit_total: float = 0.0
    profit_pct: float = 0.0
    max_dd_pct: float = 0.0
    max_dd_usd: float = 0.0
    killed: bool = False
    killed_at_bar: int = -1
    long_resets: int = 0
    short_resets: int = 0
    harvest_side_count: int = 0
    harvest_balanced_count: int = 0
    drift_recolocations: int = 0
    total_positions_opened: int = 0
    total_tps_fired: int = 0
    equity_curve: list = field(default_factory=list)  # (bar_idx, equity)
    events: list = field(default_factory=list)         # ('type', bar, details)


def pnl_buy(entry, current, volume):
    """PnL d'una posicio BUY. 1 lot XAU = 100 oz, $1 per $ move = $100/$ per 1 lot, $1 per 0.01 lot."""
    return (current - entry) * volume * 100


def pnl_sell(entry, current, volume):
    return (entry - current) * volume * 100


def total_flotant(positions, side, bid, ask):
    """Sum flotant per side. BUY closes at bid, SELL closes at ask."""
    total = 0.0
    for p in positions:
        if p.side != side:
            continue
        if p.side == 'BUY':
            total += pnl_buy(p.entry, bid, p.volume)
        else:
            total += pnl_sell(p.entry, ask, p.volume)
    return total


def sum_lot(positions, side):
    return sum(p.volume for p in positions if p.side == side)


def count_positions(positions, side):
    return sum(1 for p in positions if p.side == side)


def snap_to_grid(price, anchor, spacing):
    """Arrodoneix al nivell de grid més proper."""
    offset = price - anchor
    n = round(offset / spacing)
    return anchor + n * spacing


def run_backtest(df, cfg: GridConfig, initial_balance: float = 50000.0,
                 verbose: bool = False) -> BacktestResult:
    """
    Simula el v2 sobre dades M1 (df amb cols: time, open, high, low, close).

    Per cada bar:
      1. Determinar moviment intra-bar (assumeix open->low->high->close)
      2. Identificar pendents que disparen (segons preu)
      3. Obrir posicions, posar TPs
      4. Comprovar TPs (intra-bar)
      5. Avaluar triggers de reset / valves
      6. Update equity_curve
    """
    balance = initial_balance
    positions: List[Position] = []
    # Pending state: pendents virtuals = nivells on tindríem un BUY+SELL pair
    # Simplifiquem: tots els nivells +/- (cfg.levels) al voltant del grid anchor
    grid_anchor = 0.0  # set en primer bar

    cycle_start_balance = balance
    long_resets = 0
    short_resets = 0
    harvest_side_count = 0
    harvest_balanced_count = 0
    drift_recolocations = 0
    total_pos_opened = 0
    total_tps_fired = 0
    killed = False
    killed_at = -1
    grid_recolocated_at_bar = 0

    peak_equity = initial_balance
    max_dd_pct = 0.0
    max_dd_usd = 0.0

    equity_curve = []
    events = []

    threshold_usd = cycle_start_balance * (cfg.reset_pct / 100.0)
    kill_threshold = initial_balance * (1.0 - cfg.max_dd_pct / 100.0)

    for bar_idx, row in df.iterrows():
        bar_open  = row['open']
        bar_high  = row['high']
        bar_low   = row['low']
        bar_close = row['close']

        # Bid/Ask: aproximem amb spread/2 al voltant del mid
        # (en realitat bid=close, ask=close+spread per simplificar)
        bid_close = bar_close
        ask_close = bar_close + cfg.spread

        # Inicialitzar grid_anchor al primer bar
        if grid_anchor == 0.0:
            grid_anchor = snap_to_grid(bar_open, bar_open, cfg.spacing)
            if verbose: print(f"[bar {bar_idx}] Init grid anchor at {grid_anchor:.2f}")

        # === STEP 1: Identificar quins nivells disparen pendents ===
        # Generar nivells actius al voltant del grid_anchor
        # Veure el bar high i bar low
        # Pendents BUY LIMITS (sota anchor): disparen quan low les toca
        # Pendents SELL LIMITS (sobre anchor): disparen quan high les toca
        # Pendents BUY STOPS (sobre anchor): disparen quan high les toca (price >= level)
        # Pendents SELL STOPS (sota anchor): disparen quan low les toca

        for i in range(1, cfg.levels + 1):
            level_up = grid_anchor + i * cfg.spacing
            level_dn = grid_anchor - i * cfg.spacing

            # Pendents above level_up: BUY STOP + SELL LIMIT
            if bar_high >= level_up:
                # Verificar que no existeixi ja una posició BUY o SELL al nivell
                # Per simplificar: una posició per nivell+side
                has_buy_at_level = any(
                    p.side == 'BUY' and abs(p.entry - level_up) < cfg.spacing / 4
                    for p in positions
                )
                has_sell_at_level = any(
                    p.side == 'SELL' and abs(p.entry - level_up) < cfg.spacing / 4
                    for p in positions
                )
                # BUY STOP: dispara quan price >= level → open BUY at level
                if not has_buy_at_level:
                    positions.append(Position(side='BUY', entry=level_up, volume=cfg.lot,
                                              tp=level_up + cfg.tp_usd, open_bar=bar_idx))
                    total_pos_opened += 1
                # SELL LIMIT: dispara quan price reaches level from below → open SELL at level
                if not has_sell_at_level:
                    positions.append(Position(side='SELL', entry=level_up, volume=cfg.lot,
                                              tp=level_up - cfg.tp_usd, open_bar=bar_idx))
                    total_pos_opened += 1

            # Pendents below level_dn: BUY LIMIT + SELL STOP
            if bar_low <= level_dn:
                has_buy_at_level = any(
                    p.side == 'BUY' and abs(p.entry - level_dn) < cfg.spacing / 4
                    for p in positions
                )
                has_sell_at_level = any(
                    p.side == 'SELL' and abs(p.entry - level_dn) < cfg.spacing / 4
                    for p in positions
                )
                if not has_buy_at_level:
                    positions.append(Position(side='BUY', entry=level_dn, volume=cfg.lot,
                                              tp=level_dn + cfg.tp_usd, open_bar=bar_idx))
                    total_pos_opened += 1
                if not has_sell_at_level:
                    positions.append(Position(side='SELL', entry=level_dn, volume=cfg.lot,
                                              tp=level_dn - cfg.tp_usd, open_bar=bar_idx))
                    total_pos_opened += 1

        # === STEP 2: TPs intra-bar ===
        # Per cada posicio amb TP definit, mirar si dins el bar el preu toca el TP
        # BUY TP fires if bar_high >= TP
        # SELL TP fires if bar_low <= TP
        to_remove = []
        for idx, p in enumerate(positions):
            if p.tp <= 0:
                continue  # anchors no tenen TP
            if p.side == 'BUY':
                if bar_high >= p.tp:
                    pnl = pnl_buy(p.entry, p.tp - cfg.spread, p.volume)  # close at bid = tp - spread? hmm
                    # Mes acurat: BUY tanca al BID. Si BID = TP, profit = TP - entry. Però vam definir TP = entry + TP$. So profit = TP$ × vol × 100 - spread (already in entry vs ask considered no — let's simplify)
                    pnl = (p.tp - p.entry) * p.volume * 100 - (cfg.spread * p.volume * 100 / 2)  # spread cost
                    balance += pnl
                    to_remove.append(idx)
                    total_tps_fired += 1
            else:  # SELL
                if bar_low <= p.tp:
                    pnl = (p.entry - p.tp) * p.volume * 100 - (cfg.spread * p.volume * 100 / 2)
                    balance += pnl
                    to_remove.append(idx)
                    total_tps_fired += 1

        # Remove TP'd positions
        for idx in sorted(to_remove, reverse=True):
            del positions[idx]

        # === STEP 3: Calcular equity actual ===
        long_flot = total_flotant(positions, 'BUY', bid_close, ask_close)
        short_flot = total_flotant(positions, 'SELL', bid_close, ask_close)
        total_flot = long_flot + short_flot
        equity = balance + total_flot

        # === STEP 4: Kill switch ===
        if equity < kill_threshold:
            killed = True
            killed_at = bar_idx
            events.append(('KILL', bar_idx, {'equity': equity, 'threshold': kill_threshold}))
            # Tanca tot
            for p in positions:
                if p.side == 'BUY':
                    pnl = pnl_buy(p.entry, bid_close, p.volume) - (cfg.spread * p.volume * 100 / 2)
                else:
                    pnl = pnl_sell(p.entry, ask_close, p.volume) - (cfg.spread * p.volume * 100 / 2)
                balance += pnl
            positions.clear()
            break

        # === STEP 5: Valves (abans del trigger normal) ===
        # Valve 1: harvest_side_pct
        if cfg.harvest_side_pct > 0:
            side_threshold = balance * (cfg.harvest_side_pct / 100.0)
            if long_flot > side_threshold and long_flot > short_flot:
                # Harvest LONG
                events.append(('HARVEST_SIDE_LONG', bar_idx, {'flot': long_flot}))
                for p in [p for p in positions if p.side == 'BUY']:
                    pnl = pnl_buy(p.entry, bid_close, p.volume) - (cfg.spread * p.volume * 100 / 2)
                    balance += pnl
                positions = [p for p in positions if p.side != 'BUY']
                harvest_side_count += 1
                # No anchor reopen in this simple model (els pendents es replenishen al pròxim bar)
            elif short_flot > side_threshold and short_flot > long_flot:
                events.append(('HARVEST_SIDE_SHORT', bar_idx, {'flot': short_flot}))
                for p in [p for p in positions if p.side == 'SELL']:
                    pnl = pnl_sell(p.entry, ask_close, p.volume) - (cfg.spread * p.volume * 100 / 2)
                    balance += pnl
                positions = [p for p in positions if p.side != 'SELL']
                harvest_side_count += 1

        # Valve 2: harvest_balanced_pct (net flot > X% balance)
        if cfg.harvest_balanced_pct > 0:
            balanced_threshold = balance * (cfg.harvest_balanced_pct / 100.0)
            if total_flot > balanced_threshold:
                events.append(('HARVEST_BALANCED', bar_idx, {'net_flot': total_flot}))
                for p in positions:
                    if p.side == 'BUY':
                        pnl = pnl_buy(p.entry, bid_close, p.volume) - (cfg.spread * p.volume * 100 / 2)
                    else:
                        pnl = pnl_sell(p.entry, ask_close, p.volume) - (cfg.spread * p.volume * 100 / 2)
                    balance += pnl
                positions.clear()
                harvest_balanced_count += 1
                # Recolocacio del grid anchor
                grid_anchor = snap_to_grid(bar_close, bar_close, cfg.spacing)
                cycle_start_balance = balance
                threshold_usd = cycle_start_balance * (cfg.reset_pct / 100.0)

        # Recalcular flotant despres de valves
        long_flot = total_flotant(positions, 'BUY', bid_close, ask_close)
        short_flot = total_flotant(positions, 'SELL', bid_close, ask_close)
        total_flot = long_flot + short_flot
        equity = balance + total_flot

        # === STEP 6: Trigger normal per costat ===
        captured = balance - cycle_start_balance
        long_metric = captured + long_flot
        short_metric = captured + short_flot

        long_trigger = (long_flot < 0) and (long_metric > threshold_usd)
        short_trigger = (short_flot < 0) and (short_metric > threshold_usd)

        if long_trigger or short_trigger:
            # Si ambdos, tria el de metric mes alta
            if long_trigger and short_trigger:
                reset_side = 'BUY' if long_metric > short_metric else 'SELL'
            elif long_trigger:
                reset_side = 'BUY'
            else:
                reset_side = 'SELL'

            # Tanca tot el costat
            side_lot = sum_lot(positions, reset_side)
            if reset_side == 'BUY':
                for p in [p for p in positions if p.side == 'BUY']:
                    pnl = pnl_buy(p.entry, bid_close, p.volume) - (cfg.spread * p.volume * 100 / 2)
                    balance += pnl
                positions = [p for p in positions if p.side != 'BUY']
                long_resets += 1
                # Reobrir 1 anchor consolidada
                if side_lot > 0:
                    positions.append(Position(side='BUY', entry=ask_close, volume=side_lot,
                                              tp=0, is_anchor=True, open_bar=bar_idx))
                events.append(('RESET_LONG', bar_idx, {'metric': long_metric, 'flot_closed': long_flot}))
            else:
                for p in [p for p in positions if p.side == 'SELL']:
                    pnl = pnl_sell(p.entry, ask_close, p.volume) - (cfg.spread * p.volume * 100 / 2)
                    balance += pnl
                positions = [p for p in positions if p.side != 'SELL']
                short_resets += 1
                if side_lot > 0:
                    positions.append(Position(side='SELL', entry=bid_close, volume=side_lot,
                                              tp=0, is_anchor=True, open_bar=bar_idx))
                events.append(('RESET_SHORT', bar_idx, {'metric': short_metric, 'flot_closed': short_flot}))

            # Update cycle reference
            cycle_start_balance = balance
            threshold_usd = cycle_start_balance * (cfg.reset_pct / 100.0)

        # === STEP 7: Drift valve ===
        if cfg.drift_usd > 0:
            drift = abs(bar_close - grid_anchor)
            time_since_recoloc = bar_idx - grid_recolocated_at_bar
            if drift > cfg.drift_usd and time_since_recoloc > cfg.drift_min:
                events.append(('DRIFT_RECOLOC', bar_idx, {'drift': drift}))
                # Tanca tot + recolocació
                for p in positions:
                    if p.side == 'BUY':
                        pnl = pnl_buy(p.entry, bid_close, p.volume) - (cfg.spread * p.volume * 100 / 2)
                    else:
                        pnl = pnl_sell(p.entry, ask_close, p.volume) - (cfg.spread * p.volume * 100 / 2)
                    balance += pnl
                positions.clear()
                drift_recolocations += 1
                grid_anchor = snap_to_grid(bar_close, bar_close, cfg.spacing)
                grid_recolocated_at_bar = bar_idx
                cycle_start_balance = balance
                threshold_usd = cycle_start_balance * (cfg.reset_pct / 100.0)

        # === STEP 8: Update equity curve + DD tracking ===
        equity = balance + total_flotant(positions, 'BUY', bid_close, ask_close) + \
                 total_flotant(positions, 'SELL', bid_close, ask_close)

        if equity > peak_equity:
            peak_equity = equity
        dd = peak_equity - equity
        dd_pct = (dd / peak_equity) * 100 if peak_equity > 0 else 0
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
            max_dd_usd = dd

        # Sample equity curve every 100 bars to save memory
        if bar_idx % 100 == 0:
            equity_curve.append([bar_idx, round(equity, 2)])

    # End of backtest — close everything for final accounting
    if not killed:
        last_row = df.iloc[-1]
        bid_close = last_row['close']
        ask_close = last_row['close'] + cfg.spread
        for p in positions:
            if p.side == 'BUY':
                pnl = pnl_buy(p.entry, bid_close, p.volume) - (cfg.spread * p.volume * 100 / 2)
            else:
                pnl = pnl_sell(p.entry, ask_close, p.volume) - (cfg.spread * p.volume * 100 / 2)
            balance += pnl

    final_balance = balance
    final_equity = balance  # all closed at end
    profit_total = final_balance - initial_balance
    profit_pct = (profit_total / initial_balance) * 100

    return BacktestResult(
        config=asdict(cfg),
        final_balance=round(final_balance, 2),
        final_equity=round(final_equity, 2),
        initial_balance=initial_balance,
        profit_total=round(profit_total, 2),
        profit_pct=round(profit_pct, 4),
        max_dd_pct=round(max_dd_pct, 4),
        max_dd_usd=round(max_dd_usd, 2),
        killed=killed,
        killed_at_bar=killed_at,
        long_resets=long_resets,
        short_resets=short_resets,
        harvest_side_count=harvest_side_count,
        harvest_balanced_count=harvest_balanced_count,
        drift_recolocations=drift_recolocations,
        total_positions_opened=total_pos_opened,
        total_tps_fired=total_tps_fired,
        equity_curve=equity_curve,
        events=events[:200],  # limita events guardats
    )


if __name__ == "__main__":
    import pandas as pd
    df = pd.read_csv('data/xauusd_m1.csv')
    df['time'] = pd.to_datetime(df['time'])
    print(f"Loaded {len(df)} bars from {df.iloc[0]['time']} to {df.iloc[-1]['time']}")
    print(f"Price range: {df['low'].min():.2f} - {df['high'].max():.2f}")

    # Test config simple
    cfg = GridConfig(name="test_baseline", lot=0.01, spacing=1.0, levels=5, tp_usd=1.0,
                     reset_pct=0.25, max_dd_pct=20.0, spread=0.30)
    print(f"\nRunning baseline: {cfg.name}")
    result = run_backtest(df, cfg)
    print(f"  Final balance: ${result.final_balance:.2f}  (+{result.profit_pct:.2f}%)")
    print(f"  Max DD: {result.max_dd_pct:.2f}% (${result.max_dd_usd:.2f})")
    print(f"  Resets: LONG={result.long_resets}, SHORT={result.short_resets}")
    print(f"  TPs fired: {result.total_tps_fired}")
    print(f"  Positions opened: {result.total_positions_opened}")
    print(f"  Killed: {result.killed}")
