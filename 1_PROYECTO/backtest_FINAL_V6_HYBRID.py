"""V6 HYBRID — V4 amb AUDCAD reduït a 3 estrategies (de 7) per millor diversificació."""
import pandas as pd
import numpy as np
import sys
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
PAIR_FILES = {p: f"{p.lower()}_dk_m5_5y.csv" for p in PAIRS_INFO}

def aggregate(df_, rule):
    return df_.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last')).dropna()

def precompute(df_, ma_p):
    return {'O':df_['open'].values,'H':df_['high'].values,
            'L':df_['low'].values,'C':df_['close'].values,
            'SMA':df_['close'].rolling(ma_p).mean().values,
            'STD':df_['close'].rolling(ma_p).std().values,
            'TS':df_.index, 'n':len(df_)}

def simulate_with_state(arrs, direction, levels, stop_z, cost, pip_mul, lot):
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    SMA=arrs['SMA'];STD=arrs['STD'];n=arrs['n']
    states=[];realized=0.0;pos=None
    for i in range(50,n):
        sma=SMA[i];std=STD[i];c=C[i]
        if np.isnan(sma) or np.isnan(std) or std<=0:
            states.append({'ts':arrs['TS'][i],'realized':realized,'unrealized':0.0,'pos_units':0});continue
        z=(c-sma)/std
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
            else:
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

# V6 HYBRID — equilibri V4-V5
STRATEGIES = [
    # EURGBP × 3
    {'name':'EURGBP D1 L','pair':'EURGBP','tf':'1D','ma':30,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'long'},
    {'name':'EURGBP D1 S','pair':'EURGBP','tf':'1D','ma':30,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'short'},
    {'name':'EURGBP H4 L','pair':'EURGBP','tf':'4h','ma':200,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-5.0,'dir':'long'},

    # GBPCHF × 3 (mantenim — top performers)
    {'name':'GBPCHF D1 S','pair':'GBPCHF','tf':'1D','ma':50,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'short'},
    {'name':'GBPCHF H4 S','pair':'GBPCHF','tf':'4h','ma':150,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'short'},
    {'name':'GBPCHF H1 S','pair':'GBPCHF','tf':'1h','ma':800,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'short'},

    # AUDCAD × 3 REDUÏT (de 7)
    {'name':'AUDCAD M30 L','pair':'AUDCAD','tf':'30min','ma':200,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-6.0,'dir':'long'},
    {'name':'AUDCAD M30 S','pair':'AUDCAD','tf':'30min','ma':200,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-6.0,'dir':'short'},
    {'name':'AUDCAD H4 L','pair':'AUDCAD','tf':'4h','ma':200,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-4.0,'dir':'long'},

    # USDCAD × 2
    {'name':'USDCAD H1 S','pair':'USDCAD','tf':'1h','ma':1200,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-5.0,'dir':'short'},
    {'name':'USDCAD H4 L','pair':'USDCAD','tf':'4h','ma':200,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-3.0,'dir':'long'},

    # NZDCAD × 2 (BOTH) — diversifica de CAD
    {'name':'NZDCAD D1 L','pair':'NZDCAD','tf':'1D','ma':50,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-3.5,'dir':'long'},
    {'name':'NZDCAD D1 S','pair':'NZDCAD','tf':'1D','ma':50,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-3.5,'dir':'short'},

    # USDCHF × 1
    {'name':'USDCHF H4 S','pair':'USDCHF','tf':'4h','ma':800,'levels':[-1.5,-2.0,-2.5,-3.0],'stop':-3.5,'dir':'short'},

    # EURCHF × 2
    {'name':'EURCHF D1 S','pair':'EURCHF','tf':'1D','ma':100,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-5.0,'dir':'short'},
    {'name':'EURCHF H4 S','pair':'EURCHF','tf':'4h','ma':150,'levels':[-1.5,-2.5,-3.5,-4.0],'stop':-6.0,'dir':'short'},

    # AUDNZD × 2 (BOTH)
    {'name':'AUDNZD D1 L','pair':'AUDNZD','tf':'1D','ma':75,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-5.0,'dir':'long'},
    {'name':'AUDNZD D1 S','pair':'AUDNZD','tf':'1D','ma':75,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-5.0,'dir':'short'},

    # GBPNZD × 2 (volum alt + 6/6 anys)
    {'name':'GBPNZD H1 L','pair':'GBPNZD','tf':'1h','ma':100,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-5.0,'dir':'long'},
    {'name':'GBPNZD H4 L','pair':'GBPNZD','tf':'4h','ma':50,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-5.0,'dir':'long'},

    # EURNZD × 1 (6/6 anys)
    {'name':'EURNZD H4 L','pair':'EURNZD','tf':'4h','ma':50,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'long'},
]

print(f"V6 HYBRID — {len(STRATEGIES)} strats sobre {len(set(s['pair'] for s in STRATEGIES))} pairs", flush=True)

# Currency exposure
from collections import Counter
ccy_count = Counter()
for s in STRATEGIES:
    pair = s['pair']
    base, quote = pair[:3], pair[3:]
    ccy_count[base] += 1
    ccy_count[quote] += 1
print("\nExposicio per moneda:")
for ccy, n in sorted(ccy_count.items(), key=lambda x:-x[1]):
    pct = n / (len(STRATEGIES)*2) * 100
    print(f"  {ccy}: {n} ({pct:.1f}%)")

def run(lot):
    print(f"\nLot {lot}...", flush=True)
    sst = {}
    for s in STRATEGIES:
        df = pd.read_csv(PAIR_FILES[s['pair']], index_col=0, parse_dates=True)
        df.index = pd.to_datetime(df.index, utc=True)
        df.columns = [c.lower() for c in df.columns]
        df_tf = aggregate(df, s['tf'])
        arrs = precompute(df_tf, s['ma'])
        sst[s['name']] = simulate_with_state(arrs, s['dir'], s['levels'], s['stop'],
            PAIRS_INFO[s['pair']]['cost'], PAIRS_INFO[s['pair']]['pip'], lot)
    all_ts = pd.DatetimeIndex([])
    for sn,st in sst.items(): all_ts = all_ts.union(st.index)
    all_ts = all_ts.sort_values()
    ur = pd.DataFrame(index=all_ts);uu = pd.DataFrame(index=all_ts);up = pd.DataFrame(index=all_ts)
    for sn,st in sst.items():
        ur[sn] = st['realized'].reindex(all_ts, method='ffill').fillna(0)
        uu[sn] = st['unrealized'].reindex(all_ts, method='ffill').fillna(0)
        up[sn] = st['pos_units'].reindex(all_ts, method='ffill').fillna(0)
    tr = ur.sum(axis=1);tu = uu.sum(axis=1)
    eq = CAPITAL_INICIAL + tr + tu
    peak = eq.expanding().max()
    dd_pct = (peak-eq)/peak*100
    fin = tr.iloc[-1]
    span_y = (all_ts[-1]-all_ts[0]).days/365
    annual = fin/CAPITAL_INICIAL*100/span_y
    return {'lot':lot,'annual':annual,'max_dd_pct':dd_pct.max(),
            'loss_init_pct':max(0,CAPITAL_INICIAL-eq.min())/CAPITAL_INICIAL*100,
            'calmar':annual/dd_pct.max() if dd_pct.max()>0 else 0,
            'max_pos':int(up.sum(axis=1).max()),'tr':tr}

results = []
for lot in [0.05,0.10,0.15,0.20]:
    results.append(run(lot))

print("\n" + "="*120)
print(f"V6 HYBRID — {len(STRATEGIES)} strats")
print("="*120)
print(f"  {'Lot':<6} {'Annual':>9} {'DD%':>7} {'Loss init%':>11} {'Calmar':>7} {'Max Pos':>8}")
for r in results:
    print(f"  {r['lot']:<6} +{r['annual']:>5.2f}% {r['max_dd_pct']:>5.2f}% {r['loss_init_pct']:>9.2f}% {r['calmar']:>6.2f} {r['max_pos']:>5}")

best = max(results, key=lambda x: x['calmar'])
yearly = best['tr'].groupby(pd.to_datetime(best['tr'].index).year).last()
yd = yearly.diff().fillna(yearly.iloc[0])
print(f"\nPer any (lot {best['lot']}):")
for yr in yd.index:
    p = yd.loc[yr];pct = p/CAPITAL_INICIAL*100
    print(f"  {yr}: ${p:>+9,.0f} ({pct:+.2f}%)")

ccy_top = sorted(ccy_count.items(), key=lambda x:-x[1])[:5]
ccy_msg = "%0AExposició:%0A"
for ccy,n in ccy_top:
    pct = n/(len(STRATEGIES)*2)*100
    ccy_msg += f"{ccy}: {pct:.0f}%%0A"

msg = f"⚖️ <b>V6 HYBRID — {len(STRATEGIES)} strats, {len(set(s['pair'] for s in STRATEGIES))} pairs</b>%0A{ccy_msg}%0A"
for r in results:
    msg += f"Lot {r['lot']}: <b>+{r['annual']:.1f}%</b> DD <b>{r['max_dd_pct']:.1f}%</b> Calmar {r['calmar']:.2f}%0A"
tg_send(msg)
print("\nDONE")
