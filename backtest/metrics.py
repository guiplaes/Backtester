"""
Metrics + plots — calcula i visualitza els resultats del backtest.

Mètriques principals (les que importen):
  - Grid Alpha NET total (suma cycles − recoloc costs − rebalance fees)
  - Grid Alpha per bot
  - Sharpe ratio del Grid Alpha (sense MTM)
  - Max drawdown del Grid Alpha
  - Equity total (Grid Alpha + MTM)
  - Buy-and-hold benchmark (què hauria fet sense bot)
  - Recolocation count + cost mig
  - Rebalance count + cost mig
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # no GUI
import matplotlib.pyplot as plt


def equity_curve_to_csv(equity_curve: list[dict], out_path: Path):
    """Escriu CSV de l'equity curve.
    Each entry: {ts_ms, total_value, grid_alpha_net, mtm}
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        if not equity_curve:
            return
        w = csv.DictWriter(f, fieldnames=list(equity_curve[0].keys()))
        w.writeheader()
        w.writerows(equity_curve)


def trail_events_to_csv(events: list, out_path: Path):
    if not events:
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["ts_ms", "bot_name", "trigger", "price_at_trigger",
                      "new_top", "new_bottom", "cost_usdt", "base_delta", "quote_delta"]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for e in events:
            d = asdict(e) if not isinstance(e, dict) else e
            # Filtra les fields que cal
            row = {k: d.get(k) for k in fieldnames}
            w.writerow(row)


def rebalance_events_to_csv(events: list, out_path: Path):
    if not events:
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["ts_ms", "target_bot", "amount_usdt", "deviation_pct", "fee_total", "partial", "sources"]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for e in events:
            d = asdict(e) if not isinstance(e, dict) else e
            row = {k: d.get(k) for k in fieldnames}
            row["sources"] = json.dumps(d.get("sources", []))
            w.writerow(row)


def compute_summary_stats(
    equity_curve: list[dict],
    trail_events: list,
    rebalance_events: list,
    initial_capital: float,
    per_bot_alpha: dict,
) -> dict:
    """Calcula stats agregats."""
    if not equity_curve:
        return {}
    last = equity_curve[-1]
    first = equity_curve[0]

    period_ms = last["ts_ms"] - first["ts_ms"]
    period_days = period_ms / (1000 * 60 * 60 * 24)

    grid_alpha_net = last.get("grid_alpha_net", 0)
    total_value = last.get("total_value", initial_capital)
    mtm = last.get("mtm", 0)

    # ROI percentages
    grid_alpha_pct = (grid_alpha_net / initial_capital) * 100 if initial_capital else 0
    total_roi_pct = ((total_value - initial_capital) / initial_capital) * 100 if initial_capital else 0

    # Sharpe simplificat: mean(daily_alpha) / std(daily_alpha) × sqrt(365)
    sharpe = None
    if period_days > 7:
        # Agregar grid_alpha_net per dia
        daily_alphas = []
        bucket = {}
        for e in equity_curve:
            day = e["ts_ms"] // (1000 * 60 * 60 * 24)
            bucket[day] = e["grid_alpha_net"]
        sorted_days = sorted(bucket.keys())
        for i in range(1, len(sorted_days)):
            prev = bucket[sorted_days[i-1]]
            curr = bucket[sorted_days[i]]
            daily_alphas.append(curr - prev)
        if daily_alphas:
            mean = sum(daily_alphas) / len(daily_alphas)
            var = sum((x - mean)**2 for x in daily_alphas) / max(1, len(daily_alphas)-1)
            std = math.sqrt(var)
            if std > 0:
                sharpe = (mean / std) * math.sqrt(365)

    # Max drawdown del grid_alpha_net (peak-to-trough)
    peak = 0
    max_dd = 0
    for e in equity_curve:
        val = e["grid_alpha_net"]
        if val > peak:
            peak = val
        dd = peak - val
        if dd > max_dd:
            max_dd = dd

    # Recoloc stats
    total_reloc_count = len(trail_events)
    total_reloc_cost = sum(
        (e.cost_usdt if hasattr(e, "cost_usdt") else e.get("cost_usdt", 0))
        for e in trail_events
    )
    avg_reloc_cost = total_reloc_cost / total_reloc_count if total_reloc_count else 0

    # Rebalance stats
    total_rebal_count = len(rebalance_events)
    total_rebal_fees = sum(
        (e.fee_total if hasattr(e, "fee_total") else e.get("fee_total", 0))
        for e in rebalance_events
    )

    # APR extrapolation
    apr = (grid_alpha_pct / period_days * 365) if period_days > 0 else 0

    return {
        "period_days": round(period_days, 2),
        "initial_capital": initial_capital,
        "final_total_value": round(total_value, 2),
        "total_roi_pct": round(total_roi_pct, 3),
        "grid_alpha_net": round(grid_alpha_net, 4),
        "grid_alpha_pct": round(grid_alpha_pct, 4),
        "grid_alpha_apr_extrapolated": round(apr, 2),
        "mtm_unrealized": round(mtm, 2),
        "max_drawdown_alpha": round(max_dd, 4),
        "sharpe": round(sharpe, 2) if sharpe else None,
        "recolocation_count": total_reloc_count,
        "recolocation_total_cost": round(total_reloc_cost, 4),
        "recolocation_avg_cost": round(avg_reloc_cost, 4),
        "rebalance_count": total_rebal_count,
        "rebalance_total_fees": round(total_rebal_fees, 4),
        "per_bot_alpha": {k: round(v, 4) for k, v in per_bot_alpha.items()},
    }


def plot_equity_curve(equity_curve: list[dict], out_path: Path):
    """Plot 4 panells:
       1) Total value (line)
       2) Grid Alpha NET (line) — el que de veritat captura el grid
       3) MTM unrealized (line, gris)
       4) Drawdown del Grid Alpha
    """
    if not equity_curve:
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ts = [e["ts_ms"] for e in equity_curve]
    times = [datetime.fromtimestamp(t/1000, timezone.utc) for t in ts]
    total = [e["total_value"] for e in equity_curve]
    alpha = [e["grid_alpha_net"] for e in equity_curve]
    mtm = [e["mtm"] for e in equity_curve]

    # Drawdown
    peak = 0
    dd_series = []
    for v in alpha:
        peak = max(peak, v)
        dd_series.append(peak - v)

    fig, axes = plt.subplots(4, 1, figsize=(14, 12), sharex=True)
    axes[0].plot(times, total, color="navy", linewidth=1.2)
    axes[0].set_title("Total Portfolio Value (USDT)")
    axes[0].grid(alpha=0.3)
    axes[0].set_ylabel("USDT")

    axes[1].plot(times, alpha, color="green", linewidth=1.2)
    axes[1].axhline(0, color="gray", linestyle="--", linewidth=0.6)
    axes[1].set_title("Grid Alpha NET (cycles − reloc costs − rebal fees)")
    axes[1].grid(alpha=0.3)
    axes[1].set_ylabel("USDT")

    axes[2].plot(times, mtm, color="orange", linewidth=1.2)
    axes[2].axhline(0, color="gray", linestyle="--", linewidth=0.6)
    axes[2].set_title("MTM Unrealized (price drift of inventory)")
    axes[2].grid(alpha=0.3)
    axes[2].set_ylabel("USDT")

    axes[3].fill_between(times, dd_series, color="red", alpha=0.3)
    axes[3].plot(times, dd_series, color="red", linewidth=0.8)
    axes[3].set_title("Grid Alpha Drawdown (peak-to-trough)")
    axes[3].grid(alpha=0.3)
    axes[3].set_ylabel("USDT")
    axes[3].set_xlabel("Date (UTC)")

    plt.tight_layout()
    plt.savefig(out_path, dpi=100)
    plt.close()


def save_summary_json(summary: dict, out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    # Smoke test amb data sintètica
    eq = [
        {"ts_ms": 0, "total_value": 1000, "grid_alpha_net": 0, "mtm": 0},
        {"ts_ms": 86400000, "total_value": 1010, "grid_alpha_net": 2, "mtm": 8},
        {"ts_ms": 172800000, "total_value": 1005, "grid_alpha_net": 4, "mtm": 1},
    ]
    stats = compute_summary_stats(eq, [], [], 1000, {"BTC_USDT": 2, "ETH_USDT": 2})
    print(json.dumps(stats, indent=2))
