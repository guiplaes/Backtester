"""
True grid-bot simulation against real M5 candles.

State machine:
- Each level has either a BUY or SELL pending (never both)
- Levels above current price start with SELL pending
- Levels below current price start with BUY pending
- When price crosses level L going UP:
    if pending=SELL  → SELL fires (1 trade). Bot places BUY at L-step. cycle++
    else (pending=BUY)  → nothing (BUY was already filled before)
- When price crosses level L going DOWN:
    if pending=BUY   → BUY fires (1 trade). Bot places SELL at L+step. cycle++
    else (pending=SELL) → nothing

This matches Pionex spot-grid behavior. A "cycle" is one matched BUY+SELL pair
between two adjacent levels = real profit of (step/price × capital_per_grid − 2×fee).
"""
import sys
import time
from datetime import datetime, timezone

from pionex_client import _get_public


def fetch_klines_backwards(symbol: str, interval: str, days: int) -> list:
    now_ms = int(time.time() * 1000)
    target_ms = now_ms - days * 86400 * 1000
    klines, seen, cursor = [], set(), now_ms
    for _ in range(200):
        r = _get_public("/api/v1/market/klines", {
            "symbol": symbol, "interval": interval, "limit": 500, "endTime": cursor
        })
        chunk = r.get("data", {}).get("klines", [])
        if not chunk:
            break
        new = [k for k in chunk if int(k["time"]) not in seen]
        if not new:
            break
        for k in new:
            seen.add(int(k["time"]))
        klines.extend(new)
        oldest = min(int(k["time"]) for k in new)
        if oldest <= target_ms:
            break
        cursor = oldest - 1
    klines.sort(key=lambda k: int(k["time"]))
    return klines


def simulate_grid(klines, levels, step, start_price):
    """Simulate using HIGH/LOW of each bar so intra-bar touches fire orders
    just like Pionex executes when the market price touches the limit.

    For each bar we assume the worst-realistic path: open → far extreme → other → close.
    The path determines order in which levels get touched.
    """
    pending = {}
    for L in levels:
        pending[L] = "BUY" if L < start_price else ("SELL" if L > start_price else None)

    sells = {L: 0 for L in levels}
    buys = {L: 0 for L in levels}
    last_price = start_price
    lev_sorted = sorted(levels)

    def fire_up(from_p, to_p):
        for L in lev_sorted:
            if from_p < L <= to_p and pending[L] == "SELL":
                sells[L] += 1
                pending[L] = None
                below = round(L - step, 4)
                if below in pending:
                    pending[below] = "BUY"

    def fire_down(from_p, to_p):
        for L in reversed(lev_sorted):
            if from_p > L >= to_p and pending[L] == "BUY":
                buys[L] += 1
                pending[L] = None
                above = round(L + step, 4)
                if above in pending:
                    pending[above] = "SELL"

    for k in klines:
        o = float(k["open"])
        h = float(k["high"])
        l = float(k["low"])
        c = float(k["close"])
        # Path heuristic: if close > open, assume open → low → high → close.
        # Else open → high → low → close. This walks both extremes.
        if c >= o:
            path = [o, l, h, c]
        else:
            path = [o, h, l, c]
        for i in range(1, len(path)):
            p_from, p_to = path[i - 1], path[i]
            if p_to > p_from:
                fire_up(p_from, p_to)
            elif p_to < p_from:
                fire_down(p_from, p_to)
        last_price = c

    return sells, buys


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    bottom, top, rows = 4600.0, 4850.0, 12
    step = (top - bottom) / rows
    # Use rounded levels so dict lookups match (sub-cent precision drift)
    levels = [round(bottom + i * step, 4) for i in range(rows + 1)]

    interval = sys.argv[2] if len(sys.argv) > 2 else "5M"
    print(f"Fetching {interval} PAXG_USDT klines for last {days}d ...")
    klines = fetch_klines_backwards("PAXG_USDT", interval, days)
    if not klines:
        print("No data.")
        return

    first = datetime.fromtimestamp(int(klines[0]["time"]) / 1000, tz=timezone.utc)
    last = datetime.fromtimestamp(int(klines[-1]["time"]) / 1000, tz=timezone.utc)
    span_days = (last - first).total_seconds() / 86400
    start_price = float(klines[0]["close"])
    print(f"{len(klines)} M5 candles | {first.date()} to {last.date()} | span {span_days:.2f}d")
    print(f"Initial price (sim start): ${start_price:.2f}")
    print(f"Grid: ${bottom}-${top} | {rows} rows | step ${step:.2f}")
    print()

    sells, buys = simulate_grid(klines, levels, step, start_price)

    print("REAL grid trades fired by bot simulation (price actually moved by >=1 step to trigger):")
    print()
    print("  Level     | SELLs | BUYs | TOTAL | per day | per week | per month")
    print("  ----------|-------|------|-------|---------|----------|----------")
    total = 0
    for L in levels:
        s, b = sells[L], buys[L]
        t = s + b
        total += t
        per_d = t / span_days
        print(f"  ${L:7.2f} | {s:5d} | {b:4d} | {t:5d} | {per_d:7.2f} | {per_d*7:8.2f} | {per_d*30:8.2f}")
    print("  ----------|-------|------|-------|---------|----------|----------")
    tot_s = sum(sells.values()); tot_b = sum(buys.values())
    per_d_tot = total / span_days
    print(f"  TOTAL     | {tot_s:5d} | {tot_b:4d} | {total:5d} | {per_d_tot:7.2f} | {per_d_tot*7:8.2f} | {per_d_tot*30:8.2f}")

    # Cycles: each pair of (SELL at L, BUY at L-step) or (BUY at L, SELL at L+step) = 1 cycle
    # Easier: min(total_sells, total_buys) gives matched cycles
    cycles = min(tot_s, tot_b)

    capital = 400.0
    capital_per_grid = capital / rows
    avg_price = (top + bottom) / 2
    fee_each = 0.0005
    gross_per_cycle = step / avg_price * capital_per_grid
    fees_per_cycle = 2 * fee_each * capital_per_grid
    net_per_cycle = gross_per_cycle - fees_per_cycle

    print()
    print("Profit math (real fires only, >=$20.83 swing required):")
    print(f"  Total trades:             {total}  ({tot_s} SELLs + {tot_b} BUYs)")
    print(f"  Matched cycles:           {cycles}")
    print(f"  Gross per cycle:          ${gross_per_cycle:.4f}  ({step/avg_price*100:.3f}% × ${capital_per_grid:.2f})")
    print(f"  Fees per cycle (2×0.05%): ${fees_per_cycle:.4f}")
    print(f"  Net per cycle:            ${net_per_cycle:.4f}")
    net_profit = cycles * net_per_cycle
    print(f"  NET profit over span:     ${net_profit:.2f}")
    print()
    print(f"  Per day:   ${net_profit/span_days:.2f}")
    print(f"  Per week:  ${net_profit/span_days*7:.2f}")
    print(f"  Per month: ${net_profit/span_days*30:.2f}")
    print(f"  APR:       {net_profit/span_days*365/capital*100:.1f}%")


if __name__ == "__main__":
    main()
