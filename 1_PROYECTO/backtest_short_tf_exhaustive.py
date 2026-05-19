"""
EXHAUSTIVE M5+M15 SWEEP — 7 pairs × moltes combinacions
========================================================
Cerca edge real en TFs curts. Per cada pair:
- MA periods amplies (100, 200, 400, 800, 1600, 2400, 3200, 4800)
- Levels combinations (8 variants)
- Stops (5)
- LONG i SHORT separadament

Costs realistes VT Markets. Filter: PF >= 1.30 + n >= 30.
"""
import pandas as pd
import numpy as np
import time

LOT = 0.05

PIP_MUL = {'EURGBP':1000,'EURCHF':1000,'EURJPY':100,'CHFJPY':100,
           'USDCHF':1000,'GBPCHF':1000,'AUDNZD':1000}
COSTS = {'EURGBP':0.30,'EURCHF':0.30,'EURJPY':0.30,'CHFJPY':0.40,
         'USDCHF':0.30,'GBPCHF':0.40,'AUDNZD':0.40}
PAIR_FILES = {
    'EURGBP':'eurgbp_dk_m5_5y.csv','EURCHF':'eurchf_dk_m5_5y.csv',
    'EURJPY':'eurjpy_dk_m5_5y.csv','CHFJPY':'chfjpy_dk_m5_5y.csv',
    'USDCHF':'usdchf_dk_m5_5y.csv','GBPCHF':'gbpchf_dk_m5_5y.csv',
    'AUDNZD':'audnzd_dk_m5_5y.csv',
}

def aggregate(df_, rule):
    return df_.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last')).dropna()

def precompute(df_, ma_p):
    return {
        'O':df_['open'].values,'H':df_['high'].values,
        'L':df_['low'].values,'C':df_['close'].values,
        'SMA':df_['close'].rolling(ma_p).mean().values,
        'STD':df_['close'].rolling(ma_p).std().values,
        'TS':df_.index, 'n':len(df_)
    }

def bt_avg(arrs, direction, levels, stop_z, cost, pip_mul):
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    SMA=arrs['SMA'];STD=arrs['STD']
    n=arrs['n']
    trades=[];pos=None
    for i in range(50,n):
        sma=SMA[i];std=STD[i];c=C[i]
        if np.isnan(sma) or np.isnan(std) or std<=0: continue
        z=(c-sma)/std
        if pos is not None:
            if direction=='long': stop_hit=z<=stop_z;target=c>=sma
            else: stop_hit=z>=-stop_z;target=c<=sma
            if stop_hit or target or (i-pos['entries'][0][0])>=500:
                tot_u=len(pos['entries']);pnl=0;sgn=1 if direction=='long' else -1
                for eidx,ep in pos['entries']: pnl+=(c-ep)*sgn
                pnl_d = pnl*pip_mul*(LOT/0.01) - tot_u*cost*(LOT/0.01)
                trades.append({'pnl':pnl_d, 'ts':arrs['TS'][i]})
                pos=None;continue
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
            if direction=='long' and z<=f: pos={'entries':[(i,c)],'hit':{f}}
            elif direction=='short' and z>=-f: pos={'entries':[(i,c)],'hit':{f}}
    return trades

def stats(trades):
    if not trades: return None
    pnls=np.array([t['pnl'] for t in trades])
    n=len(pnls);wins=(pnls>0).sum();net=pnls.sum()
    pp=pnls[pnls>0].sum();pl=abs(pnls[pnls<=0].sum())
    pf=pp/pl if pl else 0
    return {'n':n,'wr':wins/n*100,'net':net,'pf':pf}

def per_year_check(trades):
    if not trades: return 0
    df_t = pd.DataFrame(trades)
    df_t['year'] = pd.to_datetime(df_t['ts']).dt.year
    yrs_pos = sum(1 for y, grp in df_t.groupby('year') if grp['pnl'].sum() > 0)
    return yrs_pos

# Parameter space — focused
TF_MA = {
    'M5':  [400, 800, 1600, 3200, 4800],
    'M15': [100, 200, 400, 800, 1600, 2400, 3200],
}
LEVELS_GRID = [
    [-0.5,-1.0,-1.5,-2.0],[-1.0,-1.5,-2.0,-2.5],
    [-1.5,-2.0,-2.5,-3.0],[-1.0,-2.0,-3.0],
    [-1.5,-2.5,-3.5],[-2.0,-3.0,-4.0],
    [-0.5,-1.5,-2.5,-3.0],
]
STOPS = [-3.0,-3.5,-4.0,-5.0]

print("Running M5+M15 exhaustive sweep on 7 pairs...", flush=True)
t0 = time.time()
ALL = []

for pair in PAIR_FILES.keys():
    print(f"\n{'#'*60}", flush=True)
    print(f"# {pair}", flush=True)
    print(f"{'#'*60}", flush=True)
    df = pd.read_csv(PAIR_FILES[pair], index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]
    cost = COSTS[pair]; pip_mul = PIP_MUL[pair]

    for tf, rule in [('M5','5min'),('M15','15min')]:
        df_tf = df if tf == 'M5' else aggregate(df, rule)
        print(f"\n{pair} {tf} ({len(df_tf)} bars)...", flush=True)
        for ma in TF_MA[tf]:
            if ma >= len(df_tf)/3: continue
            arrs = precompute(df_tf, ma)
            for levels in LEVELS_GRID:
                for stop in STOPS:
                    if stop >= levels[-1]: continue
                    for direction in ['long','short']:
                        trades = bt_avg(arrs, direction, levels, stop, cost, pip_mul)
                        s = stats(trades)
                        if s and s['n']>=30 and s['pf']>=1.30 and s['net']>0:
                            yp = per_year_check(trades)
                            ALL.append({'pair':pair,'tf':tf,'ma':ma,'levels':levels,
                                        'stop':stop,'direction':direction,
                                        'n':s['n'],'wr':s['wr'],'net':s['net'],
                                        'pf':s['pf'],'years_pos':yp})

print(f"\nDone in {time.time()-t0:.0f}s. {len(ALL)} configs valid.", flush=True)

# Top per pair+TF
print()
print("="*135)
print("MILLOR PER PAIR + TF (per Net positiu, n>=30, PF>=1.30):")
print("="*135)

ALL.sort(key=lambda x: -x['net'])

# Best per pair+tf+direction
best_combos = {}
for r in ALL:
    key = (r['pair'], r['tf'], r['direction'])
    if key not in best_combos:
        best_combos[key] = r

for pair in PAIR_FILES.keys():
    for tf in ['M5','M15']:
        for direction in ['long','short']:
            key = (pair, tf, direction)
            if key in best_combos:
                r = best_combos[key]
                lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
                print(f"  {pair:<7} {tf:<3} {direction:<5} SMA{r['ma']:<5} levels={lvl:<22} stop={r['stop']} | n={r['n']:>4} WR{r['wr']:.1f}% Net=${r['net']:>+7,.0f} PF{r['pf']:>5.2f} y+={r['years_pos']}/6")

# Top 30 global by Net
print()
print("="*135)
print("TOP 30 GLOBAL (per Net):")
print("="*135)
for r in ALL[:30]:
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"  {r['pair']:<7} {r['tf']:<3} {r['direction']:<5} SMA{r['ma']:<5} levels={lvl:<22} stop={r['stop']} | n={r['n']:>4} WR{r['wr']:.1f}% Net=${r['net']:>+7,.0f} PF{r['pf']:>5.2f} y+={r['years_pos']}/6")

# Top per PF
print()
print("="*135)
print("TOP 20 GLOBAL (per PF, amb n>=50):")
print("="*135)
high_pf = sorted([r for r in ALL if r['n']>=50], key=lambda x: -x['pf'])
for r in high_pf[:20]:
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"  {r['pair']:<7} {r['tf']:<3} {r['direction']:<5} SMA{r['ma']:<5} levels={lvl:<22} stop={r['stop']} | n={r['n']:>4} WR{r['wr']:.1f}% Net=${r['net']:>+7,.0f} PF{r['pf']:>5.2f} y+={r['years_pos']}/6")

# Robust (5/6 or 6/6 anys positius)
print()
print("="*135)
print("MES ROBUSTS (5/6 o 6/6 anys positius, n>=50):")
print("="*135)
robust = [r for r in ALL if r['years_pos']>=5 and r['n']>=50]
robust.sort(key=lambda x: -x['net'])
for r in robust[:20]:
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"  {r['pair']:<7} {r['tf']:<3} {r['direction']:<5} SMA{r['ma']:<5} levels={lvl:<22} stop={r['stop']} | n={r['n']:>4} WR{r['wr']:.1f}% Net=${r['net']:>+7,.0f} PF{r['pf']:>5.2f} y+={r['years_pos']}/6")

print("\nDONE")
