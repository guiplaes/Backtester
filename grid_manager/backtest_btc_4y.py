"""
BTC spot grid trailing backtest over ~5 years (1 full cycle).
Width is % of current price (adaptive), so step% stays constant across the cycle.
"""
import sys
import time
from datetime import datetime, timezone
from pionex_client import _get_public

CAPITAL = 400.0
ROWS = 12
WIDTH_PCT = 0.03    # 3% of current price (range = ±1.5% from center)
STEP_PCT = WIDTH_PCT / ROWS  # ~0.25% per cycle step
TAKER_FEE = 0.0005
MAKER_FEE = 0.0002
SLIPPAGE = 0.001
TRIGGER_PCT = 0.10


def fetch_klines(symbol, interval, years):
    now_ms = int(time.time() * 1000)
    target_ms = now_ms - years * 365 * 86400 * 1000
    klines, seen, cursor = [], set(), now_ms
    for _ in range(50):
        r = _get_public("/api/v1/market/klines", {
            "symbol": symbol, "interval": interval, "limit": 500, "endTime": cursor
        })
        chunk = r.get("data", {}).get("klines", [])
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
    return [k for k in klines if int(k["time"]) >= target_ms]


def simulate_trailing_pct(klines):
    """Simulate spot trailing grid with percentage-based width.
    Width always = WIDTH_PCT of CURRENT BOT CENTER (not current price)."""
    if not klines:
        return None

    start_p = float(klines[0]["close"])
    center = start_p
    width = center * WIDTH_PCT
    step = width / ROWS

    pending = {}
    levels = [center - width/2 + i * step for i in range(ROWS + 1)]
    levels = [round(L, 2) for L in levels]
    for L in levels:
        if L < start_p:
            pending[L] = "BUY"
        elif L > start_p:
            pending[L] = "SELL"
        else:
            pending[L] = None

    pos_size = 0.0
    pos_avg = 0.0
    realized_cash = 0.0  # the GRID PROFIT (what we want to measure)
    fees_paid = 0.0
    repositions = 0
    total_fills = 0
    bars_in_range = 0
    cap_per_grid = CAPITAL / ROWS

    def fire(level, side):
        nonlocal pos_size, pos_avg, realized_cash, fees_paid, total_fills
        notional = cap_per_grid  # 1x spot, no leverage
        fees_paid += notional * MAKER_FEE
        total_fills += 1
        if side == "BUY":
            if pos_size < 0:
                closing = min(-pos_size, notional)
                realized_cash += (pos_avg - level) / pos_avg * closing
                pos_size += closing
                rem = notional - closing
                if rem > 0:
                    pos_size += rem
                    pos_avg = level
            else:
                if pos_size == 0:
                    pos_avg = level
                else:
                    pos_avg = (pos_avg * pos_size + level * notional) / (pos_size + notional)
                pos_size += notional
        else:  # SELL
            if pos_size > 0:
                closing = min(pos_size, notional)
                realized_cash += (level - pos_avg) / pos_avg * closing
                pos_size -= closing
                rem = notional - closing
                if rem > 0:
                    pos_size -= rem
                    pos_avg = level
            else:
                if pos_size == 0:
                    pos_avg = level
                else:
                    abs_pos = -pos_size
                    pos_avg = (pos_avg * abs_pos + level * notional) / (abs_pos + notional)
                pos_size -= notional

    def reposition(new_price):
        nonlocal center, width, step, levels, pending, repositions, fees_paid
        center = new_price
        width = center * WIDTH_PCT
        step = width / ROWS
        levels = [round(center - width/2 + i * step, 2) for i in range(ROWS + 1)]
        # adjust_params equivalent: just reset pending. Inventory carries over.
        # Tiny rebalance fee (representing the small market BUY/SELL to rebalance)
        rebalance_cost = CAPITAL * 0.0005  # 0.05% of capital
        fees_paid += rebalance_cost
        pending = {}
        for L in levels:
            if L < new_price:
                pending[L] = "BUY"
            elif L > new_price:
                pending[L] = "SELL"
            else:
                pending[L] = None
        repositions += 1

    for k in klines:
        o = float(k["open"]); h = float(k["high"])
        l = float(k["low"]);  c = float(k["close"])

        # Trailing check
        bot_top = center + width/2
        bot_bottom = center - width/2
        half_w = width / 2
        dist_top = (bot_top - c) / width
        dist_bot = (c - bot_bottom) / width
        if c > bot_top or c < bot_bottom or dist_top < TRIGGER_PCT or dist_bot < TRIGGER_PCT:
            reposition(c)
            continue  # skip fills this bar; new range is fresh

        if bot_bottom <= l and h <= bot_top:
            bars_in_range += 1

        # Process fills with intra-bar path
        path = [o, l, h, c] if c >= o else [o, h, l, c]
        for i in range(1, 4):
            p1, p2 = path[i-1], path[i]
            if p2 > p1:
                for L in sorted(levels):
                    if p1 < L <= p2 and pending.get(L) == "SELL":
                        fire(L, "SELL")
                        pending[L] = None
                        below = L - step
                        # find nearest level
                        for L2 in levels:
                            if abs(L2 - below) < 0.01:
                                pending[L2] = "BUY"
                                break
            elif p2 < p1:
                for L in sorted(levels, reverse=True):
                    if p2 <= L < p1 and pending.get(L) == "BUY":
                        fire(L, "BUY")
                        pending[L] = None
                        above = L + step
                        for L2 in levels:
                            if abs(L2 - above) < 0.01:
                                pending[L2] = "SELL"
                                break

    span_days = (int(klines[-1]["time"]) - int(klines[0]["time"])) / 86400000

    end_price = float(klines[-1]["close"])
    btc_inventory = pos_size / pos_avg if pos_avg > 0 else 0  # rough
    inventory_value = abs(pos_size) if pos_avg == 0 else abs(pos_size) * end_price / pos_avg

    return {
        "span_days": span_days,
        "fills": total_fills,
        "repositions": repositions,
        "realized_cash": realized_cash,
        "fees_paid": fees_paid,
        "net_cash": realized_cash - fees_paid,
        "start_price": float(klines[0]["close"]),
        "end_price": end_price,
        "final_pos_notional": pos_size,
        "final_pos_avg": pos_avg,
        "final_inventory_value": inventory_value,
    }


def main():
    years = float(sys.argv[1]) if len(sys.argv) > 1 else 5.5
    print(f"Fetching BTC_USDT 1D for last {years}y ...")
    klines = fetch_klines("BTC_USDT", "1D", years)
    print(f"Got {len(klines)} candles, {(int(klines[-1]['time']) - int(klines[0]['time'])) / 86400000:.0f} days span")
    print(f"BTC: ${float(klines[0]['close']):,.0f} -> ${float(klines[-1]['close']):,.0f}")
    print(f"Config: range = {WIDTH_PCT*100:.1f}% of price, step = {STEP_PCT*100:.3f}%, trigger trailing at {TRIGGER_PCT*100:.0f}% from edge")
    print()

    r = simulate_trailing_pct(klines)
    span = r["span_days"]
    yrs = span / 365
    print(f"=== Results over {yrs:.2f}y ({span:.0f} days) ===")
    print(f"  Total fills:         {r['fills']}")
    print(f"  Repositions:         {r['repositions']}")
    print(f"  Realized grid cash:  ${r['realized_cash']:+.2f}")
    print(f"  Fees paid:           ${r['fees_paid']:.2f}")
    print(f"  NET grid cash:       ${r['net_cash']:+.2f}")
    print()
    pct_of_capital = r['net_cash'] / CAPITAL * 100
    print(f"  As % of $400 capital: {pct_of_capital:+.1f}%")
    print(f"  APR:                  {pct_of_capital / yrs:+.1f}%")
    print(f"  /mo:                  ${r['net_cash'] / yrs / 12:+.2f}")

    # Compare with HODL
    hodl_pnl = CAPITAL * (r['end_price'] / r['start_price']) - CAPITAL
    print()
    print()
    print(f"  Final BTC position (notional):  ${r['final_pos_notional']:+.2f} at avg ${r['final_pos_avg']:.2f}")
    print(f"  Final BTC value (MTM):          ${r['final_inventory_value']:.2f}")
    print(f"  TOTAL final value: cash ${r['net_cash']:.2f} + BTC ${r['final_inventory_value']:.2f} = ${r['net_cash'] + r['final_inventory_value']:.2f}")
    print()
    print(f"  HODL benchmark:  $400 → ${CAPITAL + hodl_pnl:.2f}  (${hodl_pnl:+.2f}, +{hodl_pnl/CAPITAL*100:.0f}%)")


if __name__ == "__main__":
    main()
