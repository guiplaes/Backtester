"""
Rigorous comparison: Grid bot vs DCA vs HODL vs Cash on the same PAXG history.
All same capital, same period, same fees. Realistic.
"""
import sys
import time
from datetime import datetime, timezone

from pionex_client import _get_public


CAPITAL = 400.0
FEE = 0.0005  # 0.05% per trade on Pionex spot


def fetch_klines(symbol, interval, days):
    now_ms = int(time.time() * 1000)
    target = now_ms - days * 86400 * 1000
    seen = set()
    out = []
    cursor = now_ms
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
        out.extend(new)
        oldest = min(int(k["time"]) for k in new)
        if oldest <= target:
            break
        cursor = oldest - 1
    out.sort(key=lambda k: int(k["time"]))
    return out


# ============ Strategy A: HODL ============
def hodl_strategy(klines):
    """Buy at first close with all capital. Hold to end. Sell at last close."""
    start_p = float(klines[0]["close"])
    end_p = float(klines[-1]["close"])
    paxg_bought = CAPITAL * (1 - FEE) / start_p  # buy fee
    final_value = paxg_bought * end_p * (1 - FEE)  # sell fee (or mark-to-market without selling)
    realized_if_sold = final_value - CAPITAL
    mark_to_market = paxg_bought * end_p - CAPITAL
    return {
        "name": "HODL",
        "paxg_held": paxg_bought,
        "buy_price": start_p,
        "current_price": end_p,
        "mark_to_market_pnl": mark_to_market,
        "if_sold_now_pnl": realized_if_sold,
        "trades": 1,
        "fees_paid": CAPITAL * FEE,
    }


# ============ Strategy B: DCA (weekly) ============
def dca_strategy(klines, period_days=7):
    """Buy CAPITAL/n at every period_days. Closes at end."""
    if not klines:
        return None
    span_ms = int(klines[-1]["time"]) - int(klines[0]["time"])
    span_days = span_ms / 86400000
    n_buys = max(1, int(span_days / period_days))
    chunk_usd = CAPITAL / n_buys

    paxg_total = 0
    fees_total = 0
    cost_basis = 0
    times = [int(k["time"]) for k in klines]
    closes = [float(k["close"]) for k in klines]
    t0 = times[0]
    for i in range(n_buys):
        target_t = t0 + i * period_days * 86400000
        # find closest kline
        idx = min(range(len(times)), key=lambda j: abs(times[j] - target_t))
        p = closes[idx]
        bought = chunk_usd * (1 - FEE) / p
        paxg_total += bought
        cost_basis += chunk_usd
        fees_total += chunk_usd * FEE

    end_p = closes[-1]
    mark_to_market = paxg_total * end_p - cost_basis
    avg_buy = cost_basis / paxg_total if paxg_total else 0
    return {
        "name": f"DCA (weekly, {n_buys} buys)",
        "paxg_held": paxg_total,
        "avg_buy_price": avg_buy,
        "current_price": end_p,
        "mark_to_market_pnl": mark_to_market,
        "if_sold_now_pnl": paxg_total * end_p * (1 - FEE) - cost_basis,
        "trades": n_buys,
        "fees_paid": fees_total,
    }


# ============ Strategy B2: Range-trigger DCA ============
def range_dca_strategy(klines, bottom=4600.0, top=4850.0, rows=12):
    """
    Same buy levels as the grid bot. Each time price crosses DOWN through a
    grid level, buy capital_per_grid USDT of PAXG. Never sell — pure accumulation.
    This is the fairest DCA comparison: same range, same trigger prices, same chunk size.
    """
    step = (top - bottom) / rows
    levels = [round(bottom + i * step, 4) for i in range(rows + 1)]
    capPerGrid = CAPITAL / rows
    max_buys = rows + 1  # one per level
    triggered = {L: False for L in levels}
    paxg = 0.0
    fees = 0.0
    cost = 0.0
    prev_close = float(klines[0]["close"])
    # Initial: any level below start price is already "above" (won't trigger). We trigger on DOWN crossings.
    for k in klines:
        c = float(k["close"])
        l = float(k["low"])
        h = float(k["high"])
        # Walk path: open-low-high-close. Look for DOWN moves crossing untriggered levels
        # Simpler: any level between low and prev_close that hasn't been bought yet -> buy
        for L in levels:
            if not triggered[L] and L <= prev_close and L >= l and L <= top:
                # price dipped to L on this bar -> buy
                bought = capPerGrid * (1 - FEE) / L
                paxg += bought
                cost += capPerGrid
                fees += capPerGrid * FEE
                triggered[L] = True
        prev_close = c

    end_p = float(klines[-1]["close"])
    mark = paxg * end_p - cost
    n = sum(1 for v in triggered.values() if v)
    avg = cost / paxg if paxg else 0
    return {
        "name": f"Range-DCA {bottom:.0f}-{top:.0f} ({rows}r, buy-on-dip)",
        "trades": n,
        "paxg_held": paxg,
        "cost_basis": cost,
        "avg_buy_price": avg,
        "fees_paid": fees,
        "mark_to_market_pnl": mark,
        "deployed_pct": cost / CAPITAL * 100,
    }


# ============ Strategy C: Grid bot ============
def grid_strategy(klines, bottom=4600.0, top=4850.0, rows=12):
    """Simulate exact spot-grid bot (high/low intra-bar walk)."""
    step = (top - bottom) / rows
    levels = [round(bottom + i * step, 4) for i in range(rows + 1)]
    capPerGrid = CAPITAL / rows

    if not klines:
        return None
    start_p = float(klines[0]["close"])

    pending = {}
    for L in levels:
        if L < start_p: pending[L] = "BUY"
        elif L > start_p: pending[L] = "SELL"
        else: pending[L] = None

    sells = {L: 0 for L in levels}
    buys = {L: 0 for L in levels}
    lev_asc = sorted(levels)
    lev_desc = sorted(levels, reverse=True)

    def fire_up(pf, pt):
        for L in lev_asc:
            if L > pf and L <= pt and pending[L] == "SELL":
                sells[L] += 1
                pending[L] = None
                below = round(L - step, 4)
                if below in pending:
                    pending[below] = "BUY"

    def fire_down(pf, pt):
        for L in lev_desc:
            if L < pf and L >= pt and pending[L] == "BUY":
                buys[L] += 1
                pending[L] = None
                above = round(L + step, 4)
                if above in pending:
                    pending[above] = "SELL"

    for k in klines:
        o = float(k["open"]); h = float(k["high"])
        l = float(k["low"]); c = float(k["close"])
        path = [o, l, h, c] if c >= o else [o, h, l, c]
        for i in range(1, 4):
            p1, p2 = path[i-1], path[i]
            if p2 > p1: fire_up(p1, p2)
            elif p2 < p1: fire_down(p1, p2)

    tot_s = sum(sells.values())
    tot_b = sum(buys.values())
    cycles = min(tot_s, tot_b)
    avg_p = (top + bottom) / 2
    gross = cycles * (step / avg_p) * capPerGrid
    fees = (tot_s + tot_b) * FEE * capPerGrid
    net_realized = gross - fees

    # Mark-to-market: the bot still holds inventory.
    # Approx: after all moves, the bot has roughly the initial inventory still.
    # Exact tracking would need to maintain PAXG/USDT balances. Realistic estimate:
    # the bot maintains approx half its capital in PAXG always (this depends on price position).
    end_p = float(klines[-1]["close"])
    # Net realized profit is cash. Bot value drifts with PAXG price relative to avg cost.
    # For simplicity assume bot's holdings remain at break-even with avg cost = mid range.
    # So MTM = net_realized + (drift impact, ~0 if price near mid).
    return {
        "name": f"Grid Bot {bottom:.0f}-{top:.0f} ({rows}r)",
        "trades": tot_s + tot_b,
        "cycles": cycles,
        "gross_cycles_pnl": gross,
        "fees_paid": fees,
        "realized_pnl": net_realized,
        "start_price": start_p,
        "end_price": end_p,
        "out_of_range_bars": sum(1 for k in klines if float(k["low"]) > top or float(k["high"]) < bottom),
    }


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    interval = sys.argv[2] if len(sys.argv) > 2 else "5M"
    print(f"Fetching {interval} PAXG_USDT for last {days}d ...")
    klines = fetch_klines("PAXG_USDT", interval, days)
    if not klines:
        print("No data")
        return
    # Trim to requested period (API often returns more than requested)
    cutoff = int(time.time() * 1000) - days * 86400000
    klines = [k for k in klines if int(k["time"]) >= cutoff]
    first = datetime.fromtimestamp(int(klines[0]["time"])/1000, tz=timezone.utc)
    last = datetime.fromtimestamp(int(klines[-1]["time"])/1000, tz=timezone.utc)
    span = (last - first).total_seconds() / 86400
    start_p = float(klines[0]["close"])
    end_p = float(klines[-1]["close"])
    move_pct = (end_p - start_p) / start_p * 100
    print(f"Span: {span:.1f}d | start ${start_p:.2f} -> end ${end_p:.2f} ({move_pct:+.1f}%)")
    print()

    h = hodl_strategy(klines)
    d = dca_strategy(klines)
    g = grid_strategy(klines)
    rd = range_dca_strategy(klines)

    print("=" * 70)
    print(f"Capital: ${CAPITAL:.0f} | Fee: {FEE*100:.2f}% per trade | Period: {span:.1f}d")
    print("=" * 70)

    def line(label, val):
        print(f"  {label:30s} {val}")

    # Convert to per-month for apples-to-apples
    def per_month(pnl): return pnl / span * 30 if span else 0
    def apr(pnl): return pnl / span * 365 / CAPITAL * 100 if span else 0

    print("\n[A] HODL  - buy at start, hold to end")
    line("Trades", h["trades"])
    line("PAXG held", f"{h['paxg_held']:.6f}")
    line("Fees paid", f"${h['fees_paid']:.4f}")
    line("Mark-to-market P&L", f"${h['mark_to_market_pnl']:+.2f}")
    line("If sold now (net of fees)", f"${h['if_sold_now_pnl']:+.2f}")
    line("Per month equivalent", f"${per_month(h['mark_to_market_pnl']):+.2f}")
    line("APR equivalent", f"{apr(h['mark_to_market_pnl']):+.1f}%")

    print("\n[B] DCA   - weekly buys, hold to end")
    line("Trades", d["trades"])
    line("PAXG accumulated", f"{d['paxg_held']:.6f}")
    line("Avg buy price", f"${d['avg_buy_price']:.2f}")
    line("Fees paid", f"${d['fees_paid']:.4f}")
    line("Mark-to-market P&L", f"${d['mark_to_market_pnl']:+.2f}")
    line("Per month equivalent", f"${per_month(d['mark_to_market_pnl']):+.2f}")
    line("APR equivalent", f"{apr(d['mark_to_market_pnl']):+.1f}%")

    print(f"\n[C] GRID  - ${4600}-${4850}, 12 rows, current Pionex bot")
    line("Trades", g["trades"])
    line("Cycles paired", g["cycles"])
    line("Fees paid", f"${g['fees_paid']:.4f}")
    line("Realized cash P&L", f"${g['realized_pnl']:+.2f}")
    line("Out-of-range bars", f"{g['out_of_range_bars']}/{len(klines)} ({g['out_of_range_bars']/len(klines)*100:.1f}%)")
    line("Per month equivalent", f"${per_month(g['realized_pnl']):+.2f}")
    line("APR equivalent", f"{apr(g['realized_pnl']):+.1f}%")

    print(f"\n[D] RANGE-DCA - buy {CAPITAL/12:.2f} USDT at each grid level on dip, never sell")
    line("Triggers fired", rd["trades"])
    line("PAXG accumulated", f"{rd['paxg_held']:.6f}")
    line("Cost basis deployed", f"${rd['cost_basis']:.2f} ({rd['deployed_pct']:.0f}% of capital)")
    line("Avg buy price", f"${rd['avg_buy_price']:.2f}" if rd['avg_buy_price'] else "n/a")
    line("Fees paid", f"${rd['fees_paid']:.4f}")
    line("Mark-to-market P&L", f"${rd['mark_to_market_pnl']:+.2f}")
    line("Per month equivalent", f"${per_month(rd['mark_to_market_pnl']):+.2f}")
    line("APR equivalent", f"{apr(rd['mark_to_market_pnl']):+.1f}%")

    print("\n" + "=" * 70)
    print("WINNER per month / APR:")
    rows_data = [
        ("HODL", h["mark_to_market_pnl"]),
        ("DCA (weekly any price)", d["mark_to_market_pnl"]),
        ("RANGE-DCA (buy on dip)", rd["mark_to_market_pnl"]),
        ("GRID (realized cash)", g["realized_pnl"]),
    ]
    rows_data.sort(key=lambda x: -x[1])
    for name, p in rows_data:
        print(f"  {name:25s} ${per_month(p):+.2f}/mo  APR {apr(p):+.1f}%")
    print()
    print("Note: HODL/DCA P&L is UNREALIZED (mark-to-market) — only real when you sell.")
    print("      Grid P&L is REALIZED cash (USDT extracted from cycles).")


if __name__ == "__main__":
    main()
