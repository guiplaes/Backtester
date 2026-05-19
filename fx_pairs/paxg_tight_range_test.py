"""
Test tight-range predictor variations.
Compares different k values (range tightness) to see if tighter is better.
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

def simulate(actual_high, actual_low, actual_close, pred_low, pred_high,
             capital, grids, fee_rt=0.001):
    """Better simulation: count complete grid cycles within day."""
    if pred_high <= pred_low or grids < 2:
        return 0.0, False

    grid_step = (pred_high - pred_low) / grids
    capital_per_grid = capital / grids
    avg_price = (pred_low + pred_high) / 2

    # Gross profit per cycle
    gross_pct = grid_step / avg_price
    net_pct = gross_pct - fee_rt
    profit_per_cycle = max(0, net_pct * capital_per_grid)

    # Calculate effective oscillation INSIDE predicted range
    eff_high = min(actual_high, pred_high)
    eff_low  = max(actual_low, pred_low)
    if eff_high <= eff_low:
        return 0.0, False

    # Cycles approximation: assume price makes 2 round trips of typical magnitude
    # Daily range gives one full sweep. Real oscillation typically gives 1-3 cycles per grid
    osc_amplitude = eff_high - eff_low
    # Each grid level potentially cycles 1-2 times per day if price oscillates
    cycles_per_grid_active = 1.0
    grids_traversed = osc_amplitude / grid_step
    total_cycles = grids_traversed * cycles_per_grid_active

    profit = total_cycles * profit_per_cycle

    in_range = (actual_low >= pred_low) and (actual_high <= pred_high)

    # Breakout penalty
    if not in_range:
        # If broke below, bot has all PAXG and may have unrealized loss
        if actual_low < pred_low:
            unreal_loss = (pred_low - actual_low) * (capital / avg_price) * 0.4
            profit -= unreal_loss
        # Above breakout = opportunity cost, less direct loss

    return profit, in_range

# Test different k values (tightness)
configs = [
    ('VERY TIGHT (k=0.5)', 0.5, 8),
    ('TIGHT (k=0.8)',     0.8, 10),
    ('MED (k=1.0)',       1.0, 12),
    ('STD (k=1.5)',       1.5, 15),
    ('WIDE (k=2.0)',      2.0, 20),
    ('VERY WIDE (k=3.0)', 3.0, 30),
]

CAPITAL = 1000
LOOKBACK = 20
ADJUST_COST = 0.3

print(f"\n{'Config':<22} {'Range/day':<12} {'Hit%':<8} {'AvgD$':<8} {'Total$':<10} {'APR%':<8} {'Worst$':<8}")
print("=" * 85)

for name, k, grids in configs:
    profits = []
    in_range_count = 0
    days = 0
    prev_pred = (None, None)
    range_widths = []
    for i in range(LOOKBACK, len(gold)):
        row = gold.iloc[i]
        prev = gold.iloc[i-1]
        atr = prev['ATR7']
        if np.isnan(atr): continue
        center = prev['Close']
        half = k * atr
        pred_low = center - half
        pred_high = center + half
        range_widths.append(pred_high - pred_low)

        adjusted = prev_pred != (pred_low, pred_high) and prev_pred[0] is not None
        prev_pred = (pred_low, pred_high)

        profit, in_range = simulate(
            row['High'], row['Low'], row['Close'],
            pred_low, pred_high, CAPITAL, grids
        )
        if adjusted:
            profit -= ADJUST_COST

        profits.append(profit)
        if in_range: in_range_count += 1
        days += 1

    arr = np.array(profits)
    total = arr.sum()
    hit = in_range_count / days * 100 if days > 0 else 0
    apr = (total / CAPITAL) / (days / 365) * 100 if days > 0 else 0
    avg_range = np.mean(range_widths)
    print(f"{name:<22} ${avg_range:<10.0f} {hit:<7.1f}% ${arr.mean():<7.2f} ${total:<9.2f} {apr:<7.1f}% ${arr.min():<7.2f}")

print("\n--- Summary ---")
print("This tests if tighter ranges (high hit rate from prediction) give better APR.")
print("If the math is right, tight should beat wide IF predictions are accurate.")
