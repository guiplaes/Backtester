"""SHORTS M5 amb MATEIXOS params que LONGS — més robust, menys overfit."""
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

# CRITERI COMÚ — params òptims dels LONGS
COMMON_PARAMS = {
    'ASIA': (1.0, 3, 6, 2.5),
    'LONDON': (1.0, 3, 6, 2.5),
    'NY': (1.0, 5, 10, 2.5),
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
                pending_lows.append(L[i-1])
                pending_highs.append(H[i-1])
                pending_atrs.append(b0_atr)
                pending_expiry.append(i+30)
        keep = [j for j, exp in enumerate(pending_expiry) if exp > i]
        if len(keep) < len(pending_expiry):
            pending_lows = [pending_lows[j] for j in keep]
            pending_highs = [pending_highs[j] for j in keep]
            pending_atrs = [pending_atrs[j] for j in keep]
            pending_expiry = [pending_expiry[j] for j in keep]
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
        if b0_close > b0_open and not np.isnan(b0_atr):  # GREEN candle for bearish OB
            future_low = min(L[i], L[i+1], L[i+2])
            move_dn = b0_close - future_low
            if move_dn > ob_str * b0_atr:
                pending_lows.append(L[i-1])
                pending_highs.append(H[i-1])
                pending_atrs.append(b0_atr)
                pending_expiry.append(i+30)
        keep = [j for j, exp in enumerate(pending_expiry) if exp > i]
        if len(keep) < len(pending_expiry):
            pending_lows = [pending_lows[j] for j in keep]
            pending_highs = [pending_highs[j] for j in keep]
            pending_atrs = [pending_atrs[j] for j in keep]
            pending_expiry = [pending_expiry[j] for j in keep]
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

print()
print("="*120)
print("CRITERI COMÚ — mateixos params per LONG i SHORT (per-session):")
print(f"  ASIA: SL 1.0 · TP 3/6 · OB 2.5")
print(f"  LONDON: SL 1.0 · TP 3/6 · OB 2.5")
print(f"  NY: SL 1.0 · TP 5/10 · OB 2.5")
print("="*120)

# LONGS amb COMMON_PARAMS
all_longs = []
for sess in ['ASIA', 'LONDON', 'NY']:
    sl, tp1, tp2, ob = COMMON_PARAMS[sess]
    trades = bt_long(sess, sl, tp1, tp2, ob)
    all_longs.extend(trades)
s_long = stats(np.array(all_longs))

# SHORTS amb COMMON_PARAMS (mateixos)
all_shorts = []
for sess in ['ASIA', 'LONDON', 'NY']:
    sl, tp1, tp2, ob = COMMON_PARAMS[sess]
    trades = bt_short(sess, sl, tp1, tp2, ob)
    all_shorts.extend(trades)
s_short = stats(np.array(all_shorts))

# BOTH
both = all_longs + all_shorts
s_both = stats(np.array(both))

print(f"\nLONGS only:  n={s_long['n']:>4} | WR {s_long['wr']:.1f}% | Net5y@0.05=${s_long['net']*5:+.0f} ({s_long['net']*5/50000*100:+.1f}%/any) | PF {s_long['pf']:.2f} | DD@0.05=${s_long['dd']*5:.0f} ({s_long['dd']*5/10000*100:.1f}%)", flush=True)
if s_short:
    print(f"SHORTS only: n={s_short['n']:>4} | WR {s_short['wr']:.1f}% | Net5y@0.05=${s_short['net']*5:+.0f} ({s_short['net']*5/50000*100:+.1f}%/any) | PF {s_short['pf']:.2f} | DD@0.05=${s_short['dd']*5:.0f} ({s_short['dd']*5/10000*100:.1f}%)", flush=True)
print(f"BOTH:        n={s_both['n']:>4} | WR {s_both['wr']:.1f}% | Net5y@0.05=${s_both['net']*5:+.0f} ({s_both['net']*5/50000*100:+.1f}%/any) | PF {s_both['pf']:.2f} | DD@0.05=${s_both['dd']*5:.0f} ({s_both['dd']*5/10000*100:.1f}%)", flush=True)

print()
print("="*120)
print("COMPARATIVA AMB OPTIMITZACIÓ INDEPENDENT (potencialment overfit):")
print("="*120)
print(f"LONGS independents:  +$20,668 (+41.3%/any) PF 1.65")
print(f"SHORTS independents: +$18,995 (+38.0%/any) PF 1.45")
print(f"BOTH independents:   +$39,662 (+79.3%/any) PF 1.53")

print()
print("CONCLUSIÓ:")
if s_short and s_short['net'] > 0:
    delta_pct = (s_short['net']*5/50000*100)
    if delta_pct > 20:
        print(f"  ✅ SHORTS amb mateixos params funcionen ({delta_pct:+.1f}%/any)")
        print(f"     Mantenir SHORTS si simplifica = més robust + més return")
    else:
        print(f"  ⚠️ SHORTS amb mateixos params dèbils ({delta_pct:+.1f}%/any)")
        print(f"     Els shorts NECESSITEN params específics → probablement overfit")
else:
    print(f"  ❌ SHORTS amb mateixos params NO funcionen → els shorts dependien de overfit")
