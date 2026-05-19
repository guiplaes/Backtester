"""
TESTS EXTRA — idees per millorar V4
====================================
1. TRAILING STOP després d'estar in profit
2. DYNAMIC LOT (reduir si recent DD, augmentar si recent profit)
3. STOP LOSS més proper (test diferents stops)
4. MULTI-TF FILTER (entrar només si higher TF align)
"""
import pandas as pd
import numpy as np
import sys
sys.path.insert(0, '.')
try: from tg_send import send as tg_send
except: tg_send = lambda x: None

LOT = 0.05
PAIRS_INFO = {
    'EURGBP':{'cost':0.30,'pip':1000},'GBPCHF':{'cost':0.40,'pip':1000},
    'AUDCAD':{'cost':0.40,'pip':1000},'USDCAD':{'cost':0.30,'pip':1000},
    'NZDCAD':{'cost':0.40,'pip':1000},'AUDNZD':{'cost':0.40,'pip':1000},
    'GBPNZD':{'cost':0.50,'pip':1000},'EURNZD':{'cost':0.50,'pip':1000},
    'USDCHF':{'cost':0.30,'pip':1000},'EURCHF':{'cost':0.30,'pip':1000},
}
PAIR_FILES = {p: f"{p.lower()}_dk_m5_5y.csv" for p in PAIRS_INFO}

def aggregate(df_, rule):
    return df_.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last')).dropna()

def precompute(df_, ma_p):
    return {'O':df_['open'].values,'H':df_['high'].values,
            'L':df_['low'].values,'C':df_['close'].values,
            'SMA':df_['close'].rolling(ma_p).mean().values,
            'STD':df_['close'].rolling(ma_p).std().values,
            'TS':df_.index, 'n':len(df_)}

# Test 1: STOP variations
def bt_stop_test(arrs, direction, levels, stop_z, cost, pip_mul):
    """Stop test — diferents stops"""
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
                pnl_d=pnl*pip_mul*(LOT/0.01)-tot_u*cost*(LOT/0.01)
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

# Test 2: Trailing stop after profit
def bt_trailing(arrs, direction, levels, stop_z, cost, pip_mul, trail_after_z=0.5):
    """Trail SL: quan z passat -trail_after_z (preu a meitat de tornada), mou SL a entry avg"""
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    SMA=arrs['SMA'];STD=arrs['STD']
    n=arrs['n']
    trades=[];pos=None
    for i in range(50,n):
        sma=SMA[i];std=STD[i];c=C[i]
        if np.isnan(sma) or np.isnan(std) or std<=0: continue
        z=(c-sma)/std
        if pos is not None:
            sgn=1 if direction=='long' else -1
            avg_entry = sum(p[1] for p in pos['entries'])/len(pos['entries'])
            # Activate trail when in profit
            if direction=='long' and z >= -trail_after_z and not pos.get('trail_active'):
                pos['trail_active'] = True
                pos['trail_sl'] = avg_entry  # BE
            elif direction=='short' and z <= trail_after_z and not pos.get('trail_active'):
                pos['trail_active'] = True
                pos['trail_sl'] = avg_entry
            if direction=='long':
                stop_hit = z<=stop_z
                target = c>=sma
                if pos.get('trail_active') and c<=pos['trail_sl']: stop_hit=True
            else:
                stop_hit = z>=-stop_z
                target = c<=sma
                if pos.get('trail_active') and c>=pos['trail_sl']: stop_hit=True
            if stop_hit or target or (i-pos['entries'][0][0])>=500:
                tot_u=len(pos['entries']);pnl=0
                for eidx,ep in pos['entries']: pnl+=(c-ep)*sgn
                pnl_d=pnl*pip_mul*(LOT/0.01)-tot_u*cost*(LOT/0.01)
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
            if direction=='long' and z<=f: pos={'entries':[(i,c)],'hit':{f},'trail_active':False}
            elif direction=='short' and z>=-f: pos={'entries':[(i,c)],'hit':{f},'trail_active':False}
    return trades

def stats(trades):
    if not trades: return None
    pnls=np.array([t['pnl'] for t in trades])
    n=len(pnls);wins=(pnls>0).sum();net=pnls.sum()
    pp=pnls[pnls>0].sum();pl=abs(pnls[pnls<=0].sum())
    pf=pp/pl if pl else 0
    return {'n':n,'wr':wins/n*100,'net':net,'pf':pf}

# Test pairs
TEST_CONFIGS = [
    {'pair':'EURGBP','tf':'1D','ma':30,'levels':[-0.5,-1.0,-1.5,-2.0],'dir':'long'},
    {'pair':'AUDCAD','tf':'30min','ma':200,'levels':[-1.0,-1.5,-2.0,-2.5],'dir':'long'},
    {'pair':'GBPCHF','tf':'1D','ma':50,'levels':[-0.5,-1.0,-1.5,-2.0],'dir':'short'},
    {'pair':'GBPNZD','tf':'1h','ma':100,'levels':[-0.5,-1.0,-1.5,-2.0],'dir':'long'},
]

print("Testing STOP variations + TRAILING...", flush=True)

for cfg in TEST_CONFIGS:
    print(f"\n--- {cfg['pair']} {cfg['tf']} {cfg['dir']} ---")
    df = pd.read_csv(PAIR_FILES[cfg['pair']], index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]
    df_tf = aggregate(df, cfg['tf'])
    arrs = precompute(df_tf, cfg['ma'])
    cost = PAIRS_INFO[cfg['pair']]['cost']
    pip_mul = PAIRS_INFO[cfg['pair']]['pip']

    # Baseline + stop variations
    print("  Stop variations:")
    for stop in [-3.0, -3.5, -4.0, -5.0, -6.0]:
        trades = bt_stop_test(arrs, cfg['dir'], cfg['levels'], stop, cost, pip_mul)
        s = stats(trades)
        if s:
            print(f"    Stop {stop}: n={s['n']:>4} WR{s['wr']:.1f}% Net=${s['net']:+,.0f} PF{s['pf']:.2f}")

    # Trailing stop
    print("  Trailing stop:")
    for trail_z in [0.3, 0.5, 0.7, 1.0]:
        trades = bt_trailing(arrs, cfg['dir'], cfg['levels'], -4.0, cost, pip_mul, trail_z)
        s = stats(trades)
        if s:
            print(f"    Trail @z>=-{trail_z}: n={s['n']:>4} WR{s['wr']:.1f}% Net=${s['net']:+,.0f} PF{s['pf']:.2f}")

print("\nDONE")
