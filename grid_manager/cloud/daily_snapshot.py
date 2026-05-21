"""
daily_snapshot.py — Captura 1 fila per bot/dia a daily_snapshots de Neon.

Executar cada nit (cron 23:55 UTC). Idempotent (UPSERT).

Calcula:
  - gross_grid_profit: gridProfit Pionex (lifetime, sense reset)
  - cum_lifetime_profit: el nostre comptador acumulat (capital_events delta + corrent)
  - current_value = quote + base * preu
"""
from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# Force user-site (per a Task Scheduler que no l'inclou per defecte)
_USER_SITE = r"C:\Users\Administrator\AppData\Roaming\Python\Python312\site-packages"
if _USER_SITE not in sys.path:
    sys.path.insert(0, _USER_SITE)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cloud.db_cloud import upsert_daily_snapshot, snapshot_bot_state, conn, log_wallet_snapshot
from config import BOTS
from pionex_client import get_bot_range, get_balance, get_current_price

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("daily_snapshot")


def _calc_lifetime_profit(bot_id: str, current_grid_profit: float) -> float:
    """Lifetime profit acumulat d'un bot.

    EMPIRICAL: Pionex PRESERVA el counter gridProfit a /spotGrid/profit
    (verificat 2026-05-21 amb cron weekly real). NO el reseteja com
    suposavem prèviament. Per tant NO cal sumar withdraw_profit events
    (seria doble-comptar).

    Lifetime = current Pionex gridProfit + SUM(grid_profit_at_close de
               bots ancestors via bot_lineage cross-recreation)

    Si en el futur Pionex canvia comportament i resetea el counter,
    caldrà tornar a afegir SUM(withdraw_profit) — detectable per un
    drop sobtat de current_grid_profit entre snapshots.
    """
    with conn() as c, c.cursor() as cur:
        # Sum grid_profit_at_close dels ancestors via bot_lineage (closed epochs)
        cur.execute("""
            WITH RECURSIVE ancestors(parent_id, depth) AS (
                SELECT bl.parent_bot_id, 1
                FROM bot_lineage bl WHERE bl.child_bot_id = %s
                UNION ALL
                SELECT bl.parent_bot_id, a.depth + 1
                FROM bot_lineage bl
                JOIN ancestors a ON bl.child_bot_id = a.parent_id
                WHERE a.depth < 10
            )
            SELECT COALESCE(SUM(be.grid_profit_at_close), 0)::float
            FROM ancestors a
            JOIN bot_epochs be ON be.bot_id = a.parent_id AND be.closed_at IS NOT NULL
        """, (bot_id,))
        ancestor_profit = float(cur.fetchone()[0] or 0)
    return current_grid_profit + ancestor_profit


def _get_last_snapshot_state(bot_id: str) -> dict | None:
    """Llegeix darrer state del daily_snapshots avui per detectar canvis."""
    today = date.today()
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute(
                """SELECT cycles_total, gross_grid_profit::float, invested_capital::float,
                          avg_cost::float, base_amount::float
                   FROM daily_snapshots WHERE bot_id=%s AND date=%s""",
                (bot_id, today),
            )
            row = cur.fetchone()
            if row:
                return {
                    "cycles_total": int(row[0] or 0),
                    "grid_profit": float(row[1] or 0),
                    "qti": float(row[2] or 0),
                    "avg_cost": float(row[3] or 0),
                    "base_amount": float(row[4] or 0),
                }
    except Exception:
        pass
    return None


def _has_significant_change(prev: dict | None, current: dict) -> tuple[bool, str]:
    """Detecta si el bot state ha canviat de manera significativa.
    Retorna (changed, reason)."""
    if prev is None:
        return True, "first_snapshot_today"
    if current["cycles_total"] != prev["cycles_total"]:
        return True, f"cycle_completed ({prev['cycles_total']}->{current['cycles_total']})"
    if abs(current["grid_profit"] - prev["grid_profit"]) > 0.001:
        return True, f"grid_profit_changed (Δ${current['grid_profit'] - prev['grid_profit']:+.4f})"
    if abs(current["qti"] - prev["qti"]) > 0.01:
        return True, f"qti_changed (Δ${current['qti'] - prev['qti']:+.2f})"
    if abs(current["avg_cost"] - prev["avg_cost"]) > 0.01:
        return True, f"avg_cost_changed (Δ${current['avg_cost'] - prev['avg_cost']:+.4f})"
    return False, "no_change"


def snapshot_all_bots(force: bool = False) -> int:
    """Event-driven: NOMÉS escriu a Neon quan hi ha canvi significatiu al bot
    (cycle completat, gridProfit canviat, qti canviat, avg_cost canviat).

    Si force=True (cron daily 01:55), sempre escriu (per garantir 1 row/dia/bot).

    Retorna: nombre de bots amb canvis detectats + escrits.
    """
    today = date.today()
    saved = 0
    for name, cfg in BOTS.items():
        try:
            s = get_bot_range(cfg["id"], symbol=cfg["symbol"])
            top = float(s.get("top") or 0)
            bottom = float(s.get("bottom") or 0)
            if top <= 0 or bottom <= 0 or top <= bottom:
                continue

            gross_gp = float(s.get("grid_profit", 0))
            lifetime = _calc_lifetime_profit(cfg["id"], gross_gp)
            quote_v = float(s.get("quote_in_bot", 0))
            base_amt = float(s.get("base_in_bot", 0))
            price = float(s.get("price", 0))
            base_v = base_amt * price
            invested = float(s.get("quote_total_investment") or s.get("usdt_investment") or 0)
            avg_c = float(s.get("avg_cost", 0))

            current_state = {
                "cycles_total": int(s.get("paired_cycles", 0)),
                "grid_profit": gross_gp,
                "qti": invested,
                "avg_cost": avg_c,
                "base_amount": base_amt,
            }
            prev_state = _get_last_snapshot_state(cfg["id"])
            changed, reason = _has_significant_change(prev_state, current_state)

            # daily_snapshots: UPSERT INCONDICIONAL (1 row/dia/bot, no creix DB)
            # Aixo manté els valors live (price, value, cycles) sempre frescos per al dashboard.
            upsert_daily_snapshot(
                date_=today, bot_id=cfg["id"], bot_name=name,
                gross_grid_profit=gross_gp, cum_lifetime_profit=lifetime,
                invested_capital=invested, base_amount=base_amt,
                base_value_usdt=base_v, quote_value_usdt=quote_v,
                current_value_total=quote_v + base_v,
                cycles_completed_today=0,
                cycles_total=int(s.get("paired_cycles", 0)),
                price_close=price, top=top, bottom=bottom,
                avg_cost=avg_c,
                grid_avg_open_price=float(s.get("grid_avg_open_price", 0)),
                break_even_price=float(s.get("break_even_price", 0)),
            )
            saved += 1
            # event_snapshots: NOMES quan hi ha canvi significatiu (event-driven)
            if changed or force:
                try:
                    snapshot_bot_state(
                        bot_id=cfg["id"], bot_name=name,
                        event_type="cycle_or_state_change", source="sync_health",
                        price=price,
                        notes=f"{reason}",
                    )
                except Exception:
                    pass
                log.info(f"[{name}] STATE CHANGED ({reason}): value={quote_v+base_v:.2f} lifetime={lifetime:.4f}")
        except Exception as e:
            log.error(f"[{name}] snapshot failed: {e}")
    return saved


def main_cron_force():
    """Cron diari 01:55 UTC — força 1 snapshot/bot/dia per garantir granularitat diaria."""
    log.info(f"Daily forced snapshot {date.today()}")
    saved = snapshot_all_bots(force=True)
    log.info(f"Forced snapshots saved: {saved}/{len(BOTS)}")


def main():
    log.info(f"Daily snapshot per a {date.today()} ({len(BOTS)} bots actius)")
    saved = snapshot_all_bots()
    log.info(f"Saved {saved}/{len(BOTS)} snapshots")

    # ─── Wallet balance snapshot (per al dashboard 'Reserva del sistema') ──
    try:
        bal = get_balance() or {}
        # Preus per a convertir a USDT
        btc_price = 0.0
        try:
            btc_price = float(get_current_price("BTC_USDT"))
        except Exception:
            pass
        for coin, free_amt in bal.items():
            free_f = float(free_amt or 0)
            value = free_f
            if coin == "BTC":
                value = free_f * btc_price
            elif coin not in ("USDT", "USDC"):
                # Per la resta de coins, valor aproximat = 0 (no ho fem servir activament)
                value = 0.0
            log_wallet_snapshot(coin=coin, free=free_f, value_usdt=value, source="daily_snapshot")
        log.info(f"Wallet snapshot OK ({len(bal)} coins)")
    except Exception as e:
        log.error(f"Wallet snapshot failed: {e}")


if __name__ == "__main__":
    main()
