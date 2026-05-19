"""
HYBRID REGIME-DETECTION BACKTEST
=================================
Detecta regime del mercat i aplica estratègia diferent:
- TRENDING UP (EMA50 D1 > EMA200 D1, expanding): Donchian breakout LONG
- TRENDING DOWN (EMA50 D1 < EMA200 D1, expanding): Donchian breakout SHORT
- LATERAL (bands compressed, ADX low): Bollinger mean-reversion BOTH

Cada regime té el seu propi config optimitzat per el backtest previ.

NO LOOKAHEAD: regime detectat amb dades passades, entries amb closed bars.
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

def add_ind(df_):
    df_ = df_.copy()
    df_['ema50'] = df_['close'].ewm(span=50, adjust=False).mean()
    df_['ema200'] = df_['close'].ewm(span=200, adjust=False).mean()
    hl = df_['high']-df_['low']
    hc = (df_['high']-df_['close'].shift()).abs()
    lc = (df_['low']-df_['close'].shift()).abs()
    tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
    df_['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
    df_['sma20'] = df_['close'].rolling(20).mean()
    df_['std20'] = df_['close'].rolling(20).std()
    # ADX 14 (trend strength)
    up = df_['high'].diff()
    down = -df_['low'].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df_.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df_.index)
    atr14 = tr.ewm(alpha=1/14, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1/14, adjust=False).mean() / atr14.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1/14, adjust=False).mean() / atr14.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df_['adx'] = dx.ewm(alpha=1/14, adjust=False).mean()
    return df_

def precompute(df_):
    return {
        'O':df_['open'].values,'H':df_['high'].values,
        'L':df_['low'].values,'C':df_['close'].values,
        'EMA50':df_['ema50'].values,'EMA200':df_['ema200'].values,
        'ATR':df_['atr'].values,
        'SMA20':df_['sma20'].values,'STD20':df_['std20'].values,
        'ADX':df_['adx'].values,
        'TS':df_.index, 'YEAR':df_.index.year.values,
        'n':len(df_)
    }

def make_higher_tf_regime(arrs_low, arrs_high):
    """Per cada bar de TF baix, retorna regime del higher TF: 'TRENDING_UP', 'TRENDING_DOWN', 'LATERAL'"""
    ts_low = arrs_low['TS']
    ts_high = arrs_high['TS']
    ema50 = arrs_high['EMA50']
    ema200 = arrs_high['EMA200']
    adx = arrs_high['ADX']
    idx = np.searchsorted(ts_high, ts_low, side='right') - 1
    idx = np.clip(idx, 0, len(ts_high)-1)
    ema50_v = ema50[idx]
    ema200_v = ema200[idx]
    adx_v = adx[idx]
    # Regime classification
    bull = (ema50_v > ema200_v) & (adx_v > 25)
    bear = (ema50_v < ema200_v) & (adx_v > 25)
    lateral = adx_v < 20
    regime = np.where(bull, 'BULL', np.where(bear, 'BEAR', np.where(lateral, 'LATERAL', 'NEUTRAL')))
    return regime

def bt_hybrid(arrs_low, regime_arr, real_cost,
              donch_lookback=20, donch_sl_atr=2.0, donch_tp_atr=10.0,
              meanrev_sl_atr=2.0, meanrev_max_bars=100):
    """Hybrid:
    - BULL → Donchian breakout LONG only
    - BEAR → Donchian breakout SHORT only
    - LATERAL → Bollinger mean-rev BOTH
    """
    O=arrs_low['O'];H=arrs_low['H'];L=arrs_low['L'];C=arrs_low['C']
    EMA50=arrs_low['EMA50'];ATR=arrs_low['ATR']
    SMA20=arrs_low['SMA20'];STD20=arrs_low['STD20']
    n=arrs_low['n']; TS=arrs_low['TS']
    trades=[]; pos=None
    for i in range(50,n):
        # Manage open
        if pos is not None:
            d = pos['dir']
            if d=='long':
                sl_h = L[i] <= pos['sl']
                tp_h = H[i] >= pos['tp']
            else:
                sl_h = H[i] >= pos['sl']
                tp_h = L[i] <= pos['tp']
            time_out = (i - pos['eidx']) >= meanrev_max_bars if pos['type']=='meanrev' else False
            mean_exit = False
            if pos['type']=='meanrev':
                sma = SMA20[i]
                if not np.isnan(sma):
                    if d=='long': mean_exit = C[i] >= sma
                    else: mean_exit = C[i] <= sma
            if sl_h or tp_h or mean_exit or time_out:
                if sl_h:
                    exit_p = pos['sl']; reason='SL'
                elif tp_h:
                    exit_p = pos['tp']; reason='TP'
                elif mean_exit:
                    exit_p = C[i]; reason='MEAN'
                else:
                    exit_p = C[i]; reason='TIME'
                sgn = 1 if d=='long' else -1
                pnl = (exit_p - pos['entry']) * sgn - real_cost
                trades.append({'ts':TS[pos['eidx']],'pnl':pnl,'type':pos['type'],
                               'dir':d,'reason':reason,'regime':pos['regime']})
                pos=None
        # Check entry
        if pos is None and i >= 50:
            regime = regime_arr[i]
            atr = ATR[i]; ema = EMA50[i]
            if np.isnan(atr) or atr<=0 or np.isnan(ema): continue

            if regime == 'BULL':
                # Donchian LONG breakout
                if i >= donch_lookback:
                    ph = H[i-donch_lookback:i].max()
                    pl_ = L[i-donch_lookback:i].min()
                    if C[i] > ph and C[i] > O[i] and C[i] > ema:
                        sl = pl_ - atr * donch_sl_atr * 0.3
                        tp = C[i] + atr * donch_tp_atr
                        pos = {'sl':sl,'tp':tp,'entry':C[i],'eidx':i,
                               'dir':'long','type':'donchian','regime':regime}
            elif regime == 'BEAR':
                # Donchian SHORT breakout
                if i >= donch_lookback:
                    ph = H[i-donch_lookback:i].max()
                    pl_ = L[i-donch_lookback:i].min()
                    if C[i] < pl_ and C[i] < O[i] and C[i] < ema:
                        sl = ph + atr * donch_sl_atr * 0.3
                        tp = C[i] - atr * donch_tp_atr
                        pos = {'sl':sl,'tp':tp,'entry':C[i],'eidx':i,
                               'dir':'short','type':'donchian','regime':regime}
            elif regime == 'LATERAL':
                # Bollinger mean-rev BOTH
                sma = SMA20[i]; std = STD20[i]
                if np.isnan(sma) or np.isnan(std) or std<=0: continue
                lower = sma - 2.0*std; upper = sma + 2.0*std
                if C[i] <= lower and C[i] > C[i-1]:  # bullish reversal at lower band
                    sl = C[i] - meanrev_sl_atr * atr
                    tp = sma  # exit at mean
                    pos = {'sl':sl,'tp':tp,'entry':C[i],'eidx':i,
                           'dir':'long','type':'meanrev','regime':regime}
                elif C[i] >= upper and C[i] < C[i-1]:
                    sl = C[i] + meanrev_sl_atr * atr
                    tp = sma
                    pos = {'sl':sl,'tp':tp,'entry':C[i],'eidx':i,
                           'dir':'short','type':'meanrev','regime':regime}
    return trades

def stats(trades):
    if not trades: return None
    pnls = np.array([t['pnl'] for t in trades])
    n = len(pnls); wins=(pnls>0).sum(); net=pnls.sum()
    pp=pnls[pnls>0].sum(); pl=abs(pnls[pnls<=0].sum())
    pf = pp/pl if pl else 0
    eq=np.cumsum(pnls); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    return {'n':n,'wr':wins/n*100,'net':net,'pf':pf,'dd':dd}

def fmt(s):
    if s is None: return "0 trades"
    return f"n={s['n']:>4} WR{s['wr']:.1f}% Net=${s['net']:+.0f} PF{s['pf']:.2f} DD${s['dd']:.0f}"

def per_type(trades):
    out = {}
    df = pd.DataFrame(trades)
    if len(df)==0: return out
    for t, grp in df.groupby('type'):
        pnls = grp['pnl'].values
        n=len(pnls); wins=(pnls>0).sum()
        out[t] = {'n':n,'wr':wins/n*100,'net':pnls.sum(),
                  'pf':pnls[pnls>0].sum()/(abs(pnls[pnls<=0].sum()) or 1)}
    return out

def per_year(trades):
    out = {}
    df = pd.DataFrame(trades)
    if len(df)==0: return out
    df['year'] = pd.to_datetime(df['ts']).dt.year
    for yr, grp in df.groupby('year'):
        pnls = grp['pnl'].values
        n=len(pnls); wins=(pnls>0).sum()
        out[yr] = {'n':n,'wr':wins/n*100,'net':pnls.sum(),
                   'donch':sum(1 for t in grp['type'].values if t=='donchian'),
                   'meanrev':sum(1 for t in grp['type'].values if t=='meanrev')}
    return out

# Build regime lookup using H4/D1 trend on M30/H1 entries
print("Aggregating + indicators...", flush=True)
TF_DATA = {}
for asset in ['XAUUSD','EURUSD']:
    TF_DATA[asset] = {}
    for tf, rule in [('M30','30min'),('H1','1h'),('H4','4h'),('D1','1D')]:
        df = aggregate(RAW[asset], rule)
        TF_DATA[asset][tf] = precompute(add_ind(df))

print("Running hybrid backtests...", flush=True)
print()

ALL = []

for asset in ['XAUUSD','EURUSD']:
    cost = REAL_COST_BY_ASSET[asset]
    print(f"\n{'#'*120}")
    print(f"# {asset} HYBRID (regime-based)")
    print(f"{'#'*120}")
    for entry_tf, regime_tf in [('M30','H4'),('M30','D1'),('H1','H4'),('H1','D1'),('H4','D1')]:
        arrs_low = TF_DATA[asset][entry_tf]
        arrs_high = TF_DATA[asset][regime_tf]
        regime = make_higher_tf_regime(arrs_low, arrs_high)
        # Show regime distribution
        n_bull = (regime=='BULL').sum()
        n_bear = (regime=='BEAR').sum()
        n_lat = (regime=='LATERAL').sum()
        n_neu = (regime=='NEUTRAL').sum()
        print(f"\n{asset} entry={entry_tf} regime_tf={regime_tf}")
        print(f"  Regime distribution: BULL={n_bull} BEAR={n_bear} LATERAL={n_lat} NEUTRAL={n_neu}")
        for donch_lb in [10, 20]:
            for donch_sl in [1.5, 2.0]:
                for mr_sl in [2.0, 3.0]:
                    trades = bt_hybrid(arrs_low, regime, cost,
                                       donch_lookback=donch_lb,
                                       donch_sl_atr=donch_sl,
                                       donch_tp_atr=10.0,
                                       meanrev_sl_atr=mr_sl,
                                       meanrev_max_bars=100)
                    s = stats(trades)
                    if s and s['n']>=20:
                        if s['pf']>=1.10 and s['net']>0:
                            print(f"  donch_lb={donch_lb} donch_sl={donch_sl} mr_sl={mr_sl} | {fmt(s)}")
                            ALL.append({'asset':asset,'entry_tf':entry_tf,'regime_tf':regime_tf,
                                        'donch_lb':donch_lb,'donch_sl':donch_sl,'mr_sl':mr_sl,
                                        'stats':s,'trades':trades})

# Top
print()
print("="*150)
print("TOP HYBRID CONFIGS:")
print("="*150)
valid = [r for r in ALL if r['stats']['n']>=30 and r['stats']['pf']>=1.15]
valid.sort(key=lambda x:-x['stats']['pf'])
for r in valid[:20]:
    print(f"  {r['asset']:<7} {r['entry_tf']:<4}/{r['regime_tf']:<3} "
          f"donch={r['donch_lb']}/{r['donch_sl']} mr={r['mr_sl']} | {fmt(r['stats'])}")

# Per type breakdown
print()
print("="*150)
print("BREAKDOWN PER TYPE (top 5):")
print("="*150)
for r in valid[:5]:
    print(f"\n{r['asset']} {r['entry_tf']}/{r['regime_tf']} donch={r['donch_lb']}/{r['donch_sl']} mr={r['mr_sl']}")
    print(f"  TOTAL: {fmt(r['stats'])}")
    pt = per_type(r['trades'])
    for t, p in pt.items():
        print(f"    {t}: n={p['n']:>3} WR{p['wr']:>5.1f}% Net=${p['net']:>+7.0f} PF{p['pf']:.2f}")

# Yearly breakdown
print()
print("="*150)
print("ANY-PER-ANY top 3:")
print("="*150)
for r in valid[:3]:
    print(f"\n{r['asset']} {r['entry_tf']}/{r['regime_tf']} donch={r['donch_lb']}/{r['donch_sl']} mr={r['mr_sl']}")
    yb = per_year(r['trades'])
    for yr in sorted(yb.keys()):
        y = yb[yr]
        print(f"  {yr}: n={y['n']:>3} WR{y['wr']:>5.1f}% Net=${y['net']:>+7.0f} (donch:{y['donch']} meanrev:{y['meanrev']})")

print("\nDONE")
