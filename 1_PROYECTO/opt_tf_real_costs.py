"""Recompare ALL TFs with REAL VT Markets costs ($0.40/0.01 lot RT)."""
import pandas as pd
import numpy as np

CSV = "xauusd_m5_5y.csv"
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
    return df_

SESSION_PARAMS = {
    'ASIA': (1.0, 3, 6, 2.0),
    'LONDON': (1.0, 2, 4, 1.8),
    'NY': (1.0, 2, 4, 1.5),
}

def get_session(h):
    if h < 7: return 'ASIA'
    elif h < 13: return 'LONDON'
    elif h < 16: return 'OVERLAP'
    elif h < 21: return 'NY'
    else: return 'DEAD'

# REAL COST: $0.40 per trade for 0.01 lot
REAL_COST = 0.40

def s2_ob(df_, session_params, donchian_filter=True, skip_thursday=True, cost=REAL_COST):
    trades = []; pos = None; pending_obs = []
    for i in range(50, len(df_)-3):
        bar = df_.iloc[i]; ts = df_.index[i]
        if pos is not None:
            sl_h = bar['low'] <= pos['sl']
            tp1_h = bar['high'] >= pos['tp1']
            tp2_h = bar['high'] >= pos['tp2']
            if sl_h and (tp1_h or tp2_h):
                if (bar['open']-pos['sl']) < (pos['tp1']-bar['open']): tp1_h=False; tp2_h=False
            if tp1_h and pos['q1']>0: pos['pnl1'] = (pos['tp1']-pos['e'])*0.5; pos['q1']=0
            if tp2_h and pos['q2']>0: pos['pnl2'] = (pos['tp2']-pos['e'])*0.5; pos['q2']=0
            if sl_h:
                if pos['q1']>0: pos['pnl1'] = (pos['sl']-pos['e'])*0.5; pos['q1']=0
                if pos['q2']>0: pos['pnl2'] = (pos['sl']-pos['e'])*0.5; pos['q2']=0
            if pos['q1']==0 and pos['q2']==0:
                tp = pos.get('pnl1',0)+pos.get('pnl2',0) - cost
                trades.append({'ts': pos['ts'], 'pnl': tp})
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
            for ob in list(pending_obs):
                if bar['low'] <= ob['ob_high'] and bar['close'] > ob['ob_low']:
                    if bar['close'] > bar['open']:
                        atr = ob['atr0']; e = bar['close']
                        pos = {'side':'L','e':e,'ts':ts,
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

def stats(arr, name, capital=10000):
    if len(arr)==0: print(f"{name}: 0"); return None
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    pct = net/capital*100; ann = pct/5
    print(f"{name:>50}: n={n:>4} | WR {w/n*100:>5.1f}% | Net ${net:+.0f} ({ann:+.1f}%/any) | PF {pf:.2f} | DD ${dd:.0f} ({dd/capital*100:.1f}%)", flush=True)
    return {'n':n,'pf':pf,'net':net,'dd':dd}

print("Aggregating TFs...", flush=True)
tfs = {
    'M15': add_ind(aggregate(m5, '15min')),
    'M30': add_ind(aggregate(m5, '30min')),
    'H1':  add_ind(aggregate(m5, '1h')),
    'H2':  add_ind(aggregate(m5, '2h')),
    'H4':  add_ind(aggregate(m5, '4h')),
}
for tf_name, df_ in tfs.items():
    print(f"  {tf_name}: {len(df_)} bars", flush=True)

# OPTIMAL params per cada TF (ja teníem alguns)
TF_PARAMS = {
    'M15': SESSION_PARAMS,  # default
    'M30': SESSION_PARAMS,
    'H1': {  # found earlier: SL 1.5/2.0/1.5 TP 6/12 OB 2.0/1.8/1.5
        'ASIA': (1.5, 6, 12, 2.0),
        'LONDON': (2.0, 6, 12, 1.8),
        'NY': (1.5, 6, 12, 1.5),
    },
    'H2': {  # found earlier: SL 2.0/1.5/1.5 TP 4/8 OB
        'ASIA': (2.0, 4, 8, 2.0),
        'LONDON': (1.5, 4, 8, 1.8),
        'NY': (1.5, 4, 8, 1.5),
    },
    'H4': SESSION_PARAMS,
}

print()
print("="*120)
print(f"COMPARATIVA TFs amb COSTS REALS (${REAL_COST}/trade per 0.01 lot)")
print(f"Totes amb ULTIMATE: per-session + Donchian D1 + Streak sizing")
print(f"Capital: $10,000 | Lot: 0.05 (per a fer els nombres comparables)")
print("="*120)

LOT_MULT = 5  # 0.05 lot = 5x baseline 0.01

results = {}
for tf_name, df_ in tfs.items():
    params = TF_PARAMS[tf_name]
    trades = s2_ob(df_, params, donchian_filter=True, cost=REAL_COST*LOT_MULT)
    # Scale P&L to 0.05 lot
    for t in trades:
        # P&L was for 1 unit (0.01 lot) but we already deducted cost for 0.05 lot
        # Need to scale P&L to 0.05 too
        pass
    # Re-scale arrays
    arr_raw = np.array([t['pnl'] for t in trades])
    # P&L was computed at 0.01 lot but cost was 0.05 lot
    # Scale up P&L to 0.05 lot too
    arr_scaled = arr_raw * LOT_MULT  # P&L scales but cost was already at 0.05 lot
    # Wait — when I computed pnl1/pnl2 in s2_ob, those were in 1-unit (0.01 lot) terms
    # And subtracted cost = REAL_COST*LOT_MULT = $2 for 0.05 lot
    # So arr_raw includes: pnl_in_1_unit - $2_cost_for_0.05_lot
    # That's wrong. Let me fix.
    # Actually I want to sim 0.05 lot. P&L in 1 unit × 5 = P&L in 0.05 lot.
    # Cost in 0.05 lot = $0.40 × 5 = $2.
    # Net per trade = (pnl_1_unit × 5) - $2
    # In s2_ob I did: tp = pnl_1u - $2 = wrong (should be pnl_1u*5 - $2)
    # Let me recompute properly
    pass

# Restart with proper scaling
def s2_ob_proper(df_, session_params, donchian_filter=True, skip_thursday=True, lot_mult=5):
    """lot_mult: 5 means 0.05 lot (5x 0.01 baseline)"""
    cost = REAL_COST * lot_mult  # cost scales with lot
    trades = []; pos = None; pending_obs = []
    for i in range(50, len(df_)-3):
        bar = df_.iloc[i]; ts = df_.index[i]
        if pos is not None:
            sl_h = bar['low'] <= pos['sl']
            tp1_h = bar['high'] >= pos['tp1']
            tp2_h = bar['high'] >= pos['tp2']
            if sl_h and (tp1_h or tp2_h):
                if (bar['open']-pos['sl']) < (pos['tp1']-bar['open']): tp1_h=False; tp2_h=False
            if tp1_h and pos['q1']>0: pos['pnl1'] = (pos['tp1']-pos['e'])*0.5*lot_mult; pos['q1']=0
            if tp2_h and pos['q2']>0: pos['pnl2'] = (pos['tp2']-pos['e'])*0.5*lot_mult; pos['q2']=0
            if sl_h:
                if pos['q1']>0: pos['pnl1'] = (pos['sl']-pos['e'])*0.5*lot_mult; pos['q1']=0
                if pos['q2']>0: pos['pnl2'] = (pos['sl']-pos['e'])*0.5*lot_mult; pos['q2']=0
            if pos['q1']==0 and pos['q2']==0:
                tp = pos.get('pnl1',0)+pos.get('pnl2',0) - cost
                trades.append({'ts': pos['ts'], 'pnl': tp})
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
            for ob in list(pending_obs):
                if bar['low'] <= ob['ob_high'] and bar['close'] > ob['ob_low']:
                    if bar['close'] > bar['open']:
                        atr = ob['atr0']; e = bar['close']
                        pos = {'side':'L','e':e,'ts':ts,
                            'sl':ob['ob_low']-atr*sl_atr*0.5,
                            'tp1':e+atr*tp1_atr,'tp2':e+atr*tp2_atr,'q1':0.5,'q2':0.5}
                        pending_obs.remove(ob); break
    return trades

results = {}
for tf_name, df_ in tfs.items():
    params = TF_PARAMS[tf_name]
    trades = s2_ob_proper(df_, params, lot_mult=5)
    arr = np.array([t['pnl'] for t in trades])
    sized = apply_streak(trades)
    results[tf_name] = {'no_streak':arr, 'streak':sized, 'n':len(trades)}

print()
print("Sense Streak sizing:")
for tf_name in ['M15', 'M30', 'H1', 'H2', 'H4']:
    stats(results[tf_name]['no_streak'], f"{tf_name} (Fixed 0.05 lot)")

print()
print("Amb Streak sizing:")
for tf_name in ['M15', 'M30', 'H1', 'H2', 'H4']:
    stats(results[tf_name]['streak'], f"{tf_name} (Streak 0.05 lot avg)")

print()
print("="*120)
print("PER ANY del millor TF:")
print("="*120)
best_pf = 0; best_tf = None; best_arr = None
for tf_name, r in results.items():
    arr = r['streak']
    if len(arr) == 0: continue
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    if arr.sum() > 1000 and pf > best_pf:
        best_pf = pf; best_tf = tf_name

print(f"Millor (per net + PF): {best_tf}")

# H4 ULTIMATE i H2 ULTIMATE per any
for tf_compare in ['H2', 'H4']:
    print(f"\nPer any {tf_compare} (ULTIMATE 0.05 lot):")
    df_ = tfs[tf_compare]
    trades = s2_ob_proper(df_, TF_PARAMS[tf_compare], lot_mult=5)
    sized = apply_streak(trades)
    # Need to merge ts with sized
    tdf = pd.DataFrame([{'ts': t['ts'], 'pnl_sized': p} for t,p in zip(trades, sized)])
    tdf['year'] = pd.to_datetime(tdf['ts']).dt.year
    for yr in sorted(tdf['year'].unique()):
        ydf = tdf[tdf['year']==yr]
        arr = ydf['pnl_sized'].values
        n=len(arr); w=(arr>0).sum(); net=arr.sum()
        pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
        pf=pf_p/pf_l if pf_l else 0
        print(f"  {yr}: n={n:>3} | WR {w/n*100 if n else 0:>5.1f}% | Net ${net:+.0f} | PF {pf:.2f}", flush=True)
