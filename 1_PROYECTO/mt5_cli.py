"""
MT5 CLI - eina per llegir/controlar MT5 via Python.

Us:
    python mt5_cli.py account
    python mt5_cli.py positions [--magic 88888] [--symbol XAUUSD]
    python mt5_cli.py summary [--magic 88888]
    python mt5_cli.py orders [--magic 88888]
    python mt5_cli.py tick XAUUSD
    python mt5_cli.py deals_today [--magic 88888]

Write ops (requereixen --confirm-login XXXX per seguretat):
    python mt5_cli.py close_position TICKET --confirm-login 1234567
    python mt5_cli.py close_all_by_magic 88888 --side long|short|all --confirm-login 1234567
    python mt5_cli.py cancel_order TICKET --confirm-login 1234567
    python mt5_cli.py cancel_all_orders 88888 --confirm-login 1234567

Sempre print account info abans de qualsevol operacio.
"""
import argparse
import json
import sys
from datetime import datetime

import MetaTrader5 as mt5


def connect():
    if not mt5.initialize():
        print(json.dumps({"error": f"mt5.initialize() failed: {mt5.last_error()}"}))
        sys.exit(1)


def get_account_brief():
    info = mt5.account_info()
    return {"login": info.login, "server": info.server, "balance": info.balance,
            "equity": info.equity, "broker": info.company}


def cmd_account(args):
    info = mt5.account_info()
    out = {
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
        "trade_allowed": info.trade_allowed,
    }
    print(json.dumps(out, indent=2))


def cmd_positions(args):
    if args.symbol:
        positions = mt5.positions_get(symbol=args.symbol)
    else:
        positions = mt5.positions_get()
    result = []
    if positions:
        for p in positions:
            if args.magic is not None and p.magic != args.magic:
                continue
            result.append({
                "ticket": p.ticket, "symbol": p.symbol, "magic": p.magic,
                "type": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
                "volume": p.volume, "price_open": p.price_open,
                "price_current": p.price_current, "sl": p.sl, "tp": p.tp,
                "profit": p.profit, "swap": p.swap,
                "comment": p.comment,
                "time": datetime.fromtimestamp(p.time).isoformat(),
            })
    print(json.dumps(result, indent=2))


def cmd_summary(args):
    if args.symbol:
        positions = mt5.positions_get(symbol=args.symbol)
    else:
        positions = mt5.positions_get()
    long_c, long_l, long_p = 0, 0.0, 0.0
    short_c, short_l, short_p = 0, 0.0, 0.0
    if positions:
        for p in positions:
            if args.magic is not None and p.magic != args.magic:
                continue
            pnl = p.profit + p.swap
            if p.type == mt5.POSITION_TYPE_BUY:
                long_c += 1; long_l += p.volume; long_p += pnl
            else:
                short_c += 1; short_l += p.volume; short_p += pnl
    print(json.dumps({
        "long":  {"count": long_c,  "lot": round(long_l, 4),  "profit": round(long_p, 2)},
        "short": {"count": short_c, "lot": round(short_l, 4), "profit": round(short_p, 2)},
        "total": {"count": long_c + short_c, "lot": round(long_l + short_l, 4),
                  "profit": round(long_p + short_p, 2)},
        "account": get_account_brief(),
    }, indent=2))


def cmd_orders(args):
    if args.symbol:
        orders = mt5.orders_get(symbol=args.symbol)
    else:
        orders = mt5.orders_get()
    type_names = {
        mt5.ORDER_TYPE_BUY_LIMIT: "BUY_LIMIT", mt5.ORDER_TYPE_SELL_LIMIT: "SELL_LIMIT",
        mt5.ORDER_TYPE_BUY_STOP: "BUY_STOP", mt5.ORDER_TYPE_SELL_STOP: "SELL_STOP",
    }
    result = []
    if orders:
        for o in orders:
            if args.magic is not None and o.magic != args.magic:
                continue
            result.append({
                "ticket": o.ticket, "symbol": o.symbol, "magic": o.magic,
                "type": type_names.get(o.type, str(o.type)),
                "volume": o.volume_initial, "price": o.price_open,
                "comment": o.comment,
            })
    print(json.dumps(result, indent=2))


def cmd_tick(args):
    tick = mt5.symbol_info_tick(args.symbol)
    info = mt5.symbol_info(args.symbol)
    if tick is None or info is None:
        print(json.dumps({"error": str(mt5.last_error())}))
        return
    print(json.dumps({
        "symbol": args.symbol,
        "bid": tick.bid, "ask": tick.ask,
        "spread_points": round((tick.ask - tick.bid) / info.point, 1),
        "time": datetime.fromtimestamp(tick.time).isoformat(),
    }, indent=2))


def cmd_deals_today(args):
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    deals = mt5.history_deals_get(today_start, datetime.now())
    result = []
    if deals:
        for d in deals:
            if args.magic is not None and d.magic != args.magic:
                continue
            result.append({
                "ticket": d.ticket, "symbol": d.symbol, "magic": d.magic,
                "type": "BUY" if d.type == 0 else "SELL",
                "volume": d.volume, "price": d.price,
                "profit": d.profit, "swap": d.swap,
                "time": datetime.fromtimestamp(d.time).isoformat(),
            })
    print(json.dumps(result, indent=2))


# === WRITE OPS (require --confirm-login) ===

def _verify_login_or_die(args, op_name):
    info = mt5.account_info()
    if args.confirm_login is None:
        print(json.dumps({"error": "write ops require --confirm-login XXXX with current account login",
                         "current_login": info.login, "current_server": info.server,
                         "op": op_name}))
        sys.exit(2)
    if int(args.confirm_login) != info.login:
        print(json.dumps({"error": "confirm-login mismatch — refusing write",
                         "expected": args.confirm_login, "actual": info.login,
                         "server": info.server, "op": op_name}))
        sys.exit(2)


def _close_position(ticket):
    pos_list = mt5.positions_get(ticket=ticket)
    if not pos_list:
        return {"error": "position not found", "ticket": ticket}
    pos = pos_list[0]
    order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
    tick = mt5.symbol_info_tick(pos.symbol)
    price = tick.bid if pos.type == mt5.POSITION_TYPE_BUY else tick.ask
    request = {
        "action": mt5.TRADE_ACTION_DEAL, "position": ticket, "symbol": pos.symbol,
        "volume": pos.volume, "type": order_type, "price": price,
        "deviation": 50, "magic": pos.magic, "comment": "cli_close",
        "type_filling": mt5.ORDER_FILLING_FOK,
    }
    result = mt5.order_send(request)
    if result is None:
        return {"error": str(mt5.last_error()), "ticket": ticket}
    return {"ticket": ticket, "retcode": result.retcode, "comment": result.comment,
            "ok": result.retcode == mt5.TRADE_RETCODE_DONE}


def cmd_close_position(args):
    _verify_login_or_die(args, "close_position")
    print(json.dumps(_close_position(args.ticket), indent=2))


def cmd_close_all_by_magic(args):
    _verify_login_or_die(args, "close_all_by_magic")
    positions = mt5.positions_get(symbol=args.symbol) if args.symbol else mt5.positions_get()
    closed, failed = 0, 0
    if positions:
        for p in positions:
            if p.magic != args.magic:
                continue
            if args.side == "long" and p.type != mt5.POSITION_TYPE_BUY:
                continue
            if args.side == "short" and p.type != mt5.POSITION_TYPE_SELL:
                continue
            r = _close_position(p.ticket)
            if r.get("ok"): closed += 1
            else: failed += 1
    print(json.dumps({"closed": closed, "failed": failed, "magic": args.magic,
                     "side": args.side}, indent=2))


def cmd_cancel_order(args):
    _verify_login_or_die(args, "cancel_order")
    request = {"action": mt5.TRADE_ACTION_REMOVE, "order": args.ticket}
    result = mt5.order_send(request)
    if result is None:
        print(json.dumps({"error": str(mt5.last_error())}))
        return
    print(json.dumps({"ticket": args.ticket, "retcode": result.retcode,
                     "ok": result.retcode == mt5.TRADE_RETCODE_DONE}, indent=2))


def cmd_cancel_all_orders(args):
    _verify_login_or_die(args, "cancel_all_orders")
    orders = mt5.orders_get(symbol=args.symbol) if args.symbol else mt5.orders_get()
    cancelled, failed = 0, 0
    if orders:
        for o in orders:
            if o.magic != args.magic:
                continue
            request = {"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket}
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                cancelled += 1
            else:
                failed += 1
    print(json.dumps({"cancelled": cancelled, "failed": failed, "magic": args.magic}, indent=2))


def main():
    parser = argparse.ArgumentParser(description="MT5 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("account")

    p = sub.add_parser("positions")
    p.add_argument("--magic", type=int, default=None)
    p.add_argument("--symbol", type=str, default=None)

    p = sub.add_parser("summary")
    p.add_argument("--magic", type=int, default=None)
    p.add_argument("--symbol", type=str, default=None)

    p = sub.add_parser("orders")
    p.add_argument("--magic", type=int, default=None)
    p.add_argument("--symbol", type=str, default=None)

    p = sub.add_parser("tick")
    p.add_argument("symbol")

    p = sub.add_parser("deals_today")
    p.add_argument("--magic", type=int, default=None)

    p = sub.add_parser("close_position")
    p.add_argument("ticket", type=int)
    p.add_argument("--confirm-login", type=int, required=True)

    p = sub.add_parser("close_all_by_magic")
    p.add_argument("magic", type=int)
    p.add_argument("--side", choices=["long", "short", "all"], default="all")
    p.add_argument("--symbol", type=str, default=None)
    p.add_argument("--confirm-login", type=int, required=True)

    p = sub.add_parser("cancel_order")
    p.add_argument("ticket", type=int)
    p.add_argument("--confirm-login", type=int, required=True)

    p = sub.add_parser("cancel_all_orders")
    p.add_argument("magic", type=int)
    p.add_argument("--symbol", type=str, default=None)
    p.add_argument("--confirm-login", type=int, required=True)

    args = parser.parse_args()
    connect()

    cmds = {
        "account": cmd_account, "positions": cmd_positions, "summary": cmd_summary,
        "orders": cmd_orders, "tick": cmd_tick, "deals_today": cmd_deals_today,
        "close_position": cmd_close_position, "close_all_by_magic": cmd_close_all_by_magic,
        "cancel_order": cmd_cancel_order, "cancel_all_orders": cmd_cancel_all_orders,
    }
    cmds[args.cmd](args)
    mt5.shutdown()


if __name__ == "__main__":
    main()
