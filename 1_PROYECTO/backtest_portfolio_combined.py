"""
PORTFOLIO COMBINAT — 5 estratègies operant SIMULTANIAMENT durant 5 anys
=========================================================================
Compte: $63,000 inicial
Allocacio: 5 estratègies, 0.05 lot per entry cadascuna
Simula: equity curve real amb totes operant alhora.

Mesures:
- Net Total P&L $ i %
- Max DD real (no teòric)
- Sharpe / Calmar ratio
- Per any breakdown
"""
import pandas as pd
import numpy as np

CAPITAL_INICIAL = 63000.0
LOT_PER_ENTRY = 0.05  # 0.05 lot = 5x del backtest 0.01

# Pip multiplier per asset (preu diff -> $ per 0.01 lot)
PIP_MUL = {
    'EURGBP': 1000, 'EURJPY': 100, 'EURCHF': 1000,
    'CHFJPY': 100, 'XAUUSD': 1
}

COSTS = {
    'EURGBP': 0.30, 'EURJPY': 0.30, 'EURCHF': 0.30,
    'CHFJPY': 0.40, 'XAUUSD': 0.50
}

PAIRS_FILES = {
    'EURGBP': 'eurgbp_dk_m5_5y.csv',
    'EURJPY': 'eurjpy_dk_m5_5y.csv',
    'EURCHF': 'eurchf_dk_m5_5y.csv',
    'CHFJPY': 'chfjpy_dk_m5_5y.csv',
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
    """Mean-rev averaging amb mida lot configurable.
    Retorna llista de trades amb timestamp i pnl_$ ja escalat al lot."""
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
                # Scale by lot ratio: 0.01 lot baseline → lot_real = pip_mul × (lot/0.01)
                pnl_d = pnl * pip_mul * (lot/0.01) - tot_u*cost*(lot/0.01)
                trades.append({
                    'open_ts': arrs['TS'][pos['entries'][0][0]],
                    'close_ts': arrs['TS'][i],
                    'pnl': pnl_d,'units':tot_u,
                    'reason':'STOP' if stop_hit else ('TARGET' if target else 'TIME')
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

# ==================================================================
# Configuració de les 5 estratègies
# ==================================================================
STRATEGIES = [
    {'name':'EURGBP D1','file':'eurgbp_dk_m5_5y.csv','tf':'1D','ma':100,
     'direction':'long','levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'pair':'EURGBP'},
    {'name':'EURGBP M15','file':'eurgbp_dk_m5_5y.csv','tf':'15min','ma':2400,
     'direction':'long','levels':[-1.0,-1.5,-2.0,-2.5],'stop':-4.0,'pair':'EURGBP'},
    {'name':'EURJPY H4','file':'eurjpy_dk_m5_5y.csv','tf':'4h','ma':300,
     'direction':'long','levels':[-0.5,-1.5,-2.5,-3.0],'stop':-5.0,'pair':'EURJPY'},
    {'name':'EURCHF H4','file':'eurchf_dk_m5_5y.csv','tf':'4h','ma':150,
     'direction':'short','levels':[-1.5,-2.5,-3.5,-4.0],'stop':-6.0,'pair':'EURCHF'},
    {'name':'CHFJPY H4','file':'chfjpy_dk_m5_5y.csv','tf':'4h','ma':300,
     'direction':'long','levels':[-0.5,-1.5,-2.5,-3.0],'stop':-4.0,'pair':'CHFJPY'},
]

# Run cada estratègia i collect trades
print("Running 5 strategies in parallel...", flush=True)
all_trades = []
strategy_summary = []

for s in STRATEGIES:
    print(f"  {s['name']}...", flush=True)
    df = pd.read_csv(s['file'], index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]
    df_tf = aggregate(df, s['tf'])
    arrs = precompute(df_tf, s['ma'])
    trades = bt_avg(arrs, s['direction'], s['levels'], s['stop'],
                     COSTS[s['pair']], PIP_MUL[s['pair']], LOT_PER_ENTRY)
    for t in trades:
        t['strategy'] = s['name']
        all_trades.append(t)
    n=len(trades);wins=sum(1 for t in trades if t['pnl']>0)
    net=sum(t['pnl'] for t in trades)
    strategy_summary.append({
        'name':s['name'],'n':n,'wins':wins,'wr':wins/n*100 if n>0 else 0,'net':net
    })
    print(f"    {n} trades, WR {wins/n*100 if n>0 else 0:.1f}%, Net=${net:+.0f}")

# Sort all trades by close time (when they affect equity)
all_trades.sort(key=lambda t: t['close_ts'])

# Build equity curve
print("\nBuilding equity curve...", flush=True)
equity = CAPITAL_INICIAL
equity_curve = [(all_trades[0]['open_ts'] if all_trades else pd.Timestamp.now(), equity)]
peak = equity
max_dd_dollar = 0
max_dd_pct = 0
running_pnl = 0

for t in all_trades:
    running_pnl += t['pnl']
    equity = CAPITAL_INICIAL + running_pnl
    equity_curve.append((t['close_ts'], equity))
    if equity > peak:
        peak = equity
    dd = peak - equity
    dd_pct = dd / peak * 100
    if dd > max_dd_dollar:
        max_dd_dollar = dd
    if dd_pct > max_dd_pct:
        max_dd_pct = dd_pct

# Final stats
total_pnl = running_pnl
final_equity = CAPITAL_INICIAL + total_pnl
total_return_pct = total_pnl / CAPITAL_INICIAL * 100

# Calculate years span
if all_trades:
    span = (all_trades[-1]['close_ts'] - all_trades[0]['open_ts']).days / 365
else:
    span = 0
annual_return = total_return_pct / span if span > 0 else 0

print()
print("="*100)
print("RESUM PER ESTRATEGIA:")
print("="*100)
total_n = 0
total_wins = 0
for s in strategy_summary:
    print(f"  {s['name']:<14} | n={s['n']:>4} WR{s['wr']:>5.1f}% Net=${s['net']:>+8.0f}")
    total_n += s['n']
    total_wins += s['wins']

print()
print("="*100)
print("PORTFOLIO COMBINAT (totes 5 estratègies sobre $63k, 0.05 lot per entry):")
print("="*100)
print(f"  Capital inicial:       ${CAPITAL_INICIAL:,.2f}")
print(f"  Total trades:          {total_n}")
print(f"  Total wins:            {total_wins}")
print(f"  Win rate combinat:     {total_wins/total_n*100:.1f}%")
print(f"  Total trades/any:      {total_n/span:.0f}")
print(f"  Total Net P&L:         ${total_pnl:+,.2f}")
print(f"  Final equity:          ${final_equity:,.2f}")
print(f"  TOTAL RETURN:          {total_return_pct:+.2f}%")
print(f"  Span:                  {span:.1f} anys")
print(f"  ANNUAL RETURN:         {annual_return:+.2f}%/any")
print()
print(f"  MAX DRAWDOWN $:        -${max_dd_dollar:,.2f}")
print(f"  MAX DRAWDOWN %:        -{max_dd_pct:.2f}%")
print(f"  Calmar ratio:          {annual_return/max_dd_pct:.2f}" if max_dd_pct > 0 else "  Calmar: N/A")

# Per any breakdown
print()
print("="*100)
print("PER ANY:")
print("="*100)
df_t = pd.DataFrame(all_trades)
df_t['year'] = pd.to_datetime(df_t['close_ts']).dt.year
print(f"  {'Any':<6} {'Trades':>8} {'Wins':>6} {'WR%':>6} {'Net $':>12} {'Return %':>10}")
for yr, grp in df_t.groupby('year'):
    n = len(grp)
    w = (grp['pnl']>0).sum()
    net_yr = grp['pnl'].sum()
    pct_yr = net_yr / CAPITAL_INICIAL * 100
    print(f"  {yr:<6} {n:>8} {w:>6} {w/n*100:>5.1f}% ${net_yr:>+11,.0f} {pct_yr:>+9.2f}%")

# Per estratègia by year
print()
print("="*100)
print("PER ESTRATEGIA + ANY (Net $):")
print("="*100)
header = f"  {'Strategy':<14} "
years = sorted(df_t['year'].unique())
for y in years: header += f"{y:>9}"
header += f"{'TOTAL':>11}"
print(header)
for s in STRATEGIES:
    sub = df_t[df_t['strategy']==s['name']]
    line = f"  {s['name']:<14} "
    total = 0
    for y in years:
        ysub = sub[sub['year']==y]
        net_y = ysub['pnl'].sum()
        line += f"${net_y:>+8.0f}"
        total += net_y
    line += f"  ${total:>+8.0f}"
    print(line)

# Worst day / week / month
print()
print("="*100)
print("PERIODES MES NEGATIUS:")
print("="*100)
df_t['date'] = pd.to_datetime(df_t['close_ts']).dt.date
daily = df_t.groupby('date')['pnl'].sum().sort_values()
print("Worst 5 days:")
for d, p in daily.head(5).items():
    print(f"  {d}: ${p:+.0f}")
print("\nBest 5 days:")
for d, p in daily.tail(5).items():
    print(f"  {d}: ${p:+.0f}")

# Drawdown periods
print()
print("="*100)
print("DETALLS DRAWDOWN MAX:")
print("="*100)
# Find DD period
peak_dt = None
max_dd_start = None
max_dd_end = None
peak = CAPITAL_INICIAL
running = 0
peak_running = 0
for t in all_trades:
    running += t['pnl']
    if running > peak_running:
        peak_running = running
        peak_dt = t['close_ts']
    dd = peak_running - running
    if dd / (CAPITAL_INICIAL + peak_running) * 100 == max_dd_pct:
        max_dd_start = peak_dt
        max_dd_end = t['close_ts']

if max_dd_start and max_dd_end:
    print(f"  Inici DD: {max_dd_start}")
    print(f"  Fons DD:  {max_dd_end}")
    print(f"  Durada:   {(pd.Timestamp(max_dd_end) - pd.Timestamp(max_dd_start)).days} dies")

print("\nDONE")
