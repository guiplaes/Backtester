"""Verifica què hauria de fer la estrategia en els últims 37 dies (com TV)."""
import pandas as pd
import numpy as np

df = pd.read_csv("m5_trades_with_context.csv")
df['ts'] = pd.to_datetime(df['ts'])

print(f"Total trades: {len(df)}")
print(f"Periode total: {df['ts'].min()} -> {df['ts'].max()}")
print()

# Recent 37 days (TV's window)
last_date = df['ts'].max()
cutoff = last_date - pd.Timedelta(days=37)
recent = df[df['ts'] >= cutoff]

def stats(sub, name):
    n = len(sub)
    if n == 0:
        print(f"{name}: 0 trades"); return
    losses = (sub['outcome']=='LOSS').sum()
    net = sub['pnl'].sum()
    pf_p = sub[sub['pnl']>0]['pnl'].sum()
    pf_l = abs(sub[sub['pnl']<=0]['pnl'].sum())
    pf = pf_p/pf_l if pf_l else 0
    eq = sub['pnl'].cumsum().values
    peak = np.maximum.accumulate(eq) if len(eq) else np.array([])
    dd = (peak - eq).max() if len(eq) else 0
    print(f"{name:>50}: n={n:>4} | Loss {losses/n*100:.1f}% | Net=${net:+.2f} | PF {pf:.2f} | DD ${dd:.2f}", flush=True)

print("PERIODES (cumulant des de més recent):")
for days in [7, 14, 30, 37, 60, 90, 180, 365, 730, 1825]:
    cutoff = last_date - pd.Timedelta(days=days)
    sub = df[df['ts'] >= cutoff]
    stats(sub, f"Last {days} days")

print()
print("="*120)
print("ÚLTIMS 37 DIES per any (per veure variació):")
print("="*120)
for year in [2021, 2022, 2023, 2024, 2025, 2026]:
    yr_data = df[pd.to_datetime(df['ts']).dt.year == year]
    if len(yr_data) == 0: continue
    last_yr = yr_data['ts'].max()
    cutoff = last_yr - pd.Timedelta(days=37)
    sub = yr_data[yr_data['ts'] >= cutoff]
    stats(sub, f"Last 37d of {year}")

print()
print("="*120)
print("TROSSOS DE 37 DIES al llarg del backtest (random sample):")
print("="*120)
all_dates = pd.to_datetime(df['ts']).sort_values().unique()
for sample_idx in [0, len(all_dates)//5, len(all_dates)*2//5, len(all_dates)*3//5, len(all_dates)*4//5, len(all_dates)-1]:
    if sample_idx >= len(all_dates): continue
    start = all_dates[sample_idx]
    end = pd.Timestamp(start) + pd.Timedelta(days=37)
    sub = df[(df['ts'] >= start) & (df['ts'] < end)]
    if len(sub) > 10:
        stats(sub, f"From {pd.Timestamp(start).strftime('%Y-%m-%d')}")

print()
print("="*120)
print("CONCLUSIÓ:")
print("="*120)
n_periods_pos = 0
n_periods_neg = 0
for i in range(0, len(all_dates), 100):  # mostres cada ~50 dies
    if i + 50 >= len(all_dates): break
    start = all_dates[i]
    end = pd.Timestamp(start) + pd.Timedelta(days=37)
    sub = df[(df['ts'] >= start) & (df['ts'] < end)]
    if len(sub) > 10:
        if sub['pnl'].sum() > 0:
            n_periods_pos += 1
        else:
            n_periods_neg += 1
print(f"Trossos de 37 dies positius: {n_periods_pos}")
print(f"Trossos de 37 dies negatius: {n_periods_neg}")
print(f"Win ratio per period 37d: {n_periods_pos/(n_periods_pos+n_periods_neg)*100:.1f}%")
