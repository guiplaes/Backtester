"""
Multi-bot trailing monitor — runs every 5 min via Task Scheduler.
Polls each bot in BOTS dict, logs snapshot, triggers trailing if needed.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone

# Ensure user-site packages are importable when run from Task Scheduler
_user_site = r"C:\Users\Administrator\AppData\Roaming\Python\Python312\site-packages"
if os.path.isdir(_user_site) and _user_site not in sys.path:
    sys.path.insert(0, _user_site)

from config import BOTS, EDGE_TRIGGER_PCT, LOG_DIR
from pionex_client import get_bot_range, get_atr, adjust_grid_range
# LEGACY: log_snapshot/log_decision/log_recolocation_cost del SQLite local.
# A partir de Neon=única-DB ja NO escrivim a SQLite. Stub silenciós.
def log_snapshot(*a, **kw): pass
def log_decision(*a, **kw): pass
def log_recolocation_cost(*a, **kw): pass
from notifier import notify_trigger, notify_rebalance, notify_error


def _compute_fee_consumed_usdt(state: dict, price: float) -> float:
    """Consum ACUMULAT de fees del bot des de la seva creacio, en USDT.

    Pionex pre-reserva *_fee_reserve al crear el bot i va descomptant del *_fee_remain
    cada cop que paga una fee (cycles normals + recolocacions). El gridProfit que reporta
    JA descompta les fees dels cycles normals; per tant nomes el COST INCREMENTAL durant
    una recolocacio (diff de consum entre snapshot abans i despres) ens dona el cost real
    afegit que cal restar del Grid Alpha.

    Retorna: (q_reserve - q_remain) + (b_reserve - b_remain) * price
    """
    try:
        b_res = float(state.get("base_fee_reserve", 0) or 0)
        b_rem = float(state.get("base_fee_remain", 0) or 0)
        q_res = float(state.get("quote_fee_reserve", 0) or 0)
        q_rem = float(state.get("quote_fee_remain", 0) or 0)
        return (q_res - q_rem) + (b_res - b_rem) * price
    except Exception:
        return 0.0


LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=LOG_DIR / "monitor.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("monitor")


def check_bot(name: str, cfg: dict):
    """Check one bot. Return action dict."""
    try:
        state = get_bot_range(cfg["id"], symbol=cfg["symbol"])
    except Exception as e:
        log.error(f"[{name}] failed to get state: {e}")
        return {"bot": name, "action": "error", "error": str(e)}

    # Attach metadata
    state["bot_name"] = name
    state["bot_id"] = cfg["id"]
    state["symbol"] = cfg["symbol"]

    # ── Guard: dades invàlides de l'API ──────────────────────────────
    # Pionex de vegades retorna top=0/bottom=0/status=unknown durant glitches
    # (sovint paral·lel a SSL errors). Si actuem amb aquestes dades el bot
    # es re-centra falsament. Detectem i saltem.
    top = float(state.get("top") or 0)
    bottom = float(state.get("bottom") or 0)
    price = float(state.get("price") or 0)
    status = str(state.get("status") or "")
    if top <= 0 or bottom <= 0 or top <= bottom or price <= 0 or status != "running":
        log.warning(f"[{name}] invalid state from Pionex (top={top} bottom={bottom} "
                    f"price={price} status={status!r}) — skipping cycle")
        return {"bot": name, "action": "skip_invalid_state",
                "top": top, "bottom": bottom, "price": price, "status": status}

    log_snapshot(state)
    log.info(
        f"[{name}] price={state['price']:.6g} range=[{state['bottom']:.6g}-{state['top']:.6g}] "
        f"top_dist={state['dist_to_top_pct']:.1%} bot_dist={state['dist_to_bottom_pct']:.1%}"
    )

    # Trigger conditions
    if state["dist_to_top_pct"] <= EDGE_TRIGGER_PCT:
        return _trigger(name, cfg, "near_upper_edge", state)
    if state["dist_to_bottom_pct"] <= EDGE_TRIGGER_PCT:
        return _trigger(name, cfg, "near_lower_edge", state)
    if state["price"] > state["top"]:
        return _trigger(name, cfg, "breakout_above", state)
    if state["price"] < state["bottom"]:
        return _trigger(name, cfg, "breakdown_below", state)

    return {"bot": name, "action": "no_trigger", "state": state}


def _trigger(name: str, cfg: dict, reason: str, state: dict):
    """Execute trailing reposition via direct REST call (no Claude).

    Captura SNAPSHOT abans i després de l'adjust_params per calcular el cost real
    de la recolocació (fees consumides + reducció eventual de gridProfit).
    """
    log.warning(f"[{name}] TRIGGER: {reason} at price {state['price']}")

    # ── SNAPSHOT ABANS de la recolocació ──
    gp_before = float(state.get("grid_profit", 0))
    consumed_before = _compute_fee_consumed_usdt(state, state["price"])
    log.info(f"[{name}] PRE-reloc: gridProfit={gp_before:.6f}, feeConsumed_USDT={consumed_before:.4f}")

    half_w = state["price"] * cfg["width_pct"] / 2
    new_top = state["price"] + half_w
    new_bottom = state["price"] - half_w
    rows = cfg.get("rows", 12)

    err_msg = None
    try:
        result = adjust_grid_range(cfg["id"], top=new_top, bottom=new_bottom, row=rows)
        log.info(f"[{name}] adjust_params executed: new_range=[{new_bottom:.6g}, {new_top:.6g}] result={result.get('result')}")
        action_taken = "adjust_params_ok"
        ok = result.get("result", False)
        if not ok:
            err_msg = str(result)[:300]
    except Exception as e:
        # SSL EOF, connection drop, timeout: la petició POT haver-se aplicat
        # tot i que no hem rebut resposta. Verifiquem amb un GET abans de tirar la tovallola.
        log.error(f"[{name}] adjust_params FAILED: {e}")
        err_str = str(e)
        transient_markers = ("SSL", "EOF", "Connection", "Timeout",
                             "RemoteDisconnected", "ProtocolError",
                             "Max retries", "ReadTimeout")
        is_transient = any(m in err_str for m in transient_markers)

        action_taken = "adjust_params_error"
        ok = False
        err_msg = err_str[:300]

        if is_transient:
            import time as _t
            _t.sleep(5)
            try:
                verified = get_bot_range(cfg["id"], symbol=cfg["symbol"])
                actual_top = float(verified.get("top", 0))
                actual_bottom = float(verified.get("bottom", 0))
                # Tolerància 0.5% per cobrir arrodoniments de Pionex
                if actual_top > 0 and actual_bottom > 0 \
                   and abs(actual_top - new_top) / new_top < 0.005 \
                   and abs(actual_bottom - new_bottom) / new_bottom < 0.005:
                    log.warning(f"[{name}] SSL error però rang VERIFICAT aplicat a Pionex "
                                f"[{actual_bottom:.6g},{actual_top:.6g}] — tractant com OK")
                    action_taken = "adjust_params_ok_after_ssl_verify"
                    ok = True
                    err_msg = None
                else:
                    log.warning(f"[{name}] rang NO aplicat (Pionex: [{actual_bottom:.6g},{actual_top:.6g}] "
                                f"vs desitjat [{new_bottom:.6g},{new_top:.6g}]) — reintentant adjust_params")
                    _t.sleep(3)
                    try:
                        result = adjust_grid_range(cfg["id"], top=new_top, bottom=new_bottom, row=rows)
                        log.info(f"[{name}] adjust_params RETRY succeeded: result={result.get('result')}")
                        action_taken = "adjust_params_ok_after_retry"
                        ok = bool(result.get("result", False))
                        err_msg = None if ok else str(result)[:300]
                    except Exception as e2:
                        log.error(f"[{name}] retry també ha fallat: {e2}")
                        action_taken = "adjust_params_error_after_retry"
                        err_msg = f"orig: {err_str[:140]} | retry: {str(e2)[:140]}"
            except Exception as e_check:
                log.error(f"[{name}] no he pogut verificar estat post-fail: {e_check}")
                action_taken = "adjust_params_error_unverified"
                err_msg = f"orig: {err_str[:200]} | verify: {str(e_check)[:80]}"

    # ── SNAPSHOT DESPRÉS de la recolocació (només si OK) ──
    cost_recolocation = 0.0
    if ok:
        try:
            import time as _t
            _t.sleep(2)  # deixem 2s perquè Pionex actualitzi els camps internament
            state_after = get_bot_range(cfg["id"], symbol=cfg["symbol"])
            gp_after = float(state_after.get("grid_profit", 0))
            consumed_after = _compute_fee_consumed_usdt(state_after, state_after["price"])

            grid_delta = gp_after - gp_before
            # Consum incremental durant la finestra de la recolocacio.
            # Monotonic creixent → delta sempre >= 0 (excepte arrodonim­ents API).
            fee_delta = consumed_after - consumed_before
            cost_recolocation = max(0.0, fee_delta) + max(0.0, -grid_delta)

            log.info(
                f"[{name}] POST-reloc: gridProfit={gp_after:.6f} (Δ {grid_delta:+.6f}), "
                f"feeConsumed_USDT={consumed_after:.4f} (Δ {fee_delta:+.4f}), "
                f"cost_recolocation=${cost_recolocation:.4f}"
            )

            # Registra el cost a la DB. Els camps "fee_pool_before/after" reciclen el
            # significat: ara guarden el CONSUM ACUMULAT abans/despres (no el remain).
            log_recolocation_cost(
                bot_id=cfg["id"], bot_name=name, trigger=reason,
                price=state["price"], new_top=new_top, new_bottom=new_bottom,
                grid_profit_before=gp_before, grid_profit_after=gp_after,
                fee_pool_before=consumed_before, fee_pool_after=consumed_after,
            )

            # ── Espill a Neon (STRICT: Neon és font autoritzativa). ──
            # Si Neon falla, alertem fort (notify_error) i marquem failure_count
            # perquè el watchdog ho detecti. NO retry silenciós: el reconcile cron
            # diari es la xarxa de seguretat per omplir gaps temporals de network.
            try:
                from cloud.db_cloud import log_recolocation as _cloud_reloc, snapshot_bot_state as _snapshot
                _cloud_reloc(
                    bot_id=cfg["id"], bot_name=name, trigger=reason,
                    price_at_trigger=state["price"],
                    old_top=float(state.get("top") or 0),
                    old_bottom=float(state.get("bottom") or 0),
                    new_top=new_top, new_bottom=new_bottom,
                    grid_profit_before=gp_before, grid_profit_after=gp_after,
                    fee_consumed_before=consumed_before, fee_consumed_after=consumed_after,
                    cost_usdt=cost_recolocation, executed=ok,
                    action_taken=action_taken, error_msg=err_msg,
                    idempotency_key=f"reloc_{cfg['id']}_{int(state.get('price',0)*1000)}_{action_taken}",
                )
                # Event snapshot POST-reloc: captura estat per a la gràfica evolutiva
                if ok:
                    try:
                        _snapshot(
                            bot_id=cfg["id"], bot_name=name,
                            event_type="recolocation", source="monitor",
                            price=state["price"],
                            notes=f"reloc {reason}: cost ${cost_recolocation:.4f}",
                        )
                    except Exception as _e_snap:
                        log.warning(f"[{name}] event_snapshot post-reloc fail: {_e_snap}")
            except Exception as _e_cloud:
                log.error(f"[{name}] CRITICAL: cloud reloc log failed — Neon desincronitzat: {_e_cloud}")
                # Fallback: dump a fitxer JSON local perquè sync_health (cron 1min)
                # ho reculli i ho insereixi a Neon retroactivament. Evita perdre
                # records per transient ImportErrors o errors de connexió.
                try:
                    import json as _json
                    pending_path = LOG_DIR / "pending_recolocations.jsonl"
                    payload = {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "bot_id": cfg["id"], "bot_name": name, "trigger": reason,
                        "price_at_trigger": state["price"],
                        "old_top": float(state.get("top") or 0),
                        "old_bottom": float(state.get("bottom") or 0),
                        "new_top": new_top, "new_bottom": new_bottom,
                        "grid_profit_before": gp_before, "grid_profit_after": gp_after,
                        "fee_consumed_before": consumed_before, "fee_consumed_after": consumed_after,
                        "cost_usdt": cost_recolocation, "executed": ok,
                        "action_taken": action_taken, "error_msg": err_msg,
                        "import_error": str(_e_cloud)[:200],
                    }
                    with open(pending_path, "a", encoding="utf-8") as _f:
                        _f.write(_json.dumps(payload) + "\n")
                except Exception as _e_dump:
                    log.error(f"[{name}] CRITICAL: also failed to dump fallback JSON: {_e_dump}")
                try:
                    notify_error(f"NEON LOG FAIL {name}",
                                 f"Reloc executada localment OK però NO loggada a Neon. "
                                 f"Guardat a pending_recolocations.jsonl per sync_health. "
                                 f"Error: {str(_e_cloud)[:200]}")
                except Exception:
                    pass
        except Exception as e:
            log.warning(f"[{name}] post-reloc snapshot failed: {e}")

    log_decision(
        trigger=reason,
        bot_id=cfg["id"],
        snapshot=state,
        action=action_taken,
        reasoning=f"Auto-trailing: center to {state['price']:.6g}, range ±{half_w:.6g}",
        new_range=(new_bottom, new_top),
        cost=cost_recolocation,
        executed=ok,
    )

    # Notificació TG (amb cost real)
    try:
        notify_trigger(name, reason, state["price"], new_bottom, new_top, ok)
        if cost_recolocation > 0.10:  # només alertem si cost > $0.10 (evita spam)
            notify_error(f"reloc cost {name}", f"Recolocació ha costat ${cost_recolocation:.4f}")
        if not ok and err_msg:
            notify_error(f"adjust_grid {name}", err_msg)
    except Exception as e:
        log.warning(f"notify failed: {e}")

    return {"bot": name, "action": "triggered", "reason": reason, "new_range": [new_bottom, new_top],
            "ok": ok, "cost": cost_recolocation}


def _build_bot_states_for_rebalance() -> dict:
    """Construeix bot_states en el format que el rebalancer espera (com el dashboard)."""
    from pionex_client import get_current_price
    states = {}
    btc_usdt_price = 0
    try:
        btc_usdt_price = get_current_price("BTC_USDT")
    except Exception:
        pass
    for name, cfg in BOTS.items():
        try:
            s = get_bot_range(cfg["id"], symbol=cfg["symbol"])
            quote_in = s.get("quote_in_bot", 0)
            base_in = s.get("base_in_bot", 0)
            quote_val = quote_in if cfg["quote"] == "USDT" else quote_in * btc_usdt_price
            base_val = base_in * s.get("price", 0)
            if cfg["quote"] == "BTC":
                base_val *= btc_usdt_price
            s["total_value_usdt"] = quote_val + base_val
            s["cfg"] = cfg
            states[name] = s
        except Exception as e:
            log.error(f"[rebalance] failed to get state for {name}: {e}")
    return states


def main():
    log.info(f"=== Monitor cycle start: {len(BOTS)} bots ===")
    results = []
    for name, cfg in BOTS.items():
        results.append(check_bot(name, cfg))
    # ── Rebalance check (portfolio level) ───────────────────────────────
    try:
        from rebalancer import run_cycle
        states = _build_bot_states_for_rebalance()
        if len(states) >= len(BOTS):  # només si tots accessibles
            rebal = run_cycle(states)
            if rebal["plan"]["actions"]:
                log.warning(f"[rebalance] {len(rebal['plan']['actions'])} actions, shadow_mode={rebal['plan']['shadow_mode']}")
                for a in rebal["plan"]["actions"]:
                    sources = ", ".join(f"{s['from']}:{s['amount']:.2f}" for s in a["sources"])
                    log.warning(f"  → {a['target_bot']} +${a['amount_usdt']:.2f} (dev {a['deviation_pct']:+.1f}%) from [{sources}]")
                # Notificació TG nomes si executem real (no shadow)
                if not rebal["plan"]["shadow_mode"]:
                    try:
                        notify_rebalance(rebal["plan"]["actions"])
                    except Exception as e:
                        log.warning(f"notify rebalance failed: {e}")
            else:
                log.info("[rebalance] no actions needed")
    except Exception as e:
        log.error(f"[rebalance] error: {e}")
        try:
            notify_error("rebalancer", str(e))
        except Exception:
            pass
    log.info(f"=== Monitor cycle done ===")
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
    sys.exit(0)
