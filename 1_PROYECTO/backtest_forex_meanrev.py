"""
Mean-Rev Averaging sobre TOTS els forex pairs descarregats (H1 4y)
===================================================================
EURCHF, EURGBP, AUDNZD, USDCHF, EURJPY, CHFJPY, USDCNH, NZDCHF, CADCHF, AUDCHF, USDHKD
"""
import pandas as pd
import numpy as np
import time
import os

PAIRS_FILES = {
    'EURCHF': 'eurchf_h1_5y.csv',
    'EURGBP': 'eurgbp_h1_5y.csv',
    'AUDNZD': 'audnzd_h1_5y.csv',
    'USDCHF': 'usdchf_h1_5y.csv',
    'EURJPY': 'eurjpy_h1_5y.csv',
    'CHFJPY': 'chfjpy_h1_5y.csv',
    'USDCNH': 'usdcnh_h1_5y.csv',
    'NZDCHF': 'nzdchf_h1_5y.csv',
    'CADCHF': 'cadchf_h1_5y.csv',
    'AUDCHF': 'audchf_h1_5y.csv',
    'USDHKD': 'usdhkd_h1_5y.csv',
}

# Costs estimats per 0.01 lot RT (spread + commission)
# Forex majors: ~$0.10 per pip per 0.01 lot
# Spread típic: 0.5-3 pips depenent del parell
# Cost RT $0.10-0.30
COSTS = {
    'EURCHF': 0.20, 'EURGBP': 0.15, 'AUDNZD': 0.20, 'USDCHF': 0.15,
    'EURJPY': 0.15, 'CHFJPY': 0.20, 'USDCNH': 0.30, 'NZDCHF': 0.25,
    'CADCHF': 0.25, 'AUDCHF': 0.25, 'USDHKD': 0.10,
}

# Pip values per 0.01 lot in $
# JPY pairs: 0.01 = $0.10/pip ; non-JPY: 0.0001 = $0.10/pip ; HKD: small
# General formula: pip = price diff in 4th/2nd decimal × 100k × lot
# For 0.01 lot, multiplying price-diff by 100 gives $ for non-JPY, by 1 for JPY
def pip_multiplier(pair):
    """Return multiplier to convert price diff to $ per 0.01 lot."""
    if 'JPY' in pair: return 100  # JPY pair: 1 = 100 pips, $1/pip × 0.01 = $1 → 100$ per unit price
    if 'HKD' in pair: return 12.8  # USDHKD ~7.78, $0.001 ~ $0.13/pip equivalent
    if 'CNH' in pair: return 14  # similar
    return 1000  # standard non-JPY: 1 unit price = 10000 pips × $0.10 = $1000

def aggregate_h1(df_, rule):
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
    return {'n':n,'wr':wins/n*100,'net':net,'pf':pf,'dd':dd,'avg_u':avg_u}

def fmt(s):
    if s is None: return "0"
    return f"n={s['n']:>4} WR{s['wr']:>4.1f}% Net=${s['net']:>+7.0f} PF{s['pf']:>5.2f} DD${s['dd']:>5.0f} u={s['avg_u']:.1f}"

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

# Load and aggregate to H1, H4, D1
print("Loading + aggregating...", flush=True)
DATA = {}
TF_MA = {
    'H1':  [50, 100, 200, 400, 800, 1200, 1600],
    'H4':  [50, 100, 150, 200, 300, 500],
    'D1':  [20, 30, 50, 100, 150],
}
for pair, csv_file in PAIRS_FILES.items():
    if not os.path.exists(csv_file):
        print(f"  {pair}: file missing")
        continue
    df_h1 = pd.read_csv(csv_file, index_col=0, parse_dates=True)
    df_h1.index = pd.to_datetime(df_h1.index, utc=True)
    DATA[pair] = {
        'H1': precompute(df_h1, TF_MA['H1']),
        'H4': precompute(aggregate_h1(df_h1, '4h'), TF_MA['H4']),
        'D1': precompute(aggregate_h1(df_h1, '1D'), TF_MA['D1']),
    }
    print(f"  {pair}: H1={DATA[pair]['H1']['n']} H4={DATA[pair]['H4']['n']} D1={DATA[pair]['D1']['n']} bars", flush=True)

# Yearly returns of each pair (diagnostic)
print("\nRendiments anuals per pair:")
for pair in PAIRS_FILES.keys():
    if pair not in DATA: continue
    df = pd.read_csv(PAIRS_FILES[pair], index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    yrs = df.index.year.unique()
    line = f"  {pair:<7}: "
    for yr in sorted(yrs):
        sub = df[df.index.year==yr]
        if len(sub)<10: continue
        pct = (sub['close'].iloc[-1] - sub['close'].iloc[0]) / sub['close'].iloc[0] * 100
        line += f"{yr}:{pct:+.1f}%  "
    print(line)

LEVELS_GRID = [
    [-1.0],[-1.5],[-2.0],
    [-1.0,-2.0],[-1.5,-2.5],[-2.0,-3.0],
    [-1.0,-2.0,-3.0],[-1.5,-2.5,-3.5],
    [-0.5,-1.0,-1.5,-2.0],[-1.0,-1.5,-2.0,-2.5],
    [-0.5,-1.5,-2.5],
]
STOPS = [-3.0,-3.5,-4.0,-5.0,-6.0]

print("\nRunning sweep on all forex pairs...", flush=True)
ALL = []
t0 = time.time()
for pair in PAIRS_FILES.keys():
    if pair not in DATA: continue
    cost = COSTS[pair]
    pip_mul = pip_multiplier(pair)
    for tf in ['H1','H4','D1']:
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
                        if s and s['n']>=15 and s['pf']>=1.30 and s['net']>0:
                            ALL.append({'pair':pair,'tf':tf,'ma':ma,'levels':levels,
                                        'stop':stop,'direction':direction,
                                        'stats':s,'trades':trades})

print(f"\nDone in {time.time()-t0:.0f}s. {len(ALL)} valid configs.")

ALL.sort(key=lambda x:-x['stats']['pf'])

print()
print("="*150)
print("TOP 30 GLOBAL (tots pairs):")
print("="*150)
for r in ALL[:30]:
    s = r['stats']
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"  {r['pair']:<7} {r['tf']:<3} SMA{r['ma']:<4} {r['direction']:<5} levels={lvl:<24} stop={r['stop']} | {fmt(s)}")

print()
print("="*150)
print("MILLOR PER PAIR (TOP 1 per pair):")
print("="*150)
seen = set()
for r in ALL:
    if r['pair'] in seen: continue
    seen.add(r['pair'])
    s = r['stats']
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"  {r['pair']:<7} {r['tf']:<3} SMA{r['ma']:<4} {r['direction']:<5} levels={lvl:<24} stop={r['stop']} | {fmt(s)}")

print()
print("="*150)
print("ANY-PER-ANY top 10 (robustesa):")
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
        print(f"  {sgn} {yr}: n={y['n']:>3} WR{y['wr']:>5.1f}% Net=${y['net']:>+7.0f}")

print("\nDONE")
