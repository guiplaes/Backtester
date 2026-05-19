"""
mt5_grid_client.py — llegeix l'estat del DualGridEA del MT5 des de Common Files.

L'EA escriu cada N segons un dualgrid_status.json amb tota la info del cicle,
posicions, ancores, etc. Aquest mòdul el llegeix i el normalitza al mateix
format que els bots de Pionex perquè el dashboard els pugui mostrar plegats.
"""
import json
from datetime import datetime, timezone
from pathlib import Path


# Common Files de l'instal·lació MT5 (compartit entre EAs)
MT5_COMMON_FILES = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files")
DUALGRID_STATUS_FILE = MT5_COMMON_FILES / "dualgrid_status.json"


def get_mt5_grid_state() -> dict:
    """Llegeix l'estat del MT5 DualGrid.

    Returns:
        dict amb format compatible amb els bots de Pionex (per al dashboard).
        Si el fitxer no existeix o és antic (>5 min), retorna estat 'offline'.
    """
    if not DUALGRID_STATUS_FILE.exists():
        return {
            "available": False,
            "error": f"Heartbeat file not found: {DUALGRID_STATUS_FILE.name}",
            "last_seen": None,
        }

    try:
        with open(DUALGRID_STATUS_FILE, "r", encoding="utf-8", errors="ignore") as f:
            raw = json.load(f)
    except Exception as e:
        return {"available": False, "error": f"Parse error: {e}", "last_seen": None}

    # Edat del heartbeat — usem el LastWriteTime DEL FITXER (sempre en local time)
    # i NO el ts del JSON (que ve del broker en zona horaria UTC+3 o similar).
    # Aixi evitem edats negatives per offset UTC.
    try:
        file_mtime = DUALGRID_STATUS_FILE.stat().st_mtime
        age_sec = (datetime.now().timestamp() - file_mtime)
    except Exception:
        age_sec = None

    is_stale = age_sec is not None and age_sec > 300  # >5 min = stale

    # Normalitza al format de bot del dashboard
    state = {
        "available": not is_stale,
        "stale": is_stale,
        "last_seen_age_sec": age_sec,
        "raw": raw,
        # Camps comuns
        "bot_name": "XAUUSD_MT5",
        "symbol": raw.get("symbol", "XAUUSD"),
        "quote": "USDT",  # MT5 USD ≈ USDT per al dashboard
        "status": "running" if not is_stale else "stale",
        "price": float(raw.get("current_price", 0)),
        # Range/anchor
        "top": float(raw.get("grid_anchor_price", 0)) + float(raw.get("spacing", 0)) * int(raw.get("levels_each_side", 0)),
        "bottom": float(raw.get("grid_anchor_price", 0)) - float(raw.get("spacing", 0)) * int(raw.get("levels_each_side", 0)),
        "grid_rows": int(raw.get("levels_each_side", 0)) * 2,
        # P&L (com a "grid_profit" per analogia amb Pionex)
        "grid_profit": float(raw.get("realized_cycle", 0)),
        "realized_profit": float(raw.get("realized_cycle", 0)),
        "floating": float(raw.get("floating", 0)),
        "net_cycle": float(raw.get("net_cycle", 0)),
        "target_usd": float(raw.get("target_usd", 0)),
        "target_progress_pct": float(raw.get("target_progress", 0)),
        # Inventory equivalents (no aplica directament a CFD; reportem valors notional)
        "quote_in_bot": float(raw.get("balance", 0)),
        "base_in_bot": 0.0,
        "usdt_investment": float(raw.get("cycle_start_balance", 0)),
        # Comptadors
        "filled_orders": int(raw.get("positions_total", 0)),
        "placed_orders": int(raw.get("pending_total", 0)),
        "paired_cycles": 0,  # no aplica al MT5 dual grid (no hi ha pair de buy+sell)
        "buys_count": int(raw.get("buys_count", 0)),
        "sells_count": int(raw.get("sells_count", 0)),
        # Anchors
        "anchor_buy_entry": float(raw.get("anchor_buy_entry", 0)),
        "anchor_buy_floating": float(raw.get("anchor_buy_floating", 0)),
        "anchor_sell_entry": float(raw.get("anchor_sell_entry", 0)),
        "anchor_sell_floating": float(raw.get("anchor_sell_floating", 0)),
        # Risc
        "global_sl_pct": float(raw.get("global_sl_pct", 0)),
        "floating_pct_eq": float(raw.get("floating_pct_eq", 0)),
        "is_circuit_breaker": bool(raw.get("is_circuit_breaker", False)),
        # Distances to edges (per consistència amb format Pionex)
        "dist_to_top": 0.0,
        "dist_to_bottom": 0.0,
        "dist_to_top_pct": 0.5,  # neutral default
        "dist_to_bottom_pct": 0.5,
        "avg_cost": 0.0,
    }

    # Si tenim el rang, calculem les distàncies reals
    if state["top"] > state["bottom"] > 0 and state["price"] > 0:
        width = state["top"] - state["bottom"]
        state["dist_to_top"] = state["top"] - state["price"]
        state["dist_to_bottom"] = state["price"] - state["bottom"]
        state["dist_to_top_pct"] = state["dist_to_top"] / width
        state["dist_to_bottom_pct"] = state["dist_to_bottom"] / width

    return state


if __name__ == "__main__":
    # Smoke test
    s = get_mt5_grid_state()
    print(json.dumps(s, indent=2, default=str))
