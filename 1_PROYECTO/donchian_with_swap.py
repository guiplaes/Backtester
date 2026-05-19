"""Donchian D1 amb costos REALS de swap/rollover incorporats.

VT Markets XAUUSD swap rates (approx 2025):
- LONG: -$8 to -$15 per standard lot per night
- SHORT: usually less negative or slightly positive
- Wednesday: 3x swap (covers weekend)

We use conservative estimate: -$10/lot/night LONG, with Wed 3x.
For 0.01 lot (1 unit), that's -$0.10/night.
"""
import pandas as pd
import numpy as np

CSV = "xauusd_m5_5y.csv"
COMMISSION = 0.5; SPREAD = 0.50; SLIPPAGE = 0.20

# SWAP ASSUMPTIONS (per 1 unit = 0.01 lot)
SWAP_LONG_PER_NIGHT = -0.10  # USD per night for 0.01 lot LONG XAUUSD
SWAP_LONG_WED_MULT = 3.0     # Wednesday triple swap

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

def calc_swap(entry_ts, exit_ts):
    """Calculate total swap cost for a LONG position open between entry_ts and exit_ts."""
    entry_d = pd.Timestamp(entry_ts).normalize()
    exit_d = pd.Timestamp(exit_ts).normalize()
    if exit_d <= entry_d:
        return 0
    total_swap = 0
    cur = entry_d + pd.Timedelta(days=1)  # first night charge after entry
    while cur <= exit_d:
        # Skip weekends (no swap charge if market closed Sat/Sun?)
        # XAUUSD trades Mon-Fri. Wed has triple swap to cover weekend.
        # Let's assume: charges Mon, Tue, Wed (3x), Thu, Fri. No charge Sat, Sun.
        wd = cur.weekday()  # 0=Mon, 6=Sun
        if wd == 5 or wd == 6:  # Sat, Sun
            cur += pd.Timedelta(days=1)
            continue
        if wd == 2:  # Wednesday triple
            total_swap += SWAP_LONG_PER_NIGHT * SWAP_LONG_WED_MULT
        else:
            total_swap += SWAP_LONG_PER_NIGHT
        cur += pd.Timedelta(days=1)
    return total_swap

def donchian(df_, breakout=55, exit_periods=20, sl_atr=2.5, long_only=True):
    df_ = df_.copy()
    df_['don_high'] = df_['high'].rolling(breakout).max().shift(1)
    df_['don_low'] = df_['low'].rolling(breakout).min().shift(1)
    df_['exit_low'] = df_['low'].rolling(exit_periods).min().shift(1)
    trades = []; pos = None
    for i in range(breakout+5, len(df_)):
        bar = df_.iloc[i]; ts = df_.index[i]
        if pos is not None:
            sl_h = bar['low'] <= pos['sl']
            if sl_h:
                price = pos['sl']
                pnl_raw = (price - pos['e']) - SLIPPAGE - COMMISSION*2 - SPREAD
                swap = calc_swap(pos['ts'], ts)
                pnl = pnl_raw + swap
                duration_days = (pd.Timestamp(ts) - pd.Timestamp(pos['ts'])).days
                trades.append({'ts': pos['ts'], 'exit_ts': ts, 'pnl_raw': pnl_raw, 'swap': swap, 'pnl': pnl,
                              'duration_days': duration_days, 'side':'L', 'exit_reason':'SL'})
                pos = None
            elif bar['close'] < bar['exit_low']:
                pnl_raw = (bar['close'] - pos['e']) - SLIPPAGE - COMMISSION*2 - SPREAD
                swap = calc_swap(pos['ts'], ts)
                pnl = pnl_raw + swap
                duration_days = (pd.Timestamp(ts) - pd.Timestamp(pos['ts'])).days
                trades.append({'ts': pos['ts'], 'exit_ts': ts, 'pnl_raw': pnl_raw, 'swap': swap, 'pnl': pnl,
                              'duration_days': duration_days, 'side':'L', 'exit_reason':'Don_exit'})
                pos = None
        if pos is None and not pd.isna(bar['don_high']):
            atr = bar['atr']
            if pd.isna(atr): continue
            if bar['close'] > bar['don_high']:
                pos = {'side':'L', 'e':bar['close'], 'ts':ts, 'sl':bar['close']-atr*sl_atr}
    return trades

def stats(trades, name):
    if not trades: print(f"{name:>40}: NO trades"); return
    arr_raw = np.array([t['pnl_raw'] for t in trades])
    arr = np.array([t['pnl'] for t in trades])
    swaps = np.array([t['swap'] for t in trades])
    durations = np.array([t['duration_days'] for t in trades])
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    print(f"{name:>40}: n={n:>3} | WR {w/n*100:>5.1f}% | Avg dur {durations.mean():.1f}d | "
          f"Raw ${arr_raw.sum():+.2f} | Swap ${swaps.sum():+.2f} | Net ${net:+.2f} | PF {pf:.2f} | DD ${dd:.2f}", flush=True)

print()
print("="*130)
print("DONCHIAN D1 amb SWAP REAL incorporat:")
print(f"Assumed swap: ${SWAP_LONG_PER_NIGHT}/night × 0.01 lot LONG, Wed 3×")
print("="*130)

for bo, exit_p, sl, label in [
    (55, 20, 2.5, "55/20 SL2.5 (winner anterior)"),
    (55, 30, 2.5, "55/30 SL2.5"),
    (40, 20, 2.5, "40/20 SL2.5"),
    (70, 30, 2.5, "70/30 SL2.5"),
    (20, 10, 2.0, "20/10 SL2.0 (faster, less swap)"),
    (10, 5,  1.5, "10/5 SL1.5 (very fast)"),
]:
    trades = donchian(d1, breakout=bo, exit_periods=exit_p, sl_atr=sl)
    stats(trades, label)

# Worst case scenario: -$15/night swap
print()
print("="*130)
print(f"DONCHIAN 55/20 amb SWAP MÉS CAR (worst case -$0.15/nit per 0.01 lot):")
print("="*130)
SWAP_LONG_PER_NIGHT_WORST = -0.15

# Re-run with global swap
def donchian_swap(df_, breakout=55, exit_periods=20, sl_atr=2.5, swap_rate=-0.15):
    """Same as donchian but with custom swap rate."""
    global SWAP_LONG_PER_NIGHT
    saved = SWAP_LONG_PER_NIGHT
    SWAP_LONG_PER_NIGHT = swap_rate
    trades = donchian(df_, breakout, exit_periods, sl_atr)
    SWAP_LONG_PER_NIGHT = saved
    return trades

for swap in [-0.05, -0.10, -0.15, -0.20, -0.30]:
    trades = donchian_swap(d1, swap_rate=swap)
    label = f"55/20 SL2.5 swap ${swap}/night"
    stats(trades, label)

# Per year of best
print()
print("="*130)
print("PER YEAR — Donchian 55/20 amb swap -$0.10/night:")
print("="*130)
trades = donchian(d1, breakout=55, exit_periods=20, sl_atr=2.5)
tdf = pd.DataFrame(trades)
if len(tdf):
    tdf['year'] = pd.to_datetime(tdf['ts']).dt.year
    for yr in sorted(tdf['year'].unique()):
        ydf = tdf[tdf['year']==yr]
        arr = ydf['pnl'].values
        n=len(arr); w=(arr>0).sum(); net=arr.sum()
        avg_dur = ydf['duration_days'].mean()
        swap_total = ydf['swap'].sum()
        pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
        pf=pf_p/pf_l if pf_l else 0
        print(f"  {yr}: n={n:>2} | WR {w/n*100 if n else 0:>5.1f}% | dur {avg_dur:.0f}d | swap ${swap_total:.2f} | Net ${net:+.2f} | PF {pf:.2f}", flush=True)
    print(f"\nTOTAL net (with swap): ${tdf['pnl'].sum():.2f}", flush=True)
    print(f"TOTAL swap cost: ${tdf['swap'].sum():.2f}", flush=True)

# Show individual trades
print()
print("ALL TRADES:")
for t in trades:
    print(f"  {t['ts'].strftime('%Y-%m-%d')} -> {t['exit_ts'].strftime('%Y-%m-%d')} | "
          f"dur {t['duration_days']:>3}d | raw ${t['pnl_raw']:+7.2f} | swap ${t['swap']:+6.2f} | "
          f"net ${t['pnl']:+7.2f} | {t['exit_reason']}")
