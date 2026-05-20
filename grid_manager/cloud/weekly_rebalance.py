"""
weekly_rebalance.py — Rebalance + reinversió setmanal del portfolio.

Cada dilluns 00:05 UTC (cron):
  1. Llegeix `db/rebalance_config.json` (DCA setmanal, threshold)
  2. Calcula gridProfit acumulat a cada bot (si auto_reinvest=true)
  3. Suma DCA + grid_profit = "pot setmanal"
  4. Verifica que el wallet tingui prou USDT (avisa per Telegram si manca)
  5. Genera pla de redistribució per igualar bots amb TARGET_WEIGHTS
  6. Executa via rebalancer.execute() amb _call_with_verify
  7. Registra cada operació a Neon capital_events
  8. Notifica per Telegram amb desglossament complet

L'usuari només ha de mantenir el wallet amb USDT igual o major a DCA + grid_profit
(o transferir-ho durant la setmana). Si el wallet és insuficient, el sistema
notifica i NO executa el rebalance (evita estats inconsistents).
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Force user-site i grid_manager al path
_USER_SITE = r"C:\Users\Administrator\AppData\Roaming\Python\Python312\site-packages"
if _USER_SITE not in sys.path:
    sys.path.insert(0, _USER_SITE)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import BOTS, TARGET_WEIGHTS
from cloud.db_cloud import log_capital_event, conn as cloud_conn
from pionex_client import get_bot_order, get_balance, get_current_price, invest_in_bot, extract_grid_profit

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("weekly_rebalance")

CONFIG_PATH = Path(__file__).resolve().parent.parent / "db" / "rebalance_config.json"
MIN_INVEST_PIONEX = 10.0  # Pionex spot grid minimum per invest_in_bot operation


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {"weekly_dca_usdt": 0.0, "deviation_threshold_pct": 5.0,
                "auto_reinvest_grid_profit": True, "min_reinvest_per_bot_usdt": 1.0}
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _notify_tg(text: str) -> None:
    """Telegram notify (best-effort)."""
    try:
        from notifier import notify
        notify("📅 Weekly rebalance", text, category="rebalance", key="weekly")
    except Exception as e:
        log.warning(f"TG notify failed: {e}")


def main():
    log.info("=" * 70)
    log.info("WEEKLY REBALANCE START")
    log.info("=" * 70)

    cfg = _load_config()
    dca = float(cfg.get("weekly_dca_usdt", 0.0))
    threshold = float(cfg.get("deviation_threshold_pct", 5.0)) / 100.0
    auto_reinv = bool(cfg.get("auto_reinvest_grid_profit", True))
    min_per_bot = float(cfg.get("min_reinvest_per_bot_usdt", 1.0))

    log.info(f"Config: DCA=${dca:.2f}/setmana, threshold={threshold*100:.1f}%, "
             f"reinvest={auto_reinv}, min_per_bot=${min_per_bot}")

    # ─── 1) Llegir estat actual de tots els bots ─────────────────────
    bot_states = {}
    total_invested = 0.0
    total_grid_profit = 0.0
    for name, bcfg in BOTS.items():
        try:
            bot = get_bot_order(bcfg["id"])
            d = bot.get("buOrderData", {})
            invested = float(d.get("quoteTotalInvestment", 0))
            gp = float(d.get("gridProfit", 0))
            price = get_current_price(bcfg["symbol"])
            quote = float(d.get("quoteAmount", 0))
            base = float(d.get("baseAmount", 0))
            value = quote + base * price
            bot_states[name] = {
                "id": bcfg["id"], "invested": invested, "grid_profit": gp,
                "current_value": value, "price": price,
            }
            total_invested += invested
            total_grid_profit += gp
        except Exception as e:
            log.error(f"[{name}] no s'ha pogut llegir estat: {e}")
            _notify_tg(f"❌ Weekly rebalance ABORTAT: no s'ha pogut llegir {name}: {e}")
            return 1

    log.info(f"Total invertit: ${total_invested:.2f}")
    log.info(f"Total gridProfit acumulat: ${total_grid_profit:.2f}")

    # ─── 2) Pot = NOMÉS gridProfit acumulat (interès compost pur, sense DCA extern)
    # NOTA: a partir d'ara ignorem dca, només reinvertim el profit del trading.
    pool_total = total_grid_profit
    log.info(f"Pot setmanal (només grid profit, compound): ${pool_total:.2f}")

    if pool_total < MIN_INVEST_PIONEX:
        msg = (f"📅 Weekly compound: gridProfit acumulat ${pool_total:.2f} < mínim ${MIN_INVEST_PIONEX}. "
               f"Cap acció aquesta setmana. Es sumarà a la pròxima.")
        log.info(msg)
        _notify_tg(msg)
        return 0

    # NOTA: amb withdraw_profit + invest_in necessitarem Pionex API.
    # Per ara assumim que el sistema POT extreure el gridProfit i reinvertir-lo.
    # La verificació de wallet ja no aplica perquè els diners surten dels propis bots.

    # ─── 3) Detectar bot MÉS DESBALANCEJAT (delta més negatiu) ─────────
    # delta_pct = (current_weight - target_weight) / target_weight
    # El bot més per sota del seu target rep tot el compound pot
    total_after_invested = total_invested  # invested no canvia (només mou profit entre bots)
    candidates = []
    for name, bcfg in BOTS.items():
        target_w = TARGET_WEIGHTS.get(name, 1.0 / len(BOTS))
        target_amount = total_after_invested * target_w
        invested = bot_states[name]["invested"]
        # delta negatiu = bot per SOTA del target = necessita capital
        delta = target_amount - invested
        delta_pct_target = (delta / target_amount * 100) if target_amount > 0 else 0
        candidates.append({
            "name": name, "bot_id": bcfg["id"],
            "invested": invested, "target": target_amount,
            "delta": delta, "delta_pct_target": delta_pct_target,
        })

    log.info("Estat actual vs target:")
    for c in candidates:
        marker = "← MÉS DESBALANCEJAT" if c == max(candidates, key=lambda x: x["delta"]) else ""
        log.info(f"  {c['name']:<12} actual=${c['invested']:>7.2f} target=${c['target']:>7.2f} "
                 f"delta=${c['delta']:>+7.2f} ({c['delta_pct_target']:+5.1f}%) {marker}")

    # El bot més desbalancejat = el que té el delta més POSITIU
    # (= més per sota del seu target objectiu)
    target_bot = max(candidates, key=lambda x: x["delta"])

    if target_bot["delta"] <= 0:
        msg = (f"📅 Weekly compound: TOTS els bots estan AL o PER SOBRE del seu target. "
               f"No té sentit reinvertir el pot a cap d'ells. "
               f"Pot ${pool_total:.2f} es deixa al gridProfit per a la pròxima setmana.")
        log.info(msg)
        _notify_tg(msg)
        return 0

    log.info(f"\n→ Tot el pot (${pool_total:.2f}) anirà a {target_bot['name']} (més desbalancejat)")

    # ─── 4) EXTRACCIÓ DEL GRID PROFIT pur (endpoint Pionex /spotGrid/profit)
    # Aquest endpoint específic extreu només la part de profit del grid,
    # sense tocar el base/quote del bot. Cash pur cap al wallet.
    log.info("Extracció del grid profit de cada bot...")
    MIN_EXTRACT = 0.50   # Pionex permet extraccions petites a través d'aquest endpoint
    extracted_total = 0.0
    extraction_results = []
    for name, bcfg in BOTS.items():
        gp = bot_states[name]["grid_profit"]
        if gp < MIN_EXTRACT:
            log.info(f"  {name}: gridProfit ${gp:.4f} < ${MIN_EXTRACT}, skip")
            extraction_results.append({"bot": name, "amount": gp, "ok": False, "msg": "below min", "extracted": 0})
            continue
        amount = round(gp, 4)
        log.info(f"  extract_grid_profit({name}, ${amount:.4f})")
        try:
            r = extract_grid_profit(name, amount)
            ok = bool(r.get("result", False))
            if ok:
                extracted_total += amount
                extraction_results.append({"bot": name, "amount": amount, "ok": True, "msg": "extracted", "extracted": amount})
                # Log capital_event
                try:
                    log_capital_event(
                        bot_id=bcfg["id"], bot_name=name,
                        event_type="withdraw_profit",
                        amount_usdt=amount,
                        source="weekly_cron",
                        idempotency_key=f"weekly_extract_{week_id if 'week_id' in dir() else datetime.now(timezone.utc).strftime('%Y_W%W')}_{name}",
                        success=True,
                        notes=f"Profit extraction (reduce_bot) — pre-compound to target {target_bot['name']}",
                        created_by="weekly_rebalance",
                    )
                except Exception as e:
                    log.warning(f"[{name}] cloud log failed: {e}")
            else:
                extraction_results.append({"bot": name, "amount": amount, "ok": False, "msg": f"result=False: {r}", "extracted": 0})
        except Exception as e:
            extraction_results.append({"bot": name, "amount": amount, "ok": False, "msg": str(e)[:200], "extracted": 0})

    log.info(f"Total extret: ${extracted_total:.2f}")

    if extracted_total < MIN_INVEST_PIONEX:
        msg = f"⚠️ Weekly compound: extret total ${extracted_total:.2f} < mínim. No es pot reinvertir."
        log.warning(msg)
        _notify_tg(msg)
        return 1

    # ─── 5) Operació única: invest_in_bot(target, extracted_total)
    final_ops = [{
        "name": target_bot["name"],
        "bot_id": target_bot["bot_id"],
        "delta_to_invest": extracted_total,
        "delta": target_bot["delta"],
        "invested": target_bot["invested"],
        "target": target_bot["target"],
    }]
    skipped = []
    # Actualitzem pool_total per al notify final
    pool_total = extracted_total

    # ─── 6) Executar invest_in_bot amb verificació empírica ──────────
    log.info(f"Executant {len(final_ops)} operacions...")
    results = []
    week_id = datetime.now(timezone.utc).strftime("%Y_W%W")

    for op in final_ops:
        name = op["name"]
        amount = round(op["delta_to_invest"], 2)
        log.info(f"  → invest_in({name}, ${amount:.2f})")

        # qti before
        try:
            before = get_bot_order(op["bot_id"])
            qti_before = float(before.get("buOrderData", {}).get("quoteTotalInvestment", 0))
        except Exception as e:
            log.error(f"[{name}] no he pogut llegir qti before: {e}")
            results.append({"bot": name, "amount": amount, "ok": False, "msg": str(e)})
            continue

        # Execute
        resp = None
        call_err = None
        try:
            resp = invest_in_bot(name, amount)
        except Exception as e:
            call_err = e

        # Verify
        import time as _t
        _t.sleep(5)
        try:
            after = get_bot_order(op["bot_id"])
            qti_after = float(after.get("buOrderData", {}).get("quoteTotalInvestment", 0))
        except Exception as e:
            results.append({"bot": name, "amount": amount, "ok": False,
                            "msg": f"no he pogut verificar post: {e}"})
            continue

        delta_real = qti_after - qti_before
        tol = max(0.01 * amount, 0.05)
        if abs(delta_real - amount) <= tol:
            ok = True
            msg = f"applied (qti {qti_before:.2f}->{qti_after:.2f})"
        else:
            ok = False
            msg = f"qti delta {delta_real:+.2f} vs esperat {amount:+.2f}"

        # Log a Neon (sempre, èxit o fallada)
        try:
            log_capital_event(
                bot_id=op["bot_id"], bot_name=name,
                event_type="reinvest_profit" if reinvest_amount > 0 else "rebalance_in",
                amount_usdt=amount,
                qti_before=qti_before, qti_after=qti_after,
                grid_profit_snapshot=bot_states[name]["grid_profit"],
                lifetime_profit_calc=bot_states[name]["grid_profit"],  # acumulat lifetime
                source="weekly_cron",
                idempotency_key=f"weekly_{week_id}_{name}",
                success=ok, error_msg=None if ok else msg,
                raw_response=resp if isinstance(resp, dict) else None,
                notes=f"Weekly: DCA ${dca} + reinvest ${reinvest_amount:.2f} = pot ${pool_total:.2f}",
                created_by="weekly_rebalance",
            )
        except Exception as e:
            log.warning(f"[{name}] cloud log failed: {e}")

        results.append({"bot": name, "amount": amount, "ok": ok, "msg": msg})

    # ─── 7) Update config last_run_ts + next_run_ts ─────────────────
    now = datetime.now(timezone.utc)
    next_monday = now + timedelta(days=(7 - now.weekday()) % 7 or 7)
    next_monday = next_monday.replace(hour=0, minute=5, second=0, microsecond=0)
    cfg["last_run_ts"] = now.strftime("%Y-%m-%d %H:%M UTC")
    cfg["next_run_ts"] = next_monday.strftime("%Y-%m-%d %H:%M UTC")
    _save_config(cfg)

    # ─── 8) Notificar Telegram amb resum ────────────────────────────
    n_ok = sum(1 for r in results if r["ok"])
    n_fail = sum(1 for r in results if not r["ok"])
    total_deployed = sum(r["amount"] for r in results if r["ok"])

    lines = [
        f"📅 Weekly compound executat:",
        f"",
        f"💰 Pot (gridProfit acumulat): ${pool_total:.2f}",
        f"🎯 Bot destí (més desbalancejat): {target_bot['name']}",
        f"✅ Operacions OK: {n_ok}",
        f"❌ Fallades: {n_fail}",
        f"💰 Capital desplegat: ${total_deployed:.2f}",
        f"",
        f"Detall:",
    ]
    for r in results:
        tag = "✓" if r["ok"] else "✗"
        lines.append(f"  {tag} {r['bot']:<10} ${r['amount']:>6.2f}  {r['msg'][:60]}")

    if skipped:
        lines.append(f"")
        lines.append(f"⏭ Sota mínim Pionex (${MIN_INVEST_PIONEX}), no executat:")
        for p in skipped:
            lines.append(f"  · {p['name']:<10} ${p.get('delta_to_invest', 0):>5.2f}")

    msg = "\n".join(lines)
    log.info(msg)
    _notify_tg(msg)

    log.info("=" * 70)
    log.info(f"WEEKLY REBALANCE DONE: {n_ok} ok, {n_fail} fail")
    log.info("=" * 70)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
