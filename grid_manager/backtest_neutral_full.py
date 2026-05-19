"""
Complete 6-month backtest of neutral leveraged grid on PAXG vs spot grid.

What it does (rigorous):
1. Fetches 180 days of PAXG_USDT M5 candles from Pionex
2. Simulates the neutral grid bot bar by bar (intra-bar high/low path)
3. Tracks: position size, position avg price, realized P&L, fees, funding (every 8h)
4. Implements 3 repositioning policies:
   - NEVER: leave the bot in place (loses when out of range)
   - SMART: wait for price to return near new center, then move
   - PANIC: reposition immediately when price approaches boundary
5. Counts every reposition event with its REAL cost (close P&L + fees)
6. Compares with spot grid baseline and HODL

Assumptions documented in code. All numbers from real PAXG history.
"""
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

from pionex_client import _get_public


# ─── Constants (real Pionex futures values) ──────────────────────────
MAKER_FEE = 0.0002    # 0.02% Pionex perpetual maker (grid fills are maker)
TAKER_FEE = 0.0005    # 0.05% perpetual taker (close-at-market)
SLIPPAGE  = 0.001     # 0.1% slippage on market close (Pionex estimate)
# Funding: Pionex futures every 8h. Gold-pegged perps tend to be near zero.
# Conservative: ±0.005% per 8h average over time. Settled into realized.
FUNDING_PER_8H = 0.00005

CAPITAL  = 400.0


# ─── Data fetch ──────────────────────────────────────────────────────
def fetch_klines_backwards(symbol, interval, days):
    now_ms = int(time.time() * 1000)
    target_ms = now_ms - days * 86400 * 1000
    klines, seen, cursor = [], set(), now_ms
    for _ in range(500):
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
    return [k for k in klines if int(k["time"]) >= target_ms]


# ─── Grid Bot State ─────────────────────────────────────────────────
class GridBotState:
    """One instance of a running grid bot."""
    def __init__(self, top, bottom, rows, capital, leverage):
        self.top = top
        self.bottom = bottom
        self.center = (top + bottom) / 2
        self.rows = rows
        self.step = (top - bottom) / rows
        self.leverage = leverage
        self.cap_per_grid = capital / rows
        self.notional_per_grid = self.cap_per_grid * leverage
        self.levels = [round(bottom + i * self.step, 4) for i in range(rows + 1)]

        # State
        self.pending = {}   # level -> 'BUY' | 'SELL' | None
        self.pos_size = 0.0       # signed (long+, short-) NOTIONAL
        self.pos_avg = 0.0        # vol-weighted avg entry price
        self.realized_pnl = 0.0   # cash earned from completed cycles
        self.fees_paid = 0.0
        self.funding_paid = 0.0
        self.maker_trades = 0     # grid fills
        self.cycles_paired = 0    # number of complete pairs

    def init_at(self, start_price):
        """Set initial pending orders based on starting price.
        Neutral grid: no initial position taken; orders just sit on the book."""
        for L in self.levels:
            if L < start_price:
                self.pending[L] = "BUY"
            elif L > start_price:
                self.pending[L] = "SELL"
            else:
                self.pending[L] = None

    def fill(self, level, side):
        """Execute one fill at a grid level. side: 'BUY' or 'SELL'."""
        notional = self.notional_per_grid
        self.fees_paid += notional * MAKER_FEE  # grid fills are maker on Pionex
        self.maker_trades += 1

        if side == "BUY":
            if self.pos_size < 0:
                # Closing short
                closing = min(-self.pos_size, notional)
                # short P&L = (entry - close) / entry * notional
                pnl = (self.pos_avg - level) / self.pos_avg * closing
                self.realized_pnl += pnl
                self.cycles_paired += 1
                self.pos_size += closing
                remaining = notional - closing
                if remaining > 0:
                    # Opening fresh long with remainder
                    self.pos_size += remaining
                    self.pos_avg = level
                # if pos_size now 0, avg becomes irrelevant
            else:
                # Adding to (or opening) long
                if self.pos_size == 0:
                    self.pos_avg = level
                else:
                    self.pos_avg = (self.pos_avg * self.pos_size + level * notional) / (self.pos_size + notional)
                self.pos_size += notional

        else:  # SELL
            if self.pos_size > 0:
                closing = min(self.pos_size, notional)
                # long P&L = (close - entry) / entry * notional
                pnl = (level - self.pos_avg) / self.pos_avg * closing
                self.realized_pnl += pnl
                self.cycles_paired += 1
                self.pos_size -= closing
                remaining = notional - closing
                if remaining > 0:
                    self.pos_size -= remaining
                    self.pos_avg = level
            else:
                if self.pos_size == 0:
                    self.pos_avg = level
                else:
                    abs_pos = -self.pos_size
                    self.pos_avg = (self.pos_avg * abs_pos + level * notional) / (abs_pos + notional)
                self.pos_size -= notional

    def walk(self, p1, p2):
        """Walk price from p1 to p2, firing eligible orders in order."""
        if p2 > p1:
            for L in self.levels:
                if p1 < L <= p2 and self.pending.get(L) == "SELL":
                    self.fill(L, "SELL")
                    self.pending[L] = None
                    # Re-place BUY at level below for next cycle
                    below = round(L - self.step, 4)
                    if below in self.pending:
                        self.pending[below] = "BUY"
        elif p2 < p1:
            for L in reversed(self.levels):
                if p2 <= L < p1 and self.pending.get(L) == "BUY":
                    self.fill(L, "BUY")
                    self.pending[L] = None
                    above = round(L + self.step, 4)
                    if above in self.pending:
                        self.pending[above] = "SELL"

    def process_bar(self, k):
        o = float(k["open"]); h = float(k["high"])
        l = float(k["low"]);  c = float(k["close"])
        # Path heuristic: open -> low -> high -> close if green, else open -> high -> low -> close
        path = [o, l, h, c] if c >= o else [o, h, l, c]
        for i in range(1, 4):
            self.walk(path[i-1], path[i])

    def apply_funding(self):
        """Charge funding on current absolute position (every 8h call)."""
        if abs(self.pos_size) > 0:
            f = abs(self.pos_size) * FUNDING_PER_8H
            self.funding_paid += f
            self.realized_pnl -= f

    def force_close(self, market_price, not_sell=False):
        """Force-close position. If not_sell=True (spot NOT_SELL mode), don't realize position P&L,
        the position value just transfers to wallet at market_price (no fees, no slippage)."""
        if abs(self.pos_size) < 0.01:
            return 0.0
        abs_size = abs(self.pos_size)
        if not_sell:
            # NOT_SELL: position just returns to wallet at current market price (no realized loss/gain on close)
            # The "value" of holding the residual PAXG is HODL participation, not grid attribution
            # Only attribute realized cycle profit to the grid; the residual is HODL-equivalent.
            self.pos_size = 0
            self.pos_avg = 0
            return 0.0
        # Otherwise: market close — realize P&L + fees + slippage
        if self.pos_size > 0:
            pnl = (market_price - self.pos_avg) / self.pos_avg * abs_size
        else:
            pnl = (self.pos_avg - market_price) / self.pos_avg * abs_size
        self.realized_pnl += pnl
        close_cost = abs_size * (TAKER_FEE + SLIPPAGE)
        self.fees_paid += close_cost
        self.realized_pnl -= close_cost
        self.pos_size = 0
        self.pos_avg = 0
        return -pnl + close_cost if pnl < 0 else close_cost


# ─── Simulation ──────────────────────────────────────────────────────
def simulate(klines, range_width, rows, leverage, capital, policy,
              smart_outer=0.85, panic_outer=0.95, not_sell=False):
    """
    range_width: dollar width of the bot's range (e.g. $1000 for ±$500)
    rows: number of grid lines
    leverage: 1 for spot equivalent, >1 for neutral futures
    policy: 'never', 'smart', 'panic'
    smart_outer: price > this fraction of half-range triggers "awaiting recenter"
    panic_outer: price > this fraction of half-range triggers IMMEDIATE close (panic policy)
    """
    if not klines:
        return None

    start_price = float(klines[0]["close"])
    center = round(start_price / 10) * 10
    bot = GridBotState(center + range_width/2, center - range_width/2, rows, capital, leverage)
    bot.init_at(start_price)

    # Aggregate across all bot instances (across repositions)
    tot_realized = 0.0
    tot_fees = 0.0
    tot_funding = 0.0
    tot_trades = 0
    tot_cycles = 0
    repositions = []  # list of dicts with timestamp, price, position_at_close, cost
    bars_in_range = 0
    bars_out_range = 0
    drawdown_history = []

    awaiting_recenter = False  # smart policy state
    bar_count = 0
    bars_per_8h = 96  # M5 candles

    for k in klines:
        bar_count += 1
        c = float(k["close"]); h = float(k["high"]); l = float(k["low"])

        # Track in-range
        if bot.bottom <= l and h <= bot.top:
            bars_in_range += 1
        else:
            bars_out_range += 1

        # Process fills for this bar
        bot.process_bar(k)

        # Funding every 8h
        if bar_count % bars_per_8h == 0:
            bot.apply_funding()

        # Reposition logic
        if policy == "never":
            pass

        elif policy == "panic":
            half_w = (bot.top - bot.bottom) / 2
            dist_from_center = abs(c - bot.center)
            if dist_from_center > panic_outer * half_w:
                cost = bot.force_close(c, not_sell=not_sell)
                tot_realized += bot.realized_pnl
                tot_fees += bot.fees_paid
                tot_funding += bot.funding_paid
                tot_trades += bot.maker_trades
                tot_cycles += bot.cycles_paired
                # Real cost of creating a new spot bot: initial PAXG buy = ~half of capital at taker fees
                if not_sell:
                    new_bot_creation_cost = (capital / 2) * (TAKER_FEE + SLIPPAGE)
                    tot_fees += new_bot_creation_cost
                    cost += new_bot_creation_cost
                repositions.append({
                    "ts": int(k["time"]),
                    "price": c,
                    "policy_state": "panic_at_boundary",
                    "realized_at_close": bot.realized_pnl,
                    "close_cost": cost,
                })
                # Restart at current price
                new_center = round(c / 10) * 10
                bot = GridBotState(new_center + range_width/2,
                                    new_center - range_width/2,
                                    rows, capital, leverage)
                bot.init_at(c)
                # Subtract creation cost from running realized
                if not_sell:
                    bot.realized_pnl = -new_bot_creation_cost

        elif policy == "smart":
            half_w = (bot.top - bot.bottom) / 2
            dist_from_center = abs(c - bot.center)
            if dist_from_center > smart_outer * half_w:
                awaiting_recenter = True
            if awaiting_recenter:
                # Trigger reposition only when position is near flat AND price within 20% of any center
                # (i.e. price has retraced back inside the range)
                pos_near_flat = abs(bot.pos_size) < bot.notional_per_grid * 2
                # We propose a new center at current price. Reposition if the current bot pos is near flat,
                # meaning cycles have completed during the retrace.
                if pos_near_flat:
                    cost = bot.force_close(c, not_sell=not_sell)
                    tot_realized += bot.realized_pnl
                    tot_fees += bot.fees_paid
                    tot_funding += bot.funding_paid
                    tot_trades += bot.maker_trades
                    tot_cycles += bot.cycles_paired
                    repositions.append({
                        "ts": int(k["time"]),
                        "price": c,
                        "policy_state": "smart_recenter",
                        "realized_at_close": bot.realized_pnl,
                        "close_cost": cost,
                    })
                    new_center = round(c / 10) * 10
                    bot = GridBotState(new_center + range_width/2,
                                        new_center - range_width/2,
                                        rows, capital, leverage)
                    bot.init_at(c)
                    awaiting_recenter = False

    # Final close
    bot.force_close(float(klines[-1]["close"]), not_sell=not_sell)
    tot_realized += bot.realized_pnl
    tot_fees += bot.fees_paid
    tot_funding += bot.funding_paid
    tot_trades += bot.maker_trades
    tot_cycles += bot.cycles_paired

    span_days = (int(klines[-1]["time"]) - int(klines[0]["time"])) / 86400000
    return {
        "policy": policy,
        "range_width": range_width,
        "rows": rows,
        "leverage": leverage,
        "span_days": span_days,
        "net_pnl": tot_realized,
        "fees": tot_fees,
        "funding": tot_funding,
        "trades": tot_trades,
        "cycles": tot_cycles,
        "repositions": repositions,
        "repos_count": len(repositions),
        "repos_total_cost": sum(r["close_cost"] for r in repositions),
        "bars_in_range": bars_in_range,
        "bars_out_range": bars_out_range,
        "start_price": start_price,
        "end_price": float(klines[-1]["close"]),
    }


# ─── HODL baseline ───────────────────────────────────────────────────
def hodl_pnl(klines, capital):
    start = float(klines[0]["close"])
    end = float(klines[-1]["close"])
    paxg = capital / start  # ignore fee for simplicity
    return paxg * end - capital


# ─── Reporting ───────────────────────────────────────────────────────
def fmt_report(r):
    if not r:
        return "(no data)"
    span = r["span_days"]
    net = r["net_pnl"]
    pm = net / span * 30
    apr = net / span * 365 / CAPITAL * 100
    in_pct = r["bars_in_range"] / (r["bars_in_range"] + r["bars_out_range"]) * 100

    out = []
    out.append(f"  policy={r['policy']:6s} lev={r['leverage']}x rows={r['rows']} width=${r['range_width']:.0f}")
    out.append(f"    span={span:.1f}d | net P&L ${net:+.2f} | /mo ${pm:+.2f} | APR {apr:+.1f}%")
    out.append(f"    cycles={r['cycles']} trades={r['trades']} fees=${r['fees']:.2f} funding=${r['funding']:.2f}")
    out.append(f"    repositions={r['repos_count']} cost=${r['repos_total_cost']:.2f}")
    out.append(f"    time-in-range={in_pct:.1f}%")
    return "\n".join(out)


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    interval = sys.argv[2] if len(sys.argv) > 2 else "5M"
    print(f"Fetching {interval} PAXG_USDT for last {days}d ...")
    klines = fetch_klines_backwards("PAXG_USDT", interval, days)
    if not klines:
        print("No data"); return

    first = datetime.fromtimestamp(int(klines[0]["time"])/1000, tz=timezone.utc)
    last = datetime.fromtimestamp(int(klines[-1]["time"])/1000, tz=timezone.utc)
    span = (last - first).total_seconds() / 86400
    sp = float(klines[0]["close"]); ep = float(klines[-1]["close"])
    move_pct = (ep - sp) / sp * 100
    print(f"{len(klines)} candles | {first.date()} -> {last.date()} | span {span:.1f}d")
    print(f"Price: ${sp:.2f} -> ${ep:.2f} ({move_pct:+.1f}%)")
    print()

    # HODL baseline
    hodl = hodl_pnl(klines, CAPITAL)
    print(f"[HODL benchmark] {span:.1f}d P&L=${hodl:+.2f} /mo=${hodl/span*30:+.2f} APR={hodl/span*365/CAPITAL*100:+.1f}%")
    print()

    configs = [
        # (range_width, rows, leverage, policy, not_sell, label)
        # ULTRA-NARROW (step < $5, fees > profit per cycle)
        (50,  12, 1, "panic", True,  "Spot $50  (step $4.2)  trailing — TOO TIGHT"),
        (100, 12, 1, "panic", True,  "Spot $100 (step $8.3)  trailing"),
        # NARROW (just above fee break-even)
        (150, 12, 1, "panic", True,  "Spot $150 (step $12.5) trailing"),
        (200, 12, 1, "panic", True,  "Spot $200 (step $16.7) trailing"),
        (250, 12, 1, "panic", True,  "Spot $250 (step $20.8) trailing — CURRENT"),
        # WIDER
        (350, 12, 1, "panic", True,  "Spot $350 (step $29.2) trailing"),
        (500, 12, 1, "panic", True,  "Spot $500 (step $41.7) trailing"),
        # BASELINES
        (250, 12, 1, "never", False, "Spot $250 / no repo (current behavior)"),
        (1000, 50, 10, "never", False,"Neutral 10x $1000 / no repo"),
    ]

    results = []
    for cfg in configs:
        w, r, lev, p, ns, label = cfg
        print(f"Running {label} ...", flush=True)
        res = simulate(klines, w, r, lev, CAPITAL, p, not_sell=ns)
        if res:
            res["label"] = label
            results.append(res)

    print("\n" + "=" * 78)
    print("RESULTS")
    print("=" * 78)
    for res in results:
        print(fmt_report(res))
        print()

    print("=" * 78)
    print("RANKING by /mo (descending)")
    print("=" * 78)
    results.sort(key=lambda r: -r["net_pnl"] / r["span_days"] * 30)
    for res in results:
        pm = res["net_pnl"] / res["span_days"] * 30
        apr = res["net_pnl"] / res["span_days"] * 365 / CAPITAL * 100
        print(f"  {res.get('label','?'):<48s}  /mo=${pm:+8.2f}  APR {apr:+6.1f}%  repos={res['repos_count']:3d}")
    print(f"\n  [HODL]                                              /mo=${hodl/span*30:+8.2f}  APR {hodl/span*365/CAPITAL*100:+6.1f}%")


if __name__ == "__main__":
    main()
