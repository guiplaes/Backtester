"""
vault/consume_injections.py — Cron que consumeix injection_queue.

Llegeix injection_queue.status='pending' (FIFO per ts), per cadascun:
  1. Crida vault.inventory.add_usdt() amb idempotency_key únic
  2. Si reixit: marca status='consumed', consumed_at, consumed_by
  3. Si falla: marca status='failed' amb error_msg

Executable manualment o via Task Scheduler cada 60s.

Atomicity:
  - Cada injection es processa amb SELECT FOR UPDATE per evitar race amb un
    altre worker (cas que algun dia tinguem 2 workers).
  - L'idempotency_key és 'injection_<id>' → si l'add_usdt s'executa 2 cops
    (per algun crash entre els 2 UPDATE), capital_events ho deduplica.

Logging:
  - Output al stdout (capturat per Task Scheduler wrapper a logs/inject.log)
  - cron_runs registra inici/fi de cada execució
"""
from __future__ import annotations

import logging
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cloud.db_cloud import conn, cron_run
from vault.inventory import add_usdt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("consume_injections")

HOST = socket.gethostname()


def _fetch_pending(limit: int = 50) -> list[tuple]:
    """Retorna (id, ts, amount_usdt, note) de pendents FIFO."""
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            SELECT id, ts, amount_usdt, note
            FROM injection_queue
            WHERE status = 'pending'
            ORDER BY ts ASC
            LIMIT %s
        """, (limit,))
        return cur.fetchall()


def _mark_consumed(injection_id: int) -> None:
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            UPDATE injection_queue
            SET status='consumed', consumed_at=NOW(), consumed_by=%s
            WHERE id=%s AND status='pending'
        """, (HOST, injection_id))
        c.commit()


def _mark_failed(injection_id: int, error_msg: str) -> None:
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            UPDATE injection_queue
            SET status='failed', consumed_at=NOW(), consumed_by=%s, error_msg=%s
            WHERE id=%s AND status='pending'
        """, (HOST, error_msg[:500], injection_id))
        c.commit()


def consume_all() -> dict:
    """Processa tots els pendents. Retorna {processed, consumed, failed}."""
    processed = 0
    consumed = 0
    failed = 0
    errors = []

    pending = _fetch_pending(limit=50)
    if not pending:
        return {"processed": 0, "consumed": 0, "failed": 0, "errors": []}

    log.info(f"Found {len(pending)} pending injections")

    for inj_id, ts, amount, note in pending:
        processed += 1
        amount_f = float(amount or 0)
        if amount_f <= 0:
            _mark_failed(inj_id, f"invalid amount: {amount}")
            failed += 1
            errors.append(f"id={inj_id}: invalid amount")
            continue

        idem_key = f"injection_{inj_id}"
        try:
            ok = add_usdt(
                amount=amount_f,
                source=f"injection_queue/{HOST}",
                idempotency_key=idem_key,
                notes=f"Manual injection #{inj_id} at {ts}: {note or ''}",
            )
            if ok:
                _mark_consumed(inj_id)
                consumed += 1
                log.info(f"  ✓ injection #{inj_id}: +${amount_f:.2f} USDT consumed")
            else:
                _mark_failed(inj_id, "add_usdt returned False (Neon write failure, see pending_*.jsonl)")
                failed += 1
                errors.append(f"id={inj_id}: add_usdt False")
        except Exception as e:
            _mark_failed(inj_id, f"exception: {e}")
            failed += 1
            errors.append(f"id={inj_id}: {e}")
            log.error(f"  ✗ injection #{inj_id}: {e}")

    return {
        "processed": processed, "consumed": consumed,
        "failed": failed, "errors": errors,
    }


def main():
    with cron_run("vault_consume_injections") as ctx:
        result = consume_all()
        ctx["items"] = result["consumed"]
        ctx["notes"] = (
            f"processed={result['processed']} consumed={result['consumed']} failed={result['failed']}"
        )
        if result["consumed"] > 0:
            log.info(f"Cron done: {ctx['notes']}")
        if result["failed"] > 0:
            log.warning(f"Failures: {result['errors']}")


if __name__ == "__main__":
    main()
