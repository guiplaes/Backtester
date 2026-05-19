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

# Carrega .env de la carpeta del projecte si encara no està a l'entorn
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
if _ENV_PATH.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_PATH)
    except Exception:
        pass

log = logging.getLogger("db_cloud")

DATABASE_URL = os.environ.get("DATABASE_URL")
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
        log.error(f"log_capital_event failed: {e}")
        raise


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
        log.error(f"log_recolocation failed: {e}")
        raise


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
) -> None:
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            INSERT INTO daily_snapshots
            (date, bot_id, bot_name, gross_grid_profit, cum_lifetime_profit,
             invested_capital, base_amount, base_value_usdt, quote_value_usdt,
             current_value_total, cycles_completed_today, cycles_total,
             price_close, top, bottom)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                bottom = EXCLUDED.bottom
        """, (
            date_, bot_id, bot_name, gross_grid_profit, cum_lifetime_profit,
            invested_capital, base_amount, base_value_usdt, quote_value_usdt,
            current_value_total, cycles_completed_today, cycles_total,
            price_close, top, bottom,
        ))
        c.commit()


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
