"""Diagnòstic: només UN backtest M15 amb prints constants."""
import pandas as pd
import numpy as np
import time

CSV = "xauusd_m5_5y.csv"
REAL_COST = 0.40

print("Loading...", flush=True)
m5 = pd.read_csv(CSV, index_col=0, parse_dates=True)
m5.index = pd.to_datetime(m5.index, utc=True)
m5.columns = [c.lower() for c in m5.columns]

print("Aggregating M15...", flush=True)
m15 = m5.resample('15min').agg(open=('open','first'),high=('high','max'),
    low=('low','min'),close=('close','last'),tick_volume=('tick_volume','sum') if 'tick_volume' in m5.columns else ('volume','sum')).dropna()
print(f"M15: {len(m15)} bars", flush=True)

print("Indicators...", flush=True)
m15['ema50'] = m15['close'].ewm(span=50, adjust=False).mean()
hl = m15['high']-m15['low']; hc = (m15['high']-m15['close'].shift()).abs(); lc = (m15['low']-m15['close'].shift()).abs()
tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
m15['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()

print("Converting to numpy...", flush=True)
O = m15['open'].values
H = m15['high'].values
L = m15['low'].values
C = m15['close'].values
EMA = m15['ema50'].values
ATR = m15['atr'].values
HOURS = m15.index.hour.values
WKD = m15.index.weekday.values
n = len(C)
print(f"n={n}", flush=True)

# Single backtest
def bt(sl_atr, tp1_atr, tp2_atr, ob_str):
    t0 = time.time()
    trades_pnl = []
    pos = None
    pending_lows = []
    pending_highs = []
    pending_atrs = []
    pending_expiry = []
    for i in range(50, n-3):
        if pos is not None:
            sl_h = L[i] <= pos[0]  # pos['sl']
            tp1_h = H[i] >= pos[1]  # pos['tp1']
            tp2_h = H[i] >= pos[2]  # pos['tp2']
            if sl_h and (tp1_h or tp2_h):
                if (O[i]-pos[0]) < (pos[1]-O[i]): tp1_h=False; tp2_h=False
            pnl1 = pos[3]; pnl2 = pos[4]; q1 = pos[5]; q2 = pos[6]; e = pos[7]
            if tp1_h and q1>0: pnl1 = (pos[1]-e)*0.5; q1=0
            if tp2_h and q2>0: pnl2 = (pos[2]-e)*0.5; q2=0
            if sl_h:
                if q1>0: pnl1 = (pos[0]-e)*0.5; q1=0
                if q2>0: pnl2 = (pos[0]-e)*0.5; q2=0
            if q1==0 and q2==0:
                trades_pnl.append(pnl1+pnl2 - REAL_COST)
                pos = None
            else:
                pos = (pos[0], pos[1], pos[2], pnl1, pnl2, q1, q2, e)

        b0_close = C[i-1]; b0_open = O[i-1]; b0_atr = ATR[i-1]
        if b0_close < b0_open and not np.isnan(b0_atr):
            future_high = max(H[i], H[i+1], H[i+2])
            move = future_high - b0_close
            if move > ob_str * b0_atr:
                pending_lows.append(L[i-1])
                pending_highs.append(H[i-1])
                pending_atrs.append(b0_atr)
                pending_expiry.append(i+30)

        # Clean expired
        keep = [j for j, exp in enumerate(pending_expiry) if exp > i]
        if len(keep) < len(pending_expiry):
            pending_lows = [pending_lows[j] for j in keep]
            pending_highs = [pending_highs[j] for j in keep]
            pending_atrs = [pending_atrs[j] for j in keep]
            pending_expiry = [pending_expiry[j] for j in keep]

        if pos is None and not np.isnan(EMA[i]) and C[i] > EMA[i]:
            for j in range(len(pending_lows)):
                if L[i] <= pending_highs[j] and C[i] > pending_lows[j]:
                    if C[i] > O[i]:
                        atr = pending_atrs[j]; e = C[i]
                        pos = (pending_lows[j]-atr*sl_atr*0.5,
                               e+atr*tp1_atr,
                               e+atr*tp2_atr,
                               0, 0, 0.5, 0.5, e)
                        # Remove that ob
                        pending_lows.pop(j); pending_highs.pop(j); pending_atrs.pop(j); pending_expiry.pop(j)
                        break
        if i % 10000 == 0:
            print(f"  i={i}/{n} elapsed={time.time()-t0:.1f}s trades={len(trades_pnl)}", flush=True)
    return trades_pnl

print("\nRunning single backtest M15 SL1.0 TP3/6 OB1.5...", flush=True)
t0 = time.time()
trades = bt(1.0, 3, 6, 1.5)
elapsed = time.time() - t0
print(f"Done in {elapsed:.1f}s, {len(trades)} trades", flush=True)

arr = np.array(trades)
if len(arr):
    print(f"Net: ${arr.sum():.0f}, WR: {(arr>0).mean()*100:.1f}%", flush=True)
    pf_p = arr[arr>0].sum(); pf_l = abs(arr[arr<=0].sum())
    pf = pf_p/pf_l if pf_l else 0
    print(f"PF: {pf:.2f}", flush=True)
