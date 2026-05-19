"""
BACKTEST EXHAUSTIU MEAN-REVERSION
==================================
Estratègies mean-reversion sobre XAUUSD + EURUSD, multi-TF, multi-config.

Estratègies:
1. Bollinger Bands Reversion (period 20/50, k 2.0/2.5)
2. Keltner Channel Reversion (ATR-based)
3. Z-score Reversion (threshold ±2/±2.5)
4. RSI Extreme Reversion (zone-end RSI <25/>75)
5. Bollinger + Trend filter (EMA200) — direcció amb el trend macro

NO LOOKAHEAD: tots els indicadors només amb closed bars.
Entry/exit a C[i] (close del bar de senyal).
Anàlisi any per any per cada millor config.
"""
import pandas as pd
import numpy as np
import time

REAL_COST = 0.40

# =====================================================================
# Loading data
# =====================================================================
ASSETS = {
    'XAUUSD': 'xauusd_m5_5y.csv',
    'EURUSD': 'eurusd_m5_5y.csv',
}

# Pip values per asset (cost adjustment)
# XAUUSD: $0.40 RT cost / 0.01 lot
# EURUSD: $0.07 RT cost / 0.01 lot (typical)
COST_BY_ASSET = {'XAUUSD': 0.40, 'EURUSD': 0.07}

print("Loading data...", flush=True)
RAW = {}
for asset, csv in ASSETS.items():
    df = pd.read_csv(csv, index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]
    RAW[asset] = df
    print(f"  {asset}: {len(df)} bars from {df.index[0].date()} to {df.index[-1].date()}", flush=True)

def aggregate(df_, rule):
    return df_.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last')).dropna()

def add_indicators(df_):
    df_ = df_.copy()
    # Trend MAs
    df_['ema50'] = df_['close'].ewm(span=50, adjust=False).mean()
    df_['ema200'] = df_['close'].ewm(span=200, adjust=False).mean()
    # ATR
    hl = df_['high']-df_['low']
    hc = (df_['high']-df_['close'].shift()).abs()
    lc = (df_['low']-df_['close'].shift()).abs()
    tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
    df_['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
    # Bollinger 20-2
    df_['sma20'] = df_['close'].rolling(20).mean()
    df_['std20'] = df_['close'].rolling(20).std()
    # Bollinger 50-2
    df_['sma50'] = df_['close'].rolling(50).mean()
    df_['std50'] = df_['close'].rolling(50).std()
    # RSI 14 (Wilder approximation)
    delta = df_['close'].diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta).clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df_['rsi14'] = 100 - 100/(1+rs)
    return df_

print("Aggregating + computing indicators...", flush=True)
DATA = {}
for asset, df in RAW.items():
    DATA[asset] = {
        'M15': add_indicators(aggregate(df, '15min')),
        'M30': add_indicators(aggregate(df, '30min')),
        'H1':  add_indicators(aggregate(df, '1h')),
        'H4':  add_indicators(aggregate(df, '4h')),
    }
    for tf, d in DATA[asset].items():
        print(f"  {asset} {tf}: {len(d)} bars", flush=True)

def precompute(df_):
    return {
        'O':df_['open'].values, 'H':df_['high'].values,
        'L':df_['low'].values, 'C':df_['close'].values,
        'EMA50':df_['ema50'].values, 'EMA200':df_['ema200'].values,
        'ATR':df_['atr'].values,
        'SMA20':df_['sma20'].values, 'STD20':df_['std20'].values,
        'SMA50':df_['sma50'].values, 'STD50':df_['std50'].values,
        'RSI':df_['rsi14'].values,
        'TS':df_.index, 'YEAR':df_.index.year.values,
        'n':len(df_)
    }

ARRS = {}
for asset, tfs in DATA.items():
    ARRS[asset] = {tf: precompute(d) for tf, d in tfs.items()}

# =====================================================================
# Backtest engine — reversion (entry on band touch, exit at mean)
# =====================================================================
def bt_reversion(arrs, direction, entry_check, exit_check, sl_atr, real_cost,
                 use_trend_filter=False, max_bars=200):
    """
    entry_check(arrs, i, direction) -> True if entry valid at bar i
    exit_check(arrs, i, direction, entry_idx, entry_price) -> True if should exit
    sl_atr: hard stop loss multiple of ATR from entry
    use_trend_filter: only enter LONG if EMA50 > EMA200 (or short if opposite)
    max_bars: time stop (bars since entry)
    """
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    EMA50=arrs['EMA50'];EMA200=arrs['EMA200'];ATR=arrs['ATR']
    n=arrs['n']
    trades=[]; pos=None
    for i in range(50,n):
        # Manage open pos
        if pos is not None:
            # Check stop loss first
            if direction=='long':
                sl_h = L[i] <= pos['sl']
            else:
                sl_h = H[i] >= pos['sl']
            time_out = (i - pos['entry_idx']) >= max_bars
            mean_exit = exit_check(arrs, i, direction, pos['entry_idx'], pos['entry'])
            if sl_h or mean_exit or time_out:
                if sl_h:
                    exit_p = pos['sl']
                    reason = 'SL'
                elif mean_exit:
                    exit_p = C[i]
                    reason = 'MEAN'
                else:
                    exit_p = C[i]
                    reason = 'TIME'
                sgn = 1 if direction=='long' else -1
                pnl = (exit_p - pos['entry']) * sgn - real_cost
                trades.append({'ts':pos['ts'],'pnl':pnl,'entry':pos['entry'],
                               'exit':exit_p,'reason':reason,'dir':direction})
                pos = None
        # New entry
        if pos is None:
            if entry_check(arrs, i, direction):
                if use_trend_filter:
                    if direction=='long' and EMA50[i] < EMA200[i]: continue
                    if direction=='short' and EMA50[i] > EMA200[i]: continue
                atr = ATR[i]
                if np.isnan(atr) or atr <= 0: continue
                e = C[i]
                if direction=='long':
                    sl = e - sl_atr * atr
                else:
                    sl = e + sl_atr * atr
                pos = {'sl':sl, 'entry':e, 'entry_idx':i, 'ts':arrs['TS'][i]}
    return trades

# Entry/exit helpers — all NO LOOKAHEAD (use past values only, decisions at bar i close)
def entry_bb20(arrs, i, direction):
    sma=arrs['SMA20'][i]; std=arrs['STD20'][i]; c=arrs['C'][i]; cp=arrs['C'][i-1]
    if np.isnan(sma) or np.isnan(std) or std<=0: return False
    lower = sma - 2.0*std; upper = sma + 2.0*std
    if direction=='long':
        return c <= lower and c > cp
    else:
        return c >= upper and c < cp

def entry_bb50(arrs, i, direction):
    sma=arrs['SMA50'][i]; std=arrs['STD50'][i]; c=arrs['C'][i]; cp=arrs['C'][i-1]
    if np.isnan(sma) or np.isnan(std) or std<=0: return False
    lower = sma - 2.0*std; upper = sma + 2.0*std
    if direction=='long':
        return c <= lower and c > cp
    else:
        return c >= upper and c < cp

def entry_bb20_25(arrs, i, direction):
    sma=arrs['SMA20'][i]; std=arrs['STD20'][i]; c=arrs['C'][i]; cp=arrs['C'][i-1]
    if np.isnan(sma) or np.isnan(std) or std<=0: return False
    lower = sma - 2.5*std; upper = sma + 2.5*std
    if direction=='long':
        return c <= lower and c > cp
    else:
        return c >= upper and c < cp

def entry_keltner(arrs, i, direction):
    ema=arrs['EMA50'][i]; atr=arrs['ATR'][i]; c=arrs['C'][i]; cp=arrs['C'][i-1]
    if np.isnan(ema) or np.isnan(atr) or atr<=0: return False
    lower = ema - 2.0*atr; upper = ema + 2.0*atr
    if direction=='long':
        return c <= lower and c > cp
    else:
        return c >= upper and c < cp

def entry_keltner_25(arrs, i, direction):
    ema=arrs['EMA50'][i]; atr=arrs['ATR'][i]; c=arrs['C'][i]; cp=arrs['C'][i-1]
    if np.isnan(ema) or np.isnan(atr) or atr<=0: return False
    lower = ema - 2.5*atr; upper = ema + 2.5*atr
    if direction=='long':
        return c <= lower and c > cp
    else:
        return c >= upper and c < cp

def entry_zscore50(arrs, i, direction):
    sma=arrs['SMA50'][i]; std=arrs['STD50'][i]; c=arrs['C'][i]; cp=arrs['C'][i-1]
    if np.isnan(sma) or np.isnan(std) or std<=0: return False
    z = (c-sma)/std
    if direction=='long':
        return z <= -2.0 and c > cp
    else:
        return z >= 2.0 and c < cp

def entry_zscore50_25(arrs, i, direction):
    sma=arrs['SMA50'][i]; std=arrs['STD50'][i]; c=arrs['C'][i]; cp=arrs['C'][i-1]
    if np.isnan(sma) or np.isnan(std) or std<=0: return False
    z = (c-sma)/std
    if direction=='long':
        return z <= -2.5 and c > cp
    else:
        return z >= 2.5 and c < cp

def entry_rsi(arrs, i, direction):
    rsi=arrs['RSI'][i]; rp=arrs['RSI'][i-1]; c=arrs['C'][i]; cp=arrs['C'][i-1]
    if np.isnan(rsi) or np.isnan(rp): return False
    if direction=='long':
        return rp < 25 and rsi >= 25 and c > cp
    else:
        return rp > 75 and rsi <= 75 and c < cp

def entry_rsi_extreme(arrs, i, direction):
    """RSI<20 / >80 — més extrem"""
    rsi=arrs['RSI'][i]; rp=arrs['RSI'][i-1]; c=arrs['C'][i]; cp=arrs['C'][i-1]
    if np.isnan(rsi) or np.isnan(rp): return False
    if direction=='long':
        return rp < 20 and rsi >= 20 and c > cp
    else:
        return rp > 80 and rsi <= 80 and c < cp

# Exit functions
def exit_at_sma20(arrs, i, direction, eidx, ep):
    sma=arrs['SMA20'][i]
    if np.isnan(sma): return False
    if direction=='long':
        return arrs['C'][i] >= sma
    else:
        return arrs['C'][i] <= sma

def exit_at_sma50(arrs, i, direction, eidx, ep):
    sma=arrs['SMA50'][i]
    if np.isnan(sma): return False
    if direction=='long':
        return arrs['C'][i] >= sma
    else:
        return arrs['C'][i] <= sma

def exit_at_ema50(arrs, i, direction, eidx, ep):
    ema=arrs['EMA50'][i]
    if np.isnan(ema): return False
    if direction=='long':
        return arrs['C'][i] >= ema
    else:
        return arrs['C'][i] <= ema

def exit_at_rsi50(arrs, i, direction, eidx, ep):
    rsi=arrs['RSI'][i]
    if np.isnan(rsi): return False
    if direction=='long':
        return rsi >= 50
    else:
        return rsi <= 50

# =====================================================================
# Stats helpers
# =====================================================================
def compute_stats(trades):
    if not trades: return None
    pnls = np.array([t['pnl'] for t in trades])
    n = len(pnls)
    wins = (pnls>0).sum()
    net = pnls.sum()
    pp = pnls[pnls>0].sum()
    pl = abs(pnls[pnls<=0].sum())
    pf = pp/pl if pl else 0
    eq = np.cumsum(pnls)
    peak = np.maximum.accumulate(eq)
    dd = (peak-eq).max() if len(eq) else 0
    return {'n':n,'wr':wins/n*100,'net':net,'pf':pf,'dd':dd}

def fmt(s):
    if s is None or s['n']<10: return f"n={s['n'] if s else 0} (insuf)"
    return f"n={s['n']:>4} WR{s['wr']:.1f}% Net=${s['net']:+.0f} PF{s['pf']:.2f} DD${s['dd']:.0f}"

def yearly_breakdown(trades):
    if not trades: return {}
    df = pd.DataFrame(trades)
    df['year'] = pd.to_datetime(df['ts']).dt.year
    out = {}
    for yr, grp in df.groupby('year'):
        pnls = grp['pnl'].values
        n = len(pnls)
        wins = (pnls>0).sum()
        out[yr] = {'n':n,'wr':wins/n*100,'net':pnls.sum(),
                   'pf': pnls[pnls>0].sum()/(abs(pnls[pnls<=0].sum()) or 1)}
    return out

# =====================================================================
# Run all combinations
# =====================================================================
STRATS = {
    'BB20-2.0': (entry_bb20, exit_at_sma20),
    'BB20-2.5': (entry_bb20_25, exit_at_sma20),
    'BB50-2.0': (entry_bb50, exit_at_sma50),
    'KC-EMA50-2.0': (entry_keltner, exit_at_ema50),
    'KC-EMA50-2.5': (entry_keltner_25, exit_at_ema50),
    'Z50-2.0': (entry_zscore50, exit_at_sma50),
    'Z50-2.5': (entry_zscore50_25, exit_at_sma50),
    'RSI-25/75': (entry_rsi, exit_at_rsi50),
    'RSI-20/80': (entry_rsi_extreme, exit_at_rsi50),
}

SL_GRID = [1.5, 2.0, 3.0]

print()
print("="*150)
print("RUNNING ALL STRATEGIES × TFs × CONFIGS:")
print("="*150)

ALL_RESULTS = []

for asset in ['XAUUSD','EURUSD']:
    cost = COST_BY_ASSET[asset]
    print(f"\n{'#'*120}")
    print(f"# {asset} (cost ${cost}/RT)")
    print(f"{'#'*120}")
    for tf in ['M15','M30','H1','H4']:
        arrs = ARRS[asset][tf]
        print(f"\n--- {asset} {tf} ({arrs['n']} bars) ---", flush=True)
        for strat_name, (e_fn, x_fn) in STRATS.items():
            for sl_atr in SL_GRID:
                # Both directions
                for direction in ['long','short']:
                    trades = bt_reversion(arrs, direction, e_fn, x_fn, sl_atr, cost,
                                          use_trend_filter=False, max_bars=200)
                    s = compute_stats(trades)
                    if s and s['n']>=10:
                        ALL_RESULTS.append({
                            'asset':asset,'tf':tf,'strat':strat_name,
                            'direction':direction,'sl_atr':sl_atr,
                            'trend_filter':False,'stats':s,'trades':trades
                        })
                        if s['pf'] >= 1.10 and s['n']>=20 and s['net']>0:
                            print(f"  {strat_name:<14} {direction:<5} SL{sl_atr} | {fmt(s)}", flush=True)

print()
print("="*150)
print("AMB TREND FILTER (EMA50 vs EMA200) — direcció amb macro-trend:")
print("="*150)
for asset in ['XAUUSD','EURUSD']:
    cost = COST_BY_ASSET[asset]
    print(f"\n--- {asset} amb trend filter ---")
    for tf in ['M15','M30','H1','H4']:
        arrs = ARRS[asset][tf]
        for strat_name, (e_fn, x_fn) in STRATS.items():
            for sl_atr in [2.0]:
                for direction in ['long','short']:
                    trades = bt_reversion(arrs, direction, e_fn, x_fn, sl_atr, cost,
                                          use_trend_filter=True, max_bars=200)
                    s = compute_stats(trades)
                    if s and s['n']>=10:
                        ALL_RESULTS.append({
                            'asset':asset,'tf':tf,'strat':strat_name,
                            'direction':direction,'sl_atr':sl_atr,
                            'trend_filter':True,'stats':s,'trades':trades
                        })
                        if s['pf']>=1.15 and s['n']>=20 and s['net']>0:
                            print(f"  {tf} {strat_name:<14} {direction:<5} SL{sl_atr} TF=True | {fmt(s)}", flush=True)

# =====================================================================
# TOP CONFIGS
# =====================================================================
print()
print("="*150)
print(f"TOP 30 CONFIGS GLOBALS (PF >=1.15, n>=30, Net positive):")
print("="*150)
valid = [r for r in ALL_RESULTS
         if r['stats']['n']>=30 and r['stats']['pf']>=1.15 and r['stats']['net']>0]
valid.sort(key=lambda x:-x['stats']['pf'])
for r in valid[:30]:
    s = r['stats']
    tf_flag = '+TF' if r['trend_filter'] else ''
    print(f"  {r['asset']:<7} {r['tf']:<4} {r['strat']:<14} {r['direction']:<5} SL{r['sl_atr']}{tf_flag:<3} | {fmt(s)}")

# =====================================================================
# Top per asset
# =====================================================================
print()
print("="*150)
print("MILLOR CONFIG PER ASSET + TF:")
print("="*150)
for asset in ['XAUUSD','EURUSD']:
    print(f"\n{asset}:")
    for tf in ['M15','M30','H1','H4']:
        sub = [r for r in ALL_RESULTS if r['asset']==asset and r['tf']==tf
               and r['stats']['n']>=20 and r['stats']['net']>0 and r['stats']['pf']>=1.10]
        if sub:
            best = max(sub, key=lambda x: x['stats']['pf'])
            s = best['stats']
            tf_flag = '+TF' if best['trend_filter'] else ''
            print(f"  {tf} BEST: {best['strat']:<14} {best['direction']:<5} SL{best['sl_atr']}{tf_flag} | {fmt(s)}")
        else:
            print(f"  {tf}: cap edge sòlid")

# =====================================================================
# Yearly breakdown of top configs
# =====================================================================
print()
print("="*150)
print(f"ANÀLISI ANY-PER-ANY — top 5 estratègies (per detectar dependència de bull):")
print("="*150)
for r in valid[:5]:
    s = r['stats']
    tf_flag = '+TF' if r['trend_filter'] else ''
    print(f"\n{r['asset']} {r['tf']} {r['strat']} {r['direction']} SL{r['sl_atr']}{tf_flag} | TOTAL: {fmt(s)}")
    yb = yearly_breakdown(r['trades'])
    for yr in sorted(yb.keys()):
        y = yb[yr]
        print(f"  {yr}: n={y['n']:>3} WR{y['wr']:>5.1f}% Net=${y['net']:>+7.0f} PF{y['pf']:.2f}")

print("\nDONE")
