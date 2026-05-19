"""Parser de progres intermedi del tester log live (UTF-16-LE)."""
import re
from pathlib import Path

LOG = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\9BB124B7D418C7FB69DF2865535BA9BF\Tester\logs\20260517.log")

text = LOG.read_bytes().decode('utf-16-le', errors='ignore')

# Pattern: 2024.XX.XX HH:MM:SS deal #N (buy|sell) LOT SYMBOL at PRICE done
pat_deal = re.compile(r'(\d{4}\.\d{2}\.\d{2})\s+(\d{2}:\d{2}:\d{2})\s+deal #(\d+) (buy|sell)\s+([\d.]+)\s+(\S+) at ([\d.]+) done')

deals = []
for m in pat_deal.finditer(text):
    date, time, did, side, lot, sym, price = m.groups()
    if float(lot) == 2.0:  # only current 2y test
        deals.append((date, time, int(did), side, float(lot), sym, float(price)))

print(f"Total deals: {len(deals)}")
if not deals:
    exit()

print(f"First: {deals[0][0]} {deals[0][1]} {deals[0][3]} {deals[0][4]}@{deals[0][6]}")
print(f"Last:  {deals[-1][0]} {deals[-1][1]} {deals[-1][3]} {deals[-1][4]}@{deals[-1][6]}")

# Match opens/closes FIFO per side (since EA uses hedging, opens accumulate then close one-by-one)
buys_open = []   # (date, lot, price)
sells_open = []
realized = 0.0
won = 0
lost = 0
gross_p = 0.0
gross_l = 0.0
max_floating_neg = 0.0
running_pnl_curve = []  # (date, realized)

for date, time, did, side, lot, sym, price in deals:
    if side == 'buy':
        # Either opens a buy or closes a sell
        if sells_open:
            d0, lot0, p0 = sells_open.pop(0)
            pnl = (p0 - price) * min(lot, lot0) * 1  # cent XAU: $1 move = $1/lot (1 cent lot = 1oz)
            realized += pnl
            if pnl > 0: won += 1; gross_p += pnl
            else: lost += 1; gross_l += abs(pnl)
        else:
            buys_open.append((date, lot, price))
    else:  # sell
        if buys_open:
            d0, lot0, p0 = buys_open.pop(0)
            pnl = (price - p0) * min(lot, lot0) * 100
            realized += pnl
            if pnl > 0: won += 1; gross_p += pnl
            else: lost += 1; gross_l += abs(pnl)
        else:
            sells_open.append((date, lot, price))
    running_pnl_curve.append((date, realized))

# Compute peak/trough of realized
peak = 50000.0
max_dd = 0.0
balance = 50000.0
for d, r in running_pnl_curve:
    bal = 50000.0 + r
    if bal > peak: peak = bal
    dd = peak - bal
    if dd > max_dd: max_dd = dd

print(f"\n=== INTERMEDIATE (realized P&L only) ===")
print(f"Trades closed: {won + lost}  (won {won}, lost {lost})")
print(f"Realized PnL (cents): ${realized:,.2f}")
print(f"Real USD eq.:         ${realized/100:,.2f}")
print(f"Gross profit: ${gross_p:,.2f}  Gross loss: ${gross_l:,.2f}")
print(f"Profit factor: {(gross_p/gross_l if gross_l else 0):.2f}")
print(f"Win rate: {(won/(won+lost)*100 if won+lost else 0):.1f}%")
print(f"Open buys: {len(buys_open)}  Open sells: {len(sells_open)}")
print(f"Approx balance peak: ${50000+(max(r for _,r in running_pnl_curve)):,.0f}")
print(f"Approx max DD (cents): ${max_dd:,.0f}  ({max_dd/500:.2f}%)")
print(f"\nCurrent simulated date: {deals[-1][0]}")
