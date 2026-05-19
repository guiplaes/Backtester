"""Inside Bar BO sobre EURUSD 5y (mateixa estratègia que XAUUSD).

EURUSD: pip = 0.0001, $1 PnL per 1 unit per 0.0001 move
Adaptem distàncies a la volatilitat de EURUSD (typical ATR ~0.0005-0.0015)
"""
import pandas as pd
import numpy as np

CSV = "eurusd_m5_5y.csv"
ATR_LEN = 14; EMA_LEN = 50; VOL_LEN = 20
# EURUSD costs: spread ~1 pip, slippage 0.5 pip, commission 0.5
COMMISSION = 0.5; SPREAD = 0.0001 * 1   # 1 pip
SLIPPAGE = 0.0001 * 0.5
SL_M = 1.5; TP1_M = 15; TP2_M = 30

print("Loading EURUSD 5y...", flush=True)
df = pd.read_csv(CSV, index_col=0, parse_dates=True)
df.index = pd.to_datetime(df.index, utc=True)
df.columns = [c.lower() for c in df.columns]
if 'tick_volume' not in df.columns: df['tick_volume'] = df['volume']
print(f"{len(df)} bars from {df.index[0]} to {df.index[-1]}", flush=True)

print("Indicators...", flush=True)
df['ema50'] = df['close'].ewm(span=EMA_LEN, adjust=False).mean()
df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
hl = df['high']-df['low']; hc = (df['high']-df['close'].shift()).abs(); lc = (df['low']-df['close'].shift()).abs()
tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
df['atr'] = tr.ewm(alpha=1/ATR_LEN, adjust=False).mean()
df['vol_avg'] = df['tick_volume'].rolling(VOL_LEN).mean()
df['inside'] = (df['high']<df['high'].shift(1)) & (df['low']>df['low'].shift(1))
df['ema200_60d_ago'] = df['ema200'].shift(60*24*12)
df['slope_60d'] = (df['ema200'] - df['ema200_60d_ago']) / df['ema200_60d_ago'] * 100

# EURUSD pnl is per pip. 1 oz/contract is irrelevant. Use raw price diff × 100000 to get $ per std lot
# But for comparison let's use direct USD per 1 std lot = $10 per pip
PIP = 0.0001
PIP_VALUE = 10  # USD per pip per standard lot

def backtest(slope_thresh=None, asia_only=True, long_only=True, skip_wed=True):
    trades = []; pos = None
    for i in range(60*24*12+5, len(df)):
        bar = df.iloc[i]; ts = df.index[i]
        if pos is not None:
            sl_h = bar['low'] <= pos['sl'] if pos['side']=='L' else bar['high'] >= pos['sl']
            tp1_h = bar['high'] >= pos['tp1'] if pos['side']=='L' else bar['low'] <= pos['tp1']
            tp2_h = bar['high'] >= pos['tp2'] if pos['side']=='L' else bar['low'] <= pos['tp2']
            sgn = 1 if pos['side']=='L' else -1
            if sl_h and (tp1_h or tp2_h):
                if pos['side']=='L':
                    if (bar['open']-pos['sl']) < (pos['tp1']-bar['open']): tp1_h=False; tp2_h=False
                else:
                    if (pos['sl']-bar['open']) < (bar['open']-pos['tp1']): tp1_h=False; tp2_h=False
            if tp1_h and pos['q1']>0: pos['pnl1'] = (pos['tp1']-pos['e'])*0.5*sgn - SLIPPAGE*0.5; pos['q1']=0
            if tp2_h and pos['q2']>0: pos['pnl2'] = (pos['tp2']-pos['e'])*0.5*sgn - SLIPPAGE*0.5; pos['q2']=0
            if sl_h:
                if pos['q1']>0: pos['pnl1'] = (pos['sl']-pos['e'])*0.5*sgn - SLIPPAGE*0.5; pos['q1']=0
                if pos['q2']>0: pos['pnl2'] = (pos['sl']-pos['e'])*0.5*sgn - SLIPPAGE*0.5; pos['q2']=0
            if pos['q1']==0 and pos['q2']==0:
                pip_pnl = pos.get('pnl1',0)+pos.get('pnl2',0)
                # Convert price diff to $ via PIP_VALUE per std lot = $10 per pip
                # 1 pip = 0.0001 in price. $ pnl = pip_pnl/0.0001 × 10 / 100000 (for 1 unit)
                # Actually let's use: pnl_pips = pip_pnl / PIP, $ = pnl_pips × $1 (for 0.01 lot = 1k EUR)
                # For consistency with XAUUSD ($1 per oz move), use 1 unit = 0.01 lot EURUSD ($1 per pip)
                pnl_dollars = pip_pnl / PIP * 1.0 - 1.0 - 0.5  # commission $1 + spread $0.5 (1 pip × $1.5 - already in slip)
                pnl_dollars = pip_pnl / PIP - 1.5  # subtract round-trip cost in pips × $1 = $1.5
                trades.append({'ts': pos['ts'], 'pnl': pnl_dollars, 'side': pos['side']})
                pos = None
        if pos is None and i>=2:
            prev = df.iloc[i-1]; pp = df.iloc[i-2]
            if not prev['inside']: continue
            if pd.isna(bar['ema50']) or pd.isna(bar['atr']) or pd.isna(bar['vol_avg']): continue
            if bar['tick_volume'] <= bar['vol_avg']*1.3: continue
            if skip_wed and ts.dayofweek == 2: continue
            if asia_only and not (0 <= ts.hour <= 6): continue
            if slope_thresh is not None:
                sl = bar.get('slope_60d')
                if pd.isna(sl) or sl < slope_thresh: continue
            atr = bar['atr']; e = bar['close']
            mh = pp['high']; ml = pp['low']
            if bar['close'] > bar['ema50'] and bar['high']>mh and bar['close']>mh:
                pos = {'side':'L','e':e,'ts':ts,'sl':e-atr*SL_M,'tp1':e+atr*TP1_M,'tp2':e+atr*TP2_M,'q1':0.5,'q2':0.5}
            elif (not long_only) and bar['close'] < bar['ema50'] and bar['low']<ml and bar['close']<ml:
                pos = {'side':'S','e':e,'ts':ts,'sl':e+atr*SL_M,'tp1':e-atr*TP1_M,'tp2':e-atr*TP2_M,'q1':0.5,'q2':0.5}
    return trades

def stats(trades, name):
    if not trades: print(f"{name:>50}: 0 trades"); return
    arr = np.array([t['pnl'] for t in trades])
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    print(f"{name:>50}: n={n:>4} | WR {w/n*100:>5.1f}% | Net ${net:>+9.2f} | PF {pf:>5.2f} | DD ${dd:>7.2f}", flush=True)

print()
print("="*120)
print("EURUSD 5y — Inside Bar BO variants:")
print("="*120)

stats(backtest(asia_only=True, long_only=True, skip_wed=True), "LONG+Asia+skipWed (XAUUSD config)")
stats(backtest(asia_only=False, long_only=True, skip_wed=True), "LONG+skipWed (no Asia)")
stats(backtest(asia_only=True, long_only=False, skip_wed=True), "Both directions+Asia")
stats(backtest(asia_only=False, long_only=False, skip_wed=False), "Both directions, no time filter")
stats(backtest(slope_thresh=10.0, asia_only=True, long_only=True, skip_wed=True), "+ Regime slope >=10%")
stats(backtest(slope_thresh=5.0, asia_only=True, long_only=True, skip_wed=True), "+ Regime slope >=5%")
stats(backtest(slope_thresh=3.0, asia_only=True, long_only=True, skip_wed=True), "+ Regime slope >=3%")
stats(backtest(slope_thresh=-3.0, asia_only=True, long_only=False, skip_wed=True), "Both + slope (no extreme regime)")

# Best per year
print("\nBest config per year (LONG+Asia+skipWed+slope10):", flush=True)
trades = backtest(slope_thresh=10.0, asia_only=True, long_only=True, skip_wed=True)
tdf = pd.DataFrame(trades)
if len(tdf):
    tdf['year'] = pd.to_datetime(tdf['ts']).dt.year
    for yr in sorted(tdf['year'].unique()):
        ydf = tdf[tdf['year']==yr]
        arr = ydf['pnl'].values
        n=len(arr); w=(arr>0).sum(); net=arr.sum()
        pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
        pf=pf_p/pf_l if pf_l else 0
        print(f"  {yr}: n={n:>3} | WR {w/n*100 if n else 0:>5.1f}% | Net ${net:>+9.2f} | PF {pf:>5.2f}", flush=True)
