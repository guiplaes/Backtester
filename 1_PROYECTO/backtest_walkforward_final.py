"""Walk-forward final config: LONG + skip Wed + Asia + TP 15/30 + SL 1.5"""
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timezone

SYMBOL = "XAUUSD.crp"
ATR_LEN = 14; EMA_LEN = 50; VOL_LEN = 20
COMMISSION = 0.5; SPREAD = 0.50; SLIPPAGE = 0.20
SL_M = 1.5; TP1_M = 15; TP2_M = 30

def fetch():
    mt5.initialize(); mt5.symbol_select(SYMBOL, True)
    end = datetime.now(timezone.utc)
    rates = mt5.copy_rates_from(SYMBOL, mt5.TIMEFRAME_M5, end, 50000)
    if len(rates) >= 50000:
        oldest = datetime.fromtimestamp(int(rates[0]['time']), tz=timezone.utc)
        rates2 = mt5.copy_rates_from(SYMBOL, mt5.TIMEFRAME_M5, oldest, 50000)
        if rates2 is not None:
            rates = np.concatenate([rates2, rates])
            _, idx = np.unique(rates['time'], return_index=True); rates = rates[np.sort(idx)]
    df = pd.DataFrame(rates); df['time'] = pd.to_datetime(df['time'], unit='s', utc=True); df = df.set_index('time')
    mt5.shutdown(); return df

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
            if tp1_h and pos['q1']>0:
                pos['pnl1'] = (pos['tp1']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q1']=0
            if tp2_h and pos['q2']>0:
                pos['pnl2'] = (pos['tp2']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q2']=0
            if sl_h:
                if pos['q1']>0:
                    pos['pnl1'] = (pos['sl']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q1']=0
                if pos['q2']>0:
                    pos['pnl2'] = (pos['sl']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q2']=0
            if pos['q1']==0 and pos['q2']==0:
                tp = pos.get('pnl1',0)+pos.get('pnl2',0)-COMMISSION*2-SPREAD
                trades.append({'ts': pos['ts'], 'pnl': tp})
                pos = None
        if pos is None and i>=2:
            prev = df.iloc[i-1]; pp = df.iloc[i-2]
            if not prev['inside']: continue
            if pd.isna(bar['ema50']) or pd.isna(bar['atr']) or pd.isna(bar['vol_avg']): continue
            if bar['tick_volume'] <= bar['vol_avg']*1.3: continue
            if ts.dayofweek == 2: continue  # skip Wed
            if not (0 <= ts.hour <= 6): continue  # Asia only
            if bar['close']<=bar['ema50']: continue  # LONG only filter (uptrend)
            mh = pp['high']
            if not (bar['high']>mh and bar['close']>mh): continue
            atr = bar['atr']; e = bar['close']
            pos = {'e':e,'ts':ts,'sl':e-atr*SL_M,'tp1':e+atr*TP1_M,'tp2':e+atr*TP2_M,'q1':0.5,'q2':0.5}
    return trades

def stats(trades, name):
    if not trades:
        print(f"{name}: NO trades"); return
    arr = np.array([t['pnl'] for t in trades])
    n = len(arr); w = (arr>0).sum(); net = arr.sum()
    pf_p = arr[arr>0].sum(); pf_l = abs(arr[arr<=0].sum())
    pf = pf_p/pf_l if pf_l else 0
    eq = np.cumsum(arr); peak = np.maximum.accumulate(eq); dd = (peak-eq).max()
    avg = arr.mean()
    print(f"{name:>40}: {n:>3} trades | WR {w/n*100:>5.1f}% | Net ${net:>+8.2f} | Avg ${avg:>+5.2f} | PF {pf:>5.2f} | DD ${dd:>6.2f}")

print("Fetching...")
df = fetch()
df = compute(df)
print(f"{len(df)} bars from {df.index[0].date()} to {df.index[-1].date()}\n")

# Full sample
trades_all = backtest(df)
stats(trades_all, "FULL 17 months")

# Walk-forward 50/50
total_trades = trades_all
mid_idx = len(total_trades)//2
mid_ts = total_trades[mid_idx]['ts']
print(f"\nSplit at trade #{mid_idx}: {mid_ts}")
ts_split = pd.Timestamp(mid_ts)

is_trades = [t for t in trades_all if pd.Timestamp(t['ts']) < ts_split]
oos_trades = [t for t in trades_all if pd.Timestamp(t['ts']) >= ts_split]
stats(is_trades, "IN-SAMPLE (1st half)")
stats(oos_trades, "OUT-OF-SAMPLE (2nd half)")

# Walk-forward 60/40 (more conservative)
n60 = int(len(total_trades)*0.6)
ts_split2 = pd.Timestamp(total_trades[n60]['ts'])
is2 = [t for t in trades_all if pd.Timestamp(t['ts']) < ts_split2]
oos2 = [t for t in trades_all if pd.Timestamp(t['ts']) >= ts_split2]
print()
stats(is2, "IS 60% (training)")
stats(oos2, "OOS 40% (validation)")

# Monthly breakdown
print("\nMonthly:")
mdf = pd.DataFrame(trades_all)
mdf['month'] = pd.to_datetime(mdf['ts']).dt.to_period('M')
print(mdf.groupby('month')['pnl'].agg(['count','sum','mean']).to_string())
