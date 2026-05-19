"""
EURUSD Mean-Reversion + Averaging — sweep complet
==================================================
Mateixa estratègia que XAUUSD, params adaptats a EURUSD.
EURUSD és més rangy històricament — esperem millor PF.
"""
import pandas as pd
import numpy as np
import time

# EURUSD: 0.01 lot ~= $1/pip. Spread típic 0.5 pips → cost ~$0.05/RT
# Amb spread real broker pot ser $0.10/RT a 0.01 lot
REAL_COST = 0.10

print("Loading...", flush=True)
m5 = pd.read_csv('eurusd_m5_5y.csv', index_col=0, parse_dates=True)
m5.index = pd.to_datetime(m5.index, utc=True)
m5.columns = [c.lower() for c in m5.columns]
print(f"EURUSD M5: {len(m5)} bars from {m5.index[0].date()} to {m5.index[-1].date()}", flush=True)

# Diagnostic: yearly returns
yearly_close = m5['close'].resample('1YE').last()
print("\nRendiment EURUSD per any:")
for year_end, c in yearly_close.items():
    yr = year_end.year
    if yr in m5.index.year:
        o = m5[m5.index.year == yr]['close'].iloc[0]
        pct = (c-o)/o*100
        marker = "BULL" if pct>3 else ("BEAR" if pct<-3 else "LATERAL")
        print(f"  {yr}: {o:.4f} -> {c:.4f} ({pct:+.1f}%) {marker}")

def aggregate(df_, rule):
    return df_.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last')).dropna()

def precompute(df_, ma_periods):
    return {
        'O':df_['open'].values,'H':df_['high'].values,
        'L':df_['low'].values,'C':df_['close'].values,
        'SMAS':{p:df_['close'].rolling(p).mean().values for p in ma_periods},
        'STDS':{p:df_['close'].rolling(p).std().values for p in ma_periods},
        'TS':df_.index,'n':len(df_)
    }

def bt_avg(arrs, direction, ma, levels, stop_z, cost, max_bars=500):
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    SMA=arrs['SMAS'][ma];STD=arrs['STDS'][ma]
    n=arrs['n']
    trades=[];pos=None
    for i in range(ma+10,n):
        sma=SMA[i];std=STD[i];c=C[i]
        if np.isnan(sma) or np.isnan(std) or std<=0: continue
        z=(c-sma)/std
        if pos is not None:
            if direction=='long':
                stop_hit=z<=stop_z;target=c>=sma
            else:
                stop_hit=z>=-stop_z;target=c<=sma
            time_out=(i-pos['entries'][0][0])>=max_bars
            if stop_hit or target or time_out:
                exit_p=c
                tot_u=len(pos['entries'])
                pnl=0;sgn=1 if direction=='long' else -1
                for eidx,ep in pos['entries']: pnl+=(exit_p-ep)*sgn
                # EURUSD cost: convert price-difference to dollars assuming 0.01 lot
                # 1 lot EURUSD = 100k EUR. 0.01 lot = 1k EUR.
                # 1 pip = 0.0001 = $0.10 per 0.01 lot
                # So pnl in price units * 10000 = pips, * 0.10 = $/0.01lot
                # Simplification: treat pnl as direct $ where 1 unit price = $1000 (0.01 lot)
                # Better: use $ per pip * pips
                pnl_dollars = pnl * 10000 * 0.10  # convert price diff to $ per 0.01 lot
                pnl_dollars -= tot_u * cost
                trades.append({'ts':arrs['TS'][pos['entries'][0][0]],
                               'pnl':pnl_dollars,'units':tot_u,
                               'reason':'STOP' if stop_hit else ('TARGET' if target else 'TIME'),
                               'duration':i-pos['entries'][0][0]})
                pos=None
                continue
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
            if direction=='long' and z<=f:
                pos={'entries':[(i,c)],'hit':{f}}
            elif direction=='short' and z>=-f:
                pos={'entries':[(i,c)],'hit':{f}}
    return trades

def stats(trades):
    if not trades: return None
    pnls=np.array([t['pnl'] for t in trades])
    n=len(pnls);wins=(pnls>0).sum();net=pnls.sum()
    pp=pnls[pnls>0].sum();pl=abs(pnls[pnls<=0].sum())
    pf=pp/pl if pl else 0
    eq=np.cumsum(pnls);peak=np.maximum.accumulate(eq);dd=(peak-eq).max()
    avg_u=np.mean([t['units'] for t in trades])
    avg_d=np.mean([t['duration'] for t in trades])
    return {'n':n,'wr':wins/n*100,'net':net,'pf':pf,'dd':dd,'avg_u':avg_u,'avg_d':avg_d}

def fmt(s):
    if s is None: return "0"
    return f"n={s['n']:>5} WR{s['wr']:>4.1f}% Net=${s['net']:>+7.0f} PF{s['pf']:>5.2f} DD${s['dd']:>5.0f} u={s['avg_u']:.1f}"

def per_year(trades):
    if not trades: return {}
    df=pd.DataFrame(trades)
    df['year']=pd.to_datetime(df['ts']).dt.year
    out={}
    for yr,grp in df.groupby('year'):
        pnls=grp['pnl'].values
        n=len(pnls);wins=(pnls>0).sum()
        out[yr]={'n':n,'wr':wins/n*100,'net':pnls.sum()}
    return out

# Aggregate
TF_MA = {
    'M5':  [100, 200, 400, 800, 1600, 3200, 5000],
    'M15': [50, 100, 200, 400, 800, 1600, 2400],
    'M30': [50, 100, 200, 400, 800, 1200, 1600],
    'H1':  [50, 100, 200, 400, 800, 1200, 1600],
    'H4':  [50, 100, 150, 200, 300, 500],
    'D1':  [20, 30, 50, 100, 150, 200],
}
print("\nAggregating...", flush=True)
DATA = {'M5': precompute(m5, TF_MA['M5'])}
print(f"  M5: {len(m5)} bars", flush=True)
for tf, rule in [('M15','15min'),('M30','30min'),('H1','1h'),('H4','4h'),('D1','1D')]:
    df = aggregate(m5, rule)
    DATA[tf] = precompute(df, TF_MA[tf])
    print(f"  {tf}: {len(df)} bars", flush=True)

LEVELS_GRID = [
    [-1.0],[-1.5],[-2.0],
    [-1.0,-2.0],[-1.5,-2.5],[-2.0,-3.0],
    [-1.0,-2.0,-3.0],[-1.5,-2.5,-3.5],
    [-0.5,-1.0,-1.5,-2.0],[-1.0,-1.5,-2.0,-2.5],
    [-0.5,-1.5,-2.5],
]
STOPS = [-3.0,-3.5,-4.0,-5.0,-6.0]

print("\nRunning EURUSD sweep...", flush=True)
ALL = []
t0 = time.time()
for tf in ['M5','M15','M30','H1','H4','D1']:
    arrs = DATA[tf]
    print(f"\n--- {tf} ({arrs['n']} bars) ---", flush=True)
    for ma in TF_MA[tf]:
        if ma >= arrs['n']/3: continue
        for levels in LEVELS_GRID:
            for stop in STOPS:
                if stop >= levels[-1]: continue
                for direction in ['long','short']:
                    trades = bt_avg(arrs, direction, ma, levels, stop, REAL_COST)
                    s = stats(trades)
                    if s and s['n']>=15 and s['pf']>=1.20 and s['net']>0:
                        ALL.append({'tf':tf,'ma':ma,'levels':levels,'stop':stop,
                                    'direction':direction,'stats':s,'trades':trades})

print(f"\nDone in {time.time()-t0:.0f}s. {len(ALL)} valid configs.")

ALL.sort(key=lambda x:-x['stats']['pf'])

# TOP global
print()
print("="*150)
print("TOP 30 EURUSD CONFIGS:")
print("="*150)
for r in ALL[:30]:
    s = r['stats']
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"  {r['tf']:<3} SMA{r['ma']:<4} {r['direction']:<5} levels={lvl:<24} stop={r['stop']} | {fmt(s)}")

# Per TF
for tf in ['M5','M15','M30','H1','H4','D1']:
    print()
    print(f"TOP 5 {tf}:")
    sub = [r for r in ALL if r['tf']==tf]
    for r in sub[:5]:
        s = r['stats']
        lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
        print(f"  SMA{r['ma']:<4} {r['direction']:<5} levels={lvl:<24} stop={r['stop']} | {fmt(s)}")

# Yearly per top 10
print()
print("="*150)
print("ANY-PER-ANY top 10:")
print("="*150)
for r in ALL[:10]:
    s = r['stats']
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"\n{r['tf']} SMA{r['ma']} {r['direction']} levels={lvl} stop={r['stop']} | TOTAL {fmt(s)}")
    yb = per_year(r['trades'])
    yrs_pos = sum(1 for y in yb.values() if y['net']>0)
    print(f"  Anys positius: {yrs_pos}/{len(yb)}")
    for yr in sorted(yb.keys()):
        y = yb[yr]
        sgn = "+" if y['net']>0 else "-"
        print(f"  {sgn} {yr}: n={y['n']:>4} WR{y['wr']:>5.1f}% Net=${y['net']:>+7.0f}")

print("\nDONE")
