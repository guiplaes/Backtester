"""
TEST MA VARIANTS — SMA vs EMA vs WMA, std bands vs ATR bands
==============================================================
Comparem el caracter mean-rev amb diferents tipus de mitjana i bands.
"""
import pandas as pd
import numpy as np

CAPITAL_INICIAL = 63000.0
LOT = 0.05

PIP_MUL = {'EURGBP':1000,'EURCHF':1000,'EURJPY':100,'CHFJPY':100}
COSTS = {'EURGBP':0.30,'EURCHF':0.30,'EURJPY':0.30,'CHFJPY':0.40}
PAIR_FILES = {
    'EURGBP':'eurgbp_dk_m5_5y.csv','EURCHF':'eurchf_dk_m5_5y.csv',
    'EURJPY':'eurjpy_dk_m5_5y.csv','CHFJPY':'chfjpy_dk_m5_5y.csv',
}

def aggregate(df_, rule):
    return df_.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last')).dropna()

def precompute(df_, ma_p, ma_type='sma', band_type='std'):
    """Compute MA + bands. ma_type: sma/ema/wma. band_type: std/atr"""
    if ma_type == 'sma':
        ma = df_['close'].rolling(ma_p).mean()
    elif ma_type == 'ema':
        ma = df_['close'].ewm(span=ma_p, adjust=False).mean()
    elif ma_type == 'wma':
        weights = np.arange(1, ma_p+1)
        ma = df_['close'].rolling(ma_p).apply(
            lambda x: np.dot(x, weights)/weights.sum() if len(x)==ma_p else np.nan, raw=True)

    if band_type == 'std':
        band = df_['close'].rolling(ma_p).std()
    elif band_type == 'atr':
        hl = df_['high']-df_['low']
        hc = (df_['high']-df_['close'].shift()).abs()
        lc = (df_['low']-df_['close'].shift()).abs()
        tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
        band = tr.ewm(alpha=1/14,adjust=False).mean()  # ATR

    return {
        'O':df_['open'].values,'H':df_['high'].values,
        'L':df_['low'].values,'C':df_['close'].values,
        'MA':ma.values,'BAND':band.values,
        'TS':df_.index, 'n':len(df_)
    }

def bt_avg(arrs, direction, levels, stop_z, cost, pip_mul):
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    MA=arrs['MA'];BAND=arrs['BAND']
    n=arrs['n']
    trades=[];pos=None
    for i in range(50,n):
        ma=MA[i];b=BAND[i];c=C[i]
        if np.isnan(ma) or np.isnan(b) or b<=0: continue
        z=(c-ma)/b
        if pos is not None:
            if direction=='long': stop_hit=z<=stop_z;target=c>=ma
            else: stop_hit=z>=-stop_z;target=c<=ma
            if stop_hit or target or (i-pos['entries'][0][0])>=500:
                tot_u=len(pos['entries']);pnl=0;sgn=1 if direction=='long' else -1
                for eidx,ep in pos['entries']: pnl+=(c-ep)*sgn
                pnl_d = pnl*pip_mul*(LOT/0.01) - tot_u*cost*(LOT/0.01)
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
    n=len(pnls);wins=(pnls>0).sum();net=pnls.sum()
    pp=pnls[pnls>0].sum();pl=abs(pnls[pnls<=0].sum())
    pf=pp/pl if pl else 0
    return {'n':n,'wr':wins/n*100 if n>0 else 0,'net':net,'pf':pf}

CONFIGS = [
    {'name':'EURGBP D1 LONG','pair':'EURGBP','tf':'1D','ma':100,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'long'},
    {'name':'EURGBP H4 LONG','pair':'EURGBP','tf':'4h','ma':300,'levels':[-1.0,-1.5,-2.5,-3.0],'stop':-5.0,'dir':'long'},
    {'name':'EURJPY H4 LONG','pair':'EURJPY','tf':'4h','ma':300,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-5.0,'dir':'long'},
    {'name':'EURCHF H4 SHORT','pair':'EURCHF','tf':'4h','ma':150,'levels':[-1.5,-2.5,-3.5,-4.0],'stop':-6.0,'dir':'short'},
    {'name':'CHFJPY H4 LONG','pair':'CHFJPY','tf':'4h','ma':300,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-4.0,'dir':'long'},
]

print("Testing MA variants (SMA vs EMA vs WMA, std vs ATR bands)...", flush=True)

results = []
for cfg in CONFIGS:
    print(f"\n--- {cfg['name']} ---")
    df = pd.read_csv(PAIR_FILES[cfg['pair']], index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]
    df_tf = aggregate(df, cfg['tf'])
    cost = COSTS[cfg['pair']]; pip_mul = PIP_MUL[cfg['pair']]

    for ma_type in ['sma','ema','wma']:
        for band_type in ['std','atr']:
            arrs = precompute(df_tf, cfg['ma'], ma_type, band_type)
            # ATR bands need different scaling since ATR != std. Adjust levels for ATR
            if band_type == 'atr':
                # ATR is roughly half of std for forex. So multiply levels by 2 for similar exposure
                lvls = [l*2 for l in cfg['levels']]
                stp = cfg['stop']*2
            else:
                lvls = cfg['levels']
                stp = cfg['stop']
            trades = bt_avg(arrs, cfg['dir'], lvls, stp, cost, pip_mul)
            s = stats(trades)
            if s:
                key = f"{ma_type}+{band_type}"
                print(f"  {key:<10}: n={s['n']:>4} WR{s['wr']:>5.1f}% Net=${s['net']:>+8,.0f} PF{s['pf']:>5.2f}")
                results.append({'cfg':cfg['name'],'variant':key,**s})

print()
print("="*120)
print("PIVOT (Net):")
df_r = pd.DataFrame(results)
print(df_r.pivot(index='cfg', columns='variant', values='net').to_string())

print()
print("Totals per variant:")
for v in df_r['variant'].unique():
    sub = df_r[df_r['variant']==v]
    print(f"  {v:<14}: Net total=${sub['net'].sum():+,.0f}")

print("\nDONE")
