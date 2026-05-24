"""
vault/relauncher.py — Orquestra el procés complet de relocació via vault.

Fluxe (LIVE):
  1. Llegir estat del bot just abans de tancar (per saber target_value)
  2. cancel_bot(closeSellModel=NOT_SELL) → Pionex retorna base + USDT al wallet
  3. Llegir delta de wallet per saber QUÈ s'ha recuperat exactament
  4. vault.inventory.add_base(asset, qty_recovered, cost_at_avg)
  5. vault.inventory.add_usdt(usdt_recovered)
  6. Calcular target_usdt = target_value (= valor del bot just abans del close)
     - already_have = USDT recuperat
     - shortfall = target_usdt - already_have
  7. vault.funding.compute_funding_plan(shortfall, asset_being_funded=asset)
  8. Si feasible: execute_funding_plan → ven vault profitable + treu USDT
  9. Calcular nou rang: centrat al preu actual, width = cfg["width_pct"]
  10. pionex_client.create_spot_grid(...) → crea nou bot amb target_usdt total
  11. bot_operations.log_manual_transfer style log a:
      - bots (insert new, mark old closed)
      - bot_lineage (parent → child)
      - bot_epochs (close old, open new)
      - capital_events (close + create)
      - vault_events (delta base + USDT)

Fluxe (SHADOW / dry_run):
  - Calcula tot però NO executa cap call a Pionex que muti estat
  - Logueja el pla a vault_events amb event_type='shadow_relaunch_*'
  - TG notify amb el pla complet per a revisió de l'usuari

Notes:
  - El target_value es preserva (= valor del bot just abans del breakout).
    Si bot tenia 0.005 BTC + $50 USDT amb BTC a $80k → value = $450.
    Nou grid serà creat amb $450 USDT total (després de recuperar + funding).
  - Si funding waterfall NO és feasible: ABORT (no relocació, esperar 24-72h).
    El base recuperat queda al vault i pot ser usat per finançar altres relocs.
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cloud.db_cloud import (
    conn, log_capital_event, log_bot_lifecycle, log_recolocation,
    upsert_bot, mark_bot_closed, open_epoch, close_epoch, snapshot_bot_state
)
from vault.inventory import add_base, add_usdt
from vault.funding import compute_funding_plan, execute_funding_plan

log = logging.getLogger("vault.relauncher")


def _read_bot_state(bot_id: str, symbol: str) -> dict:
    """Snapshot complet pre-close."""
    from pionex_client import get_bot_range, get_bot_order
    state = get_bot_range(bot_id, symbol=symbol)
    raw = get_bot_order(bot_id)
    bu = raw.get("buOrderData", {})
    state["base_amount"] = float(bu.get("baseAmount") or 0)
    state["quote_amount"] = float(bu.get("quoteAmount") or 0)
    state["average_cost"] = float(bu.get("averageCost") or 0)
    state["grid_profit"] = float(bu.get("gridProfit") or 0)
    state["cycles"] = int(bu.get("exchangeOrderPairedCount") or 0)
    state["raw"] = raw
    return state


def _compute_new_range(price: float, width_pct: float, rows: int) -> tuple[float, float, int]:
    """Centra el nou grid al preu actual amb width = width_pct."""
    half = price * width_pct / 2
    return (price - half, price + half, rows)


def relaunch_after_breakout(*, bot_name: str, bot_id: str, cfg: dict,
                            price_at_breakout: float,
                            dry_run: bool = True) -> dict:
    """Wrapper that runs the relocation inside a vault_batch context.
    All vault events collected → 1 single TG summary at the end."""
    from notifier import vault_batch
    title_prefix = "🔍 [SHADOW] " if dry_run else ""
    title = f"{title_prefix}Relocació {bot_name.replace('_', '-')}"
    with vault_batch(title=title, key=f"relaunch:{bot_name}"):
        return _relaunch_inner(
            bot_name=bot_name, bot_id=bot_id, cfg=cfg,
            price_at_breakout=price_at_breakout, dry_run=dry_run,
        )


def _relaunch_inner(*, bot_name: str, bot_id: str, cfg: dict,
                    price_at_breakout: float, dry_run: bool) -> dict:
    """Cos real de la relocació. Tots els TGs van al batch del wrapper."""
    asset = cfg["base"]
    now = datetime.now(timezone.utc)
    idem_prefix = f"relaunch_{bot_name}_{now.strftime('%Y%m%dT%H%M%S')}"

    out: dict = {"bot_name": bot_name, "dry_run": dry_run, "errors": []}

    # ── 1. Snapshot pre-close ────────────────────────────────────
    try:
        pre = _read_bot_state(bot_id, cfg["symbol"])
    except Exception as e:
        out["ok"] = False
        out["errors"].append(f"pre snapshot: {e}")
        return out

    target_value = pre["base_amount"] * pre["price"] + pre["quote_amount"]
    out["pre_state"] = {
        "base": pre["base_amount"], "quote": pre["quote_amount"],
        "price": pre["price"], "avg_cost": pre["average_cost"],
        "value_total": target_value,
        "grid_profit": pre["grid_profit"], "cycles": pre["cycles"],
    }
    out["target_value"] = target_value

    log.info(f"  {bot_name} PRE: base={pre['base_amount']:.6f} @ avg ${pre['average_cost']:.4f}, "
             f"quote=${pre['quote_amount']:.4f}, value=${target_value:.4f}")

    # ── 2. Cancel bot (NOT_SELL) — VERIFICAT amb wallet delta ─────
    # IMPORTANT: usar pre.base_amount/quote_amount és OPTIMISTIC. Pionex
    # pot retornar amounts diferents per fees, pending orders, etc.
    # Calculem via wallet BALANCE DELTA pre vs post cancel.
    if not dry_run:
        try:
            from pionex_client import cancel_bot, get_balance
            # 1. Wallet snapshot PRE-cancel
            try:
                bal_pre = get_balance() or {}
                base_pre_wallet = float(bal_pre.get(asset, 0))
                usdt_pre_wallet = float(bal_pre.get("USDT", 0))
            except Exception as e:
                log.warning(f"  {bot_name}: no he pogut llegir wallet PRE-cancel: {e}")
                # Fallback: usar valors del bot state (less accurate)
                base_pre_wallet = None
                usdt_pre_wallet = None

            # 2. Cancel
            cancel_result = cancel_bot(bot_id)
            log.info(f"  {bot_name} cancel: {cancel_result.get('result')}")
            if not cancel_result.get("result"):
                out["ok"] = False
                out["errors"].append(f"cancel failed: {cancel_result}")
                return out

            # 3. Esperem 3s perquè Pionex actualitzi balances
            time.sleep(3)

            # 4. Wallet snapshot POST-cancel + delta
            if base_pre_wallet is not None and usdt_pre_wallet is not None:
                try:
                    bal_post = get_balance() or {}
                    base_post_wallet = float(bal_post.get(asset, 0))
                    usdt_post_wallet = float(bal_post.get("USDT", 0))
                    recovered_base = base_post_wallet - base_pre_wallet
                    recovered_usdt = usdt_post_wallet - usdt_pre_wallet
                    log.info(f"  {bot_name} wallet delta REAL: +{recovered_base:.8f} {asset}, +${recovered_usdt:.4f} USDT")
                    # Defensiu: si delta negatiu (no hauria de passar), usar valors pre del bot
                    if recovered_base < 0 or recovered_usdt < 0:
                        log.warning(f"  {bot_name}: wallet delta negatiu! fallback a valors pre-bot")
                        recovered_base = pre["base_amount"]
                        recovered_usdt = pre["quote_amount"]
                except Exception as e:
                    log.warning(f"  {bot_name}: no he pogut llegir wallet POST-cancel ({e}), fallback")
                    recovered_base = pre["base_amount"]
                    recovered_usdt = pre["quote_amount"]
            else:
                # No wallet pre, usar valors del bot (less accurate)
                recovered_base = pre["base_amount"]
                recovered_usdt = pre["quote_amount"]
        except Exception as e:
            out["ok"] = False
            out["errors"].append(f"cancel exception: {e}")
            return out
    else:
        # Dry run: usar valors del bot pre-state
        recovered_base = pre["base_amount"]
        recovered_usdt = pre["quote_amount"]

    # Cost basis = recovered_base × avg_cost (preserva el cost original al vault)
    recovered_cost = recovered_base * pre["average_cost"] if pre["average_cost"] else 0
    out["recovered"] = {
        "base": recovered_base, "usdt": recovered_usdt, "cost_basis": recovered_cost,
    }

    # ── 3. Add recovered base + USDT al vault_inventory ───────────
    if not dry_run:
        if recovered_base > 0:
            add_base(
                asset=asset, qty=recovered_base, cost_usdt=recovered_cost,
                source=f"closer/{bot_name}",
                idempotency_key=f"{idem_prefix}_addbase",
                bot_id=bot_id, bot_name=bot_name,
                notes=f"Recovered after breakout close (price ${price_at_breakout:.4f})",
            )
        if recovered_usdt > 0:
            add_usdt(
                amount=recovered_usdt,
                source=f"closer/{bot_name}",
                idempotency_key=f"{idem_prefix}_addusdt",
                notes=f"USDT recovered from {bot_name} close",
            )

    # ── 4. Compute funding plan ────────────────────────────────────
    shortfall = target_value - recovered_usdt
    out["shortfall_usdt"] = shortfall

    if shortfall <= 0.01:
        # Hem recuperat prou USDT amb el close mateix
        log.info(f"  {bot_name}: NO funding needed (recovered ${recovered_usdt:.2f} ≥ target)")
        funding_plan = None
        funding_exec = None
        usable_usdt = target_value
    else:
        # Necessitem complementar via waterfall
        try:
            from pionex_client import get_current_price
            prices = {}
            for sym, c in __import__("config", fromlist=["BOTS"]).BOTS.items():
                a = c["base"]
                try:
                    prices[a] = get_current_price(c["symbol"])
                except Exception:
                    pass
            funding_plan = compute_funding_plan(
                target_usdt=shortfall,
                asset_being_funded=asset,
                prices=prices,
            )
            out["funding_plan_summary"] = funding_plan.summary()
            log.info(f"  Funding plan:\n{funding_plan.summary()}")
        except Exception as e:
            out["ok"] = False
            out["errors"].append(f"funding plan: {e}")
            return out

        if not funding_plan.feasible:
            out["ok"] = False
            out["errors"].append(
                f"funding NOT feasible: shortfall ${funding_plan.shortfall:.2f} "
                f"after waterfall. ABORT relocació."
            )
            log.warning(f"  {bot_name}: funding NOT feasible — base recovered restarà al vault")
            # Nota addicional al batch
            try:
                from notifier import add_batch_note
                add_batch_note(
                    f"⚠️ *Relocació abortada:* funding waterfall no cobreix target ${target_value:.2f}\n"
                    f"Shortfall: *${funding_plan.shortfall:.2f}*. Considera afegir USDT manualment."
                )
            except Exception:
                pass
            return out

        # ── 5. Execute funding (només si LIVE) ──
        funding_exec = execute_funding_plan(
            plan=funding_plan, idempotency_prefix=idem_prefix, dry_run=dry_run,
        )
        out["funding_exec"] = funding_exec
        usable_usdt = recovered_usdt + funding_exec.get("total_usdt_raised", 0)

    # ── 6. Compute new range ─────────────────────────────────────
    bottom, top, rows = _compute_new_range(
        price_at_breakout, cfg["width_pct"], cfg.get("rows", 12)
    )
    out["new_range"] = {"bottom": bottom, "top": top, "rows": rows}
    log.info(f"  New range: [${bottom:.4f}, ${top:.4f}] × {rows} rows")

    # ── 7. Create new bot (només si LIVE) ────────────────────────
    if dry_run:
        out["ok"] = True
        out["status"] = "dry_run_complete"
        # Afegir nota al batch perquè aparegui al resum (sense TG addicional)
        try:
            from notifier import add_batch_note
            add_batch_note(
                f"*PRE bot:* base={recovered_base:.6f} {asset}, quote=${recovered_usdt:.2f}, value=*${target_value:.2f}*\n"
                f"*NOU bot planejat:* rang ${bottom:.4f} – ${top:.4f} × {rows} rows amb *${target_value:.2f}*"
            )
        except Exception:
            pass
        return out

    # ── LIVE create_spot_grid ──
    try:
        from pionex_client import create_spot_grid
        create_result = create_spot_grid(
            base=asset, quote=cfg["quote"],
            top=top, bottom=bottom, row=rows,
            quote_total_investment=target_value,
            close_sell_model="NOT_SELL",
        )
        new_bot_id = create_result.get("data", {}).get("buOrderId")
        if not new_bot_id:
            out["ok"] = False
            out["errors"].append(f"create returned no bot_id: {create_result}")
            return out
        out["new_bot_id"] = new_bot_id
        log.info(f"  {bot_name} new bot created: {new_bot_id}")
    except Exception as e:
        out["ok"] = False
        out["errors"].append(f"create_spot_grid: {e}")
        return out

    # ── 8. Log a Neon (mateix patró que log_manual_transfer) ──
    try:
        # OLD bot: rename + close
        with conn() as c, c.cursor() as cur:
            cur.execute(
                "UPDATE bots SET name=%s, status='closed', closed_at=%s WHERE bot_id=%s",
                (f"{bot_name}_closed_{now.strftime('%Y%m%d_%H%M%S')}_{bot_id[:8]}",
                 now, bot_id),
            )
            c.commit()
        upsert_bot(bot_id=new_bot_id, name=bot_name, base=asset, quote=cfg["quote"],
                   created_at=now, status="running",
                   notes=f"Auto-recreated by vault.relauncher after breakout (parent {bot_id})")
        close_epoch(bot_id=bot_id, final_capital_usdt=pre["quote_amount"],
                    cycles_completed=pre["cycles"],
                    grid_profit_at_close=pre["grid_profit"],
                    realized_profit_at_close=pre["grid_profit"], closed_at=now,
                    notes=f"Auto-close via vault.closer (breakout)")
        open_epoch(bot_id=new_bot_id, initial_capital_usdt=target_value, opened_at=now,
                   initial_top=top, initial_bottom=bottom, initial_rows=rows,
                   notes=f"Auto-create via vault.relauncher (replaces {bot_id})")
        log_capital_event(
            bot_id=bot_id, bot_name=bot_name, event_type="close",
            amount_usdt=pre["quote_amount"], qti_before=target_value, qti_after=0,
            grid_profit_snapshot=pre["grid_profit"], source="vault.relauncher",
            idempotency_key=f"{idem_prefix}_close_old",
            notes=f"Auto-close after breakout at ${price_at_breakout:.4f}",
            created_by="vault.relauncher", ts=now,
        )
        log_capital_event(
            bot_id=new_bot_id, bot_name=bot_name, event_type="create",
            amount_usdt=target_value, qti_before=0, qti_after=target_value,
            source="vault.relauncher",
            idempotency_key=f"{idem_prefix}_create_new",
            notes=f"Auto-create at [${bottom:.4f}, ${top:.4f}] (replaces {bot_id})",
            created_by="vault.relauncher", ts=now,
        )
        # bot_lineage
        with conn() as c, c.cursor() as cur:
            cur.execute("""
                INSERT INTO bot_lineage
                  (child_bot_id, parent_bot_id, succession_ts, reason,
                   capital_transferred_usdt, cycles_at_succession,
                   grid_profit_at_succession, notes, detected_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (child_bot_id) DO NOTHING
            """, (new_bot_id, bot_id, now, "vault_auto_relauncher",
                  target_value, pre["cycles"], pre["grid_profit"],
                  f"Breakout at ${price_at_breakout:.4f}", "vault.relauncher"))
            c.commit()
        snapshot_bot_state(bot_id=new_bot_id, bot_name=bot_name,
                           event_type="vault_relaunch_create", source="vault.relauncher",
                           notes=f"Replaces {bot_id} after breakout")
    except Exception as e:
        log.error(f"  {bot_name}: Neon sync after create FAILED: {e}")
        out["errors"].append(f"neon sync: {e}")

    # ── 9. Nota al batch amb new_bot_id (acció manual config.py) ──
    try:
        from notifier import add_batch_note
        add_batch_note(
            f"*OLD bot_id:* `{bot_id}`\n"
            f"*NEW bot_id:* `{new_bot_id}`\n"
            f"*Nou rang:* ${bottom:.4f} – ${top:.4f} × {rows} rows\n"
            f"*Capital:* *${target_value:.2f}*\n\n"
            f"⚠️ *Acció requerida:* actualitza `config.py` BOTS\\['{bot_name}'\\]\\['id'\\]"
        )
    except Exception:
        pass

    out["ok"] = True
    out["status"] = "live_complete"
    return out
