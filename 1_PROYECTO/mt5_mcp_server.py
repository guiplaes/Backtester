"""
MCP Server per MT5 - exposa funcions de control de MetaTrader 5 a Claude Code.

Funcions disponibles:
  Read-only:
    - account_info()
    - positions_list(magic, symbol)
    - orders_list(magic, symbol)
    - symbol_tick(symbol)
    - symbol_info(symbol)
    - history_deals(from_date_str, to_date_str)

  Write (operacions de trading - usar amb cura):
    - position_close(ticket)
    - position_close_by_magic(magic, symbol, side)
    - order_cancel(ticket)
    - order_cancel_all_by_magic(magic, symbol)
    - position_modify(ticket, sl, tp)

Per arrencar manualment: python mt5_mcp_server.py
Per registrar a Claude Code: afegir al config MCP (vegis README)
"""
import sys
from datetime import datetime, timedelta
from typing import Optional

import MetaTrader5 as mt5
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("mt5-control")


def _ensure_connected():
    """Inicialitza MT5 si no esta connectat ja."""
    if not mt5.initialize():
        raise RuntimeError(f"mt5.initialize() failed: {mt5.last_error()}")


# === READ-ONLY ===

@mcp.tool()
def account_info() -> dict:
    """Retorna info del compte: balance, equity, margin, login, broker, server, currency."""
    _ensure_connected()
    info = mt5.account_info()
    if info is None:
        return {"error": str(mt5.last_error())}
    return {
        "login": info.login,
        "server": info.server,
        "broker": info.company,
        "currency": info.currency,
        "balance": info.balance,
        "equity": info.equity,
        "margin": info.margin,
        "free_margin": info.margin_free,
        "margin_level_pct": info.margin_level,
        "leverage": info.leverage,
        "trade_mode": info.trade_mode,
        "trade_allowed": info.trade_allowed,
        "trade_expert": info.trade_expert,
    }


@mcp.tool()
def positions_list(magic: Optional[int] = None, symbol: Optional[str] = None) -> list:
    """Llista posicions obertes. Filtra opcionalment per magic i/o symbol."""
    _ensure_connected()
    if symbol:
        positions = mt5.positions_get(symbol=symbol)
    else:
        positions = mt5.positions_get()
    if positions is None:
        return []
    result = []
    for p in positions:
        if magic is not None and p.magic != magic:
            continue
        result.append({
            "ticket": p.ticket,
            "symbol": p.symbol,
            "magic": p.magic,
            "type": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
            "volume": p.volume,
            "price_open": p.price_open,
            "price_current": p.price_current,
            "sl": p.sl,
            "tp": p.tp,
            "profit": p.profit,
            "swap": p.swap,
            "commission": p.commission if hasattr(p, 'commission') else 0,
            "comment": p.comment,
            "time": datetime.fromtimestamp(p.time).isoformat(),
        })
    return result


@mcp.tool()
def positions_summary(magic: Optional[int] = None, symbol: Optional[str] = None) -> dict:
    """Resum agregat de posicions: count, sum_lot, sum_profit per direccio."""
    _ensure_connected()
    if symbol:
        positions = mt5.positions_get(symbol=symbol)
    else:
        positions = mt5.positions_get()
    if positions is None:
        return {"long": {"count": 0, "lot": 0, "profit": 0},
                "short": {"count": 0, "lot": 0, "profit": 0}}
    long_c, long_l, long_p = 0, 0.0, 0.0
    short_c, short_l, short_p = 0, 0.0, 0.0
    for p in positions:
        if magic is not None and p.magic != magic:
            continue
        if p.type == mt5.POSITION_TYPE_BUY:
            long_c += 1
            long_l += p.volume
            long_p += p.profit + p.swap + (p.commission if hasattr(p, 'commission') else 0)
        else:
            short_c += 1
            short_l += p.volume
            short_p += p.profit + p.swap + (p.commission if hasattr(p, 'commission') else 0)
    return {
        "long":  {"count": long_c,  "lot": round(long_l, 4),  "profit": round(long_p, 2)},
        "short": {"count": short_c, "lot": round(short_l, 4), "profit": round(short_p, 2)},
        "total": {"count": long_c + short_c, "lot": round(long_l + short_l, 4),
                  "profit": round(long_p + short_p, 2)},
    }


@mcp.tool()
def orders_list(magic: Optional[int] = None, symbol: Optional[str] = None) -> list:
    """Llista ordres pendents. Filtra opcionalment per magic i/o symbol."""
    _ensure_connected()
    if symbol:
        orders = mt5.orders_get(symbol=symbol)
    else:
        orders = mt5.orders_get()
    if orders is None:
        return []
    type_names = {
        mt5.ORDER_TYPE_BUY: "BUY_MARKET",
        mt5.ORDER_TYPE_SELL: "SELL_MARKET",
        mt5.ORDER_TYPE_BUY_LIMIT: "BUY_LIMIT",
        mt5.ORDER_TYPE_SELL_LIMIT: "SELL_LIMIT",
        mt5.ORDER_TYPE_BUY_STOP: "BUY_STOP",
        mt5.ORDER_TYPE_SELL_STOP: "SELL_STOP",
    }
    result = []
    for o in orders:
        if magic is not None and o.magic != magic:
            continue
        result.append({
            "ticket": o.ticket,
            "symbol": o.symbol,
            "magic": o.magic,
            "type": type_names.get(o.type, str(o.type)),
            "volume": o.volume_initial,
            "price": o.price_open,
            "sl": o.sl,
            "tp": o.tp,
            "comment": o.comment,
        })
    return result


@mcp.tool()
def symbol_tick(symbol: str) -> dict:
    """Preu actual (bid/ask) del simbol."""
    _ensure_connected()
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return {"error": str(mt5.last_error())}
    return {
        "symbol": symbol,
        "bid": tick.bid,
        "ask": tick.ask,
        "spread_points": (tick.ask - tick.bid) / mt5.symbol_info(symbol).point,
        "last": tick.last,
        "time": datetime.fromtimestamp(tick.time).isoformat(),
    }


@mcp.tool()
def symbol_info(symbol: str) -> dict:
    """Info del simbol: digits, point, volume min/max/step, trade allowed, etc."""
    _ensure_connected()
    info = mt5.symbol_info(symbol)
    if info is None:
        return {"error": str(mt5.last_error())}
    return {
        "symbol": info.name,
        "digits": info.digits,
        "point": info.point,
        "spread_current": info.spread,
        "volume_min": info.volume_min,
        "volume_max": info.volume_max,
        "volume_step": info.volume_step,
        "contract_size": info.trade_contract_size,
        "margin_initial": info.margin_initial,
        "trade_mode": info.trade_mode,
    }


# === WRITE (trading operations - amb cura) ===

@mcp.tool()
def position_close(ticket: int) -> dict:
    """Tanca una posicio per ticket. Retorna resultat."""
    _ensure_connected()
    pos_list = mt5.positions_get(ticket=ticket)
    if not pos_list:
        return {"error": "position not found", "ticket": ticket}
    pos = pos_list[0]
    order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
    tick = mt5.symbol_info_tick(pos.symbol)
    price = tick.bid if pos.type == mt5.POSITION_TYPE_BUY else tick.ask
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "position": ticket,
        "symbol": pos.symbol,
        "volume": pos.volume,
        "type": order_type,
        "price": price,
        "deviation": 50,
        "magic": pos.magic,
        "comment": "mcp_close",
        "type_filling": mt5.ORDER_FILLING_FOK,
    }
    result = mt5.order_send(request)
    if result is None:
        return {"error": str(mt5.last_error()), "ticket": ticket}
    return {
        "ticket": ticket,
        "retcode": result.retcode,
        "comment": result.comment,
        "ok": result.retcode == mt5.TRADE_RETCODE_DONE,
    }


@mcp.tool()
def position_close_by_magic(magic: int, symbol: Optional[str] = None,
                             side: str = "all") -> dict:
    """Tanca totes les posicions per magic. side: 'long', 'short', o 'all'."""
    _ensure_connected()
    if symbol:
        positions = mt5.positions_get(symbol=symbol)
    else:
        positions = mt5.positions_get()
    if positions is None:
        return {"closed": 0, "failed": 0, "error": "no positions"}
    closed, failed = 0, 0
    results = []
    for p in positions:
        if p.magic != magic:
            continue
        if side == "long" and p.type != mt5.POSITION_TYPE_BUY:
            continue
        if side == "short" and p.type != mt5.POSITION_TYPE_SELL:
            continue
        r = position_close(p.ticket)
        if r.get("ok"):
            closed += 1
        else:
            failed += 1
        results.append(r)
    return {"closed": closed, "failed": failed, "magic": magic, "side": side}


@mcp.tool()
def order_cancel(ticket: int) -> dict:
    """Cancel.la una ordre pendent per ticket."""
    _ensure_connected()
    request = {
        "action": mt5.TRADE_ACTION_REMOVE,
        "order": ticket,
    }
    result = mt5.order_send(request)
    if result is None:
        return {"error": str(mt5.last_error()), "ticket": ticket}
    return {
        "ticket": ticket,
        "retcode": result.retcode,
        "ok": result.retcode == mt5.TRADE_RETCODE_DONE,
    }


@mcp.tool()
def order_cancel_all_by_magic(magic: int, symbol: Optional[str] = None) -> dict:
    """Cancel.la totes les ordres pendents per magic."""
    _ensure_connected()
    if symbol:
        orders = mt5.orders_get(symbol=symbol)
    else:
        orders = mt5.orders_get()
    if orders is None:
        return {"cancelled": 0, "error": "no orders"}
    cancelled, failed = 0, 0
    for o in orders:
        if o.magic != magic:
            continue
        r = order_cancel(o.ticket)
        if r.get("ok"):
            cancelled += 1
        else:
            failed += 1
    return {"cancelled": cancelled, "failed": failed, "magic": magic}


@mcp.tool()
def position_modify(ticket: int, sl: float = 0, tp: float = 0) -> dict:
    """Modifica SL/TP d'una posicio oberta. 0 = treure SL/TP."""
    _ensure_connected()
    pos_list = mt5.positions_get(ticket=ticket)
    if not pos_list:
        return {"error": "position not found"}
    pos = pos_list[0]
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "symbol": pos.symbol,
        "sl": sl,
        "tp": tp,
    }
    result = mt5.order_send(request)
    if result is None:
        return {"error": str(mt5.last_error()), "ticket": ticket}
    return {
        "ticket": ticket,
        "retcode": result.retcode,
        "ok": result.retcode == mt5.TRADE_RETCODE_DONE,
    }


@mcp.tool()
def history_deals_today(magic: Optional[int] = None,
                         symbol: Optional[str] = None) -> list:
    """Llista deals tancats avui. Filtra per magic i/o symbol."""
    _ensure_connected()
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    deals = mt5.history_deals_get(today_start, datetime.now())
    if deals is None:
        return []
    result = []
    for d in deals:
        if magic is not None and d.magic != magic:
            continue
        if symbol is not None and d.symbol != symbol:
            continue
        result.append({
            "ticket": d.ticket,
            "order": d.order,
            "symbol": d.symbol,
            "magic": d.magic,
            "type": "BUY" if d.type == 0 else "SELL",
            "volume": d.volume,
            "price": d.price,
            "profit": d.profit,
            "swap": d.swap,
            "commission": d.commission,
            "comment": d.comment,
            "time": datetime.fromtimestamp(d.time).isoformat(),
        })
    return result


if __name__ == "__main__":
    # Stdio transport per MCP
    mcp.run()
