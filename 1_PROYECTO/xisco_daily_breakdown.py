"""Desglossament diari del compte CENTS #2 de Xisco
des de l'inici (07/05/2026 16:31 UTC, 50.000 USC) fins avui."""
import re
from collections import defaultdict
from pathlib import Path

DUMP = Path(__file__).parent / "xisco_full_dump.txt"

# CIERRE format: 🔴 **CIERRE** | 🕐 ... | 📊 XAUUSD-VIPc  |  BUY  |  X lotes | 💲 Precio: ... | 💰 Resultado: `+X.XX USD` | 🎫 Ticket: ...
re_close = re.compile(
    r"\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) UTC\].*CIERRE.*?XAUUSD-VIPc.*?Resultado:\s*`([+-][\d.]+) USD`"
)

# Snapshots MT5 Arrancado (per validar balances)
re_snap = re.compile(
    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC\].*MT5 Arrancado.*Balance:\s*([\d.]+)\s*USC"
)

# CENTS account va comencar el 07/05 16:31 UTC amb 50.000 USC
CENTS_START_DATE = "2026-05-07"
CENTS_START_BALANCE = 50_000.00

# Snapshots coneguts (per validar)
known_snapshots = {}
daily_pnl = defaultdict(float)
daily_trades = defaultdict(int)
daily_wins = defaultdict(int)
daily_losses = defaultdict(int)

with open(DUMP, "r", encoding="utf-8") as f:
    for line in f:
        m_s = re_snap.search(line)
        if m_s:
            known_snapshots[m_s.group(1)] = float(m_s.group(2))
            continue
        m = re_close.search(line)
        if not m:
            continue
        date, _, amount_s = m.groups()
        # Nomes a partir del dia que va arrencar el cents account
        if date < CENTS_START_DATE:
            continue
        amt = float(amount_s)
        daily_pnl[date] += amt
        daily_trades[date] += 1
        if amt > 0:
            daily_wins[date] += 1
        elif amt < 0:
            daily_losses[date] += 1

print("=" * 85)
print("DESGLOSSAMENT DIARI - Compte CENTS #2 (XAUUSD-VIPc)")
print(f"Inici: {CENTS_START_DATE} 16:31 UTC  -  Balance inicial: {CENTS_START_BALANCE:,.0f} USC")
print("=" * 85)
print()
print(f"{'Data':<12} {'Open Bal':>12} {'Trades':>7} {'W':>4} {'L':>4} {'WR%':>5} {'P&L USC':>12} {'%':>7} {'Close Bal':>12}")
print("-" * 85)

# Compute running balance day by day
opening = CENTS_START_BALANCE
all_dates = sorted(set(list(daily_pnl.keys()) + [CENTS_START_DATE]))
total_pnl = 0.0
total_trades = 0
total_wins = 0
total_losses = 0

for d in all_dates:
    if d < CENTS_START_DATE:
        continue
    pnl = daily_pnl.get(d, 0.0)
    trades = daily_trades.get(d, 0)
    wins = daily_wins.get(d, 0)
    losses = daily_losses.get(d, 0)
    wr = (100 * wins / max(1, wins + losses)) if (wins + losses) > 0 else 0.0
    pct = (pnl / opening * 100) if opening > 0 else 0.0
    close_bal = opening + pnl

    # Check if there's a known snapshot to update opening (deposit detection)
    snap_today = None
    for k, v in known_snapshots.items():
        if k.startswith(d):
            snap_today = (k, v)
            break

    print(f"{d:<12} {opening:>12,.2f} {trades:>7} {wins:>4} {losses:>4} {wr:>4.1f}% {pnl:>+12,.2f} {pct:>+6.2f}% {close_bal:>12,.2f}")

    # Si hi ha snapshot que no quadra (diferencia gran) -> dipòsit o retirada
    if snap_today and abs(snap_today[1] - close_bal) > 5000:
        diff = snap_today[1] - close_bal
        accion = "DIPOSIT" if diff > 0 else "RETIRADA"
        print(f"           !! {snap_today[0]} snapshot: {snap_today[1]:,.0f} USC -> {accion} aprox {diff:+,.0f} USC")
        # Reseteja l'opening del proper dia segons el snapshot real
        close_bal = snap_today[1]

    opening = close_bal
    total_pnl += pnl
    total_trades += trades
    total_wins += wins
    total_losses += losses

print("-" * 85)
total_wr = 100 * total_wins / max(1, total_wins + total_losses)
total_pct = (total_pnl / CENTS_START_BALANCE * 100) if CENTS_START_BALANCE > 0 else 0
print(f"{'TOTAL':<12} {CENTS_START_BALANCE:>12,.2f} {total_trades:>7} {total_wins:>4} {total_losses:>4} {total_wr:>4.1f}% {total_pnl:>+12,.2f} {total_pct:>+6.1f}% {opening:>12,.2f}")
print()
print("Snapshots MT5 Arrancado vistos al dump:")
for k, v in sorted(known_snapshots.items()):
    if k >= CENTS_START_DATE:
        print(f"  {k} UTC : {v:>12,.2f} USC")
