"""Cronologia per compte: quan opera cada un?"""
import re
from collections import defaultdict
from pathlib import Path

DUMP = Path(__file__).parent / "xisco_channel_dump.txt"

# XAUUSD-VIP = REAL (USD)
# XAUUSD-VIPc = CENTS (USC)

# Per hora: comptem aperturas per compte
hour_real = defaultdict(int)   # date+hour -> count
hour_cents = defaultdict(int)

# Per dia: primera i ultima activitat per compte
day_real_first = {}
day_real_last = {}
day_cents_first = {}
day_cents_last = {}

with open(DUMP, "r", encoding="utf-8") as f:
    for line in f:
        m = re.search(r"\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) UTC\].*APERTURA", line)
        if not m:
            continue
        date, hour = m.groups()
        ts = f"{date} {hour}"
        if "XAUUSD-VIPc" in line:
            hour_cents[date+" "+hour[:2]] += 1
            if date not in day_cents_first:
                day_cents_first[date] = hour
            day_cents_last[date] = hour
        elif "XAUUSD-VIP" in line:
            hour_real[date+" "+hour[:2]] += 1
            if date not in day_real_first:
                day_real_first[date] = hour
            day_real_last[date] = hour

print("="*70)
print("CRONOLOGIA per compte (aperturas per dia)")
print("="*70)
print(f"{'Data':<12} {'REAL primera':<14} {'REAL ultima':<14} {'CENTS primera':<14} {'CENTS ultima':<14}")
print("-"*70)
all_dates = sorted(set(list(day_real_first.keys()) + list(day_cents_first.keys())))
for d in all_dates:
    rf = day_real_first.get(d, "—")
    rl = day_real_last.get(d, "—")
    cf = day_cents_first.get(d, "—")
    cl = day_cents_last.get(d, "—")
    print(f"{d:<12} {rf:<14} {rl:<14} {cf:<14} {cl:<14}")
print()

# Algun dia amb activitat als 2 comptes alhora?
print("="*70)
print("DIES amb activitat als DOS comptes alhora (overlap)")
print("="*70)
both = set(day_real_first.keys()) & set(day_cents_first.keys())
for d in sorted(both):
    rf = day_real_first[d]; rl = day_real_last[d]
    cf = day_cents_first[d]; cl = day_cents_last[d]
    # Veiem si les finestres es solapen
    if min(rl, cl) >= max(rf, cf):
        overlap = f"OVERLAP {max(rf,cf)}-{min(rl,cl)}"
    else:
        overlap = "no overlap"
    print(f"  {d}: REAL {rf}-{rl}  CENTS {cf}-{cl}  -> {overlap}")
print()

# Recompte total per compte
total_real = sum(hour_real.values())
total_cents = sum(hour_cents.values())
print("="*70)
print(f"TOTAL aperturas: REAL={total_real}, CENTS={total_cents}")
print(f"Ratio: REAL fa el {100*total_real/(total_real+total_cents):.0f}%, CENTS fa el {100*total_cents/(total_real+total_cents):.0f}%")
