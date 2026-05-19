"""M15 amb config trobada + Donchian filter."""
import pandas as pd
import numpy as np

CSV = "xauusd_m5_5y.csv"
REAL_COST = 0.40

m5 = pd.read_csv(CSV, index_col=0, parse_dates=True)
m5.index = pd.to_datetime(m5.index, utc=True)
m5.columns = [c.lower() for c in m5.columns]
if 'tick_volume' not in m5.columns: m5['tick_volume'] = m5.get('volume', 1)

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

# Convert to dict for fast lookup using date string
DON_LOOKUP = {pd.Timestamp(d).strftime('%Y-%m-%d'): v for d, v in d1_in_long.items()}

m15 = m5.resample('15min').agg(open=('open','first'),high=('high','max'),
    low=('low','min'),close=('close','last'),tick_volume=('tick_volume','sum')).dropna()
m15['ema50'] = m15['close'].ewm(span=50, adjust=False).mean()
hl = m15['high']-m15['low']; hc = (m15['high']-m15['close'].shift()).abs(); lc = (m15['low']-m15['close'].shift()).abs()
tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
m15['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
print(f"M15: {len(m15)} bars", flush=True)

# Pre-compute donchian per row using date string
DATE_STRS = m15.index.strftime('%Y-%m-%d').values
DON_FLAGS = np.array([DON_LOOKUP.get(d, False) for d in DATE_STRS])

O = m15['open'].values; H = m15['high'].values; L = m15['low'].values; C = m15['close'].values
EMA = m15['ema50'].values; ATR = m15['atr'].values
HOURS = m15.index.hour.values
WKD = m15.index.weekday.values
n = len(C)

def get_session(h):
    if h < 7: return 'ASIA'
    elif h < 13: return 'LONDON'
    elif h < 21: return 'NY' if h >= 16 else None  # skip OVERLAP and only NY 16-21
    else: return None

# M15 params trobats
M15_PARAMS = {
    'ASIA': (1.5, 2, 4, 2.5),
    'LONDON': (1.0, 2, 4, 2.5),
    'NY': (1.5, 2, 4, 2.5),
}

def get_session_full(h):
    if h < 7: return 'ASIA'
    elif h < 13: return 'LONDON'
    elif h < 16: return 'OVERLAP'
    elif h < 21: return 'NY'
    else: return 'DEAD'

def bt(donchian_filter=False, skip_thursday=False):
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
        sess = get_session_full(HOURS[i])
        params = M15_PARAMS.get(sess)
        if params is None:
            keep = [j for j, exp in enumerate(pending_expiry) if exp > i]
            if len(keep) < len(pending_expiry):
                pending_lows = [pending_lows[j] for j in keep]
                pending_highs = [pending_highs[j] for j in keep]
                pending_atrs = [pending_atrs[j] for j in keep]
                pending_expiry = [pending_expiry[j] for j in keep]
            continue
        sl_atr, tp1_atr, tp2_atr, ob_str = params
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
            if skip_thursday and WKD[i] == 3: continue
            if donchian_filter and not DON_FLAGS[i]: continue
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

def stats(arr, name):
    if len(arr)==0: print(f"{name}: 0"); return
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    # Scale to 0.05 lot
    net5 = net*5; dd5 = dd*5
    print(f"{name:>40}: n={n:>4} | WR {w/n*100:>5.1f}% | Net5y@0.05={net5:+.0f} ({net5/10000/5*100:+.1f}%/any) | PF {pf:.2f} | DD@0.05=${dd5:.0f} ({dd5/10000*100:.1f}%)", flush=True)

def apply_streak(trades_pnl):
    out = []; size = 1.0; consec_l = 0; consec_w = 0
    for pnl in trades_pnl:
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
print("="*120)
print("M15 amb config òptima — variants:")
print("="*120)

print("\n1. Base (no extra filters):")
trades = bt()
arr = np.array(trades); sized = apply_streak(trades)
stats(arr, "Fixed")
stats(sized, "+ Streak sizing")

print("\n2. + Donchian D1 filter:")
trades_d = bt(donchian_filter=True)
arr_d = np.array(trades_d); sized_d = apply_streak(trades_d)
stats(arr_d, "+ Donchian (Fixed)")
stats(sized_d, "+ Donchian + Streak")

print("\n3. + Skip Thursday:")
trades_t = bt(skip_thursday=True)
arr_t = np.array(trades_t); sized_t = apply_streak(trades_t)
stats(arr_t, "+ Skip Thu (Fixed)")
stats(sized_t, "+ Skip Thu + Streak")

print("\n4. + Donchian + Skip Thursday:")
trades_dt = bt(donchian_filter=True, skip_thursday=True)
arr_dt = np.array(trades_dt); sized_dt = apply_streak(trades_dt)
stats(arr_dt, "+ Don + SkipThu (Fixed)")
stats(sized_dt, "+ Don + SkipThu + Streak (ULTIMATE)")

# Best comparison
print()
print("="*120)
print("RESUM FINAL M15 vs H4:")
print("="*120)
print("H4 ULTIMATE:    Net5y@0.05=+$21,472 (+43%/any) PF 4.92 DD 5.6%")
print(f"M15 BASE:       Net5y@0.05={np.array(trades).sum()*5:+.0f} ({np.array(trades).sum()*5/50000*100:+.1f}%/any)")

# Per any of best M15
best_label = "Donchian+Streak" if sum(sized_d) > sum(sized_dt) else "Don+Thu+Streak"
best_trades_pnl = trades_d if sum(sized_d) > sum(sized_dt) else trades_dt
best_sized = sized_d if sum(sized_d) > sum(sized_dt) else sized_dt

# Need to track timestamps for per-year breakdown
# Re-run with timestamps
def bt_with_ts(donchian_filter=True):
    trades = []
    pos = None
    pending_lows = []; pending_highs = []; pending_atrs = []; pending_expiry = []
    for i in range(50, n-3):
        if pos is not None:
            sl_h = L[i] <= pos[0]
            tp1_h = H[i] >= pos[1]
            tp2_h = H[i] >= pos[2]
            if sl_h and (tp1_h or tp2_h):
                if (O[i]-pos[0]) < (pos[1]-O[i]): tp1_h=False; tp2_h=False
            pnl1 = pos[3]; pnl2 = pos[4]; q1 = pos[5]; q2 = pos[6]; e = pos[7]; ts = pos[8]
            if tp1_h and q1>0: pnl1 = (pos[1]-e)*0.5; q1=0
            if tp2_h and q2>0: pnl2 = (pos[2]-e)*0.5; q2=0
            if sl_h:
                if q1>0: pnl1 = (pos[0]-e)*0.5; q1=0
                if q2>0: pnl2 = (pos[0]-e)*0.5; q2=0
            if q1==0 and q2==0:
                trades.append({'ts': ts, 'pnl': pnl1+pnl2 - REAL_COST})
                pos = None
            else:
                pos = (pos[0], pos[1], pos[2], pnl1, pnl2, q1, q2, e, ts)
        b0_close = C[i-1]; b0_open = O[i-1]; b0_atr = ATR[i-1]
        sess = get_session_full(HOURS[i])
        params = M15_PARAMS.get(sess)
        if params is None:
            keep = [j for j, exp in enumerate(pending_expiry) if exp > i]
            if len(keep) < len(pending_expiry):
                pending_lows = [pending_lows[j] for j in keep]
                pending_highs = [pending_highs[j] for j in keep]
                pending_atrs = [pending_atrs[j] for j in keep]
                pending_expiry = [pending_expiry[j] for j in keep]
            continue
        sl_atr, tp1_atr, tp2_atr, ob_str = params
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
            if donchian_filter and not DON_FLAGS[i]: continue
            for j in range(len(pending_lows)):
                if L[i] <= pending_highs[j] and C[i] > pending_lows[j]:
                    if C[i] > O[i]:
                        atr = pending_atrs[j]; e = C[i]
                        pos = (pending_lows[j]-atr*sl_atr*0.5,
                               e+atr*tp1_atr,
                               e+atr*tp2_atr,
                               0, 0, 0.5, 0.5, e, m15.index[i])
                        pending_lows.pop(j); pending_highs.pop(j); pending_atrs.pop(j); pending_expiry.pop(j)
                        break
    return trades

print()
print("Per any (M15 + Donchian + Streak):")
trades_yr = bt_with_ts(donchian_filter=True)
sized_yr = apply_streak([t['pnl'] for t in trades_yr])
tdf = pd.DataFrame([{'ts': t['ts'], 'pnl_sized': p, 'year': pd.Timestamp(t['ts']).year}
                     for t, p in zip(trades_yr, sized_yr)])
for yr in sorted(tdf['year'].unique()):
    ydf = tdf[tdf['year']==yr]
    arr = ydf['pnl_sized'].values
    n_t=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    print(f"  {yr}: n={n_t:>3} | WR {w/n_t*100 if n_t else 0:>5.1f}% | Net (0.05 lot)=${net*5:+.0f} | PF {pf:.2f}", flush=True)
