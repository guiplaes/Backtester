"""
4 noves estratègies amb lògica de sessions + pre-filtres.

S1: Asia Range Sweep + London Reversal (ICT/SMC)
S2: Compression Breakout (low ATR percentile → expansion)
S3: Multi-TF Confluence Stack (D1+H1+M5)
S4: PDH/PDL Sweep + Reversal

Tots sobre XAUUSD M5 5 anys.
"""
import pandas as pd
import numpy as np

CSV = "xauusd_m5_5y.csv"
COMMISSION = 0.5; SPREAD = 0.50; SLIPPAGE = 0.20

print("Loading...", flush=True)
df = pd.read_csv(CSV, index_col=0, parse_dates=True)
df.index = pd.to_datetime(df.index, utc=True)
df.columns = [c.lower() for c in df.columns]
if 'tick_volume' not in df.columns: df['tick_volume'] = df['volume']
print(f"{len(df)} bars", flush=True)

print("Indicators...", flush=True)
df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
hl = df['high']-df['low']; hc = (df['high']-df['close'].shift()).abs(); lc = (df['low']-df['close'].shift()).abs()
tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
df['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
df['atr_h1'] = df['atr'] * 12  # approximated H1 ATR
df['date'] = df.index.date
print("Done", flush=True)

# Pre-compute Asia range and PDH/PDL per day
print("Computing daily session levels...", flush=True)
daily_levels = {}
for date in pd.unique(df['date']):
    day_df = df[df['date'] == date]
    asia = day_df.between_time('00:00', '06:55')
    if len(asia) >= 20:
        daily_levels[date] = {
            'asia_high': asia['high'].max(),
            'asia_low': asia['low'].min(),
            'asia_range': asia['high'].max() - asia['low'].min(),
            'd_high': day_df['high'].max(),
            'd_low': day_df['low'].min(),
        }

# Previous day high/low
sorted_dates = sorted(daily_levels.keys())
for i, d in enumerate(sorted_dates):
    if i > 0:
        prev = daily_levels[sorted_dates[i-1]]
        daily_levels[d]['pdh'] = prev['d_high']
        daily_levels[d]['pdl'] = prev['d_low']

print(f"{len(daily_levels)} days", flush=True)

# ATR percentile (rolling 30 days)
print("ATR percentile...", flush=True)
df['atr_rank'] = df['atr'].rolling(30*24*12, min_periods=100).rank(pct=True)

# Daily trend (D1 close > D1 close 20 days ago)
daily_close = df.groupby('date')['close'].last()
daily_close_20d = daily_close.shift(20)
daily_trend = (daily_close > daily_close_20d).to_dict()

def stats(trades, name):
    if not trades: print(f"{name:>50}: NO trades"); return None
    arr = np.array([t['pnl'] for t in trades])
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    print(f"{name:>50}: n={n:>4} | WR {w/n*100:>5.1f}% | Net ${net:>+9.2f} | PF {pf:>5.2f} | DD ${dd:>7.2f}", flush=True)
    return {'n':n,'pf':pf,'net':net}

# ============================================================
# S1: Asia Range Sweep + London Reversal
# ============================================================
def s1_asia_sweep_reversal(sl_atr=1.0, tp1_r=1.0, tp2_r=2.0, day_close_hour=15):
    """During London open (07-10 UTC), if price breaks Asia high then closes back inside,
    enter SHORT (sweep failed). Mirror for low → LONG."""
    trades = []; pos = None
    cur_date = None; al = ah = None; fired_today = False
    for i in range(50, len(df)):
        bar = df.iloc[i]; ts = df.index[i]; d = ts.date()
        if d != cur_date:
            cur_date = d
            if d in daily_levels:
                al = daily_levels[d]['asia_low']
                ah = daily_levels[d]['asia_high']
                fired_today = False
            else:
                al = ah = None
        # Manage position
        if pos is not None:
            if ts.hour >= day_close_hour:
                cur = bar['close']
                sgn = 1 if pos['side']=='L' else -1
                if pos['q1']>0: pos['pnl1'] = (cur-pos['e'])*0.5*sgn - SLIPPAGE*0.5; pos['q1']=0
                if pos['q2']>0: pos['pnl2'] = (cur-pos['e'])*0.5*sgn - SLIPPAGE*0.5; pos['q2']=0
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
                trades.append({'ts': pos['ts'], 'pnl': tp})
                pos = None
        # Entry: London open hours 07-09 UTC
        if pos is None and ah is not None and not fired_today and 7 <= ts.hour < 10:
            atr = bar['atr']; e = bar['close']
            asia_range = ah - al
            # Sweep above Asia high → SHORT (failed breakout)
            if bar['high'] > ah and bar['close'] < ah and bar['close'] < bar['open']:
                sl = bar['high'] + atr*sl_atr*0.3  # tight SL just above sweep
                tp1 = e - asia_range * tp1_r
                tp2 = e - asia_range * tp2_r
                pos = {'side':'S','e':e,'ts':ts,'sl':sl,'tp1':tp1,'tp2':tp2,'q1':0.5,'q2':0.5}
                fired_today = True
            # Sweep below Asia low → LONG
            elif bar['low'] < al and bar['close'] > al and bar['close'] > bar['open']:
                sl = bar['low'] - atr*sl_atr*0.3
                tp1 = e + asia_range * tp1_r
                tp2 = e + asia_range * tp2_r
                pos = {'side':'L','e':e,'ts':ts,'sl':sl,'tp1':tp1,'tp2':tp2,'q1':0.5,'q2':0.5}
                fired_today = True
    return trades

# ============================================================
# S2: Compression Breakout
# ============================================================
def s2_compression_breakout(atr_max_pct=0.20, sl_atr=1.0, tp1_atr=10, tp2_atr=20):
    """When ATR percentile is <20% (very low vol), wait for breakout from prior bar range
    in trend direction (close > EMA200)."""
    trades = []; pos = None
    for i in range(300, len(df)):
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
                tp = pos.get('pnl1',0)+pos.get('pnl2',0)-COMMISSION*2-SPREAD
                trades.append({'ts': pos['ts'], 'pnl': tp})
                pos = None
        if pos is None and i>=2:
            if pd.isna(bar['atr_rank']) or bar['atr_rank'] >= atr_max_pct: continue
            if pd.isna(bar['ema200']): continue
            prev = df.iloc[i-1]
            atr = bar['atr']; e = bar['close']
            # LONG: trend up + breakout above prev high
            if bar['close'] > bar['ema200'] and bar['high'] > prev['high'] and bar['close'] > prev['high']:
                pos = {'side':'L','e':e,'ts':ts,'sl':e-atr*sl_atr,'tp1':e+atr*tp1_atr,'tp2':e+atr*tp2_atr,'q1':0.5,'q2':0.5}
            elif bar['close'] < bar['ema200'] and bar['low'] < prev['low'] and bar['close'] < prev['low']:
                pos = {'side':'S','e':e,'ts':ts,'sl':e+atr*sl_atr,'tp1':e-atr*tp1_atr,'tp2':e-atr*tp2_atr,'q1':0.5,'q2':0.5}
    return trades

# ============================================================
# S3: Multi-TF Confluence (D1 + H1 pullback + M5 reversal)
# ============================================================
def s3_mtf_confluence(sl_atr=1.5, tp1_atr=10, tp2_atr=20):
    """D1 bullish + price pulled back to EMA20 H1 + M5 bullish engulfing → LONG."""
    trades = []; pos = None
    for i in range(300, len(df)):
        bar = df.iloc[i]; ts = df.index[i]; d = ts.date()
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
                trades.append({'ts': pos['ts'], 'pnl': tp})
                pos = None
        if pos is None and i>=2:
            d_trend = daily_trend.get(d, None)
            if d_trend is None: continue
            prev = df.iloc[i-1]
            atr = bar['atr']; e = bar['close']
            # Bullish engulfing on M5: current bar engulfs prev (close > prev open AND open < prev close)
            bull_engulf = (bar['close'] > prev['open']) and (bar['open'] < prev['close']) and (bar['close'] > bar['open'])
            bear_engulf = (bar['close'] < prev['open']) and (bar['open'] > prev['close']) and (bar['close'] < bar['open'])
            # Pullback to EMA20 (low touched EMA20 within last 4 bars)
            recent_low = df.iloc[i-3:i+1]['low'].min()
            ema20_now = bar['ema20']
            pullback_long = (recent_low <= ema20_now * 1.0005) and (bar['close'] > ema20_now)
            pullback_short = (df.iloc[i-3:i+1]['high'].max() >= ema20_now * 0.9995) and (bar['close'] < ema20_now)
            if d_trend and bull_engulf and pullback_long:
                pos = {'side':'L','e':e,'ts':ts,'sl':e-atr*sl_atr,'tp1':e+atr*tp1_atr,'tp2':e+atr*tp2_atr,'q1':0.5,'q2':0.5}
            elif (not d_trend) and bear_engulf and pullback_short:
                pos = {'side':'S','e':e,'ts':ts,'sl':e+atr*sl_atr,'tp1':e-atr*tp1_atr,'tp2':e-atr*tp2_atr,'q1':0.5,'q2':0.5}
    return trades

# ============================================================
# S4: PDH/PDL Sweep + Reversal
# ============================================================
def s4_pdh_pdl_sweep(sl_atr=0.5, tp1_atr=8, tp2_atr=15):
    """Price breaks PDH/PDL and reverses (sweep stop hunt)."""
    trades = []; pos = None
    cur_date = None; pdh = pdl = None; fired_today = 0
    for i in range(50, len(df)):
        bar = df.iloc[i]; ts = df.index[i]; d = ts.date()
        if d != cur_date:
            cur_date = d
            if d in daily_levels and 'pdh' in daily_levels[d]:
                pdh = daily_levels[d]['pdh']
                pdl = daily_levels[d]['pdl']
                fired_today = 0
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
                trades.append({'ts': pos['ts'], 'pnl': tp})
                pos = None
        if pos is None and pdh is not None and fired_today < 2 and 8 <= ts.hour < 20:
            atr = bar['atr']; e = bar['close']
            # Sweep PDH → SHORT (failed breakout)
            if bar['high'] > pdh and bar['close'] < pdh and bar['close'] < bar['open']:
                pos = {'side':'S','e':e,'ts':ts,'sl':bar['high']+atr*sl_atr,
                       'tp1':e-atr*tp1_atr,'tp2':e-atr*tp2_atr,'q1':0.5,'q2':0.5}
                fired_today += 1
            elif bar['low'] < pdl and bar['close'] > pdl and bar['close'] > bar['open']:
                pos = {'side':'L','e':e,'ts':ts,'sl':bar['low']-atr*sl_atr,
                       'tp1':e+atr*tp1_atr,'tp2':e+atr*tp2_atr,'q1':0.5,'q2':0.5}
                fired_today += 1
    return trades

print()
print("="*120)
print("4 NEW STRATEGIES — XAUUSD M5 5y:")
print("="*120)

print("\n--- S1: Asia Range Sweep + London Reversal ---", flush=True)
trades = s1_asia_sweep_reversal()
stats(trades, "S1 default (1R/2R)")
trades = s1_asia_sweep_reversal(tp1_r=0.5, tp2_r=1.5)
stats(trades, "S1 (0.5R/1.5R)")
trades = s1_asia_sweep_reversal(tp1_r=1.5, tp2_r=3.0)
stats(trades, "S1 (1.5R/3R)")

print("\n--- S2: Compression Breakout ---", flush=True)
trades = s2_compression_breakout()
stats(trades, "S2 ATR<20% + EMA200 trend")
trades = s2_compression_breakout(atr_max_pct=0.10)
stats(trades, "S2 ATR<10% (more strict)")
trades = s2_compression_breakout(atr_max_pct=0.30)
stats(trades, "S2 ATR<30% (looser)")

print("\n--- S3: Multi-TF Confluence (D1+EMA20 pullback+engulfing) ---", flush=True)
trades = s3_mtf_confluence()
stats(trades, "S3 default")

print("\n--- S4: PDH/PDL Sweep ---", flush=True)
trades = s4_pdh_pdl_sweep()
stats(trades, "S4 default (8R/15R)")
trades = s4_pdh_pdl_sweep(tp1_atr=5, tp2_atr=10)
stats(trades, "S4 (5R/10R)")
trades = s4_pdh_pdl_sweep(tp1_atr=12, tp2_atr=24)
stats(trades, "S4 (12R/24R)")

# Per year of best ones — compute later
