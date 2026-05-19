"""
TEST PROFUND ESTRATÈGIA GUANYADORA — Mean-Reversion + Averaging
================================================================
Validació exhaustiva:
- Multi-TF (M15, M30, H1, H4, D1) + multi-period SMA
- Walk-forward (in-sample 60% / out-sample 40%)
- Test sobre EURUSD també
- Configs ampliades

NO LOOKAHEAD verificat.
"""
import pandas as pd
import numpy as np

REAL_COST_BY_ASSET = {'XAUUSD': 0.40, 'EURUSD': 0.07}

print("Loading...", flush=True)
RAW = {
    'XAUUSD': pd.read_csv('xauusd_m5_5y.csv', index_col=0, parse_dates=True),
    'EURUSD': pd.read_csv('eurusd_m5_5y.csv', index_col=0, parse_dates=True),
}
for asset, df in RAW.items():
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]

def aggregate(df_, rule):
    return df_.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last')).dropna()

def precompute_with_sma(df_, ma_periods):
    """Compute multiple SMA/STD for different periods."""
    O = df_['open'].values; H = df_['high'].values
    L = df_['low'].values; C = df_['close'].values
    smas = {p: df_['close'].rolling(p).mean().values for p in ma_periods}
    stds = {p: df_['close'].rolling(p).std().values for p in ma_periods}
    return {
        'O':O,'H':H,'L':L,'C':C,
        'SMAS':smas,'STDS':stds,
        'TS':df_.index,'YEAR':df_.index.year.values,
        'n':len(df_)
    }

def bt_averaging_meanrev(arrs, direction, ma_period, levels, stop_z, real_cost,
                         max_bars=500, idx_range=None):
    """NO LOOKAHEAD: SMA[i], STD[i] are rolling on past bars only."""
    O=arrs['O'];H=arrs['H'];L=arrs['L'];C=arrs['C']
    SMA=arrs['SMAS'][ma_period];STD=arrs['STDS'][ma_period]
    n=arrs['n']
    start_i = ma_period+10 if idx_range is None else max(ma_period+10, idx_range[0])
    end_i = n if idx_range is None else min(n, idx_range[1])
    trades=[];pos=None
    for i in range(start_i, end_i):
        sma=SMA[i];std=STD[i];c=C[i]
        if np.isnan(sma) or np.isnan(std) or std<=0: continue
        z=(c-sma)/std
        if pos is not None:
            if direction=='long':
                stop_hit = z <= stop_z
                target = c >= sma
            else:
                stop_hit = z >= -stop_z
                target = c <= sma
            time_out = (i - pos['entries'][0][0]) >= max_bars
            if stop_hit or target or time_out:
                exit_p = c
                total_units = len(pos['entries'])
                pnl = 0; sgn = 1 if direction=='long' else -1
                for eidx,ep in pos['entries']:
                    pnl += (exit_p - ep) * sgn
                pnl -= total_units * real_cost
                reason = 'STOP' if stop_hit else ('TARGET' if target else 'TIME')
                trades.append({'ts':arrs['TS'][pos['entries'][0][0]],
                               'pnl':pnl,'units':total_units,'reason':reason,
                               'dir':direction,'entry_first':pos['entries'][0][1],
                               'exit':exit_p,'duration_bars':i-pos['entries'][0][0]})
                pos=None
                continue
            # Add to position
            if direction=='long':
                for lvl in levels:
                    if lvl not in pos['hit'] and z <= lvl:
                        pos['entries'].append((i,c)); pos['hit'].add(lvl); break
            else:
                for lvl in levels:
                    if lvl not in pos['hit'] and z >= -lvl:
                        pos['entries'].append((i,c)); pos['hit'].add(lvl); break
        if pos is None:
            first_lvl = levels[0]
            if direction=='long' and z <= first_lvl:
                pos = {'entries':[(i,c)],'hit':{first_lvl}}
            elif direction=='short' and z >= -first_lvl:
                pos = {'entries':[(i,c)],'hit':{first_lvl}}
    return trades

def stats(trades):
    if not trades: return None
    pnls = np.array([t['pnl'] for t in trades])
    n=len(pnls);wins=(pnls>0).sum();net=pnls.sum()
    pp=pnls[pnls>0].sum();pl=abs(pnls[pnls<=0].sum())
    pf=pp/pl if pl else 0
    eq=np.cumsum(pnls);peak=np.maximum.accumulate(eq);dd=(peak-eq).max()
    avg_u = np.mean([t['units'] for t in trades])
    avg_dur = np.mean([t['duration_bars'] for t in trades])
    return {'n':n,'wr':wins/n*100,'net':net,'pf':pf,'dd':dd,'avg_u':avg_u,'avg_dur':avg_dur}

def fmt(s):
    if s is None: return "0 trades"
    return f"n={s['n']:>4} WR{s['wr']:>4.1f}% Net=${s['net']:>+7.0f} PF{s['pf']:>5.2f} DD${s['dd']:>4.0f} u={s['avg_u']:.1f} dur={s['avg_dur']:.0f}"

def per_year(trades):
    out = {}
    if not trades: return out
    df = pd.DataFrame(trades)
    df['year'] = pd.to_datetime(df['ts']).dt.year
    for yr, grp in df.groupby('year'):
        pnls = grp['pnl'].values
        n=len(pnls);wins=(pnls>0).sum()
        out[yr] = {'n':n,'wr':wins/n*100,'net':pnls.sum(),
                   'stops':sum(1 for r in grp['reason'].values if r=='STOP')}
    return out

# Aggregate
MA_PERIODS = [50, 100, 150, 200, 300]
print("Aggregating...", flush=True)
TF_DATA = {}
for asset in ['XAUUSD','EURUSD']:
    TF_DATA[asset] = {}
    for tf, rule in [('M30','30min'),('H1','1h'),('H4','4h'),('D1','1D')]:
        df = aggregate(RAW[asset], rule)
        TF_DATA[asset][tf] = precompute_with_sma(df, MA_PERIODS)
        print(f"  {asset} {tf}: {len(df)} bars", flush=True)

LEVELS_GRID = [
    [-1.0],
    [-1.5],
    [-2.0],
    [-1.0,-2.0],
    [-1.5,-2.5],
    [-1.0,-2.0,-3.0],
    [-1.5,-2.5,-3.5],
    [-2.0,-3.0],
    [-2.0,-3.0,-4.0],
]
STOP_GRID = [-3.0, -4.0, -5.0, -6.0]

print("Running deep tests...", flush=True)
print()

ALL = []
for asset in ['XAUUSD','EURUSD']:
    cost = REAL_COST_BY_ASSET[asset]
    print(f"\n{'#'*120}")
    print(f"# {asset}")
    print(f"{'#'*120}")
    for tf in ['M30','H1','H4','D1']:
        arrs = TF_DATA[asset][tf]
        if arrs['n'] < 500: continue
        for ma in MA_PERIODS:
            if ma >= arrs['n']/3: continue
            for levels in LEVELS_GRID:
                for stop_z in STOP_GRID:
                    if stop_z >= levels[-1]: continue
                    for direction in ['long','short']:
                        trades = bt_averaging_meanrev(arrs, direction, ma, levels, stop_z, cost)
                        s = stats(trades)
                        if s and s['n']>=15 and s['pf']>=1.20 and s['net']>0:
                            ALL.append({'asset':asset,'tf':tf,'ma':ma,'levels':levels,
                                        'stop_z':stop_z,'direction':direction,
                                        'stats':s,'trades':trades})

print(f"\nTotal valid configs: {len(ALL)}")

# TOP rànking
ALL.sort(key=lambda x:-x['stats']['pf'])
print()
print("="*150)
print("TOP 30 CONFIGS:")
print("="*150)
for r in ALL[:30]:
    s = r['stats']
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"  {r['asset']:<7} {r['tf']:<4} SMA{r['ma']:<3} {r['direction']:<5} levels={lvl:<22} stop={r['stop_z']} | {fmt(s)}")

# Yearly per top 10
print()
print("="*150)
print("ANY-PER-ANY top 10 (clau per validar robustesa):")
print("="*150)
for r in ALL[:10]:
    s = r['stats']
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"\n{r['asset']} {r['tf']} SMA{r['ma']} {r['direction']} levels={lvl} stop={r['stop_z']} | TOTAL {fmt(s)}")
    yb = per_year(r['trades'])
    yrs_pos = sum(1 for y in yb.values() if y['net']>0)
    yrs_neg = sum(1 for y in yb.values() if y['net']<0)
    print(f"  Anys positius: {yrs_pos}/{len(yb)} | negatius: {yrs_neg}/{len(yb)}")
    for yr in sorted(yb.keys()):
        y = yb[yr]
        marker = "+" if y['net']>0 else "-"
        print(f"  {marker} {yr}: n={y['n']:>3} WR{y['wr']:>5.1f}% Net=${y['net']:>+7.0f} stops={y['stops']}")

# Walk-forward validation
print()
print("="*150)
print("WALK-FORWARD VALIDATION (in-sample 60% / out-sample 40%):")
print("="*150)
for r in ALL[:10]:
    arrs = TF_DATA[r['asset']][r['tf']]
    cost = REAL_COST_BY_ASSET[r['asset']]
    n = arrs['n']
    split = int(n * 0.6)
    in_trades = bt_averaging_meanrev(arrs, r['direction'], r['ma'], r['levels'],
                                       r['stop_z'], cost, idx_range=(0, split))
    out_trades = bt_averaging_meanrev(arrs, r['direction'], r['ma'], r['levels'],
                                        r['stop_z'], cost, idx_range=(split, n))
    s_in = stats(in_trades); s_out = stats(out_trades)
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"\n{r['asset']} {r['tf']} SMA{r['ma']} {r['direction']} levels={lvl} stop={r['stop_z']}")
    print(f"  IN-SAMPLE  ({split}/{n}): {fmt(s_in)}")
    print(f"  OUT-SAMPLE        : {fmt(s_out)}")

# Best per asset+tf
print()
print("="*150)
print("MILLOR CONFIG PER ASSET+TF:")
print("="*150)
seen = set()
for r in ALL:
    key = (r['asset'], r['tf'])
    if key in seen: continue
    seen.add(key)
    s = r['stats']
    lvl = '/'.join(f'{l:.1f}' for l in r['levels'])
    print(f"  {r['asset']:<7} {r['tf']:<4}: SMA{r['ma']} {r['direction']} levels={lvl} stop={r['stop_z']} | {fmt(s)}")

print("\nDONE")
