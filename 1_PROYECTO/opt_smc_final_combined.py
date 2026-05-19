"""
Combinació FINAL: per-session params + filters validats anteriorment.
Aplicar T3 (skip Thursday), T4 (trail BE), T6 (skip top 5% ATR).
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
h4['atr_pct'] = h4['atr'].rolling(200).rank(pct=True)
print(f"H4: {len(h4)} bars", flush=True)

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

def s2_ob(df_, session_params, skip_days=None, trail_be=False, atr_pct_max=None):
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
                    pos['sl'] = max(pos['sl'], pos['e'] + 0.05)
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
                    'expiry':i+30,'atr0':b0['atr']})
        pending_obs = [o for o in pending_obs if o['expiry'] > i]
        if pos is None and pd.notna(bar['ema50']) and bar['close'] > bar['ema50']:
            if skip_days is not None and ts.weekday() in skip_days: continue
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

def stats(trades, name):
    if not trades: print(f"{name:>50}: 0"); return None
    arr = np.array([t['pnl'] for t in trades])
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    print(f"{name:>50}: n={n:>3} | WR {w/n*100:>5.1f}% | Net ${net:+.0f} | PF {pf:.2f} | DD ${dd:.0f}", flush=True)
    return {'n':n,'pf':pf,'net':net,'dd':dd,'arr':arr}

print()
print("="*120)
print("STARTING POINT: ALL 3 sessions optimized (sense filtres extra)")
print("="*120)
baseline = stats(s2_ob(h4, SESSION_PARAMS), "Per-session params SOLS")

# Apply filters one by one, accept if better
print()
print("="*120)
print("AFEGINT FILTRES UN A UN:")
print("="*120)

# Filter 1: skip Thursday
print("\nFilter 1: Skip Thursday")
t = s2_ob(h4, SESSION_PARAMS, skip_days=[3])
s1 = stats(t, "+ skip Thursday")
delta = s1['pf'] - baseline['pf'] if s1 else 0
keep_thu = delta >= 0.10
print(f"  Delta PF {delta:+.2f} -> {'KEEP' if keep_thu else 'DISCARD'}")

# Filter 2: trail BE
print("\nFilter 2: Trail BE")
t = s2_ob(h4, SESSION_PARAMS, skip_days=[3] if keep_thu else None, trail_be=True)
s2 = stats(t, "+ trail BE")
delta = s2['pf'] - (s1['pf'] if keep_thu else baseline['pf'])
keep_be = delta >= 0.10
print(f"  Delta PF {delta:+.2f} -> {'KEEP' if keep_be else 'DISCARD'}")

# Filter 3: skip top 5% ATR
print("\nFilter 3: Skip top 5% ATR")
t = s2_ob(h4, SESSION_PARAMS,
          skip_days=[3] if keep_thu else None,
          trail_be=keep_be,
          atr_pct_max=0.95)
s3 = stats(t, "+ skip top 5% ATR")
prev = s2 if keep_be else (s1 if keep_thu else baseline)
delta = s3['pf'] - prev['pf']
keep_atr = delta >= 0.10
print(f"  Delta PF {delta:+.2f} -> {'KEEP' if keep_atr else 'DISCARD'}")

# Final
final_trades = s2_ob(h4, SESSION_PARAMS,
                     skip_days=[3] if keep_thu else None,
                     trail_be=keep_be,
                     atr_pct_max=0.95 if keep_atr else None)
print()
print("="*120)
print("FINAL CONFIG:")
print("="*120)
print(f"Session params: {SESSION_PARAMS}")
print(f"Skip Thursday: {keep_thu}")
print(f"Trail BE: {keep_be}")
print(f"Skip top 5% ATR: {keep_atr}")
final = stats(final_trades, "FINAL OPTIMIZED")

# Per any final
print()
print("Per any:")
tdf = pd.DataFrame(final_trades)
tdf['year'] = pd.to_datetime(tdf['ts']).dt.year
for yr in sorted(tdf['year'].unique()):
    ydf = tdf[tdf['year']==yr]
    arr = ydf['pnl'].values
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    print(f"  {yr}: n={n:>3} | WR {w/n*100 if n else 0:>5.1f}% | Net ${net:+.0f} | PF {pf:.2f}", flush=True)

# Walk-forward 60/40
print()
print("Walk-forward 60/40:")
mid60 = int(len(final_trades)*0.6)
stats(final_trades[:mid60], "IS 60% (training)")
stats(final_trades[mid60:], "OOS 40% (validation)")

# Walk-forward 50/50
print()
print("Walk-forward 50/50:")
mid50 = len(final_trades)//2
stats(final_trades[:mid50], "IS 50%")
stats(final_trades[mid50:], "OOS 50%")

# Per session
print()
print("Per session breakdown del FINAL:")
for sess in ['ASIA', 'LONDON', 'NY']:
    sess_trades = [t for t in final_trades if t['session']==sess]
    if sess_trades:
        arr = np.array([t['pnl'] for t in sess_trades])
        n=len(arr); w=(arr>0).sum(); net=arr.sum()
        pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
        pf=pf_p/pf_l if pf_l else 0
        print(f"  {sess}: n={n:>3} | WR {w/n*100:>5.1f}% | Net ${net:+.0f} | PF {pf:.2f}", flush=True)
