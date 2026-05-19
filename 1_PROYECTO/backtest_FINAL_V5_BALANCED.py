"""
PORTFOLIO V5 BALANCED — DIVERSIFICACIO REAL
============================================
Max 3 estratègies per pair. Risc per moneda equilibrat.

13 pairs, ~26 estrategies, exposició màx per moneda < 25%.
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
    'NZDCAD':{'cost':0.40,'pip':1000},
    'AUDNZD':{'cost':0.40,'pip':1000},
    'EURNZD':{'cost':0.50,'pip':1000},
    'GBPNZD':{'cost':0.50,'pip':1000},
    'EURAUD':{'cost':0.40,'pip':1000},
    'EURCAD':{'cost':0.40,'pip':1000},
    'AUDUSD':{'cost':0.30,'pip':1000},
    'NZDUSD':{'cost':0.40,'pip':1000},
    'GBPAUD':{'cost':0.50,'pip':1000},
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

# V5 BALANCED — Max 3 per pair, max 25% per moneda
STRATEGIES = [
    # EURGBP × 3 (intra-Europa, baix risc concentració)
    {'name':'EURGBP D1 L','pair':'EURGBP','tf':'1D','ma':30,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'long'},
    {'name':'EURGBP D1 S','pair':'EURGBP','tf':'1D','ma':30,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'short'},
    {'name':'EURGBP H4 BOTH-L','pair':'EURGBP','tf':'4h','ma':200,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-5.0,'dir':'long'},

    # GBPCHF × 3 (CHF strength theme)
    {'name':'GBPCHF D1 S','pair':'GBPCHF','tf':'1D','ma':50,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'short'},
    {'name':'GBPCHF H4 S','pair':'GBPCHF','tf':'4h','ma':150,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'short'},
    {'name':'GBPCHF H1 S','pair':'GBPCHF','tf':'1h','ma':800,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'short'},

    # AUDCAD × 3 (reduït de 7)
    {'name':'AUDCAD M30 L','pair':'AUDCAD','tf':'30min','ma':200,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-6.0,'dir':'long'},
    {'name':'AUDCAD M30 S','pair':'AUDCAD','tf':'30min','ma':200,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-6.0,'dir':'short'},
    {'name':'AUDCAD H4 L','pair':'AUDCAD','tf':'4h','ma':200,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-4.0,'dir':'long'},

    # USDCAD × 2
    {'name':'USDCAD H1 S','pair':'USDCAD','tf':'1h','ma':1200,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-5.0,'dir':'short'},
    {'name':'USDCAD H4 L','pair':'USDCAD','tf':'4h','ma':200,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-3.0,'dir':'long'},

    # NZDCAD × 2 (BOTH dirs)
    {'name':'NZDCAD D1 L','pair':'NZDCAD','tf':'1D','ma':50,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-3.5,'dir':'long'},
    {'name':'NZDCAD D1 S','pair':'NZDCAD','tf':'1D','ma':50,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-3.5,'dir':'short'},

    # USDCHF × 1
    {'name':'USDCHF H4 S','pair':'USDCHF','tf':'4h','ma':800,'levels':[-1.5,-2.0,-2.5,-3.0],'stop':-3.5,'dir':'short'},

    # EURCHF × 2
    {'name':'EURCHF D1 S','pair':'EURCHF','tf':'1D','ma':100,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-5.0,'dir':'short'},
    {'name':'EURCHF H4 S','pair':'EURCHF','tf':'4h','ma':150,'levels':[-1.5,-2.5,-3.5,-4.0],'stop':-6.0,'dir':'short'},

    # AUDNZD × 2 BOTH
    {'name':'AUDNZD D1 L','pair':'AUDNZD','tf':'1D','ma':75,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-5.0,'dir':'long'},
    {'name':'AUDNZD D1 S','pair':'AUDNZD','tf':'1D','ma':75,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-5.0,'dir':'short'},

    # NEW pairs afegits per diversificar
    # EURNZD × 1
    {'name':'EURNZD H4 L','pair':'EURNZD','tf':'4h','ma':50,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'long'},
    # GBPNZD × 2
    {'name':'GBPNZD H1 L','pair':'GBPNZD','tf':'1h','ma':100,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-5.0,'dir':'long'},
    {'name':'GBPNZD H4 L','pair':'GBPNZD','tf':'4h','ma':50,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-5.0,'dir':'long'},
    # EURAUD × 1
    {'name':'EURAUD H1 L','pair':'EURAUD','tf':'1h','ma':100,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-5.0,'dir':'long'},
    # EURCAD × 1
    {'name':'EURCAD H4 S','pair':'EURCAD','tf':'4h','ma':200,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-5.0,'dir':'short'},
    # AUDUSD × 1
    {'name':'AUDUSD H1 S','pair':'AUDUSD','tf':'1h','ma':500,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-3.0,'dir':'short'},
    # NZDUSD × 1
    {'name':'NZDUSD D1 S','pair':'NZDUSD','tf':'1D','ma':50,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-3.0,'dir':'short'},
    # GBPAUD × 1
    {'name':'GBPAUD H4 L','pair':'GBPAUD','tf':'4h','ma':200,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-3.0,'dir':'long'},
]

print(f"V5 BALANCED — {len(STRATEGIES)} estrategies sobre {len(set(s['pair'] for s in STRATEGIES))} pairs", flush=True)

# Calculate currency exposure
from collections import Counter
ccy_count = Counter()
for s in STRATEGIES:
    pair = s['pair']
    base, quote = pair[:3], pair[3:]
    ccy_count[base] += 1
    ccy_count[quote] += 1

print("\nExposició per moneda:")
for ccy, n in sorted(ccy_count.items(), key=lambda x:-x[1]):
    pct = n / (len(STRATEGIES)*2) * 100
    print(f"  {ccy}: {n} ({pct:.1f}%)")

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
print(f"V5 BALANCED — {len(STRATEGIES)} strats, {len(set(s['pair'] for s in STRATEGIES))} pairs")
print("="*120)
print(f"  {'Lot':<6} {'Annual':>9} {'DD%':>7} {'Loss init%':>11} {'Calmar':>7} {'Max Pos':>8}")
for r in results:
    print(f"  {r['lot']:<6} +{r['annual']:>5.2f}% {r['max_dd_pct']:>5.2f}% {r['loss_init_pct']:>9.2f}% {r['calmar']:>6.2f} {r['max_pos']:>5}")

best = max(results, key=lambda x: x['calmar'])
yearly = best['total_realized'].groupby(pd.to_datetime(best['total_realized'].index).year).last()
yd = yearly.diff().fillna(yearly.iloc[0])
print(f"\nPer any (lot {best['lot']}):")
for yr in yd.index:
    p = yd.loc[yr]
    pct = p / CAPITAL_INICIAL * 100
    print(f"  {yr}: ${p:>+9,.0f} ({pct:+.2f}%)")

# TG
ccy_msg = "%0AExposicio per moneda:%0A"
for ccy, n in sorted(ccy_count.items(), key=lambda x:-x[1])[:6]:
    pct = n / (len(STRATEGIES)*2) * 100
    ccy_msg += f"{ccy}: {pct:.1f}%%0A"

msg = f"📊 <b>V5 BALANCED — {len(STRATEGIES)} strats, {len(set(s['pair'] for s in STRATEGIES))} pairs</b>%0A{ccy_msg}%0A"
for r in results:
    msg += f"Lot {r['lot']}: <b>+{r['annual']:.1f}%</b>/any DD <b>{r['max_dd_pct']:.1f}%</b> Calmar {r['calmar']:.2f}%0A"
tg_send(msg)

print("\nDONE")
