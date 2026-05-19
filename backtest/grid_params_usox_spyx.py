"""
Anàlisi volatilitat USOX i SPYX a Pionex i proposta paràmetres de grid.

Llegeix les dades persistides de les crides MCP pionex_market_get_klines
i calcula:
- Vol diari mig 30d / 7d
- Range mig diari
- M5 range mig (per intuir cycles/dia)
- Daily ATR (true range)
- Proposta: width = 2 × daily_range, step, rows
"""
import json
import statistics
from pathlib import Path

ROOT = Path(r"C:\Users\Administrator\.claude\projects\C--Users-Administrator-Desktop-MT4-Claude\a2b66114-9f86-44d2-9680-6c357aa5c158\tool-results")

# Mapping per files (segons l'ordre de les crides MCP)
FILES = {
    # daily 90d
    "USOX_1D": "toolu_01Q7VzNLLwBwEh5fp16JE5i9.txt",  # primera resposta daily
    "SPYX_1D": "toolu_017Wd4HJNAKpqvsT7upPWQGf.txt",  # ATENCIO: aquest pot ser M5...
}


def load_klines(path):
    raw = Path(path).read_text(encoding="utf-8")
    obj = json.loads(raw)
    return obj["data"]["data"]["klines"]


def is_synthetic_bar(b):
    """Bar sintetic: open==close==high==low i volum=0 (cap de setmana / sense activitat)."""
    return float(b["volume"]) == 0 and float(b["open"]) == float(b["close"]) == float(b["high"]) == float(b["low"])


def analyze_daily(klines, name):
    # Ordenar de mes antic a mes nou
    klines = sorted(klines, key=lambda b: int(b["time"]))
    # Filtrar synthetic
    real = [b for b in klines if not is_synthetic_bar(b)]
    print(f"\n{'=' * 70}")
    print(f"{name} - DAILY KLINES")
    print(f"{'=' * 70}")
    print(f"Total bars: {len(klines)}  /  Real (no synthetic weekend): {len(real)}")

    daily_pct_range = []  # (high-low)/close
    daily_abs_ret = []    # |close-open|/open
    daily_signed_ret = []  # log return
    import math
    for b in real:
        h, l, o, c = float(b["high"]), float(b["low"]), float(b["open"]), float(b["close"])
        daily_pct_range.append((h - l) / c * 100)
        daily_abs_ret.append(abs(c - o) / o * 100)
        daily_signed_ret.append(math.log(c / o))

    def stats(name_, lst):
        if not lst:
            return
        lst_s = sorted(lst)
        mean = statistics.mean(lst_s)
        med = statistics.median(lst_s)
        p25 = lst_s[len(lst_s)//4]
        p75 = lst_s[3*len(lst_s)//4]
        p90 = lst_s[int(len(lst_s)*0.9)]
        print(f"  {name_:<30} mean={mean:.2f}%  median={med:.2f}%  p25={p25:.2f}%  p75={p75:.2f}%  p90={p90:.2f}%")

    print("\nGlobal (~90d real):")
    stats("Daily range (H-L)/C", daily_pct_range)
    stats("Daily abs return |C-O|/O", daily_abs_ret)

    # Vol anualitzada (std log returns × sqrt(252))
    if len(daily_signed_ret) > 1:
        sd = statistics.stdev(daily_signed_ret)
        annual_vol = sd * (252 ** 0.5) * 100
        print(f"\n  Vol diaria (std log ret): {sd*100:.2f}%  -> annual ~{annual_vol:.1f}%")

    # Ultims 30 dies
    last30 = real[-30:] if len(real) >= 30 else real
    last30_ranges = [(float(b["high"])-float(b["low"]))/float(b["close"])*100 for b in last30]
    last30_abs = [abs(float(b["close"])-float(b["open"]))/float(b["open"])*100 for b in last30]
    last30_logs = [math.log(float(b["close"])/float(b["open"])) for b in last30]
    print(f"\nUltims {len(last30)} dies reals:")
    stats("Daily range", last30_ranges)
    stats("Daily abs return", last30_abs)
    if len(last30_logs) > 1:
        sd = statistics.stdev(last30_logs)
        print(f"  Vol 30d: {sd*100:.2f}%/dia  annual ~{sd*(252**0.5)*100:.1f}%")

    # Ultims 7 dies
    last7 = real[-7:] if len(real) >= 7 else real
    last7_ranges = [(float(b["high"])-float(b["low"]))/float(b["close"])*100 for b in last7]
    print(f"\nUltims {len(last7)} dies reals:")
    stats("Daily range", last7_ranges)

    return {
        "annual_vol_90d": sd * (252 ** 0.5) * 100 if len(daily_signed_ret) > 1 else 0,
        "median_daily_range": statistics.median(daily_pct_range),
        "median_daily_range_30d": statistics.median(last30_ranges),
        "median_daily_range_7d": statistics.median(last7_ranges),
        "p75_daily_range_30d": sorted(last30_ranges)[3*len(last30_ranges)//4],
        "current_price": float(real[-1]["close"]),
    }


def analyze_m5(klines, name):
    klines = sorted(klines, key=lambda b: int(b["time"]))
    real = [b for b in klines if float(b["volume"]) > 0]
    print(f"\n{'-' * 70}")
    print(f"{name} - M5 (5-minute bars)")
    print(f"{'-' * 70}")
    print(f"Total bars: {len(klines)}  /  Real (vol>0): {len(real)}")

    if not real:
        print("  No real bars, skipping")
        return None

    m5_ranges = [(float(b["high"])-float(b["low"]))/float(b["close"])*100 for b in real]
    m5_ranges_s = sorted(m5_ranges)
    n = len(m5_ranges_s)
    print(f"  M5 range mean: {statistics.mean(m5_ranges):.3f}%")
    print(f"  M5 range median: {m5_ranges_s[n//2]:.3f}%")
    print(f"  M5 range p75: {m5_ranges_s[3*n//4]:.3f}%")
    print(f"  M5 range p90: {m5_ranges_s[int(n*0.9)]:.3f}%")
    print(f"  M5 range p95: {m5_ranges_s[int(n*0.95)]:.3f}%")
    return {"m5_median_pct": m5_ranges_s[n//2], "m5_p75_pct": m5_ranges_s[3*n//4]}


def propose_grid(name, daily_stats, m5_stats, allocation_usd, fee_per_trade_pct=0.05, min_order_usd=10):
    """
    Pionex spot grid framework:
    - width = 2 × median_daily_range_30d (cobreix swings normals)
    - step >= max(0.40%, 4×fee_per_trade) per garantir profit per cycle
    - rows segons capital ($10 minim per ordre)
    """
    p = daily_stats["current_price"]
    median_range_30d = daily_stats["median_daily_range_30d"]
    p75_range_30d = daily_stats["p75_daily_range_30d"]
    annual_vol = daily_stats["annual_vol_90d"]

    # Width: cobreix ~2 dies de range mig per banda (4× total)
    width_pct = 2 * p75_range_30d  # mes conservador que mediana
    width_pct = max(width_pct, 8.0)  # minim 8% per que el bot no recoloqui cada poc
    width_pct = min(width_pct, 40.0)  # maxim 40% per evitar grids massa amples

    lower = p * (1 - width_pct / 200)
    upper = p * (1 + width_pct / 200)

    # Step: minim que faci profit per cycle, idealment 4× fees (cost round trip = 2×fee)
    fee_round_trip = 2 * fee_per_trade_pct
    min_step_econ = fee_round_trip * 2  # 0.20% per Pionex
    step_pct = max(0.40, min_step_econ)

    # Pero step ha de ser proporcional al moviment intradia (M5) per fer cycles
    if m5_stats:
        m5_p75 = m5_stats["m5_p75_pct"]
        # un step massa petit fa que cada M5 trigui un cycle (sobrecost de fees)
        # un step massa gran no captura cycles
        # objectiu: step ~ 2-4× M5 median range -> cycle cada 5-20 bars M5
        step_pct = max(step_pct, 2 * m5_p75)

    # Rows totals dins el width
    rows = int(width_pct / step_pct)
    # Capital per row
    cap_per_row = allocation_usd / rows
    if cap_per_row < min_order_usd * 2:  # cada ordre necessita >= $10
        # cal reduir rows
        rows = int(allocation_usd / (2 * min_order_usd))
        step_pct = width_pct / rows
        cap_per_row = allocation_usd / rows

    # Profit/cycle (gross)
    gross_per_cycle_pct = step_pct - fee_round_trip
    cycles_to_double_per_grid_unit = 100 / gross_per_cycle_pct if gross_per_cycle_pct > 0 else None

    print(f"\n{'#' * 70}")
    print(f"# PROPOSTA GRID: {name}")
    print(f"{'#' * 70}")
    print(f"  Preu actual:       ${p:.2f}")
    print(f"  Median range 30d:  {median_range_30d:.2f}%")
    print(f"  P75 range 30d:     {p75_range_30d:.2f}%")
    print(f"  Vol anualitzada:   {annual_vol:.1f}%")
    print(f"")
    print(f"  >> WIDTH:          {width_pct:.1f}%  (lower=${lower:.2f}  upper=${upper:.2f})")
    print(f"  >> STEP:           {step_pct:.2f}%  ({step_pct/100*p:.3f} USDT)")
    print(f"  >> ROWS:           {rows}")
    print(f"  >> CAPITAL:        ${allocation_usd:.0f}  ({cap_per_row:.2f}$/row)")
    print(f"  >> Gross/cycle:    {gross_per_cycle_pct:.3f}% (step - 2×fee)")
    print(f"  Net per cycle:     ~{(gross_per_cycle_pct - 0.02):.3f}% (slippage incl.)")
    if m5_stats:
        # cycles estimats per dia: ~daily_range / step (sense compounding)
        cycles_per_day_est = daily_stats["median_daily_range_30d"] / step_pct
        print(f"  Cycles/dia est.:   ~{cycles_per_day_est:.1f}")
        print(f"  Profit/dia est.:   ~{cycles_per_day_est * gross_per_cycle_pct * allocation_usd / 100:.2f}$ (gross)")


def main():
    # USOX
    usox_d = load_klines(ROOT / "toolu_01Q7VzNLLwBwEh5fp16JE5i9.txt")
    spyx_d = load_klines(ROOT / "toolu_017Wd4HJNAKpqvsT7upPWQGf.txt")
    print(f"USOX file -> {len(usox_d)} bars (interval guess by time diff: {(int(usox_d[0]['time'])-int(usox_d[1]['time']))/1000/60:.0f} min)")
    print(f"SPYX file -> {len(spyx_d)} bars (interval: {(int(spyx_d[0]['time'])-int(spyx_d[1]['time']))/1000/60:.0f} min)")


if __name__ == "__main__":
    main()
