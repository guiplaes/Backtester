"""
5 optimitzacions avançades sobre el SMC OB final:
1. Streak sizing
2. Multi-OB confluence
3. FVG filter combo
4. M15 timeframe (smaller scale)
5. Donchian D1 macro layer combo
"""
import pandas as pd
import numpy as np

CSV = "xauusd_m5_5y.csv"
COMMISSION = 0.5; SPREAD = 0.50; SLIPPAGE = 0.20

m5 = pd.read_csv(CSV, index_col=0, parse_dates=True)
m5.index = pd.to_datetime(m5.index, utc=True)
m5.columns = [c.lower() for c in m5.columns]
if 'tick_volume' not in m5.columns: m5['tick_volume'] = m5.get('volume', 1)

h4 = m5.resample('4h').agg(open=('open','first'),high=('high','max'),
    low=('low','min'),close=('close','last'),tick_volume=('tick_volume','sum')).dropna()
h4['ema50'] = h4['close'].ewm(span=50, adjust=False).mean()
hl = h4['high']-h4['low']; hc = (h4['high']-h4['close'].shift()).abs(); lc = (h4['low']-h4['close'].shift()).abs()
tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
h4['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()

# D1 for macro filter
d1 = m5.resample('1D').agg(open=('open','first'),high=('high','max'),
    low=('low','min'),close=('close','last')).dropna()
d1['don_high_55'] = d1['high'].rolling(55).max().shift(1)
d1['don_low_20'] = d1['low'].rolling(20).min().shift(1)
hl_d = d1['high']-d1['low']; hc_d = (d1['high']-d1['close'].shift()).abs(); lc_d = (d1['low']-d1['close'].shift()).abs()
tr_d = pd.concat([hl_d,hc_d,lc_d],axis=1).max(axis=1)
d1['atr'] = tr_d.ewm(alpha=1/14, adjust=False).mean()

# Compute Donchian regime: in_donchian_long = True if Donchian system is currently in a LONG position
print("Computing Donchian D1 regime...", flush=True)
d1_in_long = pd.Series(False, index=d1.index)
in_pos = False
for i in range(56, len(d1)):
    if not in_pos:
        if d1.iloc[i]['close'] > d1.iloc[i]['don_high_55']:
            in_pos = True
    else:
        if d1.iloc[i]['close'] < d1.iloc[i]['don_low_20']:
            in_pos = False
    d1_in_long.iloc[i] = in_pos

# Map D1 regime to H4 timestamps
def is_donchian_long(ts):
    d = pd.Timestamp(ts).normalize()
    if d in d1_in_long.index:
        return d1_in_long.loc[d]
    # Find nearest prior date
    prior = d1_in_long[d1_in_long.index <= d]
    return prior.iloc[-1] if len(prior) else False

print(f"H4: {len(h4)} | D1: {len(d1)}", flush=True)

# Compute FVG on H4
print("Computing FVGs on H4...", flush=True)
h4['bull_fvg'] = False
for i in range(2, len(h4)):
    if h4.iloc[i-2]['high'] < h4.iloc[i]['low']:
        h4.iat[i, h4.columns.get_loc('bull_fvg')] = True

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

def s2_ob(df_, session_params, skip_thursday=True, multi_ob=False, fvg_filter=False, donchian_filter=False):
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
            pending_obs = [o for o in pending_obs if o['expiry'] > i]
            continue
        sl_atr, tp1_atr, tp2_atr, ob_str = params
        if is_red and not pd.isna(b0['atr']):
            future_high = max(df_.iloc[i]['high'], df_.iloc[i+1]['high'], df_.iloc[i+2]['high'])
            move = future_high - b0['close']
            if move > ob_str * b0['atr']:
                pending_obs.append({'ob_low':b0['low'],'ob_high':b0['high'],
                    'expiry':i+30,'atr0':b0['atr'], 'idx':i})
        pending_obs = [o for o in pending_obs if o['expiry'] > i]
        if pos is None and pd.notna(bar['ema50']) and bar['close'] > bar['ema50']:
            if skip_thursday and ts.weekday() == 3: continue
            if donchian_filter and not is_donchian_long(ts): continue
            for ob in list(pending_obs):
                if bar['low'] <= ob['ob_high'] and bar['close'] > ob['ob_low']:
                    if bar['close'] > bar['open']:
                        # Multi-OB confluence: require another OB within 5 bars
                        if multi_ob:
                            other_obs_near = [o for o in pending_obs if o is not ob and abs(o['ob_high']-ob['ob_high']) < ob['atr0']*2]
                            if not other_obs_near: continue
                        # FVG filter: require bullish FVG within last 5 bars
                        if fvg_filter:
                            recent_fvg = df_.iloc[max(0,i-5):i+1]['bull_fvg'].any()
                            if not recent_fvg: continue
                        atr = ob['atr0']; e = bar['close']
                        pos = {'side':'L','e':e,'ts':ts,'session':sess,
                            'sl':ob['ob_low']-atr*sl_atr*0.5,
                            'tp1':e+atr*tp1_atr,'tp2':e+atr*tp2_atr,'q1':0.5,'q2':0.5}
                        pending_obs.remove(ob); break
    return trades

def stats(trades, name):
    if not trades: print(f"{name:>50}: 0"); return None
    arr = np.array([t['pnl'] for t in trades])
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    print(f"{name:>50}: n={n:>3} | WR {w/n*100:>5.1f}% | Net ${net:+.0f} | PF {pf:.2f} | DD ${dd:.0f}", flush=True)
    return {'n':n,'pf':pf,'net':net,'dd':dd}

print()
print("="*120)
print("BASELINE: Per-session params + skip Thursday")
print("="*120)
baseline_trades = s2_ob(h4, SESSION_PARAMS)
baseline = stats(baseline_trades, "BASELINE")

# OPT 1: Streak sizing
print()
print("="*120)
print("OPT 1: Streak Sizing")
print("="*120)
def apply_streak(trades, k=1.3, min_size=0.5, max_size=2.0):
    out = []; size = 1.0; consec_l = 0; consec_w = 0
    for t in trades:
        out.append({**t, 'pnl_sized': t['pnl']*size, 'size': size})
        if t['pnl'] > 0:
            consec_w += 1; consec_l = 0
            if consec_w >= 3 and size < max_size: size = min(max_size, size*k)
            if consec_w == 1 and size < 1.0: size = 1.0
        else:
            consec_l += 1; consec_w = 0
            if consec_l >= 2: size = max(min_size, size*0.7)
    return out

sized = apply_streak(baseline_trades)
arr_sized = np.array([t['pnl_sized'] for t in sized])
sizes = np.array([t['size'] for t in sized])
stats([{'pnl': p, 'session':'all'} for p in arr_sized], "WITH streak sizing")
print(f"  Avg size: {sizes.mean():.2f}, range {sizes.min():.2f}-{sizes.max():.2f}")
# Fair comparison: what if we used Fixed at avg size
arr_fair = baseline['arr'] if 'arr' in baseline else np.array([t['pnl'] for t in baseline_trades])
fair_net = arr_fair.sum() * sizes.mean()
print(f"  Fair compare (Fixed at {sizes.mean():.2f}× avg): ${fair_net:.2f}")
streak_net = arr_sized.sum()
print(f"  Delta vs fair: ${streak_net - fair_net:+.2f}")
keep_streak = streak_net > fair_net * 1.05
print(f"  >>> {'PASS' if keep_streak else 'FAIL'}")

# OPT 2: Multi-OB confluence
print()
print("="*120)
print("OPT 2: Multi-OB Confluence")
print("="*120)
t = s2_ob(h4, SESSION_PARAMS, multi_ob=True)
s = stats(t, "+ Multi-OB confluence")
keep_multi = s and s['pf'] >= baseline['pf'] + 0.10
print(f"  >>> {'PASS' if keep_multi else 'FAIL'}")

# OPT 3: FVG filter combo
print()
print("="*120)
print("OPT 3: FVG filter combo")
print("="*120)
t = s2_ob(h4, SESSION_PARAMS, fvg_filter=True)
s = stats(t, "+ FVG filter combo")
keep_fvg = s and s['pf'] >= baseline['pf'] + 0.10
print(f"  >>> {'PASS' if keep_fvg else 'FAIL'}")

# OPT 5: Donchian D1 macro layer
print()
print("="*120)
print("OPT 5: Donchian D1 Macro Layer (only OB trades when D1 trend up)")
print("="*120)
t = s2_ob(h4, SESSION_PARAMS, donchian_filter=True)
s = stats(t, "+ Donchian D1 macro filter")
keep_donchian = s and s['pf'] >= baseline['pf'] + 0.10
print(f"  >>> {'PASS' if keep_donchian else 'FAIL'}")

# Combinació final acceptades
print()
print("="*120)
print("FINAL combinant filters PASS:")
print("="*120)
final_trades = s2_ob(h4, SESSION_PARAMS,
                     multi_ob=keep_multi,
                     fvg_filter=keep_fvg,
                     donchian_filter=keep_donchian)
print(f"Filters applied: multi_ob={keep_multi}, fvg={keep_fvg}, donchian={keep_donchian}")
final = stats(final_trades, "FINAL with passing filters")

# Per any final
if final_trades:
    tdf = pd.DataFrame(final_trades)
    tdf['year'] = pd.to_datetime(tdf['ts']).dt.year
    print("\nPer any:")
    for yr in sorted(tdf['year'].unique()):
        ydf = tdf[tdf['year']==yr]
        arr = ydf['pnl'].values
        n=len(arr); w=(arr>0).sum(); net=arr.sum()
        pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
        pf=pf_p/pf_l if pf_l else 0
        print(f"  {yr}: n={n:>3} | WR {w/n*100 if n else 0:>5.1f}% | Net ${net:+.0f} | PF {pf:.2f}")

# Walk-forward
mid = len(final_trades)//2
print("\nWalk-forward 50/50:")
stats(final_trades[:mid], "IS 50%")
stats(final_trades[mid:], "OOS 50%")
