"""
Comparativa de timeframes per SMC OB:
M15, M30, H1, H2, H4, D1
amb config ULTIMATE (per-session + skip Thu + Donchian D1 + streak)
"""
import pandas as pd
import numpy as np

CSV = "xauusd_m5_5y.csv"
COMMISSION = 0.5; SPREAD = 0.50; SLIPPAGE = 0.20

print("Loading...", flush=True)
m5 = pd.read_csv(CSV, index_col=0, parse_dates=True)
m5.index = pd.to_datetime(m5.index, utc=True)
m5.columns = [c.lower() for c in m5.columns]
if 'tick_volume' not in m5.columns: m5['tick_volume'] = m5.get('volume', 1)

# D1 Donchian regime
d1 = m5.resample('1D').agg(open=('open','first'),high=('high','max'),
    low=('low','min'),close=('close','last')).dropna()
d1['don_high_55'] = d1['high'].rolling(55).max().shift(1)
d1['don_low_20'] = d1['low'].rolling(20).min().shift(1)
print("Computing Donchian regime...", flush=True)
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

# Aggregate to multiple TFs
def aggregate(df_, rule):
    return df_.resample(rule).agg(
        open=('open', 'first'), high=('high', 'max'),
        low=('low', 'min'), close=('close', 'last'),
        tick_volume=('tick_volume', 'sum')
    ).dropna()

def add_indicators(df_):
    df_ = df_.copy()
    df_['ema50'] = df_['close'].ewm(span=50, adjust=False).mean()
    hl = df_['high']-df_['low']; hc = (df_['high']-df_['close'].shift()).abs(); lc = (df_['low']-df_['close'].shift()).abs()
    tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
    df_['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
    return df_

print("Aggregating TFs...", flush=True)
tfs = {
    'M15': add_indicators(aggregate(m5, '15min')),
    'M30': add_indicators(aggregate(m5, '30min')),
    'H1':  add_indicators(aggregate(m5, '1h')),
    'H2':  add_indicators(aggregate(m5, '2h')),
    'H4':  add_indicators(aggregate(m5, '4h')),
}
for tf, df_ in tfs.items():
    print(f"  {tf}: {len(df_)} bars", flush=True)

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

def s2_ob(df_, session_params=SESSION_PARAMS, donchian_filter=False, skip_thursday=True):
    trades = []; pos = None
    pending_obs = []
    for i in range(50, len(df_)-3):
        bar = df_.iloc[i]; ts = df_.index[i]
        if pos is not None:
            sl_h = bar['low'] <= pos['sl']
            tp1_h = bar['high'] >= pos['tp1']
            tp2_h = bar['high'] >= pos['tp2']
            if sl_h and (tp1_h or tp2_h):
                if (bar['open']-pos['sl']) < (pos['tp1']-bar['open']): tp1_h=False; tp2_h=False
            if tp1_h and pos['q1']>0: pos['pnl1'] = (pos['tp1']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q1']=0
            if tp2_h and pos['q2']>0: pos['pnl2'] = (pos['tp2']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q2']=0
            if sl_h:
                if pos['q1']>0: pos['pnl1'] = (pos['sl']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q1']=0
                if pos['q2']>0: pos['pnl2'] = (pos['sl']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q2']=0
            if pos['q1']==0 and pos['q2']==0:
                tp = pos.get('pnl1',0)+pos.get('pnl2',0)-COMMISSION*2-SPREAD
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
                pending_obs.append({'ob_low':b0['low'],'ob_high':b0['high'],
                    'expiry':i+30,'atr0':b0['atr']})
        pending_obs = [o for o in pending_obs if o['expiry'] > i]
        if pos is None and pd.notna(bar['ema50']) and bar['close'] > bar['ema50']:
            if skip_thursday and ts.weekday() == 3: continue
            if donchian_filter and not is_donchian_long(ts): continue
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

def stats(arr_or_trades, name):
    if isinstance(arr_or_trades, list):
        if not arr_or_trades: print(f"{name:>50}: 0"); return None
        arr = np.array([t['pnl'] for t in arr_or_trades])
    else:
        arr = arr_or_trades
        if len(arr) == 0: return None
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    print(f"{name:>50}: n={n:>4} | WR {w/n*100:>5.1f}% | Net ${net:+.0f} | PF {pf:.2f} | DD ${dd:.0f}", flush=True)
    return {'n':n,'pf':pf,'net':net,'dd':dd}

print()
print("="*120)
print("COMPARATIVA TIMEFRAMES — SMC OB amb ULTIMATE config:")
print("="*120)

for tf_name, df_ in tfs.items():
    print(f"\n>>> {tf_name}:")
    # Without Donchian (raw)
    trades = s2_ob(df_, donchian_filter=False)
    stats(trades, f"{tf_name} per-session+skipThu (no Donchian)")
    # With Donchian
    trades = s2_ob(df_, donchian_filter=True)
    s_don = stats(trades, f"{tf_name} + Donchian filter")
    # With Streak
    if trades:
        sized = apply_streak(trades)
        stats(sized, f"{tf_name} + Donchian + Streak (ULTIMATE)")

# Per any of best TF
print()
print("="*120)
print("Per any del millor TF (en busca):")
print("="*120)
best_tf = None
best_pf = 0
for tf_name, df_ in tfs.items():
    trades = s2_ob(df_, donchian_filter=True)
    if trades:
        arr = np.array([t['pnl'] for t in trades])
        pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
        pf=pf_p/pf_l if pf_l else 0
        if pf > best_pf:
            best_pf = pf; best_tf = tf_name; best_trades = trades

print(f"\nMillor TF: {best_tf} amb PF {best_pf:.2f}")
if best_trades:
    tdf = pd.DataFrame(best_trades)
    tdf['year'] = pd.to_datetime(tdf['ts']).dt.year
    print("\nPer any:")
    for yr in sorted(tdf['year'].unique()):
        ydf = tdf[tdf['year']==yr]
        arr = ydf['pnl'].values
        n=len(arr); w=(arr>0).sum(); net=arr.sum()
        pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
        pf=pf_p/pf_l if pf_l else 0
        print(f"  {yr}: n={n:>3} | WR {w/n*100 if n else 0:>5.1f}% | Net ${net:+.0f} | PF {pf:.2f}")
