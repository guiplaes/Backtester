"""
OPTIMITZACIO COMPLETA SENSE LOOKAHEAD
======================================
Per cada TF, provem variants:
- Sessions (asia/london/ny/overlap)
- Hours (top per WR)
- ATR regime (low/mid/high)
- EMA slope (lateral skip)
- Walk-forward (in-sample 70% / out-sample 30%)

Tots els tests sobre la millor config de cada TF (del backtest base).
"""
import pandas as pd
import numpy as np
import time
import collections

CSV = "xauusd_m5_5y.csv"
REAL_COST = 0.40

print("Loading data...", flush=True)
m5 = pd.read_csv(CSV, index_col=0, parse_dates=True)
m5.index = pd.to_datetime(m5.index, utc=True)
m5.columns = [c.lower() for c in m5.columns]
print(f"M5: {len(m5)} bars", flush=True)

def aggregate(df_, rule):
    return df_.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last')).dropna()

def add_ind(df_):
    df_ = df_.copy()
    df_['ema50'] = df_['close'].ewm(span=50, adjust=False).mean()
    hl = df_['high']-df_['low']
    hc = (df_['high']-df_['close'].shift()).abs()
    lc = (df_['low']-df_['close'].shift()).abs()
    tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
    df_['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
    # ATR percentile (rolling 200 bars) per regime
    df_['atr_pct'] = df_['atr'].rolling(200).rank(pct=True)
    # EMA slope (vs 10 bars ago, normalized by ATR)
    df_['ema_slope'] = (df_['ema50'] - df_['ema50'].shift(10)) / df_['atr']
    return df_

print("Aggregating timeframes...", flush=True)
TFS = {
    'M5':  add_ind(m5),
    'M15': add_ind(aggregate(m5, '15min')),
    'M30': add_ind(aggregate(m5, '30min')),
    'H1':  add_ind(aggregate(m5, '1h')),
    'H4':  add_ind(aggregate(m5, '4h')),
}
for k, v in TFS.items():
    print(f"  {k}: {len(v)} bars", flush=True)

def get_session(h):
    if h < 7: return 'ASIA'
    elif h < 13: return 'LONDON'
    elif h < 16: return 'OVERLAP'
    elif h < 21: return 'NY'
    else: return 'DEAD'

def precompute(df_):
    return {
        'O': df_['open'].values, 'H': df_['high'].values,
        'L': df_['low'].values, 'C': df_['close'].values,
        'EMA': df_['ema50'].values, 'ATR': df_['atr'].values,
        'ATR_PCT': df_['atr_pct'].values,
        'EMA_SLOPE': df_['ema_slope'].values,
        'HOUR': np.array([t.hour for t in df_.index]),
        'SESSION': np.array([get_session(t.hour) for t in df_.index]),
        'DOW': np.array([t.dayofweek for t in df_.index]),
        'TIMESTAMPS': df_.index,
        'n': len(df_)
    }

print("Precomputing...", flush=True)
TF_ARRS = {tf: precompute(df) for tf, df in TFS.items()}

def bt_filtered(arrs, direction, sl_atr, tp1_atr, tp2_atr, ob_str, entry_mask=None, idx_range=None):
    """
    NO LOOKAHEAD — mother bar at i-3.
    entry_mask: opcional array bool (n,) — només permet entrades on True.
    idx_range: (start, end) — limita el rang de barres processades.
    """
    O = arrs['O']; H = arrs['H']; L = arrs['L']; C = arrs['C']
    EMA = arrs['EMA']; ATR = arrs['ATR']
    n = arrs['n']
    if entry_mask is None:
        entry_mask = np.ones(n, dtype=bool)
    start_i = 50 if idx_range is None else max(50, idx_range[0])
    end_i = n if idx_range is None else min(n, idx_range[1])

    trades = []
    pos = None
    pending_lows = []; pending_highs = []; pending_atrs = []; pending_expiry = []

    for i in range(start_i, end_i):
        # Manage open position
        if pos is not None:
            if direction == 'long':
                sl_h = L[i] <= pos[0]; tp1_h = H[i] >= pos[1]; tp2_h = H[i] >= pos[2]
            else:
                sl_h = H[i] >= pos[0]; tp1_h = L[i] <= pos[1]; tp2_h = L[i] <= pos[2]
            if sl_h and (tp1_h or tp2_h):
                if direction == 'long':
                    if (O[i]-pos[0]) < (pos[1]-O[i]): tp1_h=False; tp2_h=False
                else:
                    if (pos[0]-O[i]) < (O[i]-pos[1]): tp1_h=False; tp2_h=False
            sgn = 1 if direction == 'long' else -1
            pnl1 = pos[3]; pnl2 = pos[4]; q1 = pos[5]; q2 = pos[6]; e = pos[7]
            if tp1_h and q1>0: pnl1 = (pos[1]-e)*0.5*sgn; q1=0
            if tp2_h and q2>0: pnl2 = (pos[2]-e)*0.5*sgn; q2=0
            if sl_h:
                if q1>0: pnl1 = (pos[0]-e)*0.5*sgn; q1=0
                if q2>0: pnl2 = (pos[0]-e)*0.5*sgn; q2=0
            if q1==0 and q2==0:
                trades.append(pnl1+pnl2 - REAL_COST)
                pos = None
            else:
                pos = (pos[0], pos[1], pos[2], pnl1, pnl2, q1, q2, e)

        # OB detection (mother at i-3, all past)
        if i >= 3:
            mc = C[i-3]; mo = O[i-3]; ml = L[i-3]; mh = H[i-3]; ma = ATR[i-3]
            if direction == 'long':
                if mc < mo and not np.isnan(ma):
                    move = max(H[i-2], H[i-1], H[i]) - mc
                    if move > ob_str * ma:
                        pending_lows.append(ml); pending_highs.append(mh)
                        pending_atrs.append(ma); pending_expiry.append(i + 30)
            else:
                if mc > mo and not np.isnan(ma):
                    move = mc - min(L[i-2], L[i-1], L[i])
                    if move > ob_str * ma:
                        pending_lows.append(ml); pending_highs.append(mh)
                        pending_atrs.append(ma); pending_expiry.append(i + 30)

        # Clean expired
        keep = [j for j, exp in enumerate(pending_expiry) if exp > i]
        if len(keep) < len(pending_expiry):
            pending_lows = [pending_lows[j] for j in keep]
            pending_highs = [pending_highs[j] for j in keep]
            pending_atrs = [pending_atrs[j] for j in keep]
            pending_expiry = [pending_expiry[j] for j in keep]

        # Entry attempt
        if pos is None and not np.isnan(EMA[i]) and entry_mask[i]:
            cond_trend = (C[i] > EMA[i]) if direction == 'long' else (C[i] < EMA[i])
            if cond_trend:
                for j in range(len(pending_lows)):
                    obl = pending_lows[j]; obh = pending_highs[j]; oba = pending_atrs[j]
                    if direction == 'long':
                        cond_in = L[i] <= obh and C[i] > obl
                        cond_rev = C[i] > O[i]
                    else:
                        cond_in = H[i] >= obl and C[i] < obh
                        cond_rev = C[i] < O[i]
                    if cond_in and cond_rev:
                        if direction == 'long':
                            sl = obl - oba * sl_atr * 0.5
                            tp1 = C[i] + oba * tp1_atr; tp2 = C[i] + oba * tp2_atr
                        else:
                            sl = obh + oba * sl_atr * 0.5
                            tp1 = C[i] - oba * tp1_atr; tp2 = C[i] - oba * tp2_atr
                        pos = (sl, tp1, tp2, 0, 0, 0.5, 0.5, C[i])
                        pending_lows.pop(j); pending_highs.pop(j); pending_atrs.pop(j); pending_expiry.pop(j)
                        break
    return trades

def stats(arr):
    if len(arr)==0: return None
    arr = np.array(arr)
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    return {'n':n,'wr':w/n*100,'net':net,'pf':pf,'dd':dd}

# Configs base (from previous backtest — best per TF for BOTH)
BEST_CONFIGS = {
    'M15': (1.5, 3, 6, 2.5),    # PF 1.11
    'M30': (1.0, 3, 6, 2.0),    # PF 1.19 (best Net BOTH)
    'H1':  (2.0, 5, 10, 2.5),   # PF 1.23
    'H4':  (1.0, 2, 4, 2.0),    # PF 1.29
}

def make_session_mask(arrs, allowed):
    return np.isin(arrs['SESSION'], list(allowed))

def make_atr_mask(arrs, lo, hi):
    a = arrs['ATR_PCT']
    return (~np.isnan(a)) & (a >= lo) & (a <= hi)

def make_slope_mask(arrs, direction, min_abs_slope):
    s = arrs['EMA_SLOPE']
    valid = ~np.isnan(s)
    if direction == 'long':
        return valid & (s >= min_abs_slope)
    else:
        return valid & (s <= -min_abs_slope)

def make_hour_mask(arrs, hours):
    return np.isin(arrs['HOUR'], list(hours))

def make_dow_mask(arrs, dows):
    return np.isin(arrs['DOW'], list(dows))

def fmt_stats(s, prefix=''):
    if s is None or s['n'] < 20: return f"{prefix}n={s['n'] if s else 0} (insuficient)"
    return f"{prefix}n={s['n']:>4} WR{s['wr']:.1f}% Net=${s['net']:+.0f} PF{s['pf']:.2f} DD${s['dd']:.0f}"

print()
print("="*150)
print("OPTIMITZACIO PER VARIANT (sobre BEST_CONFIGS de cada TF)")
print("="*150)

results_summary = []

for tf_name, (sl, tp1, tp2, ob) in BEST_CONFIGS.items():
    arrs = TF_ARRS[tf_name]
    print(f"\n{'#'*100}")
    print(f"# {tf_name}  (config: SL{sl} TP{tp1}/{tp2} OB{ob})  ({arrs['n']} bars)")
    print(f"{'#'*100}")

    # === Baseline (asia+london+ny — el que teníem) ===
    base_mask = make_session_mask(arrs, {'ASIA','LONDON','NY','OVERLAP'})
    print(f"\n{tf_name} BASELINE (totes sessions excepte DEAD):")
    for d in ['long','short']:
        t = bt_filtered(arrs, d, sl, tp1, tp2, ob, base_mask)
        print(f"  {d:5} {fmt_stats(stats(t))}")
        results_summary.append({'tf':tf_name,'variant':'baseline','dir':d,'stats':stats(t)})
    both = bt_filtered(arrs,'long',sl,tp1,tp2,ob,base_mask) + bt_filtered(arrs,'short',sl,tp1,tp2,ob,base_mask)
    print(f"  BOTH  {fmt_stats(stats(both))}")
    results_summary.append({'tf':tf_name,'variant':'baseline','dir':'BOTH','stats':stats(both)})

    # === Sessions individuals ===
    print(f"\n{tf_name} PER SESSIO:")
    for sess_name, sess_set in [('ASIA',{'ASIA'}),('LONDON',{'LONDON'}),
                                  ('OVERLAP',{'OVERLAP'}),('NY',{'NY'})]:
        m = make_session_mask(arrs, sess_set)
        for d in ['long','short']:
            t = bt_filtered(arrs, d, sl, tp1, tp2, ob, m)
            print(f"  {sess_name:8} {d:5} {fmt_stats(stats(t))}")
            results_summary.append({'tf':tf_name,'variant':f'session_{sess_name}','dir':d,'stats':stats(t)})

    # === Sessions combinades ===
    print(f"\n{tf_name} SESSIONS COMBINADES:")
    for combo_name, combo in [('LONDON+OVERLAP',{'LONDON','OVERLAP'}),
                                ('OVERLAP+NY',{'OVERLAP','NY'}),
                                ('LONDON+NY',{'LONDON','NY'}),
                                ('LDN+OVL+NY',{'LONDON','OVERLAP','NY'})]:
        m = make_session_mask(arrs, combo)
        l = bt_filtered(arrs,'long',sl,tp1,tp2,ob,m)
        s_ = bt_filtered(arrs,'short',sl,tp1,tp2,ob,m)
        both = l + s_
        print(f"  {combo_name:18} BOTH {fmt_stats(stats(both))} | L:{fmt_stats(stats(l))[:50]} | S:{fmt_stats(stats(s_))[:50]}")
        results_summary.append({'tf':tf_name,'variant':f'sess_combo_{combo_name}','dir':'BOTH','stats':stats(both)})

    # === ATR regime ===
    print(f"\n{tf_name} ATR REGIME:")
    for regime, (lo, hi) in [('LOW',(0.0,0.33)),('MID',(0.33,0.66)),('HIGH',(0.66,1.0))]:
        m = make_atr_mask(arrs, lo, hi) & base_mask
        l = bt_filtered(arrs,'long',sl,tp1,tp2,ob,m)
        s_ = bt_filtered(arrs,'short',sl,tp1,tp2,ob,m)
        both = l + s_
        print(f"  ATR_{regime:5} BOTH {fmt_stats(stats(both))}")
        results_summary.append({'tf':tf_name,'variant':f'atr_{regime}','dir':'BOTH','stats':stats(both)})

    # === EMA slope ===
    print(f"\n{tf_name} EMA SLOPE FILTER:")
    for slope_thresh in [0.0, 0.5, 1.0, 1.5]:
        m_long = base_mask & make_slope_mask(arrs, 'long', slope_thresh)
        m_short = base_mask & make_slope_mask(arrs, 'short', slope_thresh)
        l = bt_filtered(arrs,'long',sl,tp1,tp2,ob,m_long)
        s_ = bt_filtered(arrs,'short',sl,tp1,tp2,ob,m_short)
        both = l + s_
        print(f"  slope>={slope_thresh} BOTH {fmt_stats(stats(both))}")
        results_summary.append({'tf':tf_name,'variant':f'slope_{slope_thresh}','dir':'BOTH','stats':stats(both)})

    # === Day of week ===
    print(f"\n{tf_name} DOW FILTER:")
    for dow_name, dows in [('Mon-Wed',[0,1,2]),('Tue-Thu',[1,2,3]),('Mon-Fri',[0,1,2,3,4]),('Skip-Mon',[1,2,3,4]),('Skip-Fri',[0,1,2,3])]:
        m = base_mask & make_dow_mask(arrs, dows)
        l = bt_filtered(arrs,'long',sl,tp1,tp2,ob,m)
        s_ = bt_filtered(arrs,'short',sl,tp1,tp2,ob,m)
        both = l + s_
        print(f"  {dow_name:10} BOTH {fmt_stats(stats(both))}")
        results_summary.append({'tf':tf_name,'variant':f'dow_{dow_name}','dir':'BOTH','stats':stats(both)})

# =====================================================================================
# WALK-FORWARD VALIDATION
# Per cada TF, agafem la millor combinació trobada i la testem in-sample/out-sample
# =====================================================================================
print()
print("="*150)
print("WALK-FORWARD VALIDATION (in-sample 70% / out-sample 30%)")
print("="*150)

for tf_name, (sl, tp1, tp2, ob) in BEST_CONFIGS.items():
    arrs = TF_ARRS[tf_name]
    n = arrs['n']
    split = int(n * 0.7)
    print(f"\n{tf_name}: split={split}/{n}")

    # Baseline
    base_mask = make_session_mask(arrs, {'ASIA','LONDON','NY','OVERLAP'})

    # In-sample
    l_in = bt_filtered(arrs,'long',sl,tp1,tp2,ob,base_mask,(50,split))
    s_in = bt_filtered(arrs,'short',sl,tp1,tp2,ob,base_mask,(50,split))
    both_in = l_in + s_in

    # Out-sample
    l_out = bt_filtered(arrs,'long',sl,tp1,tp2,ob,base_mask,(split,n))
    s_out = bt_filtered(arrs,'short',sl,tp1,tp2,ob,base_mask,(split,n))
    both_out = l_out + s_out

    print(f"  IN-SAMPLE  BOTH: {fmt_stats(stats(both_in))}")
    print(f"  OUT-SAMPLE BOTH: {fmt_stats(stats(both_out))}")
    print(f"  IN  LONG:  {fmt_stats(stats(l_in))}")
    print(f"  OUT LONG:  {fmt_stats(stats(l_out))}")
    print(f"  IN  SHORT: {fmt_stats(stats(s_in))}")
    print(f"  OUT SHORT: {fmt_stats(stats(s_out))}")

# =====================================================================================
# TOP CONFIGS (filtrades) — global ranking
# =====================================================================================
print()
print("="*150)
print("TOP 20 VARIANTS — ranking per PF amb Net positiu i n>=50")
print("="*150)

valid = [r for r in results_summary
         if r['stats'] and r['stats']['n']>=50 and r['stats']['net']>0 and r['stats']['pf']>1.05]
valid.sort(key=lambda x: -x['stats']['pf'])

for r in valid[:25]:
    s = r['stats']
    print(f"  {r['tf']:<5} {r['variant']:<30} {r['dir']:<6} n={s['n']:>5} WR{s['wr']:>5.1f}% Net=${s['net']:>+8.0f} PF {s['pf']:.2f} DD${s['dd']:.0f}")

# =====================================================================================
# Final per-TF best summary
# =====================================================================================
print()
print("="*150)
print("MILLOR VARIANT PER TF (BOTH only):")
print("="*150)
best_per_tf = {}
for r in results_summary:
    if r['dir']!='BOTH': continue
    s = r['stats']
    if not s or s['n']<50 or s['net']<=0: continue
    key = r['tf']
    if key not in best_per_tf or s['pf'] > best_per_tf[key]['stats']['pf']:
        best_per_tf[key] = r

for tf in ['M15','M30','H1','H4']:
    if tf in best_per_tf:
        r = best_per_tf[tf]
        s = r['stats']
        print(f"  {tf}: variant={r['variant']:<25} | n={s['n']:>5} WR{s['wr']:.1f}% Net=${s['net']:+.0f} PF{s['pf']:.2f} DD${s['dd']:.0f}")
        net_005 = s['net']*5
        print(f"        Escalat 0.05 lot: Net 5y=${net_005:+.0f} ({net_005/50000*100/5:+.2f}%/any) DD={s['dd']*5:.0f}")
    else:
        print(f"  {tf}: SENSE EDGE robust")

print()
print("DONE")
