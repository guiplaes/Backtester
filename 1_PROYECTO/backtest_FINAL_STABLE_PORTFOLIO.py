"""
PORTFOLIO FINAL — NOMES PAIRS ESTABLES (sense JPY trending)
============================================================
Configs validades 5+ anys positius. Mark-to-market real.
Test diferents lots per trobar sweet spot risk/reward.

PAIRS INCLOSOS:
- EURGBP (Hurst 0.32 — REI mean-rev)
- EURCHF (D1 robust)
- GBPCHF (SHORT robust)
- AUDCAD (NEW — Hurst MR a tots TFs!)
- USDCAD (H4 sòlid)
- AUDNZD (rangy cross)
- USDCHF (SHORT-only)

PAIRS EXCLOSOS:
- EURJPY/CHFJPY (trending, regime risk)
- USDJPY/GBPJPY (trending)
- XAUUSD (bull bias)
"""
import pandas as pd
import numpy as np
import sys
sys.path.insert(0, '.')
try:
    from tg_send import send as tg_send
except:
    tg_send = lambda x: None

CAPITAL_INICIAL = 63000.0

PIP_MUL = {'EURGBP':1000,'EURCHF':1000,'GBPCHF':1000,'AUDCAD':1000,
           'USDCAD':1000,'AUDNZD':1000,'USDCHF':1000,'NZDCAD':1000}
COSTS = {'EURGBP':0.30,'EURCHF':0.30,'GBPCHF':0.40,'AUDCAD':0.40,
         'USDCAD':0.30,'AUDNZD':0.40,'USDCHF':0.30,'NZDCAD':0.40}
PAIR_FILES = {p: f"{p.lower()}_dk_m5_5y.csv" for p in PIP_MUL}

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
    """Real-time mark-to-market simulation."""
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

# Strategies — pairs ESTABLES NOMÉS, BOTH directions
STRATEGIES = [
    # EURGBP — multiTF BOTH
    {'name':'EURGBP D1 L','pair':'EURGBP','tf':'1D','ma':100,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'long'},
    {'name':'EURGBP D1 S','pair':'EURGBP','tf':'1D','ma':100,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'short'},
    {'name':'EURGBP H4 L','pair':'EURGBP','tf':'4h','ma':300,'levels':[-1.0,-1.5,-2.5,-3.0],'stop':-5.0,'dir':'long'},
    {'name':'EURGBP H4 S','pair':'EURGBP','tf':'4h','ma':300,'levels':[-1.0,-1.5,-2.5,-3.0],'stop':-5.0,'dir':'short'},
    {'name':'EURGBP H1 L','pair':'EURGBP','tf':'1h','ma':500,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-3.5,'dir':'long'},
    {'name':'EURGBP M15 L','pair':'EURGBP','tf':'15min','ma':2400,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-4.0,'dir':'long'},
    # EURCHF — D1 + H4 BOTH
    {'name':'EURCHF D1 L','pair':'EURCHF','tf':'1D','ma':100,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-5.0,'dir':'long'},
    {'name':'EURCHF D1 S','pair':'EURCHF','tf':'1D','ma':100,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-5.0,'dir':'short'},
    {'name':'EURCHF H4 S','pair':'EURCHF','tf':'4h','ma':150,'levels':[-1.5,-2.5,-3.5,-4.0],'stop':-6.0,'dir':'short'},
    # GBPCHF SHORT (validat PF 13 D1)
    {'name':'GBPCHF H4 S','pair':'GBPCHF','tf':'4h','ma':200,'levels':[-1.0,-2.0,-2.5,-3.0],'stop':-4.0,'dir':'short'},
    {'name':'GBPCHF D1 S','pair':'GBPCHF','tf':'1D','ma':300,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-4.0,'dir':'short'},
    # AUDCAD — NEW! BOTH dirs
    {'name':'AUDCAD H4 L','pair':'AUDCAD','tf':'4h','ma':50,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'long'},
    {'name':'AUDCAD H4 L2','pair':'AUDCAD','tf':'4h','ma':200,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-4.0,'dir':'long'},
    {'name':'AUDCAD H1 S','pair':'AUDCAD','tf':'1h','ma':100,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-5.0,'dir':'short'},
    {'name':'AUDCAD H1 L','pair':'AUDCAD','tf':'1h','ma':150,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-4.0,'dir':'long'},
    {'name':'AUDCAD M15 L','pair':'AUDCAD','tf':'15min','ma':200,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-5.0,'dir':'long'},
    # USDCAD H4
    {'name':'USDCAD H4 L','pair':'USDCAD','tf':'4h','ma':200,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-3.0,'dir':'long'},
    {'name':'USDCAD H4 S','pair':'USDCAD','tf':'4h','ma':300,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-5.0,'dir':'short'},
    # USDCHF SHORT
    {'name':'USDCHF H4 S','pair':'USDCHF','tf':'4h','ma':500,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-3.5,'dir':'short'},
]

def run_simulation(lot):
    print(f"\nRunning portfolio with lot {lot}...", flush=True)
    strategy_states = {}
    for s in STRATEGIES:
        df = pd.read_csv(PAIR_FILES[s['pair']], index_col=0, parse_dates=True)
        df.index = pd.to_datetime(df.index, utc=True)
        df.columns = [c.lower() for c in df.columns]
        df_tf = aggregate(df, s['tf'])
        arrs = precompute(df_tf, s['ma'])
        states = simulate_with_state(arrs, s['dir'], s['levels'], s['stop'],
                                       COSTS[s['pair']], PIP_MUL[s['pair']], lot)
        strategy_states[s['name']] = states
        net = states['realized'].iloc[-1]
        print(f"  {s['name']:<14} Net=${net:+,.0f}", flush=True)

    # Build unified
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
    loss_init = max(0, CAPITAL_INICIAL - min_equity)
    loss_init_pct = loss_init / CAPITAL_INICIAL * 100

    return {
        'lot':lot,'annual':annual,'max_dd_pct':max_dd_pct,'max_dd_dollar':max_dd_dollar,
        'loss_init_pct':loss_init_pct,'final':final_realized,
        'calmar':annual/max_dd_pct if max_dd_pct>0 else 0,
        'max_pos':int(total_positions.max()),
        'total_realized':total_realized,
    }

# Test multiple lots
results = []
for lot in [0.05, 0.10, 0.15, 0.20, 0.30]:
    r = run_simulation(lot)
    results.append(r)

print()
print("="*120)
print("PORTFOLIO FINAL — NOMÉS ESTABLES")
print("="*120)
print(f"  {'Lot':<6} {'Annual':>9} {'DD%':>7} {'Loss init%':>11} {'Calmar':>7} {'Max Pos':>8} {'Pitjor cas':>12}")
for r in results:
    print(f"  {r['lot']:<6} +{r['annual']:>5.2f}% {r['max_dd_pct']:>5.2f}% {r['loss_init_pct']:>9.2f}% {r['calmar']:>6.2f} {r['max_pos']:>5} ${r['max_dd_dollar']:+.0f}")

# TG send final
msg = "🎯 <b>PORTFOLIO FINAL ESTABLES</b>%0A%0A"
for r in results:
    msg += f"Lot {r['lot']}: +{r['annual']:.1f}%/any DD {r['max_dd_pct']:.1f}% Loss {r['loss_init_pct']:.1f}%%0A"
tg_send(msg)

# Pretty per any analysis amb best lot
best = max(results, key=lambda x: x['calmar'])
print(f"\nMillor Calmar: lot {best['lot']} (Calmar {best['calmar']:.2f})")
yearly = best['total_realized'].groupby(pd.to_datetime(best['total_realized'].index).year).last()
yd = yearly.diff().fillna(yearly.iloc[0])
print(f"\nPer any (lot {best['lot']}):")
for yr in yd.index:
    p = yd.loc[yr]
    pct = p / CAPITAL_INICIAL * 100
    print(f"  {yr}: ${p:>+9,.0f} ({pct:+.2f}%)")

print("\nDONE")
