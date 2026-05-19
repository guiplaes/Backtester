"""
RECÀLCUL amb costs REALS de VT Markets segons mida lot.
Spread XAUUSD ~30 punts = $0.30 per 0.01 lot round-trip.
Slippage ~10 punts = $0.10 per 0.01 lot round-trip.
Standard account: 0 comissió.

Per CADA mida lot, recalculo el ULTIMATE config (PF 4.61).
"""
import pandas as pd
import numpy as np

CSV = "xauusd_m5_5y.csv"

m5 = pd.read_csv(CSV, index_col=0, parse_dates=True)
m5.index = pd.to_datetime(m5.index, utc=True)
m5.columns = [c.lower() for c in m5.columns]
if 'tick_volume' not in m5.columns: m5['tick_volume'] = m5.get('volume', 1)

h4 = m5.resample('4h').agg(open=('open','first'),high=('high','max'),
    low=('low','min'),close=('close','last'),tick_volume=('tick_volume','sum')).dropna()
h4['ema50'] = h4['close'].ewm(span=50, adjust=False).mean()
hl = h4['high']-h4['low']; hc = (h4['high']-h4['close'].shift()).abs(); lc = (h4['low']-h4['close'].shift()).abs()
tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
h4['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()

d1 = m5.resample('1D').agg(open=('open','first'),high=('high','max'),
    low=('low','min'),close=('close','last')).dropna()
d1['don_high_55'] = d1['high'].rolling(55).max().shift(1)
d1['don_low_20'] = d1['low'].rolling(20).min().shift(1)
d1_in_long = pd.Series(False, index=d1.index)
in_pos = False
for i in range(56, len(d1)):
    if not in_pos:
        if d1.iloc[i]['close'] > d1.iloc[i]['don_high_55']: in_pos = True
    else:
        if d1.iloc[i]['close'] < d1.iloc[i]['don_low_20']: in_pos = False
    d1_in_long.iloc[i] = in_pos

def is_donchian_long(ts):
    d = pd.Timestamp(ts).normalize()
    if d in d1_in_long.index: return d1_in_long.loc[d]
    prior = d1_in_long[d1_in_long.index <= d]
    return prior.iloc[-1] if len(prior) else False

SESSION_PARAMS = {
    'ASIA': (1.0, 3, 6, 2.0),
    'LONDON': (1.0, 2, 4, 1.8),
    'NY': (1.0, 2, 4, 1.5),
}

def get_session(h):
    if h < 7: return 'ASIA'
    elif h < 13: return 'LONDON'
    elif h < 16: return 'OVERLAP'
    elif h < 21: return 'NY'
    else: return 'DEAD'

def s2_ob(df_, lot_size, broker_account='standard'):
    """
    lot_size: 0.01, 0.05, 0.1, 0.5, 1.0
    broker_account: 'standard' (no commission) or 'ecn' (with commission)
    """
    # P&L scales linearly with lot size (vs 0.01 = 1 unit baseline)
    lot_mult = lot_size / 0.01

    # Costs (real VT Markets)
    if broker_account == 'standard':
        # Spread ~30 points = $0.30/0.01 lot RT, 0 commission, slippage ~$0.10
        cost_per_001lot = 0.30 + 0.10  # $0.40 per 0.01 lot round-trip
    else:  # ecn
        # Spread ~12 points = $0.12, commission $0.07/0.01 lot side × 2 = $0.14, slippage $0.10
        cost_per_001lot = 0.12 + 0.14 + 0.10  # $0.36 per 0.01 lot round-trip

    cost_per_trade = cost_per_001lot * lot_mult  # scales with lot

    trades = []; pos = None; pending_obs = []
    for i in range(50, len(df_)-3):
        bar = df_.iloc[i]; ts = df_.index[i]
        if pos is not None:
            sl_h = bar['low'] <= pos['sl']
            tp1_h = bar['high'] >= pos['tp1']
            tp2_h = bar['high'] >= pos['tp2']
            if sl_h and (tp1_h or tp2_h):
                if (bar['open']-pos['sl']) < (pos['tp1']-bar['open']): tp1_h=False; tp2_h=False
            # P&L per trade scales with lot_mult
            if tp1_h and pos['q1']>0: pos['pnl1'] = (pos['tp1']-pos['e'])*0.5*lot_mult; pos['q1']=0
            if tp2_h and pos['q2']>0: pos['pnl2'] = (pos['tp2']-pos['e'])*0.5*lot_mult; pos['q2']=0
            if sl_h:
                if pos['q1']>0: pos['pnl1'] = (pos['sl']-pos['e'])*0.5*lot_mult; pos['q1']=0
                if pos['q2']>0: pos['pnl2'] = (pos['sl']-pos['e'])*0.5*lot_mult; pos['q2']=0
            if pos['q1']==0 and pos['q2']==0:
                # Subtract realistic round-trip cost
                tp = pos.get('pnl1',0)+pos.get('pnl2',0) - cost_per_trade
                trades.append({'ts': pos['ts'], 'pnl': tp})
                pos = None
        b0 = df_.iloc[i-1]
        is_red = b0['close'] < b0['open']
        sess = get_session(ts.hour)
        params = SESSION_PARAMS.get(sess)
        if params is None:
            pending_obs = [o for o in pending_obs if o['expiry'] > i]; continue
        sl_atr, tp1_atr, tp2_atr, ob_str = params
        if is_red and not pd.isna(b0['atr']):
            future_high = max(df_.iloc[i]['high'], df_.iloc[i+1]['high'], df_.iloc[i+2]['high'])
            move = future_high - b0['close']
            if move > ob_str * b0['atr']:
                pending_obs.append({'ob_low':b0['low'],'ob_high':b0['high'],'expiry':i+30,'atr0':b0['atr']})
        pending_obs = [o for o in pending_obs if o['expiry'] > i]
        if pos is None and pd.notna(bar['ema50']) and bar['close'] > bar['ema50']:
            if ts.weekday() == 3: continue
            if not is_donchian_long(ts): continue
            for ob in list(pending_obs):
                if bar['low'] <= ob['ob_high'] and bar['close'] > ob['ob_low']:
                    if bar['close'] > bar['open']:
                        atr = ob['atr0']; e = bar['close']
                        pos = {'side':'L','e':e,'ts':ts,
                            'sl':ob['ob_low']-atr*sl_atr*0.5,
                            'tp1':e+atr*tp1_atr,'tp2':e+atr*tp2_atr,'q1':0.5,'q2':0.5}
                        pending_obs.remove(ob); break
    return trades

def apply_streak(trades, k=1.3, min_size=0.5, max_size=2.0):
    out = []; size = 1.0; consec_l = 0; consec_w = 0
    for t in trades:
        out.append(t['pnl']*size)
        if t['pnl'] > 0:
            consec_w += 1; consec_l = 0
            if consec_w >= 3 and size < max_size: size = min(max_size, size*k)
            if consec_w == 1 and size < 1.0: size = 1.0
        else:
            consec_l += 1; consec_w = 0
            if consec_l >= 2: size = max(min_size, size*0.7)
    return np.array(out)

def stats(arr, name, capital=10000):
    if len(arr)==0: print(f"{name}: 0"); return
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    pct = net/capital*100
    annual = pct/5  # 5 anys
    print(f"{name:>40}: n={n:>3} | Net ${net:+.0f} ({pct:+.1f}% / {annual:+.1f}%/any) | PF {pf:.2f} | DD ${dd:.0f} ({dd/capital*100:.1f}%)", flush=True)

print()
print("="*120)
print("RECÀLCUL AMB COSTS REALS VT MARKETS (Standard Account)")
print("Cost per 0.01 lot round-trip: $0.40 (spread $0.30 + slippage $0.10, 0 comissió)")
print("="*120)

print()
print("CAPITAL: $10,000")
print()
for lot in [0.01, 0.05, 0.10, 0.20, 0.50, 1.0]:
    print(f"\n>>> LOT SIZE: {lot}")
    trades = s2_ob(h4, lot, 'standard')
    arr_raw = np.array([t['pnl'] for t in trades])
    stats(arr_raw, f"  Fixed {lot} (no streak)")
    sized = apply_streak(trades)
    stats(sized, f"  Fixed {lot} + Streak sizing")

print()
print("="*120)
print("AMB ECN ACCOUNT (spread reduït + comissió):")
print("Cost per 0.01 lot RT: $0.36 (spread $0.12 + commission $0.14 + slippage $0.10)")
print("="*120)
for lot in [0.05, 0.10, 0.50]:
    print(f"\n>>> LOT SIZE: {lot} (ECN)")
    trades = s2_ob(h4, lot, 'ecn')
    sized = apply_streak(trades)
    stats(sized, f"  Fixed {lot} + Streak (ECN)")

# Comparativa final
print()
print("="*120)
print("RESUM SIMPLE — Què esperar amb VT Markets Standard, $10k account, 5 anys:")
print("="*120)
print()
print(f"{'Lot':>6} | {'Trades':>6} | {'Net 5y':>10} | {'%/any':>7} | {'DD':>8} | {'DD%':>5}")
print("-"*70)
for lot in [0.01, 0.05, 0.10, 0.20, 0.50, 1.0]:
    trades = s2_ob(h4, lot, 'standard')
    sized = apply_streak(trades)
    net = sized.sum()
    pct_per_year = net/10000/5*100
    eq = np.cumsum(sized); peak = np.maximum.accumulate(eq); dd = (peak-eq).max()
    print(f"  {lot:>4} | {len(trades):>6} | ${net:>+8.0f} | {pct_per_year:>+5.1f}% | ${dd:>5.0f} | {dd/10000*100:>4.1f}%")
