"""
PORTFOLIO STABLE-ONLY MULTI-TF
==============================
Filosofia: només pairs HISTÒRICAMENT estables (range-bound), tots BOTH directions.
Si una direcció no funciona en un règim, l'altra sí. Mean-reversion pura.

Pairs triats per estabilitat històrica:
- EURGBP: range 0.70-0.95 over 25y (king)
- EURCHF: range 0.94-1.20 post-2015
- AUDNZD: range 1.00-1.30 over 20y+
- USDCHF: somewhat range (less stable, optional)

Multi-TF: D1, H4, H1, M15 — diferents cycles.
"""
import pandas as pd
import numpy as np

CAPITAL_INICIAL = 63000.0
LOT_PER_ENTRY = 0.05

PIP_MUL = {'EURGBP':1000,'EURCHF':1000,'AUDNZD':1000,'USDCHF':1000,'EURUSD':1000}
COSTS = {'EURGBP':0.30,'EURCHF':0.30,'AUDNZD':0.40,'USDCHF':0.30,'EURUSD':0.30}
PAIR_FILES = {
    'EURGBP': 'eurgbp_dk_m5_5y.csv','EURCHF': 'eurchf_dk_m5_5y.csv',
    'AUDNZD': 'audnzd_dk_m5_5y.csv','USDCHF': 'usdchf_dk_m5_5y.csv',
    'EURUSD': 'eurusd_m5_5y.csv',
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
        'TS':df_.index, 'n':len(df_)
    }

def bt_avg(arrs, direction, levels, stop_z, cost, pip_mul, lot):
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    SMA=arrs['SMA'];STD=arrs['STD']
    n=arrs['n']
    trades=[];pos=None
    for i in range(50,n):
        sma=SMA[i];std=STD[i];c=C[i]
        if np.isnan(sma) or np.isnan(std) or std<=0: continue
        z=(c-sma)/std
        if pos is not None:
            if direction=='long':
                stop_hit=z<=stop_z;target=c>=sma
            else:
                stop_hit=z>=-stop_z;target=c<=sma
            time_out=(i-pos['entries'][0][0])>=500
            if stop_hit or target or time_out:
                exit_p=c
                tot_u=len(pos['entries'])
                pnl=0;sgn=1 if direction=='long' else -1
                for eidx,ep in pos['entries']: pnl+=(exit_p-ep)*sgn
                pnl_d = pnl * pip_mul * (lot/0.01) - tot_u*cost*(lot/0.01)
                trades.append({
                    'open_ts': arrs['TS'][pos['entries'][0][0]],
                    'close_ts': arrs['TS'][i],
                    'pnl': pnl_d, 'direction': direction
                })
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

# Stable pairs setup multi-TF
STRATEGIES = [
    # EURGBP — el rei mean-rev (4 TFs)
    {'name':'EURGBP D1','pair':'EURGBP','tf':'1D','ma':100,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0},
    {'name':'EURGBP H4','pair':'EURGBP','tf':'4h','ma':300,'levels':[-1.0,-1.5,-2.5,-3.0],'stop':-5.0},
    {'name':'EURGBP H1','pair':'EURGBP','tf':'1h','ma':500,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-3.5},
    {'name':'EURGBP M15','pair':'EURGBP','tf':'15min','ma':2400,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-4.0},
    # EURCHF (3 TFs)
    {'name':'EURCHF D1','pair':'EURCHF','tf':'1D','ma':100,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-5.0},
    {'name':'EURCHF H4','pair':'EURCHF','tf':'4h','ma':150,'levels':[-1.5,-2.5,-3.5,-4.0],'stop':-6.0},
    # AUDNZD (2 TFs)
    {'name':'AUDNZD D1','pair':'AUDNZD','tf':'1D','ma':150,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-3.0},
    {'name':'AUDNZD H4','pair':'AUDNZD','tf':'4h','ma':300,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-3.0},
    # USDCHF (1 TF)
    {'name':'USDCHF H4','pair':'USDCHF','tf':'4h','ma':500,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-3.5},
]

print("Running stable-only multi-TF portfolio (BOTH dirs)...", flush=True)

all_trades = []
per_strat_summary = []

for s in STRATEGIES:
    df = pd.read_csv(PAIR_FILES[s['pair']], index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]
    df_tf = aggregate(df, s['tf'])
    arrs = precompute(df_tf, s['ma'])

    long_trades = bt_avg(arrs, 'long', s['levels'], s['stop'],
                         COSTS[s['pair']], PIP_MUL[s['pair']], LOT_PER_ENTRY)
    short_trades = bt_avg(arrs, 'short', s['levels'], s['stop'],
                          COSTS[s['pair']], PIP_MUL[s['pair']], LOT_PER_ENTRY)

    for t in long_trades + short_trades:
        t['strategy'] = s['name']
        all_trades.append(t)

    long_net = sum(t['pnl'] for t in long_trades)
    short_net = sum(t['pnl'] for t in short_trades)
    total = long_net + short_net
    n = len(long_trades) + len(short_trades)
    wins = sum(1 for t in long_trades+short_trades if t['pnl']>0)
    print(f"  {s['name']:<14} | n={n:>4} L:${long_net:>+7,.0f} S:${short_net:>+7,.0f} = ${total:>+7,.0f} WR{wins/n*100 if n>0 else 0:.1f}%")
    per_strat_summary.append({
        'name':s['name'],'n':n,'long_net':long_net,'short_net':short_net,'total':total,'wins':wins
    })

# Stats portfolio
all_trades.sort(key=lambda t: t['close_ts'])
running = 0; peak = 0; max_dd = 0; max_dd_pct = 0
daily_pnl = {}
for t in all_trades:
    running += t['pnl']
    if running > peak: peak = running
    dd = peak - running
    if dd > max_dd: max_dd = dd
    eq_now = CAPITAL_INICIAL + running
    eq_peak = CAPITAL_INICIAL + peak
    dd_pct = (eq_peak - eq_now) / eq_peak * 100
    if dd_pct > max_dd_pct: max_dd_pct = dd_pct
    d = pd.to_datetime(t['close_ts']).date()
    daily_pnl[d] = daily_pnl.get(d, 0) + t['pnl']

total_pnl = running
total_pct = total_pnl / CAPITAL_INICIAL * 100
span_days = (pd.to_datetime(all_trades[-1]['close_ts']) - pd.to_datetime(all_trades[0]['open_ts'])).days
span_y = span_days/365
annual = total_pct/span_y
n = len(all_trades)
wins = sum(1 for t in all_trades if t['pnl']>0)

print()
print("="*120)
print("PORTFOLIO STABLE-ONLY MULTI-TF — RESULTAT:")
print("="*120)
print(f"  Capital inicial:    ${CAPITAL_INICIAL:,.2f}")
print(f"  Total trades:       {n} ({n/span_y:.0f}/any)")
print(f"  Win rate:           {wins/n*100:.1f}%")
print(f"  Net P&L:            ${total_pnl:+,.2f}")
print(f"  Total return:       {total_pct:+.2f}%")
print(f"  ANNUAL:             {annual:+.2f}%")
print(f"  Max DD $:           ${max_dd:+,.2f}")
print(f"  Max DD %:           {max_dd_pct:.2f}%")
print(f"  Calmar:             {annual/max_dd_pct:.2f}" if max_dd_pct>0 else "  Calmar: N/A")

print()
print("="*120)
print("PER ESTRATEGIA (LONG vs SHORT):")
print("="*120)
total_long = sum(s['long_net'] for s in per_strat_summary)
total_short = sum(s['short_net'] for s in per_strat_summary)
for s in per_strat_summary:
    print(f"  {s['name']:<14} | n={s['n']:>4} L:${s['long_net']:>+7,.0f} S:${s['short_net']:>+7,.0f} = ${s['total']:>+7,.0f}")
print(f"  {'TOTAL':<14} | LONG=${total_long:+,.0f} SHORT=${total_short:+,.0f}")

print()
print("="*120)
print("PER ANY:")
print("="*120)
df_t = pd.DataFrame(all_trades)
df_t['year'] = pd.to_datetime(df_t['close_ts']).dt.year
for y, grp in df_t.groupby('year'):
    net_y = grp['pnl'].sum()
    pct_y = net_y / CAPITAL_INICIAL * 100
    nlong = (grp['direction']=='long').sum()
    nshort = (grp['direction']=='short').sum()
    sgn = "+" if net_y>0 else "-"
    print(f"  {y}: ${net_y:>+9,.0f} ({sgn}{pct_y:>5.2f}%) | trades={len(grp)} (L={nlong} S={nshort})")

# Worst days
print()
print("="*120)
print("DIES PITJORS:")
print("="*120)
ds = pd.Series(daily_pnl)
worst = ds.nsmallest(5)
for d, v in worst.items():
    pct = v / CAPITAL_INICIAL * 100
    print(f"  {d}: ${v:+,.0f} ({pct:+.2f}%)")

# BOJ check
print()
target_date = pd.to_datetime('2024-09-25').date()
v_boj = ds.get(target_date, 0)
print(f"BOJ event 25-9-2024: ${v_boj:+,.0f} ({v_boj/CAPITAL_INICIAL*100:+.2f}%)")

print("\nDONE")
