"""
TEST 2: News gate filter
Skip Inside Bar setups within ±30min of high-impact news events.
News calendar: NFP (1st Friday), FOMC (8/year), CPI (mid-month), PPI (mid),
US ISM/PMI (1st of month), Powell speeches (irregular).

Approximate by approximate dates (no real calendar API, use historical patterns):
- NFP: 1st Friday of each month at 13:30 UTC
- CPI: mid-month (10-15th) at 13:30 UTC
- FOMC: 8 dates per year (approximate)
- PPI: mid-month
- ISM: 1st business day of month
- Retail sales: mid-month

PASS if PF improves by >= 0.15 vs baseline.
"""
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

SYMBOL = "XAUUSD.crp"
ATR_LEN = 14; EMA_LEN = 50; VOL_LEN = 20
COMMISSION = 0.5; SPREAD = 0.50; SLIPPAGE = 0.20
SL_M = 1.5; TP1_M = 15; TP2_M = 30
NEWS_WINDOW_MIN = 30  # +/- 30 min around event

def fetch():
    mt5.initialize(); mt5.symbol_select(SYMBOL, True)
    end = datetime.now(timezone.utc)
    rates = mt5.copy_rates_from(SYMBOL, mt5.TIMEFRAME_M5, end, 50000)
    if len(rates) >= 50000:
        oldest = datetime.fromtimestamp(int(rates[0]['time']), tz=timezone.utc)
        rates2 = mt5.copy_rates_from(SYMBOL, mt5.TIMEFRAME_M5, oldest, 50000)
        if rates2 is not None:
            rates = np.concatenate([rates2, rates])
            _, idx = np.unique(rates['time'], return_index=True); rates = rates[np.sort(idx)]
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True); df = df.set_index('time')
    mt5.shutdown(); return df

def compute(df):
    df = df.copy()
    df['ema50'] = df['close'].ewm(span=EMA_LEN, adjust=False).mean()
    hl = df['high']-df['low']; hc = (df['high']-df['close'].shift()).abs(); lc = (df['low']-df['close'].shift()).abs()
    tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
    df['atr'] = tr.ewm(alpha=1/ATR_LEN, adjust=False).mean()
    df['vol_avg'] = df['tick_volume'].rolling(VOL_LEN).mean()
    df['inside'] = (df['high']<df['high'].shift(1)) & (df['low']>df['low'].shift(1))
    return df

def is_near_news(ts):
    """Heuristic: is this timestamp near a likely high-impact news event?"""
    # Convert to datetime
    dt = ts.to_pydatetime() if hasattr(ts, 'to_pydatetime') else ts

    # NFP: 1st Friday of month at 13:30 UTC (winter) / 12:30 UTC (summer)
    # Friday of week 1 of month
    if dt.weekday() == 4 and dt.day <= 7:  # Friday in 1st week
        if (12 <= dt.hour < 14) or (dt.hour == 14 and dt.minute < 30):
            return True

    # CPI: typically 2nd Tuesday or Wednesday of month at 13:30 UTC
    if dt.weekday() in (1, 2) and 9 <= dt.day <= 16:
        if (12 <= dt.hour < 14) or (dt.hour == 14 and dt.minute < 30):
            return True

    # PPI: typically Thursday following CPI (9-16 day range), at 13:30 UTC
    if dt.weekday() == 3 and 10 <= dt.day <= 17:
        if (12 <= dt.hour < 14) or (dt.hour == 14 and dt.minute < 30):
            return True

    # Retail sales: 14-17 of month at 13:30 UTC
    if 14 <= dt.day <= 17 and dt.weekday() <= 4:
        if (13 <= dt.hour < 14) or (dt.hour == 14 and dt.minute < 30):
            return True

    # ISM: 1st business day of month at 15:00 UTC
    if dt.day <= 3 and dt.weekday() <= 4:
        if (14 <= dt.hour < 16):
            return True

    # FOMC: typically Wednesday of week 3 of month at 19:00 UTC (8 dates per year)
    if dt.weekday() == 2 and 14 <= dt.day <= 21:
        if (18 <= dt.hour < 20):
            return True

    return False

def backtest(df, news_filter=False):
    trades = []; pos = None
    skipped_news = 0

    for i in range(EMA_LEN+5, len(df)):
        bar = df.iloc[i]; ts = df.index[i]

        if pos is not None:
            sl_h = bar['low'] <= pos['sl']
            tp1_h = bar['high'] >= pos['tp1']
            tp2_h = bar['high'] >= pos['tp2']
            if sl_h and (tp1_h or tp2_h):
                if (bar['open']-pos['sl']) < (pos['tp1']-bar['open']): tp1_h=False; tp2_h=False
            if tp1_h and pos['q1']>0:
                pos['pnl1'] = (pos['tp1']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q1']=0
            if tp2_h and pos['q2']>0:
                pos['pnl2'] = (pos['tp2']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q2']=0
            if sl_h:
                if pos['q1']>0:
                    pos['pnl1'] = (pos['sl']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q1']=0
                if pos['q2']>0:
                    pos['pnl2'] = (pos['sl']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q2']=0
            if pos['q1']==0 and pos['q2']==0:
                tp = pos.get('pnl1',0)+pos.get('pnl2',0)-COMMISSION*2-SPREAD
                trades.append({'ts': pos['ts'], 'pnl': tp})
                pos = None

        if pos is None and i>=2:
            prev = df.iloc[i-1]; pp = df.iloc[i-2]
            if not prev['inside']: continue
            if pd.isna(bar['ema50']) or pd.isna(bar['atr']) or pd.isna(bar['vol_avg']): continue
            if bar['tick_volume'] <= bar['vol_avg']*1.3: continue
            if ts.dayofweek == 2: continue  # skip Wed
            if not (0 <= ts.hour <= 6): continue  # Asia
            if bar['close']<=bar['ema50']: continue

            # NEWS FILTER - skip if near news event
            if news_filter and is_near_news(ts):
                skipped_news += 1
                continue

            mh = pp['high']
            if not (bar['high']>mh and bar['close']>mh): continue
            atr = bar['atr']; e = bar['close']
            pos = {'e':e,'ts':ts,'sl':e-atr*SL_M,'tp1':e+atr*TP1_M,'tp2':e+atr*TP2_M,'q1':0.5,'q2':0.5}

    return trades, skipped_news

def stats(trades, name):
    if not trades:
        print(f"{name}: NO trades"); return None
    arr = np.array([t['pnl'] for t in trades])
    n = len(arr); w = (arr>0).sum(); net = arr.sum()
    pf_p = arr[arr>0].sum(); pf_l = abs(arr[arr<=0].sum())
    pf = pf_p/pf_l if pf_l else 0
    eq = np.cumsum(arr); peak = np.maximum.accumulate(eq); dd = (peak-eq).max()
    print(f"{name:>30}: n={n} | WR {w/n*100:>5.1f}% | Net ${net:>+8.2f} | PF {pf:>5.2f} | DD ${dd:>6.2f}")
    return {'n':n, 'wr':w/n*100, 'net':net, 'pf':pf, 'dd':dd}

print("Fetching data..."); df = fetch(); df = compute(df); print(f"{len(df)} bars\n")

print("="*100)
print("TEST 2 — News Gate Filter")
print("="*100)
t_no_filter, _ = backtest(df, news_filter=False)
s_base = stats(t_no_filter, "Baseline (Asia config)")

t_filter, skipped = backtest(df, news_filter=True)
s_filt = stats(t_filter, f"With news filter")
print(f"  Skipped {skipped} setups near news events")

# Asian session is far from US news typically — but Asia hours = 0-6 UTC,
# US news is 13-19 UTC. So most Asia trades shouldn't be affected.
# But the strategy may also catch trades that fired near Asian news (PMI Japan, China).

# Let's also try without Asia restriction (test news filter on all hours)
def backtest_no_asia(df, news_filter=False):
    trades = []; pos = None
    skipped_news = 0
    for i in range(EMA_LEN+5, len(df)):
        bar = df.iloc[i]; ts = df.index[i]
        if pos is not None:
            sl_h = bar['low'] <= pos['sl']
            tp1_h = bar['high'] >= pos['tp1']
            tp2_h = bar['high'] >= pos['tp2']
            if sl_h and (tp1_h or tp2_h):
                if (bar['open']-pos['sl']) < (pos['tp1']-bar['open']): tp1_h=False; tp2_h=False
            if tp1_h and pos['q1']>0: pos['pnl1'] = (pos['tp1']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q1']=0
            if tp2_h and pos['q2']>0: pos['pnl2'] = (pos['tp2']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q2']=0
            if sl_h:
                if pos['q1']>0: pos['pnl1'] = (pos['sl']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q1']=0
                if pos['q2']>0: pos['pnl2'] = (pos['sl']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q2']=0
            if pos['q1']==0 and pos['q2']==0:
                tp = pos.get('pnl1',0)+pos.get('pnl2',0)-COMMISSION*2-SPREAD
                trades.append({'ts': pos['ts'], 'pnl': tp})
                pos = None
        if pos is None and i>=2:
            prev = df.iloc[i-1]; pp = df.iloc[i-2]
            if not prev['inside']: continue
            if pd.isna(bar['ema50']) or pd.isna(bar['atr']) or pd.isna(bar['vol_avg']): continue
            if bar['tick_volume'] <= bar['vol_avg']*1.3: continue
            if ts.dayofweek == 2: continue
            if bar['close']<=bar['ema50']: continue
            if news_filter and is_near_news(ts):
                skipped_news += 1; continue
            mh = pp['high']
            if not (bar['high']>mh and bar['close']>mh): continue
            atr = bar['atr']; e = bar['close']
            pos = {'e':e,'ts':ts,'sl':e-atr*SL_M,'tp1':e+atr*TP1_M,'tp2':e+atr*TP2_M,'q1':0.5,'q2':0.5}
    return trades, skipped_news

print()
print("Without Asia restriction (LONG + skip Wed only):")
t_all, _ = backtest_no_asia(df, news_filter=False)
s_all = stats(t_all, "Baseline LONG+skipWed")
t_all_n, sk = backtest_no_asia(df, news_filter=True)
s_all_n = stats(t_all_n, f"+ News filter")
print(f"  Skipped {sk}")

# Verdict
print()
print("="*100)
delta_pf = s_filt['pf'] - s_base['pf'] if s_filt and s_base else 0
delta_pf_all = s_all_n['pf'] - s_all['pf'] if s_all_n and s_all else 0
print(f"Delta PF (Asia config):     {delta_pf:+.2f}")
print(f"Delta PF (LONG+skipWed):    {delta_pf_all:+.2f}")
if delta_pf >= 0.15 or delta_pf_all >= 0.15:
    print(">>> NEWS GATE PASSES (PF improvement >= 0.15)")
else:
    print(">>> NEWS GATE FAILS (no meaningful improvement)")
