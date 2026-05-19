"""
PORTFOLIO FINAL DEFINITIU
=========================
Combina totes les millores trobades:
1. Pairs amb edge demostrat: EURGBP, EURCHF, EURJPY, CHFJPY, USDCHF, GBPCHF
2. Multi-TF per cada pair (multi-cycle exposure)
3. SESSION filter (skip ASIA + DEAD)
4. Mark-to-market real-time DD calculation
5. Direccionalitat optima per pair (LONG-only or SHORT-only or BOTH)
6. Lot sizing analysis (test diferents nivells)

RESULTAT: el portfolio definitiu per deploy.
"""
import pandas as pd
import numpy as np

CAPITAL_INICIAL = 63000.0

PIP_MUL = {'EURGBP':1000,'EURCHF':1000,'EURJPY':100,'CHFJPY':100,
           'USDCHF':1000,'GBPCHF':1000,'AUDNZD':1000,'XAUUSD':1,'EURUSD':1000}
COSTS = {'EURGBP':0.30,'EURCHF':0.30,'EURJPY':0.30,'CHFJPY':0.40,
         'USDCHF':0.30,'GBPCHF':0.40,'AUDNZD':0.40,'XAUUSD':0.50,'EURUSD':0.30}
PAIR_FILES = {
    'EURGBP':'eurgbp_dk_m5_5y.csv','EURCHF':'eurchf_dk_m5_5y.csv',
    'EURJPY':'eurjpy_dk_m5_5y.csv','CHFJPY':'chfjpy_dk_m5_5y.csv',
    'USDCHF':'usdchf_dk_m5_5y.csv','GBPCHF':'gbpchf_dk_m5_5y.csv',
    'AUDNZD':'audnzd_dk_m5_5y.csv',
}

def aggregate(df_, rule):
    return df_.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last')).dropna()

def get_session(h):
    if h < 7: return 'ASIA'
    elif h < 13: return 'LONDON'
    elif h < 16: return 'OVERLAP'
    elif h < 21: return 'NY'
    else: return 'DEAD'

def precompute(df_, ma_p):
    return {
        'O':df_['open'].values,'H':df_['high'].values,
        'L':df_['low'].values,'C':df_['close'].values,
        'SMA':df_['close'].rolling(ma_p).mean().values,
        'STD':df_['close'].rolling(ma_p).std().values,
        'HOUR':np.array([t.hour for t in df_.index]),
        'TS':df_.index, 'n':len(df_)
    }

def simulate_with_state(arrs, direction, levels, stop_z, cost, pip_mul, lot,
                         use_session_filter=True, is_d1=False):
    """Real-time simulation amb mark-to-market state per bar."""
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    SMA=arrs['SMA'];STD=arrs['STD'];HOUR=arrs['HOUR']
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

        unrealized = 0.0
        pos_units = 0
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

        # Apply session filter for new entries (no apliquem per D1 — bar és 00:00 always)
        entry_ok = True
        if use_session_filter and not is_d1:
            sess = get_session(HOUR[i])
            if sess in ['ASIA','DEAD']: entry_ok = False

        if pos is None and entry_ok:
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

# Strategies — TOTS pairs BOTH directions (PURE mean-rev)
PAIRS_TFS = [
    ('EURGBP', '1D', 100, [-0.5,-1.0,-1.5,-2.0], -4.0),
    ('EURGBP', '4h', 300, [-1.0,-1.5,-2.5,-3.0], -5.0),
    ('EURGBP', '1h', 500, [-0.5,-1.5,-2.5,-3.0], -3.5),
    ('EURGBP', '15min', 2400, [-1.0,-1.5,-2.0,-2.5], -4.0),
    ('EURCHF', '1D', 100, [-0.5,-1.5,-2.5,-3.0], -5.0),
    ('EURCHF', '4h', 150, [-1.5,-2.5,-3.5,-4.0], -6.0),
    ('EURJPY', '1D', 50, [-0.5,-1.5,-2.5,-3.0], -3.5),
    ('EURJPY', '4h', 300, [-0.5,-1.5,-2.5,-3.0], -5.0),
    ('CHFJPY', '1D', 50, [-0.5,-1.5,-2.5,-3.0], -3.5),
    ('CHFJPY', '4h', 300, [-0.5,-1.5,-2.5,-3.0], -4.0),
    ('USDCHF', '4h', 500, [-0.5,-1.5,-2.5,-3.0], -3.5),
    ('GBPCHF', '4h', 200, [-1.0,-2.0,-2.5,-3.0], -4.0),
    ('GBPCHF', '1D', 300, [-1.0,-1.5,-2.0,-2.5], -4.0),
]
# Generate BOTH directions for every pair+TF
STRATEGIES = []
for pair, tf, ma, levels, stop in PAIRS_TFS:
    for d in ['long', 'short']:
        STRATEGIES.append({
            'name':f'{pair} {tf} {d.upper()}','pair':pair,'tf':tf,
            'ma':ma,'levels':levels,'stop':stop,'dir':d
        })

def run_full_simulation(lot_per_entry, use_session=True):
    """Run full portfolio simulation, return key stats."""
    print(f"\n{'='*100}")
    print(f"LOT={lot_per_entry}, SESSION_FILTER={use_session}")
    print(f"{'='*100}")
    strategy_states = {}
    summary = []
    for s in STRATEGIES:
        df = pd.read_csv(PAIR_FILES[s['pair']], index_col=0, parse_dates=True)
        df.index = pd.to_datetime(df.index, utc=True)
        df.columns = [c.lower() for c in df.columns]
        df_tf = aggregate(df, s['tf'])
        arrs = precompute(df_tf, s['ma'])
        is_d1 = s['tf'] == '1D'
        states = simulate_with_state(arrs, s['dir'], s['levels'], s['stop'],
                                       COSTS[s['pair']], PIP_MUL[s['pair']], lot_per_entry,
                                       use_session_filter=use_session, is_d1=is_d1)
        strategy_states[s['name']] = states
        final_realized = states['realized'].iloc[-1]
        summary.append({'name':s['name'],'final_realized':final_realized})
        print(f"  {s['name']:<20} Net: ${final_realized:+,.0f}", flush=True)

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

    # Loss from initial
    min_equity = total_equity.min()
    loss_from_initial = max(0, CAPITAL_INICIAL - min_equity)
    loss_initial_pct = loss_from_initial / CAPITAL_INICIAL * 100
    n_below = (total_equity < CAPITAL_INICIAL).sum()
    pct_below_initial = n_below / len(total_equity) * 100

    # Per any
    yearly = total_realized.groupby(pd.to_datetime(total_realized.index).year).last()
    yearly_diff = yearly.diff().fillna(yearly.iloc[0])

    print()
    print(f"  *** RESULTAT ***")
    print(f"  Annual:           +{annual:.2f}%/any")
    print(f"  Total return:     +{total_return:.2f}% (${final_realized:+,.0f})")
    print(f"  Max DD:           {max_dd_pct:.2f}% (${max_dd_dollar:+,.0f})")
    print(f"  Loss from init:   {loss_initial_pct:.2f}%")
    print(f"  % time under init: {pct_below_initial:.1f}%")
    print(f"  Calmar:           {annual/max_dd_pct:.2f}" if max_dd_pct>0 else "")
    print(f"  Max sim positions: {int(total_positions.max())}")
    print(f"  % time amb pos:   {(total_positions>0).sum()/len(total_positions)*100:.0f}%")
    print()
    print(f"  Per any:")
    for yr in yearly_diff.index:
        p = yearly_diff.loc[yr]
        pct = p / CAPITAL_INICIAL * 100
        print(f"    {yr}: ${p:>+9,.0f} ({pct:>+6.2f}%)")

    return {
        'lot':lot_per_entry,'session':use_session,
        'annual':annual,'max_dd_pct':max_dd_pct,'max_dd_dollar':max_dd_dollar,
        'loss_initial_pct':loss_initial_pct,'pct_below':pct_below_initial,
        'final_realized':final_realized,'total_return':total_return,
        'calmar':annual/max_dd_pct if max_dd_pct>0 else 0,
        'max_pos':int(total_positions.max()),
    }

print("Running FINAL DEFINITIU portfolio comparison (3 lot sizes × session filter)...", flush=True)
all_runs = []

# Test 3 lot sizes amb session filter
for lot in [0.05, 0.10, 0.15]:
    r = run_full_simulation(lot, use_session=True)
    all_runs.append(r)

# Sense session filter (per comparar)
print(f"\n{'='*100}\nSENSE SESSION FILTER (lot 0.10)\n{'='*100}")
r = run_full_simulation(0.10, use_session=False)
r['_label'] = 'no_session'
all_runs.append(r)

# Final summary
print()
print("="*135)
print("RESUM FINAL — quina configuracio adoptem:")
print("="*135)
print(f"  {'Lot':<6} {'Session':<8} {'Annual':>9} {'DD%':>7} {'Loss init%':>11} {'Calmar':>7} {'Max Pos':>8}")
for r in all_runs:
    sess = 'ON' if r['session'] else 'OFF'
    print(f"  {r['lot']:<6} {sess:<8} +{r['annual']:>5.2f}%  {r['max_dd_pct']:>5.2f}%  {r['loss_initial_pct']:>9.2f}%  {r['calmar']:>5.2f}  {r['max_pos']:>5}")

print("\nDONE")
