"""
OPTIMITZACIO PROFUNDA — NOMES PAIRS ESTABLES
=============================================
"""
import sys
sys.path.insert(0, '.')
from tg_send import send as tg_send
tg_send("🔬 <b>FASE 6 INICIADA</b>%0AOptimització profunda 8 pairs estables...")
"""
Pairs: EURGBP, EURCHF, GBPCHF, AUDNZD, USDCHF, NZDCAD, USDCAD, AUDCAD
Tots BOTH directions (real mean-rev).

Per cada pair × TF, sweep complet:
- MA period (varia segons TF)
- Levels (8 combinacions)
- Stops (5 nivells)
- Direccions (LONG i SHORT)

Objectiu: trobar la millor config robust per cada combinació.
Filter: PF >=1.30, n>=20, anys positius >=4/X.
"""
import pandas as pd
import numpy as np
import time

LOT = 0.05

PAIRS = {
    'EURGBP':{'cost':0.30,'pip':1000},
    'EURCHF':{'cost':0.30,'pip':1000},
    'GBPCHF':{'cost':0.40,'pip':1000},
    'AUDNZD':{'cost':0.40,'pip':1000},
    'USDCHF':{'cost':0.30,'pip':1000},
    'NZDCAD':{'cost':0.40,'pip':1000},
    'USDCAD':{'cost':0.30,'pip':1000},
    'AUDCAD':{'cost':0.40,'pip':1000},
}
PAIR_FILES = {p: f"{p.lower()}_dk_m5_5y.csv" for p in PAIRS}

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
                trades.append({'pnl':pnl_d,'ts':arrs['TS'][i]})
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

def per_year_count(trades):
    if not trades: return 0,0
    df_t = pd.DataFrame(trades)
    df_t['year'] = pd.to_datetime(df_t['ts']).dt.year
    yp = sum(1 for y, g in df_t.groupby('year') if g['pnl'].sum()>0)
    yt = df_t['year'].nunique()
    return yp, yt

# Per cada TF: rang adequat de MAs i levels
TF_PARAMS = {
    'D1': {'rule':'1D','mas':[30,50,75,100,150,200,300]},
    'H4': {'rule':'4h','mas':[50,100,150,200,300,500,800]},
    'H1': {'rule':'1h','mas':[100,200,500,800,1200,1600]},
    'M30': {'rule':'30min','mas':[200,400,800,1200,1600,2400]},
    'M15': {'rule':'15min','mas':[400,800,1600,2400,3200]},
}
LEVELS_GRID = [
    [-0.5,-1.0,-1.5,-2.0],[-1.0,-1.5,-2.0,-2.5],
    [-1.5,-2.0,-2.5,-3.0],[-2.0,-2.5,-3.0,-3.5],
    [-1.0,-2.0,-3.0],[-1.5,-2.5,-3.5],[-2.0,-3.0,-4.0],
    [-0.5,-1.5,-2.5,-3.0],
]
STOPS = [-3.0,-3.5,-4.0,-5.0,-6.0]

print("Optimizing stable pairs only...", flush=True)
t0 = time.time()
ALL = []

for pair, info in PAIRS.items():
    print(f"\n{'#'*60}\n# {pair}\n{'#'*60}", flush=True)
    try:
        df = pd.read_csv(PAIR_FILES[pair], index_col=0, parse_dates=True)
    except FileNotFoundError:
        print(f"  No file"); continue
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]
    cost = info['cost']; pip_mul = info['pip']

    for tf, tfp in TF_PARAMS.items():
        df_tf = df if tf=='M5' else aggregate(df, tfp['rule'])
        if len(df_tf) < 200: continue
        for ma in tfp['mas']:
            if ma >= len(df_tf)/3: continue
            arrs = precompute(df_tf, ma)
            for levels in LEVELS_GRID:
                for stop in STOPS:
                    if stop >= levels[-1]: continue
                    for direction in ['long','short']:
                        trades = bt_avg(arrs, direction, levels, stop, cost, pip_mul)
                        s = stats(trades)
                        if s and s['n']>=15 and s['pf']>=1.30 and s['net']>0:
                            yp, yt = per_year_count(trades)
                            ALL.append({
                                'pair':pair,'tf':tf,'ma':ma,'levels':levels,
                                'stop':stop,'direction':direction,
                                **s,'yp':yp,'yt':yt
                            })

print(f"\nDone in {time.time()-t0:.0f}s. {len(ALL)} valid configs.")

# Top per pair
print("\n" + "="*135)
print("MILLOR PER PAIR (top 5 configs):")
print("="*135)
for pair in PAIRS.keys():
    sub = sorted([r for r in ALL if r['pair']==pair], key=lambda x:-x['net'])
    print(f"\n{pair}:")
    for r in sub[:5]:
        lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
        print(f"  {r['tf']:<3} {r['direction']:<5} SMA{r['ma']:<5} levels={lvl:<22} stop={r['stop']} | n={r['n']:>4} WR{r['wr']:.1f}% Net=${r['net']:>+7,.0f} PF{r['pf']:.2f} y+={r['yp']}/{r['yt']}")

# Robust (>=5/6 anys positius)
print("\n" + "="*135)
print("CONFIGS ROBUSTES (5+ anys positius, n>=30):")
print("="*135)
robust = [r for r in ALL if r['yp']>=5 and r['n']>=30]
robust.sort(key=lambda x:-x['net'])
for r in robust[:30]:
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"  {r['pair']:<7} {r['tf']:<3} {r['direction']:<5} SMA{r['ma']:<5} levels={lvl:<22} stop={r['stop']} | n={r['n']:>4} WR{r['wr']:.1f}% Net=${r['net']:>+7,.0f} PF{r['pf']:.2f} y+={r['yp']}/{r['yt']}")

# Best BOTH dirs combo per pair (long + short matching params if possible)
print("\n" + "="*135)
print("BEST BOTH-DIRS combination per pair (LONG+SHORT amb mateixos params):")
print("="*135)
for pair in PAIRS.keys():
    pair_results = [r for r in ALL if r['pair']==pair]
    # Find combos where both LONG and SHORT exist with same MA/levels/stop/TF
    longs = {(r['tf'],r['ma'],tuple(r['levels']),r['stop']):r for r in pair_results if r['direction']=='long'}
    shorts = {(r['tf'],r['ma'],tuple(r['levels']),r['stop']):r for r in pair_results if r['direction']=='short'}
    common = set(longs.keys()) & set(shorts.keys())
    if not common: continue
    combos = []
    for k in common:
        l=longs[k];s=shorts[k]
        combo_net = l['net']+s['net']
        combos.append({'key':k,'long':l,'short':s,'total':combo_net})
    combos.sort(key=lambda x:-x['total'])
    print(f"\n{pair}:")
    for c in combos[:3]:
        tf, ma, lvls, stop = c['key']
        lvl = '/'.join(f'{l:.1f}' for l in lvls)
        l=c['long'];s=c['short']
        print(f"  {tf:<3} SMA{ma:<5} levels={lvl:<22} stop={stop} | L:${l['net']:+,.0f} S:${s['net']:+,.0f} = ${c['total']:+,.0f} (L y+={l['yp']}, S y+={s['yp']})")

print("\nDONE")
# TG notify final
top5 = robust[:5] if 'robust' in dir() else []
msg = "✅ <b>FASE 6 COMPLETA</b>%0A"
for r in top5:
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    msg += f"{r['pair']} {r['tf']} {r['direction']} SMA{r['ma']} → ${r['net']:+.0f} PF{r['pf']:.2f} {r['yp']}/{r['yt']}%0A"
tg_send(msg)
