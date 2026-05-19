"""
TEST 1: Kelly fractional sizing
Apply dynamic position size based on:
- Recent win/loss streak
- Recent equity peak vs current
- Account drawdown state

Compare to fixed 1-unit baseline.
PASS: Same/better return with LOWER drawdown OR higher return same DD.
"""
import pandas as pd
import numpy as np

# Load winning config trades (from final combo: LONG+skip Wed+Asia+TP15/30+SL1.5)
# Need to regenerate since CSV is from baseline
import MetaTrader5 as mt5
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
    hl = df['high']-df['low']; hc = (df['high']-df['close'].shift()).abs(); lc = (df['low']-df['close'].shift()).abs()
    tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
    df['atr'] = tr.ewm(alpha=1/ATR_LEN, adjust=False).mean()
    df['vol_avg'] = df['tick_volume'].rolling(VOL_LEN).mean()
    df['inside'] = (df['high']<df['high'].shift(1)) & (df['low']>df['low'].shift(1))
    return df

def backtest_winning_config(df):
    """LONG + skip Wed + Asia + TP 15/30 + SL 1.5"""
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
            if ts.dayofweek == 2: continue
            if not (0 <= ts.hour <= 6): continue
            if bar['close']<=bar['ema50']: continue
            mh = pp['high']
            if not (bar['high']>mh and bar['close']>mh): continue
            atr = bar['atr']; e = bar['close']
            pos = {'e':e,'ts':ts,'sl':e-atr*SL_M,'tp1':e+atr*TP1_M,'tp2':e+atr*TP2_M,'q1':0.5,'q2':0.5}
    return trades

def stats(pnl_series, eq_series, name):
    arr = np.array(pnl_series)
    n = len(arr)
    if n == 0:
        print(f"{name}: no trades"); return
    w = (arr>0).sum()
    net = arr.sum()
    pf_p = arr[arr>0].sum(); pf_l = abs(arr[arr<=0].sum())
    pf = pf_p/pf_l if pf_l else 0
    eq = np.array(eq_series)
    peak = np.maximum.accumulate(eq)
    dd_abs = (peak - eq).max()
    dd_pct = ((peak - eq)/peak).max() * 100
    avg = arr.mean()
    final_eq = eq[-1] if len(eq) else 0
    print(f"{name:<35}: n={n} | WR {w/n*100:>4.1f}% | NetPnL {net:>+8.2f} | EndEq {final_eq:>9.2f} | PF {pf:>5.2f} | DD$ {dd_abs:>6.2f} ({dd_pct:>4.1f}%)")

def fixed_size(trades, size=1.0, capital=10000):
    """Fixed 1 unit per trade."""
    pnl = []
    eq = [capital]
    for t in trades:
        p = t['pnl'] * size
        pnl.append(p)
        eq.append(eq[-1] + p)
    return pnl, eq

def kelly_fractional(trades, kelly_pct=0.25, capital=10000, max_size=10.0, min_size=0.5):
    """
    Kelly fractional: size = kelly_pct × (WR - (1-WR)/R) of capital
    where R = avg_win / avg_loss
    Update Kelly estimate with rolling 30-trade window.
    Reduce size after losses, restore after wins.
    """
    pnl = []
    eq = [capital]
    sizes = []
    rolling_pnl = []

    for i, t in enumerate(trades):
        # Kelly calc from prior trades only (no future leak)
        if len(rolling_pnl) >= 20:
            arr = np.array(rolling_pnl[-50:])  # last 50 trades
            wins = arr[arr > 0]
            losses = arr[arr <= 0]
            if len(wins) > 0 and len(losses) > 0:
                wr = len(wins) / len(arr)
                avg_w = wins.mean()
                avg_l = abs(losses.mean()) if len(losses) > 0 else 1
                R = avg_w / avg_l if avg_l > 0 else 1
                kelly = wr - (1 - wr) / R
                size_kelly = max(min_size, min(max_size, kelly_pct * kelly * 10))
            else:
                size_kelly = 1.0
        else:
            size_kelly = 1.0

        # Drawdown protection: reduce size if recent DD > 5%
        if len(eq) > 5:
            recent_peak = max(eq[-20:])
            dd_now = (recent_peak - eq[-1]) / recent_peak
            if dd_now > 0.05:
                size_kelly *= 0.5  # halve size during drawdown

        sizes.append(size_kelly)
        p = t['pnl'] * size_kelly
        pnl.append(p)
        eq.append(eq[-1] + p)
        rolling_pnl.append(t['pnl'])  # raw pnl for stats

    return pnl, eq, sizes

def streak_based(trades, capital=10000):
    """
    Reduce size after 3 consecutive losses, restore after a win.
    Increase size after 3 consecutive wins (max 2×).
    """
    pnl = []
    eq = [capital]
    sizes = []
    consec_l = 0
    consec_w = 0
    size = 1.0

    for t in trades:
        if t['pnl'] > 0:
            consec_w += 1; consec_l = 0
            if consec_w >= 3 and size < 2.0:
                size = min(2.0, size * 1.3)
        else:
            consec_l += 1; consec_w = 0
            if consec_l >= 2:
                size = max(0.5, size * 0.7)
            elif consec_l == 1:
                pass  # keep
        if t['pnl'] > 0 and consec_w == 1 and size < 1.0:
            size = 1.0  # back to base after a win

        sizes.append(size)
        p = t['pnl'] * size
        pnl.append(p)
        eq.append(eq[-1] + p)

    return pnl, eq, sizes

print("Fetching data...")
df = fetch()
df = compute(df)
print(f"Data: {len(df)} bars\n")

print("Backtesting winning config...")
trades = backtest_winning_config(df)
print(f"Got {len(trades)} trades\n")

print("="*100)
print("SIZING COMPARISON:")
print("="*100)
p1, e1 = fixed_size(trades, size=1.0)
stats(p1, e1, "Fixed 1×")

p2, e2 = fixed_size(trades, size=5.0)
stats(p2, e2, "Fixed 5× (reference)")

p3, e3, s3 = kelly_fractional(trades, kelly_pct=0.25)
stats(p3, e3, "Kelly fractional 25%")
print(f"  Avg size: {np.mean(s3):.2f}, range {min(s3):.2f}-{max(s3):.2f}")

p4, e4, s4 = kelly_fractional(trades, kelly_pct=0.5)
stats(p4, e4, "Kelly fractional 50%")
print(f"  Avg size: {np.mean(s4):.2f}, range {min(s4):.2f}-{max(s4):.2f}")

p5, e5, s5 = streak_based(trades)
stats(p5, e5, "Streak-based sizing")
print(f"  Avg size: {np.mean(s5):.2f}, range {min(s5):.2f}-{max(s5):.2f}")

# Apply equivalent leverage to streak vs fixed for fair comparison
target_avg = np.mean(s5)
p6, e6 = fixed_size(trades, size=target_avg)
print()
print(f"FAIR COMPARISON (same avg size {target_avg:.2f}):")
stats(p6, e6, f"Fixed {target_avg:.2f}× (= streak avg)")

# Verdict
print("\n" + "="*100)
fixed_5 = e2[-1] - 10000
streak_net = e5[-1] - 10000
fixed_5_dd = ((np.maximum.accumulate(e2) - e2) / np.maximum.accumulate(e2)).max() * 100
streak_dd = ((np.maximum.accumulate(e5) - e5) / np.maximum.accumulate(e5)).max() * 100

print(f"Fixed 5×: Net=${fixed_5:.2f}, DD%={fixed_5_dd:.2f}%")
print(f"Streak:   Net=${streak_net:.2f}, DD%={streak_dd:.2f}%")
print()
if streak_net > fixed_5 * 0.95 and streak_dd < fixed_5_dd * 0.9:
    print(">>> KELLY/STREAK SIZING PASSES: better risk-adjusted return")
elif streak_net > fixed_5 * 1.1:
    print(">>> KELLY/STREAK SIZING PASSES: significantly higher return")
else:
    print(">>> KELLY/STREAK SIZING DOES NOT PASS: not meaningfully better")
