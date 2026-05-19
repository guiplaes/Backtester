"""
reconcile.py — Reconciliació diària: compara Neon vs Pionex i alerta de discrepàncies.

Per cada bot:
  - Llegeix gridProfit Pionex (estat actual) + quoteTotalInvestment
  - Llegeix lifetime_profit_calc des de Neon (capital_events) + suma de capital invertit
  - Compara. Si discrepa > threshold → log a reconciliation_log amb severity warn/critical.

Executar diàriament (cron 23:58 UTC, després del daily_snapshot).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# Force user-site (per a Task Scheduler que no l'inclou per defecte)
_USER_SITE = r"C:\Users\Administrator\AppData\Roaming\Python\Python312\site-packages"
if _USER_SITE not in sys.path:
    sys.path.insert(0, _USER_SITE)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cloud.db_cloud import conn, log_reconciliation
from config import BOTS
from pionex_client import get_bot_order

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("reconcile")

# Thresholds
WARN_THRESHOLD_USDT = 1.0      # discrepancia > $1 → warning
CRITICAL_THRESHOLD_USDT = 5.0  # > $5 → critical (alert)


def _our_capital_invested(bot_id: str) -> float:
    """Suma neta de capital invertit segons capital_events (sense profit reinvertit)."""
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN event_type IN ('create', 'invest_in', 'rebalance_in', 'deposit_external') THEN amount_usdt ELSE 0 END), 0)
              - COALESCE(SUM(CASE WHEN event_type IN ('reduce', 'rebalance_out', 'withdraw_external', 'close') THEN amount_usdt ELSE 0 END), 0)
              + COALESCE(SUM(CASE WHEN event_type = 'reinvest_profit' THEN amount_usdt ELSE 0 END), 0)
            FROM capital_events
            WHERE bot_id = %s AND success = TRUE
        """, (bot_id,))
        return float(cur.fetchone()[0] or 0)


def _our_lifetime_profit(bot_id: str) -> float:
    """Lifetime profit calculat: suma de reinversions + delta des de l'últim event."""
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            SELECT COALESCE(SUM(amount_usdt), 0)
            FROM capital_events
            WHERE bot_id = %s AND event_type = 'reinvest_profit' AND success = TRUE
        """, (bot_id,))
        return float(cur.fetchone()[0] or 0)


def main():
    log.info(f"Reconciling {len(BOTS)} bots...")
    issues = 0
    for name, cfg in BOTS.items():
        try:
            bot = get_bot_order(cfg["id"])
            d = bot.get("buOrderData", {})
            pionex_gp = float(d.get("gridProfit", 0))
            pionex_qti = float(d.get("quoteTotalInvestment", 0))

            our_lifetime = _our_lifetime_profit(cfg["id"])  # només reinverted
            our_capital = _our_capital_invested(cfg["id"])

            # Pionex gridProfit = total since current grid was set
            # If we haven't reinvested, pionex_gp should == lifetime_grid_profit_so_far
            # Discrepancy in INVESTED CAPITAL:
            cap_discr = pionex_qti - our_capital
            # Discrepancy in PROFIT: complex (depèn de quan vam reinvertir, no ho podem comparar 1:1)
            # Per ara només alertem en capital, profit el guardem per a anàlisi
            sev = "ok"
            notes = []
            if abs(cap_discr) >= CRITICAL_THRESHOLD_USDT:
                sev = "critical"
                notes.append(f"CAPITAL DISCR: Pionex={pionex_qti} vs nostre={our_capital} (diff={cap_discr:+.2f})")
                issues += 1
            elif abs(cap_discr) >= WARN_THRESHOLD_USDT:
                sev = "warn"
                notes.append(f"capital diff {cap_discr:+.2f}")

            log_reconciliation(
                bot_id=cfg["id"],
                pionex_grid_profit=pionex_gp,
                pionex_quote_invested=pionex_qti,
                our_lifetime_profit=our_lifetime,
                our_capital_invested=our_capital,
                severity=sev,
                notes="; ".join(notes) if notes else None,
            )
            tag = {"ok": "OK", "warn": "WARN", "critical": "CRIT"}[sev]
            log.info(f"[{name}] {tag} pionex_qti={pionex_qti:.2f} nostre={our_capital:.2f} diff={cap_discr:+.2f}")
        except Exception as e:
            log.error(f"[{name}] reconcile failed: {e}")
            issues += 1

    if issues:
        log.warning(f"{issues} bots amb problemes — revisa reconciliation_log")
        sys.exit(1)
    else:
        log.info("Tot reconciliat correctament")


if __name__ == "__main__":
    main()
