"""
COMBINED FILTERS + MULTI-TF + EXTRA TFs
========================================
- Afegim TFs: M10, M20, H2, H6, H8, H12, D1
- Combinem filtres ja guanyadors: NY + slope + LONG only, ATR_HIGH + LONG, etc.
- Multi-TF confirmation: entry TF only if higher-TF trend alineat
- Time-stop (close after N bars)
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
    df_['ema200'] = df_['close'].ewm(span=200, adjust=False).mean()
    hl = df_['high']-df_['low']
    hc = (df_['high']-df_['close'].shift()).abs()
    lc = (df_['low']-df_['close'].shift()).abs()
    tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
    df_['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
    df_['atr_pct'] = df_['atr'].rolling(200).rank(pct=True)
    df_['ema_slope'] = (df_['ema50'] - df_['ema50'].shift(10)) / df_['atr']
    return df_

print("Aggregating extra TFs...", flush=True)
TFS = {
    'M15': add_ind(aggregate(m5, '15min')),
    'M30': add_ind(aggregate(m5, '30min')),
    'H1':  add_ind(aggregate(m5, '1h')),
    'H2':  add_ind(aggregate(m5, '2h')),
    'H4':  add_ind(aggregate(m5, '4h')),
    'H6':  add_ind(aggregate(m5, '6h')),
    'H8':  add_ind(aggregate(m5, '8h')),
    'H12': add_ind(aggregate(m5, '12h')),
    'D1':  add_ind(aggregate(m5, '1D')),
}
for k,v in TFS.items(): print(f"  {k}: {len(v)} bars", flush=True)

def get_session(h):
    if h < 7: return 'ASIA'
    elif h < 13: return 'LONDON'
    elif h < 16: return 'OVERLAP'
    elif h < 21: return 'NY'
    else: return 'DEAD'

def precompute(df_):
    return {
        'O':df_['open'].values,'H':df_['high'].values,'L':df_['low'].values,'C':df_['close'].values,
        'EMA':df_['ema50'].values,'EMA200':df_['ema200'].values,'ATR':df_['atr'].values,
        'ATR_PCT':df_['atr_pct'].values,'EMA_SLOPE':df_['ema_slope'].values,
        'HOUR':np.array([t.hour for t in df_.index]),
        'SESSION':np.array([get_session(t.hour) for t in df_.index]),
        'DOW':np.array([t.dayofweek for t in df_.index]),
        'TS':df_.index,
        'n':len(df_)
    }

print("Precomputing...", flush=True)
TF_ARRS = {tf:precompute(df) for tf,df in TFS.items()}

def bt(arrs, direction, sl_atr, tp1_atr, tp2_atr, ob_str, mask=None, time_stop=None):
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    EMA=arrs['EMA'];ATR=arrs['ATR'];n=arrs['n']
    if mask is None: mask=np.ones(n,dtype=bool)
    trades=[]; pos=None
    pl=[];ph=[];pa=[];pe=[]
    for i in range(50,n):
        if pos is not None:
            # Time stop
            if time_stop and (i - pos[8]) >= time_stop:
                # Force close at C[i]
                sgn=1 if direction=='long' else -1
                pnl1=pos[3];pnl2=pos[4];q1=pos[5];q2=pos[6];e=pos[7]
                if q1>0: pnl1=(C[i]-e)*0.5*sgn
                if q2>0: pnl2=(C[i]-e)*0.5*sgn
                trades.append(pnl1+pnl2-REAL_COST)
                pos=None
            else:
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
                    pos=(pos[0],pos[1],pos[2],pnl1,pnl2,q1,q2,e,pos[8])
        # OB detection
        if i>=3:
            mc=C[i-3];mo=O[i-3];ml=L[i-3];mh=H[i-3];ma=ATR[i-3]
            if direction=='long':
                if mc<mo and not np.isnan(ma):
                    move=max(H[i-2],H[i-1],H[i])-mc
                    if move>ob_str*ma:
                        pl.append(ml);ph.append(mh);pa.append(ma);pe.append(i+30)
            else:
                if mc>mo and not np.isnan(ma):
                    move=mc-min(L[i-2],L[i-1],L[i])
                    if move>ob_str*ma:
                        pl.append(ml);ph.append(mh);pa.append(ma);pe.append(i+30)
        # Clean
        keep=[j for j,exp in enumerate(pe) if exp>i]
        if len(keep)<len(pe):
            pl=[pl[j] for j in keep];ph=[ph[j] for j in keep]
            pa=[pa[j] for j in keep];pe=[pe[j] for j in keep]
        # Entry
        if pos is None and not np.isnan(EMA[i]) and mask[i]:
            cond_t=(C[i]>EMA[i]) if direction=='long' else (C[i]<EMA[i])
            if cond_t:
                for j in range(len(pl)):
                    obl=pl[j];obh=ph[j];oba=pa[j]
                    if direction=='long':
                        ci=L[i]<=obh and C[i]>obl;cr=C[i]>O[i]
                    else:
                        ci=H[i]>=obl and C[i]<obh;cr=C[i]<O[i]
                    if ci and cr:
                        if direction=='long':
                            sl=obl-oba*sl_atr*0.5;tp1=C[i]+oba*tp1_atr;tp2=C[i]+oba*tp2_atr
                        else:
                            sl=obh+oba*sl_atr*0.5;tp1=C[i]-oba*tp1_atr;tp2=C[i]-oba*tp2_atr
                        pos=(sl,tp1,tp2,0,0,0.5,0.5,C[i],i)
                        pl.pop(j);ph.pop(j);pa.pop(j);pe.pop(j);break
    return trades

def stats(arr):
    if len(arr)==0: return None
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

# Multi-TF: at each bar of entry-TF, check if higher-TF EMA trend aligned
def make_higher_tf_mask(arrs_low, arrs_high, direction):
    """Per cada bar de TF baix, mira si higher-TF EMA50 té el bias correcte
    en aquell timestamp. Usem searchsorted."""
    ts_low = arrs_low['TS']
    ts_high = arrs_high['TS']
    ema_high = arrs_high['EMA']
    close_high = arrs_high['C']
    # For each ts_low, find the index of last ts_high <= ts_low
    idx = np.searchsorted(ts_high, ts_low, side='right') - 1
    idx = np.clip(idx, 0, len(ts_high)-1)
    bias_bull = close_high[idx] > ema_high[idx]
    if direction == 'long':
        return bias_bull
    else:
        return ~bias_bull

# Configs base
BEST = {
    'M15': (1.5, 3, 6, 2.5),
    'M30': (1.0, 3, 6, 2.0),
    'H1':  (2.0, 5, 10, 2.5),
    'H2':  (2.0, 5, 10, 2.5),  # adopt H1 best as starting point
    'H4':  (1.0, 2, 4, 2.0),
    'H6':  (1.0, 2, 4, 2.0),
    'H8':  (1.0, 2, 4, 2.0),
    'H12': (1.0, 2, 4, 2.0),
    'D1':  (1.0, 2, 4, 2.0),
}

print()
print("="*150)
print("EXTRA TFs BASELINE (sense filtres):")
print("="*150)
for tf in ['H2','H6','H8','H12','D1']:
    arrs = TF_ARRS[tf]
    sl,tp1,tp2,ob = BEST[tf]
    mask = make_session_mask(arrs, {'ASIA','LONDON','NY','OVERLAP'})
    print(f"\n{tf} (config: SL{sl} TP{tp1}/{tp2} OB{ob}, {arrs['n']} bars)")
    for d in ['long','short']:
        t = bt(arrs, d, sl, tp1, tp2, ob, mask)
        print(f"  {d}: {fmt(stats(t))}")
    both = bt(arrs,'long',sl,tp1,tp2,ob,mask)+bt(arrs,'short',sl,tp1,tp2,ob,mask)
    print(f"  BOTH: {fmt(stats(both))}")

print()
print("="*150)
print("COMBINACIONS DE FILTRES (NY + slope + LONG, etc.):")
print("="*150)

results = []

for tf in ['M15','M30','H1','H2','H4','H6','H8','H12','D1']:
    arrs = TF_ARRS[tf]
    sl,tp1,tp2,ob = BEST[tf]
    print(f"\n--- {tf} (SL{sl} TP{tp1}/{tp2} OB{ob}) ---")

    base = make_session_mask(arrs, {'ASIA','LONDON','NY','OVERLAP'})
    ny_only = make_session_mask(arrs, {'NY'})
    ldn_overlap_ny = make_session_mask(arrs, {'LONDON','OVERLAP','NY'})
    overlap_ny = make_session_mask(arrs, {'OVERLAP','NY'})
    atr_hi = make_atr_mask(arrs, 0.66, 1.0)
    slope_long = make_slope_mask(arrs, 'long', 0.5)
    slope_long_strong = make_slope_mask(arrs, 'long', 1.0)

    combos = [
        ('baseline', base, base),
        ('LONG_only', base, None),  # only long
        ('NY_LONG', ny_only, None),
        ('OVERLAP+NY_LONG', overlap_ny, None),
        ('NY+slope0.5_LONG', ny_only & slope_long, None),
        ('NY+slope1.0_LONG', ny_only & slope_long_strong, None),
        ('ATR_HIGH_BOTH', atr_hi & base, atr_hi & base),
        ('ATR_HIGH+slope0.5_LONG', atr_hi & slope_long, None),
        ('LDN+OVL+NY+slope0.5_LONG', ldn_overlap_ny & slope_long, None),
        ('NY+ATR_HIGH+slope0.5_LONG', ny_only & atr_hi & slope_long, None),
    ]
    for name, mask_l, mask_s in combos:
        l = bt(arrs,'long',sl,tp1,tp2,ob,mask_l) if mask_l is not None else []
        s = bt(arrs,'short',sl,tp1,tp2,ob,mask_s) if mask_s is not None else []
        both = l + s
        s_l = stats(l) if l else None
        s_s = stats(s) if s else None
        s_b = stats(both) if both else None
        line = f"  {name:<32}"
        if s_l: line += f" L:{fmt(s_l)[:55]:<55}"
        if s_s: line += f" S:{fmt(s_s)[:55]}"
        if not s_s and s_l: line += " (LONG only)"
        print(line)
        if s_b: results.append({'tf':tf,'variant':name,'stats':s_b,'l':s_l,'s':s_s})

print()
print("="*150)
print("MULTI-TF CONFIRMATION (entry TF aligned with higher TF EMA):")
print("="*150)
for entry_tf, higher_tf in [('M15','H1'),('M15','H4'),('M30','H4'),('M30','D1'),('H1','H4'),('H1','D1'),('H4','D1')]:
    arrs_low = TF_ARRS[entry_tf]
    arrs_high = TF_ARRS[higher_tf]
    sl,tp1,tp2,ob = BEST[entry_tf]
    base = make_session_mask(arrs_low, {'ASIA','LONDON','NY','OVERLAP'})
    htf_long = make_higher_tf_mask(arrs_low, arrs_high, 'long')
    htf_short = make_higher_tf_mask(arrs_low, arrs_high, 'short')
    l = bt(arrs_low,'long',sl,tp1,tp2,ob, base & htf_long)
    s = bt(arrs_low,'short',sl,tp1,tp2,ob, base & htf_short)
    both = l + s
    s_l = stats(l); s_s = stats(s); s_b = stats(both)
    print(f"  {entry_tf}+{higher_tf:<3} BOTH: {fmt(s_b)} | L: {fmt(s_l)} | S: {fmt(s_s)}")
    # Also LONG only with NY filter
    ny = make_session_mask(arrs_low, {'NY'})
    l2 = bt(arrs_low,'long',sl,tp1,tp2,ob, ny & htf_long)
    print(f"  {entry_tf}+{higher_tf:<3} NY_LONG only: {fmt(stats(l2))}")

print()
print("="*150)
print("TIME-STOP (close after N bars):")
print("="*150)
for tf in ['M30','H1','H4']:
    arrs = TF_ARRS[tf]
    sl,tp1,tp2,ob = BEST[tf]
    base = make_session_mask(arrs, {'ASIA','LONDON','NY','OVERLAP'})
    print(f"\n{tf}:")
    for ts_bars in [10, 20, 30, 50, None]:
        l = bt(arrs,'long',sl,tp1,tp2,ob,base, time_stop=ts_bars)
        s = bt(arrs,'short',sl,tp1,tp2,ob,base, time_stop=ts_bars)
        both = l+s
        ts_str = f"TS{ts_bars}" if ts_bars else "no_TS"
        print(f"  {ts_str:<8} BOTH: {fmt(stats(both))}")

print()
print("="*150)
print("TOP COMBINED CONFIGS (ranking per PF, n>=30):")
print("="*150)
valid = [r for r in results if r['stats'] and r['stats']['n']>=30 and r['stats']['net']>0 and r['stats']['pf']>1.10]
valid.sort(key=lambda x:-x['stats']['pf'])
for r in valid[:30]:
    s = r['stats']
    print(f"  {r['tf']:<5} {r['variant']:<35} {fmt(s)}")

print("\nDONE")
