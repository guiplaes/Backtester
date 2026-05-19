"""
TEST EXTREME — MAs MOLT LLARGUES per major estabilitat
======================================================
La hipotesi: amb MA molt llargues (1000+ bars D1, 5000+ H4), el sistema
detecta swing macro i no es deixa enganyar per trends curts/mig.

Nomes pairs estables, BOTH dirs.
"""
import pandas as pd
import numpy as np
import sys
sys.path.insert(0, '.')
try:
    from tg_send import send as tg_send
except: tg_send = lambda x: None

LOT = 0.05
PAIRS = {
    'EURGBP':{'cost':0.30,'pip':1000},
    'EURCHF':{'cost':0.30,'pip':1000},
    'GBPCHF':{'cost':0.40,'pip':1000},
    'AUDNZD':{'cost':0.40,'pip':1000},
    'USDCHF':{'cost':0.30,'pip':1000},
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
            if stop_hit or target or (i-pos['entries'][0][0])>=2000:
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
    return yp, df_t['year'].nunique()

# Test wide MA range
TF_MAS = {
    'D1': [50, 100, 200, 300, 500, 800],
    'H4': [200, 500, 1000, 2000, 3000],
    'H1': [500, 1000, 2000, 4000],
}

LEVELS_GRID = [
    [-0.5,-1.0,-1.5,-2.0],[-1.0,-1.5,-2.0,-2.5],
    [-1.5,-2.0,-2.5,-3.0],[-2.0,-2.5,-3.0,-3.5],
    [-1.0,-2.0,-3.0],[-1.5,-2.5,-3.5],
]
STOPS = [-3.0,-4.0,-5.0,-6.0]

print("Extreme long MA test — stable pairs BOTH dirs...", flush=True)
ALL = []
for pair, info in PAIRS.items():
    print(f"\n{pair}", flush=True)
    df = pd.read_csv(PAIR_FILES[pair], index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]
    cost = info['cost']; pip_mul = info['pip']
    for tf, mas in TF_MAS.items():
        rule = '1D' if tf=='D1' else ('4h' if tf=='H4' else '1h')
        df_tf = aggregate(df, rule)
        for ma in mas:
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
                                'stop':stop,'dir':direction,
                                **s,'yp':yp,'yt':yt
                            })

ALL.sort(key=lambda x:-x['net'])

# Top per pair amb both dirs available
print("\n" + "="*135)
print("TOP CONFIGS (per pair, robustas):")
print("="*135)
for pair in PAIRS.keys():
    sub = sorted([r for r in ALL if r['pair']==pair and r['yp']>=4], key=lambda x:-x['net'])
    print(f"\n{pair}:")
    for r in sub[:5]:
        lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
        print(f"  {r['tf']:<3} {r['dir']:<5} SMA{r['ma']:<5} levels={lvl:<22} stop={r['stop']} | n={r['n']:>4} WR{r['wr']:.1f}% Net=${r['net']:>+7,.0f} PF{r['pf']:.2f} y+={r['yp']}/{r['yt']}")

# Robust BOTH dirs configs (where LONG and SHORT both work)
print("\n" + "="*135)
print("BOTH-DIRS BENEFICIOSOS (LONG+SHORT junts net positiu):")
print("="*135)
for pair in PAIRS.keys():
    pair_results = [r for r in ALL if r['pair']==pair]
    longs = {(r['tf'],r['ma'],tuple(r['levels']),r['stop']):r for r in pair_results if r['dir']=='long'}
    shorts = {(r['tf'],r['ma'],tuple(r['levels']),r['stop']):r for r in pair_results if r['dir']=='short'}
    common = set(longs.keys()) & set(shorts.keys())
    if not common: continue
    combos = []
    for k in common:
        l=longs[k];s=shorts[k]
        if l['yp']>=4 and s['yp']>=4:
            combos.append({'k':k,'l':l,'s':s,'total':l['net']+s['net']})
    combos.sort(key=lambda x:-x['total'])
    if not combos: continue
    print(f"\n{pair}:")
    for c in combos[:3]:
        tf,ma,lvls,stop = c['k']
        lvl='/'.join(f'{l:.1f}' for l in lvls)
        print(f"  {tf:<3} SMA{ma:<5} levels={lvl:<22} stop={stop} | L:${c['l']['net']:+,.0f} (y+{c['l']['yp']}) S:${c['s']['net']:+,.0f} (y+{c['s']['yp']}) = ${c['total']:+,.0f}")

# TG notify
top_msg = "✅ <b>EXTREME MA TEST</b>%0A%0ATop combos BOTH dirs:%0A"
for pair in PAIRS.keys():
    pair_results = [r for r in ALL if r['pair']==pair]
    longs = {(r['tf'],r['ma'],tuple(r['levels']),r['stop']):r for r in pair_results if r['dir']=='long'}
    shorts = {(r['tf'],r['ma'],tuple(r['levels']),r['stop']):r for r in pair_results if r['dir']=='short'}
    common = set(longs.keys()) & set(shorts.keys())
    best_combo_total = 0
    for k in common:
        l=longs[k];s=shorts[k]
        if l['yp']>=4 and s['yp']>=4:
            t = l['net']+s['net']
            if t > best_combo_total: best_combo_total = t
    top_msg += f"{pair}: ${best_combo_total:+,.0f}%0A"
tg_send(top_msg)
print("\nDONE")
