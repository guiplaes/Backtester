"""
db_cloud.py — Accés a Neon Postgres per a Grid Portfolio.

Filosofia:
  - Tota mutació de capital (invest_in, reduce, reinvest) ha de quedar registrada
    a `capital_events` amb verificació empírica qti_before/qti_after.
  - Recolocations a la taula `recolocations` (mai més tornem a perdre dades de
    cost de recolocació com fins ara).
  - Daily snapshots a `daily_snapshots` per a evolució temporal.
  - Idempotency keys per evitar duplicats si un cron es dispara dues vegades.

L'esquema està a `cloud/schema.sql`. Migracions futures: incrementar la versió i afegir
nou bloc DDL idempotent al SQL.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

# Carrega .env robustament: prova path local, després el main project conegut.
# Cal que TOT script que importi db_cloud tingui DATABASE_URL set, sigui des de cron,
# manual o worktree. Si no, FAIL FAST al import per evitar operacions silenciosament desconnectades.
def _load_env_robust() -> None:
    if os.environ.get("DATABASE_URL"):
        return
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    candidates = [
        Path(__file__).resolve().parent.parent / ".env",
        Path(r"C:\Users\Administrator\Desktop\MT4 Claude\grid_manager") / ".env",
        Path.home() / "grid_manager" / ".env",
    ]
    for p in candidates:
        if p.exists():
            load_dotenv(p)
            if os.environ.get("DATABASE_URL"):
                return


_load_env_robust()

log = logging.getLogger("db_cloud")

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL no configurat. Revisa grid_manager/.env (o variable d'entorn). "
        "El sistema necessita Neon com a font autoritzativa — no es pot operar sense connexió."
    )
_POOL: ConnectionPool | None = None


def _get_pool() -> ConnectionPool:
    """Pool de connexions singleton. min=1, max=4 per evitar saturar Neon free tier."""
    global _POOL
    if _POOL is None:
        if not DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL no configurat. Revisa grid_manager/.env"
            )
        _POOL = ConnectionPool(
            DATABASE_URL,
            min_size=1,
            max_size=4,
            timeout=30,
            kwargs={"autocommit": False},
        )
    return _POOL


@contextmanager
def conn(autocommit: bool = False):
    """Context manager amb una connexió del pool."""
    pool = _get_pool()
    with pool.connection() as c:
        if autocommit:
            c.autocommit = True
        yield c


# ═══════════════════════════════════════════════════════════════════════
# Bots master
# ═══════════════════════════════════════════════════════════════════════
def upsert_bot(bot_id: str, name: str, base: str, quote: str,
               pionex_strategy_id: int | None = None,
               created_at: datetime | None = None,
               status: str = "running",
               notes: str | None = None) -> None:
    """Crea o actualitza un bot al master."""
    if created_at is None:
        created_at = datetime.now(timezone.utc)
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            INSERT INTO bots (bot_id, name, base, quote, pionex_strategy_id, created_at, status, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (bot_id) DO UPDATE SET
                name = EXCLUDED.name,
                base = EXCLUDED.base,
                quote = EXCLUDED.quote,
                pionex_strategy_id = COALESCE(EXCLUDED.pionex_strategy_id, bots.pionex_strategy_id),
                status = EXCLUDED.status,
                notes = COALESCE(EXCLUDED.notes, bots.notes)
        """, (bot_id, name, base, quote, pionex_strategy_id, created_at, status, notes))
        c.commit()


def mark_bot_closed(bot_id: str, closed_at: datetime | None = None) -> None:
    if closed_at is None:
        closed_at = datetime.now(timezone.utc)
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "UPDATE bots SET status='closed', closed_at=%s WHERE bot_id=%s",
            (closed_at, bot_id),
        )
        c.commit()


# ═══════════════════════════════════════════════════════════════════════
# Resiliència universal: si la inserció a Neon falla per qualsevol motiu
# (transient ImportError, network, lock, etc.) ABANS de propagar l'error,
# escribim el payload sencer a logs/pending_<entity>.jsonl. El sync_health
# llegirà aquests fitxers cada minut i farà retry. Així NO PERDEM mai
# cap dada per fallades transitòries.
# ═══════════════════════════════════════════════════════════════════════
def _dump_pending(entity: str, payload: dict) -> None:
    """Volca un payload fallit a logs/pending_<entity>.jsonl per a backfill posterior."""
    try:
        from config import LOG_DIR as _LOG_DIR
        path = _LOG_DIR / f"pending_{entity}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Datetime → ISO string, tot la resta JSON-friendly
        def _ser(o):
            if isinstance(o, datetime):
                return o.isoformat()
            if isinstance(o, date):
                return o.isoformat()
            return str(o)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, default=_ser) + "\n")
    except Exception as e:
        log.error(f"_dump_pending({entity}) ALSO FAILED: {e}")


# ═══════════════════════════════════════════════════════════════════════
# Capital events: la peça més important del sistema
# ═══════════════════════════════════════════════════════════════════════
def log_capital_event(
    *,
    bot_id: str,
    bot_name: str,
    event_type: str,
    amount_usdt: float,
    source: str,
    qti_before: float | None = None,
    qti_after: float | None = None,
    grid_profit_snapshot: float | None = None,
    lifetime_profit_calc: float | None = None,
    idempotency_key: str | None = None,
    success: bool = True,
    error_msg: str | None = None,
    raw_response: dict | None = None,
    notes: str | None = None,
    created_by: str | None = None,
    ts: datetime | None = None,
) -> int | None:
    """Registra un capital event. Si idempotency_key duplica, no fa res (retorna None).
    Retorna l'id del registre creat, o None si ja existia."""
    if ts is None:
        ts = datetime.now(timezone.utc)
    raw_json = json.dumps(raw_response) if raw_response is not None else None
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute("""
                INSERT INTO capital_events
                (ts, bot_id, bot_name, event_type, amount_usdt,
                 qti_before, qti_after, grid_profit_snapshot, lifetime_profit_calc,
                 source, idempotency_key, success, error_msg, raw_response, notes, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING id
            """, (
                ts, bot_id, bot_name, event_type, amount_usdt,
                qti_before, qti_after, grid_profit_snapshot, lifetime_profit_calc,
                source, idempotency_key, success, error_msg, raw_json, notes, created_by,
            ))
            row = cur.fetchone()
            c.commit()
            return row[0] if row else None
    except Exception as e:
        log.error(f"log_capital_event failed (will dump to JSONL): {e}")
        _dump_pending("capital_events", {
            "ts": ts, "bot_id": bot_id, "bot_name": bot_name, "event_type": event_type,
            "amount_usdt": amount_usdt, "qti_before": qti_before, "qti_after": qti_after,
            "grid_profit_snapshot": grid_profit_snapshot, "lifetime_profit_calc": lifetime_profit_calc,
            "source": source, "idempotency_key": idempotency_key, "success": success,
            "error_msg": error_msg, "raw_response": raw_response, "notes": notes,
            "created_by": created_by, "_neon_error": str(e)[:200],
        })
        return None


def get_lifetime_profit(bot_id: str) -> float:
    """Suma de gridProfit acumulat lifetime per a un bot.
    Calculat a partir dels capital_events: lifetime_profit_calc del més recent
    + delta des de llavors fins ara (que es queda dins de grid_profit_snapshot
    a la pròxima daily_snapshot).
    """
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            SELECT lifetime_profit_calc
            FROM capital_events
            WHERE bot_id = %s AND lifetime_profit_calc IS NOT NULL
            ORDER BY ts DESC LIMIT 1
        """, (bot_id,))
        row = cur.fetchone()
        return float(row[0]) if row else 0.0


# ═══════════════════════════════════════════════════════════════════════
# Bot epochs
# ═══════════════════════════════════════════════════════════════════════
def open_epoch(bot_id: str, initial_capital_usdt: float, opened_at: datetime | None = None,
               initial_top: float | None = None, initial_bottom: float | None = None,
               initial_rows: int | None = None, notes: str | None = None) -> int:
    """Obre un nou epoch per al bot. Retorna epoch_id."""
    if opened_at is None:
        opened_at = datetime.now(timezone.utc)
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(MAX(epoch_num), 0) + 1 FROM bot_epochs WHERE bot_id=%s",
            (bot_id,),
        )
        epoch_num = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO bot_epochs (bot_id, epoch_num, opened_at, initial_capital_usdt,
                                    initial_top, initial_bottom, initial_rows, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING epoch_id
        """, (bot_id, epoch_num, opened_at, initial_capital_usdt,
              initial_top, initial_bottom, initial_rows, notes))
        eid = cur.fetchone()[0]
        c.commit()
        return eid


def close_epoch(bot_id: str, *, final_capital_usdt: float = 0,
                cycles_completed: int = 0, grid_profit_at_close: float = 0,
                realized_profit_at_close: float = 0, true_net_pnl: float = 0,
                closed_at: datetime | None = None, notes: str | None = None) -> None:
    if closed_at is None:
        closed_at = datetime.now(timezone.utc)
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            UPDATE bot_epochs SET
                closed_at=%s, final_capital_usdt=%s, cycles_completed=%s,
                grid_profit_at_close=%s, realized_profit_at_close=%s,
                true_net_pnl=%s, notes=COALESCE(%s, notes)
            WHERE bot_id=%s AND closed_at IS NULL
        """, (closed_at, final_capital_usdt, cycles_completed,
              grid_profit_at_close, realized_profit_at_close, true_net_pnl, notes, bot_id))
        c.commit()


def get_open_epoch_id(bot_id: str) -> int | None:
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT epoch_id FROM bot_epochs WHERE bot_id=%s AND closed_at IS NULL ORDER BY epoch_id DESC LIMIT 1",
            (bot_id,),
        )
        row = cur.fetchone()
        return row[0] if row else None


# ═══════════════════════════════════════════════════════════════════════
# Recolocations
# ═══════════════════════════════════════════════════════════════════════
def log_recolocation(
    *,
    bot_id: str,
    bot_name: str,
    trigger: str,
    price_at_trigger: float,
    old_top: float, old_bottom: float,
    new_top: float, new_bottom: float,
    grid_profit_before: float = 0,
    grid_profit_after: float | None = None,
    fee_consumed_before: float = 0,
    fee_consumed_after: float | None = None,
    cost_usdt: float = 0,
    executed: bool = True,
    action_taken: str = "adjust_params_ok",
    error_msg: str | None = None,
    idempotency_key: str | None = None,
    ts: datetime | None = None,
) -> int | None:
    if ts is None:
        ts = datetime.now(timezone.utc)
    epoch_id = get_open_epoch_id(bot_id)
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute("""
                INSERT INTO recolocations
                (ts, bot_id, bot_name, epoch_id, trigger, price_at_trigger,
                 old_top, old_bottom, new_top, new_bottom,
                 grid_profit_before, grid_profit_after,
                 fee_consumed_before, fee_consumed_after,
                 cost_usdt, executed, action_taken, error_msg, idempotency_key)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING id
            """, (
                ts, bot_id, bot_name, epoch_id, trigger, price_at_trigger,
                old_top, old_bottom, new_top, new_bottom,
                grid_profit_before, grid_profit_after,
                fee_consumed_before, fee_consumed_after,
                cost_usdt, executed, action_taken, error_msg, idempotency_key,
            ))
            row = cur.fetchone()
            c.commit()
            return row[0] if row else None
    except Exception as e:
        log.error(f"log_recolocation failed (will dump to JSONL): {e}")
        _dump_pending("recolocations", {
            "ts": ts, "bot_id": bot_id, "bot_name": bot_name, "trigger": trigger,
            "price_at_trigger": price_at_trigger,
            "old_top": old_top, "old_bottom": old_bottom,
            "new_top": new_top, "new_bottom": new_bottom,
            "grid_profit_before": grid_profit_before, "grid_profit_after": grid_profit_after,
            "fee_consumed_before": fee_consumed_before, "fee_consumed_after": fee_consumed_after,
            "cost_usdt": cost_usdt, "executed": executed,
            "action_taken": action_taken, "error_msg": error_msg,
            "idempotency_key": idempotency_key, "_neon_error": str(e)[:200],
        })
        return None


# ═══════════════════════════════════════════════════════════════════════
# Daily snapshots
# ═══════════════════════════════════════════════════════════════════════
def upsert_daily_snapshot(
    *,
    date_: date,
    bot_id: str,
    bot_name: str,
    gross_grid_profit: float = 0,
    cum_lifetime_profit: float = 0,
    invested_capital: float = 0,
    base_amount: float = 0,
    base_value_usdt: float = 0,
    quote_value_usdt: float = 0,
    current_value_total: float = 0,
    cycles_completed_today: int = 0,
    cycles_total: int = 0,
    price_close: float = 0,
    top: float = 0,
    bottom: float = 0,
    avg_cost: float = 0,
    grid_avg_open_price: float = 0,
    break_even_price: float = 0,
) -> None:
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute("""
                INSERT INTO daily_snapshots
                (date, bot_id, bot_name, gross_grid_profit, cum_lifetime_profit,
                 invested_capital, base_amount, base_value_usdt, quote_value_usdt,
                 current_value_total, cycles_completed_today, cycles_total,
                 price_close, top, bottom,
                 avg_cost, grid_avg_open_price, break_even_price)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (date, bot_id) DO UPDATE SET
                    gross_grid_profit = EXCLUDED.gross_grid_profit,
                    cum_lifetime_profit = EXCLUDED.cum_lifetime_profit,
                    invested_capital = EXCLUDED.invested_capital,
                    base_amount = EXCLUDED.base_amount,
                    base_value_usdt = EXCLUDED.base_value_usdt,
                    quote_value_usdt = EXCLUDED.quote_value_usdt,
                    current_value_total = EXCLUDED.current_value_total,
                    cycles_completed_today = EXCLUDED.cycles_completed_today,
                    cycles_total = EXCLUDED.cycles_total,
                    price_close = EXCLUDED.price_close,
                    top = EXCLUDED.top,
                    bottom = EXCLUDED.bottom,
                    avg_cost = EXCLUDED.avg_cost,
                    grid_avg_open_price = EXCLUDED.grid_avg_open_price,
                    break_even_price = EXCLUDED.break_even_price
            """, (
                date_, bot_id, bot_name, gross_grid_profit, cum_lifetime_profit,
                invested_capital, base_amount, base_value_usdt, quote_value_usdt,
                current_value_total, cycles_completed_today, cycles_total,
                price_close, top, bottom,
                avg_cost, grid_avg_open_price, break_even_price,
            ))
            c.commit()
    except Exception as e:
        log.error(f"upsert_daily_snapshot failed (will dump to JSONL): {e}")
        _dump_pending("daily_snapshots", {
            "date_": date_, "bot_id": bot_id, "bot_name": bot_name,
            "gross_grid_profit": gross_grid_profit, "cum_lifetime_profit": cum_lifetime_profit,
            "invested_capital": invested_capital, "base_amount": base_amount,
            "base_value_usdt": base_value_usdt, "quote_value_usdt": quote_value_usdt,
            "current_value_total": current_value_total,
            "cycles_completed_today": cycles_completed_today, "cycles_total": cycles_total,
            "price_close": price_close, "top": top, "bottom": bottom,
            "avg_cost": avg_cost, "grid_avg_open_price": grid_avg_open_price,
            "break_even_price": break_even_price, "_neon_error": str(e)[:200],
        })


# ═══════════════════════════════════════════════════════════════════════
# Reconciliation
# ═══════════════════════════════════════════════════════════════════════
def log_reconciliation(
    *,
    bot_id: str,
    pionex_grid_profit: float,
    pionex_quote_invested: float,
    our_lifetime_profit: float,
    our_capital_invested: float,
    severity: str = "ok",
    notes: str | None = None,
) -> None:
    pdiscr = pionex_grid_profit - our_lifetime_profit
    cdiscr = pionex_quote_invested - our_capital_invested
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            INSERT INTO reconciliation_log
            (bot_id, pionex_grid_profit, pionex_quote_invested,
             our_lifetime_profit, our_capital_invested,
             profit_discrepancy, capital_discrepancy, severity, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (bot_id, pionex_grid_profit, pionex_quote_invested,
              our_lifetime_profit, our_capital_invested,
              pdiscr, cdiscr, severity, notes))
        c.commit()


# ═══════════════════════════════════════════════════════════════════════
# B1: Wallet snapshots
# ═══════════════════════════════════════════════════════════════════════
def log_wallet_snapshot(coin: str, free: float, frozen: float = 0,
                        value_usdt: float | None = None,
                        source: str = "daily_snapshot",
                        ts: datetime | None = None) -> None:
    if ts is None: ts = datetime.now(timezone.utc)
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute("""
                INSERT INTO wallet_snapshots (ts, coin, free, frozen, value_usdt, source)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (ts, coin, free, frozen, value_usdt, source))
            c.commit()
    except Exception as e:
        log.error(f"log_wallet_snapshot failed (will dump to JSONL): {e}")
        _dump_pending("wallet_snapshots", {
            "ts": ts, "coin": coin, "free": free, "frozen": frozen,
            "value_usdt": value_usdt, "source": source, "_neon_error": str(e)[:200],
        })


# ═══════════════════════════════════════════════════════════════════════
# B2: System health (heartbeat)
# ═══════════════════════════════════════════════════════════════════════
def log_system_health(*, component: str, status: str,
                      last_cycle_ms: int | None = None,
                      triggers_today: int = 0,
                      adjusts_ok_today: int = 0,
                      adjusts_fail_today: int = 0,
                      last_trigger_text: str | None = None,
                      last_adjust_text: str | None = None,
                      error_msg: str | None = None,
                      extra: dict | None = None) -> None:
    extra_json = json.dumps(extra) if extra is not None else None
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute("""
                INSERT INTO system_health
                (component, status, last_cycle_ms, triggers_today, adjusts_ok_today,
                 adjusts_fail_today, last_trigger_text, last_adjust_text, error_msg, extra)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (component, status, last_cycle_ms, triggers_today,
                  adjusts_ok_today, adjusts_fail_today,
                  last_trigger_text, last_adjust_text, error_msg, extra_json))
            c.commit()
    except Exception as e:
        log.error(f"log_system_health failed (will dump to JSONL): {e}")
        _dump_pending("system_health", {
            "component": component, "status": status, "last_cycle_ms": last_cycle_ms,
            "triggers_today": triggers_today, "adjusts_ok_today": adjusts_ok_today,
            "adjusts_fail_today": adjusts_fail_today,
            "last_trigger_text": last_trigger_text, "last_adjust_text": last_adjust_text,
            "error_msg": error_msg, "extra": extra, "_neon_error": str(e)[:200],
        })


# ═══════════════════════════════════════════════════════════════════════
# B3: Monitor log (últimes línies sincronitzades)
# ═══════════════════════════════════════════════════════════════════════
def log_monitor_lines(lines: list[tuple]) -> None:
    """lines: list of (ts, level, component, message). Bulk insert."""
    if not lines: return
    try:
        with conn() as c, c.cursor() as cur:
            cur.executemany("""
                INSERT INTO monitor_log (ts, level, component, message)
                VALUES (%s, %s, %s, %s)
            """, lines)
            c.commit()
    except Exception as e:
        log.error(f"log_monitor_lines failed (will dump to JSONL): {e}")
        for ts, level, component, message in lines:
            _dump_pending("monitor_log", {
                "ts": ts, "level": level, "component": component, "message": message,
                "_neon_error": str(e)[:200],
            })


def trim_monitor_log(max_rows: int = 500) -> int:
    """Mantenir només les últimes N files al monitor_log."""
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            DELETE FROM monitor_log
            WHERE id NOT IN (
                SELECT id FROM monitor_log ORDER BY ts DESC LIMIT %s
            )
        """, (max_rows,))
        deleted = cur.rowcount
        c.commit()
        return deleted


# ═══════════════════════════════════════════════════════════════════════
# B4: Price snapshots
# ═══════════════════════════════════════════════════════════════════════
def log_price_snapshot(symbol: str, price: float,
                       bot_top: float | None = None,
                       bot_bottom: float | None = None,
                       ts: datetime | None = None) -> None:
    if ts is None: ts = datetime.now(timezone.utc)
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            INSERT INTO price_snapshots (ts, symbol, price, bot_top, bot_bottom)
            VALUES (%s, %s, %s, %s, %s)
        """, (ts, symbol, price, bot_top, bot_bottom))
        c.commit()


def trim_price_snapshots(days_to_keep: int = 60) -> int:
    """Eliminar snapshots > N dies (per evitar acumulació)."""
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            DELETE FROM price_snapshots
            WHERE ts < NOW() - INTERVAL '%s days'
        """ % int(days_to_keep))
        deleted = cur.rowcount
        c.commit()
        return deleted


# ═══════════════════════════════════════════════════════════════════════
# MT5 state
# ═══════════════════════════════════════════════════════════════════════
def log_mt5_state(*, available: bool, equity: float | None = None,
                  balance: float | None = None, floating_pnl: float | None = None,
                  daily_profit: float | None = None, cum_profit: float | None = None,
                  open_positions: int | None = None, raw_json: dict | None = None) -> None:
    raw = json.dumps(raw_json) if raw_json else None
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            INSERT INTO mt5_state
            (available, equity, balance, floating_pnl, daily_profit, cum_profit, open_positions, raw_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (available, equity, balance, floating_pnl, daily_profit, cum_profit, open_positions, raw))
        c.commit()


# ═══════════════════════════════════════════════════════════════════════
# v3: grid_trades, operation_log, cron_runs, bot_lifecycle_events
# ═══════════════════════════════════════════════════════════════════════
def log_grid_trade(*, pionex_fill_id: int, pionex_order_id: int,
                   bot_id: str | None, bot_name: str | None,
                   symbol: str, side: str, role: str | None,
                   price: float, size: float, fee: float,
                   fee_coin: str | None, ts: datetime) -> int | None:
    """Insert d'un fill. Idempotent per pionex_fill_id."""
    quote_value = price * size
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            INSERT INTO grid_trades
              (pionex_fill_id, pionex_order_id, bot_id, bot_name, symbol,
               side, role, price, size, quote_value, fee, fee_coin, ts)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (pionex_fill_id) DO NOTHING
            RETURNING id
        """, (pionex_fill_id, pionex_order_id, bot_id, bot_name, symbol,
              side, role, price, size, quote_value, fee, fee_coin, ts))
        row = cur.fetchone()
        c.commit()
        return row[0] if row else None


def log_operation(*, component: str, operation: str,
                  status: str = "success",
                  bot_id: str | None = None, bot_name: str | None = None,
                  duration_ms: int | None = None,
                  details: dict | None = None,
                  error_msg: str | None = None) -> None:
    """Registra una acció del sistema (rebalance, recolocation, snapshot, etc)."""
    details_json = json.dumps(details) if details is not None else None
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            INSERT INTO operation_log
              (component, operation, bot_id, bot_name, status,
               duration_ms, details, error_msg)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (component, operation, bot_id, bot_name, status,
              duration_ms, details_json, error_msg))
        c.commit()


@contextmanager
def cron_run(task_name: str):
    """Context manager per registrar inici/fi d'un cron + duració.
    Ús:
        with cron_run('sync_health') as ctx:
            ctx['items'] = 6
            ctx['notes'] = 'all OK'
    """
    started = datetime.now(timezone.utc)
    ctx = {"items": 0, "notes": None, "status": "success", "error": None}
    try:
        yield ctx
    except Exception as e:
        ctx["status"] = "failed"
        ctx["error"] = str(e)[:500]
        raise
    finally:
        finished = datetime.now(timezone.utc)
        duration_ms = int((finished - started).total_seconds() * 1000)
        try:
            with conn() as c, c.cursor() as cur:
                cur.execute("""
                    INSERT INTO cron_runs
                      (task_name, started_at, finished_at, duration_ms,
                       status, items_processed, notes, error_msg)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (task_name, started, finished, duration_ms,
                      ctx["status"], ctx["items"], ctx["notes"], ctx["error"]))
                c.commit()
        except Exception as e:
            log.warning(f"cron_runs insert failed: {e}")


def log_bot_lifecycle(*, bot_id: str, bot_name: str | None,
                      event_type: str,
                      last_grid_profit: float | None = None,
                      last_quote_invested: float | None = None,
                      notes: str | None = None,
                      detected_by: str = "sync_health") -> None:
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            INSERT INTO bot_lifecycle_events
              (bot_id, bot_name, event_type, last_grid_profit,
               last_quote_invested, notes, detected_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (bot_id, bot_name, event_type, last_grid_profit,
              last_quote_invested, notes, detected_by))
        c.commit()


# ═══════════════════════════════════════════════════════════════════════
# Event snapshots — captura estat del bot a cada esdeveniment significatiu
# ═══════════════════════════════════════════════════════════════════════
def snapshot_bot_state(
    *,
    bot_id: str,
    bot_name: str,
    event_type: str,
    source: str,
    event_ref: str | None = None,
    notes: str | None = None,
    pionex_data: dict | None = None,
    price: float | None = None,
    ts: datetime | None = None,
) -> int | None:
    """Insert d'un event_snapshot. Si pionex_data no es proveït, el llegeix.

    Cap excepció es propaga — si falla, log + return None. NO bloca operacions
    financeres. El reconcile diari detectarà gaps.
    """
    if ts is None:
        ts = datetime.now(timezone.utc)
    try:
        # Lazy import per evitar cicles
        if pionex_data is None:
            from pionex_client import get_bot_order
            try:
                pionex_data = get_bot_order(bot_id)
            except Exception as e:
                log.warning(f"snapshot_bot_state: no he pogut llegir Pionex bot {bot_id}: {e}")
                return None

        bu = (pionex_data or {}).get("buOrderData", {})
        invested = float(bu.get("quoteTotalInvestment", 0) or 0)
        gp = float(bu.get("gridProfit", 0) or 0)
        pw = float(bu.get("profitWithdrawnUsdt", 0) or 0)
        # UNIFICAT (2026-05-24): cycles_total = exchangeOrderPairedCount
        # (round-trips buy+sell complets). Abans usàvem closedExchangeOrderCount
        # (orders individuals — buy O sell — i comptava el doble).
        # event_snapshots.cycles_paired manté també paired (legacy compat).
        cycles = int(bu.get("exchangeOrderPairedCount", 0) or 0)
        cycles_p = int(bu.get("exchangeOrderPairedCount", 0) or 0)
        # `closedExchangeOrderCount` (individual orders) NO es guarda més com
        # a cycles_total per evitar confusió. Si cal en el futur, afegir camp
        # explícit `total_trades`.
        base_amt = float(bu.get("baseAmount", 0) or 0)
        quote_amt = float(bu.get("quoteAmount", 0) or 0)
        top = float(bu.get("top", 0) or 0)
        bot_lo = float(bu.get("bottom", 0) or 0)
        rows = int(bu.get("row", 0) or 0)
        qfr = float(bu.get("quoteFeeReserve", 0) or 0)
        bfr = float(bu.get("baseFeeReserve", 0) or 0)
        avg_c = float(bu.get("averageCost", 0) or 0)
        grid_avg = float(bu.get("gridAverageOpenPrice", 0) or 0)
        be = float(bu.get("breakEvenWithGridProfit", 0) or 0)

        # Resol preu si no s'ha donat
        if price is None:
            try:
                from pionex_client import get_current_price
                # Derive symbol from buOrderData o bot row
                symbol = f"{pionex_data.get('base','')}_{pionex_data.get('quote','')}"
                if "_" in symbol and len(symbol) > 1:
                    price = float(get_current_price(symbol))
            except Exception:
                price = None
        if price is None:
            price = 0.0
        base_val = base_amt * price
        total_val = base_val + quote_amt

        raw_json = json.dumps(pionex_data) if pionex_data else None

        with conn() as c, c.cursor() as cur:
            cur.execute("""
                INSERT INTO event_snapshots
                    (ts, bot_id, bot_name, event_type, event_ref,
                     invested_capital, grid_profit, profit_withdrawn,
                     cycles_total, cycles_paired,
                     base_amount, quote_amount, base_value_usdt, current_value_total,
                     price, grid_top, grid_bottom, grid_rows,
                     quote_fee_reserve, base_fee_reserve,
                     avg_cost, grid_avg_open_price, break_even_price,
                     source, notes, raw_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                ts, bot_id, bot_name, event_type, event_ref,
                invested, gp, pw,
                cycles, cycles_p,
                base_amt, quote_amt, base_val, total_val,
                price, top, bot_lo, rows,
                qfr, bfr,
                avg_c, grid_avg, be,
                source, notes, raw_json,
            ))
            new_id = cur.fetchone()[0]
            c.commit()

        # Dispatch supervisor Claude per events significatius (async, no bloca)
        try:
            from cloud.supervisor import supervise_event, SIGNIFICANT_EVENTS
            if event_type in SIGNIFICANT_EVENTS:
                supervise_event(bot_name=bot_name, event_type=event_type, snapshot_id=new_id)
        except Exception as _e_sup:
            log.warning(f"supervisor dispatch failed (best-effort): {_e_sup}")

        return new_id
    except Exception as e:
        log.error(f"snapshot_bot_state failed (will dump to JSONL): {e}")
        # Snapshot d'estat al moment d'event significatiu — NO l'hem de perdre
        try:
            _dump_pending("event_snapshots", {
                "ts": ts, "bot_id": bot_id, "bot_name": bot_name,
                "event_type": event_type, "event_ref": event_ref,
                "source": source, "notes": notes,
                "price": price,
                "pionex_data": pionex_data,  # raw API response, suficient per reconstruir
                "_neon_error": str(e)[:200],
            })
        except Exception as _e_dump:
            log.error(f"event_snapshots dump ALSO failed: {_e_dump}")
        return None


# ═══════════════════════════════════════════════════════════════════════
# Vistes consultes per al dashboard — Neon és font autoritzativa
# ═══════════════════════════════════════════════════════════════════════
def get_capital_summary() -> dict[str, dict]:
    """Retorna current invested per bot name (font autoritzativa = capital_events).
    Format: {bot_name: {invested: float, active_bot_id: str, num_creates: int, ...}}
    """
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT bot_name, active_bot_id, current_invested_usdt, num_creates, num_invests, num_reductions, last_event_ts FROM capital_summary")
        out = {}
        for r in cur.fetchall():
            out[r[0]] = {
                "invested": float(r[2] or 0),
                "active_bot_id": r[1],
                "num_creates": int(r[3] or 0),
                "num_invests": int(r[4] or 0),
                "num_reductions": int(r[5] or 0),
                "last_event_ts": r[6],
            }
        return out


def get_target_weights() -> dict[str, dict]:
    """Retorna target weights per portfolio (font autoritzativa Neon).
    Format: {bot_name: {weight: float, threshold: float, notes: str}}"""
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT bot_name, weight::float, threshold::float, notes FROM target_weights ORDER BY bot_name")
        return {r[0]: {"weight": float(r[1]), "threshold": float(r[2] or 0.05), "notes": r[3] or ""}
                for r in cur.fetchall()}


def get_lifetime_summary() -> dict[str, dict]:
    """Retorna lifetime cycles + grid profit cross-recreation per bot name.
    Usa lifetime_summary_v3 (schema v7) que inclou cycles dels bots oberts.
    Format: {bot_name: {total_cycles, total_profit_realized, total_profit_unrealized,
                       total_profit_lifetime, total_epochs, total_bot_ids_in_chain, first_opened_at}}
    """
    with conn() as c, c.cursor() as cur:
        cur.execute("""SELECT bot_name, total_cycles, total_grid_profit_realized,
                              current_grid_profit_unrealized, total_grid_profit_lifetime,
                              total_epochs, total_bot_ids_in_chain, first_opened_at
                       FROM lifetime_summary_v3""")
        out = {}
        for r in cur.fetchall():
            out[r[0]] = {
                "total_cycles": int(r[1] or 0),
                "total_profit_realized": float(r[2] or 0),
                "total_profit_unrealized": float(r[3] or 0),
                "total_profit_lifetime": float(r[4] or 0),
                "total_epochs": int(r[5] or 0),
                "total_bot_ids_in_chain": int(r[6] or 0),
                "first_opened_at": r[7],
            }
        return out


# ═══════════════════════════════════════════════════════════════════════
# Health check
# ═══════════════════════════════════════════════════════════════════════
def ping() -> bool:
    """Retorna True si la BD respon."""
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute("SELECT 1")
            return cur.fetchone()[0] == 1
    except Exception as e:
        log.error(f"ping failed: {e}")
        return False
