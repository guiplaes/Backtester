"""
SIMULACIO REAL-TIME del PORTFOLIO
==================================
Track per cada bar M5 (timeline base):
- Quines estratègies tenen posicions obertes
- Unrealized PnL (mark-to-market) de cada posició
- Equity = capital + realized + unrealized

Així trobem el TRUE max DD (incloent moments amb posicions perdent).

Després calculem leverage òptim segons risk tolerance real.
"""
import pandas as pd
import numpy as np

CAPITAL_INICIAL = 63000.0
LOT_PER_ENTRY = 0.05

PIP_MUL = {'EURGBP':1000,'EURCHF':1000,'AUDNZD':1000,'USDCHF':1000}
COSTS = {'EURGBP':0.30,'EURCHF':0.30,'AUDNZD':0.40,'USDCHF':0.30}
PAIR_FILES = {
    'EURGBP': 'eurgbp_dk_m5_5y.csv','EURCHF': 'eurchf_dk_m5_5y.csv',
    'USDCHF': 'usdchf_dk_m5_5y.csv',
}

def aggregate(df_, rule):
    return df_.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last')).dropna()

def precompute(df_, ma_p):
    return {
        'O':df_['open'].values,'H':df_['high'].values,
        'L':df_['low'].values,'C':df_['close'].values,
        'SMA':df_['close'].rolling(ma_p).mean().values,
        'STD':df_['close'].rolling(ma_p).std().values,
        'TS':df_.index, 'n':len(df_), 'df_index':df_.index
    }

def simulate_strategy_with_state(arrs, direction, levels, stop_z, cost, pip_mul, lot):
    """
    Retorna per cada bar de la TF d'aquesta estrategia:
    {ts: timestamp, realized_pnl_to_date, unrealized_pnl_now, position_size}
    """
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    SMA=arrs['SMA'];STD=arrs['STD']
    n=arrs['n']

    # Per cada bar: estat realized + unrealized
    states = []
    realized = 0.0
    pos = None  # entries list of (idx, price, units), hit set

    for i in range(50, n):
        sma=SMA[i];std=STD[i];c=C[i]
        if np.isnan(sma) or np.isnan(std) or std<=0:
            states.append({'ts':arrs['TS'][i],'realized':realized,'unrealized':0.0,'pos_units':0})
            continue
        z=(c-sma)/std

        # Manage open position
        unrealized = 0.0
        pos_units = 0
        if pos is not None:
            # Compute current unrealized
            sgn = 1 if direction=='long' else -1
            for eidx, ep in pos['entries']:
                unrealized += (c - ep) * sgn * pip_mul * (lot/0.01)
            pos_units = len(pos['entries'])

            if direction=='long':
                stop_hit=z<=stop_z;target=c>=sma
            else:
                stop_hit=z>=-stop_z;target=c<=sma
            time_out=(i-pos['entries'][0][0])>=500
            if stop_hit or target or time_out:
                # Close all
                exit_p=c
                tot_u=len(pos['entries'])
                pnl_price=0
                for eidx,ep in pos['entries']: pnl_price+=(exit_p-ep)*sgn
                pnl_d = pnl_price * pip_mul * (lot/0.01) - tot_u*cost*(lot/0.01)
                realized += pnl_d
                unrealized = 0
                pos_units = 0
                pos = None
            else:
                # Add to position
                if direction=='long':
                    for lvl in levels:
                        if lvl not in pos['hit'] and z<=lvl:
                            pos['entries'].append((i,c));pos['hit'].add(lvl);break
                else:
                    for lvl in levels:
                        if lvl not in pos['hit'] and z>=-lvl:
                            pos['entries'].append((i,c));pos['hit'].add(lvl);break

        # Check entry
        if pos is None:
            f=levels[0]
            if direction=='long' and z<=f:
                pos={'entries':[(i,c)],'hit':{f}}
            elif direction=='short' and z>=-f:
                pos={'entries':[(i,c)],'hit':{f}}

        # Recompute unrealized after potential add
        if pos is not None:
            sgn = 1 if direction=='long' else -1
            unrealized = 0
            for eidx, ep in pos['entries']:
                unrealized += (c - ep) * sgn * pip_mul * (lot/0.01)
            pos_units = len(pos['entries'])

        states.append({'ts':arrs['TS'][i],'realized':realized,
                       'unrealized':unrealized,'pos_units':pos_units})

    return pd.DataFrame(states).set_index('ts')

# Stable pairs setup
STRATEGIES = [
    {'name':'EURGBP D1','pair':'EURGBP','tf':'1D','ma':100,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'long'},
    {'name':'EURGBP D1 short','pair':'EURGBP','tf':'1D','ma':100,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'short'},
    {'name':'EURGBP H4','pair':'EURGBP','tf':'4h','ma':300,'levels':[-1.0,-1.5,-2.5,-3.0],'stop':-5.0,'dir':'long'},
    {'name':'EURGBP H4 short','pair':'EURGBP','tf':'4h','ma':300,'levels':[-1.0,-1.5,-2.5,-3.0],'stop':-5.0,'dir':'short'},
    {'name':'EURGBP H1','pair':'EURGBP','tf':'1h','ma':500,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-3.5,'dir':'long'},
    {'name':'EURGBP H1 short','pair':'EURGBP','tf':'1h','ma':500,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-3.5,'dir':'short'},
    {'name':'EURGBP M15','pair':'EURGBP','tf':'15min','ma':2400,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-4.0,'dir':'long'},
    {'name':'EURGBP M15 short','pair':'EURGBP','tf':'15min','ma':2400,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-4.0,'dir':'short'},
    {'name':'EURCHF D1','pair':'EURCHF','tf':'1D','ma':100,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-5.0,'dir':'long'},
    {'name':'EURCHF D1 short','pair':'EURCHF','tf':'1D','ma':100,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-5.0,'dir':'short'},
    {'name':'EURCHF H4','pair':'EURCHF','tf':'4h','ma':150,'levels':[-1.5,-2.5,-3.5,-4.0],'stop':-6.0,'dir':'long'},
    {'name':'EURCHF H4 short','pair':'EURCHF','tf':'4h','ma':150,'levels':[-1.5,-2.5,-3.5,-4.0],'stop':-6.0,'dir':'short'},
    {'name':'USDCHF H4','pair':'USDCHF','tf':'4h','ma':500,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-3.5,'dir':'long'},
    {'name':'USDCHF H4 short','pair':'USDCHF','tf':'4h','ma':500,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-3.5,'dir':'short'},
]

print("Simulant cada estratègia amb track per bar...", flush=True)

# Run each strategy and get per-bar state
strategy_states = {}
for s in STRATEGIES:
    print(f"  {s['name']} ({s['dir']})...", flush=True)
    df = pd.read_csv(PAIR_FILES[s['pair']], index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]
    df_tf = aggregate(df, s['tf'])
    arrs = precompute(df_tf, s['ma'])
    states = simulate_strategy_with_state(arrs, s['dir'], s['levels'], s['stop'],
                                           COSTS[s['pair']], PIP_MUL[s['pair']], LOT_PER_ENTRY)
    strategy_states[s['name']] = states

# Build unified timeline (use H1 as base — fine resolution + manageable size)
print("\nBuilding unified timeline (H1)...", flush=True)
# Use the union of all timestamps, then resample to H1
all_ts = pd.DatetimeIndex([])
for sn, st in strategy_states.items():
    all_ts = all_ts.union(st.index)
all_ts = all_ts.sort_values()

# Resample each strategy state to H1 (forward fill realized, last unrealized)
unified_realized = pd.DataFrame(index=all_ts)
unified_unrealized = pd.DataFrame(index=all_ts)
unified_pos = pd.DataFrame(index=all_ts)
for sn, st in strategy_states.items():
    # Reindex to all_ts, ffill realized (cumulative), keep unrealized as-is (NaN→0)
    reindexed_realized = st['realized'].reindex(all_ts, method='ffill').fillna(0)
    reindexed_unrealized = st['unrealized'].reindex(all_ts, method='ffill').fillna(0)
    reindexed_pos = st['pos_units'].reindex(all_ts, method='ffill').fillna(0)
    unified_realized[sn] = reindexed_realized
    unified_unrealized[sn] = reindexed_unrealized
    unified_pos[sn] = reindexed_pos

# Compute total per timestamp
total_realized = unified_realized.sum(axis=1)
total_unrealized = unified_unrealized.sum(axis=1)
total_equity = CAPITAL_INICIAL + total_realized + total_unrealized
total_positions = unified_pos.sum(axis=1)

# Compute real-time DD on equity (includes unrealized!)
peak_equity = total_equity.expanding().max()
dd_dollar = peak_equity - total_equity
dd_pct = (peak_equity - total_equity) / peak_equity * 100

max_dd_dollar = dd_dollar.max()
max_dd_pct_real = dd_pct.max()

# Final realized PnL
final_realized = total_realized.iloc[-1]
total_return = final_realized / CAPITAL_INICIAL * 100
span_y = (all_ts[-1] - all_ts[0]).days / 365
annual = total_return / span_y

print()
print("="*120)
print("PORTFOLIO REAL-TIME SIMULATION (mark-to-market)")
print("="*120)
print(f"  Capital inicial:    ${CAPITAL_INICIAL:,.2f}")
print(f"  Span:               {span_y:.1f} anys")
print(f"  Final realized P&L: ${final_realized:+,.2f}")
print(f"  Final equity:       ${CAPITAL_INICIAL + final_realized:,.2f}")
print(f"  Total return:       {total_return:+.2f}%")
print(f"  Annual return:      {annual:+.2f}%/any")
print()
print(f"  *** MAX DD REAL (mark-to-market):")
print(f"  Max DD $:           ${max_dd_dollar:+,.2f}")
print(f"  Max DD %:           {max_dd_pct_real:.2f}%")
print(f"  Calmar:             {annual/max_dd_pct_real:.2f}" if max_dd_pct_real>0 else "")
print()
# Position concentration analysis
max_simultaneous = total_positions.max()
avg_simultaneous = total_positions[total_positions>0].mean()
pct_time_with_pos = (total_positions>0).sum() / len(total_positions) * 100
print(f"  Max posicions simultanies: {int(max_simultaneous)} unitats")
print(f"  Avg posicions quan tens:    {avg_simultaneous:.1f} unitats")
print(f"  Temps amb posicions:        {pct_time_with_pos:.0f}%")

# DD info
worst_dd_idx = dd_pct.idxmax()
worst_dd_value_pct = dd_pct.max()
print(f"\n  Pitjor DD instant: {worst_dd_idx} ({worst_dd_value_pct:.2f}%)")

# Per any
print()
print("="*120)
print("PER ANY (realized only):")
print("="*120)
total_realized.index = pd.to_datetime(total_realized.index)
yearly_realized = total_realized.groupby(total_realized.index.year).last()
yearly_pnl_diff = yearly_realized.diff().fillna(yearly_realized.iloc[0])
for yr in yearly_pnl_diff.index:
    p = yearly_pnl_diff.loc[yr]
    pct = p / CAPITAL_INICIAL * 100
    print(f"  {yr}: ${p:>+9,.0f} ({pct:>+6.2f}%)")

# ===== EXTRA: comparativa DD vs LOSS FROM INITIAL =====
print()
print("="*120)
print("DD vs PERDUA SOBRE INICIAL (analisi crucial):")
print("="*120)
# Min equity ever
min_equity = total_equity.min()
min_equity_ts = total_equity.idxmin()
loss_from_initial = CAPITAL_INICIAL - min_equity
loss_from_initial_pct = loss_from_initial / CAPITAL_INICIAL * 100

print(f"  Equity inicial:           ${CAPITAL_INICIAL:,.2f}")
print(f"  Equity MINIM (mai baix):  ${min_equity:,.2f}  @ {min_equity_ts}")
print(f"  Pèrdua sobre inicial:     ${loss_from_initial:+,.2f} ({loss_from_initial_pct:+.2f}%)")
print()
print(f"  Comparació:")
print(f"  - Max DD peak-to-trough:  {max_dd_pct_real:.2f}%  (caiguda des del max)")
if loss_from_initial > 0:
    print(f"  - Max LOSS from initial:  {loss_from_initial_pct:.2f}%  (com de baix vam anar sota inicial)")
else:
    print(f"  - Mai vam anar SOTA inicial! (capital sempre >= $63k)")

# Also: did we ever go below initial?
went_below = total_equity < CAPITAL_INICIAL
n_below = went_below.sum()
print(f"\n  Bars under inicial:       {n_below} ({n_below/len(total_equity)*100:.1f}% del temps)")
if n_below > 0:
    first_below = total_equity[went_below].index[0]
    last_below = total_equity[went_below].index[-1]
    print(f"  Primer cop sota inicial: {first_below}")
    print(f"  Últim cop sota inicial:  {last_below}")

# Worst DD periods (top 5)
print()
print("="*120)
print("PERIODES PITJORS (top 5):")
print("="*120)
worst_5 = dd_pct.nlargest(20).head(20)
seen_dates = set()
shown = 0
for ts, ddp in worst_5.items():
    d = ts.date()
    if d in seen_dates: continue
    seen_dates.add(d)
    eq_at = total_equity.loc[ts]
    print(f"  {ts}: DD {ddp:.2f}%, Equity ${eq_at:,.0f}, Pos units: {int(total_positions.loc[ts])}")
    shown += 1
    if shown >= 5: break

# LEVERAGE ANALYSIS
print()
print("="*120)
print("ANALISI APALANCAMENT (escalat sobre lots base 0.05):")
print("="*120)
print(f"  {'Lot per entry':<18} {'Annual':>9} {'Max DD%':>9} {'Pitjor cas':>12} {'Calmar':>7}")
for lot_mult in [0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 20.0]:
    scaled_annual = annual * lot_mult
    scaled_dd = max_dd_pct_real * lot_mult
    scaled_calmar = scaled_annual/scaled_dd if scaled_dd > 0 else 0
    lot_real = LOT_PER_ENTRY * lot_mult
    safe = "✓" if scaled_dd < 30 else ("⚠" if scaled_dd < 50 else "✗")
    print(f"  {lot_real:.3f} lot {safe:>3}    {scaled_annual:>+7.2f}%    {scaled_dd:>5.2f}%   ${max_dd_dollar*lot_mult:>+9,.0f}     {scaled_calmar:>5.2f}")

print("\nDONE")
