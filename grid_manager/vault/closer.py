"""
vault/closer.py — Detector de breakout per a tancar grids amb NOT_SELL.

Lògica:
  - Per cada bot actiu (BOTS), llegeix estat de Pionex
  - Calcula si està en "breakdown_below" (preu < bottom) confirmat
  - Confirmació: necessita CONFIRM_BARS reads consecutius sota bottom
    (state persisted a `vault_breakout_state` table)
  - Si confirmat → invoca relauncher:
      1. cancel_bot(closeSellModel=NOT_SELL) → recupera base + USDT
      2. Afegeix base recuperat al vault_inventory
      3. Afegeix USDT recuperat a vault USDT
      4. Crida vault.relauncher.relaunch(asset, target_value, recovered_*)

Modes:
  - SHADOW (per defecte): detecta i logueja + TG notify, no executa res
  - LIVE: executa de veritat (només quan tu activis explícitament)

Activació LIVE per asset:
  Edita VAULT_LIVE_ASSETS al final del fitxer per llistar els que vols actius.
  Comença per 1 sol asset (e.g. PAXG) i amplia progressivament.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cloud.db_cloud import conn, cron_run
from config import BOTS
from pionex_client import get_bot_range, get_current_price

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("vault.closer")

# Confirmació breakout: N consecutius sota bottom per actuar
CONFIRM_BARS = 3
# Tolerància: el preu ha de caure aquest mínim sota bottom (evita oscil·lacions)
BREAKOUT_TOLERANCE = 0.001  # 0.1%
# Assets per als quals enablejat LIVE (la resta queden en SHADOW)
# ── ACTIVAT 2026-05-23 per a TOTS els 6 assets ──
VAULT_LIVE_ASSETS: set[str] = {"PAXG", "BTC", "ETH", "SOL", "USOX", "SPYX"}
# Circuit breaker global: max relocations LIVE per hora a nivell sistema.
# Si arribem al límit, els breakouts addicionals queden en SHADOW fins que
# passi 1 hora. Protecció contra cascades de bugs o crashes de mercat.
MAX_LIVE_RELOCS_PER_HOUR = 2
# Per a desactivar tot ràpid: posa VAULT_LIVE_ASSETS = set() i el sistema
# torna a SHADOW al pròxim cycle (5 min).


def _ensure_state_table():
    """Crea (si no existeix) taula per fer tracking dels reads consecutius."""
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vault_breakout_state (
                bot_name VARCHAR(30) PRIMARY KEY,
                bot_id   TEXT,
                last_check_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                consecutive_below INTEGER NOT NULL DEFAULT 0,
                last_price NUMERIC(20,8),
                last_bottom NUMERIC(20,8)
            )
        """)
        c.commit()


def _read_state(bot_name: str) -> dict | None:
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT consecutive_below, last_price, last_bottom FROM vault_breakout_state WHERE bot_name=%s",
                    (bot_name,))
        r = cur.fetchone()
        if not r:
            return None
        return {"consecutive_below": r[0], "last_price": float(r[1] or 0), "last_bottom": float(r[2] or 0)}


def _write_state(bot_name: str, bot_id: str, consecutive_below: int,
                 price: float, bottom: float):
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            INSERT INTO vault_breakout_state (bot_name, bot_id, consecutive_below, last_price, last_bottom)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (bot_name) DO UPDATE SET
                bot_id=EXCLUDED.bot_id,
                consecutive_below=EXCLUDED.consecutive_below,
                last_price=EXCLUDED.last_price,
                last_bottom=EXCLUDED.last_bottom,
                last_check_ts=NOW()
        """, (bot_name, bot_id, consecutive_below, price, bottom))
        c.commit()


def check_breakout(bot_name: str, cfg: dict) -> dict:
    """Llegeix estat actual, actualitza comptador, retorna detecció.

    Returns:
        {bot_name, status, price, bottom, consecutive_below, action_triggered}
    """
    bot_id = cfg["id"]
    try:
        state = get_bot_range(bot_id, symbol=cfg["symbol"])
    except Exception as e:
        return {"bot_name": bot_name, "status": "error", "error": str(e)}

    price = float(state.get("price") or 0)
    bottom = float(state.get("bottom") or 0)
    top = float(state.get("top") or 0)
    pionex_status = str(state.get("status") or "")

    # Guard: dades invàlides
    if price <= 0 or bottom <= 0 or top <= bottom or pionex_status != "running":
        return {"bot_name": bot_name, "status": "invalid_state",
                "price": price, "bottom": bottom, "pionex_status": pionex_status}

    prev = _read_state(bot_name) or {"consecutive_below": 0}

    breakout_threshold = bottom * (1 - BREAKOUT_TOLERANCE)
    is_below = price < breakout_threshold

    new_consecutive = prev["consecutive_below"] + 1 if is_below else 0
    _write_state(bot_name, bot_id, new_consecutive, price, bottom)

    confirmed = new_consecutive >= CONFIRM_BARS

    result = {
        "bot_name": bot_name, "bot_id": bot_id,
        "status": "ok", "price": price, "bottom": bottom, "top": top,
        "is_below": is_below, "consecutive_below": new_consecutive,
        "confirmed_breakout": confirmed,
        "action_triggered": False,
    }

    if not confirmed:
        if is_below:
            log.info(f"  {bot_name}: price ${price:.4f} below bottom ${bottom:.4f} "
                     f"({new_consecutive}/{CONFIRM_BARS} consecutius)")
        return result

    # ── Confirmed breakout ────────────────────────────────────────
    log.warning(f"  {bot_name}: CONFIRMED BREAKOUT (price ${price:.4f} < bottom ${bottom:.4f})")

    asset = cfg["base"]
    is_live = asset in VAULT_LIVE_ASSETS

    if is_live:
        # Circuit breaker: comprovar quantes relocations LIVE s'han fet l'última hora
        with conn() as c, c.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM vault_events
                WHERE event_type IN ('vault_add_base', 'vault_remove_base',
                                      'fund_p1_vault_profit', 'fund_p3_vault_loss',
                                      'fund_p4_own_asset')
                  AND source LIKE 'relaunch_%'
                  AND ts > NOW() - INTERVAL '1 hour'
            """)
            recent_relocs = cur.fetchone()[0] or 0
        if recent_relocs >= MAX_LIVE_RELOCS_PER_HOUR:
            log.warning(f"  {bot_name}: CIRCUIT BREAKER tripped "
                        f"({recent_relocs} events in last hour, max {MAX_LIVE_RELOCS_PER_HOUR})")
            try:
                from notifier import notify
                notify(
                    f"⏸️ Circuit breaker: {bot_name} relocació postposada",
                    f"S'ha detectat un breakout confirmat de *{bot_name.replace('_','-')}* però el circuit breaker ha disparat ({recent_relocs} relocations LIVE l'última hora, màx {MAX_LIVE_RELOCS_PER_HOUR}).\n\nEs reintentarà al pròxim cicle quan el circuit es reseteji.",
                    category="vault_circuit", key=f"cb:{bot_name}", urgent=True,
                )
            except Exception:
                pass
            result["circuit_breaker_tripped"] = True
            return result

        # Invocar relauncher amb mode live
        from vault.relauncher import relaunch_after_breakout
        try:
            relaunch_result = relaunch_after_breakout(
                bot_name=bot_name, bot_id=bot_id, cfg=cfg,
                price_at_breakout=price, dry_run=False,
            )
            result["action_triggered"] = True
            result["relaunch_result"] = relaunch_result
            # Reset comptador
            _write_state(bot_name, bot_id, 0, price, bottom)
        except Exception as e:
            log.error(f"  {bot_name}: relauncher LIVE failed: {e}")
            result["error"] = str(e)
            # Si el LIVE falla, notificar fortament — possiblement bug
            try:
                from notifier import notify
                notify(
                    f"🚨 ERROR LIVE relauncher: {bot_name.replace('_','-')}",
                    f"S'ha intentat una relocació LIVE però ha fallat:\n\n`{str(e)[:300]}`\n\nEl bot original encara està actiu a Pionex (cancel NO executat o NO confirmat). Revisa manualment.",
                    category="vault_error", key=f"err:{bot_name}", urgent=True,
                )
            except Exception:
                pass
    else:
        # SHADOW: log + notify, no execució
        try:
            from notifier import notify
            notify(
                f"🟡 [SHADOW] Breakout detectat: {bot_name}",
                f"S'ha confirmat un breakout per sota a *{bot_name}*.\n\n"
                f"Preu actual: *${price:,.4f}*\n"
                f"Bottom del grid: *${bottom:,.4f}*\n"
                f"Consecutius sota: *{new_consecutive}*\n\n"
                f"_Mode SHADOW: no s'ha executat cap acció. "
                f"Per activar LIVE per a {asset}, afegeix '{asset}' a "
                f"VAULT\\_LIVE\\_ASSETS a closer.py._",
                category="vault_shadow", key=f"breakout_shadow:{bot_name}", urgent=True,
            )
        except Exception as e:
            log.warning(f"TG notify failed: {e}")

        # Invocar relauncher amb dry_run per registrar el pla a vault_events
        try:
            from vault.relauncher import relaunch_after_breakout
            shadow_result = relaunch_after_breakout(
                bot_name=bot_name, bot_id=bot_id, cfg=cfg,
                price_at_breakout=price, dry_run=True,
            )
            result["shadow_result"] = shadow_result
        except Exception as e:
            log.warning(f"  {bot_name}: shadow relauncher failed: {e}")

    return result


def main():
    _ensure_state_table()
    with cron_run("vault_closer") as ctx:
        log.info(f"=== Vault Closer scan ({len(BOTS)} bots) ===")
        if VAULT_LIVE_ASSETS:
            log.info(f"LIVE assets: {VAULT_LIVE_ASSETS}")
        else:
            log.info("SHADOW mode (cap asset en LIVE)")

        results = []
        n_confirmed = 0
        n_triggered = 0
        for name, cfg in BOTS.items():
            r = check_breakout(name, cfg)
            results.append(r)
            if r.get("confirmed_breakout"):
                n_confirmed += 1
            if r.get("action_triggered"):
                n_triggered += 1

        ctx["items"] = len(results)
        ctx["notes"] = f"confirmed={n_confirmed} triggered={n_triggered} mode={'LIVE' if VAULT_LIVE_ASSETS else 'SHADOW'}"
        log.info(f"=== Done: {n_confirmed} confirmed, {n_triggered} triggered ===")


if __name__ == "__main__":
    main()
