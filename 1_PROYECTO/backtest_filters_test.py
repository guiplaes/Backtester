"""
TEST FILTRES — volatilitat regime, sessions, day-of-week
=========================================================
Veure si afegir filtres millora el sistema o l'empitjora.

Tests:
1. Volatility regime: skip quan ATR_pct > 80 percentil (high vol = trend often)
2. Session filter: només London/NY (no Asian/Dead)
3. Day-of-week: skip Mondays + Fridays
4. Combined filters
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

def add_ind(df_, ma_p):
    df_ = df_.copy()
    df_['sma'] = df_['close'].rolling(ma_p).mean()
    df_['std'] = df_['close'].rolling(ma_p).std()
    # ATR for volatility regime
    hl = df_['high']-df_['low']
    hc = (df_['high']-df_['close'].shift()).abs()
    lc = (df_['low']-df_['close'].shift()).abs()
    tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
    df_['atr'] = tr.ewm(alpha=1/14,adjust=False).mean()
    df_['atr_pct'] = df_['atr'].rolling(200).rank(pct=True)
    return df_

def precompute_full(df_):
    return {
        'O':df_['open'].values,'H':df_['high'].values,
        'L':df_['low'].values,'C':df_['close'].values,
        'SMA':df_['sma'].values,'STD':df_['std'].values,
        'ATR_PCT':df_['atr_pct'].values,
        'HOUR':np.array([t.hour for t in df_.index]),
        'DOW':np.array([t.dayofweek for t in df_.index]),
        'TS':df_.index, 'n':len(df_)
    }

def get_session(h):
    if h < 7: return 'ASIA'
    elif h < 13: return 'LONDON'
    elif h < 16: return 'OVERLAP'
    elif h < 21: return 'NY'
    else: return 'DEAD'

def bt_filtered(arrs, direction, levels, stop_z, cost, pip_mul,
                vol_filter=False, session_filter=False, dow_filter=False):
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    SMA=arrs['SMA'];STD=arrs['STD'];ATR_PCT=arrs['ATR_PCT']
    HOUR=arrs['HOUR'];DOW=arrs['DOW']
    n=arrs['n']
    trades=[];pos=None
    for i in range(50,n):
        sma=SMA[i];std=STD[i];c=C[i]
        if np.isnan(sma) or np.isnan(std) or std<=0: continue
        z=(c-sma)/std
        # Manage existing pos (always)
        if pos is not None:
            if direction=='long': stop_hit=z<=stop_z;target=c>=sma
            else: stop_hit=z>=-stop_z;target=c<=sma
            if stop_hit or target or (i-pos['entries'][0][0])>=500:
                tot_u=len(pos['entries']);pnl=0;sgn=1 if direction=='long' else -1
                for eidx,ep in pos['entries']: pnl+=(c-ep)*sgn
                pnl_d = pnl*pip_mul*(LOT/0.01) - tot_u*cost*(LOT/0.01)
                trades.append({'open_ts':arrs['TS'][pos['entries'][0][0]],
                                'close_ts':arrs['TS'][i],'pnl':pnl_d})
                pos=None;continue
            if direction=='long':
                for lvl in levels:
                    if lvl not in pos['hit'] and z<=lvl:
                        pos['entries'].append((i,c));pos['hit'].add(lvl);break
            else:
                for lvl in levels:
                    if lvl not in pos['hit'] and z>=-lvl:
                        pos['entries'].append((i,c));pos['hit'].add(lvl);break
        # Apply filters BEFORE new entry
        if pos is None:
            entry_ok = True
            if vol_filter:
                ap = ATR_PCT[i]
                if not np.isnan(ap) and ap > 0.80:  # skip high vol regime
                    entry_ok = False
            if session_filter and entry_ok:
                sess = get_session(HOUR[i])
                if sess in ['ASIA','DEAD']: entry_ok = False
            if dow_filter and entry_ok:
                if DOW[i] in [0, 4]: entry_ok = False  # skip Mon, Fri
            if entry_ok:
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

print("Testing filters...", flush=True)

results = []
for cfg in CONFIGS:
    print(f"\n--- {cfg['name']} ---")
    df = pd.read_csv(PAIR_FILES[cfg['pair']], index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]
    df_tf = aggregate(df, cfg['tf'])
    df_tf = add_ind(df_tf, cfg['ma'])
    arrs = precompute_full(df_tf)
    cost = COSTS[cfg['pair']]; pip_mul = PIP_MUL[cfg['pair']]

    test_filters = [
        ('NO_FILTER',False,False,False),
        ('VOL_FILTER',True,False,False),
        ('SESSION',False,True,False),
        ('DOW_SKIP_MF',False,False,True),
        ('VOL+SESS',True,True,False),
        ('ALL',True,True,True),
    ]
    for fname, vf, sf, df_ in test_filters:
        trades = bt_filtered(arrs, cfg['dir'], cfg['levels'], cfg['stop'], cost, pip_mul,
                              vol_filter=vf, session_filter=sf, dow_filter=df_)
        s = stats(trades)
        if s:
            print(f"  {fname:<14}: n={s['n']:>4} WR{s['wr']:>5.1f}% Net=${s['net']:>+8,.0f} PF{s['pf']:>5.2f}")
            results.append({'cfg':cfg['name'],'filter':fname,**s})

print()
print("="*120)
print("RESUM (Net) per filtre:")
df_r = pd.DataFrame(results)
print(df_r.pivot(index='cfg', columns='filter', values='net').to_string())

print()
print("Totals per filtre:")
for f in ['NO_FILTER','VOL_FILTER','SESSION','DOW_SKIP_MF','VOL+SESS','ALL']:
    sub = df_r[df_r['filter']==f]
    print(f"  {f:<14}: Total Net=${sub['net'].sum():+,.0f} avg PF={sub['pf'].mean():.2f}")

print("\nDONE")
