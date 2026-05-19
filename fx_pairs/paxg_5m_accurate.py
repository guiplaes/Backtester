"""
ACCURATE INTRADAY BACKTEST - 5-minute resolution
Tracks REAL grid bot trades by following price tick-by-tick (5m closes).
"""

import yfinance as yf
import pandas as pd
import numpy as np

print("Fetching gold futures 5-minute data (60 days)...")
g5 = yf.download("GC=F", period="60d", interval="5m", progress=False, auto_adjust=False)
if isinstance(g5.columns, pd.MultiIndex):
    g5.columns = g5.columns.get_level_values(0)
g5 = g5.dropna().reset_index()
date_col = g5.columns[0]
g5['Date'] = pd.to_datetime(g5[date_col]).dt.tz_localize(None)
print(f"Loaded {len(g5)} 5-min bars")
print(f"Range: {g5['Date'].iloc[0]} to {g5['Date'].iloc[-1]}")

def simulate_grid_bot(prices, pred_low, pred_high, capital, grids, fee_rt=0.001):
    """
    REAL grid bot simulation: tracks state of each grid level.
    Each level has a buy or sell pending.
    When price crosses UP through a sell level -> sell executed, profit captured
    When price crosses DOWN through a buy level -> buy executed (resets the cycle)
    """
    if pred_high <= pred_low or grids < 2 or len(prices) < 2:
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
    grid_levels = sorted([pred_low + i * grid_step for i in range(grids + 1)])

    # State: for each grid level, what's the next expected action?
    # 'buy' means waiting to buy at this level (price needs to go down to it)
    # 'sell' means waiting to sell at this level (price needs to go up to it)
    # Initialize based on starting price
    init_px = prices[0]
    state = {}
    for lvl in grid_levels:
        if lvl < init_px:
            state[lvl] = 'buy'  # below current price -> waiting for price to come down
        else:
            state[lvl] = 'sell'  # above current price -> waiting for price to go up

    cycles_completed = 0
    prev_px = init_px

    for px in prices[1:]:
        for lvl in grid_levels:
            if state[lvl] == 'sell':
                # Price needs to cross UP through this level
                if prev_px < lvl and px >= lvl:
                    # Sell executed
                    state[lvl] = 'buy'  # now wait for price to come back down
                    cycles_completed += 0.5  # half cycle (sell side)
            else:  # 'buy'
                # Price needs to cross DOWN through this level
                if prev_px > lvl and px <= lvl:
                    state[lvl] = 'sell'
                    cycles_completed += 0.5  # half cycle (buy side)
        prev_px = px

    # A "full cycle" = sell + buy round trip = 1 unit
    profit = cycles_completed * profit_per_cycle
    return profit, cycles_completed

# Group by day
g5['date_only'] = g5['Date'].dt.date
daily_groups = g5.groupby('date_only')

configs = [
    ('TIGHT  (±$50, w=$100)',   50, 8),
    ('MED-T  (±$75, w=$150)',   75, 10),
    ('STD    (±$100, w=$200)',  100, 12),
    ('WIDE   (±$150, w=$300)',  150, 15),
    ('XWIDE  (±$200, w=$400)',  200, 18),
]

CAPITAL = 1000
ADJUST_COST = 0.3

print(f"\n{'Config':<25} {'AvgCycles/d':<13} {'AvgProfit/d':<13} {'Total$':<10} {'APR%':<10}")
print("=" * 95)

date_list = sorted(daily_groups.groups.keys())

for cname, half_width, grids in configs:
    results = []
    prev_close = None
    for d in date_list:
        bars = daily_groups.get_group(d).sort_values('Date').reset_index(drop=True)
        if len(bars) < 50: continue  # need enough bars in day
        if prev_close is None:
            center = bars['Open'].iloc[0]
        else:
            center = prev_close
        pred_low = center - half_width
        pred_high = center + half_width

        # Get all 5m closes from this day
        prices = bars['Close'].tolist()
        profit, cycles = simulate_grid_bot(prices, pred_low, pred_high, CAPITAL, grids)
        # Subtract daily adjustment cost
        profit -= ADJUST_COST
        results.append({'date': d, 'profit': profit, 'cycles': cycles})
        prev_close = bars['Close'].iloc[-1]

    df = pd.DataFrame(results)
    if len(df) == 0: continue
    days = len(df)
    total = df['profit'].sum()
    avg_cyc = df['cycles'].mean()
    avg_p = df['profit'].mean()
    apr = (total / CAPITAL) / (days / 365) * 100
    print(f"{cname:<25} {avg_cyc:<12.2f} ${avg_p:<11.3f} ${total:<8.2f} {apr:<9.1f}%")

print(f"\nDays simulated: {len(date_list)}")
print(f"Methodology: 5-min closes, real grid state tracking, daily adjustment cost $0.30")
