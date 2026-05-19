"""
Apply learned filters from segmented analysis:
- LONG only (no shorts)
- Skip Wednesday
- Optional: Asia session preferential
- Optional: Q3 ATR regime (medium-high vol, not extreme)
Test on full 17m sample.
"""
import pandas as pd
import numpy as np

df = pd.read_csv("backtest_trades_1y.csv", parse_dates=['entry_time', 'exit_time'])
df['hour'] = pd.to_datetime(df['entry_time']).dt.hour
df['dow'] = pd.to_datetime(df['entry_time']).dt.day_name()

print(f"Total trades: {len(df)} | Net: ${df['pnl'].sum():.2f} | PF: ", end='')
pf_p = df[df['pnl']>0]['pnl'].sum()
pf_l = abs(df[df['pnl']<=0]['pnl'].sum())
print(f"{pf_p/pf_l:.2f}")
print()

filters = [
    ("Baseline (all trades)", df),
    ("LONG only", df[df['side']=='L']),
    ("LONG + skip Wednesday", df[(df['side']=='L') & (df['dow']!='Wednesday')]),
    ("LONG + skip Wed + skip Friday", df[(df['side']=='L') & (~df['dow'].isin(['Wednesday','Friday']))]),
    ("LONG + Monday only", df[(df['side']=='L') & (df['dow']=='Monday')]),
    ("LONG + Asia session (00-06 UTC)", df[(df['side']=='L') & (df['hour'].between(0,6))]),
    ("Both LONG/SHORT + Asia only", df[df['hour'].between(0,6)]),
    ("LONG + skip 'bad' hours (06-08, 16-21)", df[(df['side']=='L') & (~df['hour'].isin([6,7,8,16,17,18,19,20,21]))]),
    ("LONG + skip Wednesday + Q3 ATR", df[(df['side']=='L') & (df['dow']!='Wednesday') & (df['atr'].between(2.97,4.52))]),
    ("LONG + Mon/Tue/Thu (skip Wed/Fri)", df[(df['side']=='L') & (df['dow'].isin(['Monday','Tuesday','Thursday']))]),
]

print(f"{'Filter':<55} | {'Trades':>6} | {'WR':>5} | {'Net':>9} | {'Avg':>6} | {'PF':>5} | {'DD':>7}")
print("-"*120)

for name, sub in filters:
    if len(sub) == 0:
        continue
    n = len(sub)
    w = (sub['pnl']>0).sum()
    net = sub['pnl'].sum()
    avg = sub['pnl'].mean()
    pf_p = sub[sub['pnl']>0]['pnl'].sum()
    pf_l = abs(sub[sub['pnl']<=0]['pnl'].sum())
    pf = pf_p/pf_l if pf_l else 0
    eq = sub['pnl'].cumsum().values
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq).max()
    marker = " <-- OK" if pf > 1.3 and net > 100 else ""
    print(f"{name:<55} | {n:>6} | {w/n*100:>4.1f}% | ${net:>+8.2f} | ${avg:>+5.2f} | {pf:>5.2f} | ${dd:>5.2f}{marker}")

print()
print("="*100)
print("WALK-FORWARD VALIDATION (split 17m in half, see if filters hold up):")
print("="*100)

mid = pd.to_datetime(df['entry_time']).quantile(0.5)
in_sample = df[pd.to_datetime(df['entry_time']) < mid]
out_sample = df[pd.to_datetime(df['entry_time']) >= mid]
print(f"In-sample: {len(in_sample)} trades through {mid}")
print(f"Out-of-sample: {len(out_sample)} trades after {mid}")
print()

# Test "LONG + skip Wed" filter on each half
test_filter = lambda d: d[(d['side']=='L') & (d['dow']!='Wednesday')]

is_filt = test_filter(in_sample)
oos_filt = test_filter(out_sample)
print(f"In-sample with filter:  {len(is_filt)} trades | Net ${is_filt['pnl'].sum():+.2f}")
print(f"Out-of-sample with filter: {len(oos_filt)} trades | Net ${oos_filt['pnl'].sum():+.2f}")
if len(is_filt)>0:
    pf_p = is_filt[is_filt['pnl']>0]['pnl'].sum()
    pf_l = abs(is_filt[is_filt['pnl']<=0]['pnl'].sum())
    print(f"  IS PF: {pf_p/pf_l if pf_l else 0:.2f}")
if len(oos_filt)>0:
    pf_p = oos_filt[oos_filt['pnl']>0]['pnl'].sum()
    pf_l = abs(oos_filt[oos_filt['pnl']<=0]['pnl'].sum())
    print(f"  OOS PF: {pf_p/pf_l if pf_l else 0:.2f}")
