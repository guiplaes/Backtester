"""
Mean-Rev Averaging amb TFs CURTS (M5, M15, M30)
=================================================
Test per veure si l'edge persisteix en TFs baixos amb més soroll.
Costs realistes (M5/M15 acumulen molt cost per nombre alt trades).
"""
import pandas as pd
import numpy as np
import time

REAL_COST = 0.40

print("Loading...", flush=True)
m5 = pd.read_csv('xauusd_m5_5y.csv', index_col=0, parse_dates=True)
m5.index = pd.to_datetime(m5.index, utc=True)
m5.columns = [c.lower() for c in m5.columns]

def aggregate(df_, rule):
    return df_.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last')).dropna()

def precompute(df_, ma_periods):
    return {
        'O':df_['open'].values,'H':df_['high'].values,
        'L':df_['low'].values,'C':df_['close'].values,
        'SMAS':{p:df_['close'].rolling(p).mean().values for p in ma_periods},
        'STDS':{p:df_['close'].rolling(p).std().values for p in ma_periods},
        'TS':df_.index,'n':len(df_)
    }

def bt_avg(arrs, direction, ma, levels, stop_z, cost, max_bars=500):
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
                pnl-=tot_u*cost
                trades.append({'ts':arrs['TS'][pos['entries'][0][0]],
                               'pnl':pnl,'units':tot_u,
                               'reason':'STOP' if stop_hit else ('TARGET' if target else 'TIME'),
                               'duration':i-pos['entries'][0][0]})
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
    avg_u=np.mean([t['units'] for t in trades])
    avg_d=np.mean([t['duration'] for t in trades])
    return {'n':n,'wr':wins/n*100,'net':net,'pf':pf,'dd':dd,'avg_u':avg_u,'avg_d':avg_d}

def fmt(s):
    if s is None: return "0"
    return f"n={s['n']:>5} WR{s['wr']:>4.1f}% Net=${s['net']:>+7.0f} PF{s['pf']:>5.2f} DD${s['dd']:>5.0f} u={s['avg_u']:.1f} dur={s['avg_d']:.0f}"

def per_year(trades):
    if not trades: return {}
    df=pd.DataFrame(trades)
    df['year']=pd.to_datetime(df['ts']).dt.year
    out={}
    for yr,grp in df.groupby('year'):
        pnls=grp['pnl'].values
        n=len(pnls);wins=(pnls>0).sum()
        out[yr]={'n':n,'wr':wins/n*100,'net':pnls.sum()}
    return out

# Aggregate
MA_PERIODS = [50, 100, 150, 200, 300, 500, 800]
TFS = {
    'M5':  m5,
    'M15': aggregate(m5,'15min'),
    'M30': aggregate(m5,'30min'),
}
print("\nAggregating + indicators...", flush=True)
DATA = {}
for tf, df in TFS.items():
    DATA[tf] = precompute(df, MA_PERIODS)
    print(f"  {tf}: {len(df)} bars", flush=True)

LEVELS_GRID = [
    [-1.0],[-1.5],[-2.0],
    [-1.0,-2.0],[-1.5,-2.5],[-2.0,-3.0],
    [-1.0,-2.0,-3.0],[-1.5,-2.5,-3.5],
    [-0.5,-1.0,-1.5,-2.0],
]
STOPS = [-3.0,-4.0,-5.0]

print("\nRunning short-TF sweep...", flush=True)
ALL = []
t0 = time.time()
for tf in ['M5','M15','M30']:
    arrs = DATA[tf]
    print(f"\n--- {tf} ---", flush=True)
    for ma in MA_PERIODS:
        if ma >= arrs['n']/3: continue
        for levels in LEVELS_GRID:
            for stop in STOPS:
                if stop >= levels[-1]: continue
                for direction in ['long','short']:
                    trades = bt_avg(arrs, direction, ma, levels, stop, REAL_COST)
                    s = stats(trades)
                    if s and s['n']>=30 and s['pf']>=1.20 and s['net']>0:
                        ALL.append({'tf':tf,'ma':ma,'levels':levels,'stop':stop,
                                    'direction':direction,'stats':s,'trades':trades})
print(f"\nDone in {time.time()-t0:.0f}s. {len(ALL)} valid configs.")

ALL.sort(key=lambda x:-x['stats']['pf'])

print()
print("="*150)
print("TOP 30 SHORT-TF CONFIGS:")
print("="*150)
for r in ALL[:30]:
    s = r['stats']
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"  {r['tf']:<3} SMA{r['ma']:<3} {r['direction']:<5} levels={lvl:<24} stop={r['stop']} | {fmt(s)}")

print()
print("="*150)
print("ANY-PER-ANY top 10 robustesa:")
print("="*150)
for r in ALL[:10]:
    s = r['stats']
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"\n{r['tf']} SMA{r['ma']} {r['direction']} levels={lvl} stop={r['stop']} | TOTAL {fmt(s)}")
    yb = per_year(r['trades'])
    yrs_pos = sum(1 for y in yb.values() if y['net']>0)
    print(f"  Anys positius: {yrs_pos}/{len(yb)}")
    for yr in sorted(yb.keys()):
        y = yb[yr]
        sgn = "+" if y['net']>0 else "-"
        print(f"  {sgn} {yr}: n={y['n']:>4} WR{y['wr']:>5.1f}% Net=${y['net']:>+7.0f}")

print()
print("="*150)
print("MILLOR PER TF (focus en VOLUM ALT):")
print("="*150)
for tf in ['M5','M15','M30']:
    sub = [r for r in ALL if r['tf']==tf and r['stats']['n']>=100]
    if sub:
        sub.sort(key=lambda x:-x['stats']['pf'])
        r = sub[0]
        lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
        s = r['stats']
        print(f"  {tf}: SMA{r['ma']} {r['direction']} levels={lvl} stop={r['stop']} | {fmt(s)}")
    else:
        print(f"  {tf}: cap config amb n>=100 i PF>=1.20")

print("\nDONE")
