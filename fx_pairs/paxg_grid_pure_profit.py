"""
PURE GRID PROFIT BACKTEST
Question: assuming user HOLDS PAXG anyway long-term,
how much CASH profit does grid generate from oscillations?

This DOES NOT penalize:
- Being stuck below range (just HODL)
- Being above range (just miss upside)

Only counts: real cash profit from completed cycles - fees - adjustment costs.
"""

import yfinance as yf
import pandas as pd
import numpy as np

gold = yf.download("GC=F", period="3y", interval="1d", progress=False, auto_adjust=False)
if isinstance(gold.columns, pd.MultiIndex):
    gold.columns = gold.columns.get_level_values(0)
gold = gold.dropna().reset_index()
print(f"Loaded {len(gold)} daily bars")

def compute_atr(df, period=14):
    high, low, close = df['High'], df['Low'], df['Close'].shift(1)
    tr = pd.concat([(high - low), (high - close).abs(), (low - close).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()

gold['ATR7'] = compute_atr(gold, 7)
gold['day_range'] = gold['High'] - gold['Low']

def grid_cycles_profit(actual_high, actual_low, pred_low, pred_high,
                       capital, grids, fee_rt=0.001):
    """
    Estimate real cash profit from grid cycles.
    No penalty for being stuck (user is HODL).
    """
    if pred_high <= pred_low or grids < 2:
        return 0.0, 0, False

    grid_step = (pred_high - pred_low) / grids
    capital_per_grid = capital / grids
    avg_price = (pred_low + pred_high) / 2

    # Effective oscillation INSIDE predicted range
    eff_high = min(actual_high, pred_high)
    eff_low  = max(actual_low, pred_low)
    if eff_high <= eff_low:
        return 0.0, 0, False

    osc_range = eff_high - eff_low

    # Profit per single cycle (one buy + one sell at adjacent grids)
    gross_pct = grid_step / avg_price
    net_pct = gross_pct - fee_rt
    if net_pct <= 0:
        return 0.0, 0, False
    profit_per_cycle = net_pct * capital_per_grid

    # Cycle estimation:
    # For each day, price typically does N grid touches based on intraday volatility
    # Assume ~50-80% of grids crossed get full cycle completion
    grids_touched = osc_range / grid_step
    cycle_efficiency = 0.6  # tunable: pct of touches that complete cycles
    cycles = grids_touched * cycle_efficiency

    profit = cycles * profit_per_cycle
    in_range = (actual_low >= pred_low) and (actual_high <= pred_high)
    return profit, cycles, in_range

# Test multiple configurations
configs = [
    ('VERY TIGHT (k=0.4)', 0.4, 6),
    ('TIGHT (k=0.6)',     0.6, 8),
    ('MED-T (k=0.8)',     0.8, 10),
    ('MED (k=1.0)',       1.0, 12),
    ('STD (k=1.5)',       1.5, 15),
    ('WIDE (k=2.0)',      2.0, 20),
    ('WIDE-2 (k=2.5)',    2.5, 25),
]

CAPITAL = 1000
LOOKBACK = 20
ADJUST_COST = 0.3  # per adjustment

print(f"\n{'Config':<22} {'AvgRange':<10} {'Hit%':<7} {'Cycles/d':<10} {'AvgD$':<8} {'Total$':<10} {'APR%':<8}")
print("=" * 95)

for name, k, grids in configs:
    profits = []
    cycle_counts = []
    in_range_count = 0
    days = 0
    prev_pred = (None, None)
    range_widths = []
    adjusts = 0
    for i in range(LOOKBACK, len(gold)):
        row = gold.iloc[i]
        prev = gold.iloc[i-1]
        atr = prev['ATR7']
        if np.isnan(atr): continue
        center = prev['Close']
        half = k * atr
        pred_low = max(0, center - half)
        pred_high = center + half
        range_widths.append(pred_high - pred_low)

        if prev_pred[0] is not None and prev_pred != (pred_low, pred_high):
            adjusts += 1
        prev_pred = (pred_low, pred_high)

        profit, cycles, in_range = grid_cycles_profit(
            row['High'], row['Low'], pred_low, pred_high, CAPITAL, grids
        )

        # Cost of adjusting (only if adjusted from prev day)
        if adjusts == days + 1:  # adjusted this day
            profit -= ADJUST_COST

        profits.append(profit)
        cycle_counts.append(cycles)
        if in_range: in_range_count += 1
        days += 1

    arr = np.array(profits)
    cyc = np.array(cycle_counts)
    total = arr.sum()
    hit = in_range_count / days * 100 if days > 0 else 0
    apr = (total / CAPITAL) / (days / 365) * 100 if days > 0 else 0
    avg_range = np.mean(range_widths)
    avg_cycles = cyc.mean()
    print(f"{name:<22} ${avg_range:<8.0f} {hit:<6.1f}% {avg_cycles:<9.2f} ${arr.mean():<7.2f} ${total:<9.2f} {apr:<7.1f}%")

print(f"\nTotal adjustments simulated: {adjusts}")
print(f"\nNote: This metric measures CASH PROFIT only.")
print(f"Does NOT penalize being stuck in PAXG below range (user is HODL).")
print(f"Does NOT penalize being all USDT above range (only opportunity cost).")
