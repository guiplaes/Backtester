"""Robustness check on H4 Order Block winner."""
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
h4['ema200'] = h4['close'].ewm(span=200, adjust=False).mean()
hl = h4['high']-h4['low']; hc = (h4['high']-h4['close'].shift()).abs(); lc = (h4['low']-h4['close'].shift()).abs()
tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
h4['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
print(f"H4: {len(h4)} bars from {h4.index[0]} to {h4.index[-1]}", flush=True)

# Swap cost simulation (H4 OB trades duren ~10-20 H4 = 2-3 dies, swap minim)
SWAP_PER_NIGHT = -0.10

def calc_swap(entry_ts, exit_ts):
    entry_d = pd.Timestamp(entry_ts).normalize()
    exit_d = pd.Timestamp(exit_ts).normalize()
    if exit_d <= entry_d: return 0
    total = 0
    cur = entry_d + pd.Timedelta(days=1)
    while cur <= exit_d:
        wd = cur.weekday()
        if wd in (5,6): cur += pd.Timedelta(days=1); continue
        mult = 3 if wd == 2 else 1
        total += SWAP_PER_NIGHT * mult
        cur += pd.Timedelta(days=1)
    return total

def s2_ob(df_, sl_atr=1.5, tp1_atr=3, tp2_atr=6, ob_strength=1.5, max_lookback=30, ema_filter=True):
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
                pnl_raw = pos.get('pnl1',0)+pos.get('pnl2',0)-COMMISSION*2-SPREAD
                swap = calc_swap(pos['ts'], ts)
                tp = pnl_raw + swap
                trades.append({'ts': pos['ts'], 'exit_ts': ts, 'pnl_raw': pnl_raw, 'swap': swap, 'pnl': tp,
                              'duration_days': (pd.Timestamp(ts)-pd.Timestamp(pos['ts'])).days})
                pos = None
        b0 = df_.iloc[i-1]
        is_red = b0['close'] < b0['open']
        if is_red and not pd.isna(b0['atr']):
            future_high = max(df_.iloc[i]['high'], df_.iloc[i+1]['high'], df_.iloc[i+2]['high'])
            move = future_high - b0['close']
            if move > ob_strength * b0['atr']:
                pending_obs.append({'ob_low':b0['low'],'ob_high':b0['high'],
                    'expiry':i+max_lookback,'atr0':b0['atr']})
        pending_obs = [o for o in pending_obs if o['expiry'] > i]
        if pos is None:
            ema_ok = True if not ema_filter else (pd.notna(bar['ema50']) and bar['close'] > bar['ema50'])
            if ema_ok:
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
    arr_raw = np.array([t['pnl_raw'] for t in trades])
    swaps = np.array([t['swap'] for t in trades])
    durs = np.array([t['duration_days'] for t in trades])
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    print(f"{name:>50}: n={n:>3} | WR {w/n*100:>5.1f}% | dur {durs.mean():.1f}d | Raw ${arr_raw.sum():+.0f} | Swap ${swaps.sum():+.0f} | Net ${net:+.0f} | PF {pf:.2f} | DD ${dd:.0f}", flush=True)
    return {'pf':pf,'net':net}

print()
print("="*140)
print("H4 ORDER BLOCK — Test robustesa amb SWAP REAL:")
print("="*140)

# Sensitivity to ob_strength
print("\n>>> ob_strength variants (default 1.5):")
for s in [1.0, 1.5, 2.0, 2.5, 3.0]:
    trades = s2_ob(h4, ob_strength=s)
    stats(trades, f"OB strength {s}")

# Sensitivity to SL/TP
print("\n>>> SL/TP variants (default 1.5/3/6):")
for sl, tp1, tp2 in [(1.0, 3, 6), (1.5, 3, 6), (2.0, 3, 6),
                      (1.5, 5, 10), (2.0, 5, 10),
                      (1.5, 4, 8), (1.5, 2, 4)]:
    trades = s2_ob(h4, sl_atr=sl, tp1_atr=tp1, tp2_atr=tp2)
    stats(trades, f"SL{sl} TP{tp1}/{tp2}")

# Sensitivity to lookback
print("\n>>> lookback variants (default 30):")
for lb in [10, 20, 30, 50, 100]:
    trades = s2_ob(h4, max_lookback=lb)
    stats(trades, f"lookback {lb}")

# Without EMA filter
print("\n>>> EMA filter ON/OFF:")
trades = s2_ob(h4, ema_filter=True); stats(trades, "WITH EMA50 filter (uptrend)")
trades = s2_ob(h4, ema_filter=False); stats(trades, "NO EMA filter")

# Per any
print("\n>>> PER ANY (default config):")
trades = s2_ob(h4)
tdf = pd.DataFrame(trades)
if len(tdf):
    tdf['year'] = pd.to_datetime(tdf['ts']).dt.year
    for yr in sorted(tdf['year'].unique()):
        ydf = tdf[tdf['year']==yr]
        arr = ydf['pnl'].values
        n=len(arr); w=(arr>0).sum(); net=arr.sum()
        pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
        pf=pf_p/pf_l if pf_l else 0
        avg_dur = ydf['duration_days'].mean()
        print(f"  {yr}: n={n:>3} | WR {w/n*100 if n else 0:>5.1f}% | dur {avg_dur:.1f}d | Net ${net:+.0f} | PF {pf:.2f}", flush=True)

# Walk-forward 50/50 and 60/40
print("\n>>> WALK-FORWARD VALIDATION:")
trades = s2_ob(h4)
mid = len(trades)//2
mid60 = int(len(trades)*0.6)
print()
stats(trades[:mid], "IS 50% (training)")
stats(trades[mid:], "OOS 50% (validation)")
print()
stats(trades[:mid60], "IS 60% (training)")
stats(trades[mid60:], "OOS 40% (validation)")
