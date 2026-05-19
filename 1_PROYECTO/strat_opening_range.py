"""
Opening Range Breakout (ORB) — strategy classica.

Lògica:
- Definir "opening range" del primer hour de London (07:00-08:00 UTC)
- Si preu trenca high del range entre 08:00-15:00 UTC → BUY
- Si preu trenca low del range → SELL
- SL: 0.5×ATR més enllà del range
- TP1: ràng × 1.0 (1R), TP2: ràng × 2.0 (2R)
- Tanca al final del dia si no s'ha tocat ni TP ni SL

Aplicat a XAUUSD M5 5y.
"""
import pandas as pd
import numpy as np

CSV = "xauusd_m5_5y.csv"
COMMISSION = 0.5; SPREAD = 0.50; SLIPPAGE = 0.20

print("Loading 5y...", flush=True)
df = pd.read_csv(CSV, index_col=0, parse_dates=True)
df.index = pd.to_datetime(df.index, utc=True)
df.columns = [c.lower() for c in df.columns]
if 'tick_volume' not in df.columns: df['tick_volume'] = df['volume']
print(f"{len(df)} bars", flush=True)

# ATR
hl = df['high']-df['low']; hc = (df['high']-df['close'].shift()).abs(); lc = (df['low']-df['close'].shift()).abs()
tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
df['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
df['date'] = df.index.date

# Opening range = high/low between 07:00-08:00 UTC each day
print("Computing daily OR...", flush=True)
or_data = {}
for date in pd.unique(df['date']):
    day_df = df[df['date'] == date]
    or_window = day_df.between_time('07:00', '07:55')
    if len(or_window) < 5:
        continue
    or_high = or_window['high'].max()
    or_low = or_window['low'].min()
    or_range = or_high - or_low
    or_data[date] = (or_high, or_low, or_range)

print(f"OR days: {len(or_data)}", flush=True)

def backtest(direction='both', sl_atr=0.5, tp1_r=1.0, tp2_r=2.0, mid_close_hour=21):
    """Run ORB strategy. direction: 'both', 'long', 'short'."""
    trades = []; pos = None
    cur_date = None; or_h = or_l = or_r = None; fired_today = False

    for i in range(20, len(df)):
        ts = df.index[i]; bar = df.iloc[i]
        d = ts.date()

        # New day setup
        if d != cur_date:
            cur_date = d
            if d in or_data:
                or_h, or_l, or_r = or_data[d]
                fired_today = False
            else:
                or_h = or_l = or_r = None

        # Close trade at end of day if still open
        if pos is not None and ts.hour >= mid_close_hour:
            cur = bar['close']
            sgn = 1 if pos['side'] == 'L' else -1
            if pos['q1']>0: pos['pnl1'] = (cur - pos['e'])*0.5*sgn - SLIPPAGE*0.5; pos['q1']=0
            if pos['q2']>0: pos['pnl2'] = (cur - pos['e'])*0.5*sgn - SLIPPAGE*0.5; pos['q2']=0

        # Manage open position
        if pos is not None:
            sgn = 1 if pos['side']=='L' else -1
            sl_h = bar['low'] <= pos['sl'] if pos['side']=='L' else bar['high'] >= pos['sl']
            tp1_h = bar['high'] >= pos['tp1'] if pos['side']=='L' else bar['low'] <= pos['tp1']
            tp2_h = bar['high'] >= pos['tp2'] if pos['side']=='L' else bar['low'] <= pos['tp2']
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

        # Look for entries
        if pos is None and or_h is not None and not fired_today and 8 <= ts.hour < 15:
            atr = bar['atr']; e = bar['close']
            # Breakout above OR high → LONG
            if direction in ('both','long') and bar['high'] > or_h and bar['close'] > or_h:
                sl = or_l - atr*sl_atr
                tp1 = e + or_r*tp1_r
                tp2 = e + or_r*tp2_r
                pos = {'side':'L','e':e,'ts':ts,'sl':sl,'tp1':tp1,'tp2':tp2,'q1':0.5,'q2':0.5}
                fired_today = True
            # Breakout below OR low → SHORT
            elif direction in ('both','short') and bar['low'] < or_l and bar['close'] < or_l:
                sl = or_h + atr*sl_atr
                tp1 = e - or_r*tp1_r
                tp2 = e - or_r*tp2_r
                pos = {'side':'S','e':e,'ts':ts,'sl':sl,'tp1':tp1,'tp2':tp2,'q1':0.5,'q2':0.5}
                fired_today = True

    return trades

def stats(trades, name):
    if not trades: print(f"{name:>40}: NO trades"); return
    arr = np.array([t['pnl'] for t in trades])
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    print(f"{name:>40}: n={n:>4} | WR {w/n*100:>5.1f}% | Net ${net:>+9.2f} | PF {pf:>5.2f} | DD ${dd:>7.2f}", flush=True)

print()
print("="*100)
print("ORB on XAUUSD 5y:")
print("="*100)
trades = backtest(direction='both')
stats(trades, "Both directions, R 1:2")

trades = backtest(direction='long')
stats(trades, "LONG only")

trades = backtest(direction='short')
stats(trades, "SHORT only")

trades = backtest(direction='both', tp1_r=0.5, tp2_r=1.5)
stats(trades, "Both, smaller TP1 (0.5R/1.5R)")

trades = backtest(direction='both', tp1_r=1.5, tp2_r=3.0)
stats(trades, "Both, wider TP (1.5R/3R)")

# Per year
trades_long = backtest(direction='long')
if trades_long:
    print("\nPer year (LONG only):", flush=True)
    tdf = pd.DataFrame(trades_long)
    tdf['year'] = pd.to_datetime(tdf['ts']).dt.year
    for yr in sorted(tdf['year'].unique()):
        ydf = tdf[tdf['year']==yr]
        arr = ydf['pnl'].values
        n=len(arr); w=(arr>0).sum(); net=arr.sum()
        pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
        pf=pf_p/pf_l if pf_l else 0
        print(f"  {yr}: n={n:>3} | WR {w/n*100 if n else 0:>5.1f}% | Net ${net:>+9.2f} | PF {pf:>5.2f}", flush=True)
