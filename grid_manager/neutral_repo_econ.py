"""
Economics test: can a leveraged neutral grid on GOLD generate enough profit
to offset repositioning costs?

Simulates the neutral 3x grid on PAXG with two repositioning policies:
A) DUMB: reposition when price hits boundary (panic, full direction loss)
B) SMART: reposition when price comes back to the new center (cost ≈ $0)
"""
import sys
import time
from datetime import datetime, timezone

from pionex_client import _get_public

CAPITAL = 400.0
LEVERAGE = 3
RANGE_WIDTH = 250.0  # $4600-$4850 width
ROWS = 12
MAKER_FEE = 0.0002    # 0.02% Pionex futures maker
TAKER_FEE = 0.0005    # 0.05% Pionex futures taker (market close)
SLIPPAGE = 0.001      # 0.1% slippage on market close
FUNDING_PER_8H = 0.0001  # 0.01% typical, applied to notional


def fetch_klines(symbol, interval, days):
    now_ms = int(time.time() * 1000)
    target = now_ms - days * 86400 * 1000
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
        if oldest <= target:
            break
        cursor = oldest - 1
    klines.sort(key=lambda k: int(k["time"]))
    cutoff = now_ms - days * 86400 * 1000
    return [k for k in klines if int(k["time"]) >= cutoff]


class NeutralGridSim:
    """Simulate one cycle of neutral grid: place orders, track fills, compute P&L on close."""

    def __init__(self, top, bottom, rows, capital, leverage):
        self.top = top
        self.bottom = bottom
        self.rows = rows
        self.step = (top - bottom) / rows
        self.cap_per_grid = capital / rows
        self.notional_per_grid = self.cap_per_grid * leverage
        self.center = (top + bottom) / 2
        self.levels = [round(bottom + i * self.step, 4) for i in range(rows + 1)]

    def init(self, start_price):
        """Set initial pending orders based on starting price."""
        self.pending = {}
        for L in self.levels:
            if L < start_price:
                self.pending[L] = "BUY"   # ready to BUY when price drops
            elif L > start_price:
                self.pending[L] = "SELL"  # ready to SELL when price rises
            else:
                self.pending[L] = None
        self.position_size = 0.0   # +long, -short in units of notional
        self.position_avg_price = 0.0
        self.realized_pnl = 0.0
        self.fees = 0.0
        self.trades = 0

    def fire_at(self, level, direction):
        """direction: 'SELL' or 'BUY'. Updates position and realized PnL."""
        notional = self.notional_per_grid
        self.fees += notional * MAKER_FEE
        self.trades += 1

        # Update position-weighted average
        if direction == "SELL":
            # Reduce long position or increase short
            new_size = self.position_size - notional
            if self.position_size > 0:  # closing long
                # realized = (level - avg) * size
                closed = min(self.position_size, notional)
                self.realized_pnl += (level - self.position_avg_price) * (closed / self.position_avg_price)
                remaining = notional - closed
                if remaining > 0:
                    # opening short with remainder
                    self.position_avg_price = level
                self.position_size -= closed
                if remaining > 0:
                    self.position_size -= remaining
                    self.position_avg_price = level
            else:  # adding to short (or starting short)
                if self.position_size == 0:
                    self.position_avg_price = level
                else:
                    old_size = abs(self.position_size)
                    self.position_avg_price = (
                        (self.position_avg_price * old_size + level * notional) / (old_size + notional)
                    )
                self.position_size -= notional
        else:  # BUY
            if self.position_size < 0:  # closing short
                closed = min(-self.position_size, notional)
                self.realized_pnl += (self.position_avg_price - level) * (closed / self.position_avg_price)
                remaining = notional - closed
                self.position_size += closed
                if remaining > 0:
                    self.position_size += remaining
                    self.position_avg_price = level
            else:  # adding to long
                if self.position_size == 0:
                    self.position_avg_price = level
                else:
                    old_size = abs(self.position_size)
                    self.position_avg_price = (
                        (self.position_avg_price * old_size + level * notional) / (old_size + notional)
                    )
                self.position_size += notional

    def walk_path(self, p1, p2):
        """Walk price from p1 to p2, firing eligible pending orders."""
        if p2 > p1:
            for L in sorted(self.levels):
                if L > p1 and L <= p2 and self.pending.get(L) == "SELL":
                    self.fire_at(L, "SELL")
                    self.pending[L] = None
                    below = round(L - self.step, 4)
                    if below in self.pending:
                        self.pending[below] = "BUY"
        elif p2 < p1:
            for L in sorted(self.levels, reverse=True):
                if L < p1 and L >= p2 and self.pending.get(L) == "BUY":
                    self.fire_at(L, "BUY")
                    self.pending[L] = None
                    above = round(L + self.step, 4)
                    if above in self.pending:
                        self.pending[above] = "SELL"

    def process_bar(self, k):
        o, h, l, c = (float(k["open"]), float(k["high"]),
                     float(k["low"]), float(k["close"]))
        path = [o, l, h, c] if c >= o else [o, h, l, c]
        for i in range(1, 4):
            self.walk_path(path[i - 1], path[i])

    def close(self, market_price):
        """Force-close any open position at market_price."""
        cost = 0.0
        if abs(self.position_size) > 0:
            notional = abs(self.position_size)
            # Realize the unrealized P&L of position
            if self.position_size > 0:  # long
                pnl = (market_price - self.position_avg_price) * (notional / self.position_avg_price)
            else:  # short
                pnl = (self.position_avg_price - market_price) * (notional / self.position_avg_price)
            self.realized_pnl += pnl
            # Fees + slippage on market close
            cost = notional * (TAKER_FEE + SLIPPAGE)
            self.fees += cost
            self.position_size = 0
            self.position_avg_price = 0
        return cost


def simulate_with_reposition(klines, policy="smart", trigger_pct=0.45, range_width=RANGE_WIDTH):
    """
    Run neutral grid with periodic repositioning.

    policy:
      'never'  - never reposition
      'dumb'   - reposition immediately when price hits boundary (worst case)
      'smart'  - reposition only when price returns to center after going to boundary
    trigger_pct: how close to boundary (0.45 = within 45% of half-range from center) triggers boundary alert
    """
    if not klines:
        return None

    start_p = float(klines[0]["close"])
    center = round(start_p / 5) * 5
    bottom = center - range_width / 2
    top = center + range_width / 2

    bot = NeutralGridSim(top, bottom, ROWS, CAPITAL, LEVERAGE)
    bot.init(start_p)

    total_realized = 0
    total_fees = 0
    total_funding = 0
    total_trades = 0
    reposition_count = 0
    reposition_cost = 0
    bars_in_range = 0
    bars_out_range = 0

    awaiting_recenter = False  # for 'smart' policy
    bars_per_funding = 8 * 12  # 96 M5 bars per 8h
    bar_count = 0

    for k in klines:
        bar_count += 1
        c = float(k["close"])
        h = float(k["high"])
        l = float(k["low"])

        # Process bar fills
        bot.process_bar(k)

        # Funding charge every 96 bars (~8h on M5)
        if bar_count % bars_per_funding == 0 and abs(bot.position_size) > 0:
            f = abs(bot.position_size) * FUNDING_PER_8H
            total_funding += f
            bot.realized_pnl -= f  # funding is a cost

        # Range tracking
        if bottom <= l and h <= top:
            bars_in_range += 1
        else:
            bars_out_range += 1

        # Reposition logic
        if policy == "never":
            pass
        elif policy == "dumb":
            # Reposition the moment price approaches boundary
            dist_to_top = (top - c) / range_width
            dist_to_bot = (c - bottom) / range_width
            if dist_to_top < (1 - trigger_pct) or dist_to_bot < (1 - trigger_pct):
                # Close and reset around current price
                cost = bot.close(c)
                reposition_cost += cost
                reposition_count += 1
                total_realized += bot.realized_pnl
                total_fees += bot.fees
                total_trades += bot.trades
                # Restart at current price as new center
                new_center = round(c / 5) * 5
                new_bottom = new_center - range_width / 2
                new_top = new_center + range_width / 2
                bot = NeutralGridSim(new_top, new_bottom, ROWS, CAPITAL, LEVERAGE)
                bot.init(c)
                bottom, top = new_bottom, new_top
        elif policy == "smart":
            # Mark for reposition when price gets too far from center
            mid = (top + bottom) / 2
            off_center_pct = abs(c - mid) / (range_width / 2)
            if off_center_pct > trigger_pct:
                awaiting_recenter = True

            # If awaiting and price now within 10% of center, reposition
            if awaiting_recenter:
                # Reposition target = mid + small offset to follow trend
                new_mid = c  # follow price; price is near center
                new_off = abs(c - new_mid) / (range_width / 2)
                # Actually for SMART we close ONLY when current bot position is near flat:
                pos_near_flat = abs(bot.position_size) < bot.notional_per_grid * 1.5
                if pos_near_flat:
                    cost = bot.close(c)
                    reposition_cost += cost
                    reposition_count += 1
                    total_realized += bot.realized_pnl
                    total_fees += bot.fees
                    total_trades += bot.trades
                    new_center = round(c / 5) * 5
                    new_bottom = new_center - range_width / 2
                    new_top = new_center + range_width / 2
                    bot = NeutralGridSim(new_top, new_bottom, ROWS, CAPITAL, LEVERAGE)
                    bot.init(c)
                    bottom, top = new_bottom, new_top
                    awaiting_recenter = False

    # Final close to realize remaining position
    final_price = float(klines[-1]["close"])
    bot.close(final_price)
    total_realized += bot.realized_pnl
    total_fees += bot.fees
    total_trades += bot.trades

    span = (int(klines[-1]["time"]) - int(klines[0]["time"])) / 86400000
    return {
        "policy": policy,
        "span_days": span,
        "trades": total_trades,
        "realized_pnl": total_realized,
        "fees": total_fees,
        "funding": total_funding,
        "reposition_count": reposition_count,
        "reposition_cost": reposition_cost,
        "bars_in_range": bars_in_range,
        "bars_out_range": bars_out_range,
        "final_price": final_price,
    }


def report(name, r):
    if not r:
        return
    span = r["span_days"]
    net = r["realized_pnl"]  # already net of fees + funding (deducted in sim)
    per_mo = net / span * 30
    apr = net / span * 365 / CAPITAL * 100
    print(f"\n[{name}]")
    print(f"  Period:              {span:.1f}d")
    print(f"  Total trades:        {r['trades']}")
    print(f"  Realized P&L:        ${net:+.2f}")
    print(f"  Fees paid:           ${r['fees']:.2f}")
    print(f"  Funding paid:        ${r['funding']:.2f}")
    print(f"  Repositions:         {r['reposition_count']}")
    print(f"  Reposition cost:     ${r['reposition_cost']:.2f}")
    print(f"  Per month:           ${per_mo:+.2f}")
    print(f"  APR equivalent:      {apr:+.1f}%")


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    interval = sys.argv[2] if len(sys.argv) > 2 else "5M"
    print(f"Fetching {interval} PAXG_USDT for last {days}d (leverage {LEVERAGE}x, range ${RANGE_WIDTH:.0f})...")
    klines = fetch_klines("PAXG_USDT", interval, days)
    if not klines:
        print("No data"); return

    print(f"{len(klines)} candles | {datetime.fromtimestamp(int(klines[0]['time'])/1000, tz=timezone.utc).date()} to {datetime.fromtimestamp(int(klines[-1]['time'])/1000, tz=timezone.utc).date()}")
    print(f"Start ${float(klines[0]['close']):.2f} -> End ${float(klines[-1]['close']):.2f}")

    for policy in ["never", "smart", "dumb"]:
        r = simulate_with_reposition(klines, policy=policy)
        report(f"Neutral 3x ({policy} reposition)", r)


if __name__ == "__main__":
    main()
