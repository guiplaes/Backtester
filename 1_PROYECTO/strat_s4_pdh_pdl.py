"""S4: PDH/PDL Sweep + Reversal - simpler, faster."""
import pandas as pd
import numpy as np

CSV = "xauusd_m5_5y.csv"
COMMISSION = 0.5; SPREAD = 0.50; SLIPPAGE = 0.20

print("Loading...", flush=True)
df = pd.read_csv(CSV, index_col=0, parse_dates=True)
df.index = pd.to_datetime(df.index, utc=True)
df.columns = [c.lower() for c in df.columns]
if 'tick_volume' not in df.columns: df['tick_volume'] = df['volume']
hl = df['high']-df['low']; hc = (df['high']-df['close'].shift()).abs(); lc = (df['low']-df['close'].shift()).abs()
tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
df['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
df['date'] = df.index.date
print(f"{len(df)} bars", flush=True)

# Daily H/L
print("Daily H/L...", flush=True)
daily = df.groupby('date').agg(d_high=('high','max'), d_low=('low','min')).reset_index()
daily['pdh'] = daily['d_high'].shift(1)
daily['pdl'] = daily['d_low'].shift(1)
daily_levels = {row['date']: (row['pdh'], row['pdl']) for _, row in daily.iterrows() if not pd.isna(row['pdh'])}
print(f"{len(daily_levels)} days", flush=True)

def stats(trades, name):
    if not trades: print(f"{name:>50}: NO trades"); return
    arr = np.array([t['pnl'] for t in trades])
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    print(f"{name:>50}: n={n:>4} | WR {w/n*100:>5.1f}% | Net ${net:>+9.2f} | PF {pf:>5.2f} | DD ${dd:>7.2f}", flush=True)

def s4(sl_atr=0.5, tp1_atr=8, tp2_atr=15, time_window=(8, 20)):
    trades = []; pos = None
    cur_date = None; pdh = pdl = None; fired_today = 0
    for i in range(50, len(df)):
        bar = df.iloc[i]; ts = df.index[i]; d = ts.date()
        if d != cur_date:
            cur_date = d
            if d in daily_levels:
                pdh, pdl = daily_levels[d]; fired_today = 0
            else:
                pdh = pdl = None
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
                tp = pos.get('pnl1',0)+pos.get('pnl2',0)-COMMISSION*2-SPREAD
                trades.append({'ts': pos['ts'], 'pnl': tp, 'side': pos['side']})
                pos = None
        if pos is None and pdh is not None and fired_today < 2 and time_window[0] <= ts.hour < time_window[1]:
            atr = bar['atr']; e = bar['close']
            # Sweep PDH → SHORT
            if bar['high'] > pdh and bar['close'] < pdh and bar['close'] < bar['open']:
                pos = {'side':'S','e':e,'ts':ts,'sl':bar['high']+atr*sl_atr,
                       'tp1':e-atr*tp1_atr,'tp2':e-atr*tp2_atr,'q1':0.5,'q2':0.5}
                fired_today += 1
            elif bar['low'] < pdl and bar['close'] > pdl and bar['close'] > bar['open']:
                pos = {'side':'L','e':e,'ts':ts,'sl':bar['low']-atr*sl_atr,
                       'tp1':e+atr*tp1_atr,'tp2':e+atr*tp2_atr,'q1':0.5,'q2':0.5}
                fired_today += 1
    return trades

print("\nS4 PDH/PDL Sweep variants:", flush=True)
trades = s4(); stats(trades, "Default (8R/15R, 8-20 UTC)")
trades = s4(tp1_atr=5, tp2_atr=10); stats(trades, "5R/10R")
trades = s4(tp1_atr=12, tp2_atr=24); stats(trades, "12R/24R")
trades = s4(time_window=(13,17)); stats(trades, "London/NY only (13-17)")
trades = s4(sl_atr=1.0); stats(trades, "Wider SL (1xATR)")

# Best: per year
trades = s4()
tdf = pd.DataFrame(trades)
if len(tdf):
    tdf['year'] = pd.to_datetime(tdf['ts']).dt.year
    print("\nPer year (default):", flush=True)
    for yr in sorted(tdf['year'].unique()):
        ydf = tdf[tdf['year']==yr]
        arr = ydf['pnl'].values
        n=len(arr); w=(arr>0).sum(); net=arr.sum()
        pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
        pf=pf_p/pf_l if pf_l else 0
        print(f"  {yr}: n={n:>3} | WR {w/n*100 if n else 0:>5.1f}% | Net ${net:>+9.2f} | PF {pf:>5.2f}", flush=True)
