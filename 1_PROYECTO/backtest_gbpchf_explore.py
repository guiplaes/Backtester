"""GBPCHF — descobrir si te edge mean-rev (just descarregat)."""
import pandas as pd
import numpy as np

df = pd.read_csv('gbpchf_dk_m5_5y.csv', index_col=0, parse_dates=True)
df.index = pd.to_datetime(df.index, utc=True)
df.columns = [c.lower() for c in df.columns]
print(f"GBPCHF: {len(df)} bars from {df.index[0]} to {df.index[-1]}")

# Yearly drift
yrs = sorted(df.index.year.unique())
for yr in yrs:
    sub = df[df.index.year==yr]
    if len(sub)<10: continue
    pct = (sub['close'].iloc[-1] - sub['close'].iloc[0]) / sub['close'].iloc[0] * 100
    print(f"  {yr}: {pct:+.1f}%")

def aggregate(df_, rule):
    return df_.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last')).dropna()

def hurst(prices, max_lag=100):
    prices = np.asarray(prices)
    if len(prices) < 200: return 0.5
    lags = range(2, min(max_lag, len(prices)//4))
    tau = []
    for lag in lags:
        diff = np.subtract(prices[lag:], prices[:-lag])
        if len(diff) < 2: continue
        tau.append(np.sqrt(np.std(diff)))
    if len(tau) < 5: return 0.5
    poly = np.polyfit(np.log(list(lags[:len(tau)])), np.log(tau), 1)
    return poly[0] * 2.0

print("\nHurst per TF:")
for tf, rule in [('M15','15min'),('M30','30min'),('H1','1h'),('H4','4h'),('D1','1D')]:
    df_tf = aggregate(df, rule)
    h = hurst(df_tf['close'].values)
    cat = "MEAN-REV" if h<0.45 else ("TREND" if h>0.55 else "MIXED")
    print(f"  {tf}: Hurst={h:.3f} {cat}")

# Quick backtest sweep
def precompute(df_, ma_p):
    return {
        'O':df_['open'].values,'H':df_['high'].values,
        'L':df_['low'].values,'C':df_['close'].values,
        'SMA':df_['close'].rolling(ma_p).mean().values,
        'STD':df_['close'].rolling(ma_p).std().values,
        'TS':df_.index, 'n':len(df_)
    }

def bt_avg(arrs, direction, levels, stop_z, cost=0.40, pip_mul=1000, lot=0.05):
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    SMA=arrs['SMA'];STD=arrs['STD']
    n=arrs['n']
    trades=[];pos=None
    for i in range(50,n):
        sma=SMA[i];std=STD[i];c=C[i]
        if np.isnan(sma) or np.isnan(std) or std<=0: continue
        z=(c-sma)/std
        if pos is not None:
            if direction=='long': stop_hit=z<=stop_z;target=c>=sma
            else: stop_hit=z>=-stop_z;target=c<=sma
            if stop_hit or target or (i-pos['entries'][0][0])>=500:
                tot_u=len(pos['entries']);pnl=0;sgn=1 if direction=='long' else -1
                for eidx,ep in pos['entries']: pnl+=(c-ep)*sgn
                pnl_d = pnl*pip_mul*(lot/0.01) - tot_u*cost*(lot/0.01)
                trades.append({'pnl':pnl_d})
                pos=None;continue
            if direction=='long':
                for lvl in levels:
                    if lvl not in pos['hit'] and z<=lvl:
                        pos['entries'].append((i,c));pos['hit'].add(lvl);break
            else:
                for lvl in levels:
                    if lvl not in pos['hit'] and z>=-lvl:
                        pos['entries'].append((i,c));pos['hit'].add(lvl);break
        if pos is None:
            f=levels[0]
            if direction=='long' and z<=f: pos={'entries':[(i,c)],'hit':{f}}
            elif direction=='short' and z>=-f: pos={'entries':[(i,c)],'hit':{f}}
    return trades

def stats(trades):
    if not trades: return None
    pnls=np.array([t['pnl'] for t in trades])
    return {'n':len(pnls),'wr':(pnls>0).sum()/len(pnls)*100,'net':pnls.sum(),
             'pf':pnls[pnls>0].sum()/(abs(pnls[pnls<=0].sum()) or 1)}

print("\nBacktest sweep (top configs):")
for tf, rule in [('D1','1D'),('H4','4h'),('H1','1h')]:
    df_tf = aggregate(df, rule)
    print(f"\n--- {tf} ---")
    for ma in [50, 100, 150, 200, 300, 500]:
        if ma >= len(df_tf)/3: continue
        arrs = precompute(df_tf, ma)
        for levels in [[-1.0,-1.5,-2.0,-2.5],[-0.5,-1.5,-2.5,-3.0],[-1.0,-2.0,-3.0]]:
            for stop in [-3.0,-4.0,-5.0]:
                if stop >= levels[-1]: continue
                for direction in ['long','short']:
                    trades = bt_avg(arrs, direction, levels, stop)
                    s = stats(trades)
                    if s and s['n']>=15 and s['pf']>=1.30 and s['net']>0:
                        lvl_s = '/'.join(f'{l:.1f}' for l in levels)
                        print(f"  SMA{ma:<4} {direction:<5} levels={lvl_s:<22} stop={stop} | n={s['n']:>4} WR{s['wr']:.1f}% Net=${s['net']:+.0f} PF{s['pf']:.2f}")
print("\nDONE")
