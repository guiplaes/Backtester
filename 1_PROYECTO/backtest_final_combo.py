"""
Final combination test: best grid params + best filters
"""
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timezone

SYMBOL = "XAUUSD.crp"
ATR_LEN = 14
EMA_LEN = 50
VOL_LEN = 20
COMMISSION = 0.5
SPREAD = 0.50
SLIPPAGE = 0.20

def fetch_data():
    mt5.initialize()
    mt5.symbol_select(SYMBOL, True)
    end = datetime.now(timezone.utc)
    rates = mt5.copy_rates_from(SYMBOL, mt5.TIMEFRAME_M5, end, 50000)
    if len(rates) >= 50000:
        oldest = datetime.fromtimestamp(int(rates[0]['time']), tz=timezone.utc)
        rates2 = mt5.copy_rates_from(SYMBOL, mt5.TIMEFRAME_M5, oldest, 50000)
        if rates2 is not None:
            rates = np.concatenate([rates2, rates])
            _, idx = np.unique(rates['time'], return_index=True)
            rates = rates[np.sort(idx)]
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df = df.set_index('time')
    mt5.shutdown()
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

def backtest(df, sl_m, tp1_m, tp2_m, vol_m=1.3, long_only=False, skip_wed=False, asia_only=False):
    trades = []
    pos = None
    for i in range(max(EMA_LEN, ATR_LEN, VOL_LEN) + 5, len(df)):
        bar = df.iloc[i]
        ts = df.index[i]
        if pos is not None:
            side = pos['side']
            sl = pos['sl']; tp1 = pos['tp1']; tp2 = pos['tp2']
            if side == 'L':
                sl_h = bar['low'] <= sl
                tp1_h = bar['high'] >= tp1
                tp2_h = bar['high'] >= tp2
                if sl_h and (tp1_h or tp2_h):
                    op = bar['open']
                    if (op-sl) < (tp1-op): tp1_h=False; tp2_h=False
            else:
                sl_h = bar['high'] >= sl
                tp1_h = bar['low'] <= tp1
                tp2_h = bar['low'] <= tp2
                if sl_h and (tp1_h or tp2_h):
                    op = bar['open']
                    if (sl-op) < (op-tp1): tp1_h=False; tp2_h=False
            if tp1_h and pos['q1']>0:
                pnl = (tp1-pos['e'])*0.5*(1 if side=='L' else -1) - SLIPPAGE*0.5
                pos['pnl1']=pnl; pos['q1']=0
            if tp2_h and pos['q2']>0:
                pnl = (tp2-pos['e'])*0.5*(1 if side=='L' else -1) - SLIPPAGE*0.5
                pos['pnl2']=pnl; pos['q2']=0
            if sl_h:
                if pos['q1']>0:
                    pnl = (sl-pos['e'])*0.5*(1 if side=='L' else -1) - SLIPPAGE*0.5
                    pos['pnl1']=pnl; pos['q1']=0
                if pos['q2']>0:
                    pnl = (sl-pos['e'])*0.5*(1 if side=='L' else -1) - SLIPPAGE*0.5
                    pos['pnl2']=pnl; pos['q2']=0
            if pos['q1']==0 and pos['q2']==0:
                tp = pos.get('pnl1',0) + pos.get('pnl2',0) - COMMISSION*2 - SPREAD
                trades.append({'ts': pos['ts'], 'side': side, 'pnl': tp, 'atr': pos['atr']})
                pos = None
        if pos is None and i>=2:
            prev = df.iloc[i-1]; prev_prev = df.iloc[i-2]
            if not prev['inside']: continue
            if pd.isna(bar['ema50']) or pd.isna(bar['atr']) or pd.isna(bar['vol_avg']): continue
            if bar['tick_volume'] <= bar['vol_avg']*vol_m: continue
            # Filters
            if skip_wed and ts.dayofweek == 2: continue  # Wednesday
            if asia_only and not (0 <= ts.hour <= 6): continue
            mh = prev_prev['high']; ml = prev_prev['low']
            atr = bar['atr']; e = bar['close']
            if bar['close'] > bar['ema50'] and bar['high']>mh and bar['close']>mh:
                pos = {'side':'L','e':e,'ts':ts,'atr':atr,'sl':e-atr*sl_m,'tp1':e+atr*tp1_m,'tp2':e+atr*tp2_m,'q1':0.5,'q2':0.5}
            elif (not long_only) and bar['close'] < bar['ema50'] and bar['low']<ml and bar['close']<ml:
                pos = {'side':'S','e':e,'ts':ts,'atr':atr,'sl':e+atr*sl_m,'tp1':e-atr*tp1_m,'tp2':e-atr*tp2_m,'q1':0.5,'q2':0.5}
    return trades

def stats(trades, name):
    if not trades:
        print(f"{name}: NO trades")
        return
    arr = np.array([t['pnl'] for t in trades])
    n = len(arr); w = (arr>0).sum(); net = arr.sum()
    pf_p = arr[arr>0].sum(); pf_l = abs(arr[arr<=0].sum())
    pf = pf_p/pf_l if pf_l else 0
    eq = np.cumsum(arr); peak = np.maximum.accumulate(eq); dd = (peak-eq).max()
    avg = arr.mean()
    print(f"{name:>50}: {n:>4} | WR {w/n*100:>5.1f}% | Net ${net:>+8.2f} | Avg ${avg:>+5.2f} | PF {pf:>5.2f} | DD ${dd:>6.2f}")

print("Fetching...")
df = fetch_data()
print(f"{len(df)} bars from {df.index[0].date()} to {df.index[-1].date()}\n")
df = compute(df)

configs = [
    ("Baseline 10/20 SL1.5", 1.5, 10, 20, 1.3, False, False, False),
    ("LONG only 10/20 SL1.5", 1.5, 10, 20, 1.3, True, False, False),
    ("LONG + skip Wed 10/20", 1.5, 10, 20, 1.3, True, True, False),
    ("LONG + skip Wed 15/30 SL1.5", 1.5, 15, 30, 1.3, True, True, False),
    ("LONG + skip Wed 15/30 SL2.0", 2.0, 15, 30, 1.3, True, True, False),
    ("LONG + skip Wed + Asia 15/30", 1.5, 15, 30, 1.3, True, True, True),
    ("LONG + skip Wed + Asia 10/20", 1.5, 10, 20, 1.3, True, True, True),
    ("LONG + skip Wed + Vol 2.0", 1.5, 10, 20, 2.0, True, True, False),
    ("Both + skip Wed 15/30", 1.5, 15, 30, 1.3, False, True, False),
]

for name, sl, tp1, tp2, vol, lo, sw, ao in configs:
    t = backtest(df, sl, tp1, tp2, vol, lo, sw, ao)
    stats(t, name)
