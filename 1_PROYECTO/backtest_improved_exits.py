"""
TEST EXIT MECHANISMS — TP partial, BE move, Trailing
=====================================================
Compara 4 exit strategies sobre les top configs:
1. SIMPLE: tot a SMA (baseline)
2. TP_PARTIAL: 50% a meitat, 50% a SMA
3. BE_MOVE: mou stop a entry quan 50% al TP
4. TRAILING: trail per ATR un cop in profit
"""
import pandas as pd
import numpy as np

CAPITAL_INICIAL = 63000.0
LOT = 0.05

PIP_MUL = {'EURGBP':1000,'EURCHF':1000,'EURJPY':100,'CHFJPY':100,'XAUUSD':1,'AUDNZD':1000}
COSTS = {'EURGBP':0.30,'EURCHF':0.30,'EURJPY':0.30,'CHFJPY':0.40,'XAUUSD':0.50,'AUDNZD':0.40}
PAIR_FILES = {
    'EURGBP':'eurgbp_dk_m5_5y.csv','EURCHF':'eurchf_dk_m5_5y.csv',
    'EURJPY':'eurjpy_dk_m5_5y.csv','CHFJPY':'chfjpy_dk_m5_5y.csv',
}

def aggregate(df_, rule):
    return df_.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last')).dropna()

def precompute(df_, ma_p):
    return {
        'O':df_['open'].values,'H':df_['high'].values,
        'L':df_['low'].values,'C':df_['close'].values,
        'SMA':df_['close'].rolling(ma_p).mean().values,
        'STD':df_['close'].rolling(ma_p).std().values,
        'TS':df_.index, 'n':len(df_)
    }

def bt_simple(arrs, direction, levels, stop_z, cost, pip_mul):
    """Baseline: tot a SMA o SL"""
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
        if pos is None:
            f=levels[0]
            if direction=='long' and z<=f: pos={'entries':[(i,c)],'hit':{f}}
            elif direction=='short' and z>=-f: pos={'entries':[(i,c)],'hit':{f}}
    return trades

def bt_partial(arrs, direction, levels, stop_z, cost, pip_mul):
    """50% a meitat (z=lvl1/2), 50% a SMA"""
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
            # Half-target: z back to half of first entry level
            half_z = levels[0]/2  # e.g., -0.5 if level 1 is -1.0
            if direction=='long': half_target = z >= half_z
            else: half_target = z <= -half_z
            partial_done = pos.get('partial_done', False)
            if not partial_done and half_target and len(pos['entries']) >= 2:
                # Close 50% (half the entries)
                n_close = len(pos['entries']) // 2
                closed_entries = pos['entries'][:n_close]
                pos['entries'] = pos['entries'][n_close:]
                pnl=0;sgn=1 if direction=='long' else -1
                for eidx,ep in closed_entries: pnl+=(c-ep)*sgn
                pnl_d = pnl*pip_mul*(LOT/0.01) - len(closed_entries)*cost*(LOT/0.01)
                trades.append({'open_ts':arrs['TS'][closed_entries[0][0]],
                                'close_ts':arrs['TS'][i],'pnl':pnl_d})
                pos['partial_done'] = True
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
        if pos is None:
            f=levels[0]
            if direction=='long' and z<=f: pos={'entries':[(i,c)],'hit':{f},'partial_done':False}
            elif direction=='short' and z>=-f: pos={'entries':[(i,c)],'hit':{f},'partial_done':False}
    return trades

def bt_be_move(arrs, direction, levels, stop_z, cost, pip_mul):
    """Move stop to breakeven (entry avg) when 50% to TP"""
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    SMA=arrs['SMA'];STD=arrs['STD']
    n=arrs['n']
    trades=[];pos=None
    for i in range(50,n):
        sma=SMA[i];std=STD[i];c=C[i]
        if np.isnan(sma) or np.isnan(std) or std<=0: continue
        z=(c-sma)/std
        if pos is not None:
            avg_entry = sum(p[1] for p in pos['entries']) / len(pos['entries'])
            sgn=1 if direction=='long' else -1
            current_progress = (c - avg_entry) * sgn
            target_dist = (sma - avg_entry) * sgn
            be_active = pos.get('be_active', False)
            if not be_active and target_dist > 0 and current_progress >= target_dist * 0.5:
                pos['be_active'] = True
                pos['be_price'] = avg_entry

            # Stop: original SL OR BE if active
            if be_active or pos.get('be_active', False):
                # Hit BE
                hit_be = (c <= pos['be_price']) if direction=='long' else (c >= pos['be_price'])
                target_hit = c >= sma if direction=='long' else c <= sma
                if hit_be or target_hit or (i-pos['entries'][0][0])>=500:
                    tot_u=len(pos['entries']);pnl=0
                    for eidx,ep in pos['entries']: pnl+=(c-ep)*sgn
                    pnl_d = pnl*pip_mul*(LOT/0.01) - tot_u*cost*(LOT/0.01)
                    trades.append({'open_ts':arrs['TS'][pos['entries'][0][0]],
                                    'close_ts':arrs['TS'][i],'pnl':pnl_d})
                    pos=None;continue
            else:
                if direction=='long': stop_hit=z<=stop_z;target=c>=sma
                else: stop_hit=z>=-stop_z;target=c<=sma
                if stop_hit or target or (i-pos['entries'][0][0])>=500:
                    tot_u=len(pos['entries']);pnl=0
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
        if pos is None:
            f=levels[0]
            if direction=='long' and z<=f: pos={'entries':[(i,c)],'hit':{f},'be_active':False,'be_price':0}
            elif direction=='short' and z>=-f: pos={'entries':[(i,c)],'hit':{f},'be_active':False,'be_price':0}
    return trades

def stats(trades):
    if not trades: return None
    pnls=np.array([t['pnl'] for t in trades])
    n=len(pnls);wins=(pnls>0).sum();net=pnls.sum()
    pp=pnls[pnls>0].sum();pl=abs(pnls[pnls<=0].sum())
    pf=pp/pl if pl else 0
    return {'n':n,'wr':wins/n*100 if n>0 else 0,'net':net,'pf':pf}

# Test configs (top performers from previous backtests)
TEST_CONFIGS = [
    {'name':'EURGBP D1 LONG','pair':'EURGBP','tf':'1D','ma':100,'levels':[-0.5,-1.0,-1.5,-2.0],'stop':-4.0,'dir':'long'},
    {'name':'EURGBP H4 LONG','pair':'EURGBP','tf':'4h','ma':300,'levels':[-1.0,-1.5,-2.5,-3.0],'stop':-5.0,'dir':'long'},
    {'name':'EURGBP H4 SHORT','pair':'EURGBP','tf':'4h','ma':300,'levels':[-1.0,-1.5,-2.5,-3.0],'stop':-5.0,'dir':'short'},
    {'name':'EURJPY H4 LONG','pair':'EURJPY','tf':'4h','ma':300,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-5.0,'dir':'long'},
    {'name':'EURCHF H4 SHORT','pair':'EURCHF','tf':'4h','ma':150,'levels':[-1.5,-2.5,-3.5,-4.0],'stop':-6.0,'dir':'short'},
    {'name':'CHFJPY H4 LONG','pair':'CHFJPY','tf':'4h','ma':300,'levels':[-0.5,-1.5,-2.5,-3.0],'stop':-4.0,'dir':'long'},
]

print("Testing 3 exit mechanisms vs SIMPLE baseline...", flush=True)

results = []
for cfg in TEST_CONFIGS:
    print(f"\n--- {cfg['name']} ---", flush=True)
    df = pd.read_csv(PAIR_FILES[cfg['pair']], index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]
    df_tf = aggregate(df, cfg['tf'])
    arrs = precompute(df_tf, cfg['ma'])
    cost = COSTS[cfg['pair']]; pip_mul = PIP_MUL[cfg['pair']]

    for exit_name, exit_fn in [('SIMPLE',bt_simple),('PARTIAL',bt_partial),('BE_MOVE',bt_be_move)]:
        trades = exit_fn(arrs, cfg['dir'], cfg['levels'], cfg['stop'], cost, pip_mul)
        s = stats(trades)
        if s:
            print(f"  {exit_name:<10}: n={s['n']:>4} WR{s['wr']:>5.1f}% Net=${s['net']:>+8,.0f} PF{s['pf']:>5.2f}")
            results.append({'cfg':cfg['name'],'exit':exit_name,**s})

print()
print("="*120)
print("RESUM CONJUNT:")
print("="*120)
df_r = pd.DataFrame(results)
pivot = df_r.pivot(index='cfg', columns='exit', values='net')
print(pivot.to_string())

print()
print("Total per exit:")
for ex in ['SIMPLE','PARTIAL','BE_MOVE']:
    sub = df_r[df_r['exit']==ex]
    print(f"  {ex:<10}: Total Net=${sub['net'].sum():+,.0f}")

print("\nDONE")
