"""Test SHORTS with the SMC OB ultimate config."""
import pandas as pd
import numpy as np

CSV = "xauusd_m5_5y.csv"
COMMISSION = 0.5; SPREAD = 0.50; SLIPPAGE = 0.20

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
d1['don_low_55'] = d1['low'].rolling(55).min().shift(1)
d1['don_high_20'] = d1['high'].rolling(20).max().shift(1)
d1['don_low_20'] = d1['low'].rolling(20).min().shift(1)

# Donchian regime — both LONG and SHORT
d1_in_long = pd.Series(False, index=d1.index)
d1_in_short = pd.Series(False, index=d1.index)
in_long = False; in_short = False
for i in range(56, len(d1)):
    bar = d1.iloc[i]
    # Long entry/exit
    if not in_long and bar['close'] > bar['don_high_55']: in_long = True
    elif in_long and bar['close'] < bar['don_low_20']: in_long = False
    # Short entry/exit
    if not in_short and bar['close'] < bar['don_low_55']: in_short = True
    elif in_short and bar['close'] > bar['don_high_20']: in_short = False
    d1_in_long.iloc[i] = in_long
    d1_in_short.iloc[i] = in_short

def is_donchian_long(ts):
    d = pd.Timestamp(ts).normalize()
    if d in d1_in_long.index: return d1_in_long.loc[d]
    prior = d1_in_long[d1_in_long.index <= d]
    return prior.iloc[-1] if len(prior) else False

def is_donchian_short(ts):
    d = pd.Timestamp(ts).normalize()
    if d in d1_in_short.index: return d1_in_short.loc[d]
    prior = d1_in_short[d1_in_short.index <= d]
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

def s2_ob(df_, mode='long', session_params=SESSION_PARAMS, donchian_filter=False, skip_thursday=True):
    """mode: 'long', 'short', 'both'"""
    trades = []; pos = None
    pending_obs = []
    for i in range(50, len(df_)-3):
        bar = df_.iloc[i]; ts = df_.index[i]
        if pos is not None:
            sgn = 1 if pos['side']=='L' else -1
            sl_h = bar['low'] <= pos['sl'] if pos['side']=='L' else bar['high'] >= pos['sl']
            tp1_h = bar['high'] >= pos['tp1'] if pos['side']=='L' else bar['low'] <= pos['tp1']
            tp2_h = bar['high'] >= pos['tp2'] if pos['side']=='L' else bar['low'] <= pos['tp2']
            if sl_h and (tp1_h or tp2_h):
                if pos['side']=='L':
                    if (bar['open']-pos['sl']) < (pos['tp1']-bar['open']): tp1_h=False; tp2_h=False
                else:
                    if (pos['sl']-bar['open']) < (bar['open']-pos['tp1']): tp1_h=False; tp2_h=False
            if tp1_h and pos['q1']>0: pos['pnl1'] = (pos['tp1']-pos['e'])*0.5*sgn - SLIPPAGE*0.5; pos['q1']=0
            if tp2_h and pos['q2']>0: pos['pnl2'] = (pos['tp2']-pos['e'])*0.5*sgn - SLIPPAGE*0.5; pos['q2']=0
            if sl_h:
                if pos['q1']>0: pos['pnl1'] = (pos['sl']-pos['e'])*0.5*sgn - SLIPPAGE*0.5; pos['q1']=0
                if pos['q2']>0: pos['pnl2'] = (pos['sl']-pos['e'])*0.5*sgn - SLIPPAGE*0.5; pos['q2']=0
            if pos['q1']==0 and pos['q2']==0:
                tp = pos.get('pnl1',0)+pos.get('pnl2',0)-COMMISSION*2-SPREAD
                trades.append({'ts': pos['ts'], 'pnl': tp, 'session': pos['session'], 'side': pos['side']})
                pos = None
        b0 = df_.iloc[i-1]
        is_red = b0['close'] < b0['open']
        is_green = b0['close'] > b0['open']
        sess = get_session(ts.hour)
        params = session_params.get(sess)
        if params is None:
            pending_obs = [o for o in pending_obs if o['expiry'] > i]; continue
        sl_atr, tp1_atr, tp2_atr, ob_str = params

        # Bullish OB (red candle before strong up move)
        if is_red and not pd.isna(b0['atr']):
            future_high = max(df_.iloc[i]['high'], df_.iloc[i+1]['high'], df_.iloc[i+2]['high'])
            move_up = future_high - b0['close']
            if move_up > ob_str * b0['atr']:
                pending_obs.append({'type':'L','low':b0['low'],'high':b0['high'],
                    'expiry':i+30,'atr0':b0['atr']})
        # Bearish OB (green candle before strong down move)
        if is_green and not pd.isna(b0['atr']):
            future_low = min(df_.iloc[i]['low'], df_.iloc[i+1]['low'], df_.iloc[i+2]['low'])
            move_dn = b0['close'] - future_low
            if move_dn > ob_str * b0['atr']:
                pending_obs.append({'type':'S','low':b0['low'],'high':b0['high'],
                    'expiry':i+30,'atr0':b0['atr']})

        pending_obs = [o for o in pending_obs if o['expiry'] > i]

        if pos is None:
            if skip_thursday and ts.weekday() == 3: continue

            # LONG entry
            if mode in ('long', 'both') and pd.notna(bar['ema50']) and bar['close'] > bar['ema50']:
                if donchian_filter and not is_donchian_long(ts): pass
                else:
                    for ob in list(pending_obs):
                        if ob['type'] != 'L': continue
                        if bar['low'] <= ob['high'] and bar['close'] > ob['low']:
                            if bar['close'] > bar['open']:
                                atr = ob['atr0']; e = bar['close']
                                pos = {'side':'L','e':e,'ts':ts,'session':sess,
                                    'sl':ob['low']-atr*sl_atr*0.5,
                                    'tp1':e+atr*tp1_atr,'tp2':e+atr*tp2_atr,'q1':0.5,'q2':0.5}
                                pending_obs.remove(ob); break

            # SHORT entry
            if pos is None and mode in ('short', 'both') and pd.notna(bar['ema50']) and bar['close'] < bar['ema50']:
                if donchian_filter and not is_donchian_short(ts): pass
                else:
                    for ob in list(pending_obs):
                        if ob['type'] != 'S': continue
                        if bar['high'] >= ob['low'] and bar['close'] < ob['high']:
                            if bar['close'] < bar['open']:
                                atr = ob['atr0']; e = bar['close']
                                pos = {'side':'S','e':e,'ts':ts,'session':sess,
                                    'sl':ob['high']+atr*sl_atr*0.5,
                                    'tp1':e-atr*tp1_atr,'tp2':e-atr*tp2_atr,'q1':0.5,'q2':0.5}
                                pending_obs.remove(ob); break
    return trades

def stats(trades, name):
    if not trades: print(f"{name:>50}: 0"); return None
    arr = np.array([t['pnl'] for t in trades])
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    sides = [t.get('side','L') for t in trades]
    nL = sides.count('L'); nS = sides.count('S')
    print(f"{name:>50}: n={n:>3}(L{nL}/S{nS}) | WR {w/n*100:>5.1f}% | Net ${net:+.0f} | PF {pf:.2f} | DD ${dd:.0f}", flush=True)
    return {'n':n,'pf':pf,'net':net,'dd':dd}

print()
print("="*120)
print("SMC OB SHORTS test (per-session + Skip Thu + Donchian D1 macro)")
print("="*120)

# LONG only (current ULTIMATE)
stats(s2_ob(h4, mode='long', donchian_filter=False), "LONG only (no Donchian)")
stats(s2_ob(h4, mode='long', donchian_filter=True), "LONG only + Donchian filter")

# SHORT only
stats(s2_ob(h4, mode='short', donchian_filter=False), "SHORT only (no Donchian)")
stats(s2_ob(h4, mode='short', donchian_filter=True), "SHORT only + Donchian SHORT regime")

# BOTH
stats(s2_ob(h4, mode='both', donchian_filter=False), "BOTH (no Donchian)")
stats(s2_ob(h4, mode='both', donchian_filter=True), "BOTH + Donchian regime aware")

# Per any of SHORTS only with Donchian
print()
print("Per any SHORT only + Donchian regime:")
trades_s = s2_ob(h4, mode='short', donchian_filter=True)
if trades_s:
    tdf = pd.DataFrame(trades_s)
    tdf['year'] = pd.to_datetime(tdf['ts']).dt.year
    for yr in sorted(tdf['year'].unique()):
        ydf = tdf[tdf['year']==yr]
        arr = ydf['pnl'].values
        n=len(arr); w=(arr>0).sum(); net=arr.sum()
        pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
        pf=pf_p/pf_l if pf_l else 0
        print(f"  {yr}: n={n:>3} | WR {w/n*100 if n else 0:>5.1f}% | Net ${net:+.0f} | PF {pf:.2f}")

# Donchian SHORT regime stats: how many days were in SHORT regime?
short_days = d1_in_short.sum()
print(f"\nDonchian SHORT regime days: {short_days} of {len(d1)} ({short_days/len(d1)*100:.1f}%)")
long_days = d1_in_long.sum()
print(f"Donchian LONG regime days: {long_days} of {len(d1)} ({long_days/len(d1)*100:.1f}%)")
flat_days = len(d1) - short_days - long_days
print(f"Flat days: {flat_days} ({flat_days/len(d1)*100:.1f}%)")
