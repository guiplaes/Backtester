"""
Backtest of adaptive vs static range prediction for PAXG/gold grid bot.

Compares:
1. ADAPTIVE: predicts next day's range using ATR/recent volatility
2. STATIC: fixed wide range covering 95% of historical days
3. NAIVE: same range every day (yesterday's high-low extended 50%)

Metrics:
- Hit rate (% of days where actual range is inside predicted)
- Simulated grid profit per day
- Adjustment costs deducted
- Final net profit comparison
"""

import yfinance as yf
import pandas as pd
import numpy as np

# Get gold futures (GC=F) — proxy for PAXG since PAXG tracks XAU
print("Fetching 3 years of gold daily data...")
gold = yf.download("GC=F", period="3y", interval="1d", progress=False, auto_adjust=False)
if isinstance(gold.columns, pd.MultiIndex):
    gold.columns = gold.columns.get_level_values(0)

gold = gold.dropna().reset_index()
print(f"Loaded {len(gold)} daily bars")
print(f"Range: {gold['Date'].iloc[0].date()} to {gold['Date'].iloc[-1].date()}")
print(f"Price range: ${gold['Low'].min():.2f} - ${gold['High'].max():.2f}")

# ----- Compute ATR -----
def compute_atr(df, period=14):
    high = df['High']
    low = df['Low']
    close = df['Close'].shift(1)
    tr = pd.concat([(high - low), (high - close).abs(), (low - close).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()

gold['ATR14'] = compute_atr(gold, 14)
gold['ATR7']  = compute_atr(gold, 7)
gold['day_range'] = gold['High'] - gold['Low']

# ----- Predictors -----

def predict_adaptive(row_idx, df, k=1.5):
    """Adaptive: center on yesterday's close, +/- k × ATR(7) for tighter prediction."""
    prev = df.iloc[row_idx - 1]
    atr = prev['ATR7'] if not np.isnan(prev['ATR7']) else prev['ATR14']
    center = prev['Close']
    half = k * atr
    return center - half, center + half

def predict_static(row_idx, df, k=3.0):
    """Static: same wide range every day, based on long-term mean and stdev of daily range."""
    history = df.iloc[max(0, row_idx-60):row_idx]
    if len(history) < 10:
        return None, None
    avg_close = history['Close'].mean()
    avg_range = history['day_range'].mean()
    half = (avg_range * k) / 2
    return avg_close - half, avg_close + half

def predict_naive(row_idx, df, expansion=1.5):
    """Naive: yesterday's high-low extended by 50%."""
    prev = df.iloc[row_idx - 1]
    center = (prev['High'] + prev['Low']) / 2
    half = (prev['High'] - prev['Low']) * expansion / 2
    return center - half, center + half

# ----- Backtest -----

def simulate_grid_day(actual_high, actual_low, actual_close, prev_close,
                     pred_low, pred_high, capital=1000, grids=15,
                     fee_per_round_trip=0.001, adjust_cost=0.3):
    """
    Simulate a 1-day grid bot.
    Returns: profit_usd, was_in_range, was_adjusted
    """
    if pred_low is None or pred_high is None:
        return 0.0, False, False

    grid_step = (pred_high - pred_low) / grids
    if grid_step <= 0:
        return 0.0, False, False

    capital_per_grid = capital / grids
    avg_price = (pred_low + pred_high) / 2

    # Approximate: cycles depend on how much price oscillates within range
    # Simple model: cycles = (actual movement within predicted range) / grid_step
    eff_high = min(actual_high, pred_high)
    eff_low  = max(actual_low, pred_low)
    movement_in_range = max(0, eff_high - eff_low)

    # Each grid crossed = potential profit
    # But cycles need round trips. Roughly: cycles = movement / (grid_step × 2)
    # Multiplied by ~1.5 for intra-day oscillation
    cycles = (movement_in_range / (grid_step * 2)) * 1.5

    # Profit per cycle (after fees)
    gross_pct = grid_step / avg_price
    net_pct = gross_pct - fee_per_round_trip
    profit_per_cycle = max(0, net_pct * capital_per_grid)

    profit = cycles * profit_per_cycle

    # Penalty if actual range broke out of prediction
    breakout_below = actual_low < pred_low
    breakout_above = actual_high > pred_high
    in_range = not (breakout_below or breakout_above)

    if not in_range:
        # If breakout, bot got stuck on one side
        # Approximate the unrealized PnL impact
        if breakout_below:
            # Bot fully long PAXG, price below buy levels
            unrealized = (actual_close - pred_low) * (capital / avg_price)
            profit += min(0, unrealized * 0.3)  # 30% of unrealized considered "soft loss"
        if breakout_above:
            # Bot fully USDT, missed upside (opportunity cost, not loss)
            pass

    return profit, in_range, False  # adjustment cost subtracted separately

# ----- Run backtests -----

LOOKBACK = 20  # need history for predictors
CAPITAL = 1000
GRIDS = 15
FEE_PER_RT = 0.001
ADJUST_COST = 0.3

results_adaptive = []
results_static = []
results_naive = []

prev_pred_adaptive = (None, None)

for i in range(LOOKBACK, len(gold)):
    row = gold.iloc[i]
    actual_high = row['High']
    actual_low  = row['Low']
    actual_close = row['Close']
    prev_close = gold.iloc[i-1]['Close']

    # ADAPTIVE
    pred_low_a, pred_high_a = predict_adaptive(i, gold)

    # Check if we need to adjust (cost incurred)
    adjusted = False
    if prev_pred_adaptive != (pred_low_a, pred_high_a):
        # New prediction differs from previous -> count adjustment
        adjusted = (prev_pred_adaptive[0] is not None)
    prev_pred_adaptive = (pred_low_a, pred_high_a)

    profit_a, in_range_a, _ = simulate_grid_day(
        actual_high, actual_low, actual_close, prev_close,
        pred_low_a, pred_high_a, CAPITAL, GRIDS, FEE_PER_RT, ADJUST_COST)
    if adjusted:
        profit_a -= ADJUST_COST

    results_adaptive.append({
        'date': row['Date'], 'pred_low': pred_low_a, 'pred_high': pred_high_a,
        'actual_low': actual_low, 'actual_high': actual_high,
        'profit': profit_a, 'in_range': in_range_a, 'adjusted': adjusted
    })

    # STATIC
    pred_low_s, pred_high_s = predict_static(i, gold)
    profit_s, in_range_s, _ = simulate_grid_day(
        actual_high, actual_low, actual_close, prev_close,
        pred_low_s, pred_high_s, CAPITAL, GRIDS, FEE_PER_RT, ADJUST_COST)
    results_static.append({
        'date': row['Date'], 'pred_low': pred_low_s, 'pred_high': pred_high_s,
        'actual_low': actual_low, 'actual_high': actual_high,
        'profit': profit_s, 'in_range': in_range_s
    })

    # NAIVE
    pred_low_n, pred_high_n = predict_naive(i, gold)
    profit_n, in_range_n, _ = simulate_grid_day(
        actual_high, actual_low, actual_close, prev_close,
        pred_low_n, pred_high_n, CAPITAL, GRIDS, FEE_PER_RT, ADJUST_COST)
    profit_n -= ADJUST_COST  # naive readjusts every day
    results_naive.append({
        'date': row['Date'], 'pred_low': pred_low_n, 'pred_high': pred_high_n,
        'actual_low': actual_low, 'actual_high': actual_high,
        'profit': profit_n, 'in_range': in_range_n
    })

df_a = pd.DataFrame(results_adaptive)
df_s = pd.DataFrame(results_static)
df_n = pd.DataFrame(results_naive)

# ----- Reports -----

def report(name, df):
    total = df['profit'].sum()
    days = len(df)
    hit_rate = df['in_range'].mean() * 100
    avg_daily = df['profit'].mean()
    annual_apr = (total / CAPITAL) / (days / 365) * 100
    print(f"\n--- {name} ---")
    print(f"  Days simulated: {days}")
    print(f"  Hit rate (in range): {hit_rate:.1f}%")
    print(f"  Avg daily profit: ${avg_daily:.2f}")
    print(f"  Total profit: ${total:.2f}")
    print(f"  APR (annualized): {annual_apr:.1f}%")
    print(f"  Best day: ${df['profit'].max():.2f}")
    print(f"  Worst day: ${df['profit'].min():.2f}")
    print(f"  Std deviation: ${df['profit'].std():.2f}")

report("ADAPTIVE (ATR x 1.5)", df_a)
report("STATIC (wide range, no adjust)", df_s)
report("NAIVE (yesterday range x 1.5, daily adjust)", df_n)

# Save trades for later analysis
df_a.to_csv('C:/Users/Administrator/Desktop/MT4 Claude/fx_pairs/adaptive_results.csv', index=False)
df_s.to_csv('C:/Users/Administrator/Desktop/MT4 Claude/fx_pairs/static_results.csv', index=False)
df_n.to_csv('C:/Users/Administrator/Desktop/MT4 Claude/fx_pairs/naive_results.csv', index=False)

print("\n[OK] Results saved to *_results.csv")
