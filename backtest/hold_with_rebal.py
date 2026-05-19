"""
Baseline: hold dels 4 actius amb rebalanceig (sense grid).
Per saber quina és la contribució del grid per se.
"""
from data_loader import load_csv
from datetime import datetime, timezone

CAPS = {'PAXG_USDT': 380, 'BTC_USDT': 285, 'ETH_USDT': 190, 'SOL_USDT': 95}
TARGET_WEIGHTS = {'PAXG_USDT': 0.40, 'BTC_USDT': 0.30, 'ETH_USDT': 0.20, 'SOL_USDT': 0.10}
RESERVE_INITIAL = 50.0
REBALANCE_THRESHOLD = 0.05  # 5% deviation
FEE_RATE = 0.0005  # 0.05% per moviment

# Carrega tots els assets
print("Carregant CSVs...")
data = {sym: load_csv(sym) for sym in CAPS}
ts_set = set()
for bars in data.values():
    ts_set.update(bars.keys())
sorted_ts = sorted(ts_set)
print(f"Total minuts: {len(sorted_ts):,}")

# Estat inicial: cada bot té qty_base a preu inicial
state = {}
first_ts = sorted_ts[0]
for sym, cap in CAPS.items():
    first_price = data[sym][first_ts]['open']
    state[sym] = {
        'base': cap / first_price,
        'usdt': 0.0,
    }
reserve = RESERVE_INITIAL
total_fees_paid = 0.0
rebal_count = 0
last_rebal_min = 0

# Itera cada minut (només mirem cada 2 min per velocitat com el sistema real)
checks = 0
for i, ts in enumerate(sorted_ts):
    minute_idx = ts // 60_000
    if minute_idx - last_rebal_min < 2:
        continue
    last_rebal_min = minute_idx
    checks += 1

    # Calcula valor actual per bot
    bars_now = {sym: data[sym].get(ts) for sym in CAPS}
    if any(b is None for b in bars_now.values()):
        continue
    prices = {sym: bars_now[sym]['close'] for sym in CAPS}
    values = {sym: state[sym]['base'] * prices[sym] + state[sym]['usdt'] for sym in CAPS}
    total_value = sum(values.values()) + reserve
    if total_value <= 0:
        continue

    weights = {sym: values[sym] / total_value for sym in CAPS}

    # Identifica overs/unders
    overs = []
    unders = []
    for sym in CAPS:
        target = TARGET_WEIGHTS[sym]
        dev = weights[sym] - target
        if dev >= REBALANCE_THRESHOLD:
            overs.append((sym, dev * total_value))
        elif dev <= -REBALANCE_THRESHOLD:
            unders.append((sym, -dev * total_value))

    if not unders or not overs:
        continue

    # Rebalance: per cada under, agafem dels overs i del reserve
    for under_sym, under_amt in unders:
        remaining = under_amt
        # Primer del reserve
        if reserve > 0:
            use = min(reserve, remaining)
            if use >= 5:  # min $5
                reserve -= use
                # Comprem base al under_sym
                qty_bought = use / prices[under_sym]
                fee = use * FEE_RATE
                state[under_sym]['base'] += qty_bought * (1 - FEE_RATE)
                total_fees_paid += fee
                remaining -= use
        # Llavors dels overs
        for j, (over_sym, over_amt) in enumerate(overs):
            if remaining < 5:
                break
            use = min(over_amt, remaining)
            if use < 5:
                continue
            # Venem base de l'over
            qty_sold = use / prices[over_sym]
            if state[over_sym]['base'] >= qty_sold:
                state[over_sym]['base'] -= qty_sold
                fee_sell = use * FEE_RATE
                # Comprem base de l'under
                fee_buy = use * FEE_RATE
                state[under_sym]['base'] += use * (1 - FEE_RATE) / prices[under_sym]
                total_fees_paid += fee_sell + fee_buy
                remaining -= use
                overs[j] = (over_sym, over_amt - use)
        rebal_count += 1

# Final
last_ts = sorted_ts[-1]
final_prices = {sym: data[sym][last_ts]['close'] for sym in CAPS}
final_values = {sym: state[sym]['base'] * final_prices[sym] + state[sym]['usdt'] for sym in CAPS}
total_final = sum(final_values.values()) + reserve

print()
print("=" * 60)
print("HOLD + REBALANCEIG (sense grid) — 12 MESOS")
print("=" * 60)
print(f"Capital inicial:     $1,000.00")
print(f"Reserve preservada:  ${reserve:.2f}")
print(f"Total final:         ${total_final:.2f}")
print(f"P&L:                 ${total_final - 1000:+.2f}  ({(total_final-1000)/1000*100:+.2f}%)")
print(f"Rebalanceigs fets:   {rebal_count}")
print(f"Fees totals:         ${total_fees_paid:.2f}")
print()
print("Per asset (final):")
for sym in CAPS:
    print(f"  {sym}: base={state[sym]['base']:.6f}, value=${final_values[sym]:.2f}")
