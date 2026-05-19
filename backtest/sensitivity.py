"""
Sensitivity analysis — varia paràmetres clau i compara resultats.

Variables a provar:
  - EDGE_TRIGGER_PCT: 0.05, 0.10, 0.15, 0.20
  - width_pct (multiplicat per factor): 0.7, 1.0, 1.3, 1.6
  - REBALANCE_THRESHOLD: 0.03, 0.05, 0.08, 0.12
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from runner import run_backtest, BOTS_CONFIG, TARGET_WEIGHTS
import trailing_logic
import rebalancer_sim


BASE = Path(__file__).resolve().parent
RESULTS = BASE / "results" / "sensitivity"
RESULTS.mkdir(parents=True, exist_ok=True)


def run_with_params(edge_pct: float, width_mult: float, rebal_threshold: float, period_days: int = 365):
    """Executa un backtest amb paràmetres modificats."""
    # Modifica EDGE_TRIGGER_PCT global del trailing
    trailing_logic.EDGE_TRIGGER_PCT = edge_pct
    rebalancer_sim.REBALANCE_THRESHOLD = rebal_threshold

    # Modifica width_pct per bot (proporcional al factor)
    bots_modified = {}
    for name, cfg in BOTS_CONFIG.items():
        bots_modified[name] = {
            **cfg,
            "width_pct": cfg["width_pct"] * width_mult,
        }

    run_id = f"sens_edge{int(edge_pct*100)}_w{int(width_mult*100)}_reb{int(rebal_threshold*100)}"

    t0 = time.time()
    summary = run_backtest(
        initial_capital=1000.0, initial_reserve_pct=0.05,
        start_ms=0, end_ms=0,
        snapshot_every_min=240,  # 4h snapshot (més ràpid)
        rebalance_every_min=2,
        run_id=run_id,
        bots_config=bots_modified, target_weights=TARGET_WEIGHTS,
    )
    elapsed = time.time() - t0
    return {
        "run_id": run_id,
        "edge_pct": edge_pct,
        "width_mult": width_mult,
        "rebal_threshold": rebal_threshold,
        "elapsed_sec": round(elapsed, 1),
        "grid_alpha_net": summary.get("grid_alpha_net"),
        "grid_alpha_pct": summary.get("grid_alpha_pct"),
        "grid_alpha_apr": summary.get("grid_alpha_apr_extrapolated"),
        "total_roi_pct": summary.get("total_roi_pct"),
        "mtm": summary.get("mtm_unrealized"),
        "drawdown": summary.get("max_drawdown_alpha"),
        "reloc_count": summary.get("recolocation_count"),
        "reloc_cost": summary.get("recolocation_total_cost"),
        "rebal_count": summary.get("rebalance_count"),
        "rebal_fees": summary.get("rebalance_total_fees"),
        "deposits": summary.get("external_deposits", {}).get("total_usdt", 0),
    }


# Combos a provar
COMBINATIONS = [
    # (edge_pct, width_mult, rebal_threshold)
    # Baseline + variacions una a una
    (0.10, 1.00, 0.05),  # Producció actual

    # Sensitivity a EDGE_TRIGGER
    (0.05, 1.00, 0.05),
    (0.15, 1.00, 0.05),
    (0.20, 1.00, 0.05),

    # Sensitivity a width
    (0.10, 0.70, 0.05),  # 30% més estret
    (0.10, 1.30, 0.05),  # 30% més ample
    (0.10, 1.60, 0.05),  # 60% més ample

    # Sensitivity a rebalance threshold
    (0.10, 1.00, 0.03),  # més sensible al rebal
    (0.10, 1.00, 0.08),  # més tolerant
    (0.10, 1.00, 0.12),  # molt tolerant
]


def main():
    print(f"Sensitivity analysis — {len(COMBINATIONS)} combos")
    results = []
    for i, (edge, w, reb) in enumerate(COMBINATIONS, 1):
        print(f"\n[{i}/{len(COMBINATIONS)}] Running edge={edge*100:.0f}%, width×{w:.2f}, rebal={reb*100:.0f}%...")
        try:
            r = run_with_params(edge, w, reb)
            results.append(r)
            print(f"  alpha=${r['grid_alpha_net']:.2f}  apr={r['grid_alpha_apr']}%  reloc={r['reloc_count']}  rebal={r['rebal_count']}")
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"error": str(e), "edge_pct": edge, "width_mult": w, "rebal_threshold": reb})

    # Save report
    out = RESULTS / "sensitivity_report.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\n✅ Saved to {out}")

    # CSV-format summary
    csv_out = RESULTS / "sensitivity_report.csv"
    if results and "error" not in results[0]:
        with open(csv_out, "w", encoding="utf-8") as f:
            keys = list(results[0].keys())
            f.write(",".join(keys) + "\n")
            for r in results:
                f.write(",".join(str(r.get(k, "")) for k in keys) + "\n")
        print(f"   CSV: {csv_out}")


if __name__ == "__main__":
    main()
