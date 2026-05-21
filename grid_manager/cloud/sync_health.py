"""
sync_health.py — Cada 5 min, sincronitza al Neon:
  - system_health: heartbeat del monitor (last run, triggers avui, etc.)
  - monitor_log: últimes 50 línies del monitor.log
  - price_snapshots: preu actual de cada bot + rang (per a chart històric)
  - wallet_snapshot fresh (USDT/BTC/etc lliures)

Executar via Task Scheduler cada 5 min.
"""
from __future__ import annotations

import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Force user-site
_USER_SITE = r"C:\Users\Administrator\AppData\Roaming\Python\Python312\site-packages"
if _USER_SITE not in sys.path:
    sys.path.insert(0, _USER_SITE)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cloud.db_cloud import (
    log_system_health, log_monitor_lines, trim_monitor_log,
    log_price_snapshot, trim_price_snapshots, log_wallet_snapshot,
    log_grid_trade, log_operation, log_bot_lifecycle, cron_run, conn,
)
from config import BOTS, LOG_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("sync_health")

MONITOR_LOG = LOG_DIR / "monitor.log"


def sync_monitor_health():
    """Parse monitor.log per generar heartbeat status."""
    if not MONITOR_LOG.exists():
        log_system_health(component="monitor", status="error", error_msg="log file missing")
        return

    try:
        with open(MONITOR_LOG, encoding="utf-8", errors="ignore") as f:
            # Llegim només les últimes 1MB (suficient per a parsar avui)
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 1024 * 1024))
            lines = f.readlines()
    except Exception as e:
        log_system_health(component="monitor", status="error", error_msg=str(e)[:300])
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    triggers_today = 0
    adjusts_ok_today = 0
    adjusts_fail_today = 0
    last_run = None
    last_cycle_ms = None
    last_trigger = None
    last_adjust = None

    cycle_start = None
    for ln in lines:
        if ln.startswith(today):
            if "TRIGGER:" in ln:
                triggers_today += 1
                last_trigger = ln.strip()
            if "adjust_params executed" in ln and "result=True" in ln:
                adjusts_ok_today += 1
                last_adjust = ln.strip()
            elif "adjust_params executed" in ln and "result=False" in ln:
                adjusts_fail_today += 1
            elif "adjust_params FAILED" in ln:
                adjusts_fail_today += 1
        if "=== Monitor cycle start" in ln:
            try:
                cycle_start = datetime.strptime(ln.split(" |")[0].strip(), "%Y-%m-%d %H:%M:%S,%f")
            except Exception:
                cycle_start = None
        if "=== Monitor cycle done" in ln:
            try:
                cycle_end = datetime.strptime(ln.split(" |")[0].strip(), "%Y-%m-%d %H:%M:%S,%f")
                if cycle_start:
                    last_cycle_ms = int((cycle_end - cycle_start).total_seconds() * 1000)
                last_run = cycle_end
            except Exception:
                pass

    # Determine status from age
    if last_run is None:
        status = "error"
    else:
        age_min = (datetime.utcnow() - last_run).total_seconds() / 60
        if age_min < 10:
            status = "ok"
        elif age_min < 30:
            status = "warn"
        else:
            status = "error"

    log_system_health(
        component="monitor", status=status,
        last_cycle_ms=last_cycle_ms,
        triggers_today=triggers_today,
        adjusts_ok_today=adjusts_ok_today,
        adjusts_fail_today=adjusts_fail_today,
        last_trigger_text=last_trigger[:500] if last_trigger else None,
        last_adjust_text=last_adjust[:500] if last_adjust else None,
        extra={"last_run_ts": last_run.isoformat() if last_run else None},
    )
    log.info(f"monitor health → {status} (triggers_today={triggers_today})")


def sync_monitor_log_tail(n_lines: int = 50):
    """Pujar les últimes N línies del monitor.log a Neon, deduplicant amb el ts
    més recent ja existent. Evita triplicats quan sync_health corre sovint."""
    if not MONITOR_LOG.exists(): return
    with open(MONITOR_LOG, encoding="utf-8", errors="ignore") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 200 * 1024))
        lines = f.readlines()[-n_lines:]

    # Llegir el ts més recent ja a Neon, només pujem els més nous (estricte).
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT MAX(ts) FROM monitor_log")
        max_ts = cur.fetchone()[0]

    rows = []
    pat = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+)\s*\|\s*(\w+)\s*\|\s*(.+)$")
    for ln in lines:
        m = pat.match(ln.rstrip())
        if not m: continue
        ts_str, level, msg = m.groups()
        try:
            ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S,%f").replace(tzinfo=timezone.utc)
        except Exception:
            ts = datetime.now(timezone.utc)
        if max_ts is not None and ts <= max_ts:
            continue  # ja sincronitzat
        comp = "monitor"
        if msg.startswith("["):
            end = msg.find("]")
            if end > 0:
                comp = msg[1:end]
        rows.append((ts, level, comp, msg[:1000]))

    if rows:
        log_monitor_lines(rows)
    trim_monitor_log(max_rows=500)
    log.info(f"Synced {len(rows)} new log lines (trimmed to 500 max)")


def sync_pending_recolocations() -> int:
    """Self-healing v2: si monitor.py va dumpejar una recolocació a
    logs/pending_recolocations.jsonl (transient ImportError o connection fail),
    aquí la insertem a Neon i netegem el fitxer."""
    import json
    pending_path = LOG_DIR / "pending_recolocations.jsonl"
    if not pending_path.exists():
        return 0

    try:
        with open(pending_path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f.readlines() if ln.strip()]
    except Exception as e:
        log.warning(f"sync_pending_recolocations: read failed: {e}")
        return 0

    if not lines:
        try: pending_path.unlink()
        except Exception: pass
        return 0

    from cloud.db_cloud import log_recolocation
    inserted = 0
    unhandled = []
    for ln in lines:
        try:
            p = json.loads(ln)
            log_recolocation(
                bot_id=p["bot_id"], bot_name=p["bot_name"], trigger=p["trigger"],
                price_at_trigger=p["price_at_trigger"],
                old_top=p.get("old_top", 0), old_bottom=p.get("old_bottom", 0),
                new_top=p.get("new_top", 0), new_bottom=p.get("new_bottom", 0),
                grid_profit_before=p.get("grid_profit_before", 0),
                grid_profit_after=p.get("grid_profit_after", 0),
                fee_consumed_before=p.get("fee_consumed_before", 0),
                fee_consumed_after=p.get("fee_consumed_after", 0),
                cost_usdt=p.get("cost_usdt", 0), executed=p.get("executed", True),
                action_taken=p.get("action_taken", "adjust_params"),
                error_msg=p.get("error_msg"),
                idempotency_key=f"reloc_{p['bot_id']}_{int(float(p['price_at_trigger'])*1000)}_{p.get('action_taken','adj')}",
            )
            inserted += 1
        except Exception as e:
            log.warning(f"sync_pending_recolocations: failed to insert one record: {e}")
            unhandled.append(ln)

    # Reescriure el fitxer només amb els no processats
    try:
        if unhandled:
            with open(pending_path, "w", encoding="utf-8") as f:
                f.write("\n".join(unhandled) + "\n")
        else:
            pending_path.unlink()
    except Exception as e:
        log.warning(f"sync_pending_recolocations: cleanup failed: {e}")

    if inserted:
        log.warning(f"sync_pending_recolocations: recovered {inserted} reloc(s) from JSONL fallback")
    return inserted


def sync_prices():
    """Snapshot del preu actual de cada bot + rang."""
    from pionex_client import get_bot_range
    count = 0
    for name, cfg in BOTS.items():
        try:
            s = get_bot_range(cfg["id"], symbol=cfg["symbol"])
            top = float(s.get("top") or 0)
            bottom = float(s.get("bottom") or 0)
            price = float(s.get("price") or 0)
            if price > 0:
                log_price_snapshot(
                    symbol=cfg["symbol"], price=price,
                    bot_top=top if top > 0 else None,
                    bot_bottom=bottom if bottom > 0 else None,
                )
                count += 1
        except Exception as e:
            log.warning(f"[{name}] price snapshot failed: {e}")
    # Trim only on hour 0 to evitar fer-ho cada 5 min
    if datetime.now().minute < 5:
        deleted = trim_price_snapshots(days_to_keep=60)
        log.info(f"Trimmed {deleted} old price snapshots")
    log.info(f"Synced {count}/{len(BOTS)} prices")


def sync_wallet_fresh():
    """Snapshot del wallet ara mateix (no cada 24h)."""
    try:
        from pionex_client import get_balance, get_current_price
        bal = get_balance() or {}
        btc_price = 0.0
        try: btc_price = float(get_current_price("BTC_USDT"))
        except Exception: pass
        for coin, free_amt in bal.items():
            free_f = float(free_amt or 0)
            value = free_f if coin in ("USDT", "USDC") else (free_f * btc_price if coin == "BTC" else 0)
            log_wallet_snapshot(coin=coin, free=free_f, value_usdt=value, source="sync_health")
        log.info(f"Wallet snapshot fresh OK ({len(bal)} coins)")
    except Exception as e:
        log.error(f"Wallet snapshot failed: {e}")


def sync_grid_trades():
    """Sincronitza fills de Pionex (per cada bot symbol) a Neon.
    Idempotent: cada fill té unique pionex_fill_id.
    Pionex retorna fins a 100 fills més recents per simbol."""
    from datetime import datetime as _dt, timezone as _tz
    from pionex_client import _get_signed
    new_count = 0
    for name, cfg in BOTS.items():
        try:
            data = _get_signed("/api/v1/trade/fills", {"symbol": cfg["symbol"]})
            fills = (data or {}).get("data", {}).get("fills", [])
            for f in fills:
                try:
                    inserted_id = log_grid_trade(
                        pionex_fill_id=int(f["id"]),
                        pionex_order_id=int(f["orderId"]),
                        bot_id=cfg["id"], bot_name=name,
                        symbol=cfg["symbol"],
                        side=str(f["side"]),
                        role=str(f.get("role") or ""),
                        price=float(f["price"]),
                        size=float(f["size"]),
                        fee=float(f.get("fee") or 0),
                        fee_coin=str(f.get("feeCoin") or ""),
                        ts=_dt.fromtimestamp(int(f["timestamp"]) / 1000, tz=_tz.utc),
                    )
                    if inserted_id: new_count += 1
                except Exception as e:
                    log.warning(f"[{name}] fill parse fail: {e}")
        except Exception as e:
            log.warning(f"[{name}] fills sync fail: {e}")
    log.info(f"Grid trades: {new_count} fills nous")
    return new_count


def sync_bot_lifecycle():
    """Detecta bots creats/tancats a Pionex i actualitza Neon.
       - Bot a Pionex que no està a la nostra DB → lifecycle 'detected_created'
       - Bot 'running' a la nostra DB que ja no apareix a Pionex → 'detected_closed'
         + tanca bot_epochs amb grid_profit_at_close del últim snapshot conegut
    """
    from pionex_client import _get_signed
    detected = 0
    try:
        data = _get_signed("/api/v1/bot/orders", {"status": "running"})
        pionex_bots = (data or {}).get("data", {}).get("results", [])
        pionex_ids = {b["buOrderId"] for b in pionex_bots if b.get("buOrderType") == "spot_grid"}
    except Exception as e:
        log.warning(f"bot list fetch fail: {e}")
        return 0

    # 1) Bots a Neon que ja no estan a Pionex (= tancats sense detecció)
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT bot_id, name FROM bots WHERE status='running'")
        our_running = [(r[0], r[1]) for r in cur.fetchall()]

    for bot_id, bot_name in our_running:
        if bot_id not in pionex_ids:
            # Bot ja no existeix a Pionex → tanca
            # Obtenim últim snapshot per a grid_profit_at_close
            with conn() as c, c.cursor() as cur:
                cur.execute("""
                    SELECT gross_grid_profit::float, invested_capital::float
                    FROM daily_snapshots WHERE bot_id=%s
                    ORDER BY date DESC LIMIT 1
                """, (bot_id,))
                row = cur.fetchone()
            last_gp = row[0] if row else 0
            last_inv = row[1] if row else 0
            # Tanca epoch obert
            with conn() as c, c.cursor() as cur:
                cur.execute("""
                    UPDATE bot_epochs
                    SET closed_at=NOW(),
                        grid_profit_at_close=%s,
                        final_capital_usdt=%s,
                        notes=COALESCE(notes,'') || ' [auto-closed by sync_health]'
                    WHERE bot_id=%s AND closed_at IS NULL
                """, (last_gp, last_inv, bot_id))
                cur.execute(
                    "UPDATE bots SET status='closed', closed_at=NOW() WHERE bot_id=%s",
                    (bot_id,),
                )
                c.commit()
            log_bot_lifecycle(
                bot_id=bot_id, bot_name=bot_name,
                event_type="detected_closed",
                last_grid_profit=last_gp, last_quote_invested=last_inv,
                notes=f"Bot ja no apareix a Pionex (auto-detect). gridProfit preservat: {last_gp}",
            )
            log.warning(f"[{bot_name}] DETECTED CLOSED (last gp={last_gp:.4f})")
            detected += 1

    # 2) Bots a Pionex que no tenim al nostre DB
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT bot_id FROM bots")
        our_ids = {r[0] for r in cur.fetchall()}
    for b in pionex_bots:
        if b.get("buOrderType") != "spot_grid": continue
        bid = b["buOrderId"]
        if bid not in our_ids:
            base = b.get("base", "")
            quote = b.get("quote", "")
            bot_name = f"{base}_{quote}"
            log_bot_lifecycle(
                bot_id=bid, bot_name=bot_name,
                event_type="detected_created",
                last_grid_profit=float((b.get("buOrderData") or {}).get("gridProfit", 0)),
                last_quote_invested=float((b.get("buOrderData") or {}).get("quoteTotalInvestment", 0)),
                notes="Bot nou a Pionex (auto-detect). Cal afegir manualment a config.py i taula bots.",
            )
            log.warning(f"NEW BOT DETECTED: {bot_name} ({bid[:8]})")
            detected += 1

    if detected == 0:
        log.info("bot lifecycle: cap canvi detectat")
    return detected


def sync_mt5_state():
    """Llegeix dualgrid_status.json i guarda snapshot a Neon."""
    try:
        from mt5_grid_client import get_mt5_grid_state
        from cloud.db_cloud import log_mt5_state
        state = get_mt5_grid_state()
        available = bool(state.get("available", True) and "error" not in state)
        raw = state.get("raw") or state
        log_mt5_state(
            available=available,
            equity=float(state.get("equity") or 0) or None,
            balance=float(state.get("balance") or 0) or None,
            floating_pnl=float(state.get("floating_pnl") or 0) or None,
            daily_profit=float(state.get("daily_profit") or 0) or None,
            cum_profit=float(state.get("cum_profit") or 0) or None,
            open_positions=int(state.get("open_positions") or 0) or None,
            raw_json=raw if isinstance(raw, dict) else None,
        )
        log.info(f"MT5 state synced (available={available})")
    except Exception as e:
        log.warning(f"MT5 sync failed (no bloca): {e}")


def sync_bot_snapshots():
    """UPSERT daily_snapshots amb les dades actuals (cada bot).
    Així el dashboard té dades de bots a 5 min de freshness, no 24h."""
    try:
        from cloud.daily_snapshot import snapshot_all_bots
        n = snapshot_all_bots()
        log.info(f"Bot snapshots fresh OK ({n}/{len(BOTS)})")
    except Exception as e:
        log.error(f"Bot snapshots failed: {e}")


def sync_recolocations_from_sqlite() -> int:
    """Self-healing: si monitor.py va fallar mirroring una recolocació a Neon
    (p.ex. transient ImportError de psycopg), aquesta funció la backfilleja
    des de SQLite. Match per (bot_id, ts ±120s)."""
    import sqlite3 as _sq
    try:
        from config import DB_PATH as _DB
    except Exception:
        return 0
    if not Path(_DB).exists():
        return 0

    try:
        sq = _sq.connect(str(_DB))
        sq.row_factory = _sq.Row
        rows = sq.execute(
            "SELECT ts, bot_id, bot_name, trigger, price, "
            "new_top, new_bottom, grid_profit_before, grid_profit_after, "
            "fee_pool_before, fee_pool_after FROM recolocation_costs ORDER BY ts DESC LIMIT 200"
        ).fetchall()
        sq.close()
    except Exception as e:
        log.warning(f"sync_recolocations: sqlite read failed: {e}")
        return 0

    if not rows:
        return 0

    inserted = 0
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT bot_id, ts FROM recolocations ORDER BY ts DESC LIMIT 500")
        neon_by_bot: dict = {}
        for bot_id, ts in cur.fetchall():
            neon_by_bot.setdefault(bot_id, []).append(ts)

        for r in rows:
            try:
                sq_ts = datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
            except Exception:
                continue
            cands = neon_by_bot.get(r["bot_id"], [])
            if any(abs((ct - sq_ts).total_seconds()) <= 120 for ct in cands):
                continue
            gp_b = float(r["grid_profit_before"] or 0)
            gp_a = float(r["grid_profit_after"] or 0)
            fc_b = float(r["fee_pool_before"] or 0)
            fc_a = float(r["fee_pool_after"] or 0)
            cost = max(0.0, fc_a - fc_b) + max(0.0, -(gp_a - gp_b))
            idem = f"reloc_{r['bot_id']}_{int(sq_ts.timestamp())}"
            cur.execute("""
                INSERT INTO recolocations
                  (ts, bot_id, bot_name, trigger, price_at_trigger,
                   new_top, new_bottom, grid_profit_before, grid_profit_after,
                   fee_consumed_before, fee_consumed_after, cost_usdt,
                   executed, action_taken, error_msg, idempotency_key)
                VALUES
                  (%s::timestamptz, %s, %s, %s, %s, %s, %s, %s, %s,
                   %s, %s, %s, TRUE, 'adjust_params_ok',
                   'sync_health_backfill_from_sqlite', %s)
                ON CONFLICT (idempotency_key) DO NOTHING
            """, (
                r["ts"], r["bot_id"], r["bot_name"], r["trigger"], float(r["price"] or 0),
                float(r["new_top"]), float(r["new_bottom"]),
                gp_b, gp_a, fc_b, fc_a, cost, idem,
            ))
            if cur.rowcount:
                inserted += 1
        c.commit()

    if inserted:
        log.warning(f"sync_recolocations: backfilled {inserted} missed by monitor.py cloud mirror")
    return inserted


def main():
    log.info("Starting sync_health cycle")
    with cron_run("sync_health") as ctx:
        sync_monitor_health()
        sync_monitor_log_tail(n_lines=50)
        sync_prices()
        sync_wallet_fresh()
        sync_bot_snapshots()
        trades = sync_grid_trades()
        lifecycle_changes = sync_bot_lifecycle()
        sync_mt5_state()
        relocs_recovered = sync_recolocations_from_sqlite()
        relocs_pending = sync_pending_recolocations()
        ctx["items"] = trades + lifecycle_changes + relocs_recovered + relocs_pending
        ctx["notes"] = (f"{trades} new fills, {lifecycle_changes} lifecycle changes, "
                        f"{relocs_recovered} sqlite backfilled, "
                        f"{relocs_pending} jsonl recovered")
    log.info("Done")


if __name__ == "__main__":
    main()
