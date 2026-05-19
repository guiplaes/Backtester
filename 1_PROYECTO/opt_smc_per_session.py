"""SMC OB amb paràmetres ADAPTATS per sessió."""
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

def get_session(h):
    if h < 7: return 'ASIA'
    elif h < 13: return 'LONDON'
    elif h < 16: return 'OVERLAP'
    elif h < 21: return 'NY'
    else: return 'DEAD'

def s2_ob_session_aware(df_, session_params):
    """
    session_params: dict like {'ASIA': (sl, tp1, tp2, ob_strength), ...}
    Set value to None to skip that session.
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
                    'expiry':i+30,'atr0':b0['atr']})
        pending_obs = [o for o in pending_obs if o['expiry'] > i]
        if pos is None and pd.notna(bar['ema50']) and bar['close'] > bar['ema50']:
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
    if not trades: print(f"{name:>40}: 0"); return None
    arr = np.array([t['pnl'] for t in trades])
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    print(f"{name:>40}: n={n:>3} | WR {w/n*100:>5.1f}% | Net ${net:+.0f} | PF {pf:.2f} | DD ${dd:.0f}", flush=True)
    return {'n':n,'pf':pf,'net':net,'dd':dd}

# Default per session
print()
print("="*120)
print("STEP 1: TROBAR PARÀMETRES ÒPTIMS PER CADA SESSIÓ INDEPENDENTMENT")
print("="*120)

results_per_session = {}
for sess_name in ['ASIA', 'LONDON', 'NY']:
    print(f"\n{sess_name} session optimization:")
    only_this = {s: None for s in ['ASIA', 'LONDON', 'OVERLAP', 'NY', 'DEAD']}
    best_pf = 0; best_params = None
    for sl in [1.0, 1.5, 2.0]:
        for tp_combo in [(2,4), (3,6), (4,8), (5,10)]:
            for ob_str in [1.5, 1.8, 2.0]:
                only_this[sess_name] = (sl, tp_combo[0], tp_combo[1], ob_str)
                trades = s2_ob_session_aware(h4, only_this)
                if trades and len(trades) >= 30:
                    arr = np.array([t['pnl'] for t in trades])
                    pf_p = arr[arr>0].sum(); pf_l = abs(arr[arr<=0].sum())
                    pf = pf_p/pf_l if pf_l else 0
                    if pf > best_pf:
                        best_pf = pf
                        best_params = (sl, tp_combo[0], tp_combo[1], ob_str)
                        best_n = len(trades); best_net = arr.sum()
    if best_params:
        print(f"  BEST {sess_name}: SL={best_params[0]} TP={best_params[1]}/{best_params[2]} OB={best_params[3]}")
        print(f"  -> n={best_n} Net=${best_net:.0f} PF={best_pf:.2f}")
        results_per_session[sess_name] = best_params
    else:
        print(f"  {sess_name}: no good config found")

# Combine best per session
print()
print("="*120)
print("STEP 2: COMBINAR ELS MILLORS DE CADA SESSIÓ:")
print("="*120)

combined_params = {
    'ASIA': results_per_session.get('ASIA'),
    'LONDON': results_per_session.get('LONDON'),
    'OVERLAP': None,
    'NY': results_per_session.get('NY'),
    'DEAD': None,
}

trades = s2_ob_session_aware(h4, combined_params)
combined_stats = stats(trades, "ALL 3 sessions optimized")

# Test only NY+ASIA (skip LONDON)
no_london = dict(combined_params)
no_london['LONDON'] = None
trades_no_lon = s2_ob_session_aware(h4, no_london)
stats(trades_no_lon, "ASIA+NY only (skip London)")

# Test only NY (best alone)
only_ny = {s: None for s in ['ASIA', 'LONDON', 'OVERLAP', 'NY', 'DEAD']}
only_ny['NY'] = results_per_session.get('NY')
trades_ny = s2_ob_session_aware(h4, only_ny)
stats(trades_ny, "NY only (optimized)")

# Per any del millor
print()
print("="*120)
print("PER ANY de la combinació guanyadora:")
print("="*120)
best_trades = trades  # all 3 sessions
if best_trades:
    tdf = pd.DataFrame(best_trades)
    tdf['year'] = pd.to_datetime(tdf['ts']).dt.year
    for yr in sorted(tdf['year'].unique()):
        ydf = tdf[tdf['year']==yr]
        arr = ydf['pnl'].values
        n=len(arr); w=(arr>0).sum(); net=arr.sum()
        pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
        pf=pf_p/pf_l if pf_l else 0
        print(f"  {yr}: n={n:>3} | WR {w/n*100 if n else 0:>5.1f}% | Net ${net:+.0f} | PF {pf:.2f}", flush=True)

# Per session breakdown
print()
print("Per session breakdown del millor:")
for sess in ['ASIA', 'LONDON', 'NY']:
    sess_trades = [t for t in best_trades if t['session']==sess]
    arr = np.array([t['pnl'] for t in sess_trades])
    if len(arr):
        n=len(arr); w=(arr>0).sum(); net=arr.sum()
        pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
        pf=pf_p/pf_l if pf_l else 0
        print(f"  {sess}: n={n:>3} | WR {w/n*100:>5.1f}% | Net ${net:+.0f} | PF {pf:.2f}", flush=True)
