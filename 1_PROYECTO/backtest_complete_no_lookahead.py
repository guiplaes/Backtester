"""
BACKTEST COMPLET SENSE LOOKAHEAD BIAS
=====================================
Mother bar a X-3, futurs (X-2, X-1, X) ja són passats — sense saber el futur.

TFs: M5, M15, M30, H1, H4
Direccions: LONG, SHORT, BOTH
Params grid: múltiples combinacions
"""
import pandas as pd
import numpy as np
import time
import sys

CSV = "xauusd_m5_5y.csv"
REAL_COST = 0.40

print("Loading data...", flush=True)
m5 = pd.read_csv(CSV, index_col=0, parse_dates=True)
m5.index = pd.to_datetime(m5.index, utc=True)
m5.columns = [c.lower() for c in m5.columns]
if 'tick_volume' not in m5.columns: m5['tick_volume'] = m5.get('volume', 1)
print(f"M5: {len(m5)} bars", flush=True)

def aggregate(df_, rule):
    return df_.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last'),tick_volume=('tick_volume','sum')).dropna()

def add_ind(df_):
    df_ = df_.copy()
    df_['ema50'] = df_['close'].ewm(span=50, adjust=False).mean()
    hl = df_['high']-df_['low']
    hc = (df_['high']-df_['close'].shift()).abs()
    lc = (df_['low']-df_['close'].shift()).abs()
    tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
    df_['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
    return df_

print("Aggregating timeframes...", flush=True)
TFS = {
    'M5':  add_ind(m5),
    'M15': add_ind(aggregate(m5, '15min')),
    'M30': add_ind(aggregate(m5, '30min')),
    'H1':  add_ind(aggregate(m5, '1h')),
    'H4':  add_ind(aggregate(m5, '4h')),
}
for k, v in TFS.items():
    print(f"  {k}: {len(v)} bars", flush=True)

def get_session(h):
    if h < 7: return 'ASIA'
    elif h < 13: return 'LONDON'
    elif h < 21: return 'NY' if h >= 16 else 'OVERLAP'
    else: return 'DEAD'

def precompute(df_):
    return {
        'O': df_['open'].values, 'H': df_['high'].values,
        'L': df_['low'].values, 'C': df_['close'].values,
        'EMA': df_['ema50'].values, 'ATR': df_['atr'].values,
        'sess_active': np.array([get_session(t.hour) in {'ASIA','LONDON','NY'} for t in df_.index]),
        'n': len(df_)
    }

def bt_no_lookahead(arrs, direction, sl_atr, tp1_atr, tp2_atr, ob_str):
    """
    NO LOOKAHEAD — l'OB es detecta al bar X amb mother a X-3.
    Tots els bars usats (X-3, X-2, X-1, X) ja són passats.
    """
    O = arrs['O']; H = arrs['H']; L = arrs['L']; C = arrs['C']
    EMA = arrs['EMA']; ATR = arrs['ATR']
    sess_active = arrs['sess_active']
    n = arrs['n']

    trades = []
    pos = None
    pending_lows = []; pending_highs = []; pending_atrs = []; pending_expiry = []

    for i in range(50, n):
        # Manage open position at bar i
        if pos is not None:
            if direction == 'long':
                sl_h = L[i] <= pos[0]
                tp1_h = H[i] >= pos[1]
                tp2_h = H[i] >= pos[2]
            else:
                sl_h = H[i] >= pos[0]
                tp1_h = L[i] <= pos[1]
                tp2_h = L[i] <= pos[2]
            if sl_h and (tp1_h or tp2_h):
                if direction == 'long':
                    if (O[i]-pos[0]) < (pos[1]-O[i]): tp1_h=False; tp2_h=False
                else:
                    if (pos[0]-O[i]) < (O[i]-pos[1]): tp1_h=False; tp2_h=False
            sgn = 1 if direction == 'long' else -1
            pnl1 = pos[3]; pnl2 = pos[4]; q1 = pos[5]; q2 = pos[6]; e = pos[7]
            if tp1_h and q1>0: pnl1 = (pos[1]-e)*0.5*sgn; q1=0
            if tp2_h and q2>0: pnl2 = (pos[2]-e)*0.5*sgn; q2=0
            if sl_h:
                if q1>0: pnl1 = (pos[0]-e)*0.5*sgn; q1=0
                if q2>0: pnl2 = (pos[0]-e)*0.5*sgn; q2=0
            if q1==0 and q2==0:
                trades.append(pnl1+pnl2 - REAL_COST)
                pos = None
            else:
                pos = (pos[0], pos[1], pos[2], pnl1, pnl2, q1, q2, e)

        # OB detection at bar i (mother is at i-3, ALL past)
        if i >= 3:
            mc = C[i-3]; mo = O[i-3]; ml = L[i-3]; mh = H[i-3]; ma = ATR[i-3]
            if direction == 'long':
                if mc < mo and not np.isnan(ma):
                    move = max(H[i-2], H[i-1], H[i]) - mc
                    if move > ob_str * ma:
                        pending_lows.append(ml); pending_highs.append(mh)
                        pending_atrs.append(ma); pending_expiry.append(i + 30)
            else:
                if mc > mo and not np.isnan(ma):
                    move = mc - min(L[i-2], L[i-1], L[i])
                    if move > ob_str * ma:
                        pending_lows.append(ml); pending_highs.append(mh)
                        pending_atrs.append(ma); pending_expiry.append(i + 30)

        # Clean expired
        keep = [j for j, exp in enumerate(pending_expiry) if exp > i]
        if len(keep) < len(pending_expiry):
            pending_lows = [pending_lows[j] for j in keep]
            pending_highs = [pending_highs[j] for j in keep]
            pending_atrs = [pending_atrs[j] for j in keep]
            pending_expiry = [pending_expiry[j] for j in keep]

        # Entry attempt at bar i
        if pos is None and not np.isnan(EMA[i]) and sess_active[i]:
            cond_trend = (C[i] > EMA[i]) if direction == 'long' else (C[i] < EMA[i])
            if cond_trend:
                for j in range(len(pending_lows)):
                    obl = pending_lows[j]; obh = pending_highs[j]; oba = pending_atrs[j]
                    if direction == 'long':
                        cond_in = L[i] <= obh and C[i] > obl
                        cond_rev = C[i] > O[i]
                    else:
                        cond_in = H[i] >= obl and C[i] < obh
                        cond_rev = C[i] < O[i]
                    if cond_in and cond_rev:
                        if direction == 'long':
                            sl = obl - oba * sl_atr * 0.5
                            tp1 = C[i] + oba * tp1_atr
                            tp2 = C[i] + oba * tp2_atr
                        else:
                            sl = obh + oba * sl_atr * 0.5
                            tp1 = C[i] - oba * tp1_atr
                            tp2 = C[i] - oba * tp2_atr
                        pos = (sl, tp1, tp2, 0, 0, 0.5, 0.5, C[i])
                        pending_lows.pop(j); pending_highs.pop(j); pending_atrs.pop(j); pending_expiry.pop(j)
                        break

    return trades

def stats(arr):
    if len(arr)==0: return None
    arr = np.array(arr)
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    return {'n':n,'wr':w/n*100,'net':net,'pf':pf,'dd':dd}

# Pre-compute arrays per TF
print("Precomputing arrays...", flush=True)
TF_ARRS = {tf: precompute(df) for tf, df in TFS.items()}
print("Done", flush=True)

# Grid
GRID = [
    (1.0, 2, 4, 1.5),
    (1.0, 2, 4, 2.0),
    (1.0, 2, 4, 2.5),
    (1.0, 3, 6, 2.0),
    (1.0, 3, 6, 2.5),
    (1.5, 3, 6, 2.5),
    (1.5, 5, 10, 2.5),
    (2.0, 5, 10, 2.5),
]

print()
print("="*150)
print("BACKTEST COMPLET SENSE LOOKAHEAD — Tots TFs, LONG/SHORT/BOTH:")
print(f"{'TF':<6} {'Direction':<10} {'SL':<5} {'TP1':<5} {'TP2':<5} {'OB':<5} {'Trades':>7} {'WR':>6} {'Net':>10} {'PF':>6} {'DD':>8}")
print("="*150)

results = []
trade_cache = {}  # (tf, direction, sl, tp1, tp2, ob) -> trades list

for tf_name, arrs in TF_ARRS.items():
    print(f"\n--- {tf_name} ({arrs['n']} bars) ---", flush=True)
    for sl, tp1, tp2, ob in GRID:
        for direction in ['long', 'short']:
            t0 = time.time()
            trades = bt_no_lookahead(arrs, direction, sl, tp1, tp2, ob)
            elapsed = time.time() - t0
            trade_cache[(tf_name, direction, sl, tp1, tp2, ob)] = trades
            s = stats(trades)
            if s and s['n'] >= 20:
                line = f"{tf_name:<6} {direction:<10} {sl:<5} {tp1:<5} {tp2:<5} {ob:<5} {s['n']:>7} {s['wr']:>5.1f}% {s['net']:>+9.0f} {s['pf']:>5.2f} {s['dd']:>7.0f} ({elapsed:.1f}s)"
                print(line, flush=True)
                results.append({
                    'tf': tf_name, 'direction': direction,
                    'sl': sl, 'tp1': tp1, 'tp2': tp2, 'ob': ob,
                    'n': s['n'], 'wr': s['wr'], 'net': s['net'], 'pf': s['pf'], 'dd': s['dd'],
                })

print()
print("="*150)
print("MILLOR config per TF (per cada direcció):")
print("="*150)

# Find best per TF/direction
import collections
best_by = collections.defaultdict(lambda: None)
for r in results:
    key = (r['tf'], r['direction'])
    if best_by[key] is None or r['pf'] > best_by[key]['pf']:
        if r['net'] > 0:  # only positive results
            best_by[key] = r

for tf in TFS.keys():
    for direction in ['long', 'short']:
        r = best_by[(tf, direction)]
        if r:
            print(f"  {tf} {direction}: SL{r['sl']} TP{r['tp1']}/{r['tp2']} OB{r['ob']} | n={r['n']} WR {r['wr']:.1f}% Net=${r['net']:+.0f} PF {r['pf']:.2f} DD ${r['dd']:.0f}")
        else:
            print(f"  {tf} {direction}: NO POSITIVE config found")

print()
print("="*150)
print("BOTH (LONG+SHORT amb mateixos params, PER CADA TF):")
print("="*150)
both_results = []
for tf_name in TF_ARRS.keys():
    print(f"\n--- {tf_name} BOTH ---", flush=True)
    for sl, tp1, tp2, ob in GRID:
        l_trades = trade_cache.get((tf_name, 'long', sl, tp1, tp2, ob), [])
        s_trades = trade_cache.get((tf_name, 'short', sl, tp1, tp2, ob), [])
        both = l_trades + s_trades
        s = stats(both)
        if s and s['n'] >= 30:
            mark = " <-- POSITIU" if s['net'] > 0 else ""
            line = f"  SL{sl} TP{tp1}/{tp2} OB{ob} | n={s['n']} WR {s['wr']:.1f}% Net=${s['net']:+.0f} PF {s['pf']:.2f} DD ${s['dd']:.0f}{mark}"
            print(line, flush=True)
            both_results.append({
                'tf': tf_name, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'ob': ob,
                'n': s['n'], 'wr': s['wr'], 'net': s['net'], 'pf': s['pf'], 'dd': s['dd']
            })

print()
print("="*150)
print("CONCLUSIO HONESTA — quina configuració realment funciona:")
print("="*150)

profitable = [r for r in results if r['net'] > 0 and r['pf'] > 1.1 and r['n'] >= 30]
profitable.sort(key=lambda x: -x['pf'])

if profitable:
    print(f"\n{len(profitable)} configs (LONG o SHORT separats) amb PF >= 1.1 i Net positiu:")
    for r in profitable[:20]:
        print(f"  {r['tf']:<5} {r['direction']:<8} SL{r['sl']} TP{r['tp1']}/{r['tp2']} OB{r['ob']} | n={r['n']:>4} WR {r['wr']:>5.1f}% Net=${r['net']:>+8.0f} PF {r['pf']:.2f} DD ${r['dd']:.0f}")
else:
    print("\nCap config LONG/SHORT individual amb PF >= 1.1!")

# BOTH profitable
both_pos = [r for r in both_results if r['net'] > 0 and r['pf'] > 1.1 and r['n'] >= 50]
both_pos.sort(key=lambda x: -x['pf'])
print()
if both_pos:
    print(f"{len(both_pos)} configs BOTH amb PF >= 1.1 i Net positiu:")
    for r in both_pos[:15]:
        print(f"  {r['tf']:<5} BOTH    SL{r['sl']} TP{r['tp1']}/{r['tp2']} OB{r['ob']} | n={r['n']:>4} WR {r['wr']:>5.1f}% Net=${r['net']:>+8.0f} PF {r['pf']:.2f} DD ${r['dd']:.0f}")
else:
    print("Cap config BOTH amb PF >= 1.1 i Net positiu!")
