"""Analitza historial complet (58 dies) — quan apareix cada compte i amb quin balance."""
import re
from collections import defaultdict
from pathlib import Path

DUMP = Path(__file__).parent / "xisco_full_dump.txt"

# Counts aperturas per dia per compte
per_day = defaultdict(lambda: {"REAL": 0, "CENTS": 0, "OTHER": 0})

# Snapshots MT5 Arrancado
balance_snapshots = []  # (ts, account, balance, equity)

# Diferents simbols vistos
symbols_seen = defaultdict(set)  # date -> set of symbols

with open(DUMP, "r", encoding="utf-8") as f:
    for line in f:
        # MT5 Arrancado
        m = re.search(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC\].*MT5 Arrancado.*Balance:\s*([\d.]+)\s*(USD|USC)\s*Equity:\s*([\d.]+)", line)
        if m:
            ts, bal, cur, eq = m.groups()
            balance_snapshots.append((ts, cur, float(bal), float(eq)))
            continue

        # APERTURA
        m = re.search(r"\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) UTC\].*APERTURA.*?(XAUUSD-[A-Za-z]+)", line)
        if m:
            date, hour, sym = m.groups()
            symbols_seen[date].add(sym)
            if sym == "XAUUSD-VIP":
                per_day[date]["REAL"] += 1
            elif sym == "XAUUSD-VIPc":
                per_day[date]["CENTS"] += 1
            else:
                per_day[date]["OTHER"] += 1

print("="*80)
print("HISTORIAL — APERTURAS PER DIA per compte")
print("="*80)
print(f"{'Data':<12} {'REAL (VIP)':>12} {'CENTS (VIPc)':>14} {'OTHER':>8}  Simbols")
print("-"*80)
for d in sorted(per_day.keys()):
    r = per_day[d]["REAL"]
    c = per_day[d]["CENTS"]
    o = per_day[d]["OTHER"]
    syms = ",".join(sorted(symbols_seen[d]))
    print(f"{d:<12} {r:>12} {c:>14} {o:>8}  {syms}")
print()

print("="*80)
print("SNAPSHOTS DE BALANCE (MT5 Arrancado)")
print("="*80)
for ts, cur, bal, eq in balance_snapshots:
    bal_usd = bal if cur == "USD" else bal/100
    print(f"  {ts}  [{cur:>3}]  balance={bal:>12,.2f} {cur}  (~${bal_usd:>9,.2f} USD)  equity={eq:,.2f}")
