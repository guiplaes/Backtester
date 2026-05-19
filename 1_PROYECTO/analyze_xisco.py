"""Analitza el dump del canal Xisco i computa P&L."""
import re
from collections import defaultdict
from pathlib import Path

DUMP = Path(__file__).parent / "xisco_channel_dump.txt"

# Patrons
# CIERRE ... `+21.00 USD` o `-71.85 USD`
re_close = re.compile(r"\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) UTC\].*CIERRE.*?Resultado:\s*`([+-][\d.]+) USD`")
re_open  = re.compile(r"\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) UTC\].*APERTURA")
re_account = re.compile(r"XAUUSD-VIPc")  # cuenta cents (USC)

per_day_usd = defaultdict(float)
per_day_usc = defaultdict(float)
per_day_wins = defaultdict(int)
per_day_losses = defaultdict(int)
per_day_opens = defaultdict(int)

all_trades = []
total_usd = 0.0
total_usc = 0.0
wins = 0
losses = 0
opens_total = 0

with open(DUMP, "r", encoding="utf-8") as f:
    for line in f:
        if "APERTURA" in line:
            m = re_open.search(line)
            if m:
                date = m.group(1)
                per_day_opens[date] += 1
                opens_total += 1
            continue
        m = re_close.search(line)
        if not m:
            continue
        date, _, amount_s = m.groups()
        amount = float(amount_s)
        is_usc = bool(re_account.search(line))  # XAUUSD-VIPc = cents (USC)
        if is_usc:
            per_day_usc[date] += amount
            total_usc += amount
        else:
            per_day_usd[date] += amount
            total_usd += amount
        if amount > 0:
            wins += 1
            per_day_wins[date] += 1
        elif amount < 0:
            losses += 1
            per_day_losses[date] += 1
        all_trades.append((date, amount, is_usc))

print("="*78)
print("ANALISIS CANAL 'Senales Xisco Analisis' - ultims 7 dies")
print("="*78)
print()
print(f"Total CIERRES detectats: {wins + losses}  (wins={wins}, losses={losses})")
print(f"Total APERTURES detectades: {opens_total}")
print(f"Winrate: {100*wins/max(1,wins+losses):.1f}%")
print()
print("--- Compte REAL (XAUUSD-VIP, USD) ---")
print(f"P&L total: {total_usd:+.2f} USD")
print()
print("--- Compte CENTS (XAUUSD-VIPc, USC = USD/100) ---")
print(f"P&L total: {total_usc:+.2f} USC  (= {total_usc/100:+.2f} USD equivalents)")
print()
print(f"P&L AGREGAT (real+cents convertit): {total_usd + total_usc/100:+.2f} USD")
print()

print("="*78)
print("DESGLOSSAMENT PER DIA")
print("="*78)
print(f"{'Data':<12} {'Opens':>6} {'Wins':>5} {'Loss':>5} {'WR%':>5} {'USD (real)':>12} {'USC (cents)':>13}")
print("-"*78)
all_dates = sorted(set(list(per_day_usd.keys()) + list(per_day_usc.keys()) + list(per_day_opens.keys())))
for d in all_dates:
    o = per_day_opens.get(d, 0)
    w = per_day_wins.get(d, 0)
    l = per_day_losses.get(d, 0)
    wr = 100*w/max(1, w+l)
    u = per_day_usd.get(d, 0.0)
    c = per_day_usc.get(d, 0.0)
    print(f"{d:<12} {o:>6} {w:>5} {l:>5} {wr:>4.1f}% {u:>+12.2f} {c:>+13.2f}")
print("-"*78)
print(f"{'TOTAL':<12} {opens_total:>6} {wins:>5} {losses:>5} "
      f"{100*wins/max(1,wins+losses):>4.1f}% {total_usd:>+12.2f} {total_usc:>+13.2f}")
print()

# Pitjor i millor dia (compte real)
if per_day_usd:
    worst = min(per_day_usd.items(), key=lambda x: x[1])
    best = max(per_day_usd.items(), key=lambda x: x[1])
    print(f"Millor dia (real): {best[0]} -> {best[1]:+.2f} USD")
    print(f"Pitjor dia (real): {worst[0]} -> {worst[1]:+.2f} USD")
