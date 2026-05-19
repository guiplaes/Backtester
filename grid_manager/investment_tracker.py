"""
investment_tracker.py — Tracking de la inversió cumulativa per bot.

Pionex no actualitza el camp usdt_investment després d'`invest_in` o `reduce`,
així que nosaltres tracejem manualment.

Capital Invertit per bot = inversió_inicial + sum(invest_in) - sum(reduce)
"""
import json
from datetime import datetime, timezone
from pathlib import Path

_FILE = Path(__file__).parent / "db" / "bot_investments.json"


def _load() -> dict:
    if not _FILE.exists():
        return {}
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(d: dict) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(d, indent=2), encoding="utf-8")


def init_bot(bot_name: str, initial_amount: float) -> None:
    """Registra la inversió inicial d'un bot."""
    d = _load()
    if bot_name not in d:
        d[bot_name] = {
            "initial": initial_amount,
            "added": 0.0,
            "removed": 0.0,
            "last_action_ts": datetime.now(timezone.utc).isoformat(),
        }
        _save(d)


def add_investment(bot_name: str, amount: float, source: str = "") -> dict:
    """Registra una inversió addicional (via invest_in)."""
    d = _load()
    if bot_name not in d:
        d[bot_name] = {"initial": 0.0, "added": 0.0, "removed": 0.0, "last_action_ts": None}
    d[bot_name]["added"] = d[bot_name].get("added", 0.0) + amount
    d[bot_name]["last_action_ts"] = datetime.now(timezone.utc).isoformat()
    d[bot_name].setdefault("history", []).append({
        "ts": d[bot_name]["last_action_ts"],
        "type": "add",
        "amount": amount,
        "source": source,
    })
    _save(d)
    return d[bot_name]


def remove_investment(bot_name: str, amount: float, dest: str = "") -> dict:
    """Registra una reducció (via reduce)."""
    d = _load()
    if bot_name not in d:
        d[bot_name] = {"initial": 0.0, "added": 0.0, "removed": 0.0, "last_action_ts": None}
    d[bot_name]["removed"] = d[bot_name].get("removed", 0.0) + amount
    d[bot_name]["last_action_ts"] = datetime.now(timezone.utc).isoformat()
    d[bot_name].setdefault("history", []).append({
        "ts": d[bot_name]["last_action_ts"],
        "type": "remove",
        "amount": amount,
        "dest": dest,
    })
    _save(d)
    return d[bot_name]


def get_total_invested(bot_name: str, fallback_initial: float = 0.0) -> float:
    """Retorna la inversió cumulativa actual d'un bot."""
    d = _load()
    if bot_name not in d:
        return fallback_initial
    rec = d[bot_name]
    return rec.get("initial", 0.0) + rec.get("added", 0.0) - rec.get("removed", 0.0)


def get_all() -> dict:
    return _load()


def reset_to_pionex_values(bot_states: dict) -> None:
    """Inicialitza el tracker amb els valors actuals de Pionex.
    Útil per a primera vegada o reset."""
    d = _load()
    for name, state in bot_states.items():
        if name not in d:
            initial = float(state.get("usdt_investment", 0))
            d[name] = {
                "initial": initial,
                "added": 0.0,
                "removed": 0.0,
                "last_action_ts": datetime.now(timezone.utc).isoformat(),
            }
    _save(d)
