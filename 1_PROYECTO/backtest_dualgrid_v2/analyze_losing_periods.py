"""Analitza periodes de perdua i correlaciona amb moviments de preu."""
import re
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta

LOG = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\9BB124B7D418C7FB69DF2865535BA9BF\Tester\logs\20260517.log")
text = LOG.read_bytes().decode('utf-16-le', errors='ignore')

# Extract deals with lot=2.0 only (current test)
pat = re.compile(r'(\d{4}\.\d{2}\.\d{2})\s+(\d{2}:\d{2}:\d{2})\s+deal #(\d+) (buy|sell)\s+(2)\s+\S+ at ([\d.]+) done')

deals = []
for m in pat.finditer(text):
    date, time, did, side, lot, price = m.groups()
    deals.append((date, time, int(did), side, 2.0, float(price)))

print(f"Deals lot=2.0: {len(deals)}")
if not deals:
    exit()

# Pair opens/closes FIFO and compute P&L per close
buys = []  # (date, time, price)
sells = []
closed = []  # (date_close, time_close, side_closed, pnl)

for date, time, did, side, lot, price in deals:
    if side == 'buy':
        if sells:
            d0, t0, p0 = sells.pop(0)
            pnl = (p0 - price) * 2.0  # cent XAU: 1 lot = 1oz, 2 lot = 2oz
            closed.append((date, time, 'sell_closed', pnl, p0, price))
        else:
            buys.append((date, time, price))
    else:
        if buys:
            d0, t0, p0 = buys.pop(0)
            pnl = (price - p0) * 2.0
            closed.append((date, time, 'buy_closed', pnl, p0, price))
        else:
            sells.append((date, time, price))

print(f"Closed pairs: {len(closed)}")

# Aggregate by day
daily = defaultdict(lambda: {'pnl':0, 'trades':0, 'wins':0, 'losses':0,
                             'min_price':1e9, 'max_price':0})
for date, time, kind, pnl, p_open, p_close in closed:
    d = daily[date]
    d['pnl'] += pnl
    d['trades'] += 1
    if pnl > 0: d['wins'] += 1
    else: d['losses'] += 1
    d['min_price'] = min(d['min_price'], p_open, p_close)
    d['max_price'] = max(d['max_price'], p_open, p_close)

# Sort by date
dates_sorted = sorted(daily.keys())
running = 0.0
print(f"\n{'Date':<12} {'PnL':>10} {'Running':>11} {'Trades':>7} {'WR%':>5} {'Range$':>7}")
print("-"*60)

losing_days = []
big_wins = []
for date in dates_sorted:
    d = daily[date]
    running += d['pnl']
    wr = d['wins']/d['trades']*100 if d['trades'] else 0
    rng = d['max_price'] - d['min_price']
    line = f"{date:<12} ${d['pnl']:>+9.0f} ${running:>+10.0f} {d['trades']:>6}  {wr:>4.0f}% ${rng:>6.0f}"
    # Tag bad days
    if d['pnl'] < -200:
        losing_days.append((date, d['pnl'], rng, d['trades'], wr))
        line += " <<< BIG LOSS"
    elif d['pnl'] > 300:
        big_wins.append((date, d['pnl'], rng, d['trades'], wr))
        line += " <<< BIG WIN"
    print(line)

print("\n=== WORST 10 LOSING DAYS ===")
losing_days.sort(key=lambda x: x[1])
for d, pnl, rng, n, wr in losing_days[:10]:
    print(f"  {d}: ${pnl:+.0f}  range ${rng:.0f}  {n} trades  WR {wr:.0f}%")

print("\n=== BEST 10 WINNING DAYS ===")
big_wins.sort(key=lambda x: -x[1])
for d, pnl, rng, n, wr in big_wins[:10]:
    print(f"  {d}: ${pnl:+.0f}  range ${rng:.0f}  {n} trades  WR {wr:.0f}%")

# Correlation: are losing days the ones with bigger price range (strong trend)?
import statistics
if losing_days and big_wins:
    avg_rng_loss = statistics.mean(d[2] for d in losing_days)
    avg_rng_win  = statistics.mean(d[2] for d in big_wins)
    print(f"\n=== CORRELATION ===")
    print(f"Avg daily range on BIG LOSS days: ${avg_rng_loss:.0f}")
    print(f"Avg daily range on BIG WIN  days: ${avg_rng_win:.0f}")
    if avg_rng_loss > avg_rng_win:
        print("=> Bot fa MES perdues en dies de moviment fort (tendencia)")
    else:
        print("=> Bot fa MES perdues en dies de moviment baix (sense rang)")
