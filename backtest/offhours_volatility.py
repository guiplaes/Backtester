"""
Volatilitat dels tokens X durant hores de mercat vs fora hores.
Pregunta: el grid captura cycles quan NYSE/NASDAQ està tancat?

Mètode:
1. Fetch M5 dels últims 7 dies (Pionex API)
2. Etiquetar cada bar com "market_hours" o "off_hours" segons UTC
   NYSE/NASDAQ: 14:30-21:00 UTC dl-dv (durant DST)
3. Calcular range mig per minut a cada bucket
4. Aplicar width del sistema (2× daily ATR) i veure quants fills/hora seria possible
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Hardcoded data agafada via MCP — faig pull amb subprocess via Python no aquí
# Plan: per cada token, agafar 7 dies de M5 mitjançant MCP

TOKENS = [
    "PAXG_USDT",       # benchmark 24/7
    "BTC_USDT",        # benchmark 24/7
    "SPYX_USDT_PERP",
    "QQQX_USDT_PERP",
    "LMTX_USDT_PERP",
    "RTXX_USDT_PERP",
    "LLYX_USDT_PERP",
    "UNHX_USDT_PERP",
    "CVXX_USDT_PERP",
    "GSGX_USDT_PERP",
    "CPERX_USDT_PERP",
    "USOX_USDT_PERP",
    "AAPLX_USDT_PERP",
    "MSFTX_USDT_PERP",
    "NVDAX_USDT_PERP",
    "TSLAX_USDT_PERP",
    "EWJX_USDT_PERP",
]


def is_market_open(ts_ms: int) -> bool:
    """Retorna True si NYSE/NASDAQ està obert a aquell ts.
    Aproximat: dl-dv 14:30-21:00 UTC (durant DST, EST).
    En realitat varia per DST: 13:30-20:00 UTC en hivern.
    Usem 14:30-21:00 com mig (DST estiu = ara mateix maig)."""
    dt = datetime.fromtimestamp(ts_ms / 1000, timezone.utc)
    wd = dt.weekday()  # 0=dl, 6=dg
    if wd >= 5:  # cap de setmana
        return False
    hh_mm = dt.hour * 60 + dt.minute
    return 13 * 60 + 30 <= hh_mm <= 20 * 60  # 13:30 - 20:00 UTC (winter)


def analyze_bars(symbol: str, bars: list) -> dict:
    """Analitza una llista de bars M5 i divideix per market/off hours."""
    mkt_ranges = []
    off_ranges = []
    mkt_count = 0
    off_count = 0
    for b in bars:
        if not b.get("close") or float(b.get("close", 0)) <= 0:
            continue
        ts = int(b["time"])
        rng = (float(b["high"]) - float(b["low"])) / float(b["close"]) * 100
        if is_market_open(ts):
            mkt_ranges.append(rng)
            mkt_count += 1
        else:
            off_ranges.append(rng)
            off_count += 1

    def stats(rs):
        if not rs:
            return None
        rs = sorted(rs)
        return {
            "mean_pct": sum(rs) / len(rs),
            "median_pct": rs[len(rs) // 2],
            "max_pct": rs[-1],
            "count_bars": len(rs),
        }

    return {
        "market_hours": stats(mkt_ranges),
        "off_hours": stats(off_ranges),
    }


print("Aquest script necessita dades M5 dels tokens via Pionex MCP.")
print(f"Tokens a analitzar: {len(TOKENS)}")
print("Per cada token, cal fer pionex_market_get_klines amb interval=5M, limit=500")
print("Es processarà a part i s'escriurà offhours_results.json")
