"""Detall per sessió pura — quina aporta valor real."""
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
print(f"H4: {len(h4)} bars", flush=True)

def s2_ob(df_, hour_filter=None, sl_atr=1.5, tp1_atr=3, tp2_atr=6, ob_strength=1.8):
    """hour_filter: function ts -> bool"""
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
                trades.append({'ts': pos['ts'], 'pnl': tp, 'hour': pos['ts'].hour})
                pos = None
        b0 = df_.iloc[i-1]
        is_red = b0['close'] < b0['open']
        if is_red and not pd.isna(b0['atr']):
            future_high = max(df_.iloc[i]['high'], df_.iloc[i+1]['high'], df_.iloc[i+2]['high'])
            move = future_high - b0['close']
            if move > ob_strength * b0['atr']:
                pending_obs.append({'ob_low':b0['low'],'ob_high':b0['high'],
                    'expiry':i+30,'atr0':b0['atr']})
        pending_obs = [o for o in pending_obs if o['expiry'] > i]
        if pos is None and pd.notna(bar['ema50']) and bar['close'] > bar['ema50']:
            if hour_filter is not None and not hour_filter(ts): continue
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
    if not trades: print(f"{name:>40}: 0 trades"); return None
    arr = np.array([t['pnl'] for t in trades])
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    avg = arr.mean()
    print(f"{name:>40}: n={n:>3} | WR {w/n*100:>5.1f}% | Net ${net:+.0f} | Avg ${avg:+.2f} | PF {pf:.2f} | DD ${dd:.0f}", flush=True)
    return {'n':n,'pf':pf,'net':net,'avg':avg}

print()
print("="*120)
print("SESSIONS PURES (ob_strength=1.8, sense altres filters):")
print("="*120)

sessions = [
    ("All 24h", lambda ts: True),
    ("ASIA pure (00-07)", lambda ts: 0 <= ts.hour < 7),
    ("LONDON pure (07-13)", lambda ts: 7 <= ts.hour < 13),
    ("OVERLAP London+NY (13-16)", lambda ts: 13 <= ts.hour < 16),
    ("NY pure (16-21)", lambda ts: 16 <= ts.hour < 21),
    ("DEAD zone (21-24)", lambda ts: 21 <= ts.hour < 24),
    ("---", None),
    ("NY+OVERLAP (13-21)", lambda ts: 13 <= ts.hour < 21),
    ("LONDON+OVERLAP (7-16)", lambda ts: 7 <= ts.hour < 16),
    ("ASIA+LONDON (0-13)", lambda ts: 0 <= ts.hour < 13),
    ("OVERLAP+NY only (13-21)", lambda ts: 13 <= ts.hour < 21),
    ("ASIA+OVERLAP+NY (skip Lon)", lambda ts: ts.hour < 7 or ts.hour >= 13),
    ("Excloure DEAD (0-21)", lambda ts: ts.hour < 21),
]

results = {}
for label, fn in sessions:
    if fn is None:
        print()
        continue
    trades = s2_ob(h4, hour_filter=fn)
    s = stats(trades, label)
    if s: results[label] = s

# Per hora (granular)
print()
print("="*120)
print("PER HORA UTC (granular):")
print("="*120)
trades_all = s2_ob(h4)
hours_dict = {}
for t in trades_all:
    h = t['hour']
    hours_dict.setdefault(h, []).append(t['pnl'])

print(f"{'Hour':>4} | {'Trades':>6} | {'Net':>9} | {'Avg':>7} | {'WR':>5} | {'PF':>5} | Session")
print("-"*80)
for h in range(24):
    if h not in hours_dict: continue
    arr = np.array(hours_dict[h])
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    if h < 7: sess = "ASIA"
    elif h < 13: sess = "LONDON"
    elif h < 16: sess = "OVERLAP"
    elif h < 21: sess = "NY"
    else: sess = "DEAD"
    sign = "✅" if net > 0 else "❌"
    print(f"{h:>4} | {n:>6} | ${net:>+8.2f} | ${arr.mean():+6.2f} | {w/n*100:>4.1f}% | {pf:>4.2f} | {sess} {sign}")

# Top hours combined
print()
print("="*120)
print("Top 5 hores positives combinades:")
print("="*120)
hour_pf = {}
for h, pnls in hours_dict.items():
    arr = np.array(pnls)
    if len(arr) < 5: continue
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    hour_pf[h] = pf

top_hours = sorted(hour_pf.items(), key=lambda x: -x[1])[:5]
print(f"Top 5 hours: {[h for h,_ in top_hours]}")
top_h_set = set(h for h,_ in top_hours)
trades = s2_ob(h4, hour_filter=lambda ts: ts.hour in top_h_set)
stats(trades, "Top 5 best hours combined")
