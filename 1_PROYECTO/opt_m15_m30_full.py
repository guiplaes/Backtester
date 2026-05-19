"""
Optimització COMPLETA M15 i M30 amb mateix pipeline que H4:
1. Grid search per-session params
2. Skip Thursday
3. Trail BE
4. Skip top 5% ATR
5. Donchian D1 macro filter
6. Streak sizing
"""
import pandas as pd
import numpy as np

CSV = "xauusd_m5_5y.csv"
REAL_COST_BASE = 0.40  # per 0.01 lot RT

m5 = pd.read_csv(CSV, index_col=0, parse_dates=True)
m5.index = pd.to_datetime(m5.index, utc=True)
m5.columns = [c.lower() for c in m5.columns]
if 'tick_volume' not in m5.columns: m5['tick_volume'] = m5.get('volume', 1)

# Donchian D1 regime
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

def is_donchian_long(ts):
    d = pd.Timestamp(ts).normalize()
    if d in d1_in_long.index: return d1_in_long.loc[d]
    prior = d1_in_long[d1_in_long.index <= d]
    return prior.iloc[-1] if len(prior) else False

def aggregate(df_, rule):
    return df_.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last'),tick_volume=('tick_volume','sum')).dropna()

def add_ind(df_):
    df_ = df_.copy()
    df_['ema50'] = df_['close'].ewm(span=50, adjust=False).mean()
    hl = df_['high']-df_['low']; hc = (df_['high']-df_['close'].shift()).abs(); lc = (df_['low']-df_['close'].shift()).abs()
    tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
    df_['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
    df_['atr_pct'] = df_['atr'].rolling(200).rank(pct=True)
    return df_

def get_session(h):
    if h < 7: return 'ASIA'
    elif h < 13: return 'LONDON'
    elif h < 16: return 'OVERLAP'
    elif h < 21: return 'NY'
    else: return 'DEAD'

def s2_ob(df_, session_params, donchian_filter=False, skip_thursday=False,
          trail_be=False, atr_pct_max=None):
    trades = []; pos = None; pending_obs = []
    for i in range(50, len(df_)-3):
        bar = df_.iloc[i]; ts = df_.index[i]
        if pos is not None:
            sl_h = bar['low'] <= pos['sl']
            tp1_h = bar['high'] >= pos['tp1']
            tp2_h = bar['high'] >= pos['tp2']
            if sl_h and (tp1_h or tp2_h):
                if (bar['open']-pos['sl']) < (pos['tp1']-bar['open']): tp1_h=False; tp2_h=False
            if tp1_h and pos['q1']>0:
                pos['pnl1'] = (pos['tp1']-pos['e'])*0.5; pos['q1']=0
                if trail_be:
                    pos['sl'] = max(pos['sl'], pos['e'] + 0.05)
            if tp2_h and pos['q2']>0: pos['pnl2'] = (pos['tp2']-pos['e'])*0.5; pos['q2']=0
            if sl_h:
                if pos['q1']>0: pos['pnl1'] = (pos['sl']-pos['e'])*0.5; pos['q1']=0
                if pos['q2']>0: pos['pnl2'] = (pos['sl']-pos['e'])*0.5; pos['q2']=0
            if pos['q1']==0 and pos['q2']==0:
                tp = pos.get('pnl1',0)+pos.get('pnl2',0) - REAL_COST_BASE
                trades.append({'ts': pos['ts'], 'pnl': tp, 'session': pos['session']})
                pos = None
        b0 = df_.iloc[i-1]
        is_red = b0['close'] < b0['open']
        sess = get_session(ts.hour)
        params = session_params.get(sess)
        if params is None:
            pending_obs = [o for o in pending_obs if o['expiry'] > i]; continue
        sl_atr, tp1_atr, tp2_atr, ob_str = params
        if is_red and not pd.isna(b0['atr']):
            future_high = max(df_.iloc[i]['high'], df_.iloc[i+1]['high'], df_.iloc[i+2]['high'])
            move = future_high - b0['close']
            if move > ob_str * b0['atr']:
                pending_obs.append({'ob_low':b0['low'],'ob_high':b0['high'],'expiry':i+30,'atr0':b0['atr']})
        pending_obs = [o for o in pending_obs if o['expiry'] > i]
        if pos is None and pd.notna(bar['ema50']) and bar['close'] > bar['ema50']:
            if skip_thursday and ts.weekday() == 3: continue
            if donchian_filter and not is_donchian_long(ts): continue
            if atr_pct_max is not None and pd.notna(bar.get('atr_pct')) and bar['atr_pct'] > atr_pct_max: continue
            for ob in list(pending_obs):
                if bar['low'] <= ob['ob_high'] and bar['close'] > ob['ob_low']:
                    if bar['close'] > bar['open']:
                        atr = ob['atr0']; e = bar['close']
                        pos = {'side':'L','e':e,'ts':ts,'session':sess,
                            'sl':ob['ob_low']-atr*sl_atr*0.5,
                            'tp1':e+atr*tp1_atr,'tp2':e+atr*tp2_atr,'q1':0.5,'q2':0.5}
                        pending_obs.remove(ob); break
    return trades

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

def stats_arr(arr):
    if len(arr)==0: return None
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    return {'n':n,'wr':w/n*100,'net':net,'pf':pf,'dd':dd}

print("Aggregating M15 and M30...", flush=True)
tfs = {
    'M15': add_ind(aggregate(m5, '15min')),
    'M30': add_ind(aggregate(m5, '30min')),
}
for tf_name, df_ in tfs.items():
    print(f"  {tf_name}: {len(df_)} bars", flush=True)

# STEP 1: Grid search per session for each TF
print()
print("="*120)
print("STEP 1: GRID SEARCH PER-SESSION PER M15 i M30")
print("="*120)

OPT_PARAMS_BY_TF = {}
for tf_name, df_ in tfs.items():
    print(f"\n>>> Optimizing {tf_name}...")
    params_for_tf = {}
    for sess_name in ['ASIA', 'LONDON', 'NY']:
        only_this = {s: None for s in ['ASIA', 'LONDON', 'OVERLAP', 'NY', 'DEAD']}
        best_pf = 0; best_p = None; best_s = None
        for sl in [0.5, 1.0, 1.5, 2.0]:
            for tp_combo in [(2,4), (3,6), (4,8), (5,10), (6,12), (8,16)]:
                for ob_str in [1.5, 1.8, 2.0, 2.5]:
                    only_this[sess_name] = (sl, tp_combo[0], tp_combo[1], ob_str)
                    trades = s2_ob(df_, only_this)
                    if trades and len(trades) >= 30:
                        s = stats_arr(np.array([t['pnl'] for t in trades]))
                        if s and s['pf'] > best_pf and s['net'] > 100:
                            best_pf = s['pf']; best_p = (sl, tp_combo[0], tp_combo[1], ob_str); best_s = s
        if best_p:
            print(f"  BEST {sess_name}: SL{best_p[0]} TP{best_p[1]}/{best_p[2]} OB{best_p[3]} | n={best_s['n']} Net=${best_s['net']:.0f} PF={best_s['pf']:.2f}", flush=True)
            params_for_tf[sess_name] = best_p
    OPT_PARAMS_BY_TF[tf_name] = params_for_tf

# STEP 2: Apply additional filters
print()
print("="*120)
print("STEP 2: APLICAR FILTRES UN A UN sobre cada TF optimitzat")
print("="*120)

FINAL_RESULTS = {}
for tf_name, df_ in tfs.items():
    print(f"\n>>> {tf_name}:")
    params = OPT_PARAMS_BY_TF[tf_name]

    # Baseline (just per-session)
    trades = s2_ob(df_, params)
    s_base = stats_arr(np.array([t['pnl'] for t in trades]))
    if s_base:
        print(f"  Baseline per-session: n={s_base['n']} Net=${s_base['net']:.0f} PF={s_base['pf']:.2f} DD=${s_base['dd']:.0f}", flush=True)

    # Filter 1: Skip Thursday
    trades = s2_ob(df_, params, skip_thursday=True)
    s1 = stats_arr(np.array([t['pnl'] for t in trades]))
    keep_thu = s1 and s1['pf'] > s_base['pf'] + 0.10
    if s1:
        print(f"  + Skip Thu: n={s1['n']} Net=${s1['net']:.0f} PF={s1['pf']:.2f} {'KEEP' if keep_thu else 'DISCARD'}", flush=True)

    # Filter 2: Donchian D1
    trades = s2_ob(df_, params, skip_thursday=keep_thu, donchian_filter=True)
    s2 = stats_arr(np.array([t['pnl'] for t in trades]))
    prev = s1 if keep_thu else s_base
    keep_don = s2 and s2['pf'] > prev['pf'] + 0.10
    if s2:
        print(f"  + Donchian D1: n={s2['n']} Net=${s2['net']:.0f} PF={s2['pf']:.2f} {'KEEP' if keep_don else 'DISCARD'}", flush=True)

    # Filter 3: Trail BE
    trades = s2_ob(df_, params, skip_thursday=keep_thu, donchian_filter=keep_don, trail_be=True)
    s3 = stats_arr(np.array([t['pnl'] for t in trades]))
    prev = s2 if keep_don else (s1 if keep_thu else s_base)
    keep_be = s3 and s3['pf'] > prev['pf'] + 0.10
    if s3:
        print(f"  + Trail BE: n={s3['n']} Net=${s3['net']:.0f} PF={s3['pf']:.2f} {'KEEP' if keep_be else 'DISCARD'}", flush=True)

    # Filter 4: Skip top 5% ATR
    trades = s2_ob(df_, params, skip_thursday=keep_thu, donchian_filter=keep_don,
                   trail_be=keep_be, atr_pct_max=0.95)
    s4 = stats_arr(np.array([t['pnl'] for t in trades]))
    prev = s3 if keep_be else (s2 if keep_don else (s1 if keep_thu else s_base))
    keep_atr = s4 and s4['pf'] > prev['pf'] + 0.10
    if s4:
        print(f"  + Skip top 5% ATR: n={s4['n']} Net=${s4['net']:.0f} PF={s4['pf']:.2f} {'KEEP' if keep_atr else 'DISCARD'}", flush=True)

    # FINAL config
    final_trades = s2_ob(df_, params,
                          skip_thursday=keep_thu,
                          donchian_filter=keep_don,
                          trail_be=keep_be,
                          atr_pct_max=0.95 if keep_atr else None)
    s_fin = stats_arr(np.array([t['pnl'] for t in final_trades]))

    # Apply streak sizing
    sized = apply_streak(final_trades)
    s_streak = stats_arr(sized)

    if s_fin and s_streak:
        print(f"\n  *** FINAL {tf_name} (no streak): n={s_fin['n']} Net=${s_fin['net']:.0f} PF={s_fin['pf']:.2f} DD=${s_fin['dd']:.0f}", flush=True)
        print(f"  *** FINAL {tf_name} (+ Streak):  n={s_streak['n']} Net=${s_streak['net']:.0f} PF={s_streak['pf']:.2f} DD=${s_streak['dd']:.0f}", flush=True)

    FINAL_RESULTS[tf_name] = {
        'params': params,
        'filters': {'skip_thu':keep_thu,'donchian':keep_don,'trail_be':keep_be,'atr_filter':keep_atr},
        'stats_fixed': s_fin,
        'stats_streak': s_streak,
    }

# COMPARATIVA FINAL
print()
print("="*120)
print("COMPARATIVA FINAL (TOTS OPTIMITZATS) — sobre $10k account amb 0.05 lot:")
print("="*120)
print(f"{'TF':>6} | {'Trades':>6} | {'WR':>5} | {'Net (0.01)':>10} | {'Net (0.05)':>10} | {'%/any':>7} | {'PF':>5} | {'DD$':>6} | {'DD%':>5}")
print("-"*100)

# H4 reference (already known optimum)
H4_NET_001 = 4105 / 5  # streak result was $4105 over 5y at 0.01 lot scale
H4_PF = 4.92
H4_DD_001 = 118 / 5  # adjusted

# Show all
for tf_name, r in FINAL_RESULTS.items():
    s = r['stats_streak']
    if s:
        net_001 = s['net']  # was at 0.01 lot
        net_005 = net_001 * 5  # scale to 0.05 lot
        pct_year = net_005 / 10000 / 5 * 100
        dd_005 = s['dd'] * 5
        print(f"  {tf_name:>4} | {s['n']:>6} | {s['wr']:>4.1f}% | ${net_001:>+8.0f} | ${net_005:>+8.0f} | {pct_year:>+5.1f}% | {s['pf']:>4.2f} | ${dd_005:>4.0f} | {dd_005/10000*100:>4.1f}%")

# H4 reference
print(f"  {'H4':>4} | {100:>6} | {66.0:>4.1f}% | ${4105:>+8.0f} | ${4105*5:>+8.0f} | {4105*5/10000/5*100:>+5.1f}% | {4.92:>4.2f} | ${118*5:>4.0f} | {118*5/10000*100:>4.1f}%")
print()
print("Filters aplicats:")
for tf_name, r in FINAL_RESULTS.items():
    print(f"  {tf_name}: filters={r['filters']}")
    print(f"         params={r['params']}")
