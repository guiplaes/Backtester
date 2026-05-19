"""Test filtre mecànic basat en patrons trobats."""
import pandas as pd
import numpy as np

df = pd.read_csv("m5_trades_with_context.csv")
print(f"Total trades: {len(df)}")
print(f"Net total: ${df['pnl'].sum():.0f}")
print(f"Loss rate: {(df['outcome']=='LOSS').sum()/len(df)*100:.1f}%")
print()

def stats(sub, name):
    if len(sub)==0: print(f"{name}: 0"); return
    n = len(sub)
    losses = (sub['outcome']=='LOSS').sum()
    net = sub['pnl'].sum()
    pf_p = sub[sub['pnl']>0]['pnl'].sum()
    pf_l = abs(sub[sub['pnl']<=0]['pnl'].sum())
    pf = pf_p/pf_l if pf_l else 0
    eq = sub['pnl'].cumsum().values
    peak = np.maximum.accumulate(eq); dd = (peak - eq).max() if len(eq) else 0
    print(f"{name:>50}: n={n:>4} | Loss rate {losses/n*100:.1f}% | Net=${net:+.0f} | PF {pf:.2f} | DD ${dd:.0f}", flush=True)

print("="*120)
print("FILTRE MECÀNIC — TESTANT diferents combinacions:")
print("="*120)

stats(df, "Baseline (cap filtre)")

# F1: Skip hour 16
stats(df[df['hour']!=16], "F1 Skip hour 16 (NY open)")

# F2: Skip volume spike >2
stats(df[df['vol_ratio']<=2.0], "F2 Skip vol_ratio>2.0")

# F3: Skip lateral slope
stats(df[df['ema50_slope'].abs()>=0.05], "F3 Skip |slope|<0.05")

# F4: Skip extreme ATR
stats(df[df['atr_pct']<=0.8], "F4 Skip atr_pct>0.8")

# Combinations
print()
print("Combinacions:")
stats(df[(df['hour']!=16) & (df['vol_ratio']<=2.0)], "F1+F2 (skip hour16 + vol spike)")
stats(df[(df['hour']!=16) & (df['vol_ratio']<=2.0) & (df['ema50_slope'].abs()>=0.05)], "F1+F2+F3")
stats(df[(df['hour']!=16) & (df['vol_ratio']<=2.0) & (df['atr_pct']<=0.8)], "F1+F2+F4")
stats(df[(df['hour']!=16) & (df['vol_ratio']<=2.0) & (df['ema50_slope'].abs()>=0.05) & (df['atr_pct']<=0.8)], "F1+F2+F3+F4 (TOTS)")

# Only good hours
print()
print("Top hours filter:")
top_hours = [10, 11, 5, 6, 8, 18, 19, 20]
stats(df[df['hour'].isin(top_hours)], "Only top 8 hours (loss <45%)")

# Smart filter
print()
print("Filtre intel·ligent:")
mask_smart = (
    (df['hour'] != 16) &  # no NY open
    (df['vol_ratio'].between(0.5, 2.0)) &  # vol normal
    (df['atr_pct'].between(0.1, 0.85))  # vol regim normal
)
stats(df[mask_smart], "SMART: !16h + vol 0.5-2.0 + atr 0.1-0.85")

# Direction-specific
print()
print("Per direcció + smart filter:")
df_long = df[df['side']=='long']
df_short = df[df['side']=='short']
mask_long = (df_long['hour']!=16) & df_long['vol_ratio'].between(0.5, 2.0) & df_long['atr_pct'].between(0.1, 0.85)
mask_short = (df_short['hour']!=16) & df_short['vol_ratio'].between(0.5, 2.0) & df_short['atr_pct'].between(0.1, 0.85)
stats(df_long, "LONGS baseline")
stats(df_long[mask_long], "LONGS + smart filter")
stats(df_short, "SHORTS baseline")
stats(df_short[mask_short], "SHORTS + smart filter")

print()
print("="*120)
print("RESUM final amb millor filtre:")
print("="*120)

best = df[mask_smart]
n_total = len(df); n_kept = len(best)
print(f"Trades retinguts: {n_kept}/{n_total} = {n_kept/n_total*100:.1f}%")
print(f"Net Filtrat: ${best['pnl'].sum():.0f} | vs Baseline ${df['pnl'].sum():.0f}")

# Net at 0.05 lot
net_005 = best['pnl'].sum() * 5
pct_year = net_005/50000*100
print(f"Escalat 0.05 lot: ${net_005:.0f} ({pct_year:+.1f}%/any)")

# Compare to original unified
print()
print(f"Sistema UNIFIED original: $36,387 (+72.8%/any) PF 1.68")
pf_p = best[best['pnl']>0]['pnl'].sum()
pf_l = abs(best[best['pnl']<=0]['pnl'].sum())
pf = pf_p/pf_l if pf_l else 0
print(f"Sistema FILTRAT:          ${net_005:.0f} ({pct_year:+.1f}%/any) PF {pf:.2f}")
