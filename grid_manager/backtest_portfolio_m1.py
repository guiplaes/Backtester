"""
Portfolio backtest: PAXG + BTC + ETH grid trailing combinats.
Veles M1 reals. Profit comptat com PURE CYCLE profit (step × pos_per_cycle),
sense crystal·lització de HODL appreciation.
"""
import sys
import time
from datetime import datetime, timezone
from pionex_client import _get_public

MAKER_FEE = 0.0002
TAKER_FEE = 0.0005
SLIPPAGE  = 0.001
ROWS      = 12
TRIGGER_PCT = 0.05  # tighter, only reposition when price is REALLY near edge

# Volatility-adapted width per asset (≈ 2× daily ATR%)
WIDTH_PCT_PER_SYMBOL = {
    "PAXG_USDT": 0.025,   # ~2.5% (gold low vol)
    "BTC_USDT":  0.060,   # ~6% (BTC TR ~3.5% daily, need 2x cushion)
    "ETH_USDT":  0.080,   # ~8% (ETH TR ~5% daily)
}


def fetch_m1(symbol, max_days=10):
    """Fetch M1 candles (max ~10 days due to Pionex API limits)."""
    now_ms = int(time.time() * 1000)
    target = now_ms - max_days * 86400 * 1000
    seen, klines, cursor = set(), [], now_ms
    for _ in range(30):
        r = _get_public("/api/v1/market/klines", {
            "symbol": symbol, "interval": "1M", "limit": 500, "endTime": cursor
        })
        chunk = r.get("data", {}).get("klines", [])
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
    return [k for k in klines if int(k["time"]) >= target]


class TrailGridSim:
    """Spot grid trailing — only counts pure cycle profit (step × inventory_unit)."""
    def __init__(self, capital, rows=ROWS, width_pct=0.03):
        self.capital = capital
        self.rows = rows
        self.width_pct = width_pct
        self.cap_per_grid = capital / rows

    def init(self, start_price):
        self.center = start_price
        self.width = start_price * self.width_pct
        self.step = self.width / self.rows
        self.levels = [round(self.center - self.width/2 + i * self.step, 4)
                       for i in range(self.rows + 1)]
        # pending[L] = state for that level: 'BUY' (waiting to buy), 'SELL' (waiting to sell), None (fired)
        self.pending = {L: ("BUY" if L < start_price else "SELL" if L > start_price else None)
                        for L in self.levels}
        # Track which side is the "open" side at each level (to count complete cycles)
        self.last_side = {L: None for L in self.levels}
        self.cycles = 0
        self.fills = 0
        self.cycle_cash = 0.0  # PURE cycle profit, no HODL crystallization
        self.fees_paid = 0.0
        self.repositions = 0

    def fire(self, level, side):
        notional = self.cap_per_grid
        self.fees_paid += notional * MAKER_FEE
        self.fills += 1
        # Check if this fill closes a cycle: opposite side was last fired at this level OR adjacent
        prev = self.last_side.get(level)
        if prev and prev != side:
            # Complete cycle: profit = step × (notional / price)
            self.cycles += 1
            self.cycle_cash += self.step / level * notional
        self.last_side[level] = side

    def walk(self, p1, p2):
        if p2 > p1:
            for L in self.levels:
                if p1 < L <= p2 and self.pending.get(L) == "SELL":
                    self.fire(L, "SELL")
                    self.pending[L] = None
                    below_L = round(L - self.step, 4)
                    if below_L in self.pending:
                        self.pending[below_L] = "BUY"
        elif p2 < p1:
            for L in reversed(self.levels):
                if p2 <= L < p1 and self.pending.get(L) == "BUY":
                    self.fire(L, "BUY")
                    self.pending[L] = None
                    above_L = round(L + self.step, 4)
                    if above_L in self.pending:
                        self.pending[above_L] = "SELL"

    def process_bar(self, k):
        o = float(k["open"]); h = float(k["high"])
        l = float(k["low"]);  c = float(k["close"])
        # Process fills first
        path = [o, l, h, c] if c >= o else [o, h, l, c]
        for i in range(1, 4):
            self.walk(path[i-1], path[i])
        # Then check reposition
        top = self.center + self.width/2
        bottom = self.center - self.width/2
        dt = (top - c) / self.width
        db = (c - bottom) / self.width
        if c > top or c < bottom or dt < TRIGGER_PCT or db < TRIGGER_PCT:
            self.fees_paid += self.capital * 0.0003
            # Preserve cycle history when repositioning (instead of full reset)
            prev_last = dict(self.last_side)
            self.init(c)
            for L, side in prev_last.items():
                if L in self.last_side:
                    self.last_side[L] = side
            self.repositions += 1


def run_symbol(symbol, capital, max_days=10):
    print(f"  Fetching M1 for {symbol} ...", flush=True)
    klines = fetch_m1(symbol, max_days=max_days)
    if not klines:
        return None
    width_pct = WIDTH_PCT_PER_SYMBOL.get(symbol, 0.03)
    sim = TrailGridSim(capital, width_pct=width_pct)
    sim.init(float(klines[0]["close"]))
    for k in klines:
        sim.process_bar(k)
    span_days = (int(klines[-1]["time"]) - int(klines[0]["time"])) / 86400000
    net = sim.cycle_cash - sim.fees_paid
    return {
        "symbol": symbol,
        "capital": capital,
        "span_days": span_days,
        "candles": len(klines),
        "fills": sim.fills,
        "cycles": sim.cycles,
        "repositions": sim.repositions,
        "cycle_cash": sim.cycle_cash,
        "fees": sim.fees_paid,
        "net": net,
        "per_day": net / span_days if span_days else 0,
        "per_month": net / span_days * 30 if span_days else 0,
        "per_year": net / span_days * 365 if span_days else 0,
        "apr": net / span_days * 365 / capital * 100 if span_days else 0,
        "start_price": float(klines[0]["close"]),
        "end_price": float(klines[-1]["close"]),
        "price_change_pct": (float(klines[-1]["close"]) - float(klines[0]["close"])) / float(klines[0]["close"]) * 100,
    }


def main():
    max_days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    print(f"=== M1 Portfolio Backtest ({max_days}d max) ===")
    print()

    portfolios = {
        "Conservadora": {"PAXG_USDT": 5000, "BTC_USDT": 3000, "ETH_USDT": 2000},
        "Equilibrada":  {"PAXG_USDT": 3000, "BTC_USDT": 4000, "ETH_USDT": 3000},
        "Agressiva":    {"PAXG_USDT": 1000, "BTC_USDT": 3000, "ETH_USDT": 6000},
    }

    # Run each symbol once
    print("Running per-symbol simulations ...")
    symbol_results = {}
    for sym in ["PAXG_USDT", "BTC_USDT", "ETH_USDT"]:
        # Use 1k capital baseline (we'll scale per portfolio)
        r = run_symbol(sym, 1000, max_days=max_days)
        symbol_results[sym] = r
        if r:
            print(f"  {sym:10s} {r['span_days']:.1f}d  {r['candles']} candles  {r['cycles']} cycles  net=${r['net']:.2f}  APR per $1k: {r['apr']:+.1f}%")

    print()
    print("=" * 75)
    print(f"{'Portfolio':<14} | {'PAXG':>10} | {'BTC':>10} | {'ETH':>10} | {'/mo':>10} | {'/year':>10} | APR")
    print("=" * 75)

    for name, allocs in portfolios.items():
        totals = {"net": 0, "per_month": 0, "per_year": 0, "total_cap": 0}
        per_sym = {}
        for sym, cap in allocs.items():
            r = symbol_results.get(sym)
            if not r:
                per_sym[sym] = "n/a"
                continue
            # scale: per-symbol result is for $1000 cap. Multiply by cap/1000.
            scale = cap / 1000
            sym_net = r["net"] * scale
            sym_mo = r["per_month"] * scale
            sym_yr = r["per_year"] * scale
            per_sym[sym] = f"${sym_mo:.0f}/mo"
            totals["net"] += sym_net
            totals["per_month"] += sym_mo
            totals["per_year"] += sym_yr
            totals["total_cap"] += cap
        apr = totals["per_year"] / totals["total_cap"] * 100 if totals["total_cap"] else 0
        print(f"{name:<14} | {per_sym.get('PAXG_USDT','n/a'):>10} | {per_sym.get('BTC_USDT','n/a'):>10} | {per_sym.get('ETH_USDT','n/a'):>10} | ${totals['per_month']:>9.0f} | ${totals['per_year']:>9.0f} | {apr:+5.1f}%")


if __name__ == "__main__":
    main()
