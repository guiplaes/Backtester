"""
Mean-Rev Averaging — TF CURTS sobre PRIORITY PAIRS (Dukascopy 5y M5)
====================================================================
Pairs: EURCHF, EURGBP, AUDNZD, USDCHF, EURJPY
TFs: M5, M15
Sweep complet de SMA periods grans (M5 needs many bars to smooth).
"""
import pandas as pd
import numpy as np
import time
import os

COSTS = {'EURCHF':0.10,'EURGBP':0.10,'AUDNZD':0.20,'USDCHF':0.10,'EURJPY':0.10,'CHFJPY':0.15}
PIP_MUL = {'EURCHF':1000,'EURGBP':1000,'AUDNZD':1000,'USDCHF':1000,'EURJPY':100,'CHFJPY':100}
PAIRS_FILES = {
    'EURCHF': 'eurchf_dk_m5_5y.csv', 'EURGBP': 'eurgbp_dk_m5_5y.csv',
    'AUDNZD': 'audnzd_dk_m5_5y.csv', 'USDCHF': 'usdchf_dk_m5_5y.csv',
    'EURJPY': 'eurjpy_dk_m5_5y.csv', 'CHFJPY': 'chfjpy_dk_m5_5y.csv',
}

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

def bt_avg(arrs, direction, ma, levels, stop_z, cost, pip_mul, max_bars=500):
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
                pnl_d = pnl * pip_mul - tot_u*cost
                trades.append({'ts':arrs['TS'][pos['entries'][0][0]],
                               'pnl':pnl_d,'units':tot_u,
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
    return {'n':n,'wr':wins/n*100,'net':net,'pf':pf,'dd':dd,'avg_u':avg_u}

def fmt(s):
    if s is None: return "0"
    return f"n={s['n']:>5} WR{s['wr']:>4.1f}% Net=${s['net']:>+7.0f} PF{s['pf']:>5.2f} DD${s['dd']:>5.0f} u={s['avg_u']:.1f}"

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

# Load + aggregate
TF_MA = {
    'M5':  [400, 800, 1600, 3200, 5000],   # M5: 400=33h, 800=67h, etc
    'M15': [200, 400, 800, 1600, 2400],
}

print("Loading + aggregating...", flush=True)
DATA = {}
for pair, csv_file in PAIRS_FILES.items():
    if not os.path.exists(csv_file) or os.path.getsize(csv_file) < 100000:
        print(f"  {pair}: no file"); continue
    df_m5 = pd.read_csv(csv_file, index_col=0, parse_dates=True)
    df_m5.index = pd.to_datetime(df_m5.index, utc=True)
    df_m5.columns = [c.lower() for c in df_m5.columns]
    DATA[pair] = {
        'M5':  precompute(df_m5, TF_MA['M5']),
        'M15': precompute(aggregate(df_m5, '15min'), TF_MA['M15']),
    }
    print(f"  {pair}: M5={DATA[pair]['M5']['n']} M15={DATA[pair]['M15']['n']}", flush=True)

LEVELS_GRID = [
    [-1.0,-2.0],[-1.5,-2.5],[-2.0,-3.0],
    [-1.0,-2.0,-3.0],[-1.5,-2.5,-3.5],
    [-0.5,-1.0,-1.5,-2.0],[-1.0,-1.5,-2.0,-2.5],
    [-0.5,-1.5,-2.5],
]
STOPS = [-3.0,-3.5,-4.0,-5.0]

print("\nRunning M5+M15 sweep...", flush=True)
ALL = []
t0 = time.time()
for pair in DATA.keys():
    cost = COSTS[pair]
    pip_mul = PIP_MUL[pair]
    print(f"\n--- {pair} ---", flush=True)
    for tf in ['M5','M15']:
        arrs = DATA[pair][tf]
        if arrs['n'] < 200: continue
        for ma in TF_MA[tf]:
            if ma >= arrs['n']/3: continue
            for levels in LEVELS_GRID:
                for stop in STOPS:
                    if stop >= levels[-1]: continue
                    for direction in ['long','short']:
                        trades = bt_avg(arrs, direction, ma, levels, stop, cost, pip_mul)
                        s = stats(trades)
                        if s and s['n']>=30 and s['pf']>=1.30 and s['net']>0:
                            ALL.append({'pair':pair,'tf':tf,'ma':ma,'levels':levels,
                                        'stop':stop,'direction':direction,
                                        'stats':s,'trades':trades})

print(f"\nDone in {time.time()-t0:.0f}s. {len(ALL)} valid configs.")

ALL.sort(key=lambda x:-x['stats']['pf'])

print()
print("="*150)
print("TOP 30 SHORT-TF CONFIGS:")
print("="*150)
for r in ALL[:30]:
    s = r['stats']
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"  {r['pair']:<7} {r['tf']:<3} SMA{r['ma']:<4} {r['direction']:<5} levels={lvl:<24} stop={r['stop']} | {fmt(s)}")

print()
print("="*150)
print("TOP CONFIGS amb VOLUM (n>=100):")
print("="*150)
high_vol = [r for r in ALL if r['stats']['n']>=100]
high_vol.sort(key=lambda x:-x['stats']['pf'])
for r in high_vol[:20]:
    s = r['stats']
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"  {r['pair']:<7} {r['tf']:<3} SMA{r['ma']:<4} {r['direction']:<5} levels={lvl:<24} stop={r['stop']} | {fmt(s)}")

print()
print("="*150)
print("ANY-PER-ANY top 10 robustesa:")
print("="*150)
for r in ALL[:10]:
    s = r['stats']
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"\n{r['pair']} {r['tf']} SMA{r['ma']} {r['direction']} levels={lvl} stop={r['stop']} | TOTAL {fmt(s)}")
    yb = per_year(r['trades'])
    yrs_pos = sum(1 for y in yb.values() if y['net']>0)
    print(f"  Anys positius: {yrs_pos}/{len(yb)}")
    for yr in sorted(yb.keys()):
        y = yb[yr]
        sgn = "+" if y['net']>0 else "-"
        print(f"  {sgn} {yr}: n={y['n']:>4} WR{y['wr']:>5.1f}% Net=${y['net']:>+7.0f}")

print("\nDONE")
