"""
Pionex REST API client (read-only).
Used by monitor.py to poll bot status & price without invoking Claude.
Mutations (cancel/create/adjust) go via Claude + MCP, not from here.
"""
import hashlib
import hmac
import json
import time
import urllib.parse
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import tomllib  # Python 3.11+, falls back to tomli below

from config import PIONEX_CONFIG, SYMBOL


PIONEX_API = "https://api.pionex.com"
HTTP_TIMEOUT = 15  # seconds


def _make_session() -> requests.Session:
    """Session amb retries automàtics per GETs.
    POSTs NO s'auto-retryen (no són idempotents — la verificació es fa al caller).
    Cobreix: SSLEOFError, ConnectionError, 502/503/504/429.
    """
    s = requests.Session()
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=1.5,   # waits: 0, 1.5, 3, 6, 12s
        status_forcelist=(429, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD", "OPTIONS"}),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


_SESSION = _make_session()


def load_credentials():
    """Read API key/secret from ~/.pionex/config.toml.
    Supports two layouts:
      flat: api_key=... / api_secret=...
      profiles: default_profile="x" + [profiles.x] api_key=... secret_key=...
    """
    if not PIONEX_CONFIG.exists():
        raise FileNotFoundError(f"Pionex config not at {PIONEX_CONFIG}")
    with open(PIONEX_CONFIG, "rb") as f:
        cfg = tomllib.load(f)
    # Flat layout
    if cfg.get("api_key") and (cfg.get("api_secret") or cfg.get("secret_key")):
        return cfg["api_key"], cfg.get("api_secret") or cfg.get("secret_key")
    # Profile layout (pionex-ai-kit)
    profile = cfg.get("default_profile")
    profiles = cfg.get("profiles", {})
    if profile and profile in profiles:
        p = profiles[profile]
        return p.get("api_key"), p.get("api_secret") or p.get("secret_key")
    raise ValueError(f"Could not find api_key/secret in {PIONEX_CONFIG}")


def _sign(method: str, path: str, params: dict, body: str, secret: str, ts_ms: int) -> str:
    """Pionex signature: HMAC-SHA256 over canonical request string."""
    sorted_params = "&".join(f"{k}={params[k]}" for k in sorted(params))
    path_with_query = f"{path}?{sorted_params}" if sorted_params else path
    payload = f"{method}{path_with_query}{ts_ms}{body}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _get_signed(path: str, params: dict = None):
    """Authenticated GET to Pionex. Pionex requires `timestamp` in query string."""
    api_key, api_secret = load_credentials()
    params = dict(params or {})
    params["timestamp"] = int(time.time() * 1000)
    # Signature: METHOD + path?<sorted_params> (no separate ts arg)
    sorted_params = "&".join(f"{k}={params[k]}" for k in sorted(params))
    payload = f"GET{path}?{sorted_params}"
    sig = hmac.new(api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    headers = {
        "PIONEX-KEY": api_key,
        "PIONEX-SIGNATURE": sig,
    }
    url = f"{PIONEX_API}{path}?{sorted_params}"
    r = _SESSION.get(url, headers=headers, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _get_public(path: str, params: dict = None):
    """Public GET (no auth)."""
    params = params or {}
    url = f"{PIONEX_API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    r = _SESSION.get(url, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


# ─── Public endpoints ────────────────────────────────────────────────
def get_ticker(symbol: str = SYMBOL) -> dict:
    """Latest price + 24h stats."""
    data = _get_public("/api/v1/market/tickers", {"symbol": symbol})
    tickers = data.get("data", {}).get("tickers", [])
    return tickers[0] if tickers else {}


def get_klines(symbol: str, interval: str = "1D", limit: int = 30) -> list:
    """OHLCV klines."""
    data = _get_public("/api/v1/market/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    return data.get("data", {}).get("klines", [])


# ─── Authenticated endpoints ─────────────────────────────────────────
def get_balance() -> dict:
    """Spot balance for all coins."""
    data = _get_signed("/api/v1/account/balances")
    bals = data.get("data", {}).get("balances", [])
    return {b["coin"]: float(b["free"]) for b in bals}


def get_bot_order(bot_id: str) -> dict:
    """Get spot grid bot detail."""
    data = _get_signed("/api/v1/bot/orders/spotGrid/order", {"buOrderId": bot_id})
    return data.get("data", {})


def _post_signed(path: str, body: dict):
    """Authenticated POST to Pionex with HMAC signature."""
    api_key, api_secret = load_credentials()
    body_json = json.dumps(body, separators=(",", ":"))
    ts = int(time.time() * 1000)
    params = {"timestamp": ts}
    sorted_params = "&".join(f"{k}={params[k]}" for k in sorted(params))
    payload = f"POST{path}?{sorted_params}{body_json}"
    sig = hmac.new(api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    headers = {
        "PIONEX-KEY": api_key,
        "PIONEX-SIGNATURE": sig,
        "Content-Type": "application/json",
    }
    url = f"{PIONEX_API}{path}?{sorted_params}"
    # POSTs NO retryen automàticament (no idempotents).
    # Si falla amb SSL/connection error, el caller ha de verificar amb un GET
    # si la mutació es va aplicar o no abans de re-intentar.
    r = _SESSION.post(url, headers=headers, data=body_json, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def adjust_grid_range(bot_id: str, top: float, bottom: float, row: int = 12) -> dict:
    """Adjust the range of an existing spot grid bot in-place (no cancel/create)."""
    # Pionex precision: use sensible decimal places
    if top < 1:
        top_s = f"{top:.6f}"
        bot_s = f"{bottom:.6f}"
    elif top < 100:
        top_s = f"{top:.4f}"
        bot_s = f"{bottom:.4f}"
    else:
        top_s = f"{top:.2f}"
        bot_s = f"{bottom:.2f}"
    body = {"buOrderId": bot_id, "top": top_s, "bottom": bot_s, "row": row}
    return _post_signed("/api/v1/bot/orders/spotGrid/adjustParams", body)


def invest_in_bot(bot_id_or_name, quote_amount: float) -> dict:
    """Inverteix USDT addicional a un bot existent (sense cancel + create).
    `bot_id_or_name`: el nom del bot (PAXG_USDT, etc.) → resol via BOTS, o l'id directe."""
    bot_id = _resolve_bot_id(bot_id_or_name)
    body = {"buOrderId": bot_id, "quoteInvest": f"{quote_amount:.4f}"}
    return _post_signed("/api/v1/bot/orders/spotGrid/investIn", body)


def extract_grid_profit(bot_id_or_name, amount: float) -> dict:
    """Extreure grid profit pur d'un spot grid bot.
    Pionex envia el cash directament al wallet sense vendre cap base.
    Endpoint: POST /api/v1/bot/orders/spotGrid/profit
    """
    bot_id = _resolve_bot_id(bot_id_or_name)
    body = {"buOrderId": bot_id, "amount": f"{amount:.4f}"}
    return _post_signed("/api/v1/bot/orders/spotGrid/profit", body)


def reduce_bot(bot_id_or_name, quote_amount: float) -> dict:
    """Redueix capital d'un bot, retornant USDT al wallet."""
    bot_id = _resolve_bot_id(bot_id_or_name)
    body = {"buOrderId": bot_id, "quoteReduce": f"{quote_amount:.4f}"}
    return _post_signed("/api/v1/bot/orders/spotGrid/reduce", body)


def _resolve_bot_id(bot_id_or_name: str) -> str:
    """Resol nom de bot (PAXG_USDT) a id, o retorna l'id si ja és uuid."""
    from config import BOTS
    if bot_id_or_name in BOTS:
        return BOTS[bot_id_or_name]["id"]
    return bot_id_or_name


# ─── Helpers ─────────────────────────────────────────────────────────
def get_atr(symbol: str, period: int = 7) -> float:
    """ATR from daily klines."""
    klines = get_klines(symbol, "1D", period + 1)
    if len(klines) < 2: return 0.0
    trs = []
    for i in range(1, len(klines)):
        high = float(klines[i]["high"])
        low = float(klines[i]["low"])
        prev_close = float(klines[i-1]["close"])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs[-period:]) / min(len(trs), period)


def get_current_price(symbol: str = SYMBOL) -> float:
    t = get_ticker(symbol)
    return float(t.get("close", 0))


def get_bot_range(bot_id: str, symbol: str = None):
    """Return (top, bottom, current_price, distance_to_top, distance_to_bottom).
    If symbol not given, derives it from the bot's base+quote fields."""
    bot = get_bot_order(bot_id)
    data = bot.get("buOrderData", {})
    top = float(data.get("top", 0))
    bottom = float(data.get("bottom", 0))
    if symbol is None:
        # Derive symbol from bot fields
        base = bot.get("base", "")
        quote = bot.get("quote", "")
        symbol = f"{base}_{quote}" if base and quote else SYMBOL
    price = get_current_price(symbol)
    return {
        "top": top,
        "bottom": bottom,
        "price": price,
        "width": top - bottom,
        "dist_to_top": top - price,
        "dist_to_bottom": price - bottom,
        "dist_to_top_pct": (top - price) / (top - bottom) if top != bottom else 0,
        "dist_to_bottom_pct": (price - bottom) / (top - bottom) if top != bottom else 0,
        "status": data.get("status", "unknown"),
        "grid_profit": float(data.get("gridProfit", 0)),
        "realized_profit": float(data.get("realizedProfit", 0)),
        "base_in_bot": float(data.get("baseAmount", 0)),
        "quote_in_bot": float(data.get("quoteAmount", 0)),
        "usdt_investment": float(data.get("usdtInvestment", 0)),
        "quote_total_investment": float(data.get("quoteTotalInvestment", 0)),
        "filled_orders": int(data.get("closedExchangeOrderCount", 0)),
        "placed_orders": int(data.get("placedExchangeOrderCount", 0)),
        "paired_cycles": int(data.get("exchangeOrderPairedCount", 0)),
        "avg_cost": float(data.get("averageCost", 0)),
        "grid_rows": int(data.get("row", 0)),
        "create_time_ms": int(data.get("createTime", 0)),
        # Fee tracking (per a cost real de recolocacions)
        # consum acumulat de fees = (reserve - remain), en base i quote
        "base_fee_remain": float(data.get("baseFeeRemain", 0)),
        "quote_fee_remain": float(data.get("quoteFeeRemain", 0)),
        "base_fee_reserve": float(data.get("baseFeeReserve", 0)),
        "quote_fee_reserve": float(data.get("quoteFeeReserve", 0)),
        "fee_total_investment": float(data.get("feeTotalInvestment", 0)),
    }


if __name__ == "__main__":
    # Smoke test
    print("Ticker PAXG:", get_ticker("PAXG_USDT"))
    print("\nATR(7) PAXG:", get_atr("PAXG_USDT", 7))
    print("\nBalance:", get_balance())
