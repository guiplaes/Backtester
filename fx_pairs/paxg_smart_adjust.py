"""
SMART ADJUSTMENT BACKTEST
Compares strategies that minimize unnecessary adjustments:

A. ALWAYS ADJUST: change range every day (baseline expensive)
B. NEVER ADJUST: set once, leave forever (cheap but stuck)
C. BOUNDARY TRIGGER: adjust only when price near edge
D. DEVIATION TRIGGER: adjust only when center drifts >X%
E. HYBRID SMART: combine boundary + deviation + macro
"""

import yfinance as yf
import pandas as pd
import numpy as np

gold = yf.download("GC=F", period="3y", interval="1d", progress=False, auto_adjust=False)
if isinstance(gold.columns, pd.MultiIndex):
    gold.columns = gold.columns.get_level_values(0)
gold = gold.dropna().reset_index()

def compute_atr(df, period=14):
    high, low, close = df['High'], df['Low'], df['Close'].shift(1)
    tr = pd.concat([(high - low), (high - close).abs(), (low - close).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()

gold['ATR7'] = compute_atr(gold, 7)

def grid_profit(actual_high, actual_low, pred_low, pred_high,
                capital, grids, fee_rt=0.001):
    if pred_high <= pred_low or grids < 2: return 0.0, False
    grid_step = (pred_high - pred_low) / grids
    capital_per_grid = capital / grids
    avg_price = (pred_low + pred_high) / 2
    eff_high = min(actual_high, pred_high)
    eff_low  = max(actual_low, pred_low)
    if eff_high <= eff_low: return 0.0, False
    osc = eff_high - eff_low
    gross_pct = grid_step / avg_price
    net_pct = gross_pct - fee_rt
    if net_pct <= 0: return 0.0, False
    profit_per_cycle = net_pct * capital_per_grid
    cycles = (osc / grid_step) * 0.6
    in_range = (actual_low >= pred_low) and (actual_high <= pred_high)
    return cycles * profit_per_cycle, in_range

# Strategies
def strategy_always(prev_pred, prev, k=0.8):
    atr = prev['ATR7']
    c = prev['Close']
    h = k * atr
    return c - h, c + h, True  # always adjust

def strategy_never(prev_pred, prev, k=0.8):
    if prev_pred[0] is None:
        atr = prev['ATR7']
        c = prev['Close']
        h = k * atr
        return c - h, c + h, True
    return prev_pred[0], prev_pred[1], False

def strategy_boundary(prev_pred, prev, k=0.8, edge_pct=0.15):
    """Adjust if price within edge_pct of range boundary."""
    if prev_pred[0] is None:
        atr = prev['ATR7']
        c = prev['Close']
        h = k * atr
        return c - h, c + h, True
    pl, ph = prev_pred
    width = ph - pl
    edge = width * edge_pct
    px = prev['Close']
    if px < pl + edge or px > ph - edge:
        atr = prev['ATR7']
        h = k * atr
        return px - h, px + h, True
    return pl, ph, False

def strategy_deviation(prev_pred, prev, k=0.8, drift_pct=0.5):
    """Adjust if predicted center moved >drift_pct × ATR from current center."""
    atr = prev['ATR7']
    c = prev['Close']
    h = k * atr
    new_low, new_high = c - h, c + h
    if prev_pred[0] is None:
        return new_low, new_high, True
    pl, ph = prev_pred
    old_center = (pl + ph) / 2
    new_center = c
    drift = abs(new_center - old_center)
    if drift > drift_pct * atr:
        return new_low, new_high, True
    return pl, ph, False

def strategy_hybrid(prev_pred, prev, k=0.8):
    """Combine boundary + deviation triggers (smart)."""
    if prev_pred[0] is None:
        atr = prev['ATR7']
        c = prev['Close']
        h = k * atr
        return c - h, c + h, True
    pl, ph = prev_pred
    width = ph - pl
    edge = width * 0.15
    px = prev['Close']
    atr = prev['ATR7']
    old_center = (pl + ph) / 2
    drift = abs(px - old_center)

    if px < pl + edge or px > ph - edge or drift > 0.5 * atr:
        h = k * atr
        return px - h, px + h, True
    return pl, ph, False

# Backtest each
CAPITAL = 1000
LOOKBACK = 20
ADJUST_COST = 0.3
GRIDS = 10

strategies = [
    ('A. ALWAYS adjust',    strategy_always),
    ('B. NEVER adjust',     strategy_never),
    ('C. BOUNDARY trigger', strategy_boundary),
    ('D. DEVIATION trig',   strategy_deviation),
    ('E. HYBRID smart',     strategy_hybrid),
]

print(f"\n{'Strategy':<24} {'Hit%':<7} {'Adjusts':<10} {'TotalProfit':<13} {'AdjustCost':<12} {'Net':<10} {'APR%':<8}")
print("=" * 105)

for name, fn in strategies:
    prev_pred = (None, None)
    profits = []
    in_range_count = 0
    days = 0
    adjusts = 0
    for i in range(LOOKBACK, len(gold)):
        row = gold.iloc[i]
        prev = gold.iloc[i-1]
        if np.isnan(prev['ATR7']): continue
        pred_low, pred_high, adjusted = fn(prev_pred, prev)
        if adjusted: adjusts += 1
        prev_pred = (pred_low, pred_high)
        profit, in_range = grid_profit(
            row['High'], row['Low'], pred_low, pred_high, CAPITAL, GRIDS
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
    cost = adjusts * ADJUST_COST
    gross = total + cost
    print(f"{name:<24} {hit:<6.1f}% {adjusts:<9} ${gross:<11.2f} ${cost:<10.2f} ${total:<9.2f} {apr:<7.1f}%")
