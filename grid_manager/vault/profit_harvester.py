"""
vault/profit_harvester.py — Cron diari 22:00 UTC.

Per cada bot actiu:
  1. Llegeix gridProfit i profitWithdrawn actuals de Pionex
  2. Si gridProfit - safety_buffer > min_harvest → extract via API
  3. Afegeix l'USDT recaptat al vault_inventory (asset='USDT')
  4. Logueja a capital_events (event_type='withdraw_profit') + vault_events
  5. TG notify amb resum total

Difere del weekly_rebalance.py existent:
  - WeeklyRebalance: extrae + REINVERTEIX al bot més desbalancejat (compounding intern)
  - ProfitHarvester (aquest): extrae + AFEGEIX A VAULT (per finançar relocacions
    futures, no per re-injectar als grids existents)

Aquests dos crons poden coexistir, però normalment es desactiva un dels dos
segons l'estratègia. El de l'usuari és "afegir a vault, no reinvertir al bot"
→ aquest profit_harvester és el que cal activar.

Safety buffer: deixem $1-2 al gridProfit del bot per evitar que Pionex
reporti errors de "amount too small" al pròxim cycle.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cloud.db_cloud import conn, cron_run, log_capital_event, log_bot_lifecycle
from config import BOTS
from pionex_client import get_bot_order, extract_grid_profit
from vault.inventory import add_usdt

try:
    from notifier import notify, notify_vault_event
except Exception:
    notify = None
    notify_vault_event = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("profit_harvester")

# Configuració
# 2026-05-25: Guillem vol agressivitat màxima — recuperar és gratis a Pionex.
# Per poc que hi hagi al gridProfit, ho extreu. El check post-extract
# (línies 105-124) detecta phantom extracts si Pionex no aplica res.
MIN_HARVEST_USDT = 0.01       # Recuperar tot, fins i tot 1 cèntim
SAFETY_BUFFER_USDT = 0.0      # Sense buffer — agafem tot el profit disponible
DRY_RUN = False               # Posa True per simular sense executar


def harvest_one(bot_name: str, cfg: dict) -> dict:
    """Extreu profit d'un bot. Retorna {ok, amount, error}.

    BUG FIX 2026-05-24: ANTES usava `gridProfit` cumulatiu, comptant doble
    profits que ja s'havien extret en cycles anteriors. Pionex llavors
    rebutjava silenciosament o donava menys del demanat, però nosaltres
    apuntàvem el demanat (no el rebut) → vault inflat amb USDT fantasma.

    Fix correcte: `extractable = gridProfit - profitWithdrawn - SAFETY_BUFFER`
    (mateix que weekly_rebalance.py ja feia bé).
    A més verifiquem el delta REAL a Pionex post-extract (qti_before vs qti_after).
    """
    bot_id = cfg["id"]
    try:
        d = get_bot_order(bot_id)
    except Exception as e:
        return {"ok": False, "amount": 0, "error": f"get_bot_order: {e}"}

    bu = d.get("buOrderData") or {}
    grid_profit = float(bu.get("gridProfit") or 0)
    # CRITIC: profitWithdrawn és la SUMA cumulative ja extreta lifetime.
    # Disponible REAL = gridProfit lifetime - ja extret lifetime.
    profit_withdrawn = float(bu.get("profitWithdrawn") or 0)
    qti_before = float(bu.get("quoteTotalInvestment") or 0)
    quote_before = float(bu.get("quoteAmount") or 0)  # USDT dins el bot

    real_available = grid_profit - profit_withdrawn
    extractable = real_available - SAFETY_BUFFER_USDT
    if extractable < MIN_HARVEST_USDT:
        return {"ok": True, "amount": 0, "skipped": True,
                "grid_profit": grid_profit,
                "profit_withdrawn": profit_withdrawn,
                "real_available": real_available,
                "reason": f"real_available ${real_available:.4f} below threshold (gp=${grid_profit:.4f} - withdrawn=${profit_withdrawn:.4f})"}

    extract_amount = round(extractable, 4)
    log.info(f"  {bot_name}: gridProfit=${grid_profit:.4f} - withdrawn=${profit_withdrawn:.4f} = ${real_available:.4f} available, extracting ${extract_amount:.4f}")

    if DRY_RUN:
        return {"ok": True, "amount": extract_amount, "dry_run": True}

    # Executa l'extracció a Pionex
    try:
        result = extract_grid_profit(bot_id, extract_amount)
    except Exception as e:
        return {"ok": False, "amount": 0, "error": f"extract_grid_profit: {e}"}

    if not result.get("result"):
        return {"ok": False, "amount": 0, "error": f"Pionex returned non-result: {result}"}

    # VERIFICACIO POST: llegeix bot un altre cop per veure si quoteAmount va baixar
    # de veritat (Pionex de vegades retorna result=True però no aplica si no hi
    # ha prou disponible). Esperem 2s.
    import time
    time.sleep(2)
    try:
        d_after = get_bot_order(bot_id)
        bu_after = d_after.get("buOrderData") or {}
        quote_after = float(bu_after.get("quoteAmount") or 0)
        real_extracted = quote_before - quote_after
        if real_extracted < 0.01:
            log.warning(f"  {bot_name}: Pionex result=True però quoteAmount NO baixa "
                        f"({quote_before:.4f} → {quote_after:.4f}). NO logging fals.")
            return {"ok": False, "amount": 0, "error": "phantom extract — Pionex no va aplicar"}
        if abs(real_extracted - extract_amount) > 0.01:
            log.warning(f"  {bot_name}: Pionex va donar ${real_extracted:.4f} en lloc dels ${extract_amount:.4f} demanats")
        # Usem el delta REAL per al log al vault
        extract_amount = real_extracted
    except Exception as e:
        log.warning(f"  {bot_name}: no he pogut verificar post-extract ({e}), usant amount demanat")

    # Logueja a capital_events (event existent, no nou)
    now = datetime.now(timezone.utc)
    idem_key = f"harvest_{bot_name}_{now.strftime('%Y%m%d')}"
    try:
        log_capital_event(
            bot_id=bot_id, bot_name=bot_name,
            event_type="withdraw_profit",
            amount_usdt=extract_amount,
            source="vault.profit_harvester",
            qti_before=qti_before,
            qti_after=qti_before,  # qti no canvia, només es treu profit
            grid_profit_snapshot=grid_profit,
            idempotency_key=idem_key,
            notes=f"Daily harvest → vault USDT. SAFETY_BUFFER=${SAFETY_BUFFER_USDT}",
            created_by="vault.profit_harvester",
            ts=now,
        )
        log_bot_lifecycle(
            bot_id=bot_id, bot_name=bot_name,
            event_type="profit_harvest_to_vault",
            last_grid_profit=grid_profit,
            last_quote_invested=qti_before,
            notes=f"Extracted ${extract_amount:.4f} → vault USDT",
            detected_by="vault.profit_harvester",
        )
    except Exception as e:
        log.warning(f"  {bot_name}: capital_events log failed: {e}")

    # Afegeix al vault USDT (dispara TG notify per defecte)
    add_usdt(
        amount=extract_amount,
        source=f"profit_harvester/{bot_name}",
        idempotency_key=idem_key + "_vault",
        notes=f"Daily harvest from {bot_name} grid",
    )

    return {"ok": True, "amount": extract_amount,
            "grid_profit_before": grid_profit}


def main():
    with cron_run("vault_profit_harvester") as ctx:
        results = {}
        total_harvested = 0.0
        n_ok = 0
        n_skip = 0
        n_fail = 0

        log.info(f"=== Profit Harvester start ({len(BOTS)} bots) ===")
        if DRY_RUN:
            log.info("DRY_RUN=True (cap execució real)")

        for name, cfg in BOTS.items():
            r = harvest_one(name, cfg)
            results[name] = r
            if r.get("ok"):
                if r.get("skipped"):
                    n_skip += 1
                else:
                    n_ok += 1
                    total_harvested += r.get("amount", 0)
            else:
                n_fail += 1
                log.error(f"  {name}: FAIL {r.get('error')}")

        log.info(f"=== Harvest done: extracted=${total_harvested:.4f} "
                 f"from {n_ok} bots ({n_skip} skipped, {n_fail} failed) ===")

        ctx["items"] = n_ok
        ctx["notes"] = (
            f"harvested=${total_harvested:.4f} ok={n_ok} skip={n_skip} fail={n_fail}"
        )

        # TG resum (un sol missatge agregat, no un per bot, per evitar spam)
        if total_harvested > 0 and notify is not None:
            def _esc(s):  # Markdown escape per evitar parse errors
                return str(s).replace("_", "\\_").replace("*", "\\*")
            lines = []
            for name, r in results.items():
                if r.get("ok") and r.get("amount", 0) > 0:
                    lines.append(f"• {_esc(name)}: +${r['amount']:.4f}")
            body = (
                f"S'han recollit profits de {n_ok} bots actius:\n\n" +
                "\n".join(lines) +
                f"\n\n*Total a la reserva: +${total_harvested:.4f}*"
            )
            if n_fail > 0:
                body += f"\n\n⚠️ {n_fail} bots han fallat (revisar logs)"
            try:
                notify(f"🌾 Profits recollits: +${total_harvested:.2f}",
                       body, category="vault", key="profit_harvest_daily",
                       urgent=True)
            except Exception as e:
                log.warning(f"TG notify failed: {e}")


if __name__ == "__main__":
    main()
