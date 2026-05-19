"""
Trend Pullback amb filtre macro sobre D1 i H4.
XAUUSD 5 anys (agregat de M5 a H4 i D1).
"""
import pandas as pd
import numpy as np

CSV = "xauusd_m5_5y.csv"
COMMISSION = 0.5; SPREAD = 0.50; SLIPPAGE = 0.20

print("Loading M5 5y...", flush=True)
m5 = pd.read_csv(CSV, index_col=0, parse_dates=True)
m5.index = pd.to_datetime(m5.index, utc=True)
m5.columns = [c.lower() for c in m5.columns]
if 'tick_volume' not in m5.columns: m5['tick_volume'] = m5['volume']
print(f"M5: {len(m5)} bars", flush=True)

# Aggregate to H4 and D1
def aggregate(df_, rule):
    return df_.resample(rule).agg(
        open=('open', 'first'),
        high=('high', 'max'),
        low=('low', 'min'),
        close=('close', 'last'),
        tick_volume=('tick_volume', 'sum'),
    ).dropna()

print("Aggregating to H4 and D1...", flush=True)
h4 = aggregate(m5, '4h')
d1 = aggregate(m5, '1D')
print(f"H4: {len(h4)} bars | D1: {len(d1)} bars", flush=True)

def add_indicators(df_):
    df_ = df_.copy()
    df_['ema20'] = df_['close'].ewm(span=20, adjust=False).mean()
    df_['ema50'] = df_['close'].ewm(span=50, adjust=False).mean()
    df_['ema50_slope_30'] = (df_['ema50'] - df_['ema50'].shift(30)) / df_['ema50'].shift(30) * 100
    df_['roc60'] = (df_['close'] - df_['close'].shift(60)) / df_['close'].shift(60) * 100
    hl = df_['high']-df_['low']; hc = (df_['high']-df_['close'].shift()).abs(); lc = (df_['low']-df_['close'].shift()).abs()
    tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
    df_['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
    df_['atr_pct'] = df_['atr'].rolling(100).rank(pct=True)
    df_['body'] = abs(df_['close'] - df_['open'])
    df_['lower_wick'] = df_[['close','open']].min(axis=1) - df_['low']
    df_['upper_wick'] = df_['high'] - df_[['close','open']].max(axis=1)
    df_['hammer'] = (df_['lower_wick'] > 1.5 * df_['body']) & (df_['body'] > 0)
    df_['bull_engulf'] = (df_['close'] > df_['open']) & (df_['close'] > df_['open'].shift(1)) & (df_['open'] < df_['close'].shift(1))
    df_['red'] = df_['close'] < df_['open']
    df_['red_streak2'] = df_['red'].shift(1) & df_['red'].shift(2)
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
    return arr, trades

def backtest_trend_pullback(df_, sl_atr=2.5, tp1_atr=3, tp2_atr=6, tp3_trail=1.5,
                             min_slope=0.5, min_roc=5.0, atr_min=0.30, atr_max=0.70,
                             pullback_atr=1.5, long_only=True):
    """4-layer entry: trend + momentum + vol + pullback+reversal."""
    trades = []; pos = None
    for i in range(60, len(df_)):
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

            if tp1_h and pos['q1']>0:
                pos['pnl1'] = (pos['tp1']-pos['e'])*0.5*sgn - SLIPPAGE*0.5; pos['q1']=0
            if tp2_h and pos['q2']>0:
                pos['pnl2'] = (pos['tp2']-pos['e'])*0.25*sgn - SLIPPAGE*0.25; pos['q2']=0
                # Activate trailing for last 25%
                pos['trail_active'] = True
                pos['trail_high'] = bar['high'] if pos['side']=='L' else bar['low']
            if sl_h:
                if pos['q1']>0: pos['pnl1'] = (pos['sl']-pos['e'])*0.5*sgn - SLIPPAGE*0.5; pos['q1']=0
                if pos['q2']>0: pos['pnl2'] = (pos['sl']-pos['e'])*0.25*sgn - SLIPPAGE*0.25; pos['q2']=0
                if pos['q3']>0: pos['pnl3'] = (pos['sl']-pos['e'])*0.25*sgn - SLIPPAGE*0.25; pos['q3']=0

            # Trailing stop for q3
            if pos['q3'] > 0 and pos.get('trail_active'):
                if pos['side']=='L':
                    pos['trail_high'] = max(pos['trail_high'], bar['high'])
                    trail_sl = pos['trail_high'] - tp3_trail * pos['atr0']
                    if bar['low'] <= trail_sl:
                        pos['pnl3'] = (trail_sl - pos['e'])*0.25*sgn - SLIPPAGE*0.25; pos['q3']=0
                else:
                    pos['trail_high'] = min(pos['trail_high'], bar['low'])
                    trail_sl = pos['trail_high'] + tp3_trail * pos['atr0']
                    if bar['high'] >= trail_sl:
                        pos['pnl3'] = (trail_sl - pos['e'])*0.25*sgn - SLIPPAGE*0.25; pos['q3']=0

            if pos['q1']==0 and pos['q2']==0 and pos['q3']==0:
                tp = pos.get('pnl1',0) + pos.get('pnl2',0) + pos.get('pnl3',0) - COMMISSION*2 - SPREAD
                trades.append({'ts': pos['ts'], 'pnl': tp, 'side': pos['side']})
                pos = None

        if pos is None and i>=2:
            if pd.isna(bar['ema50']) or pd.isna(bar['ema50_slope_30']) or pd.isna(bar['roc60']) or pd.isna(bar['atr_pct']): continue
            atr = bar['atr']; e = bar['close']

            # Layer 1: Trend
            in_uptrend = bar['close'] > bar['ema50'] and bar['ema50_slope_30'] > min_slope
            in_downtrend = bar['close'] < bar['ema50'] and bar['ema50_slope_30'] < -min_slope

            # Layer 2: Momentum
            mom_up = bar['roc60'] > min_roc
            mom_dn = bar['roc60'] < -min_roc

            # Layer 3: Vol regime healthy
            vol_ok = atr_min <= bar['atr_pct'] <= atr_max

            if not vol_ok: continue

            # Layer 4: Pullback to EMA20 + reversal
            ema20 = bar['ema20']
            close_to_ema20 = abs(bar['close'] - ema20) <= pullback_atr * atr
            pulled_to_ema20 = (bar['low'] <= ema20 + pullback_atr * atr) if in_uptrend else (bar['high'] >= ema20 - pullback_atr * atr)

            # Reversal candle
            bull_reversal = bar['hammer'] or bar['bull_engulf'] or (bar['red_streak2'] and bar['close'] > bar['open'])
            # Bearish reversal mirror
            bar_color_red = bar['close'] < bar['open']
            bear_reversal = (bar['upper_wick'] > 1.5 * bar['body'] and bar['body'] > 0) or \
                            (bar['close'] < bar['open'].shift(1) if hasattr(bar['open'], 'shift') else False)

            if in_uptrend and mom_up and pulled_to_ema20 and bull_reversal:
                pos = {'side':'L','e':e,'ts':ts,'atr0':atr,
                       'sl':e-atr*sl_atr,'tp1':e+atr*tp1_atr,'tp2':e+atr*tp2_atr,
                       'q1':0.5,'q2':0.25,'q3':0.25,'trail_active':False,'trail_high':e}
            elif (not long_only) and in_downtrend and mom_dn and pulled_to_ema20 and bar_color_red:
                pos = {'side':'S','e':e,'ts':ts,'atr0':atr,
                       'sl':e+atr*sl_atr,'tp1':e-atr*tp1_atr,'tp2':e-atr*tp2_atr,
                       'q1':0.5,'q2':0.25,'q3':0.25,'trail_active':False,'trail_high':e}
    return trades

# ==========================
# H4 tests
# ==========================
print()
print("="*120)
print("H4 — Trend Pullback Macro (5 years):")
print("="*120)
trades = backtest_trend_pullback(h4, long_only=True)
res = stats(trades, "H4 LONG only — 4-layer filter")
trades = backtest_trend_pullback(h4, long_only=False)
stats(trades, "H4 Both directions")

# Wider TPs
trades = backtest_trend_pullback(h4, sl_atr=2.5, tp1_atr=5, tp2_atr=10, long_only=True)
stats(trades, "H4 LONG, wider TPs (5/10×ATR)")

# Loose pullback
trades = backtest_trend_pullback(h4, pullback_atr=2.5, long_only=True)
stats(trades, "H4 LONG, looser pullback (2.5xATR)")

# ==========================
# D1 tests
# ==========================
print()
print("="*120)
print("D1 — Trend Pullback Macro (5 years):")
print("="*120)
trades = backtest_trend_pullback(d1, long_only=True, min_roc=3.0)
stats(trades, "D1 LONG only — default")
trades = backtest_trend_pullback(d1, long_only=False, min_roc=3.0)
stats(trades, "D1 Both directions")
trades = backtest_trend_pullback(d1, sl_atr=2, tp1_atr=4, tp2_atr=8, min_roc=3.0, long_only=True)
stats(trades, "D1 LONG, tighter SL wider TP")

# ==========================
# Per-year breakdown of best
# ==========================
print()
print("="*120)
print("PER YEAR — H4 LONG only default:")
print("="*120)
trades = backtest_trend_pullback(h4, long_only=True)
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

print("\nPER YEAR — D1 LONG only:")
trades = backtest_trend_pullback(d1, long_only=True, min_roc=3.0)
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

# ==========================
# Donchian benchmark (Turtle classic) on D1
# ==========================
print()
print("="*120)
print("BENCHMARK: Donchian 20/55 (Turtle) on D1:")
print("="*120)

def donchian_d1(df_, breakout=20, exit_periods=10, sl_atr=2.0, long_only=True):
    """Classic Turtle: long when close > N-day high, exit when close < M-day low."""
    df_ = df_.copy()
    df_['don_high'] = df_['high'].rolling(breakout).max().shift(1)
    df_['don_low'] = df_['low'].rolling(breakout).min().shift(1)
    df_['exit_low'] = df_['low'].rolling(exit_periods).min().shift(1)
    df_['exit_high'] = df_['high'].rolling(exit_periods).max().shift(1)

    trades = []; pos = None
    for i in range(breakout+5, len(df_)):
        bar = df_.iloc[i]; ts = df_.index[i]
        if pos is not None:
            # Check SL
            sl_h = bar['low'] <= pos['sl'] if pos['side']=='L' else bar['high'] >= pos['sl']
            sgn = 1 if pos['side']=='L' else -1
            if sl_h:
                price = pos['sl']
                pnl = (price - pos['e'])*sgn - SLIPPAGE - COMMISSION*2 - SPREAD
                trades.append({'ts': pos['ts'], 'pnl': pnl, 'side': pos['side']})
                pos = None
            # Check exit signal
            elif pos['side']=='L' and bar['close'] < bar['exit_low']:
                pnl = (bar['close'] - pos['e']) - SLIPPAGE - COMMISSION*2 - SPREAD
                trades.append({'ts': pos['ts'], 'pnl': pnl, 'side': pos['side']})
                pos = None
            elif pos['side']=='S' and bar['close'] > bar['exit_high']:
                pnl = (pos['e'] - bar['close']) - SLIPPAGE - COMMISSION*2 - SPREAD
                trades.append({'ts': pos['ts'], 'pnl': pnl, 'side': pos['side']})
                pos = None

        if pos is None and not pd.isna(bar['don_high']):
            atr = bar['atr']
            if pd.isna(atr): continue
            if bar['close'] > bar['don_high']:
                pos = {'side':'L', 'e':bar['close'], 'ts':ts, 'sl':bar['close']-atr*sl_atr}
            elif (not long_only) and bar['close'] < bar['don_low']:
                pos = {'side':'S', 'e':bar['close'], 'ts':ts, 'sl':bar['close']+atr*sl_atr}
    return trades

trades = donchian_d1(d1, breakout=20, exit_periods=10, long_only=True)
stats(trades, "Donchian D1 20/10 LONG")
trades = donchian_d1(d1, breakout=55, exit_periods=20, long_only=True)
stats(trades, "Donchian D1 55/20 LONG (slow turtle)")
trades = donchian_d1(d1, breakout=20, exit_periods=10, long_only=False)
stats(trades, "Donchian D1 20/10 Both")

# Per year Donchian
print("\nPER YEAR — Donchian D1 20/10 LONG:")
trades = donchian_d1(d1, breakout=20, exit_periods=10, long_only=True)
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

# ==========================
# Buy & Hold benchmark
# ==========================
print()
print("="*120)
print("BENCHMARK: Buy & Hold gold")
print("="*120)
start_price = d1['close'].iloc[20]
end_price = d1['close'].iloc[-1]
bh_return = end_price - start_price
bh_pct = (end_price / start_price - 1) * 100
peak_so_far = d1['close'].iloc[20]
max_dd_pct = 0
for p in d1['close'].iloc[20:]:
    peak_so_far = max(peak_so_far, p)
    dd_pct = (peak_so_far - p) / peak_so_far * 100
    max_dd_pct = max(max_dd_pct, dd_pct)
print(f"Buy & Hold: ${start_price:.2f} -> ${end_price:.2f} = ${bh_return:+.2f} ({bh_pct:+.1f}%) | Max DD: {max_dd_pct:.1f}%", flush=True)
