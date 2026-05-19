"""M5 optimització complerta amb mateix pipeline."""
import pandas as pd
import numpy as np
import time

CSV = "xauusd_m5_5y.csv"
REAL_COST = 0.40

m5 = pd.read_csv(CSV, index_col=0, parse_dates=True)
m5.index = pd.to_datetime(m5.index, utc=True)
m5.columns = [c.lower() for c in m5.columns]
if 'tick_volume' not in m5.columns: m5['tick_volume'] = m5.get('volume', 1)

print(f"M5 RAW: {len(m5)} bars", flush=True)

# Use same M5 data, just add indicators
m5['ema50'] = m5['close'].ewm(span=50, adjust=False).mean()
hl = m5['high']-m5['low']; hc = (m5['high']-m5['close'].shift()).abs(); lc = (m5['low']-m5['close'].shift()).abs()
tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
m5['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
print("Indicators ready", flush=True)

# Donchian D1
d1 = m5.resample('1D').agg(open=('open','first'),high=('high','max'),
    low=('low','min'),close=('close','last')).dropna()
d1['don_high_55'] = d1['high'].rolling(55).max().shift(1)
d1['don_low_20'] = d1['low'].rolling(20).min().shift(1)
d1_in_long = pd.Series(False, index=d1.index)
in_pos = False
for i in range(56, len(d1)):
    if not in_pos:
        if d1.iloc[i]['close'] > d1.iloc[i]['don_high_55']: in_pos = True
    else:
        if d1.iloc[i]['close'] < d1.iloc[i]['don_low_20']: in_pos = False
    d1_in_long.iloc[i] = in_pos
DON_LOOKUP = {pd.Timestamp(d).strftime('%Y-%m-%d'): v for d, v in d1_in_long.items()}

# Pre-compute arrays
print("Precomputing arrays...", flush=True)
DATE_STRS = m5.index.strftime('%Y-%m-%d').values
DON_FLAGS = np.array([DON_LOOKUP.get(d, False) for d in DATE_STRS])
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

# Sessions for each row precomputed
SESSION_ARR = np.array([get_session(h) for h in HOURS])

def bt_session(sess_target, sl_atr, tp1_atr, tp2_atr, ob_str, donchian=False, skip_thu=False):
    """Backtest only one session at a time."""
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
            if skip_thu and WKD[i] == 3: continue
            if donchian and not DON_FLAGS[i]: continue
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

def stats(arr):
    if len(arr)==0: return None
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    return {'n':n,'wr':w/n*100,'net':net,'pf':pf,'dd':dd}

# Grid search per session
print()
print("M5 GRID SEARCH per session...", flush=True)
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

OPT = {}
for sess in ['ASIA', 'LONDON', 'NY']:
    print(f"\n>>> {sess}:", flush=True)
    best = None; best_pf = 0
    for sl, tp1, tp2, ob in GRID:
        t0 = time.time()
        trades = bt_session(sess, sl, tp1, tp2, ob)
        elapsed = time.time() - t0
        s = stats(np.array(trades))
        if s and s['n'] >= 50:
            label = f"SL{sl} TP{tp1}/{tp2} OB{ob}"
            print(f"  {label}: n={s['n']:>4} Net=${s['net']:.0f} PF={s['pf']:.2f} ({elapsed:.1f}s)", flush=True)
            if s['pf'] > best_pf and s['net'] > 50:
                best_pf = s['pf']; best = (sl, tp1, tp2, ob)
    if best:
        print(f"  >>> BEST {sess}: {best} PF {best_pf:.2f}")
        OPT[sess] = best

print()
print(f"M5 OPT params: {OPT}")

# Now run combined with all sessions (sum trades)
def bt_full(params, donchian=False, skip_thu=False):
    """Run all sessions with their respective params."""
    all_trades = []
    for sess in ['ASIA', 'LONDON', 'NY']:
        if sess not in params: continue
        sl, tp1, tp2, ob = params[sess]
        trades = bt_session(sess, sl, tp1, tp2, ob, donchian=donchian, skip_thu=skip_thu)
        all_trades.extend([{'pnl': p} for p in trades])
    # Sort by entry timestamp would be ideal, but for stats just concatenate
    return all_trades

print()
print("="*120)
print("M5 amb config òptima — variants:")
print("="*120)

trades = bt_full(OPT)
arr = np.array([t['pnl'] for t in trades])
s = stats(arr)
if s: print(f"BASE per-session:                      n={s['n']:>4} WR {s['wr']:.1f}% Net=${s['net']:.0f} PF={s['pf']:.2f} DD=${s['dd']:.0f}", flush=True)

trades_d = bt_full(OPT, donchian=True)
arr_d = np.array([t['pnl'] for t in trades_d])
s_d = stats(arr_d)
if s_d: print(f"+ Donchian D1:                         n={s_d['n']:>4} WR {s_d['wr']:.1f}% Net=${s_d['net']:.0f} PF={s_d['pf']:.2f} DD=${s_d['dd']:.0f}", flush=True)

trades_t = bt_full(OPT, skip_thu=True)
arr_t = np.array([t['pnl'] for t in trades_t])
s_t = stats(arr_t)
if s_t: print(f"+ Skip Thursday:                       n={s_t['n']:>4} WR {s_t['wr']:.1f}% Net=${s_t['net']:.0f} PF={s_t['pf']:.2f} DD=${s_t['dd']:.0f}", flush=True)

# Apply streak
def apply_streak(arr):
    out = []; size = 1.0; consec_l = 0; consec_w = 0
    for pnl in arr:
        out.append(pnl*size)
        if pnl > 0:
            consec_w += 1; consec_l = 0
            if consec_w >= 3 and size < 2.0: size = min(2.0, size*1.3)
            if consec_w == 1 and size < 1.0: size = 1.0
        else:
            consec_l += 1; consec_w = 0
            if consec_l >= 2: size = max(0.5, size*0.7)
    return np.array(out)

print()
print("Amb Streak sizing:")
for label, a in [('Base', arr), ('+Donchian', arr_d), ('+SkipThu', arr_t)]:
    sized = apply_streak(a)
    s = stats(sized)
    if s:
        net_005 = s['net'] * 5
        dd_005 = s['dd'] * 5
        print(f"  {label}+Streak: Net5y@0.05=${net_005:+.0f} ({net_005/50000*100:+.1f}%/any) PF={s['pf']:.2f} DD@0.05=${dd_005:.0f} ({dd_005/10000*100:.1f}%)", flush=True)

print()
print("="*120)
print("FINAL COMPARATIVA (escalat 0.05 lot, $10k):")
print("="*120)
print(f"M5 Base+Streak:    Net=${apply_streak(arr).sum()*5:+.0f} ({apply_streak(arr).sum()*5/50000*100:+.1f}%/any)")
print(f"M15 Base+Streak:   Net=$+23,427 (+46.9%/any) PF 2.29")
print(f"H4 ULTIMATE:       Net=$+21,472 (+43%/any) PF 4.92")
