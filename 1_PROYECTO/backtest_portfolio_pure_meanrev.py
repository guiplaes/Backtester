"""
PORTFOLIO MEAN-REV PUR — Tots BOTH directions
==============================================
La idea correcta: mean-rev és regime-agnostic per definicio.
Si JPY canvia tendencia, els SHORTs prenen el relleu dels LONGs.

Comparem:
- A_concentrat: 1 direcció per pair (LONG-only o SHORT-only)
- F_BOTH_purist: Tots els pairs BOTH directions
- G_BOTH_filtered: BOTH directions però només pairs que han demostrat funcionar bidir
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
    'EURGBP': 'eurgbp_dk_m5_5y.csv','EURJPY': 'eurjpy_dk_m5_5y.csv',
    'EURCHF': 'eurchf_dk_m5_5y.csv','CHFJPY': 'chfjpy_dk_m5_5y.csv',
    'XAUUSD': 'xauusd_m5_5y.csv','AUDNZD': 'audnzd_dk_m5_5y.csv',
    'USDCHF': 'usdchf_dk_m5_5y.csv','EURUSD': 'eurusd_m5_5y.csv',
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

def run_strategy(s, dir_):
    df = pd.read_csv(PAIR_FILES[s['pair']], index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]
    df_tf = aggregate(df, s['tf'])
    arrs = precompute(df_tf, s['ma'])
    return bt_avg(arrs, dir_, s['levels'], s['stop'],
                  COSTS[s['pair']], PIP_MUL[s['pair']], LOT_PER_ENTRY)

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
        'sharpe':sharpe,'worst_day':worst_day,
        'daily_series':daily_series, 'all_trades':all_trades,
    }

# Strategies definition (configs ja optimitzats)
STRATEGIES = {
    'EURGBP D1':{'pair':'EURGBP','tf':'1D','ma':100,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0},
    'EURGBP M15':{'pair':'EURGBP','tf':'15min','ma':2400,'levels':[-1.0,-1.5,-2.0,-2.5],'stop':-4.0},
    'EURGBP H4':{'pair':'EURGBP','tf':'4h','ma':300,'levels':[-1.0,-1.5,-2.5,-3.0],'stop':-5.0},
    'EURJPY H4':{'pair':'EURJPY','tf':'4h','ma':300,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-5.0},
    'EURCHF H4':{'pair':'EURCHF','tf':'4h','ma':150,'levels':[-1.5,-2.5,-3.5,-4.0],'stop':-6.0},
    'CHFJPY H4':{'pair':'CHFJPY','tf':'4h','ma':300,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-4.0},
}

# Portfolio configs
A_concentrat_dirs = {
    'EURGBP D1':['long'],'EURGBP M15':['long'],
    'EURJPY H4':['long'],'EURCHF H4':['short'],'CHFJPY H4':['long']
}

F_BOTH_purist = {
    'EURGBP D1':['long','short'],
    'EURGBP M15':['long','short'],
    'EURJPY H4':['long','short'],
    'EURCHF H4':['long','short'],
    'CHFJPY H4':['long','short'],
}

# G: Filtrat — només els pairs amb edge bidirectional decent (sabem EURGBP funciona BOTH)
G_BOTH_filtered = {
    'EURGBP D1':['long','short'],
    'EURGBP M15':['long','short'],
    'EURGBP H4':['long','short'],   # Afegit H4 com a tercer de EURGBP
    'EURJPY H4':['long','short'],   # JPY pair BOTH dirs
    'CHFJPY H4':['long','short'],   # JPY pair BOTH dirs
}

print("Running 3 portfolios with same strategies, different directions...", flush=True)

portfolios = {}
for name, port_dirs in [('A_concentrat', A_concentrat_dirs),
                         ('F_BOTH_purist', F_BOTH_purist),
                         ('G_BOTH_filtered', G_BOTH_filtered)]:
    print(f"\n=== Portfolio {name} ===")
    all_trades = []
    per_strat = {}
    for strat_name, dirs in port_dirs.items():
        s = STRATEGIES[strat_name]
        strat_trades = []
        for d in dirs:
            print(f"  Running {strat_name} {d}...", flush=True)
            trades = run_strategy(s, d)
            strat_trades += trades
            for t in trades: t['strategy'] = f"{strat_name} {d}"
            all_trades += trades
        n=len(strat_trades);wins=sum(1 for t in strat_trades if t['pnl']>0)
        net=sum(t['pnl'] for t in strat_trades)
        per_strat[strat_name] = {'n':n,'wins':wins,'net':net,
                                   'long_net':sum(t['pnl'] for t in strat_trades if t['direction']=='long'),
                                   'short_net':sum(t['pnl'] for t in strat_trades if t['direction']=='short')}
        print(f"    {strat_name} TOTAL: n={n} WR{wins/n*100 if n>0 else 0:.1f}% Net=${net:+.0f}")

    stat = stats_portfolio(all_trades, name)
    portfolios[name] = stat
    portfolios[name]['per_strat'] = per_strat

# Comparativa
print()
print("="*135)
print("COMPARACIO PORTFOLIOS:")
print("="*135)
print(f"  {'Portfolio':<22} {'N':>5} {'L/S':>10} {'WR%':>6} {'Net $':>11} {'%/any':>8} {'Max DD$':>9} {'DD%':>7} {'Calmar':>7} {'Sharpe':>7} {'Pitjor':>10}")
for name, s in portfolios.items():
    print(f"  {name:<22} {s['n']:>5} {s['n_long']:>4}/{s['n_short']:>4} {s['wr']:>5.1f}% ${s['net']:>+8,.0f} {s['annual']:>+6.2f}% ${s['max_dd']:>+7,.0f} {s['max_dd_pct']:>6.2f}% {s['calmar']:>6.2f} {s['sharpe']:>6.2f} ${s['worst_day']:>+8,.0f}")

# Per estratègia detall
print()
print("="*135)
print("DETALL PER ESTRATEGIA (LONG vs SHORT contribution):")
print("="*135)
for name, s in portfolios.items():
    print(f"\n{name}:")
    for sn, st in s['per_strat'].items():
        print(f"  {sn:<14} | n={st['n']:>4} Net=${st['net']:>+7,.0f} (LONG=${st['long_net']:>+7,.0f}, SHORT=${st['short_net']:>+7,.0f})")

# Per any
print()
print("="*135)
print("PER ANY (Net % del compte):")
print("="*135)
for name, s in portfolios.items():
    print(f"\n{name}:")
    df_t = pd.DataFrame(s['all_trades'])
    df_t['year'] = pd.to_datetime(df_t['close_ts']).dt.year
    yrs = sorted(df_t['year'].unique())
    for y in yrs:
        net_y = df_t[df_t['year']==y]['pnl'].sum()
        pct_y = net_y / CAPITAL_INICIAL * 100
        sgn = "+" if net_y>0 else ""
        print(f"  {y}: ${net_y:>+9,.0f} ({sgn}{pct_y:.2f}%)")

# Pitjors dies
print()
print("="*135)
print("DIES PITJORS (top 5):")
print("="*135)
for name, s in portfolios.items():
    print(f"\n{name}:")
    worst = s['daily_series'].nsmallest(5)
    for d, v in worst.items():
        pct = v / CAPITAL_INICIAL * 100
        print(f"  {d}: ${v:>+7,.0f} ({pct:+.2f}%)")

# Cas BOJ event detallat
print()
print("="*135)
print("ANALISI ESPECIFIC 25-9-2024 (BOJ event):")
print("="*135)
target_date = pd.to_datetime('2024-09-25').date()
for name, s in portfolios.items():
    if target_date in s['daily_series'].index:
        v = s['daily_series'][target_date]
        pct = v / CAPITAL_INICIAL * 100
        print(f"  {name}: ${v:+,.0f} ({pct:+.2f}%)")
    else:
        print(f"  {name}: cap trade aquell dia")

print("\nDONE")
