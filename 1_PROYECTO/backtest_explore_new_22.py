"""Test ràpid pairs nous descarregats (no JPY) per trobar mean-rev."""
import pandas as pd
import numpy as np
import sys
sys.path.insert(0, '.')
try: from tg_send import send as tg_send
except: tg_send = lambda x: None

LOT = 0.05

# Pairs SENSE JPY (estables candidates)
NEW_PAIRS = ['AUDUSD','EURAUD','EURCAD','EURNZD','GBPAUD','GBPNZD','GBPUSD','NZDUSD']
PIP_MUL = {p:1000 for p in NEW_PAIRS}
COSTS = {p:0.30 if 'USD' in p else 0.40 for p in NEW_PAIRS}
PAIR_FILES = {p: f"{p.lower()}_dk_m5_5y.csv" for p in NEW_PAIRS}

def aggregate(df_, rule):
    return df_.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last')).dropna()

def hurst(prices, max_lag=100):
    prices = np.asarray(prices)
    if len(prices)<200: return 0.5
    lags = range(2, min(max_lag, len(prices)//4))
    tau = []
    for lag in lags:
        d = np.subtract(prices[lag:], prices[:-lag])
        if len(d)<2: continue
        tau.append(np.sqrt(np.std(d)))
    if len(tau)<5: return 0.5
    poly = np.polyfit(np.log(list(lags[:len(tau)])), np.log(tau), 1)
    return poly[0]*2.0

def precompute(df_, ma_p):
    return {
        'O':df_['open'].values,'H':df_['high'].values,
        'L':df_['low'].values,'C':df_['close'].values,
        'SMA':df_['close'].rolling(ma_p).mean().values,
        'STD':df_['close'].rolling(ma_p).std().values,
        'TS':df_.index, 'n':len(df_)
    }

def bt_avg(arrs, direction, levels, stop_z, cost, pip_mul):
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
                pnl_d = pnl*pip_mul*(LOT/0.01) - tot_u*cost*(LOT/0.01)
                trades.append({'pnl':pnl_d,'ts':arrs['TS'][i]})
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
    n=len(pnls);wins=(pnls>0).sum();net=pnls.sum()
    pp=pnls[pnls>0].sum();pl=abs(pnls[pnls<=0].sum())
    pf=pp/pl if pl else 0
    return {'n':n,'wr':wins/n*100,'net':net,'pf':pf}

def per_year_count(trades):
    if not trades: return 0,0
    df_t = pd.DataFrame(trades)
    df_t['year'] = pd.to_datetime(df_t['ts']).dt.year
    yp = sum(1 for y, g in df_t.groupby('year') if g['pnl'].sum()>0)
    return yp, df_t['year'].nunique()

print("Testing 8 new pairs...", flush=True)
ALL = []
for pair in NEW_PAIRS:
    print(f"\n--- {pair} ---")
    df = pd.read_csv(PAIR_FILES[pair], index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]
    cost = COSTS[pair]; pip_mul = PIP_MUL[pair]

    # Quick Hurst
    print(f"  Hurst:")
    for tf, rule in [('M15','15min'),('H1','1h'),('H4','4h'),('D1','1D')]:
        df_tf = aggregate(df, rule)
        h = hurst(df_tf['close'].values)
        print(f"    {tf}: {h:.3f}")

    # Backtest
    for tf, rule in [('H1','1h'),('H4','4h'),('D1','1D')]:
        df_tf = aggregate(df, rule)
        for ma in [50, 100, 200, 300, 500, 1000]:
            if ma >= len(df_tf)/3: continue
            arrs = precompute(df_tf, ma)
            for levels in [[-0.5,-1.0,-1.5,-2.0],[-1.0,-1.5,-2.0,-2.5],[-1.5,-2.5,-3.5]]:
                for stop in [-3.0,-4.0,-5.0]:
                    if stop >= levels[-1]: continue
                    for direction in ['long','short']:
                        trades = bt_avg(arrs, direction, levels, stop, cost, pip_mul)
                        s = stats(trades)
                        if s and s['n']>=15 and s['pf']>=1.30 and s['net']>0:
                            yp, yt = per_year_count(trades)
                            ALL.append({
                                'pair':pair,'tf':tf,'ma':ma,'levels':levels,
                                'stop':stop,'dir':direction,
                                **s,'yp':yp,'yt':yt
                            })

ALL.sort(key=lambda x:-x['net'])

# Robust per pair
print("\n" + "="*120)
print("MILLOR PER PAIR (5+ anys positius, n>=20):")
print("="*120)
robust = [r for r in ALL if r['yp']>=5 and r['n']>=20]

best_per_pair = {}
for r in sorted(robust, key=lambda x:-x['net']):
    if r['pair'] not in best_per_pair:
        best_per_pair[r['pair']] = r

for pair, r in best_per_pair.items():
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"  {pair:<7} {r['tf']:<3} {r['dir']:<5} SMA{r['ma']:<5} levels={lvl:<22} stop={r['stop']} | n={r['n']:>4} WR{r['wr']:.1f}% Net=${r['net']:>+7,.0f} PF{r['pf']:.2f} y+={r['yp']}/{r['yt']}")

# Top 20
print("\n" + "="*120)
print("TOP 20 GLOBAL:")
print("="*120)
for r in robust[:20]:
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"  {r['pair']:<7} {r['tf']:<3} {r['dir']:<5} SMA{r['ma']:<5} levels={lvl:<22} stop={r['stop']} | n={r['n']:>4} WR{r['wr']:.1f}% Net=${r['net']:>+7,.0f} PF{r['pf']:.2f} y+={r['yp']}/{r['yt']}")

# TG
msg = "📊 <b>NEW 8 PAIRS scan</b>%0A%0AMillors per pair (5+ anys positius):%0A"
for pair, r in best_per_pair.items():
    msg += f"{pair} {r['tf']} {r['dir']}: PF{r['pf']:.1f} +${r['net']:.0f}%0A"
tg_send(msg)
print("\nDONE")
