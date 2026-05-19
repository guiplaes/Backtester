"""
Anàlisi de tots els SL trades vs WIN trades per detectar patrons que
el LLM podria reconèixer abans d'entrar.
"""
import pandas as pd
import numpy as np

CSV = "xauusd_m5_5y.csv"
REAL_COST = 0.40

m5 = pd.read_csv(CSV, index_col=0, parse_dates=True)
m5.index = pd.to_datetime(m5.index, utc=True)
m5.columns = [c.lower() for c in m5.columns]
if 'tick_volume' not in m5.columns: m5['tick_volume'] = m5.get('volume', 1)

m5['ema20'] = m5['close'].ewm(span=20, adjust=False).mean()
m5['ema50'] = m5['close'].ewm(span=50, adjust=False).mean()
m5['ema200'] = m5['close'].ewm(span=200, adjust=False).mean()
hl = m5['high']-m5['low']; hc = (m5['high']-m5['close'].shift()).abs(); lc = (m5['low']-m5['close'].shift()).abs()
tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
m5['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
m5['atr_pct'] = m5['atr'].rolling(500).rank(pct=True)
m5['vol_avg'] = m5['tick_volume'].rolling(20).mean()
m5['vol_ratio'] = m5['tick_volume'] / m5['vol_avg']
# Slope EMA50 (over last 30 bars)
m5['ema50_slope'] = (m5['ema50'] - m5['ema50'].shift(30)) / m5['ema50'].shift(30) * 100
# Distance to EMA200
m5['dist_ema200'] = (m5['close'] - m5['ema200']) / m5['ema200'] * 100
# RSI(14)
delta = m5['close'].diff()
gain = (delta.where(delta > 0, 0)).rolling(14).mean()
loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
rs = gain / loss
m5['rsi'] = 100 - (100 / (1 + rs))

print("Indicators ready", flush=True)

O = m5['open'].values; H = m5['high'].values; L = m5['low'].values; C = m5['close'].values
EMA20 = m5['ema20'].values
EMA50 = m5['ema50'].values
EMA200 = m5['ema200'].values
ATR = m5['atr'].values
ATR_PCT = m5['atr_pct'].values
VOL_RATIO = m5['vol_ratio'].values
SLOPE = m5['ema50_slope'].values
DIST_E200 = m5['dist_ema200'].values
RSI = m5['rsi'].values
HOURS = m5.index.hour.values
WKD = m5.index.weekday.values
n = len(C)

def get_session(h):
    if h < 7: return 'ASIA'
    elif h < 13: return 'LONDON'
    elif h < 21: return 'NY' if h >= 16 else 'OVERLAP'
    else: return 'DEAD'

SESSION_ARR = np.array([get_session(h) for h in HOURS])
ALLOWED_MASK = np.array([s in {'ASIA', 'LONDON', 'NY'} for s in SESSION_ARR])

# Use UNIFIED config: SL 1.0, TP 2/4, OB 2.5
SL_M, TP1_M, TP2_M, OB_S = 1.0, 2, 4, 2.5

def bt_with_context(direction='long'):
    """Return list of trades with FULL context per trade."""
    trades = []
    pos = None
    pending_lows = []; pending_highs = []; pending_atrs = []; pending_expiry = []
    for i in range(50, n-3):
        if pos is not None:
            if direction == 'long':
                sl_h = L[i] <= pos['sl']
                tp1_h = H[i] >= pos['tp1']
                tp2_h = H[i] >= pos['tp2']
            else:
                sl_h = H[i] >= pos['sl']
                tp1_h = L[i] <= pos['tp1']
                tp2_h = L[i] <= pos['tp2']
            if sl_h and (tp1_h or tp2_h):
                if direction == 'long':
                    if (O[i]-pos['sl']) < (pos['tp1']-O[i]): tp1_h=False; tp2_h=False
                else:
                    if (pos['sl']-O[i]) < (O[i]-pos['tp1']): tp1_h=False; tp2_h=False
            sgn = 1 if direction == 'long' else -1
            if tp1_h and pos['q1']>0:
                pos['pnl1'] = (pos['tp1']-pos['e'])*0.5*sgn; pos['q1']=0
                pos['exit_type'] = 'TP1'
                pos['exit_idx'] = i
            if tp2_h and pos['q2']>0:
                pos['pnl2'] = (pos['tp2']-pos['e'])*0.5*sgn; pos['q2']=0
                if pos.get('exit_type') is None or pos['exit_type'] == 'TP1':
                    pos['exit_type'] = 'TP2'
                pos['exit_idx'] = i
            if sl_h:
                if pos['q1']>0: pos['pnl1'] = (pos['sl']-pos['e'])*0.5*sgn; pos['q1']=0
                if pos['q2']>0: pos['pnl2'] = (pos['sl']-pos['e'])*0.5*sgn; pos['q2']=0
                if pos.get('exit_type') is None:
                    pos['exit_type'] = 'SL'
                pos['exit_idx'] = i
            if pos['q1']==0 and pos['q2']==0:
                tp = pos.get('pnl1',0)+pos.get('pnl2',0) - REAL_COST
                # Outcome label
                if tp <= 0:
                    outcome = 'LOSS'
                elif pos.get('exit_type') == 'TP2':
                    outcome = 'BIG_WIN'  # both TP1+TP2
                else:
                    outcome = 'SMALL_WIN'  # only TP1
                trade = dict(pos)
                trade['pnl'] = tp
                trade['outcome'] = outcome
                trades.append(trade)
                pos = None

        b0_close = C[i-1]; b0_open = O[i-1]; b0_atr = ATR[i-1]
        if direction == 'long':
            if b0_close < b0_open and not np.isnan(b0_atr):
                future_high = max(H[i], H[i+1], H[i+2])
                move = future_high - b0_close
                if move > OB_S * b0_atr:
                    pending_lows.append(L[i-1]); pending_highs.append(H[i-1])
                    pending_atrs.append(b0_atr); pending_expiry.append(i+30)
        else:
            if b0_close > b0_open and not np.isnan(b0_atr):
                future_low = min(L[i], L[i+1], L[i+2])
                move_dn = b0_close - future_low
                if move_dn > OB_S * b0_atr:
                    pending_lows.append(L[i-1]); pending_highs.append(H[i-1])
                    pending_atrs.append(b0_atr); pending_expiry.append(i+30)

        keep = [j for j, exp in enumerate(pending_expiry) if exp > i]
        if len(keep) < len(pending_expiry):
            pending_lows = [pending_lows[j] for j in keep]; pending_highs = [pending_highs[j] for j in keep]
            pending_atrs = [pending_atrs[j] for j in keep]; pending_expiry = [pending_expiry[j] for j in keep]

        if pos is None and not np.isnan(EMA50[i]) and ALLOWED_MASK[i]:
            cond_trend = (C[i] > EMA50[i]) if direction == 'long' else (C[i] < EMA50[i])
            if not cond_trend: continue
            for j in range(len(pending_lows)):
                if direction == 'long':
                    cond_in = L[i] <= pending_highs[j] and C[i] > pending_lows[j]
                    cond_rev = C[i] > O[i]
                else:
                    cond_in = H[i] >= pending_lows[j] and C[i] < pending_highs[j]
                    cond_rev = C[i] < O[i]
                if cond_in and cond_rev:
                    atr = pending_atrs[j]; e = C[i]
                    if direction == 'long':
                        sl = pending_lows[j] - atr * SL_M * 0.5
                        tp1 = e + atr * TP1_M
                        tp2 = e + atr * TP2_M
                    else:
                        sl = pending_highs[j] + atr * SL_M * 0.5
                        tp1 = e - atr * TP1_M
                        tp2 = e - atr * TP2_M
                    pos = {
                        'side': direction, 'e': e, 'sl': sl, 'tp1': tp1, 'tp2': tp2,
                        'q1': 0.5, 'q2': 0.5, 'entry_idx': i,
                        'atr_at_entry': atr,
                        # Context features at entry
                        'session': SESSION_ARR[i],
                        'hour': HOURS[i],
                        'weekday': WKD[i],
                        'atr_pct': ATR_PCT[i] if not np.isnan(ATR_PCT[i]) else 0.5,
                        'vol_ratio': VOL_RATIO[i] if not np.isnan(VOL_RATIO[i]) else 1.0,
                        'ema50_slope': SLOPE[i] if not np.isnan(SLOPE[i]) else 0,
                        'dist_ema200': DIST_E200[i] if not np.isnan(DIST_E200[i]) else 0,
                        'rsi': RSI[i] if not np.isnan(RSI[i]) else 50,
                        'price_vs_ema20': (C[i] - EMA20[i]) / atr if atr > 0 else 0,
                        'price_vs_ema50': (C[i] - EMA50[i]) / atr if atr > 0 else 0,
                        'ob_size_atr': (pending_highs[j] - pending_lows[j]) / atr if atr > 0 else 0,
                        'ob_age': i - 0,  # bars since OB created (proxy)
                    }
                    pending_lows.pop(j); pending_highs.pop(j); pending_atrs.pop(j); pending_expiry.pop(j)
                    break
    return trades

print("Running LONGS backtest with context...", flush=True)
longs = bt_with_context('long')
print(f"  {len(longs)} long trades", flush=True)

print("Running SHORTS backtest with context...", flush=True)
shorts = bt_with_context('short')
print(f"  {len(shorts)} short trades", flush=True)

# Combine
all_trades = longs + shorts
print(f"\nTotal trades: {len(all_trades)}")

df = pd.DataFrame(all_trades)
print(f"  LOSSES: {(df['outcome']=='LOSS').sum()}")
print(f"  SMALL_WIN: {(df['outcome']=='SMALL_WIN').sum()}")
print(f"  BIG_WIN: {(df['outcome']=='BIG_WIN').sum()}")

# Compare features between LOSS vs WIN
print()
print("="*120)
print("COMPARATIVA FEATURES — LOSS vs WIN trades:")
print("="*120)
print(f"{'Feature':<25} | {'LOSS mean':>12} | {'WIN mean':>12} | {'Difference':>12} | {'Discriminator?':>15}")
print("-"*100)

features = ['atr_pct', 'vol_ratio', 'ema50_slope', 'dist_ema200', 'rsi',
            'price_vs_ema20', 'price_vs_ema50', 'ob_size_atr', 'hour', 'weekday']

losses = df[df['outcome']=='LOSS']
wins = df[df['outcome']!='LOSS']

for f in features:
    l_mean = losses[f].mean()
    w_mean = wins[f].mean()
    diff = w_mean - l_mean
    diff_pct = (diff / abs(l_mean) * 100) if l_mean != 0 else 0
    significant = "YES *" if abs(diff_pct) > 10 else "no"
    print(f"{f:<25} | {l_mean:>+12.3f} | {w_mean:>+12.3f} | {diff:>+12.3f} | {significant:>15}")

# Feature distributions per outcome
print()
print("="*120)
print("DISTRIBUCIÓ per RANG:")
print("="*120)

# ATR percentile
print("\nATR percentile (cuant volat regimen):")
for label, cutoff in [('atr_pct<0.2', df['atr_pct']<0.2),
                      ('atr_pct 0.2-0.5', (df['atr_pct']>=0.2) & (df['atr_pct']<0.5)),
                      ('atr_pct 0.5-0.8', (df['atr_pct']>=0.5) & (df['atr_pct']<0.8)),
                      ('atr_pct>0.8', df['atr_pct']>=0.8)]:
    sub = df[cutoff]
    n_l = (sub['outcome']=='LOSS').sum()
    n_w = (sub['outcome']!='LOSS').sum()
    n_t = len(sub)
    if n_t > 0:
        loss_rate = n_l/n_t*100
        net = sub['pnl'].sum()
        print(f"  {label:<20}: n={n_t:>4} | Loss rate {loss_rate:>4.1f}% | Net=${net:+.0f}", flush=True)

# Volume ratio
print("\nVolume ratio (al moment d'entrada):")
for label, cutoff in [('vol<0.7 (baix)', df['vol_ratio']<0.7),
                      ('vol 0.7-1.3', (df['vol_ratio']>=0.7) & (df['vol_ratio']<1.3)),
                      ('vol 1.3-2.0', (df['vol_ratio']>=1.3) & (df['vol_ratio']<2.0)),
                      ('vol>2.0 (spike)', df['vol_ratio']>=2.0)]:
    sub = df[cutoff]
    n_t = len(sub)
    if n_t > 0:
        n_l = (sub['outcome']=='LOSS').sum()
        loss_rate = n_l/n_t*100
        net = sub['pnl'].sum()
        print(f"  {label:<20}: n={n_t:>4} | Loss rate {loss_rate:>4.1f}% | Net=${net:+.0f}", flush=True)

# RSI
print("\nRSI (al moment d'entrada):")
for label, cutoff in [('RSI<30 (oversold)', df['rsi']<30),
                      ('RSI 30-50', (df['rsi']>=30) & (df['rsi']<50)),
                      ('RSI 50-70', (df['rsi']>=50) & (df['rsi']<70)),
                      ('RSI>70 (overbought)', df['rsi']>=70)]:
    sub = df[cutoff]
    n_t = len(sub)
    if n_t > 0:
        n_l = (sub['outcome']=='LOSS').sum()
        loss_rate = n_l/n_t*100
        net = sub['pnl'].sum()
        print(f"  {label:<20}: n={n_t:>4} | Loss rate {loss_rate:>4.1f}% | Net=${net:+.0f}", flush=True)

# EMA50 slope
print("\nEMA50 slope (tendència 30-bar):")
for label, cutoff in [('slope<-0.1% (down)', df['ema50_slope']<-0.1),
                      ('slope -0.1 to 0', (df['ema50_slope']>=-0.1) & (df['ema50_slope']<0)),
                      ('slope 0 to 0.1', (df['ema50_slope']>=0) & (df['ema50_slope']<0.1)),
                      ('slope>0.1% (up)', df['ema50_slope']>=0.1)]:
    sub = df[cutoff]
    n_t = len(sub)
    if n_t > 0:
        n_l = (sub['outcome']=='LOSS').sum()
        loss_rate = n_l/n_t*100
        net = sub['pnl'].sum()
        print(f"  {label:<22}: n={n_t:>4} | Loss rate {loss_rate:>4.1f}% | Net=${net:+.0f}", flush=True)

# Distance to EMA200
print("\nDistance to EMA200 (% sobre/sota):")
for label, cutoff in [('<-2% (lluny per sota)', df['dist_ema200']<-2),
                      ('-2% a 0', (df['dist_ema200']>=-2) & (df['dist_ema200']<0)),
                      ('0 a 2%', (df['dist_ema200']>=0) & (df['dist_ema200']<2)),
                      ('>2% (lluny per sobre)', df['dist_ema200']>=2)]:
    sub = df[cutoff]
    n_t = len(sub)
    if n_t > 0:
        n_l = (sub['outcome']=='LOSS').sum()
        loss_rate = n_l/n_t*100
        net = sub['pnl'].sum()
        print(f"  {label:<22}: n={n_t:>4} | Loss rate {loss_rate:>4.1f}% | Net=${net:+.0f}", flush=True)

# Hour of day
print("\nHora del dia (UTC):")
for h in range(24):
    sub = df[df['hour']==h]
    n_t = len(sub)
    if n_t > 30:
        n_l = (sub['outcome']=='LOSS').sum()
        loss_rate = n_l/n_t*100
        net = sub['pnl'].sum()
        print(f"  hour {h:>2}: n={n_t:>4} | Loss rate {loss_rate:>4.1f}% | Net=${net:+.0f}", flush=True)

# Weekday
print("\nDia de la setmana:")
days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
for d in range(7):
    sub = df[df['weekday']==d]
    n_t = len(sub)
    if n_t > 0:
        n_l = (sub['outcome']=='LOSS').sum()
        loss_rate = n_l/n_t*100
        net = sub['pnl'].sum()
        print(f"  {days[d]}: n={n_t:>4} | Loss rate {loss_rate:>4.1f}% | Net=${net:+.0f}", flush=True)

# Save dataframe for further analysis
df.to_csv('m5_trades_with_context.csv', index=False)
print(f"\nSaved m5_trades_with_context.csv ({len(df)} trades amb context complet)")

print()
print("="*120)
print("PROPOSTA DE FILTRES BASADA EN PATRONS:")
print("="*120)

# Find patterns where loss rate is significantly higher
print("\nCONDICIONS QUE PORTEN A MÉS LOSSES (filter candidates):")
for f in features:
    if f in ['hour', 'weekday']: continue
    quartiles = df[f].quantile([0.25, 0.5, 0.75]).values
    for label, cutoff_val, cutoff_dir in [
        (f"{f} < {quartiles[0]:.2f} (Q1)", quartiles[0], 'below'),
        (f"{f} > {quartiles[2]:.2f} (Q4)", quartiles[2], 'above'),
    ]:
        sub = df[df[f] < cutoff_val] if cutoff_dir == 'below' else df[df[f] > cutoff_val]
        n_t = len(sub)
        if n_t > 50:
            loss_rate = (sub['outcome']=='LOSS').sum() / n_t * 100
            base_loss_rate = (df['outcome']=='LOSS').sum() / len(df) * 100
            if loss_rate > base_loss_rate + 5:
                net = sub['pnl'].sum()
                print(f"  !!{label}: loss rate {loss_rate:.1f}% (base {base_loss_rate:.1f}%, +{loss_rate-base_loss_rate:.1f}pp) | Net=${net:+.0f}")
