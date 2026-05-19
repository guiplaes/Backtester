"""
ACCURATE BACKTEST - 1-MINUTE DATA (last 7 days from yfinance)
Most precise grid bot simulation possible with available data.
"""
import yfinance as yf
import pandas as pd
import numpy as np

print("Fetching gold 1-minute data (last 7 days)...")
g1 = yf.download("GC=F", period="7d", interval="1m", progress=False, auto_adjust=False)
if isinstance(g1.columns, pd.MultiIndex):
    g1.columns = g1.columns.get_level_values(0)
g1 = g1.dropna().reset_index()
date_col = g1.columns[0]
g1['Date'] = pd.to_datetime(g1[date_col]).dt.tz_localize(None)
print(f"Loaded {len(g1)} 1-min bars")
print(f"Range: {g1['Date'].iloc[0]} to {g1['Date'].iloc[-1]}")
print(f"Price range: ${g1['Low'].min():.2f} - ${g1['High'].max():.2f}")

def simulate_grid_bot(prices, pred_low, pred_high, capital, grids, fee_rt=0.001):
    """Real grid bot: tracks state per level, half-cycle on each cross."""
    if pred_high <= pred_low or grids < 2 or len(prices) < 2:
        return 0.0, 0
    grid_step = (pred_high - pred_low) / grids
    capital_per_grid = capital / grids
    avg_price = (pred_low + pred_high) / 2
    gross_pct = grid_step / avg_price
    net_pct = gross_pct - fee_rt
    if net_pct <= 0: return 0.0, 0
    profit_per_cycle = net_pct * capital_per_grid
    grid_levels = sorted([pred_low + i * grid_step for i in range(grids + 1)])
    init_px = prices[0]
    state = {lvl: ('buy' if lvl < init_px else 'sell') for lvl in grid_levels}
    cycles_completed = 0
    prev_px = init_px
    for px in prices[1:]:
        for lvl in grid_levels:
            if state[lvl] == 'sell':
                if prev_px < lvl and px >= lvl:
                    state[lvl] = 'buy'
                    cycles_completed += 0.5
            else:
                if prev_px > lvl and px <= lvl:
                    state[lvl] = 'sell'
                    cycles_completed += 0.5
        prev_px = px
    return cycles_completed * profit_per_cycle, cycles_completed

g1['date_only'] = g1['Date'].dt.date
daily = g1.groupby('date_only')

configs = [
    ('TIGHT  ±$50, w=$100',   50,  8),
    ('MED-T  ±$75, w=$150',   75,  10),
    ('STD    ±$100, w=$200',  100, 12),
    ('WIDE   ±$150, w=$300',  150, 15),
    ('XWIDE  ±$200, w=$400',  200, 18),
]

CAPITAL = 1000
ADJUST_COST = 0.3

print(f"\n{'Config':<25} {'AvgCycles/d':<13} {'AvgProfit/d':<13} {'Total$':<10} {'APR%':<10}")
print("=" * 85)

date_list = sorted(daily.groups.keys())

for cname, half_width, grids in configs:
    results = []
    prev_close = None
    for d in date_list:
        bars = daily.get_group(d).sort_values('Date').reset_index(drop=True)
        if len(bars) < 100: continue
        center = prev_close if prev_close else bars['Open'].iloc[0]
        pred_low = center - half_width
        pred_high = center + half_width
        prices = bars['Close'].tolist()
        profit, cycles = simulate_grid_bot(prices, pred_low, pred_high, CAPITAL, grids)
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

print(f"\nDays: {len(date_list)} | Resolution: 1-minute | Capital: ${CAPITAL}")
