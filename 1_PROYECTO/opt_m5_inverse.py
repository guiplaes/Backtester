"""LONGS amb params SHORT i SHORTS amb params LONG — robustness check."""
import pandas as pd
import numpy as np

CSV = "xauusd_m5_5y.csv"
REAL_COST = 0.40

m5 = pd.read_csv(CSV, index_col=0, parse_dates=True)
m5.index = pd.to_datetime(m5.index, utc=True)
m5.columns = [c.lower() for c in m5.columns]
m5['ema50'] = m5['close'].ewm(span=50, adjust=False).mean()
hl = m5['high']-m5['low']; hc = (m5['high']-m5['close'].shift()).abs(); lc = (m5['low']-m5['close'].shift()).abs()
tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
m5['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()

O = m5['open'].values; H = m5['high'].values; L = m5['low'].values; C = m5['close'].values
EMA = m5['ema50'].values; ATR = m5['atr'].values
HOURS = m5.index.hour.values
n = len(C)

def get_session(h):
    if h < 7: return 'ASIA'
    elif h < 13: return 'LONDON'
    elif h < 21: return 'NY' if h >= 16 else 'OVERLAP'
    else: return 'DEAD'

SESSION_ARR = np.array([get_session(h) for h in HOURS])

# Optimitzats per LONGS
LONG_PARAMS = {
    'ASIA': (1.0, 3, 6, 2.5),
    'LONDON': (1.0, 3, 6, 2.5),
    'NY': (1.0, 5, 10, 2.5),
}

# Optimitzats per SHORTS
SHORT_PARAMS = {
    'ASIA': (2.0, 4, 8, 2.5),
    'LONDON': (1.5, 3, 6, 2.5),
    'NY': (1.0, 2, 4, 2.0),
}

def bt_long(sess_target, sl_atr, tp1_atr, tp2_atr, ob_str):
    trades_pnl = []
    pos = None
    pending_lows = []; pending_highs = []; pending_atrs = []; pending_expiry = []
    for i in range(50, n-3):
        if pos is not None:
            sl_h = L[i] <= pos[0]
            tp1_h = H[i] >= pos[1]
            tp2_h = H[i] >= pos[2]
            if sl_h and (tp1_h or tp2_h):
                if (O[i]-pos[0]) < (pos[1]-O[i]): tp1_h=False; tp2_h=False
            pnl1 = pos[3]; pnl2 = pos[4]; q1 = pos[5]; q2 = pos[6]; e = pos[7]
            if tp1_h and q1>0: pnl1 = (pos[1]-e)*0.5; q1=0
            if tp2_h and q2>0: pnl2 = (pos[2]-e)*0.5; q2=0
            if sl_h:
                if q1>0: pnl1 = (pos[0]-e)*0.5; q1=0
                if q2>0: pnl2 = (pos[0]-e)*0.5; q2=0
            if q1==0 and q2==0:
                trades_pnl.append(pnl1+pnl2 - REAL_COST)
                pos = None
            else:
                pos = (pos[0], pos[1], pos[2], pnl1, pnl2, q1, q2, e)
        b0_close = C[i-1]; b0_open = O[i-1]; b0_atr = ATR[i-1]
        if b0_close < b0_open and not np.isnan(b0_atr):
            future_high = max(H[i], H[i+1], H[i+2])
            move = future_high - b0_close
            if move > ob_str * b0_atr:
                pending_lows.append(L[i-1]); pending_highs.append(H[i-1])
                pending_atrs.append(b0_atr); pending_expiry.append(i+30)
        keep = [j for j, exp in enumerate(pending_expiry) if exp > i]
        if len(keep) < len(pending_expiry):
            pending_lows = [pending_lows[j] for j in keep]; pending_highs = [pending_highs[j] for j in keep]
            pending_atrs = [pending_atrs[j] for j in keep]; pending_expiry = [pending_expiry[j] for j in keep]
        if pos is None and not np.isnan(EMA[i]) and C[i] > EMA[i]:
            if SESSION_ARR[i] != sess_target: continue
            for j in range(len(pending_lows)):
                if L[i] <= pending_highs[j] and C[i] > pending_lows[j]:
                    if C[i] > O[i]:
                        atr = pending_atrs[j]; e = C[i]
                        pos = (pending_lows[j]-atr*sl_atr*0.5, e+atr*tp1_atr, e+atr*tp2_atr, 0, 0, 0.5, 0.5, e)
                        pending_lows.pop(j); pending_highs.pop(j); pending_atrs.pop(j); pending_expiry.pop(j)
                        break
    return trades_pnl

def bt_short(sess_target, sl_atr, tp1_atr, tp2_atr, ob_str):
    trades_pnl = []
    pos = None
    pending_lows = []; pending_highs = []; pending_atrs = []; pending_expiry = []
    for i in range(50, n-3):
        if pos is not None:
            sl_h = H[i] >= pos[0]
            tp1_h = L[i] <= pos[1]
            tp2_h = L[i] <= pos[2]
            if sl_h and (tp1_h or tp2_h):
                if (pos[0]-O[i]) < (O[i]-pos[1]): tp1_h=False; tp2_h=False
            pnl1 = pos[3]; pnl2 = pos[4]; q1 = pos[5]; q2 = pos[6]; e = pos[7]
            if tp1_h and q1>0: pnl1 = (e-pos[1])*0.5; q1=0
            if tp2_h and q2>0: pnl2 = (e-pos[2])*0.5; q2=0
            if sl_h:
                if q1>0: pnl1 = (e-pos[0])*0.5; q1=0
                if q2>0: pnl2 = (e-pos[0])*0.5; q2=0
            if q1==0 and q2==0:
                trades_pnl.append(pnl1+pnl2 - REAL_COST)
                pos = None
            else:
                pos = (pos[0], pos[1], pos[2], pnl1, pnl2, q1, q2, e)
        b0_close = C[i-1]; b0_open = O[i-1]; b0_atr = ATR[i-1]
        if b0_close > b0_open and not np.isnan(b0_atr):
            future_low = min(L[i], L[i+1], L[i+2])
            move_dn = b0_close - future_low
            if move_dn > ob_str * b0_atr:
                pending_lows.append(L[i-1]); pending_highs.append(H[i-1])
                pending_atrs.append(b0_atr); pending_expiry.append(i+30)
        keep = [j for j, exp in enumerate(pending_expiry) if exp > i]
        if len(keep) < len(pending_expiry):
            pending_lows = [pending_lows[j] for j in keep]; pending_highs = [pending_highs[j] for j in keep]
            pending_atrs = [pending_atrs[j] for j in keep]; pending_expiry = [pending_expiry[j] for j in keep]
        if pos is None and not np.isnan(EMA[i]) and C[i] < EMA[i]:
            if SESSION_ARR[i] != sess_target: continue
            for j in range(len(pending_lows)):
                if H[i] >= pending_lows[j] and C[i] < pending_highs[j]:
                    if C[i] < O[i]:
                        atr = pending_atrs[j]; e = C[i]
                        pos = (pending_highs[j]+atr*sl_atr*0.5, e-atr*tp1_atr, e-atr*tp2_atr, 0, 0, 0.5, 0.5, e)
                        pending_lows.pop(j); pending_highs.pop(j); pending_atrs.pop(j); pending_expiry.pop(j)
                        break
    return trades_pnl

def stats(arr):
    if len(arr)==0: return None
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    return {'n':n,'wr':w/n*100,'net':net,'pf':pf,'dd':dd}

def run(direction, params, label):
    all_trades = []
    for sess in ['ASIA', 'LONDON', 'NY']:
        sl, tp1, tp2, ob = params[sess]
        if direction == 'long':
            trades = bt_long(sess, sl, tp1, tp2, ob)
        else:
            trades = bt_short(sess, sl, tp1, tp2, ob)
        all_trades.extend(trades)
    s = stats(np.array(all_trades))
    if s:
        net5 = s['net']*5; dd5 = s['dd']*5
        print(f"{label}: n={s['n']:>4} | WR {s['wr']:.1f}% | Net5y@0.05=${net5:+.0f} ({net5/50000*100:+.1f}%/any) | PF {s['pf']:.2f} | DD@0.05=${dd5:.0f} ({dd5/10000*100:.1f}%)", flush=True)
    return all_trades, s

print()
print("="*120)
print("CROSS-VALIDATION — provant params 'creuats':")
print("="*120)

print()
print("Test 1: LONGS amb params LONG (referencia, ja optimitzats)")
t_LL, s_LL = run('long', LONG_PARAMS, "LONGS+L")

print()
print("Test 2: SHORTS amb params SHORT (referencia, ja optimitzats)")
t_SS, s_SS = run('short', SHORT_PARAMS, "SHORTS+S")

print()
print("Test 3: SHORTS amb params LONG (mateixos que longs)")
t_SL, s_SL = run('short', LONG_PARAMS, "SHORTS+L")

print()
print("Test 4: LONGS amb params SHORT (provem la inversa)")
t_LS, s_LS = run('long', SHORT_PARAMS, "LONGS+S")

print()
print("="*120)
print("RESUM CROSS-VALIDATION:")
print("="*120)
print(f"|  Direction  |  Params      |  Net 5y    |  %/any   |  PF   |  DD%   |")
print(f"|-------------|--------------|------------|----------|-------|--------|")
def row(name, params_label, s):
    if s:
        print(f"|  {name:>9}  |  {params_label:>12}  |  ${s['net']*5:+8.0f}  |  {s['net']*5/50000*100:+5.1f}%  |  {s['pf']:.2f} |  {s['dd']*5/10000*100:5.1f}%  |")
row("LONGS", "LONG params", s_LL)
row("LONGS", "SHORT params", s_LS)
row("SHORTS", "LONG params", s_SL)
row("SHORTS", "SHORT params", s_SS)

print()
print("INTERPRETACIO:")
if s_LL and s_LS:
    deg_long = (s_LL['net'] - s_LS['net']) / s_LL['net'] * 100
    print(f"  LONGS amb params SHORT: degradacio {deg_long:.0f}% del rendiment LONG")
if s_SS and s_SL:
    deg_short = (s_SS['net'] - s_SL['net']) / s_SS['net'] * 100
    print(f"  SHORTS amb params LONG: degradacio {deg_short:.0f}% del rendiment SHORT")
print()
print("Si la degradacio es < 30%, els params no son massa overfit.")
print("Si > 50%, hi ha overfit greu.")
