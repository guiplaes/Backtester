"""
rebalancer.py — Sistema de rebalanceig portfolio per al grid manager.

Lògica:
  - Cada bot té un TARGET_WEIGHT al portfolio
  - Si el pes actual desvia >= threshold del target → rebalancejar
  - Pure mecanic: NO mira P&L individual, només pesos sobre total_value_usdt
  - Priori­tat de fonts: (1) reserva, (2) reduce bot over, (3) parcial

Configuració al config.py:
  TARGET_WEIGHTS — pesos objectiu
  REBALANCE_THRESHOLDS — desviació per bot que activa el rebalanceig
  MIN_REBALANCE_USD — moviment mínim per executar (evita microcicles)
  REBALANCE_COOLDOWN_MIN — minim entre rebalanceigs del mateix bot
  REBALANCE_SHADOW_MODE — True = només log, False = executa real
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from config import (TARGET_WEIGHTS, REBALANCE_THRESHOLDS, MIN_REBALANCE_USD,
                    REBALANCE_COOLDOWN_MIN, REBALANCE_SHADOW_MODE)
from reserve_manager import get_state as reserve_get_state, use as reserve_use, replenish as reserve_replenish

# Importacions opcionals - tolerem error si l'API Pionex no esta configurada
try:
    from pionex_client import invest_in_bot, reduce_bot
    PIONEX_API_OK = True
except Exception:
    PIONEX_API_OK = False

_LAST_ACTIONS_FILE = Path(__file__).parent / "db" / "rebalance_last_actions.json"


def _load_last_actions() -> dict:
    if not _LAST_ACTIONS_FILE.exists():
        return {}
    try:
        return json.loads(_LAST_ACTIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_last_actions(d: dict) -> None:
    _LAST_ACTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LAST_ACTIONS_FILE.write_text(json.dumps(d, indent=2), encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _minutes_since(iso_ts: str) -> float:
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() / 60
    except Exception:
        return 9999.0


def compute_weights(bot_states: dict) -> dict:
    """Calcula pes actual de cada bot sobre el total invertit."""
    total = sum(s.get("total_value_usdt", 0) for s in bot_states.values())
    if total <= 0:
        return {}
    return {name: s.get("total_value_usdt", 0) / total for name, s in bot_states.items()}


def evaluate(bot_states: dict) -> dict:
    """
    Analitza el portfolio i retorna les accions necessàries.
    NO executa res — només propostes amb justificació.

    Returns dict amb:
      total_value_usdt: total invertit
      weights: pes actual per bot
      deviations: desviació vs target per bot
      actions: llista d'accions proposades
      reserve_state: estat de la reserva
    """
    weights = compute_weights(bot_states)
    total = sum(s.get("total_value_usdt", 0) for s in bot_states.values())
    last_actions = _load_last_actions()

    deviations = {}
    for name, weight in weights.items():
        target = TARGET_WEIGHTS.get(name, 0)
        deviations[name] = {
            "current_weight": weight,
            "target_weight": target,
            "deviation": weight - target,
            "deviation_usd": (weight - target) * total,
        }

    # Identifica bots fora del threshold
    over = []  # bots que necessiten reduce
    under = []  # bots que necessiten invest_in
    for name, dev in deviations.items():
        threshold = REBALANCE_THRESHOLDS.get(name, 0.05)
        if dev["deviation"] >= threshold:
            over.append((name, dev["deviation_usd"]))
        elif dev["deviation"] <= -threshold:
            under.append((name, -dev["deviation_usd"]))  # positiu = quant en falta

    # Ordena per magnitud descendent
    over.sort(key=lambda x: x[1], reverse=True)
    under.sort(key=lambda x: x[1], reverse=True)

    actions = []
    reserve = reserve_get_state()
    # Cap real de la reserva = min(available_budget, USDT real al wallet)
    real_wallet_usdt = 0.0
    try:
        from pionex_client import get_balance
        bal = get_balance() or {}
        # get_balance retorna dict pla: {USDT: 311.09, BTC: 0.003, ...}
        real_wallet_usdt = float(bal.get("USDT", 0))
    except Exception:
        pass
    reserve_avail = min(reserve["available_usdt"], real_wallet_usdt)
    reserve["real_wallet_usdt"] = real_wallet_usdt

    for under_name, under_amount in under:
        # Skip si moviment menor a min
        if under_amount < MIN_REBALANCE_USD:
            continue
        # Skip si en cooldown
        last_ts = last_actions.get(under_name, {}).get("ts")
        if last_ts and _minutes_since(last_ts) < REBALANCE_COOLDOWN_MIN:
            continue

        remaining = under_amount
        action_sources = []

        # Priori­tat 1: reserva
        if reserve_avail > 0:
            use_from_reserve = min(reserve_avail, remaining)
            if use_from_reserve >= MIN_REBALANCE_USD / 2:
                action_sources.append({"from": "RESERVE", "amount": use_from_reserve})
                remaining -= use_from_reserve
                reserve_avail -= use_from_reserve

        # Priori­tat 2: reduce bots over
        for over_name, over_amount in over:
            if remaining < MIN_REBALANCE_USD / 2:
                break
            use_from_over = min(over_amount, remaining)
            if use_from_over >= MIN_REBALANCE_USD / 2:
                action_sources.append({"from": over_name, "amount": use_from_over})
                remaining -= use_from_over
                over_amount -= use_from_over
                # Update la llista
                idx = next(i for i, o in enumerate(over) if o[0] == over_name)
                over[idx] = (over_name, over_amount)

        if action_sources:
            total_amount = under_amount - remaining
            actions.append({
                "type": "REBALANCE",
                "target_bot": under_name,
                "amount_usdt": total_amount,
                "sources": action_sources,
                "partial": remaining > 0,
                "deviation_pct": deviations[under_name]["deviation"] * 100,
            })

    return {
        "ts": _now_iso(),
        "total_value_usdt": total,
        "weights": weights,
        "deviations": deviations,
        "actions": actions,
        "reserve": reserve,
        "over_bots": over,
        "under_bots": under,
        "shadow_mode": REBALANCE_SHADOW_MODE,
    }


def execute(plan: dict, dry_run: bool = None) -> dict:
    """
    Executa les accions del plan.
    Si dry_run=True o REBALANCE_SHADOW_MODE=True: només log, no executa.
    """
    if dry_run is None:
        dry_run = REBALANCE_SHADOW_MODE

    results = []
    last_actions = _load_last_actions()

    for action in plan["actions"]:
        target_bot = action["target_bot"]
        total_amount = action["amount_usdt"]
        from_reserve = sum(s["amount"] for s in action["sources"] if s["from"] == "RESERVE")
        from_bots = [(s["from"], s["amount"]) for s in action["sources"] if s["from"] != "RESERVE"]

        exec_result = {
            "action": action,
            "executed": not dry_run,
            "results": [],
        }

        if dry_run:
            exec_result["note"] = "SHADOW MODE — no s'ha executat"
            results.append(exec_result)
            continue

        if not PIONEX_API_OK:
            exec_result["error"] = "Pionex API no disponible"
            results.append(exec_result)
            continue

        try:
            from investment_tracker import add_investment, remove_investment
            from pionex_client import get_bot_order

            def _call_with_verify(call_fn, bot_name, amount, expected_delta_sign):
                """Crida reduce_bot o invest_in_bot amb verificació EMPÍRICA SEMPRE.
                No confiem en `result: true` de Pionex — comprovem qti_before/qti_after.
                expected_delta_sign: +1 per invest, -1 per reduce.
                Retorna (ok: bool, message: str, raw_response).
                """
                from config import BOTS
                import time as _t
                bot_id = BOTS[bot_name]["id"]

                # Estat ABANS (obligatori per a verificació)
                try:
                    before = get_bot_order(bot_id)
                    qti_before = float(before.get("buOrderData", {}).get("quoteTotalInvestment", 0))
                except Exception as e_pre:
                    return False, f"no he pogut llegir estat ABANS: {str(e_pre)[:200]}", None

                expected_delta = amount * expected_delta_sign

                # Crida la mutació
                resp = None
                call_exception = None
                try:
                    resp = call_fn(bot_name, amount)
                except Exception as e:
                    call_exception = e
                    err_str = str(e)
                    transient = any(m in err_str for m in
                                    ("SSL", "EOF", "Connection", "Timeout",
                                     "RemoteDisconnected", "ProtocolError",
                                     "Max retries"))
                    if not transient:
                        return False, f"error no-transient: {err_str[:300]}", None

                # Si arribem aquí: o resp existeix, o exception transient. Verifiquem empíricament.
                _t.sleep(5)
                try:
                    after = get_bot_order(bot_id)
                    qti_after = float(after.get("buOrderData", {}).get("quoteTotalInvestment", 0))
                except Exception as e_post:
                    return False, f"no he pogut llegir estat DESPRÉS: {str(e_post)[:200]}", resp

                delta = qti_after - qti_before
                tolerance = max(0.01 * abs(expected_delta), 0.05)  # 1% o $0.05 mínim
                if abs(delta - expected_delta) <= tolerance:
                    # Mutació aplicada de veritat
                    note = "ok" if resp else f"applied despite error ({type(call_exception).__name__})"
                    return True, f"{note} (qti {qti_before:.2f}->{qti_after:.2f}, delta={delta:+.2f})", resp or {"result": True}

                # Mutació NO aplicada
                if resp is not None:
                    return False, f"Pionex va respondre {resp.get('result')} però qti NO va canviar (delta={delta:+.2f} vs esperat {expected_delta:+.2f})", resp
                return False, f"error transient i mutació NO aplicada (delta={delta:+.2f} vs esperat {expected_delta:+.2f}): {str(call_exception)[:150]}", None

            # Helper per log a Neon (best-effort, no bloca si falla)
            def _cloud_log(bot_name_arg, event_type, amount, qti_b, qti_a, resp, ok_flag, msg_str, idem):
                try:
                    from cloud.db_cloud import log_capital_event
                    from config import BOTS as _BOTS
                    log_capital_event(
                        bot_id=_BOTS[bot_name_arg]["id"], bot_name=bot_name_arg,
                        event_type=event_type, amount_usdt=amount,
                        qti_before=qti_b, qti_after=qti_a,
                        source="rebalancer",
                        idempotency_key=idem,
                        success=ok_flag, error_msg=None if ok_flag else msg_str[:300],
                        raw_response=resp if isinstance(resp, dict) else None,
                        notes=msg_str[:500],
                        created_by="rebalancer.execute",
                    )
                except Exception as _ce:
                    pass  # no bloca rebalance per fallada de cloud log

            import time as _tloc
            _idem_base = _tloc.strftime("%Y%m%dT%H%M%S")

            # 1) Reduce bots over (alliberen USDT al wallet) — verificat
            reduce_failed = False
            for over_name, amount in from_bots:
                ok, msg, r = _call_with_verify(reduce_bot, over_name, amount, expected_delta_sign=-1)
                exec_result["results"].append({"step": "reduce", "bot": over_name, "amount": amount, "ok": ok, "msg": msg, "result": r})
                # Cloud log (sempre, èxit o fallada, per traceabilitat)
                _cloud_log(over_name, "rebalance_out", amount, None, None, r, ok, msg,
                           f"rebal_out_{over_name}_{_idem_base}")
                if ok:
                    remove_investment(over_name, amount, dest=target_bot)
                else:
                    reduce_failed = True
                    break

            if reduce_failed:
                exec_result["error"] = "reduce_bot va fallar — abortant invest_in per seguretat"
                results.append(exec_result)
                continue

            # 2) Invest_in al bot target — verificat
            ok, msg, r = _call_with_verify(invest_in_bot, target_bot, total_amount, expected_delta_sign=+1)
            exec_result["results"].append({"step": "invest_in", "bot": target_bot, "amount": total_amount, "ok": ok, "msg": msg, "result": r})
            # Cloud log
            _cloud_log(target_bot, "rebalance_in", total_amount, None, None, r, ok, msg,
                       f"rebal_in_{target_bot}_{_idem_base}")
            if not ok:
                exec_result["error"] = f"invest_in va fallar: {msg}"
                results.append(exec_result)
                continue
            add_investment(target_bot, total_amount,
                          source=f"REBALANCE: reserve={from_reserve}, from_bots={[(b, a) for b, a in from_bots]}")

            # 3) Actualitza reserva (only la part que ha sortit de reserva) — només si tot OK
            if from_reserve > 0:
                reserve_use(from_reserve)

            # 4) Actualitza last_actions
            last_actions[target_bot] = {"ts": _now_iso(), "amount": total_amount}
            for over_name, _ in from_bots:
                last_actions[over_name] = {"ts": _now_iso(), "amount": -amount}

        except Exception as e:
            exec_result["error"] = str(e)

        results.append(exec_result)

    if not dry_run:
        _save_last_actions(last_actions)

    return {"ts": _now_iso(), "results": results, "dry_run": dry_run}


def run_cycle(bot_states: dict) -> dict:
    """Runs evaluate + execute in one call. Returns full report."""
    plan = evaluate(bot_states)
    exec_result = execute(plan)
    return {"plan": plan, "execution": exec_result}


if __name__ == "__main__":
    # Smoke test
    print("rebalancer.py loaded ok")
    print("Reserve state:")
    print(json.dumps(reserve_get_state(), indent=2))
