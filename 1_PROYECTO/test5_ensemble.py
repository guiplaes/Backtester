"""
TEST 5: Multi-strategy ensemble.
Run multiple independent strategies, see if combined gives more trades
with similar/better edge.

Strategies tested:
A. Inside Bar Breakout (winning config) — already validated
B. Pin Bar / Hammer reversal at EMA20 (new)
C. NR4 (Narrow Range 4): tightest range in 4 bars + breakout (new)
D. Engulfing reversal at EMA50 (new)

Each runs with same SL/TP profile (1.5×ATR / 15×ATR / 30×ATR) + LONG only +
skip Wednesday + Asia session.

PASS: Net P/L of ensemble > 1.2× best single strategy.
"""
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
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True); df = df.set_index('time')
    mt5.shutdown(); return df

def compute(df):
    df = df.copy()
    df['ema50'] = df['close'].ewm(span=EMA_LEN, adjust=False).mean()
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    hl = df['high']-df['low']; hc = (df['high']-df['close'].shift()).abs(); lc = (df['low']-df['close'].shift()).abs()
    tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
    df['atr'] = tr.ewm(alpha=1/ATR_LEN, adjust=False).mean()
    df['vol_avg'] = df['tick_volume'].rolling(VOL_LEN).mean()
    df['range'] = df['high'] - df['low']
    df['body'] = abs(df['close'] - df['open'])
    df['upper_wick'] = df['high'] - df[['close','open']].max(axis=1)
    df['lower_wick'] = df[['close','open']].min(axis=1) - df['low']
    # Patterns
    df['inside'] = (df['high']<df['high'].shift(1)) & (df['low']>df['low'].shift(1))
    df['nr4'] = df['range'] < df[['range']].rolling(4).min().shift(1).iloc[:,0]  # tightest range in 4 bars
    df['hammer'] = (df['lower_wick'] > 2 * df['body']) & (df['upper_wick'] < df['body']) & (df['body'] > 0)
    df['shooting'] = (df['upper_wick'] > 2 * df['body']) & (df['lower_wick'] < df['body']) & (df['body'] > 0)
    df['bullish_engulfing'] = (df['close'] > df['open']) & (df['close'] > df['high'].shift(1)) & (df['open'] < df['low'].shift(1))
    return df

def common_filters(bar, ts):
    """Asia session, LONG only, skip Wednesday."""
    if ts.dayofweek == 2: return False
    if not (0 <= ts.hour <= 6): return False
    if pd.isna(bar['ema50']) or pd.isna(bar['atr']) or pd.isna(bar['vol_avg']): return False
    if bar['close'] <= bar['ema50']: return False
    if bar['tick_volume'] <= bar['vol_avg']*1.3: return False
    return True

def detect_inside_bar_bo(df, i, bar, ts):
    if i < 2: return False
    prev = df.iloc[i-1]; pp = df.iloc[i-2]
    if not prev['inside']: return False
    return bar['high'] > pp['high'] and bar['close'] > pp['high']

def detect_nr4_bo(df, i, bar, ts):
    if i < 4: return False
    prev = df.iloc[i-1]
    if not prev['nr4']: return False
    return bar['close'] > prev['high']  # breakout above NR4 high

def detect_hammer_at_ema(df, i, bar, ts):
    """Hammer touching EMA20 from below, reversing back up."""
    if not bar['hammer']: return False
    if not (bar['low'] <= bar['ema20'] <= bar['close']): return False
    return True

def detect_bullish_engulfing(df, i, bar, ts):
    if i < 1: return False
    if not bar['bullish_engulfing']: return False
    # Engulfing must be at/near EMA20 (pullback context)
    return abs(bar['low'] - bar['ema20']) / bar['atr'] < 0.5 if bar['atr'] else False

DETECTORS = {
    'A_InsideBar': detect_inside_bar_bo,
    'B_NR4_BO': detect_nr4_bo,
    'C_Hammer': detect_hammer_at_ema,
    'D_Engulfing': detect_bullish_engulfing,
}

def backtest_strategies(df, detectors_to_use, allow_concurrent=False):
    """Run with given detectors. allow_concurrent=False means 1 trade at a time."""
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
                trades.append({'ts': pos['ts'], 'pnl': tp, 'detector': pos['detector']})
                pos = None

        if pos is None and common_filters(bar, ts):
            for name, det in detectors_to_use.items():
                if det(df, i, bar, ts):
                    atr = bar['atr']; e = bar['close']
                    pos = {'e':e,'ts':ts,'sl':e-atr*SL_M,'tp1':e+atr*TP1_M,'tp2':e+atr*TP2_M,
                           'q1':0.5,'q2':0.5,'detector':name}
                    break
    return trades

def stats(trades, name):
    if not trades: print(f"{name}: NO trades"); return None
    arr = np.array([t['pnl'] for t in trades])
    n = len(arr); w = (arr>0).sum(); net = arr.sum()
    pf_p = arr[arr>0].sum(); pf_l = abs(arr[arr<=0].sum())
    pf = pf_p/pf_l if pf_l else 0
    eq = np.cumsum(arr); peak = np.maximum.accumulate(eq); dd = (peak-eq).max()
    print(f"{name:>30}: n={n} | WR {w/n*100:>5.1f}% | Net ${net:>+8.2f} | PF {pf:>5.2f} | DD ${dd:>6.2f}")
    return {'n':n, 'pf':pf, 'net':net}

print("Fetching..."); df = fetch(); df = compute(df); print(f"{len(df)} bars\n")
print("="*100)
print("INDIVIDUAL STRATEGIES:")
print("="*100)

results = {}
for name, det in DETECTORS.items():
    t = backtest_strategies(df, {name: det})
    s = stats(t, name)
    results[name] = (t, s)

print("\n" + "="*100)
print("ENSEMBLE COMBINATIONS:")
print("="*100)

# Strategy A only (baseline)
t_A = results['A_InsideBar'][0]
s_A = stats(t_A, "Solo A (baseline)")

# A + best companion
combos = [
    ('A+B', {'A_InsideBar':DETECTORS['A_InsideBar'], 'B_NR4_BO':DETECTORS['B_NR4_BO']}),
    ('A+C', {'A_InsideBar':DETECTORS['A_InsideBar'], 'C_Hammer':DETECTORS['C_Hammer']}),
    ('A+D', {'A_InsideBar':DETECTORS['A_InsideBar'], 'D_Engulfing':DETECTORS['D_Engulfing']}),
    ('All 4', DETECTORS),
]
for name, dets in combos:
    t = backtest_strategies(df, dets)
    s = stats(t, name)

# Verdict
print("\n" + "="*100)
best_solo_net = max(r[1]['net'] for r in results.values() if r[1])
all4_t = backtest_strategies(df, DETECTORS)
all4_net = sum(t['pnl'] for t in all4_t)
print(f"Best solo Net: ${best_solo_net:.2f}")
print(f"All 4 ensemble Net: ${all4_net:.2f}")
ratio = all4_net / best_solo_net if best_solo_net > 0 else 0
print(f"Ratio ensemble/best: {ratio:.2f}×")
if ratio >= 1.20:
    print(">>> ENSEMBLE PASSES (>20% improvement)")
else:
    print(">>> ENSEMBLE FAILS (no meaningful improvement)")
