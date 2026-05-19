"""
Backtest runner — orquestrador end-to-end.

Llegeix:
  - Config dels bots (replicat del grid_manager/config.py)
  - CSVs M1 dels 4 actius
Executa:
  - Simulació minut a minut per tot el període
Output:
  - results/<run_id>/equity_curve.csv
  - results/<run_id>/recolocations.csv
  - results/<run_id>/rebalances.csv
  - results/<run_id>/summary.json
  - results/<run_id>/equity_plot.png
  - results/<run_id>/config.json (la config usada)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from data_loader import aligned_iterator, date_range_info
from portfolio import (
    PortfolioConfig, init_portfolio, step,
    get_total_value, get_total_grid_alpha, get_total_fees, get_grid_alpha_net,
    get_per_bot_summary,
)
from metrics import (
    equity_curve_to_csv, trail_events_to_csv, rebalance_events_to_csv,
    compute_summary_stats, plot_equity_curve, save_summary_json,
)


# ─── CONFIG IDÈNTIC A grid_manager/config.py ──────────────────────────────
BOTS_CONFIG = {
    "PAXG_USDT": {"symbol": "PAXG_USDT", "width_pct": 0.032,  "rows": 8},
    "BTC_USDT":  {"symbol": "BTC_USDT",  "width_pct": 0.0516, "rows": 12},
    "ETH_USDT":  {"symbol": "ETH_USDT",  "width_pct": 0.067,  "rows": 12},
    "SOL_USDT":  {"symbol": "SOL_USDT",  "width_pct": 0.070,  "rows": 9},
}

TARGET_WEIGHTS = {
    "PAXG_USDT": 0.40,
    "BTC_USDT":  0.30,
    "ETH_USDT":  0.20,
    "SOL_USDT":  0.10,
}


def run_backtest(
    initial_capital: float = 1000.0,
    initial_reserve_pct: float = 0.0,
    start_ms: int = 0,
    end_ms: int = 0,
    snapshot_every_min: int = 60,    # Cada quants minuts guardem equity_curve sample
    rebalance_every_min: int = 2,    # Cada quants minuts comprovem rebalanceig
    run_id: str | None = None,
    bots_config: dict = None,
    target_weights: dict = None,
):
    """Executa un backtest sencer."""
    if bots_config is None:
        bots_config = BOTS_CONFIG
    if target_weights is None:
        target_weights = TARGET_WEIGHTS

    if run_id is None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = BASE / "results" / run_id
    results_dir.mkdir(parents=True, exist_ok=True)

    # Logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(BASE / "logs" / f"backtest_{run_id}.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    log = logging.getLogger("runner")

    log.info("=" * 70)
    log.info(f"BACKTEST run_id={run_id}")
    log.info(f"  Capital inicial: ${initial_capital:,.2f}")
    log.info(f"  Reserve %: {initial_reserve_pct*100:.1f}%")
    log.info(f"  Bots: {list(bots_config.keys())}")
    log.info(f"  Weights: {target_weights}")
    log.info("=" * 70)

    # Save config
    config_used = {
        "run_id": run_id,
        "initial_capital": initial_capital,
        "initial_reserve_pct": initial_reserve_pct,
        "snapshot_every_min": snapshot_every_min,
        "rebalance_every_min": rebalance_every_min,
        "bots": bots_config,
        "weights": target_weights,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    (results_dir / "config.json").write_text(json.dumps(config_used, indent=2))

    # Info dades disponibles
    symbols = [c["symbol"] for c in bots_config.values()]
    info = date_range_info(symbols)
    log.info("Dades disponibles per asset:")
    for sym, d in info.items():
        log.info(f"  {sym}: {d}")

    # Inicialitza portfolio — necessitem preus inicials
    bar_iter = aligned_iterator(symbols, start_ms=start_ms, end_ms=end_ms or None)
    first_bars = next(bar_iter)
    initial_prices = {sym: first_bars[sym]["open"] for sym in symbols if first_bars[sym]}

    cfg = PortfolioConfig(
        bots=bots_config,
        target_weights=target_weights,
        initial_capital=initial_capital,
        initial_reserve_pct=initial_reserve_pct,
    )
    portfolio = init_portfolio(cfg, initial_prices)
    log.info(f"Portfolio inicialitzat. Preus inicials: {initial_prices}")

    # Loop principal
    equity_curve = []
    bar_count = 0
    last_log = time.time()
    last_snap_min = -snapshot_every_min  # forçar snapshot al primer minut

    # Processar el primer bar (ja l'hem consumit per init prices)
    step(portfolio, cfg, first_bars, rebalance_every_min=rebalance_every_min)
    bar_count = 1

    for bars in bar_iter:
        ts_ms = bars["ts_ms"]
        step(portfolio, cfg, bars, rebalance_every_min=rebalance_every_min)
        bar_count += 1

        # Snapshot periodic per equity_curve
        minute_idx = ts_ms // 60_000
        if minute_idx - last_snap_min >= snapshot_every_min:
            total_value = get_total_value(portfolio, cfg, bars)
            grid_alpha_net = get_grid_alpha_net(portfolio)
            mtm = (total_value - initial_capital) - get_total_grid_alpha(portfolio)
            equity_curve.append({
                "ts_ms": ts_ms,
                "total_value": round(total_value, 4),
                "grid_alpha_net": round(grid_alpha_net, 4),
                "grid_alpha_brut": round(get_total_grid_alpha(portfolio), 4),
                "mtm": round(mtm, 4),
                "recoloc_cost_cum": round(portfolio.total_recolocation_cost, 4),
                "rebal_fees_cum": round(portfolio.total_rebalance_fees, 4),
                "reloc_count": len(portfolio.trail_events),
                "rebal_count": len(portfolio.rebalance_events),
            })
            last_snap_min = minute_idx

        # Progress log cada 30s
        if time.time() - last_log > 30:
            log.info(f"Bar {bar_count:,} · ts={datetime.fromtimestamp(ts_ms/1000, timezone.utc).isoformat()[:16]} · "
                     f"grid_alpha_net=${get_grid_alpha_net(portfolio):.2f} · "
                     f"reloc={len(portfolio.trail_events)} · rebal={len(portfolio.rebalance_events)}")
            last_log = time.time()

    # Final snapshot
    if equity_curve:
        log.info("Loop finalitzat. Final stats:")
        log.info(f"  Bars processats: {bar_count:,}")
        log.info(f"  Grid Alpha brut: ${get_total_grid_alpha(portfolio):.4f}")
        log.info(f"  Recolocation cost: ${portfolio.total_recolocation_cost:.4f}")
        log.info(f"  Rebalance fees: ${portfolio.total_rebalance_fees:.4f}")
        log.info(f"  Grid Alpha NET: ${get_grid_alpha_net(portfolio):.4f}")

    # Save outputs
    equity_curve_to_csv(equity_curve, results_dir / "equity_curve.csv")
    trail_events_to_csv(portfolio.trail_events, results_dir / "recolocations.csv")
    rebalance_events_to_csv(portfolio.rebalance_events, results_dir / "rebalances.csv")

    per_bot_alpha = {name: g.grid_profit_realized for name, g in portfolio.grids.items()}
    summary = compute_summary_stats(
        equity_curve, portfolio.trail_events, portfolio.rebalance_events,
        initial_capital, per_bot_alpha,
    )

    # ── Extended metrics (FIX #B, #C, #E) ──
    last_bars = bars  # bar més recent del loop
    summary["per_bot"] = get_per_bot_summary(portfolio, cfg, last_bars)
    summary["external_deposits"] = {
        "total_usdt": round(portfolio.total_external_deposits, 4),
        "count": len(portfolio.external_deposits),
        "events": portfolio.external_deposits[:50],  # primers 50 events
    }
    summary["total_turnover_usdt"] = round(portfolio.total_turnover_usdt, 2)
    summary["fees_breakdown"] = {
        k: round(v, 4) for k, v in portfolio.fees_breakdown.items()
    }
    # Recalculate fees_breakdown.fills (recompose from grid states)
    summary["fees_breakdown"]["fills"] = round(
        sum(g.fees_paid_total for g in portfolio.grids.values())
        - portfolio.total_recolocation_cost, 4
    )
    # Resum final del valor total inclou reserve + grid_alpha
    summary["final_reserve_usdt"] = round(portfolio.reserve_usdt, 2)
    summary["capital_total_real"] = round(initial_capital + portfolio.total_external_deposits, 2)
    # final_total_value JA inclou portfolio.reserve_usdt (via get_total_value)
    summary["net_pnl_real"] = round(
        summary["final_total_value"]
        - initial_capital - portfolio.total_external_deposits, 4
    )

    # ── HOLD BENCHMARK & GRID EDGE ──
    # Què hagués valgut el portfolio SI no haguéssim operat el grid?
    # = inventari inicial × preu_final + reserve preservada
    try:
        from data_loader import load_csv
        hold_value = 0.0
        for name, bot_cfg in cfg.bots.items():
            sym = bot_cfg["symbol"]
            target_w = cfg.target_weights.get(name, 0)
            capital_for_this = (initial_capital * (1 - cfg.initial_reserve_pct)) * target_w
            bars = load_csv(sym)
            sorted_ts = sorted(bars.keys())
            # Filtra al rang del backtest
            if start_ms:
                sorted_ts = [t for t in sorted_ts if t >= start_ms]
            if end_ms:
                sorted_ts = [t for t in sorted_ts if t <= end_ms]
            if not sorted_ts:
                continue
            first_price = bars[sorted_ts[0]]["open"]
            last_price = bars[sorted_ts[-1]]["close"]
            qty = capital_for_this / first_price
            hold_value += qty * last_price
        reserve_preserved = initial_capital * cfg.initial_reserve_pct
        hold_total = hold_value + reserve_preserved
        grid_edge = summary["final_total_value"] - hold_total

        summary["hold_benchmark"] = {
            "hold_total_value": round(hold_total, 2),
            "hold_pnl": round(hold_total - initial_capital, 2),
            "hold_pnl_pct": round((hold_total - initial_capital) / initial_capital * 100, 3),
        }
        summary["grid_edge"] = {
            "edge_usdt": round(grid_edge, 2),
            "edge_pct": round(grid_edge / initial_capital * 100, 3),
            "interpretation": (
                "POSITIVE: grid afegeix valor vs hold pur"
                if grid_edge > 0 else
                "NEGATIVE: grid perd valor vs hold pur (mercat tendencial)"
            ),
        }
        log.info(f"\nHOLD BENCHMARK: ${hold_total:.2f} ({(hold_total-initial_capital)/initial_capital*100:+.2f}%)")
        log.info(f"GRID EDGE: ${grid_edge:+.2f} ({grid_edge/initial_capital*100:+.2f}%)")
    except Exception as e:
        log.warning(f"Hold benchmark calc failed: {e}")

    save_summary_json(summary, results_dir / "summary.json")
    plot_equity_curve(equity_curve, results_dir / "equity_plot.png")

    log.info(f"\n✅ Outputs guardats a {results_dir}")
    log.info(f"Summary: {json.dumps(summary, indent=2)}")

    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capital", type=float, default=1000.0)
    parser.add_argument("--reserve-pct", type=float, default=0.0)
    parser.add_argument("--snapshot-min", type=int, default=60)
    parser.add_argument("--rebalance-min", type=int, default=2)
    parser.add_argument("--start", type=str, default="", help="ISO date, e.g. 2025-06-01")
    parser.add_argument("--end", type=str, default="")
    parser.add_argument("--run-id", type=str, default="")
    args = parser.parse_args()

    start_ms = 0
    end_ms = 0
    if args.start:
        start_ms = int(datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc).timestamp() * 1000)
    if args.end:
        end_ms = int(datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc).timestamp() * 1000)

    run_backtest(
        initial_capital=args.capital,
        initial_reserve_pct=args.reserve_pct,
        start_ms=start_ms,
        end_ms=end_ms,
        snapshot_every_min=args.snapshot_min,
        rebalance_every_min=args.rebalance_min,
        run_id=args.run_id or None,
    )


if __name__ == "__main__":
    main()
