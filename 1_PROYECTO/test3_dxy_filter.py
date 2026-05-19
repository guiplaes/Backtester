"""
TEST 3: DXY (US Dollar Index) correlation filter.
Gold has historical -0.7 to -0.9 correlation with DXY.
Filter: skip Inside Bar LONGS if DXY is RISING strongly in last 30-60 min.

Use TVMaze... no wait, fetch DXY from MT5 too.
DXY symbol candidates: USDX, DXY, DXY.crp, DOLLAR
"""
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timezone

XAUUSD = "XAUUSD.crp"
ATR_LEN = 14; EMA_LEN = 50; VOL_LEN = 20
COMMISSION = 0.5; SPREAD = 0.50; SLIPPAGE = 0.20
SL_M = 1.5; TP1_M = 15; TP2_M = 30

mt5.initialize()
# Find DXY equivalent
print("Searching for DXY/USDX symbols...")
candidates = mt5.symbols_get('*DXY*') + mt5.symbols_get('*USDX*') + mt5.symbols_get('*DOLLAR*')
seen = set(); uniq = []
for s in candidates:
    if s.name not in seen:
        seen.add(s.name); uniq.append(s.name)
print("Found:", uniq[:10])

# Fallback: synthesize DXY from EUR/USD (DXY weight 57.6% EUR)
# We'll use 1/EURUSD as approximation
dxy_sym = None
for c in uniq:
    if 'USDX' in c or 'DXY' in c:
        info = mt5.symbol_info(c)
        if info:
            mt5.symbol_select(c, True)
            r = mt5.copy_rates_from(c, mt5.TIMEFRAME_M5, datetime.now(timezone.utc), 100)
            if r is not None and len(r) > 0:
                dxy_sym = c
                print(f"Using DXY symbol: {c}")
                break

if dxy_sym is None:
    print("No DXY found. Using EURUSD inverse as proxy.")
    eur = 'EURUSD.crp'
    info = mt5.symbol_info(eur)
    if info:
        mt5.symbol_select(eur, True)
        r = mt5.copy_rates_from(eur, mt5.TIMEFRAME_M5, datetime.now(timezone.utc), 100)
        if r is not None and len(r) > 0:
            dxy_sym = eur
            print(f"Using {eur} inverse as DXY proxy.")

mt5.shutdown()

if dxy_sym is None:
    print("FAIL: No DXY equivalent found. Test 3 cannot run.")
    exit(0)

def fetch(sym):
    mt5.initialize(); mt5.symbol_select(sym, True)
    end = datetime.now(timezone.utc)
    rates = mt5.copy_rates_from(sym, mt5.TIMEFRAME_M5, end, 50000)
    if len(rates) >= 50000:
        oldest = datetime.fromtimestamp(int(rates[0]['time']), tz=timezone.utc)
        rates2 = mt5.copy_rates_from(sym, mt5.TIMEFRAME_M5, oldest, 50000)
        if rates2 is not None:
            rates = np.concatenate([rates2, rates])
            _, idx = np.unique(rates['time'], return_index=True); rates = rates[np.sort(idx)]
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True); df = df.set_index('time')
    mt5.shutdown(); return df

print(f"\nFetching XAUUSD...")
xau = fetch(XAUUSD)
print(f"  {len(xau)} bars from {xau.index[0]} to {xau.index[-1]}")

print(f"Fetching {dxy_sym}...")
dxy = fetch(dxy_sym)
print(f"  {len(dxy)} bars from {dxy.index[0]} to {dxy.index[-1]}")

# If using EURUSD as proxy, convert to "DXY-like" (inverse)
is_proxy = 'EUR' in dxy_sym
if is_proxy:
    dxy['close'] = 1.0 / dxy['close']  # invert: high EURUSD = low DXY
    print("  (Inverted EUR/USD to approximate DXY direction)")

# Compute DXY 1h change at each xau timestamp
dxy_close = dxy['close'].reindex(xau.index, method='ffill')
xau['dxy'] = dxy_close
xau['dxy_change_1h'] = (xau['dxy'] / xau['dxy'].shift(12) - 1) * 100  # 12 M5 bars = 1h
xau['dxy_change_30m'] = (xau['dxy'] / xau['dxy'].shift(6) - 1) * 100

# Indicators for XAU
xau['ema50'] = xau['close'].ewm(span=EMA_LEN, adjust=False).mean()
hl = xau['high']-xau['low']; hc = (xau['high']-xau['close'].shift()).abs(); lc = (xau['low']-xau['close'].shift()).abs()
tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
xau['atr'] = tr.ewm(alpha=1/ATR_LEN, adjust=False).mean()
xau['vol_avg'] = xau['tick_volume'].rolling(VOL_LEN).mean()
xau['inside'] = (xau['high']<xau['high'].shift(1)) & (xau['low']>xau['low'].shift(1))

def backtest(df, dxy_filter_threshold=None):
    """If dxy_filter_threshold given, skip LONG if DXY rose more than threshold% in last 1h."""
    trades = []; pos = None; skipped = 0
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
                trades.append({'ts': pos['ts'], 'pnl': tp, 'dxy_chg': pos.get('dxy_chg', 0)})
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

            # DXY filter
            dxy_chg = bar.get('dxy_change_1h', 0)
            if dxy_filter_threshold is not None and not pd.isna(dxy_chg):
                if dxy_chg > dxy_filter_threshold:
                    skipped += 1
                    continue

            atr = bar['atr']; e = bar['close']
            pos = {'e':e,'ts':ts,'sl':e-atr*SL_M,'tp1':e+atr*TP1_M,'tp2':e+atr*TP2_M,
                   'q1':0.5,'q2':0.5,'dxy_chg':dxy_chg if not pd.isna(dxy_chg) else 0}
    return trades, skipped

def stats(trades, name):
    if not trades: print(f"{name}: NO trades"); return None
    arr = np.array([t['pnl'] for t in trades])
    n = len(arr); w = (arr>0).sum(); net = arr.sum()
    pf_p = arr[arr>0].sum(); pf_l = abs(arr[arr<=0].sum())
    pf = pf_p/pf_l if pf_l else 0
    eq = np.cumsum(arr); peak = np.maximum.accumulate(eq); dd = (peak-eq).max()
    print(f"{name:>32}: n={n} | WR {w/n*100:>5.1f}% | Net ${net:>+8.2f} | PF {pf:>5.2f} | DD ${dd:>6.2f}")
    return {'n':n, 'wr':w/n*100, 'net':net, 'pf':pf, 'dd':dd}

print("\n" + "="*100)
print("TEST 3 — DXY/EURUSD-proxy correlation filter")
print("="*100)

t_base, _ = backtest(xau, None)
s_base = stats(t_base, "Baseline (no DXY filter)")

# Test multiple thresholds
for thresh in [0.5, 0.3, 0.2, 0.1, 0.0]:
    t, sk = backtest(xau, thresh)
    name = f"Skip if DXY rose >+{thresh}%/1h"
    s = stats(t, name)
    if s:
        delta_pf = s['pf'] - s_base['pf']
        print(f"  Skipped {sk} | Delta PF: {delta_pf:+.2f}")

# Show DXY change distribution at trade times
import collections
buckets = collections.Counter()
for t in t_base:
    chg = t.get('dxy_chg', 0)
    if chg > 0.3: buckets['DXY +0.3%+'] += 1
    elif chg > 0.1: buckets['DXY +0.1-0.3%'] += 1
    elif chg > -0.1: buckets['DXY flat'] += 1
    elif chg > -0.3: buckets['DXY -0.1-0.3%'] += 1
    else: buckets['DXY -0.3%+'] += 1
print("\nTrade DXY change distribution:")
for k, v in buckets.items():
    sub = [t['pnl'] for t in t_base if (
        (k=='DXY +0.3%+' and t.get('dxy_chg',0)>0.3) or
        (k=='DXY +0.1-0.3%' and 0.1<t.get('dxy_chg',0)<=0.3) or
        (k=='DXY flat' and -0.1<t.get('dxy_chg',0)<=0.1) or
        (k=='DXY -0.1-0.3%' and -0.3<t.get('dxy_chg',0)<=-0.1) or
        (k=='DXY -0.3%+' and t.get('dxy_chg',0)<=-0.3)
    )]
    avg = np.mean(sub) if sub else 0
    print(f"  {k}: {v} trades, avg ${avg:+.2f}")

# Verdict — best result
best_pf = s_base['pf']
best_cfg = "Baseline"
for thresh in [0.5, 0.3, 0.2, 0.1, 0.0]:
    t, sk = backtest(xau, thresh)
    s = stats(t, "")
    if s and s['pf'] > best_pf:
        best_pf = s['pf']
        best_cfg = f"DXY threshold {thresh}"

delta = best_pf - s_base['pf']
print(f"\nBest: {best_cfg} (PF {best_pf:.2f}, delta {delta:+.2f})")
if delta >= 0.20:
    print(">>> DXY FILTER PASSES")
else:
    print(">>> DXY FILTER FAILS (delta < 0.20)")
