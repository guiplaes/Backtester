"""Backtest M5 SENSE lookahead — detectar OB 3 barres després de la mother."""
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
ALLOWED_MASK = np.array([s in {'ASIA', 'LONDON', 'NY'} for s in SESSION_ARR])

def bt_no_lookahead(direction, sl_atr, tp1_atr, tp2_atr, ob_str):
    """
    NO LOOKAHEAD: l'OB es detecta al bar X on les bars X-3 (mother), X-2, X-1, X
    JA HAN OCORREGUT TOTES.
    Mother és bar X-3. Confirmem el move amb bars X-2, X-1, X (totes passades).
    L'entrada es pot intentar a partir de X+1 (bar següent).

    Versió correcta sense usar futur.
    """
    trades_pnl = []
    pos = None
    pending_lows = []; pending_highs = []; pending_atrs = []; pending_expiry = []

    for i in range(50, n):
        # Manage existing position FIRST (at bar i)
        if pos is not None:
            if direction == 'long':
                sl_h = L[i] <= pos[0]
                tp1_h = H[i] >= pos[1]
                tp2_h = H[i] >= pos[2]
            else:
                sl_h = H[i] >= pos[0]
                tp1_h = L[i] <= pos[1]
                tp2_h = L[i] <= pos[2]
            if sl_h and (tp1_h or tp2_h):
                if direction == 'long':
                    if (O[i]-pos[0]) < (pos[1]-O[i]): tp1_h=False; tp2_h=False
                else:
                    if (pos[0]-O[i]) < (O[i]-pos[1]): tp1_h=False; tp2_h=False
            sgn = 1 if direction == 'long' else -1
            pnl1 = pos[3]; pnl2 = pos[4]; q1 = pos[5]; q2 = pos[6]; e = pos[7]
            if tp1_h and q1>0: pnl1 = (pos[1]-e)*0.5*sgn; q1=0
            if tp2_h and q2>0: pnl2 = (pos[2]-e)*0.5*sgn; q2=0
            if sl_h:
                if q1>0: pnl1 = (pos[0]-e)*0.5*sgn; q1=0
                if q2>0: pnl2 = (pos[0]-e)*0.5*sgn; q2=0
            if q1==0 and q2==0:
                trades_pnl.append(pnl1+pnl2 - REAL_COST)
                pos = None
            else:
                pos = (pos[0], pos[1], pos[2], pnl1, pnl2, q1, q2, e)

        # OB detection at current bar i (mother is i-3, all bars past)
        if i >= 3:
            mom_close = C[i-3]; mom_open = O[i-3]; mom_low = L[i-3]; mom_high = H[i-3]; mom_atr = ATR[i-3]
            if direction == 'long':
                if mom_close < mom_open and not np.isnan(mom_atr):
                    move = max(H[i-2], H[i-1], H[i]) - mom_close
                    if move > ob_str * mom_atr:
                        pending_lows.append(mom_low)
                        pending_highs.append(mom_high)
                        pending_atrs.append(mom_atr)
                        pending_expiry.append(i + 30)
            else:
                if mom_close > mom_open and not np.isnan(mom_atr):
                    move = mom_close - min(L[i-2], L[i-1], L[i])
                    if move > ob_str * mom_atr:
                        pending_lows.append(mom_low)
                        pending_highs.append(mom_high)
                        pending_atrs.append(mom_atr)
                        pending_expiry.append(i + 30)

        # Clean expired
        keep = [j for j, exp in enumerate(pending_expiry) if exp > i]
        if len(keep) < len(pending_expiry):
            pending_lows = [pending_lows[j] for j in keep]
            pending_highs = [pending_highs[j] for j in keep]
            pending_atrs = [pending_atrs[j] for j in keep]
            pending_expiry = [pending_expiry[j] for j in keep]

        # Try entry (only if no position)
        if pos is None and not np.isnan(EMA[i]) and ALLOWED_MASK[i]:
            cond_trend = (C[i] > EMA[i]) if direction == 'long' else (C[i] < EMA[i])
            if cond_trend:
                for j in range(len(pending_lows)):
                    ob_low = pending_lows[j]
                    ob_high = pending_highs[j]
                    ob_a = pending_atrs[j]
                    if direction == 'long':
                        cond_in = L[i] <= ob_high and C[i] > ob_low
                        cond_rev = C[i] > O[i]
                    else:
                        cond_in = H[i] >= ob_low and C[i] < ob_high
                        cond_rev = C[i] < O[i]
                    if cond_in and cond_rev:
                        atr = ob_a; e = C[i]
                        if direction == 'long':
                            sl = ob_low - atr * sl_atr * 0.5
                            tp1 = e + atr * tp1_atr
                            tp2 = e + atr * tp2_atr
                        else:
                            sl = ob_high + atr * sl_atr * 0.5
                            tp1 = e - atr * tp1_atr
                            tp2 = e - atr * tp2_atr
                        pos = (sl, tp1, tp2, 0, 0, 0.5, 0.5, e)
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

print("Backtest M5 UNIFIED (SL 1.0 / TP 2/4 / OB 2.5) SENSE lookahead", flush=True)
print()

# LONGS
print("Running LONGS no-lookahead...", flush=True)
longs = bt_no_lookahead('long', 1.0, 2, 4, 2.5)
s_l = stats(np.array(longs))

# SHORTS
print("Running SHORTS no-lookahead...", flush=True)
shorts = bt_no_lookahead('short', 1.0, 2, 4, 2.5)
s_s = stats(np.array(shorts))

# BOTH
both = longs + shorts
s_b = stats(np.array(both))

print()
print("="*100)
print("RESULTAT REAL (sense lookahead bias):")
print("="*100)
print()
if s_l:
    print(f"LONGS:  n={s_l['n']:>4} | WR {s_l['wr']:.1f}% | Net=${s_l['net']:+.0f} | PF {s_l['pf']:.2f} | DD ${s_l['dd']:.0f}")
if s_s:
    print(f"SHORTS: n={s_s['n']:>4} | WR {s_s['wr']:.1f}% | Net=${s_s['net']:+.0f} | PF {s_s['pf']:.2f} | DD ${s_s['dd']:.0f}")
if s_b:
    print(f"BOTH:   n={s_b['n']:>4} | WR {s_b['wr']:.1f}% | Net=${s_b['net']:+.0f} | PF {s_b['pf']:.2f} | DD ${s_b['dd']:.0f}")
    print()
    print(f"Escalat 0.05 lot:")
    print(f"  Net5y: ${s_b['net']*5:+.0f} ({s_b['net']*5/50000*100:+.1f}%/any)")
    print(f"  DD: ${s_b['dd']*5:.0f} ({s_b['dd']*5/10000*100:.1f}%)")

print()
print("="*100)
print("VS BACKTEST AMB LOOKAHEAD (BUG):")
print("="*100)
print(f"  BUG (lookahead):  n=7362 | Net=$+7,277 | PF 1.68")
print(f"  REAL (no lookahead): vegis sobre")
