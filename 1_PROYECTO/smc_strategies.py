"""
SMC strategies mecàniques sobre XAUUSD H4 i D1.
Tres patrons clàssics ben codificables:

S1: FAIR VALUE GAP (FVG) retest
   - 3 candles consecutives on midcandle té gap respecte candle 1 i 3
   - Entry: preu torna a omplir el FVG → entra en direcció trend

S2: BULLISH ORDER BLOCK retest
   - Última espelma vermella abans d'un moviment fort alcista (>1.5×ATR en 3 barres)
   - Entry: preu torna a aquesta espelma → LONG amb rejection

S3: LIQUIDITY SWEEP + BOS (Break of Structure)
   - Preu trenca un swing low (sweeps liquiditat baixa)
   - Després trenca el lower-high anterior (BOS upward)
   - Entry: LONG en el BOS

Tots amb SL/TP basats en ATR i regime filter LONG-only.
"""
import pandas as pd
import numpy as np

CSV = "xauusd_m5_5y.csv"
COMMISSION = 0.5; SPREAD = 0.50; SLIPPAGE = 0.20

print("Loading + aggregating...", flush=True)
m5 = pd.read_csv(CSV, index_col=0, parse_dates=True)
m5.index = pd.to_datetime(m5.index, utc=True)
m5.columns = [c.lower() for c in m5.columns]

def aggregate(df_, rule):
    return df_.resample(rule).agg(
        open=('open', 'first'), high=('high', 'max'),
        low=('low', 'min'), close=('close', 'last'),
        tick_volume=('tick_volume', 'sum') if 'tick_volume' in df_.columns else ('volume', 'sum')
    ).dropna()

if 'tick_volume' not in m5.columns: m5['tick_volume'] = m5.get('volume', 1)
h4 = aggregate(m5, '4h')
d1 = aggregate(m5, '1D')
print(f"H4: {len(h4)} | D1: {len(d1)}", flush=True)

def add_indicators(df_):
    df_ = df_.copy()
    df_['ema20'] = df_['close'].ewm(span=20, adjust=False).mean()
    df_['ema50'] = df_['close'].ewm(span=50, adjust=False).mean()
    df_['ema200'] = df_['close'].ewm(span=200, adjust=False).mean()
    hl = df_['high']-df_['low']; hc = (df_['high']-df_['close'].shift()).abs(); lc = (df_['low']-df_['close'].shift()).abs()
    tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
    df_['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
    return df_

h4 = add_indicators(h4)
d1 = add_indicators(d1)
print("Indicators ready", flush=True)

def stats(trades, name):
    if not trades: print(f"{name:>50}: NO trades"); return None
    arr = np.array([t['pnl'] for t in trades])
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    print(f"{name:>50}: n={n:>4} | WR {w/n*100:>5.1f}% | Net ${net:>+9.2f} | PF {pf:>5.2f} | DD ${dd:>7.2f}", flush=True)
    return {'pf':pf, 'net':net}

# ============================================================
# S1: FAIR VALUE GAP (FVG) retest
# ============================================================
def s1_fvg_retest(df_, sl_atr=1.5, tp1_atr=3, tp2_atr=6, max_lookback=20):
    """
    Bullish FVG: high[i-2] < low[i] AND close[i] > open[i-2] (gap up)
    Wait for price to retrace into the FVG zone, enter LONG with bullish bar.
    """
    trades = []; pos = None
    pending_fvgs = []  # list of {gap_low, gap_high, expiry_bar, atr0}

    for i in range(50, len(df_)):
        bar = df_.iloc[i]; ts = df_.index[i]
        if pos is not None:
            sgn = 1 if pos['side']=='L' else -1
            sl_h = bar['low'] <= pos['sl'] if pos['side']=='L' else bar['high'] >= pos['sl']
            tp1_h = bar['high'] >= pos['tp1'] if pos['side']=='L' else bar['low'] <= pos['tp1']
            tp2_h = bar['high'] >= pos['tp2'] if pos['side']=='L' else bar['low'] <= pos['tp2']
            if sl_h and (tp1_h or tp2_h):
                if pos['side']=='L':
                    if (bar['open']-pos['sl']) < (pos['tp1']-bar['open']): tp1_h=False; tp2_h=False
                else:
                    if (pos['sl']-bar['open']) < (bar['open']-pos['tp1']): tp1_h=False; tp2_h=False
            if tp1_h and pos['q1']>0: pos['pnl1'] = (pos['tp1']-pos['e'])*0.5*sgn - SLIPPAGE*0.5; pos['q1']=0
            if tp2_h and pos['q2']>0: pos['pnl2'] = (pos['tp2']-pos['e'])*0.5*sgn - SLIPPAGE*0.5; pos['q2']=0
            if sl_h:
                if pos['q1']>0: pos['pnl1'] = (pos['sl']-pos['e'])*0.5*sgn - SLIPPAGE*0.5; pos['q1']=0
                if pos['q2']>0: pos['pnl2'] = (pos['sl']-pos['e'])*0.5*sgn - SLIPPAGE*0.5; pos['q2']=0
            if pos['q1']==0 and pos['q2']==0:
                tp = pos.get('pnl1',0)+pos.get('pnl2',0)-COMMISSION*2-SPREAD
                trades.append({'ts': pos['ts'], 'pnl': tp, 'side': pos['side']})
                pos = None

        # Detect new bullish FVG (i-2 high < i low, gap up)
        if i >= 2:
            b0 = df_.iloc[i-2]; b1 = df_.iloc[i-1]; b2 = bar
            # Bullish FVG: gap between bar -2 high and bar 0 low
            if b0['high'] < b2['low']:
                pending_fvgs.append({
                    'gap_low': b0['high'],
                    'gap_high': b2['low'],
                    'expiry': i + max_lookback,
                    'atr0': b1['atr'] if not pd.isna(b1['atr']) else 1.0,
                })

        # Clean expired FVGs
        pending_fvgs = [f for f in pending_fvgs if f['expiry'] > i]

        # Look for FVG retest entry
        if pos is None and pd.notna(bar['ema50']) and bar['close'] > bar['ema50']:  # uptrend filter
            for fvg in list(pending_fvgs):
                # Price has retraced into FVG zone
                if bar['low'] <= fvg['gap_high'] and bar['close'] >= fvg['gap_low']:
                    # Bullish reversal at FVG
                    if bar['close'] > bar['open']:
                        atr = fvg['atr0']
                        e = bar['close']
                        pos = {'side':'L', 'e':e, 'ts':ts,
                               'sl':fvg['gap_low'] - atr*sl_atr*0.5,
                               'tp1':e + atr*tp1_atr,
                               'tp2':e + atr*tp2_atr,
                               'q1':0.5, 'q2':0.5}
                        pending_fvgs.remove(fvg)
                        break
    return trades

# ============================================================
# S2: BULLISH ORDER BLOCK retest
# ============================================================
def s2_order_block(df_, sl_atr=1.5, tp1_atr=3, tp2_atr=6, ob_strength=1.5, max_lookback=30):
    """
    Bullish OB: last red candle before a strong impulsive move up
    (next 3 bars total move > ob_strength × ATR).
    Wait for price to retest the OB candle's low/high range, enter LONG.
    """
    trades = []; pos = None
    pending_obs = []

    for i in range(50, len(df_)-3):
        bar = df_.iloc[i]; ts = df_.index[i]
        if pos is not None:
            sgn = 1 if pos['side']=='L' else -1
            sl_h = bar['low'] <= pos['sl']
            tp1_h = bar['high'] >= pos['tp1']
            tp2_h = bar['high'] >= pos['tp2']
            if sl_h and (tp1_h or tp2_h):
                if (bar['open']-pos['sl']) < (pos['tp1']-bar['open']): tp1_h=False; tp2_h=False
            if tp1_h and pos['q1']>0: pos['pnl1'] = (pos['tp1']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q1']=0
            if tp2_h and pos['q2']>0: pos['pnl2'] = (pos['tp2']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q2']=0
            if sl_h:
                if pos['q1']>0: pos['pnl1'] = (pos['sl']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q1']=0
                if pos['q2']>0: pos['pnl2'] = (pos['sl']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q2']=0
            if pos['q1']==0 and pos['q2']==0:
                tp = pos.get('pnl1',0)+pos.get('pnl2',0)-COMMISSION*2-SPREAD
                trades.append({'ts': pos['ts'], 'pnl': tp})
                pos = None

        # Detect bullish OB: red candle followed by strong move up
        b0 = df_.iloc[i-1]
        is_red = b0['close'] < b0['open']
        if is_red and not pd.isna(b0['atr']):
            # Next 3 bars total upward range
            future_high = max(df_.iloc[i]['high'], df_.iloc[i+1]['high'], df_.iloc[i+2]['high'])
            move = future_high - b0['close']
            if move > ob_strength * b0['atr']:
                # Valid bullish OB
                pending_obs.append({
                    'ob_low': b0['low'],
                    'ob_high': b0['high'],
                    'expiry': i + max_lookback,
                    'atr0': b0['atr'],
                })

        pending_obs = [o for o in pending_obs if o['expiry'] > i]

        if pos is None and pd.notna(bar['ema50']) and bar['close'] > bar['ema50']:
            for ob in list(pending_obs):
                # Price retraces into OB
                if bar['low'] <= ob['ob_high'] and bar['close'] > ob['ob_low']:
                    if bar['close'] > bar['open']:  # bullish rejection
                        atr = ob['atr0']
                        e = bar['close']
                        pos = {'side':'L', 'e':e, 'ts':ts,
                               'sl':ob['ob_low'] - atr*sl_atr*0.5,
                               'tp1':e + atr*tp1_atr,
                               'tp2':e + atr*tp2_atr,
                               'q1':0.5, 'q2':0.5}
                        pending_obs.remove(ob)
                        break
    return trades

# ============================================================
# S3: LIQUIDITY SWEEP + BOS
# ============================================================
def s3_sweep_bos(df_, sl_atr=1.0, tp1_atr=3, tp2_atr=6, swing_lookback=10):
    """
    Bullish setup:
    1) Find recent swing low (lowest in last N bars)
    2) Price sweeps it (briefly goes below) and closes back above (sweep)
    3) Then breaks above the most recent lower-high (BOS)
    4) Enter LONG on the BOS bar.
    """
    trades = []; pos = None
    swept_low_value = None
    swept_low_idx = None
    waiting_for_bos = False
    bos_target = None

    for i in range(50, len(df_)):
        bar = df_.iloc[i]; ts = df_.index[i]
        if pos is not None:
            sl_h = bar['low'] <= pos['sl']
            tp1_h = bar['high'] >= pos['tp1']
            tp2_h = bar['high'] >= pos['tp2']
            if sl_h and (tp1_h or tp2_h):
                if (bar['open']-pos['sl']) < (pos['tp1']-bar['open']): tp1_h=False; tp2_h=False
            if tp1_h and pos['q1']>0: pos['pnl1'] = (pos['tp1']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q1']=0
            if tp2_h and pos['q2']>0: pos['pnl2'] = (pos['tp2']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q2']=0
            if sl_h:
                if pos['q1']>0: pos['pnl1'] = (pos['sl']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q1']=0
                if pos['q2']>0: pos['pnl2'] = (pos['sl']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q2']=0
            if pos['q1']==0 and pos['q2']==0:
                tp = pos.get('pnl1',0)+pos.get('pnl2',0)-COMMISSION*2-SPREAD
                trades.append({'ts': pos['ts'], 'pnl': tp})
                pos = None

        if i < swing_lookback + 5: continue
        recent_low = df_.iloc[i-swing_lookback:i]['low'].min()
        recent_high = df_.iloc[i-swing_lookback:i]['high'].max()

        # Phase 1: detect sweep
        if not waiting_for_bos:
            if bar['low'] < recent_low and bar['close'] > recent_low:
                # sweep happened
                waiting_for_bos = True
                bos_target = recent_high
                swept_low_value = bar['low']
                swept_low_idx = i
        # Phase 2: wait for BOS
        else:
            # Timeout: 10 bars without BOS
            if i - swept_low_idx > 10:
                waiting_for_bos = False
                continue
            # BOS: close above bos_target
            if bar['close'] > bos_target:
                if pos is None and pd.notna(bar['ema50']) and bar['close'] > bar['ema50']:
                    atr = bar['atr']
                    e = bar['close']
                    pos = {'side':'L', 'e':e, 'ts':ts,
                           'sl':swept_low_value - atr*sl_atr*0.3,
                           'tp1':e + atr*tp1_atr,
                           'tp2':e + atr*tp2_atr,
                           'q1':0.5, 'q2':0.5}
                waiting_for_bos = False

    return trades

# ============================================================
# Run all on H4 and D1
# ============================================================
print()
print("="*120)
print("SMC patterns mecànics — XAUUSD H4 (5 anys):")
print("="*120)

print("\nS1: Fair Value Gap retest (LONG only, EMA50 trend)")
stats(s1_fvg_retest(h4), "H4 FVG retest 1.5/3/6")
stats(s1_fvg_retest(h4, tp1_atr=5, tp2_atr=10), "H4 FVG retest 1.5/5/10")
stats(s1_fvg_retest(h4, sl_atr=2, tp1_atr=5, tp2_atr=10), "H4 FVG retest 2/5/10")

print("\nS2: Order Block retest (LONG only)")
stats(s2_order_block(h4), "H4 OB retest 1.5/3/6")
stats(s2_order_block(h4, ob_strength=2.5), "H4 OB strength 2.5")
stats(s2_order_block(h4, tp1_atr=5, tp2_atr=10), "H4 OB 1.5/5/10")

print("\nS3: Liquidity Sweep + BOS (LONG only)")
stats(s3_sweep_bos(h4), "H4 Sweep+BOS 1.0/3/6")
stats(s3_sweep_bos(h4, sl_atr=1.5, tp1_atr=5, tp2_atr=10), "H4 Sweep+BOS wider")
stats(s3_sweep_bos(h4, swing_lookback=20), "H4 Sweep+BOS lookback 20")

# D1
print()
print("="*120)
print("SMC patterns mecànics — XAUUSD D1 (5 anys):")
print("="*120)
print("\nS1 FVG D1:")
stats(s1_fvg_retest(d1), "D1 FVG retest 1.5/3/6")
stats(s1_fvg_retest(d1, tp1_atr=5, tp2_atr=10), "D1 FVG retest 1.5/5/10")

print("\nS2 OB D1:")
stats(s2_order_block(d1), "D1 OB retest")
stats(s2_order_block(d1, tp1_atr=5, tp2_atr=10), "D1 OB 1.5/5/10")

print("\nS3 Sweep+BOS D1:")
stats(s3_sweep_bos(d1), "D1 Sweep+BOS")
stats(s3_sweep_bos(d1, sl_atr=1.5, tp1_atr=5, tp2_atr=10), "D1 Sweep+BOS wider")

# ============================================================
# Best one per year
# ============================================================
best_h4 = stats(s1_fvg_retest(h4, sl_atr=2, tp1_atr=5, tp2_atr=10), "(checking best...)")
print()
print("Per any de H4 FVG 2/5/10:")
trades = s1_fvg_retest(h4, sl_atr=2, tp1_atr=5, tp2_atr=10)
if trades:
    tdf = pd.DataFrame(trades)
    tdf['year'] = pd.to_datetime(tdf['ts']).dt.year
    for yr in sorted(tdf['year'].unique()):
        ydf = tdf[tdf['year']==yr]
        arr = ydf['pnl'].values
        n=len(arr); w=(arr>0).sum(); net=arr.sum()
        pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
        pf=pf_p/pf_l if pf_l else 0
        print(f"  {yr}: n={n:>3} | WR {w/n*100 if n else 0:>5.1f}% | Net ${net:>+9.2f} | PF {pf:>5.2f}", flush=True)
