"""
EXTREME PARAMETER SWEEP — Mean-Reversion Averaging
====================================================
Sweep ampli per trobar configs amb VOLUM ALT mantenint PF >1.5
- SMA periods: 30, 50, 75, 100, 150, 200, 250, 300, 400, 500
- Levels: many combinations
- Stop: -3 to -7
- Test sobre H1, H4, D1 — més probables per mean-reversion
"""
import pandas as pd
import numpy as np
import time

REAL_COST_BY_ASSET = {'XAUUSD': 0.40, 'EURUSD': 0.07}

print("Loading...", flush=True)
RAW = {
    'XAUUSD': pd.read_csv('xauusd_m5_5y.csv', index_col=0, parse_dates=True),
}
for asset, df in RAW.items():
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]

def aggregate(df_, rule):
    return df_.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last')).dropna()

def precompute(df_, ma_periods):
    smas = {p: df_['close'].rolling(p).mean().values for p in ma_periods}
    stds = {p: df_['close'].rolling(p).std().values for p in ma_periods}
    return {
        'O':df_['open'].values,'H':df_['high'].values,
        'L':df_['low'].values,'C':df_['close'].values,
        'SMAS':smas,'STDS':stds,
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
                stop_hit = z <= stop_z
                target = c >= sma
            else:
                stop_hit = z >= -stop_z
                target = c <= sma
            time_out = (i-pos['entries'][0][0]) >= max_bars
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
    avg_u = np.mean([t['units'] for t in trades])
    return {'n':n,'wr':wins/n*100,'net':net,'pf':pf,'dd':dd,'avg_u':avg_u}

def fmt(s):
    if s is None: return "0"
    return f"n={s['n']:>4} WR{s['wr']:>4.1f}% Net=${s['net']:>+7.0f} PF{s['pf']:>5.2f} DD${s['dd']:>4.0f} u={s['avg_u']:.1f}"

def per_year(trades):
    if not trades: return {}
    df = pd.DataFrame(trades)
    df['year'] = pd.to_datetime(df['ts']).dt.year
    out={}
    for yr,grp in df.groupby('year'):
        pnls=grp['pnl'].values
        n=len(pnls);wins=(pnls>0).sum()
        out[yr]={'n':n,'wr':wins/n*100,'net':pnls.sum()}
    return out

MA_PERIODS = [30, 50, 75, 100, 150, 200, 250, 300, 400, 500]
LEVELS_GRID = [
    [-1.0],[-1.5],[-2.0],
    [-1.0,-2.0],[-1.5,-2.5],[-2.0,-3.0],
    [-1.0,-2.0,-3.0],[-1.5,-2.5,-3.5],[-2.0,-3.0,-4.0],
    [-0.5,-1.5,-2.5],[-0.5,-1.0,-1.5,-2.0],
]
STOPS = [-3.0, -3.5, -4.0, -5.0, -6.0, -7.0]

print("Aggregating...", flush=True)
DATA = {}
for tf, rule in [('H1','1h'),('H4','4h'),('D1','1D')]:
    df = aggregate(RAW['XAUUSD'], rule)
    DATA[tf] = precompute(df, MA_PERIODS)
    print(f"  {tf}: {len(df)} bars", flush=True)

print("\nRunning extreme sweep...", flush=True)
ALL = []
t0 = time.time()
for tf in ['H1','H4','D1']:
    arrs = DATA[tf]
    print(f"\n--- {tf} ---", flush=True)
    for ma in MA_PERIODS:
        if ma >= arrs['n']/3: continue
        for levels in LEVELS_GRID:
            for stop in STOPS:
                if stop >= levels[-1]: continue
                for direction in ['long','short']:
                    trades = bt_avg(arrs, direction, ma, levels, stop, 0.40)
                    s = stats(trades)
                    if s and s['n']>=20 and s['pf']>=1.30 and s['net']>0:
                        ALL.append({'tf':tf,'ma':ma,'levels':levels,'stop':stop,
                                    'direction':direction,'stats':s,'trades':trades})

elapsed = time.time()-t0
print(f"\nDone. {len(ALL)} valid configs in {elapsed:.0f}s")

ALL.sort(key=lambda x:-x['stats']['pf'])

print()
print("="*150)
print("TOP 30 EXTREME CONFIGS (PF>=1.30, n>=20):")
print("="*150)
for r in ALL[:30]:
    s = r['stats']
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"  {r['tf']:<3} SMA{r['ma']:<3} {r['direction']:<5} levels={lvl:<24} stop={r['stop']} | {fmt(s)}")

print()
print("="*150)
print("TOP 10 amb VOLUM (n>=50):")
print("="*150)
hi_vol = [r for r in ALL if r['stats']['n']>=50]
hi_vol.sort(key=lambda x:-x['stats']['pf'])
for r in hi_vol[:10]:
    s = r['stats']
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"  {r['tf']:<3} SMA{r['ma']:<3} {r['direction']:<5} levels={lvl:<24} stop={r['stop']} | {fmt(s)}")

print()
print("="*150)
print("ANY-PER-ANY top 10 (amb volum):")
print("="*150)
for r in hi_vol[:10]:
    s = r['stats']
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"\n{r['tf']} SMA{r['ma']} {r['direction']} levels={lvl} stop={r['stop']} | TOTAL {fmt(s)}")
    yb = per_year(r['trades'])
    yrs_pos = sum(1 for y in yb.values() if y['net']>0)
    print(f"  Anys positius: {yrs_pos}/{len(yb)}")
    for yr in sorted(yb.keys()):
        y = yb[yr]
        sgn = "+" if y['net']>0 else "-"
        print(f"  {sgn} {yr}: n={y['n']:>3} WR{y['wr']:>5.1f}% Net=${y['net']:>+7.0f}")

print("\nDONE")
