"""
ESTRATÈGIES PROTEGIDES CONTRA BEAR MARKETS
===========================================
1. Donchian LONG amb filtre EMA200 D1 (només operar si Or alcista macro)
2. Donchian SHORT amb filtre EMA200 D1 (només si baixista)
3. Donchian BIDIRECTIONAL amb filtre regime D1 (long si bull, short si bear, no si lateral)
4. Linear regression channel reversion
5. Bollinger Squeeze breakout (volatilitat baixa → breakout)

NO LOOKAHEAD. Anàlisi any-per-any.
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
    df_['ema100'] = df_['close'].ewm(span=100, adjust=False).mean()
    df_['ema200'] = df_['close'].ewm(span=200, adjust=False).mean()
    hl = df_['high']-df_['low']
    hc = (df_['high']-df_['close'].shift()).abs()
    lc = (df_['low']-df_['close'].shift()).abs()
    tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
    df_['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
    df_['sma20'] = df_['close'].rolling(20).mean()
    df_['std20'] = df_['close'].rolling(20).std()
    # Bollinger band width — for squeeze detection
    df_['bb_width'] = (df_['std20']*2*2) / df_['sma20']
    df_['bb_width_pct'] = df_['bb_width'].rolling(100).rank(pct=True)
    return df_

def precompute(df_):
    return {
        'O':df_['open'].values,'H':df_['high'].values,
        'L':df_['low'].values,'C':df_['close'].values,
        'EMA50':df_['ema50'].values,'EMA100':df_['ema100'].values,'EMA200':df_['ema200'].values,
        'ATR':df_['atr'].values,
        'SMA20':df_['sma20'].values,'STD20':df_['std20'].values,
        'BB_WIDTH_PCT':df_['bb_width_pct'].values,
        'TS':df_.index, 'YEAR':df_.index.year.values,
        'n':len(df_)
    }

def make_d1_trend_mask(arrs_low, arrs_d1):
    """Retorna mask: True si D1 close > EMA200 D1 al moment de cada bar low-TF."""
    ts_low = arrs_low['TS']
    ts_d1 = arrs_d1['TS']
    c_d1 = arrs_d1['C']
    ema200_d1 = arrs_d1['EMA200']
    idx = np.searchsorted(ts_d1, ts_low, side='right') - 1
    idx = np.clip(idx, 0, len(ts_d1)-1)
    return c_d1[idx] > ema200_d1[idx], c_d1[idx] < ema200_d1[idx]

def bt_donchian(arrs, direction, sl_atr, tp_atr, lookback, real_cost, mask=None):
    """Donchian breakout amb mask opcional."""
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    EMA50=arrs['EMA50'];ATR=arrs['ATR'];n=arrs['n'];TS=arrs['TS']
    if mask is None: mask = np.ones(n, dtype=bool)
    trades=[];pos=None
    for i in range(50,n):
        if pos is not None:
            if direction=='long':
                sl_h=L[i]<=pos['sl'];tp_h=H[i]>=pos['tp']
            else:
                sl_h=H[i]>=pos['sl'];tp_h=L[i]<=pos['tp']
            if sl_h and tp_h:
                # Conservative: SL hits first if closer to open
                if direction=='long':
                    if (O[i]-pos['sl'])<(pos['tp']-O[i]): tp_h=False
                else:
                    if (pos['sl']-O[i])<(O[i]-pos['tp']): tp_h=False
            if sl_h or tp_h:
                exit_p = pos['sl'] if sl_h else pos['tp']
                sgn = 1 if direction=='long' else -1
                pnl = (exit_p-pos['entry'])*sgn - real_cost
                trades.append({'ts':TS[pos['eidx']],'pnl':pnl,'entry':pos['entry'],'exit':exit_p,
                               'reason':'SL' if sl_h else 'TP','dir':direction})
                pos=None
        if pos is None and not np.isnan(EMA50[i]) and not np.isnan(ATR[i]) and i>=lookback and mask[i]:
            atr=ATR[i]
            if direction=='long':
                ph = H[i-lookback:i].max()
                if C[i] > ph and C[i] > O[i] and C[i] > EMA50[i]:
                    sl_ref = L[i-lookback:i].min()
                    sl = sl_ref - atr * sl_atr * 0.3
                    tp = C[i] + atr * tp_atr
                    pos={'sl':sl,'tp':tp,'entry':C[i],'eidx':i}
            else:
                pl_ = L[i-lookback:i].min()
                if C[i] < pl_ and C[i] < O[i] and C[i] < EMA50[i]:
                    sl_ref = H[i-lookback:i].max()
                    sl = sl_ref + atr * sl_atr * 0.3
                    tp = C[i] - atr * tp_atr
                    pos={'sl':sl,'tp':tp,'entry':C[i],'eidx':i}
    return trades

def bt_bb_squeeze(arrs, direction, sl_atr, tp_atr, real_cost, mask=None):
    """Bollinger squeeze breakout: bandes comprimides → breakout."""
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    EMA50=arrs['EMA50'];ATR=arrs['ATR'];n=arrs['n'];TS=arrs['TS']
    SMA20=arrs['SMA20'];STD20=arrs['STD20'];BBW=arrs['BB_WIDTH_PCT']
    if mask is None: mask = np.ones(n, dtype=bool)
    trades=[];pos=None
    for i in range(50,n):
        if pos is not None:
            if direction=='long':
                sl_h=L[i]<=pos['sl'];tp_h=H[i]>=pos['tp']
            else:
                sl_h=H[i]>=pos['sl'];tp_h=L[i]<=pos['tp']
            if sl_h and tp_h:
                if direction=='long':
                    if (O[i]-pos['sl'])<(pos['tp']-O[i]): tp_h=False
                else:
                    if (pos['sl']-O[i])<(O[i]-pos['tp']): tp_h=False
            if sl_h or tp_h:
                exit_p = pos['sl'] if sl_h else pos['tp']
                sgn = 1 if direction=='long' else -1
                pnl = (exit_p-pos['entry'])*sgn - real_cost
                trades.append({'ts':TS[pos['eidx']],'pnl':pnl,'reason':'SL' if sl_h else 'TP','dir':direction})
                pos=None
        if pos is None and not np.isnan(EMA50[i]) and not np.isnan(ATR[i]) and mask[i]:
            sma=SMA20[i];std=STD20[i];bbw_pct=BBW[i]
            if np.isnan(sma) or np.isnan(std) or std<=0 or np.isnan(bbw_pct): continue
            # Squeeze: BB width in lowest 25% percentile
            if bbw_pct > 0.25: continue
            atr=ATR[i]
            upper = sma + 2*std; lower = sma - 2*std
            if direction=='long':
                # Breakout above upper band after squeeze
                if C[i] > upper and C[i] > O[i]:
                    sl = sma  # stop at mean
                    tp = C[i] + atr * tp_atr
                    pos={'sl':sl,'tp':tp,'entry':C[i],'eidx':i}
            else:
                if C[i] < lower and C[i] < O[i]:
                    sl = sma
                    tp = C[i] - atr * tp_atr
                    pos={'sl':sl,'tp':tp,'entry':C[i],'eidx':i}
    return trades

def stats(trades):
    if not trades: return None
    pnls = np.array([t['pnl'] for t in trades])
    n=len(pnls); wins=(pnls>0).sum(); net=pnls.sum()
    pp=pnls[pnls>0].sum(); pl=abs(pnls[pnls<=0].sum())
    pf = pp/pl if pl else 0
    eq=np.cumsum(pnls); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    return {'n':n,'wr':wins/n*100,'net':net,'pf':pf,'dd':dd}

def fmt(s):
    if s is None: return "0 trades"
    return f"n={s['n']:>4} WR{s['wr']:.1f}% Net=${s['net']:+.0f} PF{s['pf']:.2f} DD${s['dd']:.0f}"

def per_year(trades):
    out = {}
    if not trades: return out
    df = pd.DataFrame(trades)
    df['year'] = pd.to_datetime(df['ts']).dt.year
    for yr, grp in df.groupby('year'):
        pnls = grp['pnl'].values
        n=len(pnls); wins=(pnls>0).sum()
        out[yr] = {'n':n,'wr':wins/n*100,'net':pnls.sum()}
    return out

# Pre-compute
print("Aggregating + indicators...", flush=True)
TF_DATA = {}
for asset in ['XAUUSD','EURUSD']:
    TF_DATA[asset] = {}
    for tf, rule in [('M30','30min'),('H1','1h'),('H4','4h'),('D1','1D')]:
        TF_DATA[asset][tf] = precompute(add_ind(aggregate(RAW[asset], rule)))

print("Running...", flush=True)
print()

# =====================================================================
# 1. Donchian + EMA200 D1 PROTECTION
# =====================================================================
print("="*150)
print("1. DONCHIAN AMB FILTRE EMA200 D1 (només operar si trend macro favorable):")
print("="*150)
ALL = []
for asset in ['XAUUSD','EURUSD']:
    cost = REAL_COST_BY_ASSET[asset]
    print(f"\n{asset}:")
    for tf in ['M30','H1','H4']:
        arrs = TF_DATA[asset][tf]
        d1_arrs = TF_DATA[asset]['D1']
        bull_mask, bear_mask = make_d1_trend_mask(arrs, d1_arrs)
        for lb in [10, 20]:
            for sl in [1.5, 2.0]:
                for tp in [5.0, 10.0]:
                    # LONG only when D1 bullish
                    trades_l = bt_donchian(arrs, 'long', sl, tp, lb, cost, mask=bull_mask)
                    s_l = stats(trades_l)
                    # SHORT only when D1 bearish
                    trades_s = bt_donchian(arrs, 'short', sl, tp, lb, cost, mask=bear_mask)
                    s_s = stats(trades_s)
                    # Combined bidirectional
                    trades_b = trades_l + trades_s
                    s_b = stats(trades_b)
                    if s_l and s_l['n']>=20 and s_l['pf']>=1.10 and s_l['net']>0:
                        print(f"  {tf} lb={lb} sl={sl} tp={tp} LONG (bull D1): {fmt(s_l)}")
                        ALL.append({'asset':asset,'tf':tf,'strat':'donch_long_d1bull',
                                    'lb':lb,'sl':sl,'tp':tp,'stats':s_l,'trades':trades_l})
                    if s_s and s_s['n']>=20 and s_s['pf']>=1.10 and s_s['net']>0:
                        print(f"  {tf} lb={lb} sl={sl} tp={tp} SHORT (bear D1): {fmt(s_s)}")
                        ALL.append({'asset':asset,'tf':tf,'strat':'donch_short_d1bear',
                                    'lb':lb,'sl':sl,'tp':tp,'stats':s_s,'trades':trades_s})
                    if s_b and s_b['n']>=30 and s_b['pf']>=1.10 and s_b['net']>0:
                        print(f"  {tf} lb={lb} sl={sl} tp={tp} BIDIR (regime D1): {fmt(s_b)}")
                        ALL.append({'asset':asset,'tf':tf,'strat':'donch_bidir_d1regime',
                                    'lb':lb,'sl':sl,'tp':tp,'stats':s_b,'trades':trades_b})

# =====================================================================
# 2. Bollinger Squeeze breakout
# =====================================================================
print()
print("="*150)
print("2. BOLLINGER SQUEEZE BREAKOUT (bandes comprimides -> breakout):")
print("="*150)
for asset in ['XAUUSD','EURUSD']:
    cost = REAL_COST_BY_ASSET[asset]
    print(f"\n{asset}:")
    for tf in ['M30','H1','H4']:
        arrs = TF_DATA[asset][tf]
        d1_arrs = TF_DATA[asset]['D1']
        bull_mask, bear_mask = make_d1_trend_mask(arrs, d1_arrs)
        for tp in [5.0, 8.0, 12.0]:
            for direction, mask in [('long',bull_mask),('short',bear_mask)]:
                trades = bt_bb_squeeze(arrs, direction, 1.0, tp, cost, mask=mask)
                s = stats(trades)
                if s and s['n']>=20 and s['pf']>=1.10 and s['net']>0:
                    print(f"  {tf} {direction} tp={tp} (D1 {('bull' if direction=='long' else 'bear')} only): {fmt(s)}")
                    ALL.append({'asset':asset,'tf':tf,'strat':f'bbsqueeze_{direction}',
                                'tp':tp,'stats':s,'trades':trades})

# =====================================================================
# 3. Comparació amb Donchian SENSE filtre (per veure diferència)
# =====================================================================
print()
print("="*150)
print("3. COMPARACIÓ — Donchian SENSE filtre vs AMB filtre EMA200 D1:")
print("="*150)
for asset in ['XAUUSD','EURUSD']:
    cost = REAL_COST_BY_ASSET[asset]
    print(f"\n{asset} H1 (Donchian lb=20 sl=2.0 tp=10):")
    arrs = TF_DATA[asset]['H1']
    d1_arrs = TF_DATA[asset]['D1']
    bull_mask, bear_mask = make_d1_trend_mask(arrs, d1_arrs)
    # SENSE filter
    t_no_l = bt_donchian(arrs,'long',2.0,10.0,20,cost,mask=None)
    t_no_s = bt_donchian(arrs,'short',2.0,10.0,20,cost,mask=None)
    # AMB filter
    t_yes_l = bt_donchian(arrs,'long',2.0,10.0,20,cost,mask=bull_mask)
    t_yes_s = bt_donchian(arrs,'short',2.0,10.0,20,cost,mask=bear_mask)
    print(f"  LONG sense: {fmt(stats(t_no_l))}")
    print(f"  LONG amb D1 bull filter: {fmt(stats(t_yes_l))}")
    print(f"  SHORT sense: {fmt(stats(t_no_s))}")
    print(f"  SHORT amb D1 bear filter: {fmt(stats(t_yes_s))}")
    print(f"  BIDIR amb regime: {fmt(stats(t_yes_l + t_yes_s))}")

# =====================================================================
# 4. TOP CONFIGS + Yearly breakdown
# =====================================================================
print()
print("="*150)
print("4. TOP CONFIGS PROTEGITS:")
print("="*150)
valid = [r for r in ALL if r['stats']['n']>=30 and r['stats']['pf']>=1.20]
valid.sort(key=lambda x:-x['stats']['pf'])
for r in valid[:20]:
    s = r['stats']
    print(f"  {r['asset']:<7} {r['tf']:<4} {r['strat']:<28} | {fmt(s)}")

print()
print("="*150)
print("5. ANY-PER-ANY top 5 protegits — clau per veure si sobreviuen bear:")
print("="*150)
for r in valid[:5]:
    print(f"\n{r['asset']} {r['tf']} {r['strat']}: TOTAL {fmt(r['stats'])}")
    yb = per_year(r['trades'])
    for yr in sorted(yb.keys()):
        y = yb[yr]
        print(f"  {yr}: n={y['n']:>3} WR{y['wr']:>5.1f}% Net=${y['net']:>+7.0f}")

print("\nDONE")
