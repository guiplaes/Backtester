"""
BACKTEST DFMO — disparador zone-end del sistema actual
=======================================================
Implementació EXACTA de dfmo.py (Python port del MT5 indicator):
- Slow Stoch (25,4,4) — SMA(4) of raw_k(25), %D = SMA(4) of K
- RSI Fast (3) — SIMPLE MEAN (NO Wilder), tal com fa dfmo.py
- Zone end: prev bar both K & RSI in zone, current NOT both in zone
- LONG: zone-end OS (<20). SHORT: zone-end OB (>80).

NO LOOKAHEAD: a bar i tancat, mirem K[i-1] vs K[i] (tots passats).
Entry a C[i] (close del bar de senyal).
"""
import pandas as pd
import numpy as np
import time

CSV = "xauusd_m5_5y.csv"
REAL_COST = 0.40

print("Loading...", flush=True)
m5 = pd.read_csv(CSV, index_col=0, parse_dates=True)
m5.index = pd.to_datetime(m5.index, utc=True)
m5.columns = [c.lower() for c in m5.columns]
print(f"M5: {len(m5)} bars")

def aggregate(df_, rule):
    return df_.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last')).dropna()

def add_dfmo(df_, stoch_period=25, k_smooth=4, d_smooth=4, rsi_period=3):
    """Match dfmo.py exactly: simple-mean RSI, slow stoch K = SMA(k_smooth) of raw_k."""
    df_ = df_.copy()
    # EMA50 for trend filter
    df_['ema50'] = df_['close'].ewm(span=50, adjust=False).mean()
    # ATR
    hl = df_['high']-df_['low']
    hc = (df_['high']-df_['close'].shift()).abs()
    lc = (df_['low']-df_['close'].shift()).abs()
    tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
    df_['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
    df_['atr_pct'] = df_['atr'].rolling(200).rank(pct=True)
    df_['ema_slope'] = (df_['ema50'] - df_['ema50'].shift(10)) / df_['atr']

    # === DFMO computation (EXACT match dfmo.py) ===
    # Raw %K (25-bar)
    ll = df_['low'].rolling(stoch_period).min()
    hh = df_['high'].rolling(stoch_period).max()
    rng = (hh - ll)
    # When rng==0 (flat), dfmo.py returns 50.0
    raw_k = pd.Series(np.where(rng > 0, (df_['close'] - ll) / rng * 100, 50.0), index=df_.index)
    raw_k[rng.isna()] = np.nan
    # Slow %K = SMA(k_smooth) of raw_k
    df_['stoch_k'] = raw_k.rolling(k_smooth).mean()
    # %D = SMA(d_smooth) of stoch_k
    df_['stoch_d'] = df_['stoch_k'].rolling(d_smooth).mean()

    # RSI fast (3) — simple mean (NOT Wilder) as in dfmo.py
    delta = df_['close'].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_g = gain.rolling(rsi_period).mean()
    avg_l = loss.rolling(rsi_period).mean()
    rs = avg_g / avg_l.replace(0, np.nan)
    rsi = 100 - 100/(1+rs)
    # Edge cases:
    rsi = rsi.where(avg_l > 0, np.where(avg_g > 0, 100.0, 50.0))
    df_['rsi3'] = rsi

    return df_

def get_session(h):
    if h < 7: return 'ASIA'
    elif h < 13: return 'LONDON'
    elif h < 16: return 'OVERLAP'
    elif h < 21: return 'NY'
    else: return 'DEAD'

print("Aggregating + DFMO computing...", flush=True)
TFS = {
    'M5':  add_dfmo(m5),
    'M15': add_dfmo(aggregate(m5, '15min')),
    'M30': add_dfmo(aggregate(m5, '30min')),
    'H1':  add_dfmo(aggregate(m5, '1h')),
    'H4':  add_dfmo(aggregate(m5, '4h')),
}
for k,v in TFS.items(): print(f"  {k}: {len(v)} bars")

def precompute(df_):
    return {
        'O':df_['open'].values,'H':df_['high'].values,'L':df_['low'].values,'C':df_['close'].values,
        'EMA':df_['ema50'].values,'ATR':df_['atr'].values,
        'ATR_PCT':df_['atr_pct'].values,'EMA_SLOPE':df_['ema_slope'].values,
        'K':df_['stoch_k'].values,'RSI':df_['rsi3'].values,
        'SESSION':np.array([get_session(t.hour) for t in df_.index]),
        'HOUR':np.array([t.hour for t in df_.index]),
        'TS':df_.index,
        'n':len(df_)
    }

TF_ARRS = {tf:precompute(df) for tf,df in TFS.items()}

def bt_dfmo(arrs, direction, sl_atr, tp1_atr, tp2_atr, mask=None,
            ob=80.0, os=20.0, use_trend=True):
    """
    Backtest DFMO zone-end entry.
    NO LOOKAHEAD: bar i closed, mirem K[i-1],RSI[i-1] vs K[i],RSI[i].
    """
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    EMA=arrs['EMA'];ATR=arrs['ATR'];K=arrs['K'];RSI=arrs['RSI']
    n=arrs['n']
    if mask is None: mask=np.ones(n,dtype=bool)
    trades=[];pos=None
    for i in range(50, n):
        # Manage open position FIRST
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
        # Entry — DFMO zone-end at bar i
        if pos is None and not np.isnan(EMA[i]) and mask[i] and not np.isnan(ATR[i]):
            kp=K[i-1];kc=K[i];rp=RSI[i-1];rc=RSI[i]
            if np.isnan(kp) or np.isnan(kc) or np.isnan(rp) or np.isnan(rc):
                continue
            atr=ATR[i]
            if direction=='long':
                # Zone-end OS: prev both<20, curr NOT both<20
                prev_in = (kp < os) and (rp < os)
                curr_in = (kc < os) and (rc < os)
                signal = prev_in and (not curr_in)
                if use_trend and signal:
                    signal = signal and (C[i] > EMA[i])
                if signal:
                    sl=C[i]-atr*sl_atr
                    tp1=C[i]+atr*tp1_atr;tp2=C[i]+atr*tp2_atr
                    pos=(sl,tp1,tp2,0,0,0.5,0.5,C[i])
            else:
                prev_in = (kp > ob) and (rp > ob)
                curr_in = (kc > ob) and (rc > ob)
                signal = prev_in and (not curr_in)
                if use_trend and signal:
                    signal = signal and (C[i] < EMA[i])
                if signal:
                    sl=C[i]+atr*sl_atr
                    tp1=C[i]-atr*tp1_atr;tp2=C[i]-atr*tp2_atr
                    pos=(sl,tp1,tp2,0,0,0.5,0.5,C[i])
    return trades

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
def make_atr_mask(arrs, lo, hi):
    a=arrs['ATR_PCT']; return (~np.isnan(a))&(a>=lo)&(a<=hi)
def make_slope_mask(arrs, direction, thr):
    s=arrs['EMA_SLOPE']; v=~np.isnan(s)
    return (v&(s>=thr)) if direction=='long' else (v&(s<=-thr))
def make_higher_tf_mask(arrs_low, arrs_high, direction):
    ts_low=arrs_low['TS']; ts_high=arrs_high['TS']
    ema_high=arrs_high['EMA']; close_high=arrs_high['C']
    idx=np.searchsorted(ts_high, ts_low, side='right')-1
    idx=np.clip(idx, 0, len(ts_high)-1)
    bias_bull = close_high[idx] > ema_high[idx]
    return bias_bull if direction=='long' else ~bias_bull

# Diagnostic: count zone-ends per TF
print()
print("="*150)
print("DIAGNÒSTIC — Quants senyals zone-end per TF (sense filtres):")
print("="*150)
for tf, arrs in TF_ARRS.items():
    K=arrs['K'];RSI=arrs['RSI'];n=arrs['n']
    n_long=0;n_short=0
    for i in range(50,n):
        kp=K[i-1];kc=K[i];rp=RSI[i-1];rc=RSI[i]
        if np.isnan(kp) or np.isnan(rp): continue
        if (kp<20 and rp<20) and not(kc<20 and rc<20): n_long+=1
        if (kp>80 and rp>80) and not(kc>80 and rc>80): n_short+=1
    print(f"  {tf}: zone-end LONG={n_long}, SHORT={n_short}")

# Grids
SLTP_GRID = [
    (0.5, 1.0, 2.0),
    (1.0, 1.5, 3.0),
    (1.0, 2.0, 4.0),
    (1.5, 3.0, 6.0),
    (2.0, 3.0, 6.0),
    (2.0, 5.0, 10.0),
]

print()
print("="*150)
print("DFMO BASELINE (EMA50 trend filter, sessions allowed):")
print("="*150)
results = []
for tf, arrs in TF_ARRS.items():
    print(f"\n--- {tf} ({arrs['n']} bars) ---")
    base = make_session_mask(arrs, {'ASIA','LONDON','NY','OVERLAP'})
    for sl,tp1,tp2 in SLTP_GRID:
        l = bt_dfmo(arrs,'long',sl,tp1,tp2,base, use_trend=True)
        s = bt_dfmo(arrs,'short',sl,tp1,tp2,base, use_trend=True)
        both = l+s
        s_l=stats(l);s_s=stats(s);s_b=stats(both)
        line=f"  SL{sl} TP{tp1}/{tp2} | L:{fmt(s_l)[:55]:<55} | S:{fmt(s_s)[:55]:<55} | B:{fmt(s_b)}"
        print(line)
        if s_b: results.append({'tf':tf,'sl':sl,'tp1':tp1,'tp2':tp2,'variant':'baseline_trend','b':s_b,'l':s_l,'s':s_s})

print()
print("="*150)
print("DFMO SENSE EMA50 trend filter (zone-end pur):")
print("="*150)
for tf in ['M5','M15','M30','H1','H4']:
    arrs = TF_ARRS[tf]
    print(f"\n--- {tf} ---")
    base = make_session_mask(arrs, {'ASIA','LONDON','NY','OVERLAP'})
    for sl,tp1,tp2 in [(1.0,2.0,4.0),(1.5,3.0,6.0),(2.0,5.0,10.0)]:
        l = bt_dfmo(arrs,'long',sl,tp1,tp2,base, use_trend=False)
        s = bt_dfmo(arrs,'short',sl,tp1,tp2,base, use_trend=False)
        both = l+s
        line=f"  SL{sl} TP{tp1}/{tp2} | L:{fmt(stats(l))[:55]:<55} | S:{fmt(stats(s))[:55]:<55} | B:{fmt(stats(both))}"
        print(line)

print()
print("="*150)
print("DFMO + SESSIONS específiques (LONG only, EMA trend):")
print("="*150)
for tf in ['M5','M15','M30','H1','H4']:
    arrs = TF_ARRS[tf]
    sl,tp1,tp2 = (1.0,2.0,4.0)  # mid-range
    print(f"\n--- {tf} (SL1.0 TP2/4) ---")
    for sess_name, sess in [('ALL',{'ASIA','LONDON','OVERLAP','NY'}),('LONDON',{'LONDON'}),
                             ('OVERLAP',{'OVERLAP'}),('NY',{'NY'}),('ASIA',{'ASIA'}),
                             ('OVERLAP+NY',{'OVERLAP','NY'}),('LONDON+NY',{'LONDON','NY'})]:
        m = make_session_mask(arrs, sess)
        l = bt_dfmo(arrs,'long',sl,tp1,tp2,m, use_trend=True)
        print(f"  {sess_name:12} L: {fmt(stats(l))}")
        if stats(l): results.append({'tf':tf,'sl':sl,'tp1':tp1,'tp2':tp2,
                                       'variant':f'sess_{sess_name}_LONG','b':stats(l),'l':stats(l),'s':None})

print()
print("="*150)
print("DFMO + MULTI-TF CONFIRMATION:")
print("="*150)
for entry_tf, higher_tf in [('M5','M15'),('M5','H1'),('M15','H1'),('M15','H4'),('M30','H4'),('H1','H4')]:
    arrs_low = TF_ARRS[entry_tf]
    arrs_high = TF_ARRS[higher_tf]
    base = make_session_mask(arrs_low, {'ASIA','LONDON','NY','OVERLAP'})
    htf_long = make_higher_tf_mask(arrs_low, arrs_high, 'long')
    htf_short = make_higher_tf_mask(arrs_low, arrs_high, 'short')
    print(f"\n{entry_tf}+{higher_tf}:")
    for sl,tp1,tp2 in [(1.0,2.0,4.0),(1.5,3.0,6.0),(2.0,5.0,10.0)]:
        l = bt_dfmo(arrs_low,'long',sl,tp1,tp2, base & htf_long, use_trend=False)  # rely on htf trend
        s = bt_dfmo(arrs_low,'short',sl,tp1,tp2, base & htf_short, use_trend=False)
        both = l+s
        s_l=stats(l);s_s=stats(s);s_b=stats(both)
        print(f"  SL{sl} TP{tp1}/{tp2} | L:{fmt(s_l)[:55]:<55} | B:{fmt(s_b)}")
        if s_b: results.append({'tf':entry_tf,'higher':higher_tf,'sl':sl,'tp1':tp1,'tp2':tp2,
                                  'variant':f'mtf_{higher_tf}','b':s_b,'l':s_l,'s':s_s})

print()
print("="*150)
print("DFMO + ATR filter + slope (LONG only):")
print("="*150)
for tf in ['M5','M15','M30','H1','H4']:
    arrs = TF_ARRS[tf]
    print(f"\n--- {tf} ---")
    base = make_session_mask(arrs, {'ASIA','LONDON','NY','OVERLAP'})
    atr_hi = make_atr_mask(arrs, 0.66, 1.0)
    slope_long = make_slope_mask(arrs, 'long', 0.5)
    for sl,tp1,tp2 in [(1.0,2.0,4.0),(1.5,3.0,6.0)]:
        for fname, m in [('ATR_HIGH', base & atr_hi),
                           ('slope0.5', base & slope_long),
                           ('ATR_HIGH+slope0.5', base & atr_hi & slope_long),
                           ('NY+slope0.5', make_session_mask(arrs,{'NY'}) & slope_long)]:
            l = bt_dfmo(arrs,'long',sl,tp1,tp2,m, use_trend=True)
            print(f"  SL{sl} TP{tp1}/{tp2} {fname:<22} L: {fmt(stats(l))}")
            if stats(l): results.append({'tf':tf,'sl':sl,'tp1':tp1,'tp2':tp2,
                                           'variant':f'{fname}_LONG','b':stats(l),'l':stats(l),'s':None})

print()
print("="*150)
print("TOP DFMO CONFIGS — ranking PF amb n>=30:")
print("="*150)
valid = [r for r in results if r['b'] and r['b']['n']>=30 and r['b']['net']>0 and r['b']['pf']>1.10]
valid.sort(key=lambda x:-x['b']['pf'])
for r in valid[:30]:
    s = r['b']
    extra = f" htf={r.get('higher','')}" if 'higher' in r else ""
    print(f"  {r['tf']:<5} {r['variant']:<28} SL{r['sl']} TP{r['tp1']}/{r['tp2']}{extra} | {fmt(s)}")

print("\nDONE")
