"""Robustness tests on Donchian D1 winner."""
import pandas as pd
import numpy as np

CSV = "xauusd_m5_5y.csv"
COMMISSION = 0.5; SPREAD = 0.50; SLIPPAGE = 0.20

print("Loading + aggregating...", flush=True)
m5 = pd.read_csv(CSV, index_col=0, parse_dates=True)
m5.index = pd.to_datetime(m5.index, utc=True)
m5.columns = [c.lower() for c in m5.columns]
d1 = m5.resample('1D').agg(
    open=('open', 'first'),
    high=('high', 'max'),
    low=('low', 'min'),
    close=('close', 'last'),
).dropna()
print(f"D1: {len(d1)} bars from {d1.index[0]} to {d1.index[-1]}", flush=True)

hl = d1['high']-d1['low']; hc = (d1['high']-d1['close'].shift()).abs(); lc = (d1['low']-d1['close'].shift()).abs()
tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
d1['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()

def donchian(df_, breakout=20, exit_periods=10, sl_atr=2.0, long_only=True, trail_atr=None):
    df_ = df_.copy()
    df_['don_high'] = df_['high'].rolling(breakout).max().shift(1)
    df_['don_low'] = df_['low'].rolling(breakout).min().shift(1)
    df_['exit_low'] = df_['low'].rolling(exit_periods).min().shift(1)
    df_['exit_high'] = df_['high'].rolling(exit_periods).max().shift(1)
    trades = []; pos = None
    for i in range(breakout+5, len(df_)):
        bar = df_.iloc[i]; ts = df_.index[i]
        if pos is not None:
            sgn = 1 if pos['side']=='L' else -1
            # Trailing stop update
            if trail_atr is not None:
                if pos['side']=='L':
                    pos['trail_high'] = max(pos.get('trail_high', pos['e']), bar['high'])
                    new_sl = pos['trail_high'] - trail_atr * pos['atr0']
                    pos['sl'] = max(pos['sl'], new_sl)
                else:
                    pos['trail_high'] = min(pos.get('trail_high', pos['e']), bar['low'])
                    new_sl = pos['trail_high'] + trail_atr * pos['atr0']
                    pos['sl'] = min(pos['sl'], new_sl)
            sl_h = bar['low'] <= pos['sl'] if pos['side']=='L' else bar['high'] >= pos['sl']
            if sl_h:
                price = pos['sl']
                pnl = (price - pos['e'])*sgn - SLIPPAGE - COMMISSION*2 - SPREAD
                trades.append({'ts': pos['ts'], 'pnl': pnl, 'side': pos['side'], 'exit_reason':'SL'})
                pos = None
            elif pos['side']=='L' and bar['close'] < bar['exit_low']:
                pnl = (bar['close'] - pos['e']) - SLIPPAGE - COMMISSION*2 - SPREAD
                trades.append({'ts': pos['ts'], 'pnl': pnl, 'side': pos['side'], 'exit_reason':'Don_exit'})
                pos = None
            elif pos['side']=='S' and bar['close'] > bar['exit_high']:
                pnl = (pos['e'] - bar['close']) - SLIPPAGE - COMMISSION*2 - SPREAD
                trades.append({'ts': pos['ts'], 'pnl': pnl, 'side': pos['side'], 'exit_reason':'Don_exit'})
                pos = None
        if pos is None and not pd.isna(bar['don_high']):
            atr = bar['atr']
            if pd.isna(atr): continue
            if bar['close'] > bar['don_high']:
                pos = {'side':'L', 'e':bar['close'], 'ts':ts, 'sl':bar['close']-atr*sl_atr, 'atr0':atr, 'trail_high':bar['close']}
            elif (not long_only) and bar['close'] < bar['don_low']:
                pos = {'side':'S', 'e':bar['close'], 'ts':ts, 'sl':bar['close']+atr*sl_atr, 'atr0':atr, 'trail_high':bar['close']}
    return trades

def stats(trades, name):
    if not trades: print(f"{name:>40}: NO trades"); return None
    arr = np.array([t['pnl'] for t in trades])
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    print(f"{name:>40}: n={n:>4} | WR {w/n*100:>5.1f}% | Net ${net:>+9.2f} | PF {pf:>5.2f} | DD ${dd:>7.2f}", flush=True)
    return {'n':n,'wr':w/n*100,'net':net,'pf':pf,'dd':dd}

print()
print("="*120)
print("DONCHIAN D1 — Sensibilitat de paràmetres:")
print("="*120)

# Variants of breakout/exit periods
configs = [
    (10, 5, "10/5"),
    (20, 10, "20/10 (faster turtle)"),
    (20, 20, "20/20"),
    (30, 15, "30/15"),
    (40, 20, "40/20"),
    (55, 20, "55/20 (slow turtle)"),
    (55, 30, "55/30"),
    (70, 30, "70/30"),
    (100, 40, "100/40"),
]
for bo, exit_p, label in configs:
    trades = donchian(d1, breakout=bo, exit_periods=exit_p, long_only=True)
    stats(trades, f"D1 {label} LONG")

print()
print("="*120)
print("DONCHIAN 55/20 — Walk-forward Robustness:")
print("="*120)
trades = donchian(d1, breakout=55, exit_periods=20, long_only=True)

# Per year
tdf = pd.DataFrame(trades)
if len(tdf):
    tdf['year'] = pd.to_datetime(tdf['ts']).dt.year
    print("Per year:", flush=True)
    for yr in sorted(tdf['year'].unique()):
        ydf = tdf[tdf['year']==yr]
        arr = ydf['pnl'].values
        n=len(arr); w=(arr>0).sum(); net=arr.sum()
        pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
        pf=pf_p/pf_l if pf_l else 0
        print(f"  {yr}: n={n:>3} | WR {w/n*100 if n else 0:>5.1f}% | Net ${net:>+9.2f} | PF {pf:>5.2f}", flush=True)

# Walk-forward 50/50
mid_idx = len(trades)//2
if mid_idx >= 5:
    is_t = trades[:mid_idx]
    oos_t = trades[mid_idx:]
    print("\nWalk-forward (50/50):", flush=True)
    stats(is_t, "IS 50% (training period)")
    stats(oos_t, "OOS 50% (validation period)")

print()
print("="*120)
print("DONCHIAN 55/20 — Trailing stop variants:")
print("="*120)
trades = donchian(d1, breakout=55, exit_periods=20, long_only=True, trail_atr=None)
stats(trades, "No trailing")
trades = donchian(d1, breakout=55, exit_periods=20, long_only=True, trail_atr=3.0)
stats(trades, "Trail 3xATR")
trades = donchian(d1, breakout=55, exit_periods=20, long_only=True, trail_atr=2.0)
stats(trades, "Trail 2xATR")
trades = donchian(d1, breakout=55, exit_periods=20, long_only=True, trail_atr=4.0)
stats(trades, "Trail 4xATR")

print()
print("="*120)
print("DONCHIAN 55/20 with SL variants:")
print("="*120)
for sl in [1.0, 1.5, 2.0, 2.5, 3.0]:
    trades = donchian(d1, breakout=55, exit_periods=20, long_only=True, sl_atr=sl)
    stats(trades, f"SL {sl}xATR")
