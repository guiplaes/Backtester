"""Analisi profund del backtest LIVE 2 anys: mensual + identificacio bon/mal"""
import re
from pathlib import Path
from collections import defaultdict
from datetime import datetime

LOG = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\9BB124B7D418C7FB69DF2865535BA9BF\Tester\logs\20260517.log")
text = LOG.read_bytes().decode('utf-16-le', errors='ignore')

# Match deals with lot=2 only (current live test)
pat = re.compile(r'(\d{4}\.\d{2}\.\d{2})\s+(\d{2}):(\d{2}):\d{2}\s+deal #(\d+) (buy|sell)\s+2\s+\S+ at ([\d.]+) done')

deals = []
for m in pat.finditer(text):
    date, hh, mm, did, side, price = m.groups()
    deals.append((date, int(hh), int(mm), int(did), side, float(price)))

print(f"Total deals lot=2: {len(deals)}")

# Pair opens/closes FIFO
buys = []
sells = []
closed = []
for date, hh, mm, did, side, price in deals:
    if side == 'buy' and sells:
        d0, h0, m0, p0 = sells.pop(0)
        pnl = (p0 - price) * 2.0
        closed.append((date, hh, pnl, p0, price, 'close_sell'))
    elif side == 'sell' and buys:
        d0, h0, m0, p0 = buys.pop(0)
        pnl = (price - p0) * 2.0
        closed.append((date, hh, pnl, p0, price, 'close_buy'))
    else:
        if side == 'buy': buys.append((date, hh, mm, price))
        else: sells.append((date, hh, mm, price))

print(f"Closed pairs: {len(closed)}")

# By MONTH
by_month = defaultdict(lambda: {'pnl':0, 'trades':0, 'wins':0,
                                'min_price':1e9, 'max_price':0, 'first_p':0, 'last_p':0})
for date, hh, pnl, p_open, p_close, kind in closed:
    ym = date[:7]
    m = by_month[ym]
    m['pnl'] += pnl
    m['trades'] += 1
    if pnl > 0: m['wins'] += 1
    m['min_price'] = min(m['min_price'], p_open, p_close)
    m['max_price'] = max(m['max_price'], p_open, p_close)
    if m['first_p'] == 0: m['first_p'] = p_open
    m['last_p'] = p_close

# Print monthly table
print(f"\n=== PROFIT PER MES (live 2y) ===")
print(f"{'Month':<8} {'PnL_cent':>10} {'USD_real':>9} {'Trades':>7} {'WR%':>5} {'Range$':>7} {'Trend':>8}")
print("-"*70)
running = 0
months = sorted(by_month.keys())
for ym in months:
    m = by_month[ym]
    running += m['pnl']
    wr = m['wins']/m['trades']*100 if m['trades'] else 0
    rng = m['max_price'] - m['min_price']
    trend = m['last_p'] - m['first_p']
    sign = '+' if trend > 0 else '-' if trend < 0 else ' '
    tag = ""
    if m['pnl'] < -1000: tag = " <<< BAD"
    elif m['pnl'] > 2000: tag = " <<< GOOD"
    print(f"{ym:<8} ${m['pnl']:>+8.0f}  ${m['pnl']/100:>+7.0f} {m['trades']:>6}  {wr:>4.0f}% ${rng:>6.0f} {sign}${abs(trend):>5.0f}{tag}")

# Summary by year
print(f"\n=== ANUAL ===")
by_year = defaultdict(lambda: {'pnl':0, 'trades':0, 'wins':0})
for ym in months:
    y = ym[:4]
    m = by_month[ym]
    by_year[y]['pnl'] += m['pnl']
    by_year[y]['trades'] += m['trades']
    by_year[y]['wins'] += m['wins']
for y in sorted(by_year):
    yr = by_year[y]
    wr = yr['wins']/yr['trades']*100 if yr['trades'] else 0
    print(f"{y}: ${yr['pnl']:>+9.0f} cent (${yr['pnl']/100:+.0f} USD)  {yr['trades']} trades  WR {wr:.1f}%")

# WORST months
print(f"\n=== PITJORS 5 MESOS ===")
worst = sorted(months, key=lambda y: by_month[y]['pnl'])[:5]
for ym in worst:
    m = by_month[ym]
    trend = m['last_p'] - m['first_p']
    wr = m['wins']/m['trades']*100
    print(f"  {ym}: ${m['pnl']:>+7.0f}  range ${m['max_price']-m['min_price']:.0f}  trend {trend:+.0f}  WR {wr:.0f}%")

# BEST months
print(f"\n=== MILLORS 5 MESOS ===")
best = sorted(months, key=lambda y: -by_month[y]['pnl'])[:5]
for ym in best:
    m = by_month[ym]
    trend = m['last_p'] - m['first_p']
    wr = m['wins']/m['trades']*100
    print(f"  {ym}: ${m['pnl']:>+7.0f}  range ${m['max_price']-m['min_price']:.0f}  trend {trend:+.0f}  WR {wr:.0f}%")
