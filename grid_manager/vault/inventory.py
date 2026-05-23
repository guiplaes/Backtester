"""
vault/inventory.py — CRUD i lectura MTM de la taula vault_inventory.

Filosofia (seguint patró db_cloud.py):
  - Tota mutació logga també a capital_events (audit trail amb idempotency).
  - Errors a Neon → dump a logs/pending_*.jsonl per backfill posterior.
  - avg_cost = cost_total_usdt / qty (NULL si qty=0).
  - USDT és tractat com asset més amb avg=1.

Funcions públiques:
    get_inventory()                      → dict[asset, {qty, cost_total, avg_cost}]
    get_inventory_mtm(prices)            → dict[asset, {..., value_mtm, unrealized_pnl}]
    add_base(asset, qty, cost_usdt, ...) → quan tanquem un bot i base va al vault
    remove_base(asset, qty, ...)         → quan venem vault per finançar / re-crear bot
    add_usdt(amount, ...)                → manual injection o profit harvest
    remove_usdt(amount, ...)             → quan creem nou bot consumeix de la reserva
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cloud.db_cloud import conn, _dump_pending

try:
    from notifier import notify_vault_event
except Exception:
    # Notifier opcional — el sistema funciona sense ell
    notify_vault_event = None

log = logging.getLogger("vault.inventory")


# ═══════════════════════════════════════════════════════════════════════
# Reads
# ═══════════════════════════════════════════════════════════════════════
def get_inventory() -> dict[str, dict]:
    """Retorna l'estat actual de l'inventari per asset.

    Returns:
        {asset: {qty: float, cost_total_usdt: float, avg_cost: float | None,
                 updated_at: datetime, notes: str | None}}
    """
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            SELECT asset, qty, cost_total_usdt, updated_at, notes
            FROM vault_inventory
            ORDER BY CASE WHEN asset='USDT' THEN 0 ELSE 1 END, asset
        """)
        out = {}
        for asset, qty, cost, updated_at, notes in cur.fetchall():
            qty_f = float(qty or 0)
            cost_f = float(cost or 0)
            out[asset] = {
                "qty": qty_f,
                "cost_total_usdt": cost_f,
                "avg_cost": (cost_f / qty_f) if qty_f > 0 else None,
                "updated_at": updated_at,
                "notes": notes,
            }
        return out


def get_inventory_mtm(prices: dict[str, float]) -> dict[str, dict]:
    """Retorna inventari + valor MTM amb preus actuals.

    Args:
        prices: {asset: current_price_usdt}. USDT s'assumeix preu=1.

    Returns:
        {asset: {qty, cost_total_usdt, avg_cost, price_now, value_mtm,
                 unrealized_pnl, unrealized_pct, is_profit}}
    """
    inv = get_inventory()
    out = {}
    for asset, d in inv.items():
        price = 1.0 if asset == "USDT" else float(prices.get(asset, 0) or 0)
        qty = d["qty"]
        cost = d["cost_total_usdt"]
        value = qty * price
        pnl = value - cost
        pct = (pnl / cost * 100) if cost > 0 else 0.0
        out[asset] = {
            **d,
            "price_now": price,
            "value_mtm": value,
            "unrealized_pnl": pnl,
            "unrealized_pct": pct,
            "is_profit": pnl > 0,
        }
    return out


def get_total_system_value(prices: dict[str, float]) -> dict:
    """Valor total del vault (no inclou bots actius).

    Returns:
        {total_value_mtm, total_cost_basis, total_unrealized_pnl,
         vault_usdt, vault_base_value}
    """
    mtm = get_inventory_mtm(prices)
    vault_usdt = mtm.get("USDT", {}).get("value_mtm", 0)
    vault_base = sum(m["value_mtm"] for a, m in mtm.items() if a != "USDT")
    total_value = vault_usdt + vault_base
    total_cost = sum(m["cost_total_usdt"] for m in mtm.values())
    return {
        "total_value_mtm": total_value,
        "total_cost_basis": total_cost,
        "total_unrealized_pnl": total_value - total_cost,
        "vault_usdt": vault_usdt,
        "vault_base_value": vault_base,
    }


# ═══════════════════════════════════════════════════════════════════════
# Writes — UPDATE + log a capital_events per audit
# ═══════════════════════════════════════════════════════════════════════
def _apply_delta(asset: str, qty_delta: float, cost_delta: float,
                 event_type: str, source: str,
                 idempotency_key: str | None = None,
                 bot_id: str | None = None,
                 bot_name: str | None = None,
                 notes: str | None = None) -> bool:
    """Aplica un delta a vault_inventory + log a vault_events.

    qty_delta i cost_delta poden ser negatius (vendre vault).
    Retorna True si l'operació s'ha executat (idempotency hit o nou).

    Transaccional: UPDATE vault_inventory + INSERT vault_events en la mateixa
    transacció. Si una falla, l'altra es revoca. Idempotency_key UNIQUE a
    vault_events bloca dobles execucions.
    """
    try:
        with conn() as c, c.cursor() as cur:
            # Idempotency check primer (early return si ja existeix)
            if idempotency_key:
                cur.execute("SELECT id FROM vault_events WHERE idempotency_key=%s", (idempotency_key,))
                if cur.fetchone():
                    log.info(f"_apply_delta: idempotency hit for {idempotency_key}, skipping")
                    return True

            # Read current state (row lock per safety si hi hagués paral·lelisme)
            cur.execute("SELECT qty, cost_total_usdt FROM vault_inventory WHERE asset=%s FOR UPDATE", (asset,))
            row = cur.fetchone()
            if row is None:
                cur.execute("INSERT INTO vault_inventory (asset, qty, cost_total_usdt) VALUES (%s, 0, 0)", (asset,))
                qty_before, cost_before = 0.0, 0.0
            else:
                qty_before = float(row[0] or 0)
                cost_before = float(row[1] or 0)

            qty_after = qty_before + qty_delta
            cost_after = cost_before + cost_delta

            # Defensiu: no permetre qty negatiu (bug de tracking)
            if qty_after < -1e-8:
                raise ValueError(
                    f"vault_inventory.{asset}: qty would go negative "
                    f"({qty_before} + {qty_delta} = {qty_after}). Refusing."
                )
            if qty_after < 0:
                qty_after = 0
            if cost_after < -1e-8:
                cost_after = 0

            # UPDATE inventory
            cur.execute("""
                UPDATE vault_inventory
                SET qty=%s, cost_total_usdt=%s, updated_at=NOW(),
                    notes=COALESCE(%s, notes)
                WHERE asset=%s
            """, (qty_after, cost_after, notes, asset))

            # INSERT audit event (mateixa transacció)
            cur.execute("""
                INSERT INTO vault_events
                  (event_type, asset, qty_delta, cost_delta_usdt,
                   qty_before, qty_after, cost_before, cost_after,
                   source, idempotency_key, bot_id, bot_name, notes)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (idempotency_key) DO NOTHING
            """, (
                event_type, asset, qty_delta, cost_delta,
                qty_before, qty_after, cost_before, cost_after,
                source, idempotency_key, bot_id, bot_name, notes,
            ))
            c.commit()

        # Telegram notify (best-effort, no bloca si falla)
        if notify_vault_event is not None:
            try:
                notify_vault_event(
                    event_type=event_type, asset=asset,
                    qty_delta=qty_delta, cost_delta_usdt=cost_delta,
                    qty_after=qty_after, cost_after=cost_after,
                    source=source, notes=notes,
                )
            except Exception as _e_n:
                log.warning(f"TG notify failed (best-effort): {_e_n}")

        return True
    except Exception as e:
        log.error(f"_apply_delta({asset}, dq={qty_delta}, dc={cost_delta}) failed: {e}")
        _dump_pending("vault_inventory_delta", {
            "asset": asset, "qty_delta": qty_delta, "cost_delta": cost_delta,
            "event_type": event_type, "source": source,
            "idempotency_key": idempotency_key,
            "bot_id": bot_id, "bot_name": bot_name, "notes": notes,
            "_neon_error": str(e)[:200],
        })
        return False


def add_base(asset: str, qty: float, cost_usdt: float, *,
             source: str, idempotency_key: str | None = None,
             bot_id: str | None = None, bot_name: str | None = None,
             notes: str | None = None) -> bool:
    """Afegeix base recuperat al vault (típicament des d'un bot_close).

    Args:
        asset: BTC, ETH, etc.
        qty: quantitat de base a afegir (positiu)
        cost_usdt: cost total en USDT d'aquesta base (per a avg cost basis)
        source: identificador d'origen ('closer', 'reconcile', etc.)
        idempotency_key: per evitar dobles afegits si reintents
    """
    if qty <= 0:
        log.warning(f"add_base: qty<=0 ({qty}), no-op")
        return False
    return _apply_delta(
        asset=asset, qty_delta=qty, cost_delta=cost_usdt,
        event_type="vault_add_base", source=source,
        idempotency_key=idempotency_key,
        bot_id=bot_id, bot_name=bot_name, notes=notes,
    )


def remove_base(asset: str, qty: float, *,
                source: str, idempotency_key: str | None = None,
                bot_id: str | None = None, bot_name: str | None = None,
                notes: str | None = None) -> bool:
    """Treu base del vault (típicament per a vendre / finançar relocació).

    El cost basis es redueix proporcionalment al qty venut (FIFO no s'aplica
    perquè avg cost és l'estàndard de tracking).
    """
    if qty <= 0:
        log.warning(f"remove_base: qty<=0 ({qty}), no-op")
        return False
    # Llegir avg cost actual per descomptar proporcionalment
    inv = get_inventory()
    asset_d = inv.get(asset)
    if not asset_d or asset_d["qty"] <= 0:
        log.error(f"remove_base({asset}): no qty available")
        return False
    avg = asset_d["avg_cost"] or 0
    cost_delta = -(qty * avg)
    return _apply_delta(
        asset=asset, qty_delta=-qty, cost_delta=cost_delta,
        event_type="vault_remove_base", source=source,
        idempotency_key=idempotency_key,
        bot_id=bot_id, bot_name=bot_name, notes=notes,
    )


def add_usdt(amount: float, *, source: str,
             idempotency_key: str | None = None,
             notes: str | None = None,
             bot_id: str | None = None, bot_name: str | None = None) -> bool:
    """Afegeix USDT a l'inventari.

    Casos d'ús:
      - manual injection des de ComptesLab
      - profit harvest diari
      - venda de vault base per a finançar (la part USDT obtinguda)
    """
    if amount <= 0:
        log.warning(f"add_usdt: amount<=0 ({amount}), no-op")
        return False
    return _apply_delta(
        asset="USDT", qty_delta=amount, cost_delta=amount,
        event_type="vault_add_usdt", source=source,
        idempotency_key=idempotency_key,
        bot_id=bot_id, bot_name=bot_name, notes=notes,
    )


def remove_usdt(amount: float, *, source: str,
                idempotency_key: str | None = None,
                notes: str | None = None,
                bot_id: str | None = None, bot_name: str | None = None) -> bool:
    """Treu USDT del vault (típicament per a finançar un nou bot)."""
    if amount <= 0:
        log.warning(f"remove_usdt: amount<=0 ({amount}), no-op")
        return False
    return _apply_delta(
        asset="USDT", qty_delta=-amount, cost_delta=-amount,
        event_type="vault_remove_usdt", source=source,
        idempotency_key=idempotency_key,
        bot_id=bot_id, bot_name=bot_name, notes=notes,
    )


# ═══════════════════════════════════════════════════════════════════════
# CLI / debug
# ═══════════════════════════════════════════════════════════════════════
def _pretty_print():
    """Imprimeix estat actual + MTM amb preus actuals de Pionex."""
    from pionex_client import get_current_price
    assets = ["BTC", "ETH", "PAXG", "SOL", "USOX", "SPYX"]
    prices = {}
    for a in assets:
        try:
            prices[a] = float(get_current_price(f"{a}_USDT"))
        except Exception as e:
            log.warning(f"price fetch {a}: {e}")
            prices[a] = 0
    mtm = get_inventory_mtm(prices)
    tot = get_total_system_value(prices)

    print(f"\n=== VAULT INVENTORY ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ===")
    print(f"{'Asset':<6}{'Qty':>15}{'Avg Cost':>12}{'Price Now':>12}{'Value MTM':>12}{'PnL':>10}{'%':>8}")
    print("-" * 75)
    for asset, m in mtm.items():
        avg = f"${m['avg_cost']:.4f}" if m['avg_cost'] else "—"
        pnl_s = f"{m['unrealized_pnl']:+.2f}" if m['qty'] else "—"
        pct_s = f"{m['unrealized_pct']:+.1f}%" if m['cost_total_usdt'] else "—"
        print(f"{asset:<6}{m['qty']:>15.8f}{avg:>12}{m['price_now']:>12.4f}"
              f"${m['value_mtm']:>10.2f}{pnl_s:>10}{pct_s:>8}")
    print("-" * 75)
    print(f"{'TOTAL':<6}{'':>15}{'':>12}{'':>12}${tot['total_value_mtm']:>10.2f}"
          f"{tot['total_unrealized_pnl']:>+10.2f}")
    print(f"  vault USDT: ${tot['vault_usdt']:.2f}")
    print(f"  vault base value: ${tot['vault_base_value']:.2f}")
    print(f"  total cost basis: ${tot['total_cost_basis']:.2f}")


if __name__ == "__main__":
    _pretty_print()
