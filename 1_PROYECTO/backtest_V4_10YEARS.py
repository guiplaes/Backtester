"""V4 OPTIMIZED sobre 10 ANYS — test definitiu inclou crisis."""
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
}
PAIR_FILES = {p: f"{p.lower()}_dk_m5_10y.csv" for p in PAIRS_INFO}

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

# V4 OPTIMIZED 27 strats
STRATEGIES = [
    {'name':'EURGBP D1 L','pair':'EURGBP','tf':'1D','ma':30,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'long'},
    {'name':'EURGBP D1 S','pair':'EURGBP','tf':'1D','ma':30,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'short'},
    {'name':'EURGBP H4 L','pair':'EURGBP','tf':'4h','ma':200,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-5.0,'dir':'long'},
    {'name':'EURGBP H4 S','pair':'EURGBP','tf':'4h','ma':200,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-5.0,'dir':'short'},
    {'name':'EURGBP M15 L','pair':'EURGBP','tf':'15min','ma':2400,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-4.0,'dir':'long'},
    {'name':'GBPCHF D1 S','pair':'GBPCHF','tf':'1D','ma':50,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'short'},
    {'name':'GBPCHF D1 L','pair':'GBPCHF','tf':'1D','ma':50,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'long'},
    {'name':'GBPCHF H4 S','pair':'GBPCHF','tf':'4h','ma':150,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'short'},
    {'name':'GBPCHF H4 S2','pair':'GBPCHF','tf':'4h','ma':300,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'short'},
    {'name':'GBPCHF H1 S','pair':'GBPCHF','tf':'1h','ma':800,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'short'},
    {'name':'AUDCAD M30 L','pair':'AUDCAD','tf':'30min','ma':200,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-6.0,'dir':'long'},
    {'name':'AUDCAD M30 S','pair':'AUDCAD','tf':'30min','ma':200,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-6.0,'dir':'short'},
    {'name':'AUDCAD H4 L','pair':'AUDCAD','tf':'4h','ma':50,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'long'},
    {'name':'AUDCAD H4 L2','pair':'AUDCAD','tf':'4h','ma':200,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-4.0,'dir':'long'},
    {'name':'AUDCAD H1 S','pair':'AUDCAD','tf':'1h','ma':100,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-5.0,'dir':'short'},
    {'name':'AUDCAD H1 L','pair':'AUDCAD','tf':'1h','ma':150,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-4.0,'dir':'long'},
    {'name':'AUDCAD M15 L','pair':'AUDCAD','tf':'15min','ma':200,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-5.0,'dir':'long'},
    {'name':'USDCAD H1 S','pair':'USDCAD','tf':'1h','ma':1200,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-5.0,'dir':'short'},
    {'name':'USDCAD H4 L','pair':'USDCAD','tf':'4h','ma':200,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-3.0,'dir':'long'},
    {'name':'USDCAD H4 S','pair':'USDCAD','tf':'4h','ma':300,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-5.0,'dir':'short'},
    {'name':'NZDCAD D1 L','pair':'NZDCAD','tf':'1D','ma':50,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-3.5,'dir':'long'},
    {'name':'NZDCAD D1 S','pair':'NZDCAD','tf':'1D','ma':50,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-3.5,'dir':'short'},
    {'name':'USDCHF H4 S','pair':'USDCHF','tf':'4h','ma':800,'levels':[-1.5,-2.0,-2.5,-3.0],'stop':-3.5,'dir':'short'},
    {'name':'EURCHF D1 S','pair':'EURCHF','tf':'1D','ma':100,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-5.0,'dir':'short'},
    {'name':'EURCHF H4 S','pair':'EURCHF','tf':'4h','ma':150,'levels':[-1.5,-2.5,-3.5,-4.0],'stop':-6.0,'dir':'short'},
    {'name':'AUDNZD D1 L','pair':'AUDNZD','tf':'1D','ma':75,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-5.0,'dir':'long'},
    {'name':'AUDNZD D1 S','pair':'AUDNZD','tf':'1D','ma':75,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-5.0,'dir':'short'},
]

print(f"V4 OPTIMIZED 10-YEAR test ({len(STRATEGIES)} strats)...", flush=True)

def run_simulation(lot):
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
    ur=pd.DataFrame(index=all_ts);uu=pd.DataFrame(index=all_ts);up=pd.DataFrame(index=all_ts)
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
            'final':fin,'span_y':span_y,'tr':tr,'eq':eq}

results = []
for lot in [0.05, 0.10, 0.15]:
    results.append(run_simulation(lot))

print(f"\nV4 10 ANYS ({results[0]['span_y']:.1f}y)")
for r in results:
    print(f"  Lot {r['lot']}: +{r['annual']:.2f}%/any DD {r['max_dd_pct']:.2f}% Loss inicial {r['loss_init_pct']:.2f}% Calmar {r['calmar']:.2f}")

# Per any
best = max(results, key=lambda x: x['calmar'])
yearly = best['tr'].groupby(pd.to_datetime(best['tr'].index).year).last()
yd = yearly.diff().fillna(yearly.iloc[0])
print(f"\nPer any (lot {best['lot']}):")
yp = yn = 0
for yr in yd.index:
    p = yd.loc[yr];pct = p/CAPITAL_INICIAL*100
    if p>0: yp+=1
    else: yn+=1
    print(f"  {yr}: ${p:>+10,.0f} ({pct:+.2f}%)")
print(f"\nAnys positius: {yp}/{yp+yn}")

msg = f"📊 <b>V4 OPTIMIZED 10 ANYS</b>%0A%0A"
for r in results:
    msg += f"Lot {r['lot']}: +{r['annual']:.1f}%/any DD {r['max_dd_pct']:.1f}% (Calmar {r['calmar']:.2f})%0A"
msg += f"%0AAnys positius: {yp}/{yp+yn}"
tg_send(msg)
print("\nDONE")
