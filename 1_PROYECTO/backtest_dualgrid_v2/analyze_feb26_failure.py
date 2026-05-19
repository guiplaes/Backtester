"""Anàlisi profund del fracas de N11 Model=4 Feb 2026 — on i per que va caure."""
import re
from collections import defaultdict
from pathlib import Path

LOG = Path(r"C:\MT5_Tester3\tester\logs\20260518.log")

# Read full log in chunks
with open(LOG, 'rb') as f:
    f.seek(0, 2); size = f.tell()
    f.seek(0)
    chunks = []
    while f.tell() < size:
        chunks.append(f.read(50*1024*1024))
text = b''.join(chunks).decode('utf-16-le', errors='ignore')

# Extract all deals
pat = re.compile(r'(\d{4}\.\d{2}\.\d{2})\s+(\d{2}:\d{2}:\d{2})\s+deal #\d+ (buy|sell)\s+([\d.]+)\s+\S+ at ([\d.]+) done')
deals = []
for m in pat.finditer(text):
    d, t, side, lot, price = m.groups()
    if float(lot) == 0.02:
        deals.append((d, t, side, float(price)))
print(f"Deals: {len(deals)}")
if deals:
    print(f"Range: {deals[0][0]} to {deals[-1][0]}")

# Pair FIFO
buys=[]; sells=[]
daily = defaultdict(lambda: {'pnl':0, 'trades':0, 'wins':0, 'min_p':1e9, 'max_p':0,
                              'first_p':0, 'last_p':0})

for d, t, side, p in deals:
    if side == 'buy' and sells:
        d0, t0, p0 = sells.pop(0)
        pnl = (p0-p) * 2.0  # 0.02 lot std XAU
        daily[d]['pnl'] += pnl; daily[d]['trades'] += 1
        if pnl > 0: daily[d]['wins'] += 1
    elif side == 'sell' and buys:
        d0, t0, p0 = buys.pop(0)
        pnl = (p-p0) * 2.0
        daily[d]['pnl'] += pnl; daily[d]['trades'] += 1
        if pnl > 0: daily[d]['wins'] += 1
    else:
        if side == 'buy': buys.append((d, t, p))
        else: sells.append((d, t, p))
    daily[d]['min_p'] = min(daily[d]['min_p'], p)
    daily[d]['max_p'] = max(daily[d]['max_p'], p)
    if daily[d]['first_p'] == 0: daily[d]['first_p'] = p
    daily[d]['last_p'] = p

# Daily curve with running balance
print(f"\n{'Date':<12} {'PnL':>8} {'Run':>9} {'Trd':>5} {'WR%':>4} {'Range':>6} {'Trend':>7} {'Note'}")
print("-"*80)
running = 0
peak = 0
max_dd = 0
for d in sorted(daily.keys()):
    x = daily[d]
    running += x['pnl']
    peak = max(peak, running)
    dd = peak - running
    max_dd = max(max_dd, dd)
    rng = x['max_p'] - x['min_p']
    trend = x['last_p'] - x['first_p']
    wr = x['wins']/x['trades']*100 if x['trades'] else 0
    note = ""
    if x['pnl'] < -500: note = "<<< BAD DAY"
    elif x['pnl'] > 500: note = "<<< GOOD DAY"
    elif dd > 8000: note = "<<< NEAR KILL"
    print(f"{d} ${x['pnl']:>+7.0f} ${running:>+8.0f} {x['trades']:>5} {wr:>3.0f}% ${rng:>5.0f} {'+' if trend>0 else '-' if trend<0 else ' '}${abs(trend):>4.0f}  {note}")

print(f"\nPeak running: ${peak:.0f}")
print(f"Max DD: ${max_dd:.0f}")
print(f"Final realized: ${running:.0f}")
print(f"Open buys: {len(buys)}, sells: {len(sells)}")
