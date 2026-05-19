"""
EXPLORADOR DE VARIANTS — més enllà del V6
==========================================
Testeja 7 variants de la mateixa idea mean-rev sobre les 10 pairs V6 i 10y data:
  V0  - Baseline V6 (levels -0.5/-1/-1.5/-2)
  V1  - DEEP (levels -1/-1.5/-2/-2.5)
  V2  - VERY_DEEP (levels -1.5/-2/-2.5/-3)
  V3  - EXTREME_DEEP (levels -2/-2.5/-3/-3.5)
  V4  - WIDE (levels -0.5/-1.5/-2.5/-3.5)
  V5  - TIGHT_DEEP (levels -2/-2.25/-2.5/-2.75)
  V6  - DUAL_MA % deviation (fast SMA vs slow SMA)
  V7  - SINGLE_DEEP_NO_AVG (1 sola entrada a -2.5σ, sense averaging)

Per cada variant, mesurem Calmar/DD/Annual sobre 10y.
"""
import pandas as pd
import numpy as np
import sys, time, os, json
sys.path.insert(0, '.')
try: from tg_send import send as tg_send
except: tg_send = lambda x: None

CAPITAL_INICIAL = 63000.0

PAIRS_INFO = {
    'EURGBP':{'cost':0.30,'pip':1000},'EURCHF':{'cost':0.30,'pip':1000},
    'GBPCHF':{'cost':0.40,'pip':1000},'AUDCAD':{'cost':0.40,'pip':1000},
    'USDCAD':{'cost':0.30,'pip':1000},'USDCHF':{'cost':0.30,'pip':1000},
    'NZDCAD':{'cost':0.40,'pip':1000},'AUDNZD':{'cost':0.40,'pip':1000},
    'GBPNZD':{'cost':0.50,'pip':1000},'EURNZD':{'cost':0.50,'pip':1000},
}
PAIR_FILES = {p: f"{p.lower()}_dk_m5_10y.csv" for p in PAIRS_INFO}

# Config base (pair, tf, ma, dir, stop)
BASE_STRATS = [
    ('EURGBP','1D',30,'long',-4.0),
    ('EURGBP','1D',30,'short',-4.0),
    ('EURGBP','4h',200,'long',-5.0),
    ('GBPCHF','1D',50,'short',-4.0),
    ('GBPCHF','4h',150,'short',-4.0),
    ('AUDCAD','30min',200,'long',-6.0),
    ('AUDCAD','30min',200,'short',-6.0),
    ('AUDCAD','4h',200,'long',-4.0),
    ('USDCAD','1h',1200,'short',-5.0),
    ('USDCAD','4h',200,'long',-3.0),
    ('NZDCAD','1D',50,'long',-3.5),
    ('NZDCAD','1D',50,'short',-3.5),
    ('USDCHF','4h',800,'short',-3.5),
    ('EURCHF','1D',100,'short',-5.0),
    ('AUDNZD','1D',75,'long',-5.0),
    ('AUDNZD','1D',75,'short',-5.0),
    ('GBPNZD','1h',100,'long',-5.0),
    ('GBPNZD','4h',50,'long',-5.0),
    ('EURNZD','4h',50,'long',-4.0),
]

# Variants
VARIANTS = {
    'V0_BASELINE':       {'levels':[-0.5,-1.0,-1.5,-2.0], 'kind':'sigma'},
    'V1_DEEP':           {'levels':[-1.0,-1.5,-2.0,-2.5], 'kind':'sigma'},
    'V2_VERY_DEEP':      {'levels':[-1.5,-2.0,-2.5,-3.0], 'kind':'sigma'},
    'V3_EXTREME_DEEP':   {'levels':[-2.0,-2.5,-3.0,-3.5], 'kind':'sigma'},
    'V4_WIDE':           {'levels':[-0.5,-1.5,-2.5,-3.5], 'kind':'sigma'},
    'V5_TIGHT_DEEP':     {'levels':[-2.0,-2.25,-2.5,-2.75],'kind':'sigma'},
    'V6_DUAL_MA':        {'fast':20,'slow':100,'levels':[-1.0,-1.5,-2.0,-2.5], 'kind':'dual_ma'},
    'V7_SINGLE_DEEP':    {'levels':[-2.5,-2.5,-2.5,-2.5], 'kind':'sigma','no_avg':True},
}

def aggregate(df_, rule):
    return df_.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last')).dropna()

def precompute_sigma(df_, ma_p):
    return {'O':df_['open'].values,'H':df_['high'].values,
            'L':df_['low'].values,'C':df_['close'].values,
            'SMA':df_['close'].rolling(ma_p).mean().values,
            'STD':df_['close'].rolling(ma_p).std().values,
            'TS':df_.index, 'n':len(df_)}

def precompute_dual_ma(df_, fast, slow):
    """Per dual-MA, calculem (fast-slow)/slow*100 com a 'z'"""
    fma = df_['close'].rolling(fast).mean()
    sma = df_['close'].rolling(slow).mean()
    dev = (fma - sma) / sma * 100  # % deviation
    # Std del deviation com a referencia (per fer levels comparables)
    dev_std = dev.rolling(slow).std()
    z_eq = dev / dev_std  # z-score del deviation
    return {'O':df_['open'].values,'H':df_['high'].values,
            'L':df_['low'].values,'C':df_['close'].values,
            'SMA':sma.values,
            'STD':dev_std.values,
            'Z':z_eq.values,
            'TS':df_.index, 'n':len(df_)}

def simulate(arrs, direction, levels, stop_z, cost, pip_mul, lot, kind, no_avg=False):
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    n=arrs['n']
    SMA=arrs['SMA']
    if kind=='sigma':
        STD=arrs['STD']
    states=[];realized=0.0;pos=None
    levels_unique = list(dict.fromkeys(levels))  # for no_avg, just first level matters
    for i in range(50,n):
        sma=SMA[i];c=C[i]
        if kind=='sigma':
            std=STD[i]
            if np.isnan(sma) or np.isnan(std) or std<=0:
                states.append({'ts':arrs['TS'][i],'realized':realized,'unrealized':0.0,'pos_units':0});continue
            z=(c-sma)/std
        else:  # dual_ma
            z=arrs['Z'][i]
            if np.isnan(sma) or np.isnan(z):
                states.append({'ts':arrs['TS'][i],'realized':realized,'unrealized':0.0,'pos_units':0});continue
        unrealized=0.0;pos_units=0
        if pos is not None:
            sgn=1 if direction=='long' else -1
            for eidx,ep in pos['entries']: unrealized+=(c-ep)*sgn*pip_mul*(lot/0.01)
            pos_units=len(pos['entries'])
            if direction=='long': stop_hit=z<=stop_z;target=c>=sma
            else: stop_hit=z>=-stop_z;target=c<=sma
            if stop_hit or target or (i-pos['entries'][0][0])>=500:
                tot_u=len(pos['entries']);pnl=0
                for eidx,ep in pos['entries']: pnl+=(c-ep)*sgn
                pnl_d=pnl*pip_mul*(lot/0.01)-tot_u*cost*(lot/0.01)
                realized+=pnl_d;unrealized=0;pos_units=0;pos=None
            elif not no_avg:
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
        if pos is not None:
            sgn=1 if direction=='long' else -1
            unrealized=0
            for eidx,ep in pos['entries']: unrealized+=(c-ep)*sgn*pip_mul*(lot/0.01)
            pos_units=len(pos['entries'])
        states.append({'ts':arrs['TS'][i],'realized':realized,'unrealized':unrealized,'pos_units':pos_units})
    return pd.DataFrame(states).set_index('ts')

LOT = 0.05  # comparem totes amb el mateix lot

# Cache dataframes per pair to avoid re-reading
_DF_CACHE = {}
def load(pair):
    if pair in _DF_CACHE: return _DF_CACHE[pair]
    df = pd.read_csv(PAIR_FILES[pair], index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]
    _DF_CACHE[pair] = df
    return df

# Cache aggregated TF per (pair, tf)
_TF_CACHE = {}
def load_tf(pair, tf):
    k=(pair,tf)
    if k in _TF_CACHE: return _TF_CACHE[k]
    df = load(pair)
    df_tf = aggregate(df, tf)
    _TF_CACHE[k] = df_tf
    return df_tf

print(f"VARIANTS EXPLORER — {len(VARIANTS)} variants × {len(BASE_STRATS)} strats", flush=True)
tg_send(f"🔬 Variants explorer iniciat: {len(VARIANTS)} variants × {len(BASE_STRATS)} strats sobre 10y")

ALL_RESULTS = {}

for vname, vcfg in VARIANTS.items():
    t0=time.time()
    print(f"\n{'='*80}\n{vname}: {vcfg}", flush=True)
    sst = {}
    for (pair,tf,ma,dir_,stop) in BASE_STRATS:
        df_tf = load_tf(pair, tf)
        if vcfg['kind']=='dual_ma':
            arrs = precompute_dual_ma(df_tf, vcfg['fast'], vcfg['slow'])
        else:
            arrs = precompute_sigma(df_tf, ma)
        no_avg = vcfg.get('no_avg', False)
        sname = f"{pair} {tf} {dir_[0].upper()}"
        sst[sname] = simulate(arrs, dir_, vcfg['levels'], stop,
            PAIRS_INFO[pair]['cost'], PAIRS_INFO[pair]['pip'], LOT,
            vcfg['kind'], no_avg=no_avg)

    all_ts = pd.DatetimeIndex([])
    for sn,st in sst.items(): all_ts = all_ts.union(st.index)
    all_ts = all_ts.sort_values()
    ur=pd.DataFrame(index=all_ts);uu=pd.DataFrame(index=all_ts)
    for sn,st in sst.items():
        ur[sn] = st['realized'].reindex(all_ts, method='ffill').fillna(0)
        uu[sn] = st['unrealized'].reindex(all_ts, method='ffill').fillna(0)

    tr = ur.sum(axis=1);tu = uu.sum(axis=1)
    eq = CAPITAL_INICIAL + tr + tu
    peak = eq.expanding().max()
    dd_pct = (peak-eq)/peak*100
    fin = tr.iloc[-1]
    span_y = (all_ts[-1]-all_ts[0]).days/365
    annual = fin/CAPITAL_INICIAL*100/span_y
    calmar = annual/dd_pct.max() if dd_pct.max()>0 else 0
    loss_init = max(0,CAPITAL_INICIAL-eq.min())/CAPITAL_INICIAL*100

    # Yearly breakdown
    yearly = tr.groupby(pd.to_datetime(tr.index).year).last()
    yd = yearly.diff().fillna(yearly.iloc[0])
    yrs_pos = (yd>0).sum()
    yrs_neg = (yd<=0).sum()
    worst_y = yd.min()/CAPITAL_INICIAL*100
    best_y = yd.max()/CAPITAL_INICIAL*100

    ALL_RESULTS[vname] = {
        'annual':annual,'dd':dd_pct.max(),'calmar':calmar,
        'loss_init':loss_init,'final':fin,
        'yrs_pos':int(yrs_pos),'yrs_neg':int(yrs_neg),
        'worst_y':worst_y,'best_y':best_y,
        'time_s': time.time()-t0,
    }
    print(f"  Annual {annual:+.2f}% DD {dd_pct.max():.2f}% Calmar {calmar:.2f} | Loss init {loss_init:.2f}% | {yrs_pos}/{yrs_pos+yrs_neg} pos | worst yr {worst_y:.1f}% best {best_y:.1f}% | {time.time()-t0:.0f}s")

# Final sorted report
print("\n" + "="*100)
print("RESUM ORDENAT PER CALMAR")
print("="*100)
print(f"  {'Variant':<22} {'Annual':>8} {'DD%':>7} {'Calmar':>7} {'Loss_init':>10} {'Yrs+':>5} {'Worst':>7} {'Best':>7}")
ranked = sorted(ALL_RESULTS.items(), key=lambda x: -x[1]['calmar'])
for vn, r in ranked:
    print(f"  {vn:<22} {r['annual']:+7.2f}% {r['dd']:6.2f}% {r['calmar']:6.2f} {r['loss_init']:9.2f}% {r['yrs_pos']:>2}/{r['yrs_pos']+r['yrs_neg']} {r['worst_y']:6.1f}% {r['best_y']:6.1f}%")

# Save JSON
with open('variants_results.json','w') as f:
    json.dump(ALL_RESULTS, f, indent=2)
print("\nSaved variants_results.json")

# TG summary
msg = "🔬 <b>VARIANTS EXPLORER</b> (10y, lot 0.05)%0A%0A"
msg += "<b>Ranking per Calmar:</b>%0A"
for vn, r in ranked:
    msg += f"{vn}: <b>+{r['annual']:.1f}%%</b>/an DD <b>{r['dd']:.1f}%%</b> Calmar <b>{r['calmar']:.2f}</b>%0A"
msg += f"%0A<b>Best:</b> {ranked[0][0]} Calmar {ranked[0][1]['calmar']:.2f}"
tg_send(msg)
print("\nDONE")
