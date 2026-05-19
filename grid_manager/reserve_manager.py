"""
reserve_manager.py — Gestió de la reserva USDT del sistema.

L'usuari configura un BUDGET (capital que el sistema pot usar lliurement).
El sistema track 'used' i automàticament replenisseix quan retornen diners
des de reduces de bots.

Fitxer persistent: db/reserve_state.json
"""
import json
from datetime import datetime, timezone
from pathlib import Path

_FILE = Path(__file__).parent / "db" / "reserve_state.json"
_DEFAULT_BUDGET = 700.0

# Capital inicial total del sistema (USDT real dipositats per l'usuari)
_CAPITAL_FILE = Path(__file__).parent / "db" / "system_capital.json"


def get_capital() -> dict:
    """Retorna el capital total dipositat al sistema."""
    if not _CAPITAL_FILE.exists():
        return {"initial_capital_usdt": 0.0, "last_updated": None}
    try:
        return json.loads(_CAPITAL_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"initial_capital_usdt": 0.0, "last_updated": None}


def set_capital(amount: float) -> dict:
    """Configura el capital inicial total."""
    d = {"initial_capital_usdt": float(amount),
         "last_updated": datetime.now(timezone.utc).isoformat()}
    _CAPITAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CAPITAL_FILE.write_text(json.dumps(d, indent=2), encoding="utf-8")
    return d


def _load() -> dict:
    if not _FILE.exists():
        return {"budget_usdt": _DEFAULT_BUDGET, "used_usdt": 0.0,
                "last_updated": datetime.now(timezone.utc).isoformat()}
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"budget_usdt": _DEFAULT_BUDGET, "used_usdt": 0.0,
                "last_updated": datetime.now(timezone.utc).isoformat()}


def _save(d: dict) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    d["last_updated"] = datetime.now(timezone.utc).isoformat()
    _FILE.write_text(json.dumps(d, indent=2), encoding="utf-8")


def get_state() -> dict:
    """Retorna estat actual amb 'available' calculat."""
    s = _load()
    s["available_usdt"] = max(0.0, s.get("budget_usdt", 0) - s.get("used_usdt", 0))
    return s


def set_budget(new_budget: float) -> dict:
    """Actualitza el budget. No toca 'used' (l'usuari pot baixar budget
    sense que el sistema deixi de tenir control del que ja ha consumit)."""
    s = _load()
    s["budget_usdt"] = float(new_budget)
    _save(s)
    return get_state()


def use(amount: float) -> tuple[float, dict]:
    """Marca 'amount' com a usat. Retorna (amount_efectivament_usat, state).
    Si amount > available, només es marca el disponible."""
    s = _load()
    avail = max(0.0, s.get("budget_usdt", 0) - s.get("used_usdt", 0))
    used = min(amount, avail)
    s["used_usdt"] = s.get("used_usdt", 0) + used
    _save(s)
    return used, get_state()


def replenish(amount: float) -> dict:
    """Diners que tornen al sistema (per reduce de bot). Decrementa 'used'."""
    s = _load()
    s["used_usdt"] = max(0.0, s.get("used_usdt", 0) - amount)
    _save(s)
    return get_state()


if __name__ == "__main__":
    print(json.dumps(get_state(), indent=2))
