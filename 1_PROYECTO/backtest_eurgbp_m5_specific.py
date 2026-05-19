"""
EURGBP M5 — sweep complet LONG i SHORT amb threshold baix per veure tota la veritat.
"""
import pandas as pd
import numpy as np

REAL_COST = 0.30  # VT Markets EURGBP
PIP_MUL = 1000

print("Loading EURGBP M5...", flush=True)
df = pd.read_csv('eurgbp_dk_m5_5y.csv', index_col=0, parse_dates=True)
df.index = pd.to_datetime(df.index, utc=True)
df.columns = [c.lower() for c in df.columns]
print(f"Bars: {len(df)} de {df.index[0]} a {df.index[-1]}")

# Yearly returns
print("\nRendiments anuals:")
for yr in sorted(df.index.year.unique()):
    sub = df[df.index.year==yr]
    if len(sub)<10: continue
    pct = (sub['close'].iloc[-1] - sub['close'].iloc[0]) / sub['close'].iloc[0] * 100
    print(f"  {yr}: {pct:+.1f}%")

MA_PERIODS = [800, 1600, 2400, 3200, 4800, 7000]
LEVELS_GRID = [
    [-1.0,-2.0],[-1.5,-2.5],[-2.0,-3.0],
    [-1.0,-2.0,-3.0],[-1.5,-2.5,-3.5],[-2.0,-3.0,-4.0],
    [-1.0,-1.5,-2.0,-2.5],[-1.5,-2.0,-2.5,-3.0],
    [-0.5,-1.0,-1.5,-2.0],[-0.5,-1.5,-2.5],
]
STOPS = [-3.0,-3.5,-4.0,-5.0,-6.0]

# Precompute
arrs = {
    'O':df['open'].values,'H':df['high'].values,
    'L':df['low'].values,'C':df['close'].values,
    'SMAS':{p:df['close'].rolling(p).mean().values for p in MA_PERIODS},
    'STDS':{p:df['close'].rolling(p).std().values for p in MA_PERIODS},
    'TS':df.index,'n':len(df)
}

def bt_avg(direction, ma, levels, stop_z, max_bars=500):
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    SMA=arrs['SMAS'][ma];STD=arrs['STDS'][ma]
    n=arrs['n']
    trades=[];pos=None
    for i in range(ma+10,n):
        sma=SMA[i];std=STD[i];c=C[i]
        if np.isnan(sma) or np.isnan(std) or std<=0: continue
        z=(c-sma)/std
        if pos is not None:
            if direction=='long':
                stop_hit=z<=stop_z;target=c>=sma
            else:
                stop_hit=z>=-stop_z;target=c<=sma
            time_out=(i-pos['entries'][0][0])>=max_bars
            if stop_hit or target or time_out:
                exit_p=c
                tot_u=len(pos['entries'])
                pnl=0;sgn=1 if direction=='long' else -1
                for eidx,ep in pos['entries']: pnl+=(exit_p-ep)*sgn
                pnl_d = pnl * PIP_MUL - tot_u*REAL_COST
                trades.append({'ts':arrs['TS'][pos['entries'][0][0]],'pnl':pnl_d,'units':tot_u})
                pos=None
                continue
            if direction=='long':
                for lvl in levels:
                    if lvl not in pos['hit'] and z<=lvl:
                        pos['entries'].append((i,c));pos['hit'].add(lvl);break
            else:
                for lvl in levels:
                    if lvl not in pos['hit'] and z>=-lvl:
                        pos['entries'].append((i,c));pos['hit'].add(lvl);break
        if pos is None:
            f=levels[0]
            if direction=='long' and z<=f:
                pos={'entries':[(i,c)],'hit':{f}}
            elif direction=='short' and z>=-f:
                pos={'entries':[(i,c)],'hit':{f}}
    return trades

def stats(trades):
    if not trades: return None
    pnls=np.array([t['pnl'] for t in trades])
    n=len(pnls);wins=(pnls>0).sum();net=pnls.sum()
    pp=pnls[pnls>0].sum();pl=abs(pnls[pnls<=0].sum())
    pf=pp/pl if pl else 0
    eq=np.cumsum(pnls);peak=np.maximum.accumulate(eq);dd=(peak-eq).max()
    return {'n':n,'wr':wins/n*100,'net':net,'pf':pf,'dd':dd}

def per_year(trades):
    if not trades: return {}
    df_=pd.DataFrame(trades);df_['year']=pd.to_datetime(df_['ts']).dt.year
    out={}
    for yr,grp in df_.groupby('year'):
        pnls=grp['pnl'].values
        out[yr]={'n':len(pnls),'wr':(pnls>0).sum()/len(pnls)*100,'net':pnls.sum()}
    return out

print("\nSweep complet LONG i SHORT...", flush=True)
ALL = []
for ma in MA_PERIODS:
    for levels in LEVELS_GRID:
        for stop in STOPS:
            if stop >= levels[-1]: continue
            for direction in ['long','short']:
                trades = bt_avg(direction, ma, levels, stop)
                s = stats(trades)
                if s and s['n']>=15:
                    ALL.append({'direction':direction,'ma':ma,'levels':levels,
                                'stop':stop,'stats':s,'trades':trades})

print(f"\n{len(ALL)} configs totals.")

# Best LONG
print()
print("="*120)
print("TOP 10 LONG (millor PF):")
print("="*120)
longs = sorted([r for r in ALL if r['direction']=='long' and r['stats']['net']>0], key=lambda x:-x['stats']['pf'])
for r in longs[:10]:
    s = r['stats']
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"  SMA{r['ma']:<5} L levels={lvl:<24} stop={r['stop']} | n={s['n']:>4} WR{s['wr']:.1f}% Net=${s['net']:+.0f} PF{s['pf']:.2f} DD${s['dd']:.0f}")

# Best SHORT
print()
print("="*120)
print("TOP 10 SHORT (millor PF):")
print("="*120)
shorts = sorted([r for r in ALL if r['direction']=='short' and r['stats']['net']>0], key=lambda x:-x['stats']['pf'])
for r in shorts[:10]:
    s = r['stats']
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"  SMA{r['ma']:<5} S levels={lvl:<24} stop={r['stop']} | n={s['n']:>4} WR{s['wr']:.1f}% Net=${s['net']:+.0f} PF{s['pf']:.2f} DD${s['dd']:.0f}")

# Tots positius
print()
print("="*120)
print(f"TOTS configs amb Net positiu (LONG): {sum(1 for r in ALL if r['direction']=='long' and r['stats']['net']>0)}")
print(f"TOTS configs amb Net positiu (SHORT): {sum(1 for r in ALL if r['direction']=='short' and r['stats']['net']>0)}")
print(f"TOTS configs perdedors LONG: {sum(1 for r in ALL if r['direction']=='long' and r['stats']['net']<=0)}")
print(f"TOTS configs perdedors SHORT: {sum(1 for r in ALL if r['direction']=='short' and r['stats']['net']<=0)}")

# Anys per top 5 LONG
print()
print("="*120)
print("ANY-PER-ANY top 5 LONG:")
print("="*120)
for r in longs[:5]:
    s = r['stats']
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"\nSMA{r['ma']} LONG levels={lvl} stop={r['stop']} | n={s['n']} PF{s['pf']:.2f} Net=${s['net']:+.0f}")
    yb = per_year(r['trades'])
    yrs_pos = sum(1 for y in yb.values() if y['net']>0)
    print(f"  Anys positius: {yrs_pos}/{len(yb)}")
    for yr in sorted(yb.keys()):
        y = yb[yr]
        print(f"  {'+' if y['net']>0 else '-'} {yr}: n={y['n']:>3} WR{y['wr']:>5.1f}% Net=${y['net']:>+7.0f}")

# Anys per top 5 SHORT
print()
print("ANY-PER-ANY top 5 SHORT:")
for r in shorts[:5]:
    s = r['stats']
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"\nSMA{r['ma']} SHORT levels={lvl} stop={r['stop']} | n={s['n']} PF{s['pf']:.2f} Net=${s['net']:+.0f}")
    yb = per_year(r['trades'])
    yrs_pos = sum(1 for y in yb.values() if y['net']>0)
    print(f"  Anys positius: {yrs_pos}/{len(yb)}")
    for yr in sorted(yb.keys()):
        y = yb[yr]
        print(f"  {'+' if y['net']>0 else '-'} {yr}: n={y['n']:>3} WR{y['wr']:>5.1f}% Net=${y['net']:>+7.0f}")

print("\nDONE")
