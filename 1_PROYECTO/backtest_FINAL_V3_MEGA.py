"""
PORTFOLIO V3 MEGA — TOTS els nous pairs guanyadors
====================================================
26 estratègies sobre 12 pairs (NOMÉS estables, NO JPY).
Tots BOTH directions o direcció òptima validada 5+ anys.
"""
import pandas as pd
import numpy as np
import sys
sys.path.insert(0, '.')
try: from tg_send import send as tg_send
except: tg_send = lambda x: None

CAPITAL_INICIAL = 63000.0

PAIRS_INFO = {
    'EURGBP':{'cost':0.30,'pip':1000},
    'EURCHF':{'cost':0.30,'pip':1000},
    'GBPCHF':{'cost':0.40,'pip':1000},
    'AUDCAD':{'cost':0.40,'pip':1000},
    'USDCAD':{'cost':0.30,'pip':1000},
    'USDCHF':{'cost':0.30,'pip':1000},
    'GBPNZD':{'cost':0.50,'pip':1000},
    'EURNZD':{'cost':0.50,'pip':1000},
    'EURAUD':{'cost':0.40,'pip':1000},
    'GBPAUD':{'cost':0.50,'pip':1000},
    'AUDUSD':{'cost':0.30,'pip':1000},
    'NZDUSD':{'cost':0.40,'pip':1000},
    'EURCAD':{'cost':0.40,'pip':1000},
}
PAIR_FILES = {p: f"{p.lower()}_dk_m5_5y.csv" for p in PAIRS_INFO}

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

def simulate_with_state(arrs, direction, levels, stop_z, cost, pip_mul, lot):
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    SMA=arrs['SMA'];STD=arrs['STD']
    n=arrs['n']
    states = []
    realized = 0.0
    pos = None
    for i in range(50, n):
        sma=SMA[i];std=STD[i];c=C[i]
        if np.isnan(sma) or np.isnan(std) or std<=0:
            states.append({'ts':arrs['TS'][i],'realized':realized,'unrealized':0.0,'pos_units':0})
            continue
        z=(c-sma)/std
        unrealized = 0.0; pos_units = 0
        if pos is not None:
            sgn = 1 if direction=='long' else -1
            for eidx, ep in pos['entries']:
                unrealized += (c - ep) * sgn * pip_mul * (lot/0.01)
            pos_units = len(pos['entries'])
            if direction=='long': stop_hit=z<=stop_z;target=c>=sma
            else: stop_hit=z>=-stop_z;target=c<=sma
            if stop_hit or target or (i-pos['entries'][0][0])>=500:
                tot_u=len(pos['entries']);pnl=0
                for eidx,ep in pos['entries']: pnl+=(c-ep)*sgn
                pnl_d = pnl*pip_mul*(lot/0.01) - tot_u*cost*(lot/0.01)
                realized += pnl_d
                unrealized = 0; pos_units = 0; pos = None
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
            sgn = 1 if direction=='long' else -1
            unrealized = 0
            for eidx, ep in pos['entries']:
                unrealized += (c - ep) * sgn * pip_mul * (lot/0.01)
            pos_units = len(pos['entries'])
        states.append({'ts':arrs['TS'][i],'realized':realized,
                       'unrealized':unrealized,'pos_units':pos_units})
    return pd.DataFrame(states).set_index('ts')

# 26 estratègies validades — només estables, configs òptimes
STRATEGIES = [
    # EURGBP × 4 TFs BOTH
    {'name':'EURGBP D1 L','pair':'EURGBP','tf':'1D','ma':100,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'long'},
    {'name':'EURGBP D1 S','pair':'EURGBP','tf':'1D','ma':100,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'short'},
    {'name':'EURGBP H4 L','pair':'EURGBP','tf':'4h','ma':200,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-5.0,'dir':'long'},
    {'name':'EURGBP H4 S','pair':'EURGBP','tf':'4h','ma':200,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-5.0,'dir':'short'},
    {'name':'EURGBP H1 L','pair':'EURGBP','tf':'1h','ma':500,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-3.5,'dir':'long'},
    {'name':'EURGBP M15 L','pair':'EURGBP','tf':'15min','ma':2400,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-4.0,'dir':'long'},
    # EURCHF
    {'name':'EURCHF D1 S','pair':'EURCHF','tf':'1D','ma':100,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-5.0,'dir':'short'},
    {'name':'EURCHF H4 S','pair':'EURCHF','tf':'4h','ma':150,'levels':[-1.5,-2.5,-3.5,-4.0],'stop':-6.0,'dir':'short'},
    # GBPCHF
    {'name':'GBPCHF H4 S','pair':'GBPCHF','tf':'4h','ma':200,'levels':[-1.0,-2.0,-2.5,-3.0],'stop':-4.0,'dir':'short'},
    {'name':'GBPCHF D1 S','pair':'GBPCHF','tf':'1D','ma':50,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'short'},
    {'name':'GBPCHF H1 S','pair':'GBPCHF','tf':'1h','ma':1000,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'short'},
    # AUDCAD × 5 BOTH
    {'name':'AUDCAD H4 L','pair':'AUDCAD','tf':'4h','ma':50,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'long'},
    {'name':'AUDCAD H4 L2','pair':'AUDCAD','tf':'4h','ma':200,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-4.0,'dir':'long'},
    {'name':'AUDCAD H1 S','pair':'AUDCAD','tf':'1h','ma':100,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-5.0,'dir':'short'},
    {'name':'AUDCAD H1 L','pair':'AUDCAD','tf':'1h','ma':150,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-4.0,'dir':'long'},
    {'name':'AUDCAD M15 L','pair':'AUDCAD','tf':'15min','ma':200,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-5.0,'dir':'long'},
    # USDCAD
    {'name':'USDCAD H4 L','pair':'USDCAD','tf':'4h','ma':200,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-3.0,'dir':'long'},
    {'name':'USDCAD H4 S','pair':'USDCAD','tf':'4h','ma':300,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-5.0,'dir':'short'},
    # USDCHF
    {'name':'USDCHF H4 S','pair':'USDCHF','tf':'4h','ma':1000,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-3.0,'dir':'short'},
    # GBPNZD — NEW REI
    {'name':'GBPNZD H1 L','pair':'GBPNZD','tf':'1h','ma':100,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-5.0,'dir':'long'},
    {'name':'GBPNZD H4 L','pair':'GBPNZD','tf':'4h','ma':50,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-5.0,'dir':'long'},
    {'name':'GBPNZD H1 S','pair':'GBPNZD','tf':'1h','ma':100,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-5.0,'dir':'short'},
    # EURNZD — NEW
    {'name':'EURNZD H4 L','pair':'EURNZD','tf':'4h','ma':50,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'long'},
    # EURAUD H1
    {'name':'EURAUD H1 L','pair':'EURAUD','tf':'1h','ma':100,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-5.0,'dir':'long'},
    # GBPAUD H4
    {'name':'GBPAUD H4 L','pair':'GBPAUD','tf':'4h','ma':200,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-3.0,'dir':'long'},
    # AUDUSD H1 SHORT
    {'name':'AUDUSD H1 S','pair':'AUDUSD','tf':'1h','ma':500,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-3.0,'dir':'short'},
    # NZDUSD D1 SHORT
    {'name':'NZDUSD D1 S','pair':'NZDUSD','tf':'1D','ma':50,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-3.0,'dir':'short'},
    # EURCAD H4 SHORT
    {'name':'EURCAD H4 S','pair':'EURCAD','tf':'4h','ma':200,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-5.0,'dir':'short'},
]

print(f"Running V3 MEGA portfolio ({len(STRATEGIES)} strategies)...", flush=True)

def run_simulation(lot):
    print(f"\nLot {lot}...", flush=True)
    strategy_states = {}
    for s in STRATEGIES:
        df = pd.read_csv(PAIR_FILES[s['pair']], index_col=0, parse_dates=True)
        df.index = pd.to_datetime(df.index, utc=True)
        df.columns = [c.lower() for c in df.columns]
        df_tf = aggregate(df, s['tf'])
        arrs = precompute(df_tf, s['ma'])
        states = simulate_with_state(arrs, s['dir'], s['levels'], s['stop'],
                                       PAIRS_INFO[s['pair']]['cost'], PAIRS_INFO[s['pair']]['pip'], lot)
        strategy_states[s['name']] = states
        net = states['realized'].iloc[-1]

    all_ts = pd.DatetimeIndex([])
    for sn, st in strategy_states.items():
        all_ts = all_ts.union(st.index)
    all_ts = all_ts.sort_values()
    unified_realized = pd.DataFrame(index=all_ts)
    unified_unrealized = pd.DataFrame(index=all_ts)
    unified_pos = pd.DataFrame(index=all_ts)
    for sn, st in strategy_states.items():
        unified_realized[sn] = st['realized'].reindex(all_ts, method='ffill').fillna(0)
        unified_unrealized[sn] = st['unrealized'].reindex(all_ts, method='ffill').fillna(0)
        unified_pos[sn] = st['pos_units'].reindex(all_ts, method='ffill').fillna(0)

    total_realized = unified_realized.sum(axis=1)
    total_unrealized = unified_unrealized.sum(axis=1)
    total_equity = CAPITAL_INICIAL + total_realized + total_unrealized
    total_positions = unified_pos.sum(axis=1)

    peak = total_equity.expanding().max()
    dd_pct = (peak - total_equity) / peak * 100
    max_dd_pct = dd_pct.max()
    max_dd_dollar = (peak - total_equity).max()

    final_realized = total_realized.iloc[-1]
    total_return = final_realized / CAPITAL_INICIAL * 100
    span_y = (all_ts[-1] - all_ts[0]).days / 365
    annual = total_return / span_y

    min_equity = total_equity.min()
    loss_init_pct = max(0, CAPITAL_INICIAL - min_equity) / CAPITAL_INICIAL * 100

    return {
        'lot':lot,'annual':annual,'max_dd_pct':max_dd_pct,'max_dd_dollar':max_dd_dollar,
        'loss_init_pct':loss_init_pct,'final':final_realized,
        'calmar':annual/max_dd_pct if max_dd_pct>0 else 0,
        'max_pos':int(total_positions.max()),
        'total_realized':total_realized,
    }

results = []
for lot in [0.05, 0.10, 0.15, 0.20]:
    r = run_simulation(lot)
    results.append(r)

print()
print("="*120)
print(f"PORTFOLIO V3 MEGA — {len(STRATEGIES)} strategies on {len(set(s['pair'] for s in STRATEGIES))} pairs")
print("="*120)
print(f"  {'Lot':<6} {'Annual':>9} {'DD%':>7} {'Loss init%':>11} {'Calmar':>7} {'Max Pos':>8} {'Pitjor cas':>12}")
for r in results:
    print(f"  {r['lot']:<6} +{r['annual']:>5.2f}% {r['max_dd_pct']:>5.2f}% {r['loss_init_pct']:>9.2f}% {r['calmar']:>6.2f} {r['max_pos']:>5} ${r['max_dd_dollar']:+.0f}")

# Per any best
best = max(results, key=lambda x: x['calmar'])
print(f"\nMillor Calmar: lot {best['lot']}")
yearly = best['total_realized'].groupby(pd.to_datetime(best['total_realized'].index).year).last()
yd = yearly.diff().fillna(yearly.iloc[0])
print(f"\nPer any (lot {best['lot']}):")
for yr in yd.index:
    p = yd.loc[yr]
    pct = p / CAPITAL_INICIAL * 100
    print(f"  {yr}: ${p:>+9,.0f} ({pct:+.2f}%)")

# TG
msg = f"🏆 <b>V3 MEGA PORTFOLIO — {len(STRATEGIES)} estrategies</b>%0A%0A"
for r in results:
    msg += f"Lot {r['lot']}: +{r['annual']:.1f}%/any DD {r['max_dd_pct']:.1f}% Calmar {r['calmar']:.2f}%0A"
tg_send(msg)
print("\nDONE")
