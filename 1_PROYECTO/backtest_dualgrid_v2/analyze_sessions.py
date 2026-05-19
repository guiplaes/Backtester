"""Analitza profit per hora UTC i per sessio de mercat."""
import re
from pathlib import Path
from collections import defaultdict

LOG = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\9BB124B7D418C7FB69DF2865535BA9BF\Tester\logs\20260517.log")
text = LOG.read_bytes().decode('utf-16-le', errors='ignore')

# Match deals with lot=2 only (current test)
pat = re.compile(r'(\d{4}\.\d{2}\.\d{2})\s+(\d{2}):(\d{2}):\d{2}\s+deal #(\d+) (buy|sell)\s+2\s+\S+ at ([\d.]+) done')

deals = []
for m in pat.finditer(text):
    date, hh, mm, did, side, price = m.groups()
    deals.append((date, int(hh), int(mm), int(did), side, float(price)))

# FIFO match
buys = []
sells = []
closed = []
for date, hh, mm, did, side, price in deals:
    if side == 'buy' and sells:
        d0, h0, m0, p0 = sells.pop(0)
        pnl = (p0 - price) * 2.0
        closed.append((date, hh, mm, pnl))
    elif side == 'sell' and buys:
        d0, h0, m0, p0 = buys.pop(0)
        pnl = (price - p0) * 2.0
        closed.append((date, hh, mm, pnl))
    else:
        if side == 'buy': buys.append((date, hh, mm, price))
        else: sells.append((date, hh, mm, price))

print(f"Closed pairs: {len(closed)}")

# By hour (close time UTC, MT5 server time is UTC+3 typically; we use raw)
by_hour = defaultdict(lambda: {'pnl':0, 'trades':0, 'wins':0})
for date, hh, mm, pnl in closed:
    by_hour[hh]['pnl'] += pnl
    by_hour[hh]['trades'] += 1
    if pnl > 0: by_hour[hh]['wins'] += 1

print(f"\n=== PROFIT PER HORA (server time GMT+1/+2) ===")
print(f"{'Hora':>4} {'PnL':>9} {'Trades':>7} {'WR%':>6}  Session")
for h in range(24):
    d = by_hour[h]
    wr = d['wins']/d['trades']*100 if d['trades'] else 0
    # Session tags (UTC approx — server GMT+1 or +2 depending on DST)
    # Asia: 00-08, London: 08-16, NY: 13-21, Overlap LDN-NY: 13-16
    if 1 <= h <= 8: ses = "Asia"
    elif 8 <= h <= 11: ses = "Asia/London"
    elif 12 <= h <= 14: ses = "London"
    elif 14 <= h <= 17: ses = "London+NY"
    elif 17 <= h <= 21: ses = "NY"
    else: ses = "Quiet"
    marker = ""
    if d['pnl'] < -50: marker = " <<< PERD"
    elif d['pnl'] > 50: marker = " <<< GUANYA"
    print(f"{h:>3}h ${d['pnl']:>+8.0f} {d['trades']:>6}  {wr:>5.0f}%  {ses}{marker}")

# By session
sessions = {
    'Asia (00-07)':      list(range(0, 8)),
    'London (08-12)':    list(range(8, 13)),
    'Overlap (13-16)':   list(range(13, 17)),
    'NY (17-20)':        list(range(17, 21)),
    'Late (21-23)':      list(range(21, 24)),
}

print(f"\n=== PROFIT PER SESSIO ===")
print(f"{'Sessio':<20} {'PnL':>10} {'Trades':>7} {'WR%':>6}")
for name, hours in sessions.items():
    tot_pnl = sum(by_hour[h]['pnl'] for h in hours)
    tot_trd = sum(by_hour[h]['trades'] for h in hours)
    tot_win = sum(by_hour[h]['wins'] for h in hours)
    wr = tot_win/tot_trd*100 if tot_trd else 0
    print(f"{name:<20} ${tot_pnl:>+9.0f} {tot_trd:>6}  {wr:>5.1f}%")

# By weekday
from datetime import datetime
by_wd = defaultdict(lambda: {'pnl':0, 'trades':0, 'wins':0})
for date, hh, mm, pnl in closed:
    try:
        dt = datetime.strptime(date, '%Y.%m.%d')
        by_wd[dt.weekday()]['pnl'] += pnl
        by_wd[dt.weekday()]['trades'] += 1
        if pnl > 0: by_wd[dt.weekday()]['wins'] += 1
    except: pass

print(f"\n=== PROFIT PER DIA DE LA SETMANA ===")
wd_names = ['Dilluns','Dimarts','Dimecres','Dijous','Divendres','Dissabte','Diumenge']
for i in range(7):
    d = by_wd[i]
    wr = d['wins']/d['trades']*100 if d['trades'] else 0
    print(f"{wd_names[i]:<12} ${d['pnl']:>+8.0f} {d['trades']:>6}  WR {wr:>5.1f}%")
