"""Analisi mes profunda: dia 11 desastrós cents, dia 12 recuperacio."""
import re
from collections import defaultdict
from pathlib import Path

DUMP = Path(__file__).parent / "xisco_channel_dump.txt"

re_close = re.compile(r"\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) UTC\].*CIERRE.*?([\d.]+) lotes.*?Resultado:\s*`([+-][\d.]+) USD`")
re_account_type = re.compile(r"XAUUSD-VIPc")

# Mira ultimes 100 linies pel "tail" - posicions encara obertes
lines = open(DUMP, "r", encoding="utf-8").read().split("\n")

# Trobar la ultima NO_POSITIONS
last_no_positions = None
for i, line in enumerate(lines):
    if "NO_POSITIONS" in line:
        last_no_positions = i

print("=== ESTAT FINAL: ultimes aperturas SENSE tancament posterior ===")
print(f"Ultima NO_POSITIONS a linia {last_no_positions}")
print(f"Total linies: {len(lines)}")
print()

# Tickets oberts (apertures sense cierre)
tickets_open = {}  # ticket -> (date, lots, price, side, account_type)
for line in lines:
    m = re.search(r"\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}).*?(APERTURA|CIERRE).*?(SELL|BUY).*?([\d.]+) lotes.*?Precio:\s*`([\d.]+)`.*?Ticket:\s*`(\d+)`", line)
    if not m:
        continue
    date, hour, typ, side, lots, price, ticket = m.groups()
    account = "USC" if "VIPc" in line else "USD"
    if typ == "APERTURA":
        tickets_open[ticket] = (date, hour, side, lots, price, account)
    elif typ == "CIERRE":
        tickets_open.pop(ticket, None)

print(f"Posicions encara OBERTES al final del dump: {len(tickets_open)}")
print()
# Agrupa per data
by_date_open = defaultdict(list)
for tk, (d, h, side, lots, price, acc) in tickets_open.items():
    by_date_open[d].append((h, side, lots, price, acc, tk))
for d in sorted(by_date_open.keys()):
    print(f"  [{d}]")
    for h, side, lots, price, acc, tk in sorted(by_date_open[d]):
        print(f"    {h} {side} {lots} @ {price} ({acc}) ticket {tk}")
print()

# DIA 11 (gran perdua cents) — quin moviment va passar?
print("=== DIA 11-05 (gran perdua USC -5847) ===")
day11_losses = []
day11_wins = []
for line in lines:
    if "2026-05-11" not in line:
        continue
    m = re_close.search(line)
    if not m:
        continue
    date, hour, lots, amount_s = m.groups()
    amount = float(amount_s)
    if amount > 0:
        day11_wins.append((hour, lots, amount))
    elif amount < 0:
        day11_losses.append((hour, lots, amount))

print(f"Wins dia 11: {len(day11_wins)}  suma: {sum(x[2] for x in day11_wins):+.2f}")
print(f"Loss dia 11: {len(day11_losses)}  suma: {sum(x[2] for x in day11_losses):+.2f}")
print(f"Top 10 PERDUES dia 11:")
for h, lots, amt in sorted(day11_losses, key=lambda x: x[2])[:10]:
    print(f"   {h} {lots} lots -> {amt:+.2f} USC")
print()
print(f"Top 10 GUANYS dia 11:")
for h, lots, amt in sorted(day11_wins, key=lambda x: -x[2])[:10]:
    print(f"   {h} {lots} lots -> {amt:+.2f} USC")
