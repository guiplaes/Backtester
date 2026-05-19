"""Test rapid de 3 pairs noves (NZDCAD, USDCAD, AUDCAD) — multi-TF + BOTH dirs."""
import pandas as pd
import numpy as np

LOT = 0.05
PIP_MUL = {'NZDCAD':1000,'USDCAD':1000,'AUDCAD':1000}
COSTS = {'NZDCAD':0.40,'USDCAD':0.30,'AUDCAD':0.40}
PAIR_FILES = {
    'NZDCAD':'nzdcad_dk_m5_5y.csv','USDCAD':'usdcad_dk_m5_5y.csv',
    'AUDCAD':'audcad_dk_m5_5y.csv',
}

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
    return {'n':len(pnls),'wr':(pnls>0).sum()/len(pnls)*100,'net':pnls.sum(),
             'pf':pnls[pnls>0].sum()/(abs(pnls[pnls<=0].sum()) or 1)}

def per_year_count(trades):
    if not trades: return 0,0
    df_t = pd.DataFrame(trades)
    df_t['year'] = pd.to_datetime(df_t['ts']).dt.year
    yp = sum(1 for y, g in df_t.groupby('year') if g['pnl'].sum()>0)
    yt = df_t['year'].nunique()
    return yp, yt

print("Testing NZDCAD, USDCAD, AUDCAD...", flush=True)

ALL = []
for pair in PAIR_FILES.keys():
    print(f"\n--- {pair} ---")
    df = pd.read_csv(PAIR_FILES[pair], index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]
    cost = COSTS[pair]; pip_mul = PIP_MUL[pair]

    # Hurst per TF
    print(f"  Hurst per TF:")
    for tf, rule in [('M15','15min'),('H1','1h'),('H4','4h'),('D1','1D')]:
        df_tf = aggregate(df, rule)
        h = hurst(df_tf['close'].values)
        print(f"    {tf}: {h:.3f}")

    # Backtest sweep
    for tf, rule in [('M15','15min'),('H1','1h'),('H4','4h'),('D1','1D')]:
        df_tf = aggregate(df, rule)
        for ma in [50, 100, 150, 200, 300, 500, 800, 1600] if tf!='M15' else [200, 400, 800, 1600, 2400]:
            if ma >= len(df_tf)/3: continue
            arrs = precompute(df_tf, ma)
            for levels in [[-0.5,-1.0,-1.5,-2.0],[-1.0,-1.5,-2.0,-2.5],
                           [-1.5,-2.5,-3.5],[-1.0,-2.0,-3.0]]:
                for stop in [-3.0,-4.0,-5.0]:
                    if stop >= levels[-1]: continue
                    for direction in ['long','short']:
                        trades = bt_avg(arrs, direction, levels, stop, cost, pip_mul)
                        s = stats(trades)
                        if s and s['n']>=20 and s['pf']>=1.30 and s['net']>0:
                            yp, yt = per_year_count(trades)
                            ALL.append({
                                'pair':pair,'tf':tf,'ma':ma,'levels':levels,
                                'stop':stop,'direction':direction,
                                'n':s['n'],'wr':s['wr'],'net':s['net'],
                                'pf':s['pf'],'years_pos':yp,'years_total':yt
                            })

ALL.sort(key=lambda x: -x['net'])
print(f"\nTotal valid: {len(ALL)}")
print("\nTOP 30 (per Net):")
for r in ALL[:30]:
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"  {r['pair']:<7} {r['tf']:<3} {r['direction']:<5} SMA{r['ma']:<4} levels={lvl:<22} stop={r['stop']} | n={r['n']:>4} WR{r['wr']:.1f}% Net=${r['net']:>+7,.0f} PF{r['pf']:>5.2f} y+={r['years_pos']}/{r['years_total']}")

print("\nROBUST (5/6 anys+):")
robust = [r for r in ALL if r['years_pos']>=5 and r['n']>=30]
robust.sort(key=lambda x: -x['net'])
for r in robust[:15]:
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"  {r['pair']:<7} {r['tf']:<3} {r['direction']:<5} SMA{r['ma']:<4} levels={lvl:<22} stop={r['stop']} | n={r['n']:>4} WR{r['wr']:.1f}% Net=${r['net']:>+7,.0f} PF{r['pf']:>5.2f} y+={r['years_pos']}/{r['years_total']}")

print("\nDONE")
