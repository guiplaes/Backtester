"""
Optimització SIMPLE M15 i M30:
- Provar diferents SL/TP per global (no per-session, més robust)
- Aplicar filtres validats
- Streak sizing
- Comparar amb H4
"""
import pandas as pd
import numpy as np
import sys

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
DON_LOOKUP = d1_in_long.to_dict()

def is_donchian_long(ts):
    d = pd.Timestamp(ts).normalize()
    return DON_LOOKUP.get(d, False)

def prep_tf(rule):
    df_ = m5.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last'),tick_volume=('tick_volume','sum')).dropna()
    df_['ema50'] = df_['close'].ewm(span=50, adjust=False).mean()
    hl = df_['high']-df_['low']; hc = (df_['high']-df_['close'].shift()).abs(); lc = (df_['low']-df_['close'].shift()).abs()
    tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
    df_['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
    return df_

def get_session(h):
    if h < 7: return 'ASIA'
    elif h < 13: return 'LONDON'
    elif h < 16: return 'OVERLAP'
    elif h < 21: return 'NY'
    else: return 'DEAD'

def s2_ob_global(df_, sl_atr, tp1_atr, tp2_atr, ob_str,
                  donchian_filter=False, skip_thursday=False, allowed_sessions=None):
    """Global params (not per-session). Faster."""
    O = df_['open'].values; H = df_['high'].values; L = df_['low'].values; C = df_['close'].values
    EMA = df_['ema50'].values; ATR = df_['atr'].values
    INDEX = df_.index
    HOURS = INDEX.hour.values
    WEEKDAYS = INDEX.weekday.values
    n = len(C)
    trades = []; pos = None; pending_obs = []
    for i in range(50, n-3):
        if pos is not None:
            sl_h = L[i] <= pos['sl']
            tp1_h = H[i] >= pos['tp1']
            tp2_h = H[i] >= pos['tp2']
            if sl_h and (tp1_h or tp2_h):
                if (O[i]-pos['sl']) < (pos['tp1']-O[i]): tp1_h=False; tp2_h=False
            if tp1_h and pos['q1']>0: pos['pnl1'] = (pos['tp1']-pos['e'])*0.5; pos['q1']=0
            if tp2_h and pos['q2']>0: pos['pnl2'] = (pos['tp2']-pos['e'])*0.5; pos['q2']=0
            if sl_h:
                if pos['q1']>0: pos['pnl1'] = (pos['sl']-pos['e'])*0.5; pos['q1']=0
                if pos['q2']>0: pos['pnl2'] = (pos['sl']-pos['e'])*0.5; pos['q2']=0
            if pos['q1']==0 and pos['q2']==0:
                tp = pos.get('pnl1',0)+pos.get('pnl2',0) - REAL_COST
                trades.append({'ts': pos['ts'], 'pnl': tp})
                pos = None
        b0_close = C[i-1]; b0_open = O[i-1]; b0_atr = ATR[i-1]
        if b0_close < b0_open and not np.isnan(b0_atr):
            future_high = max(H[i], H[i+1], H[i+2])
            move = future_high - b0_close
            if move > ob_str * b0_atr:
                pending_obs.append({'ob_low':L[i-1],'ob_high':H[i-1],'expiry':i+30,'atr0':b0_atr})
        pending_obs = [o for o in pending_obs if o['expiry'] > i]
        if pos is None and not np.isnan(EMA[i]) and C[i] > EMA[i]:
            sess = get_session(HOURS[i])
            if allowed_sessions and sess not in allowed_sessions: continue
            if skip_thursday and WEEKDAYS[i] == 3: continue
            if donchian_filter and not is_donchian_long(INDEX[i]): continue
            for ob in list(pending_obs):
                if L[i] <= ob['ob_high'] and C[i] > ob['ob_low']:
                    if C[i] > O[i]:
                        atr = ob['atr0']; e = C[i]
                        pos = {'side':'L','e':e,'ts':INDEX[i],
                            'sl':ob['ob_low']-atr*sl_atr*0.5,
                            'tp1':e+atr*tp1_atr,'tp2':e+atr*tp2_atr,'q1':0.5,'q2':0.5}
                        pending_obs.remove(ob); break
    return trades

def stats_arr(arr):
    if len(arr)==0: return None
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    return {'n':n,'wr':w/n*100,'net':net,'pf':pf,'dd':dd}

def apply_streak(trades, k=1.3, min_size=0.5, max_size=2.0):
    out = []; size = 1.0; consec_l = 0; consec_w = 0
    for t in trades:
        out.append(t['pnl']*size)
        if t['pnl'] > 0:
            consec_w += 1; consec_l = 0
            if consec_w >= 3 and size < max_size: size = min(max_size, size*k)
            if consec_w == 1 and size < 1.0: size = 1.0
        else:
            consec_l += 1; consec_w = 0
            if consec_l >= 2: size = max(min_size, size*0.7)
    return np.array(out)

print("Loading TFs...", flush=True)
m15 = prep_tf('15min')
m30 = prep_tf('30min')
print(f"M15: {len(m15)} | M30: {len(m30)}", flush=True)

print()
print("="*120)
print("M15 — Grid search SIMPLIFICAT (params globals):")
print("="*120)

GRID = [
    (1.0, 2, 4, 1.5),
    (1.0, 2, 4, 2.0),
    (1.0, 3, 6, 1.5),
    (1.0, 3, 6, 2.0),
    (1.0, 5, 10, 1.5),
    (1.0, 5, 10, 2.0),
    (1.5, 3, 6, 1.5),
    (1.5, 3, 6, 2.0),
    (1.5, 5, 10, 1.5),
    (1.5, 5, 10, 2.0),
    (2.0, 4, 8, 1.5),
    (2.0, 4, 8, 2.0),
]

best_m15 = None; best_m15_pf = 0
for sl, tp1, tp2, ob in GRID:
    trades = s2_ob_global(m15, sl, tp1, tp2, ob)
    s = stats_arr(np.array([t['pnl'] for t in trades]))
    if s and s['n'] >= 50:
        label = f"M15 SL{sl} TP{tp1}/{tp2} OB{ob}"
        print(f"{label:>40}: n={s['n']:>4} WR {s['wr']:>5.1f}% Net ${s['net']:+.0f} PF {s['pf']:.2f} DD ${s['dd']:.0f}", flush=True)
        if s['pf'] > best_m15_pf and s['net'] > 200:
            best_m15_pf = s['pf']; best_m15 = (sl, tp1, tp2, ob)

print(f"\nBest M15: {best_m15} PF {best_m15_pf:.2f}")

# Now apply filters to best M15
if best_m15:
    sl, tp1, tp2, ob = best_m15
    print(f"\n>>> M15 amb {best_m15} + APLICANT FILTRES:", flush=True)
    t_base = s2_ob_global(m15, sl, tp1, tp2, ob)
    s_base = stats_arr(np.array([t['pnl'] for t in t_base]))
    print(f"  Base: n={s_base['n']} Net=${s_base['net']:.0f} PF={s_base['pf']:.2f}", flush=True)

    t_thu = s2_ob_global(m15, sl, tp1, tp2, ob, skip_thursday=True)
    s_thu = stats_arr(np.array([t['pnl'] for t in t_thu]))
    keep_thu = s_thu and s_thu['pf'] > s_base['pf'] + 0.10
    print(f"  + Skip Thu: n={s_thu['n']} Net=${s_thu['net']:.0f} PF={s_thu['pf']:.2f} {'KEEP' if keep_thu else 'DISCARD'}", flush=True)

    t_don = s2_ob_global(m15, sl, tp1, tp2, ob, skip_thursday=keep_thu, donchian_filter=True)
    s_don = stats_arr(np.array([t['pnl'] for t in t_don]))
    prev = s_thu if keep_thu else s_base
    keep_don = s_don and s_don['pf'] > prev['pf'] + 0.10
    print(f"  + Donchian D1: n={s_don['n']} Net=${s_don['net']:.0f} PF={s_don['pf']:.2f} {'KEEP' if keep_don else 'DISCARD'}", flush=True)

    # FINAL with streak
    final = s2_ob_global(m15, sl, tp1, tp2, ob, skip_thursday=keep_thu, donchian_filter=keep_don)
    sized = apply_streak(final)
    s_streak = stats_arr(sized)
    print(f"\n  M15 FINAL + Streak: n={s_streak['n']} Net=${s_streak['net']:.0f} PF={s_streak['pf']:.2f} DD=${s_streak['dd']:.0f}", flush=True)

# M30
print()
print("="*120)
print("M30 — Grid search:")
print("="*120)

best_m30 = None; best_m30_pf = 0
for sl, tp1, tp2, ob in GRID:
    trades = s2_ob_global(m30, sl, tp1, tp2, ob)
    s = stats_arr(np.array([t['pnl'] for t in trades]))
    if s and s['n'] >= 50:
        label = f"M30 SL{sl} TP{tp1}/{tp2} OB{ob}"
        print(f"{label:>40}: n={s['n']:>4} WR {s['wr']:>5.1f}% Net ${s['net']:+.0f} PF {s['pf']:.2f} DD ${s['dd']:.0f}", flush=True)
        if s['pf'] > best_m30_pf and s['net'] > 200:
            best_m30_pf = s['pf']; best_m30 = (sl, tp1, tp2, ob)

print(f"\nBest M30: {best_m30} PF {best_m30_pf:.2f}")

if best_m30:
    sl, tp1, tp2, ob = best_m30
    print(f"\n>>> M30 amb {best_m30} + FILTRES:", flush=True)
    t_base = s2_ob_global(m30, sl, tp1, tp2, ob)
    s_base = stats_arr(np.array([t['pnl'] for t in t_base]))
    print(f"  Base: n={s_base['n']} Net=${s_base['net']:.0f} PF={s_base['pf']:.2f}", flush=True)

    t_thu = s2_ob_global(m30, sl, tp1, tp2, ob, skip_thursday=True)
    s_thu = stats_arr(np.array([t['pnl'] for t in t_thu]))
    keep_thu = s_thu and s_thu['pf'] > s_base['pf'] + 0.10
    print(f"  + Skip Thu: n={s_thu['n']} Net=${s_thu['net']:.0f} PF={s_thu['pf']:.2f} {'KEEP' if keep_thu else 'DISCARD'}", flush=True)

    t_don = s2_ob_global(m30, sl, tp1, tp2, ob, skip_thursday=keep_thu, donchian_filter=True)
    s_don = stats_arr(np.array([t['pnl'] for t in t_don]))
    prev = s_thu if keep_thu else s_base
    keep_don = s_don and s_don['pf'] > prev['pf'] + 0.10
    print(f"  + Donchian D1: n={s_don['n']} Net=${s_don['net']:.0f} PF={s_don['pf']:.2f} {'KEEP' if keep_don else 'DISCARD'}", flush=True)

    final = s2_ob_global(m30, sl, tp1, tp2, ob, skip_thursday=keep_thu, donchian_filter=keep_don)
    sized = apply_streak(final)
    s_streak = stats_arr(sized)
    print(f"\n  M30 FINAL + Streak: n={s_streak['n']} Net=${s_streak['net']:.0f} PF={s_streak['pf']:.2f} DD=${s_streak['dd']:.0f}", flush=True)

print()
print("="*120)
print("COMPARATIVA FINAL (escalat 0.05 lot, $10k):")
print("="*120)
print(f"H4 ULTIMATE (reference): Net 5y +$21,472 (+43%/any) PF 4.92 DD 5.6%")
