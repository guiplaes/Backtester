"""
4-asset portfolio (PAXG + BTC + ETH + SOL) with grid trailing + REBALANCING.
M1 candles real. Pure cycle profit only.
Rebalances weekly to target weights using accumulated cash.
"""
import sys
import time
from datetime import datetime, timezone
from pionex_client import _get_public

MAKER_FEE = 0.0002
TAKER_FEE = 0.0005
SLIPPAGE  = 0.001
ROWS      = 12
TRIGGER_PCT = 0.05

WIDTH_PCT = {
    "PAXG_USDT": 0.025,
    "BTC_USDT":  0.060,
    "ETH_USDT":  0.080,
    "SOL_USDT":  0.100,
}

REBAL_THRESHOLD = 0.05  # rebalance if any asset weight deviates >5% from target
REBAL_FEE = 0.0005      # 0.05% per rebalance transfer


def fetch_m1(symbol, max_days=7):
    now_ms = int(time.time() * 1000)
    target = now_ms - max_days * 86400 * 1000
    seen, klines, cursor = set(), [], now_ms
    for _ in range(30):
        r = _get_public("/api/v1/market/klines", {
            "symbol": symbol, "interval": "1M", "limit": 500, "endTime": cursor
        })
        chunk = r.get("data", {}).get("klines", [])
        new = [k for k in chunk if int(k["time"]) not in seen]
        if not new: break
        for k in new:
            seen.add(int(k["time"]))
        klines.extend(new)
        oldest = min(int(k["time"]) for k in new)
        if oldest <= target: break
        cursor = oldest - 1
    klines.sort(key=lambda k: int(k["time"]))
    return [k for k in klines if int(k["time"]) >= target]


class Bot:
    def __init__(self, symbol, capital, width_pct):
        self.symbol = symbol
        self.capital = capital
        self.width_pct = width_pct
        self.rows = ROWS
        self.cap_per_grid = capital / self.rows
        self.cash = 0.0          # USDT extracted (cycle profit)
        self.fees_paid = 0.0
        self.fills = 0
        self.cycles = 0
        self.repositions = 0
        self.btc_eq_value = capital  # mark-to-market value of bot's position

    def init(self, price):
        self.price = price
        self.center = price
        self.width = price * self.width_pct
        self.step = self.width / self.rows
        self.levels = [round(self.center - self.width/2 + i * self.step, 4)
                       for i in range(self.rows + 1)]
        self.pending = {L: ("BUY" if L < price else "SELL" if L > price else None)
                        for L in self.levels}
        self.last_side = {L: None for L in self.levels}

    def fire(self, level, side):
        self.fees_paid += self.cap_per_grid * MAKER_FEE
        self.fills += 1
        prev = self.last_side.get(level)
        if prev and prev != side:
            self.cycles += 1
            # pure cycle profit = step * cap_per_grid / price
            self.cash += self.step / level * self.cap_per_grid
        self.last_side[level] = side

    def walk(self, p1, p2):
        if p2 > p1:
            for L in self.levels:
                if p1 < L <= p2 and self.pending.get(L) == "SELL":
                    self.fire(L, "SELL")
                    self.pending[L] = None
                    below = round(L - self.step, 4)
                    if below in self.pending: self.pending[below] = "BUY"
        elif p2 < p1:
            for L in reversed(self.levels):
                if p2 <= L < p1 and self.pending.get(L) == "BUY":
                    self.fire(L, "BUY")
                    self.pending[L] = None
                    above = round(L + self.step, 4)
                    if above in self.pending: self.pending[above] = "SELL"

    def process_bar(self, k):
        o, h, l, c = float(k["open"]), float(k["high"]), float(k["low"]), float(k["close"])
        path = [o, l, h, c] if c >= o else [o, h, l, c]
        for i in range(1, 4):
            self.walk(path[i-1], path[i])
        # Check trail
        top, bottom = self.center + self.width/2, self.center - self.width/2
        dt = (top - c) / self.width
        db = (c - bottom) / self.width
        if c > top or c < bottom or dt < TRIGGER_PCT or db < TRIGGER_PCT:
            self.fees_paid += self.capital * 0.0003
            prev_last = dict(self.last_side)
            self.init(c)
            for L, s in prev_last.items():
                if L in self.last_side: self.last_side[L] = s
            self.repositions += 1
        self.price = c
        # MTM value: capital tracks the price (assumes ~50/50 inventory always)
        # Simplification: bot value oscillates with price relative to last reposition
        # Real PAXG/BTC/ETH/SOL appreciation is tracked separately at portfolio level
        self.btc_eq_value = self.capital + self.cash - self.fees_paid

    def adjust_capital(self, delta):
        """Add/remove capital. Positive delta = add, negative = remove."""
        if delta == 0:
            return 0.0
        # Pionex would charge ~0.05% for the rebalance transfer (BUY/SELL spot)
        fee = abs(delta) * REBAL_FEE
        self.fees_paid += fee
        self.capital += delta
        self.cap_per_grid = self.capital / self.rows
        # Re-init to update grid levels with new capital sizing
        self.init(self.price)
        return fee


def run_portfolio(symbol_data, allocations, target_weights, total_capital, max_days, rebal_period_bars):
    """
    symbol_data: {symbol: [klines]}
    allocations: {symbol: dollar_amount} initial
    target_weights: {symbol: pct} for rebalancing
    rebal_period_bars: how many bars between rebalance checks (e.g., 1440 = 1 day in M1)
    """
    bots = {}
    for sym, cap in allocations.items():
        if sym not in symbol_data or not symbol_data[sym]:
            continue
        bots[sym] = Bot(sym, cap, WIDTH_PCT.get(sym, 0.03))
        bots[sym].init(float(symbol_data[sym][0]["close"]))

    # All bars by timestamp (assume aligned)
    # For simplicity, iterate bar-by-bar per asset independently
    # Then check rebalance at intervals
    max_bars = min(len(klines) for klines in symbol_data.values() if klines)
    rebalances = 0
    total_rebal_fees = 0.0

    for bar_idx in range(max_bars):
        for sym in bots:
            klines = symbol_data[sym]
            if bar_idx < len(klines):
                bots[sym].process_bar(klines[bar_idx])

        # Rebalance check every N bars
        if (bar_idx + 1) % rebal_period_bars == 0:
            # Compute current capital allocations
            total_bot_capital = sum(b.capital for b in bots.values())
            current_weights = {sym: b.capital / total_bot_capital for sym, b in bots.items()}
            max_dev = max(abs(current_weights[s] - target_weights[s]) for s in bots)
            if max_dev > REBAL_THRESHOLD:
                # Rebalance: move capital from overweight to underweight
                for sym in bots:
                    target_cap = target_weights[sym] * total_bot_capital
                    delta = target_cap - bots[sym].capital
                    fee = bots[sym].adjust_capital(delta)
                    total_rebal_fees += fee
                rebalances += 1

    return bots, rebalances, total_rebal_fees, max_bars


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    total_capital = 10000

    print(f"=== 4-asset Portfolio with Rebalancing — M1 {days}d ===\n")

    symbols = ["PAXG_USDT", "BTC_USDT", "ETH_USDT", "SOL_USDT"]
    data = {}
    for s in symbols:
        print(f"Fetching {s} M1 ...", flush=True)
        data[s] = fetch_m1(s, max_days=days)
        if data[s]:
            print(f"  {s}: {len(data[s])} candles")

    # Three portfolios
    portfolios = {
        "Conservadora": {"PAXG_USDT": 0.40, "BTC_USDT": 0.30, "ETH_USDT": 0.20, "SOL_USDT": 0.10},
        "Equilibrada":  {"PAXG_USDT": 0.20, "BTC_USDT": 0.30, "ETH_USDT": 0.30, "SOL_USDT": 0.20},
        "Agressiva":    {"PAXG_USDT": 0.10, "BTC_USDT": 0.20, "ETH_USDT": 0.35, "SOL_USDT": 0.35},
    }

    rebal_period = 1440 * 1  # rebalance daily (1440 M1 bars)

    print()
    for name, weights in portfolios.items():
        allocs = {s: total_capital * w for s, w in weights.items()}
        # No rebalance baseline
        bots_norb, _, _, bars = run_portfolio(data, dict(allocs), weights, total_capital, days, rebal_period_bars=10**9)
        # With rebalance
        bots_rb, n_reb, rb_fees, _ = run_portfolio(data, dict(allocs), weights, total_capital, days, rebal_period_bars=rebal_period)

        def summary(bots, label):
            total_cash = sum(b.cash for b in bots.values())
            total_fees = sum(b.fees_paid for b in bots.values())
            net = total_cash - total_fees
            span_days = bars / 1440
            per_mo = net / span_days * 30
            per_yr = net / span_days * 365
            apr = per_yr / total_capital * 100
            per_sym = {s: f"{b.cycles}c/${b.cash:.1f}" for s, b in bots.items()}
            print(f"  {label}")
            print(f"    Per asset: {per_sym}")
            print(f"    Total cycles: {sum(b.cycles for b in bots.values())} | cash extracted: ${total_cash:.2f}")
            print(f"    Net: ${net:.2f} ({apr:+.1f}% APR, ${per_mo:+.2f}/mo)")

        print(f"--- {name} ({total_capital} USDT) ---")
        print(f"  Target weights: {weights}")
        summary(bots_norb, "WITHOUT rebalancing:")
        summary(bots_rb, f"WITH daily rebalancing ({n_reb} rebals, ${rb_fees:.2f} fees):")
        print()


if __name__ == "__main__":
    main()
