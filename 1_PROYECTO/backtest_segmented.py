"""
Segmented analysis: do we find an edge in any specific session/regime?
Splits the 391 trades from the previous backtest by:
- Hour of day (UTC) — session
- Day of week
- ATR percentile (volatility regime)
- Direction (LONG vs SHORT)
- EMA50 distance at entry (strong vs weak trend)
"""
import pandas as pd
import numpy as np

# Load saved trades from previous run
df = pd.read_csv("backtest_trades_1y.csv", parse_dates=['entry_time', 'exit_time'])

print(f"Total trades: {len(df)}")
print(f"Net P/L: ${df['pnl'].sum():.2f}")
print(f"WR: {(df['pnl']>0).mean()*100:.1f}%")
print()

# ==== Hour of day analysis ====
df['hour'] = pd.to_datetime(df['entry_time']).dt.hour
print("="*80)
print("BY HOUR (UTC):")
print("-"*80)
print(f"{'Hour':>4} | {'Trades':>6} | {'WR':>5} | {'Net P/L':>10} | {'Avg':>8} | {'PF':>6}")
print("-"*80)
hours = df.groupby('hour').agg(
    n=('pnl','count'),
    wins=('pnl', lambda x: (x>0).sum()),
    net=('pnl','sum'),
    avg=('pnl','mean'),
    gp=('pnl', lambda x: x[x>0].sum()),
    gl=('pnl', lambda x: abs(x[x<=0].sum())),
)
hours['wr%'] = hours['wins']/hours['n']*100
hours['pf'] = hours['gp']/hours['gl'].replace(0, np.nan)
for h, row in hours.iterrows():
    sign = "+" if row['net']>0 else ""
    pf_str = f"{row['pf']:.2f}" if not pd.isna(row['pf']) else "  inf"
    print(f"{h:>4} | {int(row['n']):>6} | {row['wr%']:>4.1f}% | {sign}${row['net']:>+9.2f} | ${row['avg']:>+6.2f} | {pf_str:>6}")
print()

# ==== Day of week ====
df['dow'] = pd.to_datetime(df['entry_time']).dt.day_name()
print("="*80)
print("BY DAY OF WEEK:")
print("-"*80)
dow_order = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
for dow in dow_order:
    sub = df[df['dow']==dow]
    if len(sub)==0: continue
    n = len(sub)
    w = (sub['pnl']>0).sum()
    net = sub['pnl'].sum()
    pf_p = sub[sub['pnl']>0]['pnl'].sum()
    pf_l = abs(sub[sub['pnl']<=0]['pnl'].sum())
    pf = pf_p/pf_l if pf_l else 0
    print(f"{dow:>10}: {n:>4} trades | WR {w/n*100:>4.1f}% | Net {net:>+8.2f} | PF {pf:.2f}")
print()

# ==== Direction ====
print("="*80)
print("BY DIRECTION:")
print("-"*80)
for side in ['L', 'S']:
    sub = df[df['side']==side]
    if len(sub)==0: continue
    n = len(sub)
    w = (sub['pnl']>0).sum()
    net = sub['pnl'].sum()
    pf_p = sub[sub['pnl']>0]['pnl'].sum()
    pf_l = abs(sub[sub['pnl']<=0]['pnl'].sum())
    pf = pf_p/pf_l if pf_l else 0
    print(f"  {side}: {n:>4} trades | WR {w/n*100:>4.1f}% | Net {net:>+8.2f} | PF {pf:.2f}")
print()

# ==== ATR quartile (volatility regime) ====
df['atr_q'] = pd.qcut(df['atr'], 4, labels=['Q1_low', 'Q2', 'Q3', 'Q4_high'])
print("="*80)
print("BY ATR QUARTILE (volatility regime):")
print("-"*80)
for q in ['Q1_low', 'Q2', 'Q3', 'Q4_high']:
    sub = df[df['atr_q']==q]
    if len(sub)==0: continue
    n = len(sub)
    w = (sub['pnl']>0).sum()
    net = sub['pnl'].sum()
    pf_p = sub[sub['pnl']>0]['pnl'].sum()
    pf_l = abs(sub[sub['pnl']<=0]['pnl'].sum())
    pf = pf_p/pf_l if pf_l else 0
    atr_range = f"[{sub['atr'].min():.2f}-{sub['atr'].max():.2f}]"
    print(f"  {q} {atr_range}: {n:>4} | WR {w/n*100:>4.1f}% | Net {net:>+8.2f} | PF {pf:.2f}")
print()

# ==== Combined: London/NY sessions only? ====
print("="*80)
print("SESSION FILTERS:")
print("-"*80)
sessions = {
    'Asia (00-06 UTC)': df[df['hour'].between(0,5)],
    'London open (06-09)': df[df['hour'].between(6,8)],
    'London (09-13)': df[df['hour'].between(9,12)],
    'London/NY overlap (13-16)': df[df['hour'].between(13,15)],
    'NY (16-21)': df[df['hour'].between(16,20)],
    'After NY (21-23)': df[df['hour'].between(21,23)],
}
for name, sub in sessions.items():
    if len(sub)==0: continue
    n = len(sub)
    w = (sub['pnl']>0).sum()
    net = sub['pnl'].sum()
    pf_p = sub[sub['pnl']>0]['pnl'].sum()
    pf_l = abs(sub[sub['pnl']<=0]['pnl'].sum())
    pf = pf_p/pf_l if pf_l else 0
    avg = sub['pnl'].mean()
    print(f"  {name:>26}: {n:>4} | WR {w/n*100:>4.1f}% | Net {net:>+8.2f} | Avg {avg:>+6.2f} | PF {pf:.2f}")
print()

# ==== Best subset: all profitable filters combined ====
print("="*80)
print("HUNTING FOR PROFITABLE SUBSET:")
print("-"*80)
# Find hours with positive expectancy
profitable_hours = hours[hours['net']>0].index.tolist()
print(f"Profitable hours: {profitable_hours}")
sub = df[df['hour'].isin(profitable_hours)]
n = len(sub)
w = (sub['pnl']>0).sum()
net = sub['pnl'].sum()
pf_p = sub[sub['pnl']>0]['pnl'].sum()
pf_l = abs(sub[sub['pnl']<=0]['pnl'].sum())
pf = pf_p/pf_l if pf_l else 0
print(f"  Filtered to profitable hours only: {n} trades | WR {w/n*100:.1f}% | Net {net:+.2f} | PF {pf:.2f}")
print()
print("WARNING: The above is overfitting to in-sample hours. Out-of-sample")
print("performance will likely be much worse. Use only as exploratory analysis.")
