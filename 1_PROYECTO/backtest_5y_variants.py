"""5-year backtest of multiple variants."""
import pandas as pd
import numpy as np

CSV = "xauusd_m5_5y.csv"
ATR_LEN = 14; EMA_LEN = 50; VOL_LEN = 20
COMMISSION = 0.5; SPREAD = 0.50; SLIPPAGE = 0.20

def load():
    df = pd.read_csv(CSV, index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]
    if 'volume' in df.columns and 'tick_volume' not in df.columns:
        df['tick_volume'] = df['volume']
    return df

def compute(df):
    df = df.copy()
    df['ema50'] = df['close'].ewm(span=EMA_LEN, adjust=False).mean()
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    hl = df['high']-df['low']; hc = (df['high']-df['close'].shift()).abs(); lc = (df['low']-df['close'].shift()).abs()
    tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
    df['atr'] = tr.ewm(alpha=1/ATR_LEN, adjust=False).mean()
    df['vol_avg'] = df['tick_volume'].rolling(VOL_LEN).mean()
    df['inside'] = (df['high']<df['high'].shift(1)) & (df['low']>df['low'].shift(1))
    return df

def backtest(df, sl_m=1.5, tp1_m=15, tp2_m=30, vol_m=1.3,
             long_only=True, skip_wed=True, asia_only=True, ema200_filter=False):
    trades = []; pos = None
    for i in range(max(EMA_LEN, ATR_LEN, 200)+5, len(df)):
        bar = df.iloc[i]; ts = df.index[i]
        if pos is not None:
            sl_h = bar['low'] <= pos['sl'] if pos['side']=='L' else bar['high'] >= pos['sl']
            tp1_h = bar['high'] >= pos['tp1'] if pos['side']=='L' else bar['low'] <= pos['tp1']
            tp2_h = bar['high'] >= pos['tp2'] if pos['side']=='L' else bar['low'] <= pos['tp2']
            if sl_h and (tp1_h or tp2_h):
                if pos['side']=='L':
                    if (bar['open']-pos['sl']) < (pos['tp1']-bar['open']): tp1_h=False; tp2_h=False
                else:
                    if (pos['sl']-bar['open']) < (bar['open']-pos['tp1']): tp1_h=False; tp2_h=False
            sgn = 1 if pos['side']=='L' else -1
            if tp1_h and pos['q1']>0: pos['pnl1'] = (pos['tp1']-pos['e'])*0.5*sgn - SLIPPAGE*0.5; pos['q1']=0
            if tp2_h and pos['q2']>0: pos['pnl2'] = (pos['tp2']-pos['e'])*0.5*sgn - SLIPPAGE*0.5; pos['q2']=0
            if sl_h:
                if pos['q1']>0: pos['pnl1'] = (pos['sl']-pos['e'])*0.5*sgn - SLIPPAGE*0.5; pos['q1']=0
                if pos['q2']>0: pos['pnl2'] = (pos['sl']-pos['e'])*0.5*sgn - SLIPPAGE*0.5; pos['q2']=0
            if pos['q1']==0 and pos['q2']==0:
                tp = pos.get('pnl1',0)+pos.get('pnl2',0)-COMMISSION*2-SPREAD
                trades.append({'ts': pos['ts'], 'pnl': tp, 'side': pos['side']})
                pos = None
        if pos is None and i>=2:
            prev = df.iloc[i-1]; pp = df.iloc[i-2]
            if not prev['inside']: continue
            if pd.isna(bar['ema50']) or pd.isna(bar['atr']) or pd.isna(bar['vol_avg']): continue
            if bar['tick_volume'] <= bar['vol_avg']*vol_m: continue
            if skip_wed and ts.dayofweek == 2: continue
            if asia_only and not (0 <= ts.hour <= 6): continue
            atr = bar['atr']; e = bar['close']
            mh = pp['high']; ml = pp['low']
            # LONG signal
            if bar['close'] > bar['ema50']:
                if ema200_filter and bar['ema50'] <= bar['ema200']: pass  # block
                elif bar['high']>mh and bar['close']>mh:
                    pos = {'side':'L','e':e,'ts':ts,'sl':e-atr*sl_m,'tp1':e+atr*tp1_m,'tp2':e+atr*tp2_m,'q1':0.5,'q2':0.5}
                    continue
            # SHORT signal
            if not long_only and bar['close'] < bar['ema50']:
                if ema200_filter and bar['ema50'] >= bar['ema200']: pass
                elif bar['low']<ml and bar['close']<ml:
                    pos = {'side':'S','e':e,'ts':ts,'sl':e+atr*sl_m,'tp1':e-atr*tp1_m,'tp2':e-atr*tp2_m,'q1':0.5,'q2':0.5}
    return trades

def stats(trades, name):
    if not trades: print(f"{name:>40}: NO trades"); return None
    arr = np.array([t['pnl'] for t in trades])
    n = len(arr); w = (arr>0).sum(); net = arr.sum()
    pf_p = arr[arr>0].sum(); pf_l = abs(arr[arr<=0].sum())
    pf = pf_p/pf_l if pf_l else 0
    eq = np.cumsum(arr); peak = np.maximum.accumulate(eq); dd = (peak-eq).max()
    avg = arr.mean()
    print(f"{name:>40}: n={n:>4} | WR {w/n*100:>5.1f}% | Net ${net:>+9.2f} | PF {pf:>5.2f} | DD ${dd:>7.2f}")
    return {'n':n,'wr':w/n*100,'net':net,'pf':pf,'dd':dd}

print("Loading 5y...", flush=True)
df = load(); df = compute(df)
print(f"{len(df)} bars\n", flush=True)

print("="*120)
print("5-YEAR VARIANTS:")
print("="*120)

variants = [
    ("Baseline (LONG+Wed+Asia 15/30)", dict(long_only=True, skip_wed=True, asia_only=True, sl_m=1.5, tp1_m=15, tp2_m=30)),
    ("LONG only no time filter", dict(long_only=True, skip_wed=False, asia_only=False)),
    ("LONG + skip Wed (no Asia)", dict(long_only=True, skip_wed=True, asia_only=False)),
    ("Both directions + Asia + skipWed", dict(long_only=False, skip_wed=True, asia_only=True)),
    ("LONG + Asia only", dict(long_only=True, skip_wed=False, asia_only=True)),
    ("LONG + skip Wed + EMA50>EMA200", dict(long_only=True, skip_wed=True, asia_only=False, ema200_filter=True)),
    ("Tighter SL 1.0 + TP 10/20", dict(sl_m=1.0, tp1_m=10, tp2_m=20)),
    ("Wider TPs 1:20/40 SL 1.5", dict(sl_m=1.5, tp1_m=20, tp2_m=40)),
    ("Vol filter 1.5x", dict(vol_m=1.5)),
    ("Vol filter 2.0x", dict(vol_m=2.0)),
    ("LONG + EMA200 trend + Asia", dict(long_only=True, skip_wed=True, asia_only=True, ema200_filter=True)),
]

for name, kw in variants:
    t = backtest(df, **kw)
    stats(t, name)

# Walk-forward best variant on each year
print()
print("="*120)
print("PER-YEAR breakdown of baseline (regime sensitivity):")
print("="*120)
t_base = backtest(df, long_only=True, skip_wed=True, asia_only=True)
tdf = pd.DataFrame(t_base)
if len(tdf):
    tdf['year'] = pd.to_datetime(tdf['ts']).dt.year
    for yr in sorted(tdf['year'].unique()):
        ydf = tdf[tdf['year']==yr]
        arr = ydf['pnl'].values
        n=len(arr); w=(arr>0).sum(); net=arr.sum()
        pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
        pf=pf_p/pf_l if pf_l else 0
        print(f"  {yr}: n={n:>3} | WR {w/n*100 if n else 0:>5.1f}% | Net ${net:>+9.2f} | PF {pf:>5.2f}")
