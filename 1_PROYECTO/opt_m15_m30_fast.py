"""Optimització M15+M30 amb grid REDUÏT i sample manageable."""
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
# Convert to dict for fast lookup
DON_LOOKUP = d1_in_long.to_dict()
def is_donchian_long(ts):
    d = pd.Timestamp(ts).normalize()
    if d in DON_LOOKUP: return DON_LOOKUP[d]
    # Fallback for missing dates
    return False

print("Aggregating + indicators...", flush=True)
def prep_tf(rule):
    df_ = m5.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last'),tick_volume=('tick_volume','sum')).dropna()
    df_['ema50'] = df_['close'].ewm(span=50, adjust=False).mean()
    hl = df_['high']-df_['low']; hc = (df_['high']-df_['close'].shift()).abs(); lc = (df_['low']-df_['close'].shift()).abs()
    tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
    df_['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
    return df_

m15 = prep_tf('15min')
m30 = prep_tf('30min')
print(f"M15: {len(m15)} | M30: {len(m30)}", flush=True)

# Convert to numpy arrays for speed
def df_to_arrays(df_):
    return {
        'idx': df_.index.values,
        'open': df_['open'].values,
        'high': df_['high'].values,
        'low': df_['low'].values,
        'close': df_['close'].values,
        'ema50': df_['ema50'].values,
        'atr': df_['atr'].values,
    }

def get_session_int(hour):
    if hour < 7: return 0  # ASIA
    elif hour < 13: return 1  # LONDON
    elif hour < 16: return 2  # OVERLAP
    elif hour < 21: return 3  # NY
    else: return 4  # DEAD

def s2_ob_fast(arr, session_idx, sess_target, sl_atr, tp1_atr, tp2_atr, ob_str,
               donchian_filter=False, skip_thursday=False, weekday=None):
    """Fast version with numpy. Computes only for ONE session."""
    O = arr['open']; H = arr['high']; L = arr['low']; C = arr['close']
    EMA = arr['ema50']; ATR = arr['atr']; IDX = arr['idx']
    n = len(C)
    trades = []
    pos = None
    pending_obs = []
    for i in range(50, n-3):
        ts_int = IDX[i]
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
                trades.append({'ts': pos['ts_int'], 'pnl': tp})
                pos = None
        b0_close = C[i-1]; b0_open = O[i-1]; b0_low = L[i-1]; b0_high = H[i-1]; b0_atr = ATR[i-1]
        is_red = b0_close < b0_open
        if is_red and not np.isnan(b0_atr):
            future_high = max(H[i], H[i+1], H[i+2])
            move = future_high - b0_close
            if move > ob_str * b0_atr:
                pending_obs.append({'ob_low':b0_low,'ob_high':b0_high,'expiry':i+30,'atr0':b0_atr})
        pending_obs = [o for o in pending_obs if o['expiry'] > i]
        if pos is None:
            if not (not np.isnan(EMA[i]) and C[i] > EMA[i]): continue
            if session_idx[i] != sess_target: continue
            if skip_thursday and weekday[i] == 3: continue
            if donchian_filter and not is_donchian_long(pd.Timestamp(IDX[i])): continue
            for ob in list(pending_obs):
                if L[i] <= ob['ob_high'] and C[i] > ob['ob_low']:
                    if C[i] > O[i]:
                        atr = ob['atr0']; e = C[i]
                        pos = {'side':'L','e':e,'ts_int':IDX[i],
                            'sl':ob['ob_low']-atr*sl_atr*0.5,
                            'tp1':e+atr*tp1_atr,'tp2':e+atr*tp2_atr,'q1':0.5,'q2':0.5}
                        pending_obs.remove(ob); break
    return trades

def s2_ob_full(arr, session_params, session_idx, weekday, donchian_filter=False, skip_thursday=False, atr_pct_max=None, atr_pct_arr=None):
    """Full version using session_params dict."""
    O = arr['open']; H = arr['high']; L = arr['low']; C = arr['close']
    EMA = arr['ema50']; ATR = arr['atr']; IDX = arr['idx']
    n = len(C)
    trades = []; pos = None; pending_obs = []
    sess_map = {0:'ASIA', 1:'LONDON', 2:'OVERLAP', 3:'NY', 4:'DEAD'}
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
                trades.append({'ts': pos['ts_int'], 'pnl': tp})
                pos = None
        b0_close = C[i-1]; b0_open = O[i-1]; b0_low = L[i-1]; b0_high = H[i-1]; b0_atr = ATR[i-1]
        sess_key = sess_map.get(session_idx[i])
        params = session_params.get(sess_key)
        if params is None:
            pending_obs = [o for o in pending_obs if o['expiry'] > i]; continue
        sl_atr, tp1_atr, tp2_atr, ob_str = params
        if b0_close < b0_open and not np.isnan(b0_atr):
            future_high = max(H[i], H[i+1], H[i+2])
            move = future_high - b0_close
            if move > ob_str * b0_atr:
                pending_obs.append({'ob_low':b0_low,'ob_high':b0_high,'expiry':i+30,'atr0':b0_atr})
        pending_obs = [o for o in pending_obs if o['expiry'] > i]
        if pos is None and not np.isnan(EMA[i]) and C[i] > EMA[i]:
            if skip_thursday and weekday[i] == 3: continue
            if donchian_filter and not is_donchian_long(pd.Timestamp(IDX[i])): continue
            if atr_pct_max is not None and atr_pct_arr is not None and not np.isnan(atr_pct_arr[i]) and atr_pct_arr[i] > atr_pct_max: continue
            for ob in list(pending_obs):
                if L[i] <= ob['ob_high'] and C[i] > ob['ob_low']:
                    if C[i] > O[i]:
                        atr = ob['atr0']; e = C[i]
                        pos = {'side':'L','e':e,'ts_int':IDX[i],
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

# Pre-compute session indices per TF
def precompute(df_):
    arr = df_to_arrays(df_)
    sess_idx = np.array([get_session_int(pd.Timestamp(t).hour) for t in df_.index])
    weekday = np.array([pd.Timestamp(t).weekday() for t in df_.index])
    return arr, sess_idx, weekday

print("Precomputing arrays...", flush=True)
m15_arr, m15_sess, m15_wkd = precompute(m15)
m30_arr, m30_sess, m30_wkd = precompute(m30)
print("Done", flush=True)

# Reduced grid
SL_GRID = [1.0, 1.5, 2.0]
TP_GRID = [(2,4), (3,6), (5,10)]
OB_GRID = [1.5, 2.0, 2.5]

OPT_RESULTS = {}
for tf_name, (arr, sess, wkd) in [('M15', (m15_arr, m15_sess, m15_wkd)),
                                    ('M30', (m30_arr, m30_sess, m30_wkd))]:
    print(f"\n>>> Grid search {tf_name}...", flush=True)
    params_for_tf = {}
    for sess_name, sess_target in [('ASIA',0), ('LONDON',1), ('NY',3)]:
        best_pf = 0; best_params = None; best_stats = None
        tested = 0
        for sl in SL_GRID:
            for tp_combo in TP_GRID:
                for ob in OB_GRID:
                    trades = s2_ob_fast(arr, sess, sess_target, sl, tp_combo[0], tp_combo[1], ob, weekday=wkd)
                    tested += 1
                    if trades and len(trades) >= 30:
                        s = stats_arr(np.array([t['pnl'] for t in trades]))
                        if s and s['pf'] > best_pf and s['net'] > 50:
                            best_pf = s['pf']; best_params = (sl, tp_combo[0], tp_combo[1], ob); best_stats = s
        if best_params:
            print(f"  {sess_name}: SL{best_params[0]} TP{best_params[1]}/{best_params[2]} OB{best_params[3]} | n={best_stats['n']} Net=${best_stats['net']:.0f} PF={best_stats['pf']:.2f}", flush=True)
            params_for_tf[sess_name] = best_params
        else:
            print(f"  {sess_name}: NO good config", flush=True)
    OPT_RESULTS[tf_name] = params_for_tf

# Apply filters
print()
print("="*120)
print("APPLYING ALL FILTERS:")
print("="*120)

FINAL_RES = {}
for tf_name, (arr, sess, wkd) in [('M15', (m15_arr, m15_sess, m15_wkd)),
                                    ('M30', (m30_arr, m30_sess, m30_wkd))]:
    params = OPT_RESULTS[tf_name]
    print(f"\n>>> {tf_name}:")

    # Baseline
    trades = s2_ob_full(arr, params, sess, wkd)
    s_b = stats_arr(np.array([t['pnl'] for t in trades]))
    if s_b: print(f"  BASELINE per-session: n={s_b['n']} Net=${s_b['net']:.0f} PF={s_b['pf']:.2f} DD=${s_b['dd']:.0f}", flush=True)

    # +Skip Thu
    trades_thu = s2_ob_full(arr, params, sess, wkd, skip_thursday=True)
    s_thu = stats_arr(np.array([t['pnl'] for t in trades_thu]))
    keep_thu = s_thu and s_thu['pf'] > s_b['pf'] + 0.10
    if s_thu: print(f"  + Skip Thu: n={s_thu['n']} Net=${s_thu['net']:.0f} PF={s_thu['pf']:.2f} {'KEEP' if keep_thu else 'DISCARD'}", flush=True)

    # +Donchian D1
    trades_don = s2_ob_full(arr, params, sess, wkd, skip_thursday=keep_thu, donchian_filter=True)
    s_don = stats_arr(np.array([t['pnl'] for t in trades_don]))
    prev = s_thu if keep_thu else s_b
    keep_don = s_don and s_don['pf'] > prev['pf'] + 0.10
    if s_don: print(f"  + Donchian D1: n={s_don['n']} Net=${s_don['net']:.0f} PF={s_don['pf']:.2f} {'KEEP' if keep_don else 'DISCARD'}", flush=True)

    # FINAL config
    final_trades = s2_ob_full(arr, params, sess, wkd, skip_thursday=keep_thu, donchian_filter=keep_don)
    sized = apply_streak(final_trades)
    s_streak = stats_arr(sized)

    if s_streak:
        print(f"\n  *** FINAL {tf_name} + Streak: n={s_streak['n']} Net=${s_streak['net']:.0f} PF={s_streak['pf']:.2f} DD=${s_streak['dd']:.0f}", flush=True)

    FINAL_RES[tf_name] = {'params':params, 'filters':{'thu':keep_thu,'don':keep_don}, 'streak':s_streak}

# Comparativa amb H4
print()
print("="*120)
print("COMPARATIVA OPTIMITZADA — escalada a 0.05 lot, $10k account:")
print("="*120)
print(f"{'TF':>6} | {'Trades':>6} | {'WR':>5} | {'Net 5y':>10} | {'%/any':>7} | {'PF':>5} | {'DD':>6} | {'DD%':>5}")
print("-"*100)

for tf_name, r in FINAL_RES.items():
    s = r['streak']
    if s:
        net_005 = s['net'] * 5
        pct_year = net_005/10000/5*100
        dd_005 = s['dd'] * 5
        print(f"  {tf_name:>4} | {s['n']:>6} | {s['wr']:>4.1f}% | ${net_005:>+8.0f} | {pct_year:>+5.1f}% | {s['pf']:>4.2f} | ${dd_005:>4.0f} | {dd_005/10000*100:>4.1f}%", flush=True)

# H4 reference
print(f"  {'H4':>4} | {100:>6} | {66.0:>4.1f}% | ${4105*5:>+8.0f} | {4105*5/10000/5*100:>+5.1f}% | {4.92:>4.2f} | ${118*5:>4.0f} | {118*5/10000*100:>4.1f}%")

print()
for tf_name, r in FINAL_RES.items():
    print(f"{tf_name} params: {r['params']}")
    print(f"{tf_name} filters: {r['filters']}")
