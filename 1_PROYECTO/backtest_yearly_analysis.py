"""
ANÀLISI ANY-PER-ANY — H1 Donchian LONG
=======================================
Veure si la estratègia funciona també en anys baixistes/laterals.
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
    return df_

h1 = add_ind(aggregate(m5, '1h'))
print(f"H1: {len(h1)} bars, from {h1.index[0]} to {h1.index[-1]}")

# Calculate gold's yearly return
print()
print("="*100)
print("RENDIMENT DEL OR PER ANY (per veure quin tipus d'any és):")
print("="*100)
yearly_close = h1['close'].resample('1YE').last()
yearly_open = h1['close'].resample('1YE').first()
yearly_high = h1['close'].resample('1YE').max()
yearly_low = h1['close'].resample('1YE').min()
for year_end, c in yearly_close.items():
    yr = year_end.year
    o = h1[h1.index.year == yr]['close'].iloc[0]
    hi = h1[h1.index.year == yr]['high'].max()
    lo = h1[h1.index.year == yr]['low'].min()
    pct = (c-o)/o*100
    drawdown = (lo-hi)/hi*100
    direction = "BULL" if pct > 5 else "BEAR" if pct < -5 else "LATERAL"
    print(f"  {yr}: Open={o:.0f} Close={c:.0f} ({pct:+.1f}%) | Range {lo:.0f}-{hi:.0f} | DD intra {drawdown:.1f}% | {direction}")

# Backtest H1 Donchian LONG — yearly breakdown
def bt_donchian_long(arrs, sl_atr=2.0, tp1_atr=5.0, tp2_atr=10.0, lookback=20):
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    EMA=arrs['EMA'];ATR=arrs['ATR'];n=arrs['n'];TS=arrs['TS']
    trades=[];pos=None
    for i in range(50,n):
        if pos is not None:
            sl_h=L[i]<=pos[0];tp1_h=H[i]>=pos[1];tp2_h=H[i]>=pos[2]
            if sl_h and (tp1_h or tp2_h):
                if (O[i]-pos[0])<(pos[1]-O[i]): tp1_h=False;tp2_h=False
            sgn=1
            pnl1=pos[3];pnl2=pos[4];q1=pos[5];q2=pos[6];e=pos[7];ts_e=pos[8]
            if tp1_h and q1>0: pnl1=(pos[1]-e)*0.5*sgn;q1=0
            if tp2_h and q2>0: pnl2=(pos[2]-e)*0.5*sgn;q2=0
            if sl_h:
                if q1>0: pnl1=(pos[0]-e)*0.5*sgn;q1=0
                if q2>0: pnl2=(pos[0]-e)*0.5*sgn;q2=0
            if q1==0 and q2==0:
                trades.append({'ts':ts_e,'pnl':pnl1+pnl2-REAL_COST,'entry':e})
                pos=None
            else:
                pos=(pos[0],pos[1],pos[2],pnl1,pnl2,q1,q2,e,ts_e)
        if pos is None and not np.isnan(EMA[i]) and not np.isnan(ATR[i]) and i>=lookback:
            # Donchian breakout LONG: close > max(H of last lookback bars)
            ph = H[i-lookback:i].max()
            cond_t = C[i] > EMA[i]
            if cond_t and C[i] > ph and C[i] > O[i]:
                atr = ATR[i]
                sl = L[i-lookback:i].min()  # SL at the channel low
                # OR fixed ATR-based:
                # sl = C[i] - sl_atr*atr
                tp1 = C[i] + tp1_atr*atr
                tp2 = C[i] + tp2_atr*atr
                pos=(sl,tp1,tp2,0,0,0.5,0.5,C[i],TS[i])
    return trades

def precompute(df_):
    return {
        'O':df_['open'].values,'H':df_['high'].values,'L':df_['low'].values,'C':df_['close'].values,
        'EMA':df_['ema50'].values,'ATR':df_['atr'].values,
        'TS':df_.index,
        'n':len(df_)
    }

arrs = precompute(h1)
print()
print("="*100)
print("Test 1: SL channel-low (variable) — versió original del backtest TOP")
print("="*100)
all_trades = bt_donchian_long(arrs, sl_atr=2.0, tp1_atr=5.0, tp2_atr=10.0, lookback=20)
df_t = pd.DataFrame(all_trades)
df_t['year'] = pd.to_datetime(df_t['ts']).dt.year

print(f"Total trades: {len(df_t)} | Net: ${df_t['pnl'].sum():+.0f}")
print()
print("PER ANY:")
for yr, grp in df_t.groupby('year'):
    n = len(grp)
    net = grp['pnl'].sum()
    wins = (grp['pnl']>0).sum()
    wr = wins/n*100
    pf_p = grp[grp['pnl']>0]['pnl'].sum()
    pf_l = abs(grp[grp['pnl']<=0]['pnl'].sum())
    pf = pf_p/pf_l if pf_l else 0
    eq = grp['pnl'].cumsum().values
    peak = np.maximum.accumulate(eq)
    dd = (peak-eq).max()
    print(f"  {yr}: n={n:>3} WR{wr:>5.1f}% Net=${net:>+7.0f} PF{pf:.2f} DD${dd:.0f}")

# Now with fixed ATR SL (the original backtest)
def bt_donchian_long_fixsl(arrs, sl_atr=2.0, tp1_atr=5.0, tp2_atr=10.0, lookback=20):
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    EMA=arrs['EMA'];ATR=arrs['ATR'];n=arrs['n'];TS=arrs['TS']
    trades=[];pos=None
    for i in range(50,n):
        if pos is not None:
            sl_h=L[i]<=pos[0];tp1_h=H[i]>=pos[1];tp2_h=H[i]>=pos[2]
            if sl_h and (tp1_h or tp2_h):
                if (O[i]-pos[0])<(pos[1]-O[i]): tp1_h=False;tp2_h=False
            sgn=1
            pnl1=pos[3];pnl2=pos[4];q1=pos[5];q2=pos[6];e=pos[7];ts_e=pos[8]
            if tp1_h and q1>0: pnl1=(pos[1]-e)*0.5*sgn;q1=0
            if tp2_h and q2>0: pnl2=(pos[2]-e)*0.5*sgn;q2=0
            if sl_h:
                if q1>0: pnl1=(pos[0]-e)*0.5*sgn;q1=0
                if q2>0: pnl2=(pos[0]-e)*0.5*sgn;q2=0
            if q1==0 and q2==0:
                trades.append({'ts':ts_e,'pnl':pnl1+pnl2-REAL_COST,'entry':e})
                pos=None
            else:
                pos=(pos[0],pos[1],pos[2],pnl1,pnl2,q1,q2,e,ts_e)
        if pos is None and not np.isnan(EMA[i]) and not np.isnan(ATR[i]) and i>=lookback:
            ph = H[i-lookback:i].max()
            cond_t = C[i] > EMA[i]
            if cond_t and C[i] > ph and C[i] > O[i]:
                atr = ATR[i]
                sl_ref = L[i-lookback:i].min()  # channel low
                sl = sl_ref - atr * sl_atr * 0.5  # fixed offset below channel
                tp1 = C[i] + tp1_atr*atr
                tp2 = C[i] + tp2_atr*atr
                pos=(sl,tp1,tp2,0,0,0.5,0.5,C[i],TS[i])
    return trades

print()
print("="*100)
print("Test 2: SL fix ATR-based (variant més robust):")
print("="*100)
all_trades_v2 = bt_donchian_long_fixsl(arrs, sl_atr=2.0, tp1_atr=5.0, tp2_atr=10.0, lookback=20)
df_t2 = pd.DataFrame(all_trades_v2)
df_t2['year'] = pd.to_datetime(df_t2['ts']).dt.year

print(f"Total trades: {len(df_t2)} | Net: ${df_t2['pnl'].sum():+.0f}")
print()
print("PER ANY:")
for yr, grp in df_t2.groupby('year'):
    n = len(grp)
    net = grp['pnl'].sum()
    wins = (grp['pnl']>0).sum()
    wr = wins/n*100
    pf_p = grp[grp['pnl']>0]['pnl'].sum()
    pf_l = abs(grp[grp['pnl']<=0]['pnl'].sum())
    pf = pf_p/pf_l if pf_l else 0
    eq = grp['pnl'].cumsum().values
    peak = np.maximum.accumulate(eq)
    dd = (peak-eq).max()
    print(f"  {yr}: n={n:>3} WR{wr:>5.1f}% Net=${net:>+7.0f} PF{pf:.2f} DD${dd:.0f}")

# Test bidirectional: if we add SHORT mode when EMA200 bearish?
def bt_donchian_bidirectional(arrs, sl_atr=2.0, tp1_atr=5.0, tp2_atr=10.0, lookback=20):
    """Donchian breakout LONG OR SHORT depending on EMA200 trend."""
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    EMA=arrs['EMA'];ATR=arrs['ATR'];n=arrs['n'];TS=arrs['TS']
    trades=[];pos=None
    direction='long'
    for i in range(50,n):
        if pos is not None:
            d = pos[9]
            if d=='long':
                sl_h=L[i]<=pos[0];tp1_h=H[i]>=pos[1];tp2_h=H[i]>=pos[2]
            else:
                sl_h=H[i]>=pos[0];tp1_h=L[i]<=pos[1];tp2_h=L[i]<=pos[2]
            if sl_h and (tp1_h or tp2_h):
                if d=='long':
                    if (O[i]-pos[0])<(pos[1]-O[i]): tp1_h=False;tp2_h=False
                else:
                    if (pos[0]-O[i])<(O[i]-pos[1]): tp1_h=False;tp2_h=False
            sgn=1 if d=='long' else -1
            pnl1=pos[3];pnl2=pos[4];q1=pos[5];q2=pos[6];e=pos[7];ts_e=pos[8]
            if tp1_h and q1>0: pnl1=(pos[1]-e)*0.5*sgn;q1=0
            if tp2_h and q2>0: pnl2=(pos[2]-e)*0.5*sgn;q2=0
            if sl_h:
                if q1>0: pnl1=(pos[0]-e)*0.5*sgn;q1=0
                if q2>0: pnl2=(pos[0]-e)*0.5*sgn;q2=0
            if q1==0 and q2==0:
                trades.append({'ts':ts_e,'pnl':pnl1+pnl2-REAL_COST,'entry':e,'dir':d})
                pos=None
            else:
                pos=(pos[0],pos[1],pos[2],pnl1,pnl2,q1,q2,e,ts_e,d)
        if pos is None and not np.isnan(EMA[i]) and not np.isnan(ATR[i]) and i>=lookback:
            atr = ATR[i]
            # Determine bias from EMA50 (we already checked EMA50 trend)
            if C[i] > EMA[i]:
                # Possible long
                ph = H[i-lookback:i].max()
                if C[i] > ph and C[i] > O[i]:
                    sl_ref = L[i-lookback:i].min()
                    sl = sl_ref - atr * sl_atr * 0.5
                    tp1 = C[i] + tp1_atr*atr
                    tp2 = C[i] + tp2_atr*atr
                    pos=(sl,tp1,tp2,0,0,0.5,0.5,C[i],TS[i],'long')
            elif C[i] < EMA[i]:
                # Possible short
                pl = L[i-lookback:i].min()
                if C[i] < pl and C[i] < O[i]:
                    sl_ref = H[i-lookback:i].max()
                    sl = sl_ref + atr * sl_atr * 0.5
                    tp1 = C[i] - tp1_atr*atr
                    tp2 = C[i] - tp2_atr*atr
                    pos=(sl,tp1,tp2,0,0,0.5,0.5,C[i],TS[i],'short')
    return trades

print()
print("="*100)
print("Test 3: Donchian BIDIRECCIONAL — LONG OR SHORT depenent EMA50 bias:")
print("="*100)
all_trades_v3 = bt_donchian_bidirectional(arrs, sl_atr=2.0, tp1_atr=5.0, tp2_atr=10.0, lookback=20)
df_t3 = pd.DataFrame(all_trades_v3)
df_t3['year'] = pd.to_datetime(df_t3['ts']).dt.year

print(f"Total trades: {len(df_t3)} | Net: ${df_t3['pnl'].sum():+.0f}")
print(f"  LONG: {(df_t3['dir']=='long').sum()} | SHORT: {(df_t3['dir']=='short').sum()}")
print()
print("PER ANY (bidireccional):")
for yr, grp in df_t3.groupby('year'):
    n = len(grp)
    n_l = (grp['dir']=='long').sum()
    n_s = (grp['dir']=='short').sum()
    net = grp['pnl'].sum()
    net_l = grp[grp['dir']=='long']['pnl'].sum()
    net_s = grp[grp['dir']=='short']['pnl'].sum()
    wins = (grp['pnl']>0).sum()
    wr = wins/n*100
    pf_p = grp[grp['pnl']>0]['pnl'].sum()
    pf_l = abs(grp[grp['pnl']<=0]['pnl'].sum())
    pf = pf_p/pf_l if pf_l else 0
    print(f"  {yr}: n={n:>3} (L:{n_l:>3} S:{n_s:>3}) WR{wr:>5.1f}% Net=${net:>+7.0f} (L:${net_l:+.0f} S:${net_s:+.0f}) PF{pf:.2f}")

# 2013-2018 Was a notorious bearish/lateral period for gold
# Let's see how strategy would do in bearish-like sub-periods of our 5y dataset

print()
print("="*100)
print("STRESS TEST — Sub-periodes baixistes/laterals dins de la data 5y:")
print("="*100)

# Find drawdown periods of gold
prices = h1['close']
peak = prices.expanding().max()
dd_pct = (prices - peak) / peak * 100

# Find significant drawdown periods (>5%)
in_dd = dd_pct < -5
periods = []
start = None
for ts, val in in_dd.items():
    if val and start is None:
        start = ts
    elif not val and start is not None:
        periods.append((start, ts))
        start = None

print(f"Trobats {len(periods)} sub-periodes amb DD>5% del peak local:")
for st, en in periods[:10]:
    sub = df_t2[(pd.to_datetime(df_t2['ts'])>=st) & (pd.to_datetime(df_t2['ts'])<=en)]
    if len(sub) > 0:
        n = len(sub)
        net = sub['pnl'].sum()
        wins = (sub['pnl']>0).sum()
        days = (en-st).days
        print(f"  {st.date()} -> {en.date()} ({days}d) | trades={n} WR{wins/n*100:.0f}% Net=${net:+.0f}")
    else:
        print(f"  {st.date()} -> {en.date()}: cap trade")

print("\nDONE")
