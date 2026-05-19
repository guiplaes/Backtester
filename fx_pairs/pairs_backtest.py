"""
FX Pairs Trading Backtest — EURUSD vs GBPUSD
Mean reversion of the spread using z-score signals.
Includes realistic spread costs.
"""
import yfinance as yf
import numpy as np
import pandas as pd

# --- Fetch data ----------------------------------------------------------
print("Fetching EURUSD + GBPUSD daily data (5 years)...")
eu = yf.download("EURUSD=X", period="5y", interval="1d", progress=False, auto_adjust=False)
gb = yf.download("GBPUSD=X", period="5y", interval="1d", progress=False, auto_adjust=False)

# Flatten multi-index columns if needed
if isinstance(eu.columns, pd.MultiIndex):
    eu.columns = eu.columns.get_level_values(0)
if isinstance(gb.columns, pd.MultiIndex):
    gb.columns = gb.columns.get_level_values(0)

# Align on common dates
df = pd.DataFrame({
    'EU': eu['Close'],
    'GB': gb['Close'],
}).dropna()

print(f"\nDataset: {len(df)} daily bars")
print(f"Range: {df.index[0].date()} to {df.index[-1].date()}")
print(f"Correlation EUR/USD vs GBP/USD: {df['EU'].corr(df['GB']):.4f}")

# --- Compute hedge ratio (OLS regression) --------------------------------
# Use rolling 60-day window for hedge ratio + spread mean/std
ROLL = 60
df['beta'] = np.nan
df['spread'] = np.nan
df['spread_mean'] = np.nan
df['spread_std'] = np.nan
df['zscore'] = np.nan

for i in range(ROLL, len(df)):
    window = df.iloc[i-ROLL:i]
    # OLS: EU = beta * GB
    beta = np.cov(window['EU'], window['GB'])[0,1] / np.var(window['GB'])
    df.iloc[i, df.columns.get_loc('beta')] = beta
    spread = df['EU'].iloc[i] - beta * df['GB'].iloc[i]
    df.iloc[i, df.columns.get_loc('spread')] = spread
    # Compute mean/std of spread over the same window
    spread_window = window['EU'] - beta * window['GB']
    df.iloc[i, df.columns.get_loc('spread_mean')] = spread_window.mean()
    df.iloc[i, df.columns.get_loc('spread_std')] = spread_window.std()
    df.iloc[i, df.columns.get_loc('zscore')] = (spread - spread_window.mean()) / spread_window.std()

# --- Strategy ------------------------------------------------------------
# Signals:
# z > +2.0: spread too high to SHORT EU + LONG GB×beta
# z < -2.0: spread too low to LONG EU + SHORT GB×beta
# Exit when |z| < 0.5

ENTRY_Z = 2.0
EXIT_Z = 0.5

# Cost parameters (one-pip spread per leg, paid twice per round trip)
SPREAD_EU_PIP = 1.0   # 1 pip = 0.0001
SPREAD_GB_PIP = 1.5   # 1.5 pip
PIP = 0.0001
LOT_SIZE = 100000     # standard lot (100k base currency)

# Cost per leg per round trip (entry + exit cross spread once each = 2× half-spread = 1 spread)
cost_eu_usd = SPREAD_EU_PIP * PIP * LOT_SIZE  # USD value of EU spread = $10 per std lot
cost_gb_usd = SPREAD_GB_PIP * PIP * LOT_SIZE  # USD value of GB spread = $15

# --- Simulate ------------------------------------------------------------
position = 0  # 0 = flat, +1 = long spread (long EU short GB), -1 = short spread
entry_eu = 0
entry_gb = 0
entry_beta = 0
trades = []

for i in range(ROLL + 1, len(df)):
    z = df['zscore'].iloc[i]
    if np.isnan(z):
        continue
    eu_px = df['EU'].iloc[i]
    gb_px = df['GB'].iloc[i]
    date = df.index[i]

    if position == 0:
        # Look for entry
        if z > ENTRY_Z:
            # Short spread: short EU, long GB×beta
            position = -1
            entry_eu = eu_px
            entry_gb = gb_px
            entry_beta = df['beta'].iloc[i]
            entry_date = date
        elif z < -ENTRY_Z:
            position = +1
            entry_eu = eu_px
            entry_gb = gb_px
            entry_beta = df['beta'].iloc[i]
            entry_date = date
    else:
        # In position — check exit
        if abs(z) < EXIT_Z:
            # Compute PnL
            eu_pnl = (eu_px - entry_eu) * LOT_SIZE * position
            gb_pnl = (gb_px - entry_gb) * LOT_SIZE * (-position) * entry_beta
            gross_pnl = eu_pnl + gb_pnl
            cost = cost_eu_usd + cost_gb_usd * entry_beta
            net_pnl = gross_pnl - cost
            trades.append({
                'entry_date': entry_date,
                'exit_date': date,
                'days_held': (date - entry_date).days,
                'direction': 'LONG_SPREAD' if position == +1 else 'SHORT_SPREAD',
                'entry_eu': entry_eu, 'exit_eu': eu_px,
                'entry_gb': entry_gb, 'exit_gb': gb_px,
                'beta': entry_beta,
                'gross_pnl': gross_pnl,
                'cost': cost,
                'net_pnl': net_pnl,
                'z_entry': df['zscore'].iloc[df.index.get_loc(entry_date)],
                'z_exit': z,
            })
            position = 0

# --- Results -------------------------------------------------------------
if len(trades) == 0:
    print("\nNo trades generated.")
    exit()

td = pd.DataFrame(trades)
total_net = td['net_pnl'].sum()
total_gross = td['gross_pnl'].sum()
total_cost = td['cost'].sum()
wins = td[td['net_pnl'] > 0]
losses = td[td['net_pnl'] < 0]
wr = len(wins) / len(td) * 100 if len(td) > 0 else 0
pf = wins['net_pnl'].sum() / abs(losses['net_pnl'].sum()) if len(losses) > 0 else np.inf

# Max DD on equity curve
equity = td['net_pnl'].cumsum()
peak = equity.cummax()
dd = (equity - peak).min()

print(f"\n=======================================================")
print(f"FX PAIRS TRADING — EURUSD vs GBPUSD (5 years daily)")
print(f"=======================================================")
print(f"Total trades: {len(td)}")
print(f"Wins / Losses: {len(wins)} / {len(losses)} (WR {wr:.1f}%)")
print(f"Total gross PnL: ${total_gross:,.2f}")
print(f"Total cost (spread): ${total_cost:,.2f}")
print(f"Total NET PnL: ${total_net:,.2f}")
print(f"Profit Factor: {pf:.2f}")
print(f"Avg per trade: ${total_net/len(td):,.2f}")
print(f"Avg days held: {td['days_held'].mean():.1f}")
print(f"Max Drawdown: ${dd:,.2f}")
print(f"Profit / DD: {total_net/abs(dd):.2f}x" if dd != 0 else "N/A")

print(f"\n--- Sample of trades -------------------------")
print(td[['entry_date', 'exit_date', 'days_held', 'direction', 'gross_pnl', 'cost', 'net_pnl']].head(10).to_string())

# Save full results
td.to_csv('C:/Users/Administrator/Desktop/MT4 Claude/fx_pairs/trades.csv', index=False)
print(f"\n[OK] Full trades saved to trades.csv")
