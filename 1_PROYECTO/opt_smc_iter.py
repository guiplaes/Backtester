"""
Optimització iterativa SMC OB H4.
Baseline: ob_strength=1.5, SL 1.5/TP 3/6, EMA50 filter, +$2,346 PF 2.04.
Cada test es fa SOL (sobre baseline) per aïllar l'efecte.
"""
import pandas as pd
import numpy as np
import sys

CSV = "xauusd_m5_5y.csv"
COMMISSION = 0.5; SPREAD = 0.50; SLIPPAGE = 0.20
SWAP_PER_NIGHT = -0.10

m5 = pd.read_csv(CSV, index_col=0, parse_dates=True)
m5.index = pd.to_datetime(m5.index, utc=True)
m5.columns = [c.lower() for c in m5.columns]
if 'tick_volume' not in m5.columns: m5['tick_volume'] = m5.get('volume', 1)

h4 = m5.resample('4h').agg(open=('open','first'),high=('high','max'),
    low=('low','min'),close=('close','last'),tick_volume=('tick_volume','sum')).dropna()
h4['ema20'] = h4['close'].ewm(span=20, adjust=False).mean()
h4['ema50'] = h4['close'].ewm(span=50, adjust=False).mean()
h4['ema200'] = h4['close'].ewm(span=200, adjust=False).mean()
h4['ema50_slope'] = (h4['ema50'] - h4['ema50'].shift(20)) / h4['ema50'].shift(20) * 100
hl = h4['high']-h4['low']; hc = (h4['high']-h4['close'].shift()).abs(); lc = (h4['low']-h4['close'].shift()).abs()
tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
h4['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
h4['atr_pct'] = h4['atr'].rolling(200).rank(pct=True)
h4['vol_avg'] = h4['tick_volume'].rolling(20).mean()
print(f"H4: {len(h4)} bars", flush=True)

def calc_swap(entry_ts, exit_ts):
    e = pd.Timestamp(entry_ts).normalize(); x = pd.Timestamp(exit_ts).normalize()
    if x <= e: return 0
    total = 0; cur = e + pd.Timedelta(days=1)
    while cur <= x:
        wd = cur.weekday()
        if wd in (5,6): cur += pd.Timedelta(days=1); continue
        mult = 3 if wd == 2 else 1
        total += SWAP_PER_NIGHT * mult
        cur += pd.Timedelta(days=1)
    return total

def s2_ob(df_, sl_atr=1.5, tp1_atr=3, tp2_atr=6, ob_strength=1.5, max_lookback=30,
          ema_filter=True, sessions=None, skip_days=None, trail_be=False,
          ob_vol_min=None, ema200_max_dist=None, atr_pct_max=None,
          slope_min=None):
    """
    sessions: tuple of allowed UTC hours (e.g., (8, 20))
    skip_days: list of weekday ints (0=Mon)
    trail_be: move SL to entry after TP1 hit
    ob_vol_min: minimum volume ratio of OB candle
    ema200_max_dist: max distance to EMA200 in ATR (for OB validity)
    atr_pct_max: skip if ATR percentile > this
    slope_min: minimum EMA50 slope %
    """
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
            if tp1_h and pos['q1']>0:
                pos['pnl1'] = (pos['tp1']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q1']=0
                if trail_be:
                    pos['sl'] = max(pos['sl'], pos['e'] + 0.05)  # move to BE+
            if tp2_h and pos['q2']>0: pos['pnl2'] = (pos['tp2']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q2']=0
            if sl_h:
                if pos['q1']>0: pos['pnl1'] = (pos['sl']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q1']=0
                if pos['q2']>0: pos['pnl2'] = (pos['sl']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q2']=0
            if pos['q1']==0 and pos['q2']==0:
                pnl_raw = pos.get('pnl1',0)+pos.get('pnl2',0)-COMMISSION*2-SPREAD
                swap = calc_swap(pos['ts'], ts)
                trades.append({'ts': pos['ts'], 'exit_ts': ts, 'pnl': pnl_raw + swap,
                              'pnl_raw': pnl_raw, 'swap': swap})
                pos = None
        b0 = df_.iloc[i-1]
        is_red = b0['close'] < b0['open']
        if is_red and not pd.isna(b0['atr']):
            future_high = max(df_.iloc[i]['high'], df_.iloc[i+1]['high'], df_.iloc[i+2]['high'])
            move = future_high - b0['close']
            if move > ob_strength * b0['atr']:
                # Optional: vol filter on OB candle
                if ob_vol_min is not None and not pd.isna(b0.get('vol_avg', np.nan)):
                    if b0['tick_volume'] < b0['vol_avg'] * ob_vol_min: continue
                # Optional: EMA200 distance filter
                if ema200_max_dist is not None and not pd.isna(b0.get('ema200', np.nan)):
                    dist = abs(b0['close'] - b0['ema200']) / b0['atr'] if b0['atr'] else 999
                    if dist > ema200_max_dist: continue
                pending_obs.append({'ob_low':b0['low'],'ob_high':b0['high'],
                    'expiry':i+max_lookback,'atr0':b0['atr']})
        pending_obs = [o for o in pending_obs if o['expiry'] > i]
        if pos is None:
            ema_ok = True if not ema_filter else (pd.notna(bar['ema50']) and bar['close'] > bar['ema50'])
            sess_ok = True if sessions is None else (sessions[0] <= ts.hour < sessions[1])
            day_ok = True if skip_days is None else (ts.weekday() not in skip_days)
            atr_ok = True if atr_pct_max is None else (pd.isna(bar.get('atr_pct')) or bar['atr_pct'] <= atr_pct_max)
            slope_ok = True if slope_min is None else (pd.notna(bar.get('ema50_slope')) and bar['ema50_slope'] >= slope_min)
            if ema_ok and sess_ok and day_ok and atr_ok and slope_ok:
                for ob in list(pending_obs):
                    if bar['low'] <= ob['ob_high'] and bar['close'] > ob['ob_low']:
                        if bar['close'] > bar['open']:
                            atr = ob['atr0']; e = bar['close']
                            pos = {'side':'L','e':e,'ts':ts,
                                'sl':ob['ob_low']-atr*sl_atr*0.5,
                                'tp1':e+atr*tp1_atr,'tp2':e+atr*tp2_atr,'q1':0.5,'q2':0.5}
                            pending_obs.remove(ob); break
    return trades

def stats(trades, name):
    if not trades: print(f"{name:>50}: NO trades"); return None
    arr = np.array([t['pnl'] for t in trades])
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    print(f"{name:>50}: n={n:>3} | WR {w/n*100:>5.1f}% | Net ${net:+.0f} | PF {pf:.2f} | DD ${dd:.0f}", flush=True)
    return {'n':n,'pf':pf,'net':net,'dd':dd}

# ============================================================
# BASELINE
# ============================================================
print()
print("="*120)
print("BASELINE (default config):")
print("="*120)
baseline_trades = s2_ob(h4)
baseline = stats(baseline_trades, "BASELINE: ob1.5 SL1.5 TP3/6 +EMA")

print()
print("="*120)
print("TEST 1: Fine-tune ob_strength")
print("="*120)
best_obs = None; best_obs_pf = baseline['pf']
for ob in [1.5, 1.8, 2.0, 2.2, 2.5]:
    t = s2_ob(h4, ob_strength=ob)
    s = stats(t, f"ob_strength={ob}")
    if s and s['pf'] > best_obs_pf and s['n'] > 50:
        best_obs_pf = s['pf']; best_obs = ob

if best_obs:
    delta = best_obs_pf - baseline['pf']
    if delta >= 0.10:
        print(f">>> T1 PASS: ob_strength={best_obs} (PF {best_obs_pf:.2f}, delta {delta:+.2f})")
        baseline_obs = best_obs
    else:
        print(f">>> T1 FAIL: best ob_strength gave only +{delta:.2f} PF")
        baseline_obs = 1.5
else:
    baseline_obs = 1.5
    print(f">>> T1 FAIL: no improvement")

print()
print("="*120)
print("TEST 2: Session filter (UTC hours)")
print("="*120)
best_sess = None; best_sess_pf = baseline['pf']
for sess, label in [((0,24), "All hours (baseline)"), ((7,21), "London+NY"),
                     ((13,21), "NY only"), ((7,15), "London only"),
                     ((0,12), "Asia+early London"), ((8,16), "London core")]:
    t = s2_ob(h4, ob_strength=baseline_obs, sessions=sess)
    s = stats(t, label)
    if s and s['pf'] > best_sess_pf and s['n'] > 50:
        best_sess_pf = s['pf']; best_sess = sess

if best_sess:
    delta = best_sess_pf - baseline['pf']
    if delta >= 0.10:
        print(f">>> T2 PASS: sessions={best_sess} (delta {delta:+.2f})")
        baseline_sess = best_sess
    else:
        print(f">>> T2 FAIL: best session +{delta:.2f}")
        baseline_sess = None
else:
    baseline_sess = None
    print(f">>> T2 FAIL")

print()
print("="*120)
print("TEST 3: Day-of-week filter")
print("="*120)
best_dow_pf = baseline['pf']; best_skip = None
for skip, label in [(None, "All days"), ([0], "Skip Mon"), ([1], "Skip Tue"),
                     ([2], "Skip Wed"), ([3], "Skip Thu"), ([4], "Skip Fri"),
                     ([0,4], "Skip Mon+Fri"), ([2,4], "Skip Wed+Fri")]:
    t = s2_ob(h4, ob_strength=baseline_obs, sessions=baseline_sess, skip_days=skip)
    s = stats(t, label)
    if s and s['pf'] > best_dow_pf and s['n'] > 50:
        best_dow_pf = s['pf']; best_skip = skip

if best_skip is not None:
    delta = best_dow_pf - baseline['pf']
    if delta >= 0.10:
        print(f">>> T3 PASS: skip_days={best_skip} (delta {delta:+.2f})")
        baseline_skip = best_skip
    else:
        print(f">>> T3 FAIL")
        baseline_skip = None
else:
    baseline_skip = None
    print(f">>> T3 FAIL")

print()
print("="*120)
print("TEST 4: Trail SL to BE after TP1")
print("="*120)
t = s2_ob(h4, ob_strength=baseline_obs, sessions=baseline_sess, skip_days=baseline_skip, trail_be=True)
s = stats(t, "WITH trail_be")
delta = (s['pf'] - baseline['pf']) if s else 0
if delta >= 0.10:
    print(f">>> T4 PASS: trail_be (delta {delta:+.2f})")
    baseline_trail = True
else:
    print(f">>> T4 FAIL: delta {delta:+.2f}")
    baseline_trail = False

print()
print("="*120)
print("TEST 5: Volume filter on OB candle")
print("="*120)
best_vol_pf = baseline['pf']; best_vol = None
for vol, label in [(None, "No vol filter"), (0.8, "Vol >= 0.8x avg"),
                    (1.0, "Vol >= 1.0x avg"), (1.3, "Vol >= 1.3x avg"),
                    (1.5, "Vol >= 1.5x avg")]:
    t = s2_ob(h4, ob_strength=baseline_obs, sessions=baseline_sess,
              skip_days=baseline_skip, trail_be=baseline_trail, ob_vol_min=vol)
    s = stats(t, label)
    if s and s['pf'] > best_vol_pf and s['n'] > 50:
        best_vol_pf = s['pf']; best_vol = vol

delta = best_vol_pf - baseline['pf']
if best_vol is not None and delta >= 0.10:
    print(f">>> T5 PASS: vol_min={best_vol} (delta {delta:+.2f})")
    baseline_vol = best_vol
else:
    print(f">>> T5 FAIL")
    baseline_vol = None

print()
print("="*120)
print("TEST 6: ATR regime filter (skip extreme volatility)")
print("="*120)
best_atr_pf = baseline['pf']; best_atr = None
for atr_max, label in [(None, "No ATR filter"), (0.95, "Skip top 5%"),
                        (0.85, "Skip top 15%"), (0.70, "Skip top 30%")]:
    t = s2_ob(h4, ob_strength=baseline_obs, sessions=baseline_sess,
              skip_days=baseline_skip, trail_be=baseline_trail, ob_vol_min=baseline_vol,
              atr_pct_max=atr_max)
    s = stats(t, label)
    if s and s['pf'] > best_atr_pf and s['n'] > 50:
        best_atr_pf = s['pf']; best_atr = atr_max

delta = best_atr_pf - baseline['pf']
if best_atr is not None and delta >= 0.10:
    print(f">>> T6 PASS: atr_max={best_atr} (delta {delta:+.2f})")
    baseline_atr = best_atr
else:
    print(f">>> T6 FAIL")
    baseline_atr = None

print()
print("="*120)
print("TEST 7: EMA50 slope filter (require positive trend slope)")
print("="*120)
best_slope_pf = baseline['pf']; best_slope = None
for sl_min, label in [(None, "No slope filter"), (0, "Slope > 0"),
                       (0.5, "Slope > 0.5%"), (1.0, "Slope > 1.0%"),
                       (2.0, "Slope > 2.0%")]:
    t = s2_ob(h4, ob_strength=baseline_obs, sessions=baseline_sess,
              skip_days=baseline_skip, trail_be=baseline_trail, ob_vol_min=baseline_vol,
              atr_pct_max=baseline_atr, slope_min=sl_min)
    s = stats(t, label)
    if s and s['pf'] > best_slope_pf and s['n'] > 50:
        best_slope_pf = s['pf']; best_slope = sl_min

delta = best_slope_pf - baseline['pf']
if best_slope is not None and delta >= 0.10:
    print(f">>> T7 PASS: slope_min={best_slope} (delta {delta:+.2f})")
    baseline_slope = best_slope
else:
    print(f">>> T7 FAIL")
    baseline_slope = None

# ============================================================
# FINAL: combine all PASS-ed
# ============================================================
print()
print("="*120)
print("FINAL CONFIG (totes les optimitzacions PASS-ed combinades):")
print("="*120)
print(f"ob_strength = {baseline_obs}", flush=True)
print(f"sessions = {baseline_sess}", flush=True)
print(f"skip_days = {baseline_skip}", flush=True)
print(f"trail_be = {baseline_trail}", flush=True)
print(f"ob_vol_min = {baseline_vol}", flush=True)
print(f"atr_pct_max = {baseline_atr}", flush=True)
print(f"slope_min = {baseline_slope}", flush=True)

t = s2_ob(h4, ob_strength=baseline_obs, sessions=baseline_sess,
          skip_days=baseline_skip, trail_be=baseline_trail, ob_vol_min=baseline_vol,
          atr_pct_max=baseline_atr, slope_min=baseline_slope)
final = stats(t, "FINAL OPTIMIZED CONFIG")

# Per any final
if t:
    tdf = pd.DataFrame(t)
    tdf['year'] = pd.to_datetime(tdf['ts']).dt.year
    print()
    print("Per any:")
    for yr in sorted(tdf['year'].unique()):
        ydf = tdf[tdf['year']==yr]
        arr = ydf['pnl'].values
        n=len(arr); w=(arr>0).sum(); net=arr.sum()
        pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
        pf=pf_p/pf_l if pf_l else 0
        print(f"  {yr}: n={n:>3} | WR {w/n*100 if n else 0:>5.1f}% | Net ${net:+.0f} | PF {pf:.2f}", flush=True)

# Walk-forward final
print()
print("Walk-forward 60/40 final:")
mid60 = int(len(t)*0.6)
stats(t[:mid60], "IS 60%")
stats(t[mid60:], "OOS 40%")
