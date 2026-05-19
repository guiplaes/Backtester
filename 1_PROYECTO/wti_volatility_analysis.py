"""Analisi volatilitat WTI_USDT_PERP a Pionex (67 dies daily klines)."""
import json
import statistics
from datetime import datetime, timezone

# Klines descarregats abans (67 dies cap enrere des de avui)
# Time, open, close, high, low, volume
DATA = [
    (1778630400000, 98.92, 99.04, 99.22, 97.41, 17762.697),
    (1778544000000, 96.05, 98.92, 100.06, 96.05, 50946.876),
    (1778457600000, 97.27, 96.05, 98.56, 94.62, 67450.642),
    (1778371200000, 93.56, 97.27, 97.29, 92.14, 50582.418),
    (1778284800000, 93.12, 93.56, 93.88, 92.04, 11763.653),
    (1778198400000, 96.10, 93.12, 96.38, 92.99, 67799.474),
    (1778112000000, 95.86, 96.10, 98.48, 89.65, 144701.862),
    (1778025600000, 99.74, 95.86, 101.32, 88.73, 201528.137),
    (1777939200000, 104.86, 99.74, 105.29, 99.13, 98821.467),
    (1777852800000, 101.77, 104.86, 107.50, 100.12, 152481.658),
    (1777766400000, 102.00, 101.77, 102.45, 98.51, 60987.979),
    (1777680000000, 102.72, 102.00, 102.79, 101.27, 10605.499),
    (1777593600000, 105.61, 102.72, 106.44, 99.16, 118354.885),
    (1777507200000, 107.68, 105.61, 110.76, 103.31, 157979.598),
    (1777420800000, 99.31, 107.68, 108.67, 98.37, 113494.908),
    (1777334400000, 96.62, 99.31, 101.70, 96.24, 112676.494),
    (1777248000000, 96.09, 96.62, 97.61, 94.62, 58205.686),
    (1777161600000, 95.82, 96.09, 96.58, 94.76, 13931.061),
    (1777075200000, 94.81, 95.82, 96.67, 94.73, 17973.453),
    (1776988800000, 96.71, 94.81, 97.72, 92.73, 125422.518),
    (1776902400000, 92.69, 96.71, 98.20, 92.36, 167029.307),
    (1776816000000, 90.57, 92.69, 93.68, 87.73, 92971.066),
    (1776729600000, 86.28, 90.57, 92.27, 85.77, 141921.459),
    (1776643200000, 88.67, 86.28, 89.05, 85.54, 98390.331),
    (1776556800000, 85.90, 88.67, 90.40, 85.00, 93524.234),
    (1776470400000, 83.85, 85.90, 88.24, 83.63, 94454.207),
    (1776384000000, 90.12, 83.85, 90.37, 79.17, 125340.824),
    (1776297600000, 87.79, 90.12, 91.85, 87.44, 53786.118),
    (1776211200000, 87.46, 87.79, 90.53, 87.33, 64887.284),
    (1776124800000, 91.53, 87.46, 93.04, 84.38, 117235.448),
    (1776038400000, 99.13, 91.53, 99.67, 90.88, 170666.045),
    (1775952000000, 90.33, 99.13, 99.95, 89.59, 180953.214),
    (1775865600000, 92.15, 90.33, 92.40, 88.41, 51012.138),
    (1775779200000, 95.42, 92.15, 96.21, 91.26, 107014.110),
    (1775692800000, 95.59, 95.42, 99.28, 93.32, 242548.075),
    (1775606400000, 96.43, 95.59, 97.99, 90.80, 311597.367),
    (1775520000000, 113.19, 96.43, 116.33, 91.16, 464563.607),
    (1775433600000, 113.49, 113.19, 113.65, 108.30, 174609.777),
    (1775347200000, 112.00, 113.49, 114.87, 111.78, 62616.276),
    (1775260800000, 111.56, 112.00, 112.19, 110.94, 11574.368),
    (1775174400000, 111.13, 111.56, 112.21, 110.69, 27989.279),
    (1775088000000, 98.65, 111.13, 113.19, 97.38, 358628.077),
    (1775001600000, 101.35, 98.65, 103.07, 96.45, 230322.837),
    (1774915200000, 105.93, 101.35, 106.18, 99.55, 204393.596),
    (1774828800000, 102.69, 105.93, 106.59, 99.74, 118818.601),
    (1774742400000, 100.67, 102.69, 104.01, 99.87, 78714.654),
    (1774656000000, 100.75, 100.67, 100.77, 98.19, 58651.004),
    (1774569600000, 93.20, 100.75, 101.38, 92.13, 95099.156),
    (1774483200000, 90.94, 93.20, 95.35, 89.84, 93428.105),
    (1774396800000, 88.82, 90.94, 91.40, 86.46, 88840.896),
    (1774310400000, 89.44, 88.82, 93.30, 86.38, 128739.520),
    (1774224000000, 98.62, 89.44, 101.51, 82.15, 232616.736),
    (1774137600000, 97.97, 98.62, 101.42, 96.46, 65254.340),
    (1774051200000, 96.08, 97.97, 97.99, 94.66, 32128.908),
    (1773964800000, 93.94, 96.08, 98.68, 92.61, 82154.461),
    (1773878400000, 98.25, 93.94, 100.29, 92.14, 136157.187),
    (1773792000000, 95.22, 98.25, 99.57, 91.54, 85541.754),
    (1773705600000, 94.04, 95.22, 97.57, 93.27, 49195.813),
    (1773619200000, 97.07, 94.04, 99.44, 91.99, 98219.384),
    (1773532800000, 100.16, 97.07, 102.08, 96.71, 120971.636),
    (1773446400000, 95.71, 100.16, 103.33, 95.71, 132998.738),
    (1773360000000, 95.46, 95.71, 98.39, 91.31, 107301.881),
    (1773273600000, 91.90, 95.46, 97.35, 89.16, 112363.653),
    (1773187200000, 85.15, 91.90, 93.90, 81.49, 92773.379),
    (1773100800000, 88.56, 85.15, 91.05, 76.91, 59957.043),
    (1773014400000, 99.85, 88.56, 104.19, 81.01, 21357.434),
]

DATA.reverse()  # cronologic ascendent

def daily_range_pct(d):
    h, l = d[3], d[4]
    avg = (h + l) / 2
    return ((h - l) / avg) * 100

def daily_atr(d):  # True range simple (H-L)
    return d[3] - d[4]

def window_stats(data, label):
    if not data: return
    ranges = [daily_range_pct(d) for d in data]
    atrs = [daily_atr(d) for d in data]
    closes = [d[2] for d in data]
    highs = [d[3] for d in data]
    lows = [d[4] for d in data]
    volumes = [d[5] for d in data]
    first_open = data[0][1]
    last_close = data[-1][2]
    period_change_pct = (last_close - first_open) / first_open * 100

    print(f"\n=== {label} ({len(data)} dies) ===")
    print(f"  Rang preu:         min ${min(lows):.2f}   max ${max(highs):.2f}   spread ${max(highs)-min(lows):.2f}")
    print(f"  Variacio periode:  {first_open:.2f} -> {last_close:.2f}  ({period_change_pct:+.1f}%)")
    print(f"  Range diari %:     mitja {statistics.mean(ranges):.2f}%   mediana {statistics.median(ranges):.2f}%   max {max(ranges):.2f}%   min {min(ranges):.2f}%")
    print(f"  ATR diari $:       mitja ${statistics.mean(atrs):.2f}     mediana ${statistics.median(atrs):.2f}     max ${max(atrs):.2f}")
    print(f"  Volum diari avg:   {statistics.mean(volumes):,.0f} contractes")
    if len(ranges) > 5:
        stdev = statistics.stdev(ranges)
        print(f"  Stdev range %:     {stdev:.2f}%  (cv={stdev/statistics.mean(ranges):.2f})")

# Analisi per finestres
print("=" * 75)
print("WTI_USDT_PERP - ANALISI VOLATILITAT (dades Pionex)")
print(f"Periode: {datetime.fromtimestamp(DATA[0][0]/1000, timezone.utc).strftime('%Y-%m-%d')}"
      f" -> {datetime.fromtimestamp(DATA[-1][0]/1000, timezone.utc).strftime('%Y-%m-%d')}")
print("=" * 75)

window_stats(DATA, "TOTAL HISTORIC (~67 dies)")
window_stats(DATA[-7:], "ULTIMS 7 DIES (ara)")
window_stats(DATA[-30:], "ULTIMS 30 DIES")
window_stats(DATA[:30], "PRIMERS 30 DIES (fa ~2 mesos)")

# Spike days - top 5 dies amb mes range %
print("\n=== TOP 5 DIES MES VOLATILS (spikes) ===")
sorted_by_range = sorted(DATA, key=lambda d: -daily_range_pct(d))
for d in sorted_by_range[:5]:
    dt = datetime.fromtimestamp(d[0]/1000, timezone.utc).strftime('%Y-%m-%d')
    print(f"  {dt}: O={d[1]:.2f}  H={d[3]:.2f}  L={d[4]:.2f}  C={d[2]:.2f}   range={daily_range_pct(d):.2f}%   vol={d[5]:,.0f}")

# Per a un grid: quina width 2x daily range, step minim
print("\n=== RECOMANACIO GRID (basat en mitjana ultims 30 dies) ===")
last30_avg_range = statistics.mean([daily_range_pct(d) for d in DATA[-30:]])
last30_atr = statistics.mean([daily_atr(d) for d in DATA[-30:]])
current_price = DATA[-1][2]
print(f"  Preu actual:                ${current_price:.2f}")
print(f"  Range diari mig (30d):      {last30_avg_range:.2f}%  = ${last30_atr:.2f}")
print(f"  Grid width recomanada (2x): {last30_avg_range*2:.2f}% = ${last30_atr*2:.2f}")
print(f"  Grid range ($):             ${current_price - last30_atr:.2f}  ->  ${current_price + last30_atr:.2f}")
