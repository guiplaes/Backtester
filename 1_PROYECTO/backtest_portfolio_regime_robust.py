"""
PORTFOLIO REGIME-ROBUST
========================
Filosofia: No apostem a tendencia macro. Apostem a MEAN-REVERSION pura.
Cada estrategia BOTH directions (LONG quan z<<0, SHORT quan z>>0).
Si la tendencia macro canvia, l'estrategia s'adapta sola.

Triem pairs amb mean-reversion històrica forta (range-bound),
NO pairs trending (que només funcionen en una direcció).

Compara vs Portfolio A (concentrat) per veure el cost de la robustesa.
"""
import pandas as pd
import numpy as np

CAPITAL_INICIAL = 63000.0
LOT_PER_ENTRY = 0.05

PIP_MUL = {'EURGBP':1000,'EURJPY':100,'EURCHF':1000,'CHFJPY':100,
           'XAUUSD':1,'AUDNZD':1000,'USDCHF':1000,'EURUSD':1000}

COSTS = {'EURGBP':0.30,'EURJPY':0.30,'EURCHF':0.30,'CHFJPY':0.40,
         'XAUUSD':0.50,'AUDNZD':0.40,'USDCHF':0.30,'EURUSD':0.30}

PAIR_FILES = {
    'EURGBP': 'eurgbp_dk_m5_5y.csv',
    'EURJPY': 'eurjpy_dk_m5_5y.csv',
    'EURCHF': 'eurchf_dk_m5_5y.csv',
    'CHFJPY': 'chfjpy_dk_m5_5y.csv',
    'XAUUSD': 'xauusd_m5_5y.csv',
    'AUDNZD': 'audnzd_dk_m5_5y.csv',
    'USDCHF': 'usdchf_dk_m5_5y.csv',
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

def run_strategy_both(s):
    """Run both LONG and SHORT for the strategy."""
    df = pd.read_csv(PAIR_FILES[s['pair']], index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]
    df_tf = aggregate(df, s['tf'])
    arrs = precompute(df_tf, s['ma'])
    long_trades = bt_avg(arrs, 'long', s['levels'], s['stop'],
                         COSTS[s['pair']], PIP_MUL[s['pair']], LOT_PER_ENTRY)
    short_trades = bt_avg(arrs, 'short', s['levels'], s['stop'],
                          COSTS[s['pair']], PIP_MUL[s['pair']], LOT_PER_ENTRY)
    return long_trades + short_trades

def stats_portfolio(all_trades, name):
    if not all_trades: return None
    all_trades.sort(key=lambda t: t['close_ts'])
    running = 0; peak = 0; max_dd_dollar = 0; max_dd_pct = 0
    daily_pnl = {}
    for t in all_trades:
        running += t['pnl']
        if running > peak: peak = running
        dd = peak - running
        equity_now = CAPITAL_INICIAL + running
        equity_peak = CAPITAL_INICIAL + peak
        dd_pct_val = (equity_peak - equity_now) / equity_peak * 100
        if dd > max_dd_dollar: max_dd_dollar = dd
        if dd_pct_val > max_dd_pct: max_dd_pct = dd_pct_val
        d = pd.to_datetime(t['close_ts']).date()
        daily_pnl[d] = daily_pnl.get(d, 0) + t['pnl']

    total_pnl = running
    total_pct = total_pnl / CAPITAL_INICIAL * 100
    span_days = (pd.to_datetime(all_trades[-1]['close_ts']) -
                  pd.to_datetime(all_trades[0]['open_ts'])).days
    span_y = span_days/365 if span_days else 1
    annual_pct = total_pct / span_y
    n = len(all_trades)
    wins = sum(1 for t in all_trades if t['pnl']>0)
    n_long = sum(1 for t in all_trades if t['direction']=='long')
    n_short = sum(1 for t in all_trades if t['direction']=='short')

    daily_series = pd.Series(daily_pnl)
    worst_day = daily_series.min()

    daily_returns = daily_series / CAPITAL_INICIAL * 100
    sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252) if daily_returns.std() > 0 else 0

    return {
        'name':name,'n':n,'wr':wins/n*100 if n>0 else 0,
        'n_long':n_long,'n_short':n_short,
        'net':total_pnl,'pct':total_pct,'annual':annual_pct,
        'max_dd':max_dd_dollar,'max_dd_pct':max_dd_pct,
        'calmar':annual_pct/max_dd_pct if max_dd_pct>0 else 0,
        'sharpe':sharpe,
        'worst_day':worst_day,
        'daily_series':daily_series,
        'all_trades':all_trades,
    }

# ==================================================================
# PORTFOLIOS:
# ==================================================================

# A: ORIGINAL (LONG/SHORT optimal direction concentration)
PORTFOLIO_A = [
    {'name':'EURGBP D1','pair':'EURGBP','tf':'1D','ma':100,
     'direction':'long','levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0},
    {'name':'EURGBP M15','pair':'EURGBP','tf':'15min','ma':2400,
     'direction':'long','levels':[-1.0,-1.5,-2.0,-2.5],'stop':-4.0},
    {'name':'EURJPY H4 LONG','pair':'EURJPY','tf':'4h','ma':300,
     'direction':'long','levels':[-0.5,-1.5,-2.5,-3.0],'stop':-5.0},
    {'name':'EURCHF H4 SHORT','pair':'EURCHF','tf':'4h','ma':150,
     'direction':'short','levels':[-1.5,-2.5,-3.5,-4.0],'stop':-6.0},
    {'name':'CHFJPY H4 LONG','pair':'CHFJPY','tf':'4h','ma':300,
     'direction':'long','levels':[-0.5,-1.5,-2.5,-3.0],'stop':-4.0},
]

# D: REGIME-ROBUST (mean-reverting pairs, BOTH directions)
PORTFOLIO_D = [
    # EURGBP — el rei mean-rev, BOTH directions
    {'name':'EURGBP D1 BOTH','pair':'EURGBP','tf':'1D','ma':100,
     'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'both':True},
    {'name':'EURGBP M15 BOTH','pair':'EURGBP','tf':'15min','ma':2400,
     'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-4.0,'both':True},
    {'name':'EURGBP H4 BOTH','pair':'EURGBP','tf':'4h','ma':300,
     'levels':[-1.0,-1.5,-2.5,-3.0],'stop':-5.0,'both':True},
    # AUDNZD — antipodean cross, range-bound
    {'name':'AUDNZD H4 BOTH','pair':'AUDNZD','tf':'4h','ma':300,
     'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-3.0,'both':True},
    # EURCHF — range bound, BOTH dirs
    {'name':'EURCHF H4 BOTH','pair':'EURCHF','tf':'4h','ma':150,
     'levels':[-1.5,-2.5,-3.5,-4.0],'stop':-6.0,'both':True},
]

# E: MIXED (mig portfolio A i mig D — equilibri risc/rendiment)
PORTFOLIO_E = [
    # 2 SLOTs cum portfolio A (focus en pairs robustos)
    {'name':'EURGBP D1','pair':'EURGBP','tf':'1D','ma':100,
     'direction':'long','levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0},
    {'name':'EURJPY H4 LONG','pair':'EURJPY','tf':'4h','ma':300,
     'direction':'long','levels':[-0.5,-1.5,-2.5,-3.0],'stop':-5.0},
    # 3 SLOTs BOTH directions (regime-robust)
    {'name':'EURGBP M15 BOTH','pair':'EURGBP','tf':'15min','ma':2400,
     'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-4.0,'both':True},
    {'name':'EURCHF H4 BOTH','pair':'EURCHF','tf':'4h','ma':150,
     'levels':[-1.5,-2.5,-3.5,-4.0],'stop':-6.0,'both':True},
    {'name':'AUDNZD H4 BOTH','pair':'AUDNZD','tf':'4h','ma':300,
     'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-3.0,'both':True},
]

print("Running 3 portfolios (A vs D vs E)...", flush=True)

portfolios = {}
for name, port in [('A_concentrat', PORTFOLIO_A),
                    ('D_regime_robust', PORTFOLIO_D),
                    ('E_mixed', PORTFOLIO_E)]:
    print(f"\n=== Portfolio {name} ===")
    all_trades = []
    daily_per_strat = {}
    for s in port:
        if s.get('both'):
            print(f"  Running {s['name']} (BOTH dirs)...", flush=True)
            trades = run_strategy_both(s)
        else:
            print(f"  Running {s['name']} ({s['direction']})...", flush=True)
            df = pd.read_csv(PAIR_FILES[s['pair']], index_col=0, parse_dates=True)
            df.index = pd.to_datetime(df.index, utc=True)
            df.columns = [c.lower() for c in df.columns]
            df_tf = aggregate(df, s['tf'])
            arrs = precompute(df_tf, s['ma'])
            trades = bt_avg(arrs, s['direction'], s['levels'], s['stop'],
                            COSTS[s['pair']], PIP_MUL[s['pair']], LOT_PER_ENTRY)
        for t in trades:
            t['strategy'] = s['name']
        all_trades += trades
        daily = {}
        for t in trades:
            d = pd.to_datetime(t['close_ts']).date()
            daily[d] = daily.get(d, 0) + t['pnl']
        daily_per_strat[s['name']] = daily
        n=len(trades);wins=sum(1 for t in trades if t['pnl']>0)
        net=sum(t['pnl'] for t in trades)
        n_l=sum(1 for t in trades if t['direction']=='long')
        n_s=sum(1 for t in trades if t['direction']=='short')
        print(f"    n={n} (L={n_l} S={n_s}) WR{wins/n*100 if n>0 else 0:.1f}% Net=${net:+.0f}")

    stat = stats_portfolio(all_trades, name)
    portfolios[name] = stat
    portfolios[name]['daily_per_strat'] = daily_per_strat

# COMPARATIVE
print()
print("="*135)
print("COMPARACIO PORTFOLIOS:")
print("="*135)
print(f"  {'Portfolio':<20} {'N':>5} {'L/S':>10} {'WR%':>6} {'Net $':>11} {'%/any':>8} {'Max DD$':>9} {'DD%':>6} {'Calmar':>7} {'Sharpe':>7} {'Worst':>9}")
for name, s in portfolios.items():
    print(f"  {name:<20} {s['n']:>5} {s['n_long']:>4}/{s['n_short']:>4} {s['wr']:>5.1f}% ${s['net']:>+8,.0f} {s['annual']:>+6.2f}% ${s['max_dd']:>+7,.0f} {s['max_dd_pct']:>5.2f}% {s['calmar']:>6.2f} {s['sharpe']:>6.2f} ${s['worst_day']:>+7,.0f}")

# PER ANY
print()
print("="*135)
print("PER ANY (Net % del compte):")
print("="*135)
header = f"  {'Portfolio':<20}"
yrs = sorted(set(pd.to_datetime(t['close_ts']).year for p in portfolios.values() for t in p['all_trades']))
for y in yrs: header += f" {y:>8}"
header += f" {'TOTAL':>10}"
print(header)
for name, s in portfolios.items():
    df_t = pd.DataFrame(s['all_trades'])
    df_t['year'] = pd.to_datetime(df_t['close_ts']).dt.year
    line = f"  {name:<20}"
    total = 0
    for y in yrs:
        net_y = df_t[df_t['year']==y]['pnl'].sum()
        pct_y = net_y / CAPITAL_INICIAL * 100
        line += f" {pct_y:>+7.2f}%"
        total += pct_y
    line += f" {total:>+9.2f}%"
    print(line)

# WORST 5 DAYS PER PORTFOLIO
print()
print("="*135)
print("DIES PITJORS:")
print("="*135)
for name, s in portfolios.items():
    print(f"\n{name} — top 5 dies pitjors:")
    worst = s['daily_series'].nsmallest(5)
    for d, v in worst.items():
        pct = v / CAPITAL_INICIAL * 100
        print(f"  {d}: ${v:>+7,.0f} ({pct:+.2f}% del compte)")

# SHORT contributions in robust portfolios
print()
print("="*135)
print("CONTRIBUCIO SHORT vs LONG (per portfolio):")
print("="*135)
for name, s in portfolios.items():
    df_t = pd.DataFrame(s['all_trades'])
    long_pnl = df_t[df_t['direction']=='long']['pnl'].sum()
    short_pnl = df_t[df_t['direction']=='short']['pnl'].sum()
    print(f"  {name}: LONG=${long_pnl:+.0f} SHORT=${short_pnl:+.0f}")

print("\nDONE")
