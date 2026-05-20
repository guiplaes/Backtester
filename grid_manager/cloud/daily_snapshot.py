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

from cloud.db_cloud import upsert_daily_snapshot, conn, log_wallet_snapshot
from config import BOTS
from pionex_client import get_bot_range, get_balance, get_current_price

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("daily_snapshot")


def _calc_lifetime_profit(bot_id: str, current_grid_profit: float) -> float:
    """Lifetime profit acumulat REAL d'un bot, preservat encara que Pionex reseteja.

    Quan extreiem profit via /spotGrid/profit, Pionex baixa el seu gridProfit
    a 0. Nosaltres registrem un 'withdraw_profit' event a capital_events.

    Lifetime = SUM(withdraw_profit events del bot) + current Pionex gridProfit.

    Així el comptador nostre creix monotonicament, fins i tot després d'extraccions
    setmanals. La història completa queda guardada a capital_events.
    """
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            SELECT COALESCE(SUM(amount_usdt), 0)::float
            FROM capital_events
            WHERE bot_id = %s
              AND event_type = 'withdraw_profit'
              AND success = TRUE
        """, (bot_id,))
        prev_withdrawn = float(cur.fetchone()[0] or 0)
    return prev_withdrawn + current_grid_profit


def snapshot_all_bots() -> int:
    """Recull snapshot de TOTS els bots i fa UPSERT a daily_snapshots
    (mateixa data → actualitza els camps en lloc de crear fila nova).
    Cridable des de daily_snapshot.py o sync_health.py.
    Retorna: nombre de bots amb snapshot OK.
    """
    today = date.today()
    saved = 0
    for name, cfg in BOTS.items():
        try:
            s = get_bot_range(cfg["id"], symbol=cfg["symbol"])
            top = float(s.get("top") or 0)
            bottom = float(s.get("bottom") or 0)
            if top <= 0 or bottom <= 0 or top <= bottom:
                log.warning(f"[{name}] estat invàlid, skip snapshot")
                continue

            gross_gp = float(s.get("grid_profit", 0))
            lifetime = _calc_lifetime_profit(cfg["id"], gross_gp)
            quote_v = float(s.get("quote_in_bot", 0))
            base_amt = float(s.get("base_in_bot", 0))
            price = float(s.get("price", 0))
            base_v = base_amt * price
            # Usar quote_total_investment (current, inclou rebalances)
            # i caure a usdt_investment (initial) si no està disponible
            invested = float(s.get("quote_total_investment") or s.get("usdt_investment") or 0)

            upsert_daily_snapshot(
                date_=today,
                bot_id=cfg["id"], bot_name=name,
                gross_grid_profit=gross_gp,
                cum_lifetime_profit=lifetime,
                invested_capital=invested,
                base_amount=base_amt,
                base_value_usdt=base_v,
                quote_value_usdt=quote_v,
                current_value_total=quote_v + base_v,
                cycles_completed_today=0,
                cycles_total=int(s.get("paired_cycles", 0)),
                price_close=price, top=top, bottom=bottom,
            )
            saved += 1
            log.info(f"[{name}] snapshot OK: value={quote_v+base_v:.2f} lifetime={lifetime:.4f}")
        except Exception as e:
            log.error(f"[{name}] snapshot failed: {e}")
    return saved


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
