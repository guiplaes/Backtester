"""Anàlisi de volatilitat per dimensionar grids correctament.
Connecta directament a Pionex i agafa 365d.
"""
import urllib.request
import json
import statistics

SYMBOLS = ['BTC_USDT', 'ETH_USDT', 'SOL_USDT', 'PAXG_USDT']

def get_klines(symbol, interval='1D', limit=365):
    url = f'https://api.pionex.com/api/v1/market/klines?symbol={symbol}&interval={interval}&limit={limit}'
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read())['data']['klines']

def analyze(symbol):
    k = get_klines(symbol)
    daily_ranges = []
    weekly_ranges = []  # 7-day rolling max-min
    closes = [float(c['close']) for c in k]
    highs = [float(c['high']) for c in k]
    lows = [float(c['low']) for c in k]

    for i, c in enumerate(k):
        h, l, cl = float(c['high']), float(c['low']), float(c['close'])
        daily_ranges.append((h - l) / cl * 100)

    for i in range(7, len(k)):
        wh = max(highs[i-7:i])
        wl = min(lows[i-7:i])
        wc = closes[i]
        weekly_ranges.append((wh - wl) / wc * 100)

    daily_sorted = sorted(daily_ranges)
    weekly_sorted = sorted(weekly_ranges)
    n = len(daily_sorted)
    nw = len(weekly_sorted)

    print(f'\n=== {symbol} (365d) ===')
    print(f'DAILY RANGE:')
    print(f'  mediana:    {daily_sorted[n//2]:.2f}%')
    print(f'  mitjana:    {sum(daily_ranges)/n:.2f}%')
    print(f'  p25:        {daily_sorted[n//4]:.2f}%')
    print(f'  p75:        {daily_sorted[3*n//4]:.2f}%')
    print(f'  p95:        {daily_sorted[int(0.95*n)]:.2f}%')
    print(f'  max:        {daily_sorted[-1]:.2f}%')
    print(f'  >5%: {sum(1 for r in daily_ranges if r>5)} dies ({sum(1 for r in daily_ranges if r>5)/n*100:.1f}%)')
    print(f'  >10%: {sum(1 for r in daily_ranges if r>10)} dies ({sum(1 for r in daily_ranges if r>10)/n*100:.1f}%)')

    print(f'WEEKLY RANGE (7d rolling):')
    print(f'  mediana:    {weekly_sorted[nw//2]:.2f}%')
    print(f'  p75:        {weekly_sorted[3*nw//4]:.2f}%')
    print(f'  p95:        {weekly_sorted[int(0.95*nw)]:.2f}%')

    return {
        'symbol': symbol,
        'daily_median': daily_sorted[n//2],
        'daily_p75': daily_sorted[3*n//4],
        'daily_p95': daily_sorted[int(0.95*n)],
        'weekly_median': weekly_sorted[nw//2],
        'weekly_p75': weekly_sorted[3*nw//4],
        'weekly_p95': weekly_sorted[int(0.95*nw)],
    }

results = {}
for s in SYMBOLS:
    results[s] = analyze(s)

print('\n\n===== RESUM PER A DIMENSIONAR GRIDS =====')
print(f'{"Asset":12} {"DailyMed":>8} {"DailyP75":>8} {"DailyP95":>8} {"WklyMed":>8} {"WklyP75":>8} {"WklyP95":>8}')
for s, r in results.items():
    print(f'{s:12} {r["daily_median"]:>7.2f}% {r["daily_p75"]:>7.2f}% {r["daily_p95"]:>7.2f}% {r["weekly_median"]:>7.2f}% {r["weekly_p75"]:>7.2f}% {r["weekly_p95"]:>7.2f}%')
