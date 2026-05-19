"""Donchian D1 — comparació LONG / SHORT / BOTH amb diferents configs."""
import pandas as pd
import numpy as np

CSV = "xauusd_m5_5y.csv"
COMMISSION = 0.5; SPREAD = 0.50; SLIPPAGE = 0.20
SWAP_LONG = -0.10   # per night per 0.01 lot LONG
SWAP_SHORT = +0.05  # per night per 0.01 lot SHORT (often positive for shorts on gold)
SWAP_WED_MULT = 3.0

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
hl = d1['high']-d1['low']; hc = (d1['high']-d1['close'].shift()).abs(); lc = (d1['low']-d1['close'].shift()).abs()
tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
d1['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
print(f"D1: {len(d1)} bars", flush=True)

def calc_swap(entry_ts, exit_ts, side):
    """Swap cost. LONG=negative, SHORT=usually positive on gold."""
    rate = SWAP_LONG if side == 'L' else SWAP_SHORT
    entry_d = pd.Timestamp(entry_ts).normalize()
    exit_d = pd.Timestamp(exit_ts).normalize()
    if exit_d <= entry_d: return 0
    total = 0
    cur = entry_d + pd.Timedelta(days=1)
    while cur <= exit_d:
        wd = cur.weekday()
        if wd in (5, 6):
            cur += pd.Timedelta(days=1); continue
        mult = SWAP_WED_MULT if wd == 2 else 1.0
        total += rate * mult
        cur += pd.Timedelta(days=1)
    return total

def donchian(df_, breakout=55, exit_periods=20, sl_atr=2.5, mode='long'):
    """mode: 'long' | 'short' | 'both'"""
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
            sl_h = bar['low'] <= pos['sl'] if pos['side']=='L' else bar['high'] >= pos['sl']
            if sl_h:
                price = pos['sl']
                pnl_raw = (price - pos['e'])*sgn - SLIPPAGE - COMMISSION*2 - SPREAD
                swap = calc_swap(pos['ts'], ts, pos['side'])
                pnl = pnl_raw + swap
                dur = (pd.Timestamp(ts) - pd.Timestamp(pos['ts'])).days
                trades.append({'ts': pos['ts'], 'exit_ts': ts, 'pnl_raw': pnl_raw, 'swap': swap, 'pnl': pnl,
                              'duration_days': dur, 'side': pos['side'], 'reason':'SL'})
                pos = None
            elif pos['side']=='L' and bar['close'] < bar['exit_low']:
                pnl_raw = (bar['close'] - pos['e']) - SLIPPAGE - COMMISSION*2 - SPREAD
                swap = calc_swap(pos['ts'], ts, pos['side'])
                pnl = pnl_raw + swap
                dur = (pd.Timestamp(ts) - pd.Timestamp(pos['ts'])).days
                trades.append({'ts': pos['ts'], 'exit_ts': ts, 'pnl_raw': pnl_raw, 'swap': swap, 'pnl': pnl,
                              'duration_days': dur, 'side': pos['side'], 'reason':'Don_exit'})
                pos = None
            elif pos['side']=='S' and bar['close'] > bar['exit_high']:
                pnl_raw = (pos['e'] - bar['close']) - SLIPPAGE - COMMISSION*2 - SPREAD
                swap = calc_swap(pos['ts'], ts, pos['side'])
                pnl = pnl_raw + swap
                dur = (pd.Timestamp(ts) - pd.Timestamp(pos['ts'])).days
                trades.append({'ts': pos['ts'], 'exit_ts': ts, 'pnl_raw': pnl_raw, 'swap': swap, 'pnl': pnl,
                              'duration_days': dur, 'side': pos['side'], 'reason':'Don_exit'})
                pos = None
        if pos is None and not pd.isna(bar['don_high']):
            atr = bar['atr']
            if pd.isna(atr): continue
            if mode in ('long', 'both') and bar['close'] > bar['don_high']:
                pos = {'side':'L', 'e':bar['close'], 'ts':ts, 'sl':bar['close']-atr*sl_atr}
            elif mode in ('short', 'both') and bar['close'] < bar['don_low']:
                pos = {'side':'S', 'e':bar['close'], 'ts':ts, 'sl':bar['close']+atr*sl_atr}
    return trades

def stats(trades, name):
    if not trades: print(f"{name:>50}: NO trades"); return
    arr = np.array([t['pnl'] for t in trades])
    arr_raw = np.array([t['pnl_raw'] for t in trades])
    swaps = np.array([t['swap'] for t in trades])
    sides = [t['side'] for t in trades]
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    n_long = sides.count('L'); n_short = sides.count('S')
    print(f"{name:>50}: n={n:>3} (L{n_long}/S{n_short}) | WR {w/n*100:>5.1f}% | "
          f"Raw ${arr_raw.sum():+.2f} | Swap ${swaps.sum():+.2f} | Net ${net:+.2f} | PF {pf:.2f} | DD ${dd:.2f}", flush=True)

print()
print("="*140)
print("DONCHIAN D1 — Comparació LONG / SHORT / BOTH amb swap REAL:")
print(f"Swap LONG: ${SWAP_LONG}/night | Swap SHORT: ${SWAP_SHORT}/night (positive! often) | Wed: {SWAP_WED_MULT}×")
print("="*140)

print("\n>>> 55/20 SL2.5:")
stats(donchian(d1, 55, 20, 2.5, mode='long'), "LONG only")
stats(donchian(d1, 55, 20, 2.5, mode='short'), "SHORT only")
stats(donchian(d1, 55, 20, 2.5, mode='both'), "BOTH (LONG+SHORT)")

print("\n>>> 40/20 SL2.5:")
stats(donchian(d1, 40, 20, 2.5, mode='long'), "LONG only")
stats(donchian(d1, 40, 20, 2.5, mode='short'), "SHORT only")
stats(donchian(d1, 40, 20, 2.5, mode='both'), "BOTH")

print("\n>>> 20/10 SL2.0:")
stats(donchian(d1, 20, 10, 2.0, mode='long'), "LONG only")
stats(donchian(d1, 20, 10, 2.0, mode='short'), "SHORT only")
stats(donchian(d1, 20, 10, 2.0, mode='both'), "BOTH")

print("\n>>> 70/30 SL2.5:")
stats(donchian(d1, 70, 30, 2.5, mode='long'), "LONG only")
stats(donchian(d1, 70, 30, 2.5, mode='short'), "SHORT only")
stats(donchian(d1, 70, 30, 2.5, mode='both'), "BOTH")

# Per any del SHORT-only millor i del BOTH millor
print("\n" + "="*140)
print("DETALL PER ANY:")
print("="*140)

for mode in ['short', 'both']:
    trades = donchian(d1, 55, 20, 2.5, mode=mode)
    if trades:
        print(f"\n>>> 55/20 {mode.upper()}:")
        tdf = pd.DataFrame(trades)
        tdf['year'] = pd.to_datetime(tdf['ts']).dt.year
        for yr in sorted(tdf['year'].unique()):
            ydf = tdf[tdf['year']==yr]
            arr = ydf['pnl'].values
            n=len(arr); w=(arr>0).sum(); net=arr.sum()
            n_l = (ydf['side']=='L').sum(); n_s = (ydf['side']=='S').sum()
            pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
            pf=pf_p/pf_l if pf_l else 0
            print(f"  {yr}: n={n} (L{n_l}/S{n_s}) | WR {w/n*100 if n else 0:.1f}% | Net ${net:+.2f} | PF {pf:.2f}")

# Show all SHORT trades to understand why they fail/succeed
print("\n" + "="*140)
print("ALL SHORT trades 55/20 (per veure si val la pena):")
trades_s = donchian(d1, 55, 20, 2.5, mode='short')
for t in trades_s:
    print(f"  {t['ts'].strftime('%Y-%m-%d')} -> {t['exit_ts'].strftime('%Y-%m-%d')} | dur {t['duration_days']:>3}d | "
          f"raw ${t['pnl_raw']:+7.2f} | swap ${t['swap']:+6.2f} | net ${t['pnl']:+7.2f} | {t['reason']}")
