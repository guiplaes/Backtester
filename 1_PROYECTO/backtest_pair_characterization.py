"""
CARACTERITZACIO DE PARELLS — Volatilitat + Trendiness + Best Params per TF
============================================================================
Per cada parell × TF, calcula:
1. Volatilitat (ATR / std relatiu)
2. "Mean-revertness" (Hurst exponent — < 0.5 = mean-revert, > 0.5 = trend)
3. Best params òptims (SMA + levels + stop)
4. Adaptació dels params segons les característiques

Mostra una taula clara per saber QUÈ ajustar a cada parell+TF.
"""
import pandas as pd
import numpy as np

PAIR_FILES = {
    'EURGBP': 'eurgbp_dk_m5_5y.csv',
    'EURCHF': 'eurchf_dk_m5_5y.csv',
    'EURUSD': 'eurusd_m5_5y.csv',
    'EURJPY': 'eurjpy_dk_m5_5y.csv',
    'CHFJPY': 'chfjpy_dk_m5_5y.csv',
    'USDCHF': 'usdchf_dk_m5_5y.csv',
    'AUDNZD': 'audnzd_dk_m5_5y.csv',
    'XAUUSD': 'xauusd_m5_5y.csv',
}

PIP_MUL = {'EURGBP':1000,'EURJPY':100,'EURCHF':1000,'CHFJPY':100,
           'XAUUSD':1,'AUDNZD':1000,'USDCHF':1000,'EURUSD':1000}
COSTS = {'EURGBP':0.30,'EURJPY':0.30,'EURCHF':0.30,'CHFJPY':0.40,
         'XAUUSD':0.50,'AUDNZD':0.40,'USDCHF':0.30,'EURUSD':0.30}

def aggregate(df_, rule):
    return df_.resample(rule).agg(open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last')).dropna()

def hurst_exponent(prices, max_lag=100):
    """Hurst < 0.5 = mean-reverting, = 0.5 = random walk, > 0.5 = trending"""
    prices = np.asarray(prices)
    if len(prices) < 200: return 0.5
    lags = range(2, min(max_lag, len(prices)//4))
    tau = []
    for lag in lags:
        diff = np.subtract(prices[lag:], prices[:-lag])
        if len(diff) < 2: continue
        tau.append(np.sqrt(np.std(diff)))
    if len(tau) < 5: return 0.5
    poly = np.polyfit(np.log(list(lags[:len(tau)])), np.log(tau), 1)
    return poly[0] * 2.0

def compute_volatility_profile(df_):
    """Returns annualized volatility, ATR/price ratio, daily range"""
    closes = df_['close'].values
    log_returns = np.diff(np.log(closes))
    daily_vol = np.std(log_returns)
    bars_per_year = 252 * 24 * 12 / (len(df_) / (df_.index[-1] - df_.index[0]).total_seconds() * 86400 / 365.25)
    annualized_vol = daily_vol * np.sqrt(bars_per_year * 365)  # simplified

    # ATR-style
    tr = np.maximum(df_['high'] - df_['low'],
                    np.maximum(np.abs(df_['high'] - df_['close'].shift(1)),
                               np.abs(df_['low'] - df_['close'].shift(1)))).dropna()
    atr_pct = (tr.mean() / df_['close'].mean()) * 100
    return {'daily_vol_pct': daily_vol*100, 'atr_pct': atr_pct,
            'mean_price': df_['close'].mean(),'std_price': df_['close'].std()}

print("Calculant caracteristiques de cada parell × TF...", flush=True)
print()

# Build characterization table
results = []
for pair, csv_file in PAIR_FILES.items():
    print(f"\n--- {pair} ---")
    df_m5 = pd.read_csv(csv_file, index_col=0, parse_dates=True)
    df_m5.index = pd.to_datetime(df_m5.index, utc=True)
    df_m5.columns = [c.lower() for c in df_m5.columns]

    # Yearly returns
    yrs = sorted(df_m5.index.year.unique())
    yearly_pcts = []
    for yr in yrs:
        sub = df_m5[df_m5.index.year==yr]
        if len(sub) < 100: continue
        pct = (sub['close'].iloc[-1] - sub['close'].iloc[0]) / sub['close'].iloc[0] * 100
        yearly_pcts.append(pct)
    avg_annual_drift = np.mean(np.abs(yearly_pcts)) if yearly_pcts else 0
    cumulative_drift = abs(sum(yearly_pcts))

    print(f"  Yearly drift avg abs: {avg_annual_drift:.1f}%")
    print(f"  Cumulative 5y drift:  {cumulative_drift:.1f}%")

    for tf_name, rule in [('M15','15min'),('M30','30min'),('H1','1h'),('H4','4h'),('D1','1D')]:
        df_tf = aggregate(df_m5, rule)
        if len(df_tf) < 50: continue
        vol = compute_volatility_profile(df_tf)
        # Hurst on close prices
        hurst = hurst_exponent(df_tf['close'].values, max_lag=min(100, len(df_tf)//4))

        # Categorize
        is_meanrev = hurst < 0.45
        is_trending = hurst > 0.55
        category = "MEAN-REV" if is_meanrev else ("TRENDING" if is_trending else "MIXED")

        results.append({
            'pair':pair, 'tf':tf_name, 'hurst':hurst, 'category':category,
            'atr_pct':vol['atr_pct'], 'mean_price':vol['mean_price'],
            'std_price':vol['std_price'], 'avg_drift':avg_annual_drift,
            'cumulative_drift':cumulative_drift,
        })

# Display table
print()
print("="*150)
print("CARACTERITZACIO COMPLETA:")
print("="*150)
print(f"  {'Pair':<8} {'TF':<4} {'Hurst':>7} {'Categoria':>10} {'ATR%':>7} {'std_price':>10} {'avg drift/any':>14}")

for pair in PAIR_FILES.keys():
    rows = [r for r in results if r['pair']==pair]
    for r in rows:
        marker = "**" if r['category']=='MEAN-REV' else ("--" if r['category']=='TRENDING' else "..")
        print(f"  {r['pair']:<8} {r['tf']:<4} {r['hurst']:>7.3f} {r['category']:>10}{marker} {r['atr_pct']:>6.3f}% {r['std_price']:>10.4f} {r['avg_drift']:>12.1f}%")

# CONCLUSIO per pair
print()
print("="*150)
print("RANKING PARELLS PER MEAN-REV (Hurst mig de tots TFs):")
print("="*150)
pair_avg_hurst = {}
for pair in PAIR_FILES.keys():
    rows = [r for r in results if r['pair']==pair]
    if rows:
        avg_h = np.mean([r['hurst'] for r in rows])
        pair_avg_hurst[pair] = avg_h

sorted_pairs = sorted(pair_avg_hurst.items(), key=lambda x: x[1])
print(f"  {'Pair':<8} {'Hurst mig':>10} {'Veredicte':>30}")
for p, h in sorted_pairs:
    if h < 0.40: v = "FORT MEAN-REV ★★★"
    elif h < 0.45: v = "MEAN-REV ★★"
    elif h < 0.50: v = "lleugerament MR ★"
    elif h < 0.55: v = "RANDOM WALK"
    elif h < 0.60: v = "lleugerament TREND"
    else: v = "TRENDING (avoid for MR)"
    print(f"  {p:<8} {h:>10.3f} {v:>30}")

# RULES OF THUMB
print()
print("="*150)
print("REGLES D'AJUST PER A LA NOSTRA ESTRATEGIA:")
print("="*150)
print()
print("  Per Hurst < 0.45 (forta mean-rev) → SMA curt + levels propers + stop ample")
print("    Exemple: SMA 100, levels -0.5/-1.0/-1.5/-2.0, stop -4σ")
print()
print("  Per Hurst 0.45-0.50 (mean-rev moderat) → SMA mig + levels ampls + stop")
print("    Exemple: SMA 200-500, levels -1.0/-1.5/-2.0/-2.5, stop -4σ")
print()
print("  Per Hurst > 0.55 (trending) → SMA llarg + levels MOLT extremes + stop ample")
print("    Exemple: SMA 1000+, levels -2.0/-3.0/-4.0/-5.0, stop -7σ")
print("    ⚠ Pero millor evitar: tendencia destrueix mean-rev")
print()
print("  Per ATR % > 0.5% (volatil) → levels MES allunyats + stop ample")
print("    Exemple: levels × 1.5x del baseline")
print()
print("  Per ATR % < 0.1% (calm) → levels MES propers + stop tight")
print("    Exemple: levels × 0.7x del baseline")

# RECOMMENDED PARAMS per Pair + TF
print()
print("="*150)
print("PARAMS RECOMANATS (basats en caracteristiques):")
print("="*150)
print(f"  {'Pair':<8} {'TF':<4} {'Categoria':>10} {'SMA reco':>10} {'Levels reco':>22} {'Stop':>6}")
for r in results:
    pair = r['pair']
    tf = r['tf']
    h = r['hurst']
    atr = r['atr_pct']

    # Base SMA per TF
    if tf == 'D1': base_sma = 50
    elif tf == 'H4': base_sma = 200
    elif tf == 'H1': base_sma = 500
    elif tf == 'M30': base_sma = 800
    else: base_sma = 2400

    # Adjust SMA by trendiness (more trend → larger SMA)
    if h > 0.55: sma = int(base_sma * 1.5)
    elif h < 0.45: sma = int(base_sma * 0.8)
    else: sma = base_sma

    # Adjust levels by volatility
    if atr > 0.5:  # high vol
        levels_str = "[-1.0,-2.0,-3.0,-4.0]"
        stop = "-6.0"
    elif atr > 0.3:  # mid vol
        levels_str = "[-0.5,-1.5,-2.5,-3.0]"
        stop = "-5.0"
    elif atr > 0.1:  # low-mid vol
        levels_str = "[-0.5,-1.0,-1.5,-2.0]"
        stop = "-4.0"
    else:  # very low vol
        levels_str = "[-1.0,-1.5,-2.0,-2.5]"
        stop = "-4.0"

    print(f"  {pair:<8} {tf:<4} {r['category']:>10} {sma:>10} {levels_str:>22} {stop:>6}")

print("\nDONE")
