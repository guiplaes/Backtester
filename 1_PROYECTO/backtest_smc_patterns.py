"""
ALTRES PATRONS SMC — sense lookahead bias
==========================================
1. FVG (Fair Value Gap) — gap entre H[i-3] i L[i-1] (bullish), enter on FVG fill
2. Liquidity Sweep + Reversion — preu trenca un swing high recent i tanca a sota
3. Engulfing entry
4. Pin Bar / Hammer (zona) entry
5. Inside Bar Breakout
6. Break of Structure (BOS) — preu trenca swing previ i continua
7. Donchian Channel Breakout
"""
import pandas as pd
import numpy as np

CSV = "xauusd_m5_5y.csv"
REAL_COST = 0.40

print("Loading...", flush=True)
m5 = pd.read_csv(CSV, index_col=0, parse_dates=True)
m5.index = pd.to_datetime(m5.index, utc=True)
m5.columns = [c.lower() for c in m5.columns]

def aggregate(df_, rule):
    return df_.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last')).dropna()

def add_ind(df_):
    df_ = df_.copy()
    df_['ema50'] = df_['close'].ewm(span=50, adjust=False).mean()
    hl = df_['high']-df_['low']
    hc = (df_['high']-df_['close'].shift()).abs()
    lc = (df_['low']-df_['close'].shift()).abs()
    tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
    df_['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
    return df_

print("Aggregating...", flush=True)
TFS = {
    'M15': add_ind(aggregate(m5, '15min')),
    'M30': add_ind(aggregate(m5, '30min')),
    'H1':  add_ind(aggregate(m5, '1h')),
    'H4':  add_ind(aggregate(m5, '4h')),
    'D1':  add_ind(aggregate(m5, '1D')),
}

def get_session(h):
    if h < 7: return 'ASIA'
    elif h < 13: return 'LONDON'
    elif h < 16: return 'OVERLAP'
    elif h < 21: return 'NY'
    else: return 'DEAD'

def precompute(df_):
    return {
        'O':df_['open'].values,'H':df_['high'].values,'L':df_['low'].values,'C':df_['close'].values,
        'EMA':df_['ema50'].values,'ATR':df_['atr'].values,
        'SESSION':np.array([get_session(t.hour) for t in df_.index]),
        'n':len(df_)
    }

TF_ARRS = {tf:precompute(df) for tf,df in TFS.items()}

def stats(arr):
    if not arr: return None
    arr=np.array(arr);n=len(arr);w=(arr>0).sum();net=arr.sum()
    pp=arr[arr>0].sum();pl_=abs(arr[arr<=0].sum())
    pf=pp/pl_ if pl_ else 0
    eq=np.cumsum(arr);peak=np.maximum.accumulate(eq);dd=(peak-eq).max()
    return {'n':n,'wr':w/n*100,'net':net,'pf':pf,'dd':dd}

def fmt(s):
    if s is None or s['n']<10: return f"n={s['n'] if s else 0} (insuf)"
    return f"n={s['n']:>4} WR{s['wr']:.1f}% Net=${s['net']:+.0f} PF{s['pf']:.2f} DD${s['dd']:.0f}"

def make_session_mask(arrs, allowed):
    return np.isin(arrs['SESSION'], list(allowed))

# =====================================================================
# Generic backtest with custom signal generator
# =====================================================================
def bt_signal(arrs, direction, signal_fn, sl_atr, tp1_atr, tp2_atr, mask=None, ttl=30):
    """
    signal_fn(arrs, i, direction) -> (entry_ok, sl_ref_price, atr_ref) or None
    Returns trades list.
    """
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    EMA=arrs['EMA'];ATR=arrs['ATR'];n=arrs['n']
    if mask is None: mask=np.ones(n,dtype=bool)
    trades=[];pos=None
    for i in range(50,n):
        if pos is not None:
            if direction=='long':
                sl_h=L[i]<=pos[0];tp1_h=H[i]>=pos[1];tp2_h=H[i]>=pos[2]
            else:
                sl_h=H[i]>=pos[0];tp1_h=L[i]<=pos[1];tp2_h=L[i]<=pos[2]
            if sl_h and (tp1_h or tp2_h):
                if direction=='long':
                    if (O[i]-pos[0])<(pos[1]-O[i]): tp1_h=False;tp2_h=False
                else:
                    if (pos[0]-O[i])<(O[i]-pos[1]): tp1_h=False;tp2_h=False
            sgn=1 if direction=='long' else -1
            pnl1=pos[3];pnl2=pos[4];q1=pos[5];q2=pos[6];e=pos[7]
            if tp1_h and q1>0: pnl1=(pos[1]-e)*0.5*sgn;q1=0
            if tp2_h and q2>0: pnl2=(pos[2]-e)*0.5*sgn;q2=0
            if sl_h:
                if q1>0: pnl1=(pos[0]-e)*0.5*sgn;q1=0
                if q2>0: pnl2=(pos[0]-e)*0.5*sgn;q2=0
            if q1==0 and q2==0:
                trades.append(pnl1+pnl2-REAL_COST);pos=None
            else:
                pos=(pos[0],pos[1],pos[2],pnl1,pnl2,q1,q2,e)
        if pos is None and not np.isnan(EMA[i]) and mask[i]:
            cond_t=(C[i]>EMA[i]) if direction=='long' else (C[i]<EMA[i])
            if cond_t:
                sig = signal_fn(arrs, i, direction)
                if sig is not None:
                    sl_ref, atr_ref = sig
                    if direction=='long':
                        sl=sl_ref-atr_ref*sl_atr*0.5
                        tp1=C[i]+atr_ref*tp1_atr;tp2=C[i]+atr_ref*tp2_atr
                    else:
                        sl=sl_ref+atr_ref*sl_atr*0.5
                        tp1=C[i]-atr_ref*tp1_atr;tp2=C[i]-atr_ref*tp2_atr
                    pos=(sl,tp1,tp2,0,0,0.5,0.5,C[i])
    return trades

# =====================================================================
# SIGNAL GENERATORS — totes amb dades passades only
# =====================================================================

def sig_fvg(arrs, i, direction):
    """FVG: gap entre H[i-3] i L[i-1] (bullish) o L[i-3] i H[i-1] (bearish)
    Bullish FVG: H[i-3] < L[i-1] AND a la barra i preu cau dins el gap.
    SL ref: bottom of FVG. ATR: ATR[i-3]."""
    if i < 5: return None
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C'];ATR=arrs['ATR']
    if direction == 'long':
        # Bullish FVG between bar i-3 and bar i-1
        gap_top = L[i-1]
        gap_bot = H[i-3]
        if gap_top > gap_bot and not np.isnan(ATR[i-3]):
            atr = ATR[i-3]
            if (gap_top - gap_bot) > 0.3 * atr:  # FVG significatiu
                # Enter if bar i fills the gap (low touched gap zone) + reverses
                if L[i] <= gap_top and L[i] >= gap_bot - 0.5*atr and C[i] > O[i]:
                    return (gap_bot, atr)
    else:
        # Bearish FVG: L[i-3] > H[i-1]
        gap_top = L[i-3]
        gap_bot = H[i-1]
        if gap_top > gap_bot and not np.isnan(ATR[i-3]):
            atr = ATR[i-3]
            if (gap_top - gap_bot) > 0.3 * atr:
                if H[i] >= gap_bot and H[i] <= gap_top + 0.5*atr and C[i] < O[i]:
                    return (gap_top, atr)
    return None

def sig_liquidity_sweep(arrs, i, direction, lookback=20):
    """Liquidity Sweep + Reversion: preu trenca el high (low) dels últims N bars
    PEROOOO tanca de tornada al rang. Enter al tancament de la barra de sweep."""
    if i < lookback+1: return None
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C'];ATR=arrs['ATR']
    if direction == 'long':
        # Find low of last N bars (bars i-lookback..i-1)
        prev_low = L[i-lookback:i].min()
        # Sweep: bar i breaks below prev_low but closes back above
        if L[i] < prev_low and C[i] > prev_low and C[i] > O[i]:
            atr = ATR[i] if not np.isnan(ATR[i]) else (H[i]-L[i])
            if atr > 0:
                return (L[i], atr)
    else:
        prev_high = H[i-lookback:i].max()
        if H[i] > prev_high and C[i] < prev_high and C[i] < O[i]:
            atr = ATR[i] if not np.isnan(ATR[i]) else (H[i]-L[i])
            if atr > 0:
                return (H[i], atr)
    return None

def sig_engulfing(arrs, i, direction):
    """Bullish engulfing: bar i-1 red, bar i green and engulfs i-1.
    SL ref: low of engulfing bar."""
    if i < 2: return None
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C'];ATR=arrs['ATR']
    if direction == 'long':
        # Prev red, current green and engulfing
        if C[i-1] < O[i-1] and C[i] > O[i]:
            if O[i] <= C[i-1] and C[i] >= O[i-1]:
                atr = ATR[i] if not np.isnan(ATR[i]) else (H[i]-L[i])
                if atr > 0 and (C[i]-O[i]) > 0.5 * atr:
                    return (L[i], atr)
    else:
        if C[i-1] > O[i-1] and C[i] < O[i]:
            if O[i] >= C[i-1] and C[i] <= O[i-1]:
                atr = ATR[i] if not np.isnan(ATR[i]) else (H[i]-L[i])
                if atr > 0 and (O[i]-C[i]) > 0.5 * atr:
                    return (H[i], atr)
    return None

def sig_pin_bar(arrs, i, direction):
    """Hammer (long) / Shooting star (short): tail >= 2x body, body small."""
    if i < 1: return None
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C'];ATR=arrs['ATR']
    rng = H[i]-L[i]; body = abs(C[i]-O[i])
    if rng <= 0 or body == 0: return None
    if direction == 'long':
        # Hammer: long lower wick, small body near top
        lower_wick = min(O[i], C[i]) - L[i]
        upper_wick = H[i] - max(O[i], C[i])
        if lower_wick >= 2 * body and lower_wick >= 0.6 * rng and upper_wick < 0.2 * rng:
            atr = ATR[i] if not np.isnan(ATR[i]) else rng
            return (L[i], atr)
    else:
        upper_wick = H[i] - max(O[i], C[i])
        lower_wick = min(O[i], C[i]) - L[i]
        if upper_wick >= 2 * body and upper_wick >= 0.6 * rng and lower_wick < 0.2 * rng:
            atr = ATR[i] if not np.isnan(ATR[i]) else rng
            return (H[i], atr)
    return None

def sig_inside_bar(arrs, i, direction):
    """Inside bar breakout: bar i-1 inside bar i-2, bar i breaks out."""
    if i < 3: return None
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C'];ATR=arrs['ATR']
    # i-2 is mother, i-1 is inside
    mother_h = H[i-2]; mother_l = L[i-2]
    if H[i-1] <= mother_h and L[i-1] >= mother_l:
        atr = ATR[i-2] if not np.isnan(ATR[i-2]) else (mother_h-mother_l)
        if atr <= 0: return None
        if direction == 'long':
            if C[i] > mother_h and C[i] > O[i]:
                return (mother_l, atr)
        else:
            if C[i] < mother_l and C[i] < O[i]:
                return (mother_h, atr)
    return None

def sig_bos(arrs, i, direction, lookback=10):
    """Break of Structure: bar i closes above last N highs (long) or below last N lows."""
    if i < lookback+2: return None
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C'];ATR=arrs['ATR']
    if direction == 'long':
        prev_high = H[i-lookback-1:i].max()
        if C[i] > prev_high and C[i] > O[i]:
            atr = ATR[i] if not np.isnan(ATR[i]) else (H[i]-L[i])
            if atr > 0:
                return (L[i-lookback-1:i].min(), atr)
    else:
        prev_low = L[i-lookback-1:i].min()
        if C[i] < prev_low and C[i] < O[i]:
            atr = ATR[i] if not np.isnan(ATR[i]) else (H[i]-L[i])
            if atr > 0:
                return (H[i-lookback-1:i].max(), atr)
    return None

def sig_donchian(arrs, i, direction, lookback=20):
    """Donchian: close > max(High of last N bars excluding current)."""
    if i < lookback+1: return None
    H=arrs['H'];L=arrs['L'];C=arrs['C'];O=arrs['O'];ATR=arrs['ATR']
    if direction == 'long':
        ph = H[i-lookback:i].max()
        if C[i] > ph:
            atr = ATR[i] if not np.isnan(ATR[i]) else (H[i]-L[i])
            if atr > 0:
                return (L[i-lookback:i].min(), atr)
    else:
        pl = L[i-lookback:i].min()
        if C[i] < pl:
            atr = ATR[i] if not np.isnan(ATR[i]) else (H[i]-L[i])
            if atr > 0:
                return (H[i-lookback:i].max(), atr)
    return None

# =====================================================================
SIGNALS = {
    'FVG': sig_fvg,
    'LiquiditySweep': sig_liquidity_sweep,
    'Engulfing': sig_engulfing,
    'PinBar': sig_pin_bar,
    'InsideBar': sig_inside_bar,
    'BOS': sig_bos,
    'Donchian': sig_donchian,
}

# Config grids per pattern
GRID_PATTERNS = [
    (1.0, 2, 4),
    (1.0, 3, 6),
    (1.5, 3, 6),
    (1.5, 5, 10),
    (2.0, 5, 10),
]

print()
print("="*150)
print("PATRONS SMC ALTERNATIUS — Comparativa per TF")
print("="*150)

results = []

for tf in ['M15','M30','H1','H4','D1']:
    arrs = TF_ARRS[tf]
    base = make_session_mask(arrs, {'ASIA','LONDON','NY','OVERLAP'})
    print(f"\n{'#'*100}")
    print(f"# {tf} ({arrs['n']} bars)")
    print(f"{'#'*100}")

    for sig_name, sig_fn in SIGNALS.items():
        print(f"\n{tf} — {sig_name}:")
        for sl, tp1, tp2 in GRID_PATTERNS:
            l = bt_signal(arrs, 'long', sig_fn, sl, tp1, tp2, base)
            s = bt_signal(arrs, 'short', sig_fn, sl, tp1, tp2, base)
            both = l + s
            s_l = stats(l); s_s = stats(s); s_b = stats(both)
            line = f"  SL{sl} TP{tp1}/{tp2} | L:{fmt(s_l)[:50]:<50} | S:{fmt(s_s)[:50]:<50} | B:{fmt(s_b)}"
            print(line)
            if s_b and s_b['n']>=30:
                results.append({'tf':tf,'pattern':sig_name,'sl':sl,'tp1':tp1,'tp2':tp2,
                                'b':s_b,'l':s_l,'s':s_s})

print()
print("="*150)
print("TOP PATTERNS — ranking BOTH per PF amb Net positiu i n>=50:")
print("="*150)
valid = [r for r in results if r['b']['n']>=50 and r['b']['net']>0 and r['b']['pf']>1.10]
valid.sort(key=lambda x:-x['b']['pf'])
for r in valid[:30]:
    print(f"  {r['tf']:<5} {r['pattern']:<18} SL{r['sl']} TP{r['tp1']}/{r['tp2']} | B:{fmt(r['b'])}")

print()
print("="*150)
print("TOP LONG-only PATTERNS:")
print("="*150)
long_valid = [r for r in results if r['l'] and r['l']['n']>=50 and r['l']['net']>0 and r['l']['pf']>1.15]
long_valid.sort(key=lambda x:-x['l']['pf'])
for r in long_valid[:30]:
    print(f"  {r['tf']:<5} {r['pattern']:<18} SL{r['sl']} TP{r['tp1']}/{r['tp2']} | L:{fmt(r['l'])}")

print("\nDONE")
