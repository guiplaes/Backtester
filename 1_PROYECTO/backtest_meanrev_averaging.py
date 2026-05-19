"""
MEAN-REVERSION AMB SCALE-IN (AVERAGING)
========================================
"Operar entorn a una mitjana, auto-ajustant-se" — escala posicions a mesura
que el preu s'allunya de la mitjana, i tanca tot quan torna.

Estratègia:
1. Si preu < SMA - 1*std → BUY 1 lot
2. Si preu < SMA - 2*std → BUY 1 més (acumulem)
3. Si preu < SMA - 3*std → BUY 1 més (3 lots total)
4. Si preu < SMA - 4*std → STOP (regime change, sortir tot amb pèrdua)
5. Si preu >= SMA → SELL all (cobrar profit avg)

La gràcia: en lateral o pullback temporal acumules i guanyes el retorn.
En trend fort, el stop a -4σ et tanca abans de catastrofe.

NO LOOKAHEAD: tots els SMA/std calculats amb dades passades only.
"""
import pandas as pd
import numpy as np

REAL_COST_BY_ASSET = {'XAUUSD': 0.40, 'EURUSD': 0.07}

print("Loading...", flush=True)
RAW = {
    'XAUUSD': pd.read_csv('xauusd_m5_5y.csv', index_col=0, parse_dates=True),
    'EURUSD': pd.read_csv('eurusd_m5_5y.csv', index_col=0, parse_dates=True),
}
for asset, df in RAW.items():
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]

def aggregate(df_, rule):
    return df_.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last')).dropna()

def add_ind(df_, ma_period=50):
    df_ = df_.copy()
    df_['ema50'] = df_['close'].ewm(span=50, adjust=False).mean()
    df_['ema200'] = df_['close'].ewm(span=200, adjust=False).mean()
    hl = df_['high']-df_['low']
    hc = (df_['high']-df_['close'].shift()).abs()
    lc = (df_['low']-df_['close'].shift()).abs()
    tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
    df_['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
    df_[f'sma{ma_period}'] = df_['close'].rolling(ma_period).mean()
    df_[f'std{ma_period}'] = df_['close'].rolling(ma_period).std()
    return df_

def precompute(df_, ma_period=50):
    return {
        'O':df_['open'].values,'H':df_['high'].values,
        'L':df_['low'].values,'C':df_['close'].values,
        'EMA50':df_['ema50'].values,'EMA200':df_['ema200'].values,
        'ATR':df_['atr'].values,
        'SMA':df_[f'sma{ma_period}'].values,
        'STD':df_[f'std{ma_period}'].values,
        'TS':df_.index, 'YEAR':df_.index.year.values,
        'n':len(df_)
    }

def bt_averaging_meanrev(arrs, direction, levels, stop_z, real_cost, max_bars=500):
    """
    direction: 'long' or 'short'
    levels: list of z-scores at which to add a unit. e.g. [-1,-2,-3] for long
    stop_z: z-score that triggers stop loss (e.g. -4 for long)
    Quan preu torna a SMA → tanca tot.
    """
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    SMA=arrs['SMA'];STD=arrs['STD']
    n=arrs['n']
    trades = []
    pos = None  # dict: {entries: [(idx, price)], levels_hit: set}
    for i in range(50, n):
        sma = SMA[i]; std = STD[i]; c = C[i]
        if np.isnan(sma) or np.isnan(std) or std<=0:
            continue
        z = (c - sma) / std

        if pos is not None:
            # Check stop loss
            if direction == 'long':
                stop_hit = z <= stop_z
                profit_target = c >= sma
            else:
                stop_hit = z >= -stop_z  # symmetric
                profit_target = c <= sma
            time_out = (i - pos['entries'][0][0]) >= max_bars
            if stop_hit or profit_target or time_out:
                # Close all at C[i]
                exit_p = c
                total_units = len(pos['entries'])
                pnl_total = 0
                for eidx, ep in pos['entries']:
                    sgn = 1 if direction=='long' else -1
                    pnl_total += (exit_p - ep) * sgn
                pnl_total -= total_units * real_cost
                reason = 'STOP' if stop_hit else ('PROFIT' if profit_target else 'TIME')
                trades.append({'ts':pos['entries'][0][1] if False else arrs['TS'][pos['entries'][0][0]],
                               'pnl':pnl_total, 'units':total_units, 'reason':reason,
                               'entry_first':pos['entries'][0][1], 'exit':exit_p, 'dir':direction})
                pos = None
                continue
            # Add to position if level not hit
            if direction=='long':
                for lvl in levels:
                    if lvl not in pos['levels_hit'] and z <= lvl:
                        pos['entries'].append((i, c))
                        pos['levels_hit'].add(lvl)
                        break  # one add per bar max
            else:
                for lvl in levels:
                    if lvl not in pos['levels_hit'] and z >= -lvl:
                        pos['entries'].append((i, c))
                        pos['levels_hit'].add(lvl)
                        break

        if pos is None:
            # Initial entry at first level
            first_lvl = levels[0]
            if direction=='long' and z <= first_lvl:
                pos = {'entries':[(i, c)], 'levels_hit':{first_lvl}}
            elif direction=='short' and z >= -first_lvl:
                pos = {'entries':[(i, c)], 'levels_hit':{first_lvl}}
    return trades

def stats(trades):
    if not trades: return None
    pnls = np.array([t['pnl'] for t in trades])
    n = len(pnls); wins=(pnls>0).sum(); net=pnls.sum()
    pp=pnls[pnls>0].sum(); pl=abs(pnls[pnls<=0].sum())
    pf = pp/pl if pl else 0
    eq=np.cumsum(pnls); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    avg_units = np.mean([t['units'] for t in trades])
    stop_pct = sum(1 for t in trades if t['reason']=='STOP') / n * 100
    return {'n':n,'wr':wins/n*100,'net':net,'pf':pf,'dd':dd,
            'avg_units':avg_units,'stop_pct':stop_pct}

def fmt(s):
    if s is None: return "0 trades"
    return f"n={s['n']:>4} WR{s['wr']:.1f}% Net=${s['net']:+.0f} PF{s['pf']:.2f} DD${s['dd']:.0f} avg_u={s['avg_units']:.1f} stop%={s['stop_pct']:.0f}"

def yearly(trades):
    if not trades: return {}
    df = pd.DataFrame(trades)
    df['year'] = pd.to_datetime(df['ts']).dt.year
    out = {}
    for yr, grp in df.groupby('year'):
        pnls = grp['pnl'].values
        n = len(pnls); wins=(pnls>0).sum()
        out[yr] = {'n':n, 'wr':wins/n*100, 'net':pnls.sum(),
                   'stops':sum(1 for r in grp['reason'].values if r=='STOP')}
    return out

# Configurations
LEVELS_GRID = [
    [-1.0, -2.0],
    [-1.5, -2.5],
    [-1.0, -2.0, -3.0],
    [-1.5, -2.5, -3.5],
    [-2.0, -3.0],
]
STOP_Z_GRID = [-3.0, -4.0, -5.0]
MA_PERIODS = [50, 100, 200]

print("Aggregating data...", flush=True)
TF_DATA = {}
for asset in ['XAUUSD','EURUSD']:
    TF_DATA[asset] = {}
    for tf, rule in [('M30','30min'),('H1','1h'),('H4','4h')]:
        df_tf = aggregate(RAW[asset], rule)
        TF_DATA[asset][tf] = {}
        for ma in MA_PERIODS:
            TF_DATA[asset][tf][ma] = precompute(add_ind(df_tf, ma), ma)

print("Done. Running backtests...", flush=True)
print()

ALL = []

for asset in ['XAUUSD','EURUSD']:
    cost = REAL_COST_BY_ASSET[asset]
    print(f"\n{'#'*120}")
    print(f"# {asset} — Mean-Reversion + Averaging")
    print(f"{'#'*120}")
    for tf in ['M30','H1','H4']:
        for ma in MA_PERIODS:
            arrs = TF_DATA[asset][tf][ma]
            print(f"\n{asset} {tf} SMA{ma} ({arrs['n']} bars)", flush=True)
            for levels in LEVELS_GRID:
                for stop_z in STOP_Z_GRID:
                    if stop_z >= levels[-1]: continue  # stop must be below last entry level
                    for direction in ['long','short']:
                        trades = bt_averaging_meanrev(arrs, direction, levels, stop_z, cost)
                        s = stats(trades)
                        if s and s['n']>=10 and s['pf']>=1.10 and s['net']>0:
                            lvl_str = '/'.join(f'{l:.1f}' for l in levels)
                            print(f"  {direction:<5} levels={lvl_str:<20} stop={stop_z} | {fmt(s)}", flush=True)
                            ALL.append({'asset':asset,'tf':tf,'ma':ma,'levels':levels,
                                        'stop_z':stop_z,'direction':direction,
                                        'stats':s,'trades':trades})

# Top
print()
print("="*150)
print("TOP 30 CONFIGS — meanrev+averaging:")
print("="*150)
valid = [r for r in ALL if r['stats']['n']>=20 and r['stats']['pf']>=1.15]
valid.sort(key=lambda x:-x['stats']['pf'])
for r in valid[:30]:
    s = r['stats']
    lvl_str = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"  {r['asset']:<7} {r['tf']:<4} SMA{r['ma']:<3} {r['direction']:<5} levels={lvl_str:<20} stop={r['stop_z']} | {fmt(s)}")

# Yearly per top 5
print()
print("="*150)
print("ANY-PER-ANY top 5:")
print("="*150)
for r in valid[:5]:
    s = r['stats']
    lvl_str = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"\n{r['asset']} {r['tf']} SMA{r['ma']} {r['direction']} levels={lvl_str} stop={r['stop_z']} | TOTAL {fmt(s)}")
    yb = yearly(r['trades'])
    for yr in sorted(yb.keys()):
        y = yb[yr]
        print(f"  {yr}: n={y['n']:>3} WR{y['wr']:>5.1f}% Net=${y['net']:>+7.0f} stops={y['stops']}")

print("\nDONE")
