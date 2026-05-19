"""
FX Pairs Trading Backtest v2 - Multi-pair, tighter thresholds, realistic sizing
"""
import yfinance as yf
import numpy as np
import pandas as pd

# Pairs to test (correlated pairs)
PAIR_GROUPS = [
    ('EURUSD=X', 'GBPUSD=X', 'EU/GB'),    # 0.94 correlation
    ('AUDUSD=X', 'NZDUSD=X', 'AU/NZ'),    # 0.90+ correlation
    ('USDCAD=X', 'USDCHF=X', 'CA/CH'),    # safer havens
]

ROLL = 60          # rolling window for hedge ratio + zscore
ENTRY_Z = 1.5      # was 2.0
EXIT_Z = 0.3       # was 0.5

SPREAD_PIPS = {
    'EURUSD=X': 1.0, 'GBPUSD=X': 1.5,
    'AUDUSD=X': 1.5, 'NZDUSD=X': 2.0,
    'USDCAD=X': 1.8, 'USDCHF=X': 2.0,
}
PIP = 0.0001
LOT_SIZE_BASE = 100000   # standard lot

def fetch(symbol):
    df = yf.download(symbol, period="5y", interval="1d", progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df['Close']

def run_pair(sym1, sym2, label):
    px1 = fetch(sym1)
    px2 = fetch(sym2)
    df = pd.DataFrame({'p1': px1, 'p2': px2}).dropna()

    # Rolling hedge ratio with PROPER no-look-ahead: use prior-day window
    df['beta'] = np.nan
    df['spread'] = np.nan
    df['z'] = np.nan
    for i in range(ROLL, len(df)):
        window = df.iloc[i-ROLL:i]   # NO current day included
        beta = np.cov(window['p1'], window['p2'])[0,1] / np.var(window['p2'])
        spread_w = window['p1'] - beta * window['p2']
        mean_w = spread_w.mean()
        std_w = spread_w.std()
        cur_spread = df['p1'].iloc[i] - beta * df['p2'].iloc[i]
        df.iloc[i, df.columns.get_loc('beta')] = beta
        df.iloc[i, df.columns.get_loc('spread')] = cur_spread
        df.iloc[i, df.columns.get_loc('z')] = (cur_spread - mean_w) / std_w if std_w > 0 else 0

    # Costs
    cost1 = SPREAD_PIPS[sym1] * PIP * LOT_SIZE_BASE
    cost2 = SPREAD_PIPS[sym2] * PIP * LOT_SIZE_BASE

    # Simulate
    pos = 0
    entry_p1 = entry_p2 = entry_beta = 0
    entry_date = None
    trades = []
    for i in range(ROLL + 1, len(df)):
        z = df['z'].iloc[i]
        if np.isnan(z): continue
        p1 = df['p1'].iloc[i]
        p2 = df['p2'].iloc[i]
        date = df.index[i]
        if pos == 0:
            if z > ENTRY_Z:
                pos = -1
                entry_p1, entry_p2, entry_beta, entry_date = p1, p2, df['beta'].iloc[i], date
            elif z < -ENTRY_Z:
                pos = +1
                entry_p1, entry_p2, entry_beta, entry_date = p1, p2, df['beta'].iloc[i], date
        else:
            if abs(z) < EXIT_Z:
                p1_pnl = (p1 - entry_p1) * LOT_SIZE_BASE * pos
                p2_pnl = (p2 - entry_p2) * LOT_SIZE_BASE * (-pos) * entry_beta
                gross = p1_pnl + p2_pnl
                cost = cost1 + cost2 * entry_beta
                net = gross - cost
                trades.append({'pair': label, 'entry': entry_date, 'exit': date,
                              'days': (date - entry_date).days, 'gross': gross, 'cost': cost, 'net': net})
                pos = 0
    return trades

all_trades = []
print("Fetching + backtesting 3 pair groups...")
for sym1, sym2, label in PAIR_GROUPS:
    print(f"  {label}...")
    trades = run_pair(sym1, sym2, label)
    all_trades.extend(trades)
    if trades:
        net = sum(t['net'] for t in trades)
        wins = sum(1 for t in trades if t['net'] > 0)
        print(f"    -> {len(trades)} trades, net ${net:,.0f}, WR {wins/len(trades)*100:.0f}%")

if not all_trades:
    print("No trades!"); exit()

td = pd.DataFrame(all_trades).sort_values('entry').reset_index(drop=True)
total_net = td['net'].sum()
total_gross = td['gross'].sum()
wins = td[td['net'] > 0]
losses = td[td['net'] < 0]
pf = wins['net'].sum() / abs(losses['net'].sum()) if len(losses) > 0 else np.inf

# Equity + DD
td['cum'] = td['net'].cumsum()
peak = td['cum'].cummax()
dd = (td['cum'] - peak).min()

print(f"\n=========== AGGREGATE (3 pairs, 5 years) ===========")
print(f"Total trades: {len(td)}")
print(f"Wins: {len(wins)} ({len(wins)/len(td)*100:.1f}%)")
print(f"Gross: ${total_gross:,.2f}")
print(f"Net (after spread): ${total_net:,.2f}")
print(f"Profit Factor: {pf:.2f}")
print(f"Max DD: ${dd:,.2f}")
print(f"Profit/DD: {total_net/abs(dd):.2f}x")
print(f"Avg per trade: ${total_net/len(td):,.2f}")
print(f"Avg days held: {td['days'].mean():.1f}")

# By pair breakdown
print(f"\n--- Per pair ---")
for pair in td['pair'].unique():
    sub = td[td['pair'] == pair]
    sub_net = sub['net'].sum()
    sub_wr = len(sub[sub['net']>0])/len(sub)*100
    print(f"  {pair}: {len(sub)} trades, net ${sub_net:,.0f}, WR {sub_wr:.0f}%")

# Sizing analysis
print(f"\n--- SIZING ANALYSIS on $10k account ---")
for lot_frac, label in [(0.1, '0.1 lot'), (0.5, '0.5 lot'), (1.0, '1.0 lot')]:
    scaled_net = total_net * lot_frac
    scaled_dd = abs(dd) * lot_frac
    # margin needed approx: notional / leverage 30
    avg_notional = 1.15 * LOT_SIZE_BASE * lot_frac
    margin = avg_notional / 30
    print(f"  {label}: net ${scaled_net:,.0f} ({scaled_net/10000*100:.1f}% account), DD ${scaled_dd:,.0f} ({scaled_dd/10000*100:.1f}%), margin/leg ~${margin:.0f}")

annual_total = (total_net / 10000) / 5 * 100
print(f"\n  Annualized at 1.0 lot: ~{annual_total:.1f}%/year on $10k")
