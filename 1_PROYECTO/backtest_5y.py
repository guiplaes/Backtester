"""
Backtest winning config on 5 years of XAUUSD M5 from Dukascopy.
NO LLM filter — pure mechanical strategy.

Tests:
1. Baseline winning config
2. Walk-forward 5-year (split into 5 yearly slices)
3. Performance per year
"""
import pandas as pd
import numpy as np
import sys

CSV = "xauusd_m5_5y.csv"
ATR_LEN = 14; EMA_LEN = 50; VOL_LEN = 20
COMMISSION = 0.5; SPREAD = 0.50; SLIPPAGE = 0.20
SL_M = 1.5; TP1_M = 15; TP2_M = 30

def load():
    df = pd.read_csv(CSV, index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]
    if 'volume' in df.columns and 'tick_volume' not in df.columns:
        df['tick_volume'] = df['volume']
    return df

def compute(df):
    df = df.copy()
    df['ema50'] = df['close'].ewm(span=EMA_LEN, adjust=False).mean()
    hl = df['high']-df['low']; hc = (df['high']-df['close'].shift()).abs(); lc = (df['low']-df['close'].shift()).abs()
    tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
    df['atr'] = tr.ewm(alpha=1/ATR_LEN, adjust=False).mean()
    df['vol_avg'] = df['tick_volume'].rolling(VOL_LEN).mean()
    df['inside'] = (df['high']<df['high'].shift(1)) & (df['low']>df['low'].shift(1))
    return df

def backtest(df):
    trades = []; pos = None
    for i in range(EMA_LEN+5, len(df)):
        bar = df.iloc[i]; ts = df.index[i]
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
            prev = df.iloc[i-1]; pp = df.iloc[i-2]
            if not prev['inside']: continue
            if pd.isna(bar['ema50']) or pd.isna(bar['atr']) or pd.isna(bar['vol_avg']): continue
            if bar['tick_volume'] <= bar['vol_avg']*1.3: continue
            if ts.dayofweek == 2: continue
            if not (0 <= ts.hour <= 6): continue
            if bar['close']<=bar['ema50']: continue
            mh = pp['high']
            if not (bar['high']>mh and bar['close']>mh): continue
            atr = bar['atr']; e = bar['close']
            pos = {'e':e,'ts':ts,'sl':e-atr*SL_M,'tp1':e+atr*TP1_M,'tp2':e+atr*TP2_M,'q1':0.5,'q2':0.5}
    return trades

def stats(trades, name):
    if not trades: print(f"{name}: NO trades"); return None
    arr = np.array([t['pnl'] for t in trades])
    n = len(arr); w = (arr>0).sum(); net = arr.sum()
    pf_p = arr[arr>0].sum(); pf_l = abs(arr[arr<=0].sum())
    pf = pf_p/pf_l if pf_l else 0
    eq = np.cumsum(arr); peak = np.maximum.accumulate(eq); dd = (peak-eq).max()
    avg = arr.mean()
    print(f"{name:>40}: n={n:>4} | WR {w/n*100:>5.1f}% | Net ${net:>+9.2f} | PF {pf:>5.2f} | DD ${dd:>7.2f} | Avg ${avg:>+5.2f}")
    return {'n':n, 'wr':w/n*100, 'net':net, 'pf':pf, 'dd':dd, 'avg':avg}

print("Loading 5y CSV...", flush=True)
df = load()
print(f"Loaded {len(df)} bars from {df.index[0]} to {df.index[-1]}", flush=True)
df = compute(df)

print("\nBaseline winning config (LONG+skipWed+Asia+TP15/30+SL1.5):", flush=True)
trades = backtest(df)
s_full = stats(trades, "FULL 5 YEARS")

print("\nPer-year breakdown:", flush=True)
import csv
tdf = pd.DataFrame(trades)
if len(tdf):
    tdf['year'] = pd.to_datetime(tdf['ts']).dt.year
    for yr in sorted(tdf['year'].unique()):
        ydf = tdf[tdf['year']==yr]
        arr = ydf['pnl'].values
        n = len(arr); w = (arr>0).sum(); net = arr.sum()
        pf_p = arr[arr>0].sum(); pf_l = abs(arr[arr<=0].sum())
        pf = pf_p/pf_l if pf_l else 0
        print(f"  {yr}: n={n:>3} | WR {w/n*100 if n else 0:>5.1f}% | Net ${net:>+9.2f} | PF {pf:>5.2f}")
    tdf.to_csv("backtest_trades_5y.csv", index=False)

print("\nWalk-forward 5y (split 60/40):", flush=True)
mid_idx = int(len(trades)*0.6)
if mid_idx >= 10:
    is_t = trades[:mid_idx]
    oos_t = trades[mid_idx:]
    stats(is_t, "IS 60% (training)")
    stats(oos_t, "OOS 40% (validation)")
