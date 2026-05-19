"""
Test: ATR auto-adapta volatilitat → els mateixos multiplicadors haurien de funcionar
en totes les sessions. Buscar UN sol set de params que sigui comparable als per-session.
"""
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
ALLOWED = {'ASIA', 'LONDON', 'NY'}
ALLOWED_MASK = np.array([s in ALLOWED for s in SESSION_ARR])

def bt_long(sl_atr, tp1_atr, tp2_atr, ob_str):
    """LONG only, totes les sessions actives, mateixos params globals."""
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
            if not ALLOWED_MASK[i]: continue
            for j in range(len(pending_lows)):
                if L[i] <= pending_highs[j] and C[i] > pending_lows[j]:
                    if C[i] > O[i]:
                        atr = pending_atrs[j]; e = C[i]
                        pos = (pending_lows[j]-atr*sl_atr*0.5, e+atr*tp1_atr, e+atr*tp2_atr, 0, 0, 0.5, 0.5, e)
                        pending_lows.pop(j); pending_highs.pop(j); pending_atrs.pop(j); pending_expiry.pop(j)
                        break
    return trades_pnl

def bt_short(sl_atr, tp1_atr, tp2_atr, ob_str):
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
            if not ALLOWED_MASK[i]: continue
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
print("CERCA del MILLOR set GLOBAL de params (mateixos per totes les sessions):")
print("="*120)

GRID = [
    (1.0, 2, 4, 1.5),
    (1.0, 2, 4, 2.0),
    (1.0, 2, 4, 2.5),
    (1.0, 3, 6, 2.0),
    (1.0, 3, 6, 2.5),
    (1.0, 4, 8, 2.5),
    (1.0, 5, 10, 2.5),
    (1.5, 2, 4, 2.0),
    (1.5, 3, 6, 2.0),
    (1.5, 3, 6, 2.5),
    (1.5, 4, 8, 2.5),
    (1.5, 5, 10, 2.5),
    (2.0, 3, 6, 2.0),
    (2.0, 4, 8, 2.5),
    (2.0, 5, 10, 2.5),
]

best_both = None; best_both_stats = None; best_both_metric = -999999

print(f"\n{'Config':<22} | {'LONGS Net':>10} | {'SHORTS Net':>10} | {'BOTH Net':>10} | {'BOTH PF':>7} | {'BOTH DD%':>8}")
print("-"*100)

results = []
for sl, tp1, tp2, ob in GRID:
    label = f"SL{sl} TP{tp1}/{tp2} OB{ob}"
    longs = bt_long(sl, tp1, tp2, ob)
    shorts = bt_short(sl, tp1, tp2, ob)
    both = longs + shorts
    s_l = stats(np.array(longs)) if longs else None
    s_s = stats(np.array(shorts)) if shorts else None
    s_b = stats(np.array(both))
    if s_b:
        net5y_005_long  = s_l['net']*5 if s_l else 0
        net5y_005_short = s_s['net']*5 if s_s else 0
        net5y_005_both  = s_b['net']*5
        dd_005_pct = s_b['dd']*5/10000*100
        print(f"{label:<22} | ${net5y_005_long:>+9.0f} | ${net5y_005_short:>+9.0f} | ${net5y_005_both:>+9.0f} | {s_b['pf']:>6.2f} | {dd_005_pct:>6.1f}%", flush=True)
        results.append({
            'label':label, 'sl':sl, 'tp1':tp1, 'tp2':tp2, 'ob':ob,
            'longs':s_l, 'shorts':s_s, 'both':s_b,
            'net':net5y_005_both, 'pf':s_b['pf'], 'dd_pct':dd_005_pct,
        })
        # Score: net adjusted by DD penalty
        score = s_b['net'] - s_b['dd']*0.5  # penalitza DD
        if score > best_both_metric:
            best_both_metric = score
            best_both = (sl, tp1, tp2, ob)
            best_both_stats = s_b

print()
print("="*120)
print(f"MILLOR config UNIFICAT GLOBAL: SL={best_both[0]} TP={best_both[1]}/{best_both[2]} OB={best_both[3]}")
print(f"   Net 5y@0.05: ${best_both_stats['net']*5:+.0f} ({best_both_stats['net']*5/50000*100:+.1f}%/any)")
print(f"   PF: {best_both_stats['pf']:.2f} | DD@0.05: ${best_both_stats['dd']*5:.0f} ({best_both_stats['dd']*5/10000*100:.1f}%)")
print()
print("="*120)
print("COMPARATIVA AMB PER-SESSION OPTIMITZAT:")
print("="*120)
print(f"  Per-session optimitzat (6 sets):     +$39,663 (+79.3%/any) PF 1.53")
print(f"  Mateixos params SHORT-optim (3 sets): +$39,517 (+79.0%/any) PF ~1.49")
print(f"  GLOBAL UNIFICAT (1 set):              ${best_both_stats['net']*5:+.0f} ({best_both_stats['net']*5/50000*100:+.1f}%/any) PF {best_both_stats['pf']:.2f}")

# Top 3 results
print()
print("Top 3 configs globals:")
sorted_results = sorted(results, key=lambda r: -r['net'])
for r in sorted_results[:3]:
    print(f"  {r['label']:<22} Net=${r['net']:+.0f} ({r['net']/50000*100:+.1f}%/any) PF={r['pf']:.2f} DD={r['dd_pct']:.1f}%")
