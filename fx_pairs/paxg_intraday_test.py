"""
INTRADAY BACKTEST PAXG GRID BOT
Counts actual cycles based on hourly data (not just daily OHLC).
Tests range widths around ±$100 from daily center.
"""

import yfinance as yf
import pandas as pd
import numpy as np

# Get 60 days of hourly gold data (max yfinance allows)
print("Fetching gold futures 1H data (60 days)...")
gold_1h = yf.download("GC=F", period="60d", interval="1h", progress=False, auto_adjust=False)
if isinstance(gold_1h.columns, pd.MultiIndex):
    gold_1h.columns = gold_1h.columns.get_level_values(0)
gold_1h = gold_1h.dropna().reset_index()
gold_1h['Date'] = pd.to_datetime(gold_1h.iloc[:, 0]).dt.tz_localize(None)
print(f"Loaded {len(gold_1h)} hourly bars")
print(f"Range: {gold_1h['Date'].iloc[0]} to {gold_1h['Date'].iloc[-1]}")

def count_cycles(prices, grid_levels):
    """
    Count REAL grid bot cycles by tracking which grid levels get crossed.
    A 'cycle' = price crosses level UP and then back DOWN (or vice versa).
    """
    if len(grid_levels) < 2 or len(prices) < 2:
        return 0
    cycles = 0
    last_position = {}  # which side of each level was the last price
    for level in grid_levels:
        last_position[level] = None

    for px in prices:
        for level in grid_levels:
            current_side = 'above' if px > level else 'below'
            if last_position[level] is not None and last_position[level] != current_side:
                # Crossed this level
                cycles += 0.5  # each cross = half cycle (need round trip for full)
            last_position[level] = current_side
    return cycles

def simulate_grid_real(hourly_bars, pred_low, pred_high, capital, grids, fee_rt=0.001):
    """
    Realistic simulation: tracks intraday price moves through grid levels.
    """
    if pred_high <= pred_low or grids < 2 or len(hourly_bars) == 0:
        return 0.0, 0

    grid_step = (pred_high - pred_low) / grids
    capital_per_grid = capital / grids
    avg_price = (pred_low + pred_high) / 2

    gross_pct = grid_step / avg_price
    net_pct = gross_pct - fee_rt
    if net_pct <= 0:
        return 0.0, 0
    profit_per_cycle = net_pct * capital_per_grid

    # Generate grid levels
    grid_levels = [pred_low + i * grid_step for i in range(grids + 1)]

    # Extract all prices (open + high + low + close from each hour)
    all_prices = []
    for _, row in hourly_bars.iterrows():
        all_prices.extend([row['Open'], row['High'], row['Low'], row['Close']])

    cycles = count_cycles(all_prices, grid_levels)
    profit = cycles * profit_per_cycle
    return profit, cycles

# Group hourly data by day
gold_1h['date_only'] = gold_1h['Date'].dt.date
daily_groups = gold_1h.groupby('date_only')

# Get one previous day for predictor
gold_1h_sorted = gold_1h.sort_values('Date').reset_index(drop=True)

# Test configurations (range widths around current price)
configs = [
    ('TIGHT  (±$50, w=$100)',  50, 8),
    ('MED-T  (±$75, w=$150)',  75, 10),
    ('STD    (±$100, w=$200)', 100, 12),
    ('WIDE   (±$150, w=$300)', 150, 15),
    ('XWIDE  (±$200, w=$400)', 200, 18),
]

CAPITAL = 1000
ADJUST_COST = 0.3

print(f"\n{'Config':<25} {'AvgCycles/day':<15} {'AvgProfit/d':<13} {'Total Cycles':<15} {'Total$':<10} {'APR (60d)':<10}")
print("=" * 100)

# For each day, predict range based on previous day close
date_list = sorted(daily_groups.groups.keys())

for cname, half_width, grids in configs:
    daily_results = []
    prev_close = None
    for i, d in enumerate(date_list):
        day_bars = daily_groups.get_group(d).sort_values('Date').reset_index(drop=True)
        if len(day_bars) < 4: continue
        day_open = day_bars['Open'].iloc[0]
        # Use previous day close as center, or open if first day
        if prev_close is None:
            center = day_open
        else:
            center = prev_close
        pred_low = center - half_width
        pred_high = center + half_width
        profit, cycles = simulate_grid_real(day_bars, pred_low, pred_high, CAPITAL, grids)
        daily_results.append({'date': d, 'profit': profit, 'cycles': cycles,
                              'pred_low': pred_low, 'pred_high': pred_high,
                              'day_low': day_bars['Low'].min(), 'day_high': day_bars['High'].max()})
        prev_close = day_bars['Close'].iloc[-1]
    df = pd.DataFrame(daily_results)
    if len(df) == 0: continue
    days = len(df)
    total = df['profit'].sum()
    avg_cycles = df['cycles'].mean()
    avg_profit = df['profit'].mean()
    total_cycles = df['cycles'].sum()
    apr = (total / CAPITAL) / (days / 365) * 100
    print(f"{cname:<25} {avg_cycles:<14.2f} ${avg_profit:<11.3f} {total_cycles:<14.1f} ${total:<8.2f} {apr:<9.1f}%")

print(f"\nDays analyzed: {len(date_list)}")
print(f"Methodology: tracks every hourly price (OHLC each hour) crossing grid levels")
