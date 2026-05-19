"""
PORTFOLIOS DIVERSIFICATS amb COBERTURA INTERNA
================================================
Compara 3 portfolios:
A) Original (concentrat JPY)
B) Diversificat macro (afegir or i AUDNZD)
C) Hedge interno (parells amb correlació negativa)

Mesures: Return, Max DD, Calmar, correlació inter-estratègies, dies pitjors.
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
                    'pnl': pnl_d
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

def run_strategy(s):
    df = pd.read_csv(PAIR_FILES[s['pair']], index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]
    df_tf = aggregate(df, s['tf'])
    arrs = precompute(df_tf, s['ma'])
    return bt_avg(arrs, s['direction'], s['levels'], s['stop'],
                  COSTS[s['pair']], PIP_MUL[s['pair']], LOT_PER_ENTRY)

def stats_portfolio(all_trades, name):
    if not all_trades:
        print(f"{name}: NO TRADES"); return None
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

    daily_series = pd.Series(daily_pnl)
    worst_day = daily_series.min()
    worst_day_date = daily_series.idxmin()

    # Calculate Sharpe (simplified)
    daily_returns = daily_series / CAPITAL_INICIAL * 100
    sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252) if daily_returns.std() > 0 else 0

    return {
        'name':name,'n':n,'wr':wins/n*100,
        'net':total_pnl,'pct':total_pct,'annual':annual_pct,
        'max_dd':max_dd_dollar,'max_dd_pct':max_dd_pct,
        'calmar':annual_pct/max_dd_pct if max_dd_pct>0 else 0,
        'sharpe':sharpe,
        'worst_day':worst_day,'worst_day_date':worst_day_date,
        'daily_series':daily_series,
    }

# ==================================================================
# DEFINICIO DELS 3 PORTFOLIOS
# ==================================================================

PORTFOLIO_A = [  # Original (concentrat JPY)
    {'name':'EURGBP D1','pair':'EURGBP','file':'eurgbp_dk_m5_5y.csv','tf':'1D','ma':100,
     'direction':'long','levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0},
    {'name':'EURGBP M15','pair':'EURGBP','file':'eurgbp_dk_m5_5y.csv','tf':'15min','ma':2400,
     'direction':'long','levels':[-1.0,-1.5,-2.0,-2.5],'stop':-4.0},
    {'name':'EURJPY H4','pair':'EURJPY','file':'eurjpy_dk_m5_5y.csv','tf':'4h','ma':300,
     'direction':'long','levels':[-0.5,-1.5,-2.5,-3.0],'stop':-5.0},
    {'name':'EURCHF H4','pair':'EURCHF','file':'eurchf_dk_m5_5y.csv','tf':'4h','ma':150,
     'direction':'short','levels':[-1.5,-2.5,-3.5,-4.0],'stop':-6.0},
    {'name':'CHFJPY H4','pair':'CHFJPY','file':'chfjpy_dk_m5_5y.csv','tf':'4h','ma':300,
     'direction':'long','levels':[-0.5,-1.5,-2.5,-3.0],'stop':-4.0},
]

PORTFOLIO_B = [  # Diversificat macro (no concentrat JPY)
    {'name':'EURGBP D1','pair':'EURGBP','file':'eurgbp_dk_m5_5y.csv','tf':'1D','ma':100,
     'direction':'long','levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0},
    {'name':'EURJPY H4','pair':'EURJPY','file':'eurjpy_dk_m5_5y.csv','tf':'4h','ma':300,
     'direction':'long','levels':[-0.5,-1.5,-2.5,-3.0],'stop':-5.0},
    {'name':'EURCHF H4 short','pair':'EURCHF','file':'eurchf_dk_m5_5y.csv','tf':'4h','ma':150,
     'direction':'short','levels':[-1.5,-2.5,-3.5,-4.0],'stop':-6.0},
    {'name':'XAUUSD H4 long','pair':'XAUUSD','file':'xauusd_m5_5y.csv','tf':'4h','ma':200,
     'direction':'long','levels':[-0.5,-1.0,-1.5,-2.0],'stop':-3.0},
    {'name':'AUDNZD H4','pair':'AUDNZD','file':'audnzd_dk_m5_5y.csv','tf':'4h','ma':300,
     'direction':'long','levels':[-0.5,-1.0,-1.5,-2.0],'stop':-3.0},
]

PORTFOLIO_C = [  # Hedge intern: pairs LONG vs SHORT amb relacions creuades
    {'name':'EURGBP D1','pair':'EURGBP','file':'eurgbp_dk_m5_5y.csv','tf':'1D','ma':100,
     'direction':'long','levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0},
    {'name':'EURJPY H4 LONG','pair':'EURJPY','file':'eurjpy_dk_m5_5y.csv','tf':'4h','ma':300,
     'direction':'long','levels':[-0.5,-1.5,-2.5,-3.0],'stop':-5.0},
    # SHORT pair: si JPY rally, EURJPY perd, AUDNZD podria estar invariant (hedge)
    {'name':'AUDNZD H4 BOTH','pair':'AUDNZD','file':'audnzd_dk_m5_5y.csv','tf':'4h','ma':300,
     'direction':'long','levels':[-0.5,-1.0,-1.5,-2.0],'stop':-3.0},
    # SHORT EURCHF (CHF rallies often when JPY rallies → SHORT EURCHF win!)
    {'name':'EURCHF H4 SHORT','pair':'EURCHF','file':'eurchf_dk_m5_5y.csv','tf':'4h','ma':150,
     'direction':'short','levels':[-1.5,-2.5,-3.5,-4.0],'stop':-6.0},
    # XAUUSD: hedge contra crisi USD/risk-off (or rally)
    {'name':'XAUUSD H4','pair':'XAUUSD','file':'xauusd_m5_5y.csv','tf':'4h','ma':200,
     'direction':'long','levels':[-0.5,-1.0,-1.5,-2.0],'stop':-3.0},
]

print("Running 3 portfolios...", flush=True)

portfolios = {}
for name, port in [('A_original', PORTFOLIO_A), ('B_diversificat', PORTFOLIO_B), ('C_hedge_intern', PORTFOLIO_C)]:
    print(f"\n=== Portfolio {name} ===")
    all_trades = []
    daily_per_strat = {}
    for s in port:
        print(f"  Running {s['name']}...", flush=True)
        trades = run_strategy(s)
        # Annotate
        for t in trades: t['strategy'] = s['name']
        all_trades += trades
        # Per-strategy daily PnL
        daily = {}
        for t in trades:
            d = pd.to_datetime(t['close_ts']).date()
            daily[d] = daily.get(d, 0) + t['pnl']
        daily_per_strat[s['name']] = daily

    stat = stats_portfolio(all_trades, name)
    portfolios[name] = {'stat': stat, 'all_trades': all_trades, 'daily_per_strat': daily_per_strat, 'port': port}

# COMPARATIVE
print()
print("="*120)
print("COMPARACIO PORTFOLIOS:")
print("="*120)
print(f"  {'Portfolio':<20} {'N':>5} {'WR%':>6} {'Net $':>11} {'%/any':>8} {'Max DD$':>9} {'DD%':>6} {'Calmar':>7} {'Sharpe':>7} {'Pitjor dia':>11}")
for name, p in portfolios.items():
    s = p['stat']
    print(f"  {name:<20} {s['n']:>5} {s['wr']:>5.1f}% ${s['net']:>+8,.0f} {s['annual']:>+6.2f}% ${s['max_dd']:>+7,.0f} {s['max_dd_pct']:>5.2f}% {s['calmar']:>6.2f} {s['sharpe']:>6.2f} ${s['worst_day']:>+8,.0f}")

# CORRELATIONS — for each portfolio, compute inter-strategy correlation
print()
print("="*120)
print("CORRELACIO INTER-ESTRATEGIES (per portfolio):")
print("="*120)
for name, p in portfolios.items():
    print(f"\n{name}:")
    # Build daily PnL DataFrame
    df_daily = pd.DataFrame(p['daily_per_strat']).fillna(0)
    if len(df_daily) > 10:
        corr = df_daily.corr()
        print(corr.round(2).to_string())

# PER ANY breakdown for each
print()
print("="*120)
print("PER ANY (Net %):")
print("="*120)
for name, p in portfolios.items():
    print(f"\n{name}:")
    df_t = pd.DataFrame(p['all_trades'])
    df_t['year'] = pd.to_datetime(df_t['close_ts']).dt.year
    yrs = sorted(df_t['year'].unique())
    for y in yrs:
        net_y = df_t[df_t['year']==y]['pnl'].sum()
        pct_y = net_y / CAPITAL_INICIAL * 100
        sgn = "+" if net_y>0 else ""
        print(f"  {y}: ${net_y:>+9,.0f} ({sgn}{pct_y:.2f}%)")

print()
print("="*120)
print("DIES PITJORS (max pèrdua diari):")
print("="*120)
for name, p in portfolios.items():
    daily = p['stat']['daily_series']
    worst5 = daily.nsmallest(5)
    print(f"\n{name} — top 5 dies pitjors:")
    for d, v in worst5.items():
        print(f"  {d}: ${v:+.0f}")

print("\nDONE")
