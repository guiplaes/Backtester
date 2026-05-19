"""SHORTS sobre M5 amb mateix pipeline d'optimització per session."""
import pandas as pd
import numpy as np
import time

CSV = "xauusd_m5_5y.csv"
REAL_COST = 0.40

m5 = pd.read_csv(CSV, index_col=0, parse_dates=True)
m5.index = pd.to_datetime(m5.index, utc=True)
m5.columns = [c.lower() for c in m5.columns]
if 'tick_volume' not in m5.columns: m5['tick_volume'] = m5.get('volume', 1)
m5['ema50'] = m5['close'].ewm(span=50, adjust=False).mean()
hl = m5['high']-m5['low']; hc = (m5['high']-m5['close'].shift()).abs(); lc = (m5['low']-m5['close'].shift()).abs()
tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
m5['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()

print(f"M5: {len(m5)} bars", flush=True)

O = m5['open'].values; H = m5['high'].values; L = m5['low'].values; C = m5['close'].values
EMA = m5['ema50'].values; ATR = m5['atr'].values
HOURS = m5.index.hour.values
WKD = m5.index.weekday.values
n = len(C)

def get_session(h):
    if h < 7: return 'ASIA'
    elif h < 13: return 'LONDON'
    elif h < 16: return 'OVERLAP'
    elif h < 21: return 'NY'
    else: return 'DEAD'

SESSION_ARR = np.array([get_session(h) for h in HOURS])

def bt_short_session(sess_target, sl_atr, tp1_atr, tp2_atr, ob_str):
    """SHORT-only backtest. Bearish OB = green candle before strong down move."""
    trades_pnl = []
    pos = None
    pending_lows = []; pending_highs = []; pending_atrs = []; pending_expiry = []
    for i in range(50, n-3):
        if pos is not None:
            # SHORT: SL up, TP down
            sl_h = H[i] >= pos[0]
            tp1_h = L[i] <= pos[1]
            tp2_h = L[i] <= pos[2]
            if sl_h and (tp1_h or tp2_h):
                if (pos[0]-O[i]) < (O[i]-pos[1]): tp1_h=False; tp2_h=False
            pnl1 = pos[3]; pnl2 = pos[4]; q1 = pos[5]; q2 = pos[6]; e = pos[7]
            if tp1_h and q1>0: pnl1 = (e-pos[1])*0.5; q1=0  # short profit = entry - tp
            if tp2_h and q2>0: pnl2 = (e-pos[2])*0.5; q2=0
            if sl_h:
                if q1>0: pnl1 = (e-pos[0])*0.5; q1=0
                if q2>0: pnl2 = (e-pos[0])*0.5; q2=0
            if q1==0 and q2==0:
                trades_pnl.append(pnl1+pnl2 - REAL_COST)
                pos = None
            else:
                pos = (pos[0], pos[1], pos[2], pnl1, pnl2, q1, q2, e)

        # Bearish OB: green candle before strong down move
        b0_close = C[i-1]; b0_open = O[i-1]; b0_atr = ATR[i-1]
        if b0_close > b0_open and not np.isnan(b0_atr):  # GREEN candle
            future_low = min(L[i], L[i+1], L[i+2])
            move_dn = b0_close - future_low
            if move_dn > ob_str * b0_atr:
                pending_lows.append(L[i-1])
                pending_highs.append(H[i-1])
                pending_atrs.append(b0_atr)
                pending_expiry.append(i+30)

        keep = [j for j, exp in enumerate(pending_expiry) if exp > i]
        if len(keep) < len(pending_expiry):
            pending_lows = [pending_lows[j] for j in keep]
            pending_highs = [pending_highs[j] for j in keep]
            pending_atrs = [pending_atrs[j] for j in keep]
            pending_expiry = [pending_expiry[j] for j in keep]

        # SHORT entry: in downtrend (close < EMA50), price retraced into bearish OB
        if pos is None and not np.isnan(EMA[i]) and C[i] < EMA[i]:
            if SESSION_ARR[i] != sess_target: continue
            for j in range(len(pending_lows)):
                # SHORT: price came UP into OB zone, then bearish reversal
                if H[i] >= pending_lows[j] and C[i] < pending_highs[j]:
                    if C[i] < O[i]:  # bearish candle
                        atr = pending_atrs[j]; e = C[i]
                        # SHORT: SL above OB high, TP below entry
                        pos = (pending_highs[j]+atr*sl_atr*0.5,  # SL up
                               e-atr*tp1_atr,                     # TP1 down
                               e-atr*tp2_atr,                     # TP2 down
                               0, 0, 0.5, 0.5, e)
                        pending_lows.pop(j); pending_highs.pop(j); pending_atrs.pop(j); pending_expiry.pop(j)
                        break
    return trades_pnl

def stats(arr):
    if len(arr)==0: return None
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    return {'n':n,'wr':w/n*100,'net':net,'pf':pf,'dd':dd}

# Grid search per session for SHORTS
print()
print("="*120)
print("M5 SHORTS — Grid search per session:")
print("="*120)

GRID = [
    (1.0, 2, 4, 1.5),
    (1.0, 2, 4, 2.0),
    (1.0, 3, 6, 2.0),
    (1.0, 3, 6, 2.5),
    (1.0, 5, 10, 2.5),
    (1.5, 3, 6, 2.0),
    (1.5, 3, 6, 2.5),
    (1.5, 5, 10, 2.5),
    (2.0, 4, 8, 2.5),
]

OPT_SHORT = {}
for sess in ['ASIA', 'LONDON', 'NY']:
    print(f"\n>>> SHORT {sess}:", flush=True)
    best = None; best_pf = 0; best_stats = None
    for sl, tp1, tp2, ob in GRID:
        t0 = time.time()
        trades = bt_short_session(sess, sl, tp1, tp2, ob)
        elapsed = time.time() - t0
        s = stats(np.array(trades))
        if s and s['n'] >= 50:
            label = f"SL{sl} TP{tp1}/{tp2} OB{ob}"
            print(f"  {label}: n={s['n']:>4} WR {s['wr']:>4.1f}% Net=${s['net']:.0f} PF={s['pf']:.2f} ({elapsed:.1f}s)", flush=True)
            if s['pf'] > best_pf:
                best_pf = s['pf']; best = (sl, tp1, tp2, ob); best_stats = s
    if best:
        print(f"  >>> BEST {sess}: {best} PF {best_pf:.2f} (n={best_stats['n']} Net=${best_stats['net']:.0f})", flush=True)
        OPT_SHORT[sess] = best
    else:
        print(f"  >>> NO config positive for SHORT {sess}", flush=True)

print()
print("="*120)
print("M5 SHORT-only RESUM:")
print("="*120)

# Combined SHORT
all_shorts = []
for sess in ['ASIA', 'LONDON', 'NY']:
    if sess in OPT_SHORT:
        sl, tp1, tp2, ob = OPT_SHORT[sess]
        trades = bt_short_session(sess, sl, tp1, tp2, ob)
        all_shorts.extend(trades)

if all_shorts:
    s = stats(np.array(all_shorts))
    if s:
        net5 = s['net']*5  # 0.05 lot
        dd5 = s['dd']*5
        print(f"M5 SHORT (combined per-session): n={s['n']} WR {s['wr']:.1f}% Net5y@0.05=${net5:+.0f} ({net5/50000*100:+.1f}%/any) PF={s['pf']:.2f} DD@0.05=${dd5:.0f}", flush=True)

# Comparison vs LONG
print()
print(f"M5 LONG (best earlier):  Net5y@0.05=$+25,052 (+50.1%/any) PF 1.69")

# BOTH (LONG + SHORT)
print()
print("="*120)
print("M5 BOTH (LONG + SHORT combined):")
print("="*120)

# Need to re-run LONGS at same params to combine
def bt_long_session(sess_target, sl_atr, tp1_atr, tp2_atr, ob_str):
    trades_pnl = []
    pos = None
    pending_lows = []; pending_highs = []; pending_atrs = []; pending_expiry = []
    for i in range(50, n-3):
        if pos is not None:
            sl_h = L[i] <= pos[0]
            tp1_h = H[i] >= pos[1]
            tp2_h = H[i] >= pos[2]
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
        keep = [j for j, exp in enumerate(pending_expiry) if exp > i]
        if len(keep) < len(pending_expiry):
            pending_lows = [pending_lows[j] for j in keep]
            pending_highs = [pending_highs[j] for j in keep]
            pending_atrs = [pending_atrs[j] for j in keep]
            pending_expiry = [pending_expiry[j] for j in keep]
        if pos is None and not np.isnan(EMA[i]) and C[i] > EMA[i]:
            if SESSION_ARR[i] != sess_target: continue
            for j in range(len(pending_lows)):
                if L[i] <= pending_highs[j] and C[i] > pending_lows[j]:
                    if C[i] > O[i]:
                        atr = pending_atrs[j]; e = C[i]
                        pos = (pending_lows[j]-atr*sl_atr*0.5,
                               e+atr*tp1_atr,
                               e+atr*tp2_atr,
                               0, 0, 0.5, 0.5, e)
                        pending_lows.pop(j); pending_highs.pop(j); pending_atrs.pop(j); pending_expiry.pop(j)
                        break
    return trades_pnl

# Use M5 OPT params from earlier
OPT_LONG = {'ASIA': (1.0, 3, 6, 2.5), 'LONDON': (1.0, 3, 6, 2.5), 'NY': (1.0, 5, 10, 2.5)}

all_longs = []
for sess in ['ASIA', 'LONDON', 'NY']:
    sl, tp1, tp2, ob = OPT_LONG[sess]
    trades = bt_long_session(sess, sl, tp1, tp2, ob)
    all_longs.extend(trades)

# BOTH = combined longs + shorts
both = all_longs + all_shorts
if both:
    s = stats(np.array(both))
    net5 = s['net']*5; dd5 = s['dd']*5
    print(f"M5 BOTH (LONGS + SHORTS): n={s['n']} WR {s['wr']:.1f}% Net5y@0.05=${net5:+.0f} ({net5/50000*100:+.1f}%/any) PF={s['pf']:.2f} DD@0.05=${dd5:.0f}", flush=True)

# Summary
print()
print("="*120)
print("RESUM TOTAL M5 (5 anys, 0.05 lot, $10k):")
print("="*120)
s_long = stats(np.array(all_longs)) if all_longs else None
s_short = stats(np.array(all_shorts)) if all_shorts else None
s_both = stats(np.array(both)) if both else None

if s_long:
    print(f"LONGS only:  n={s_long['n']:>4} | WR {s_long['wr']:.1f}% | Net5y@0.05=${s_long['net']*5:+.0f} ({s_long['net']*5/50000*100:+.1f}%/any) | PF {s_long['pf']:.2f}")
if s_short:
    print(f"SHORTS only: n={s_short['n']:>4} | WR {s_short['wr']:.1f}% | Net5y@0.05=${s_short['net']*5:+.0f} ({s_short['net']*5/50000*100:+.1f}%/any) | PF {s_short['pf']:.2f}")
if s_both:
    print(f"BOTH:        n={s_both['n']:>4} | WR {s_both['wr']:.1f}% | Net5y@0.05=${s_both['net']*5:+.0f} ({s_both['net']*5/50000*100:+.1f}%/any) | PF {s_both['pf']:.2f}")
