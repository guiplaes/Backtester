"""
5-year backtest with REGIME FILTER:
Only take trades when daily trend is strongly bullish (EMA200 > EMA200 of 60 days ago).
Hypothesis: the strategy works in bull markets, not in chop.
"""
import pandas as pd
import numpy as np

CSV = "xauusd_m5_5y.csv"
ATR_LEN = 14; EMA_LEN = 50; VOL_LEN = 20
COMMISSION = 0.5; SPREAD = 0.50; SLIPPAGE = 0.20
SL_M = 1.5; TP1_M = 15; TP2_M = 30

print("Loading...", flush=True)
df = pd.read_csv(CSV, index_col=0, parse_dates=True)
df.index = pd.to_datetime(df.index, utc=True)
df.columns = [c.lower() for c in df.columns]
if 'tick_volume' not in df.columns: df['tick_volume'] = df['volume']
print(f"{len(df)} bars", flush=True)

print("Computing indicators...", flush=True)
df['ema50'] = df['close'].ewm(span=EMA_LEN, adjust=False).mean()
df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
hl = df['high']-df['low']; hc = (df['high']-df['close'].shift()).abs(); lc = (df['low']-df['close'].shift()).abs()
tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
df['atr'] = tr.ewm(alpha=1/ATR_LEN, adjust=False).mean()
df['vol_avg'] = df['tick_volume'].rolling(VOL_LEN).mean()
df['inside'] = (df['high']<df['high'].shift(1)) & (df['low']>df['low'].shift(1))

# Daily slope: trend regime indicator
# Compute % change of EMA200 over last N M5 bars (approx daily span)
df['ema200_60d_ago'] = df['ema200'].shift(60*24*12)  # 60 days × 24 hours × 12 M5 bars
df['ema200_30d_ago'] = df['ema200'].shift(30*24*12)
df['daily_slope_60d'] = (df['ema200'] - df['ema200_60d_ago']) / df['ema200_60d_ago'] * 100
df['daily_slope_30d'] = (df['ema200'] - df['ema200_30d_ago']) / df['ema200_30d_ago'] * 100
print("Indicators done", flush=True)

def backtest(df_, name, regime_filter=None):
    trades = []; pos = None
    for i in range(max(EMA_LEN, ATR_LEN, 60*24*12)+5, len(df_)):
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
        if pos is None and i>=2:
            prev = df_.iloc[i-1]; pp = df_.iloc[i-2]
            if not prev['inside']: continue
            if pd.isna(bar['ema50']) or pd.isna(bar['atr']) or pd.isna(bar['vol_avg']): continue
            if bar['tick_volume'] <= bar['vol_avg']*1.3: continue
            if ts.dayofweek == 2: continue
            if not (0 <= ts.hour <= 6): continue
            if bar['close']<=bar['ema50']: continue

            # REGIME FILTER
            if regime_filter is not None:
                slope = bar.get(regime_filter)
                if pd.isna(slope) or slope < 5.0:  # require 5%+ uptrend over period
                    continue

            mh = pp['high']
            if not (bar['high']>mh and bar['close']>mh): continue
            atr = bar['atr']; e = bar['close']
            pos = {'e':e,'ts':ts,'sl':e-atr*SL_M,'tp1':e+atr*TP1_M,'tp2':e+atr*TP2_M,'q1':0.5,'q2':0.5}
    return trades

def stats(trades, name):
    if not trades: print(f"{name:>50}: NO trades"); return None
    arr = np.array([t['pnl'] for t in trades])
    n = len(arr); w = (arr>0).sum(); net = arr.sum()
    pf_p = arr[arr>0].sum(); pf_l = abs(arr[arr<=0].sum())
    pf = pf_p/pf_l if pf_l else 0
    eq = np.cumsum(arr); peak = np.maximum.accumulate(eq); dd = (peak-eq).max()
    print(f"{name:>50}: n={n:>4} | WR {w/n*100:>5.1f}% | Net ${net:>+9.2f} | PF {pf:>5.2f} | DD ${dd:>7.2f}", flush=True)
    return {'n':n,'wr':w/n*100,'net':net,'pf':pf,'dd':dd}

print("\n" + "="*120)
print("REGIME FILTER TESTS:")
print("="*120)
trades = backtest(df, "no filter")
stats(trades, "Baseline (no regime filter)")

trades = backtest(df, "60d 5%+", "daily_slope_60d")
stats(trades, "Only when EMA200 +5% in 60d")

# Try with various thresholds
for thresh, period in [(3.0, "60d 3%+"), (8.0, "60d 8%+"), (10.0, "60d 10%+"),
                       (3.0, "30d 3%+"), (5.0, "30d 5%+")]:
    days = 60 if "60d" in period else 30
    col = f"daily_slope_{days}d"
    # Define filter inline
    def make_bt(c, t):
        def bt(d, name):
            trades = []; pos = None
            for i in range(max(EMA_LEN, ATR_LEN, days*24*12)+5, len(d)):
                bar = d.iloc[i]; ts = d.index[i]
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
                if pos is None and i>=2:
                    prev = d.iloc[i-1]; pp = d.iloc[i-2]
                    if not prev['inside']: continue
                    if pd.isna(bar['ema50']) or pd.isna(bar['atr']) or pd.isna(bar['vol_avg']): continue
                    if bar['tick_volume'] <= bar['vol_avg']*1.3: continue
                    if ts.dayofweek == 2: continue
                    if not (0 <= ts.hour <= 6): continue
                    if bar['close']<=bar['ema50']: continue
                    slope = bar.get(c)
                    if pd.isna(slope) or slope < t: continue
                    mh = pp['high']
                    if not (bar['high']>mh and bar['close']>mh): continue
                    atr = bar['atr']; e = bar['close']
                    pos = {'e':e,'ts':ts,'sl':e-atr*SL_M,'tp1':e+atr*TP1_M,'tp2':e+atr*TP2_M,'q1':0.5,'q2':0.5}
            return trades
        return bt
    bt = make_bt(col, thresh)
    trades = bt(df, period)
    stats(trades, f"Slope {period}")
