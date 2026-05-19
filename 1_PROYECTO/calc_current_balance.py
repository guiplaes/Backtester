"""Calcula balance actual estimat del CENTS #2 sumant tancaments
desde l'ultim snapshot conegut."""
import re
from datetime import datetime
from pathlib import Path

DUMP = Path(__file__).parent / "xisco_channel_dump.txt"

# Ultim snapshot conegut
SNAPSHOT_TS = datetime(2026, 5, 10, 19, 40)
SNAPSHOT_BAL = 221248.87  # USC

re_close = re.compile(r"\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) UTC\].*CIERRE.*?XAUUSD-VIPc.*?([\d.]+) lotes.*?Precio:\s*`([\d.]+)`.*?Resultado:\s*`([+-][\d.]+) USD`")
re_open  = re.compile(r"\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) UTC\].*APERTURA.*?XAUUSD-VIPc.*?(SELL|BUY).*?([\d.]+) lotes.*?Precio:\s*`([\d.]+)`.*?Ticket:\s*`(\d+)`")

closed_pnl = 0.0
closed_count = 0
closed_wins = 0
closed_losses = 0

# Tickets oberts post-snapshot
opens_after = {}  # ticket -> (ts, side, lots, price)

with open(DUMP, "r", encoding="utf-8") as f:
    for line in f:
        # APERTURA post-snapshot
        m = re_open.search(line)
        if m:
            d, h, side, lots, price, ticket = m.groups()
            ts = datetime.strptime(f"{d} {h}", "%Y-%m-%d %H:%M")
            if ts >= SNAPSHOT_TS:
                opens_after[ticket] = (ts, side, float(lots), float(price))

        # CIERRE post-snapshot
        m = re_close.search(line)
        if not m: continue
        d, h, lots, price, amt_s = m.groups()
        ts = datetime.strptime(f"{d} {h}", "%Y-%m-%d %H:%M")
        if ts < SNAPSHOT_TS: continue
        amt = float(amt_s)
        closed_pnl += amt
        closed_count += 1
        if amt > 0: closed_wins += 1
        elif amt < 0: closed_losses += 1
        # Remove from opens si encara hi era
        ticket_m = re.search(r"Ticket:\s*`(\d+)`", line)
        if ticket_m:
            opens_after.pop(ticket_m.group(1), None)

# Balance estimat
balance_now = SNAPSHOT_BAL + closed_pnl

print("="*70)
print("CÀLCUL BALANCE ACTUAL ESTIMAT — CENTS #2 (XAUUSD-VIPc)")
print("="*70)
print()
print(f"  Snapshot conegut: 10/05 19:40 UTC -> {SNAPSHOT_BAL:>12,.2f} USC")
print()
print(f"  P&L tancat desde llavors: {closed_pnl:>+12,.2f} USC")
print(f"    ({closed_count} tancaments: {closed_wins} wins / {closed_losses} losses)")
print()
print(f"  BALANCE ESTIMAT ARA:      {balance_now:>+12,.2f} USC")
print(f"                          = ${balance_now/100:>+9,.2f} USD")
print()
print("-"*70)
print(f"  Posicions encara OBERTES (sense tancament al dump): {len(opens_after)}")
print("-"*70)

# Floating P&L estimat amb preu actual aproximat
# Xisco últim preu vist 13/05 ~ 4700 region
LAST_PRICE = 4700.0  # aprox
print(f"\nEstimacio floating P&L amb gold ~${LAST_PRICE}:\n")
floating_pnl = 0.0
for tk, (ts, side, lots, price) in sorted(opens_after.items(), key=lambda x: x[1][0]):
    # SELL guanya si LAST_PRICE < price
    if side == "SELL":
        pnl = (price - LAST_PRICE) * lots * 100
    else:
        pnl = (LAST_PRICE - price) * lots * 100
    floating_pnl += pnl
    # Mostrem nomes les top 5 perdedores i 5 millors
print(f"  Floating estimat (preu ${LAST_PRICE}): {floating_pnl:>+12,.2f} USC = ${floating_pnl/100:>+8,.2f} USD")
print()
print(f"  EQUITY ESTIMADA: {balance_now + floating_pnl:>+12,.2f} USC = ${(balance_now+floating_pnl)/100:>+8,.2f} USD")
