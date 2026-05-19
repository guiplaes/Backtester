"""
Data loader — llegeix CSVs M1 i genera streams alineats per timestamp.

CSVs (un per actiu):
  open_time_ms, open, high, low, close, volume, close_time_ms

Output del loader: iterador de dicts amb format
  {ts_ms: int, "PAXG_USDT": bar_dict, "BTC_USDT": ..., ...}

Si en un minut un asset NO té dades (festiu, etc.), el bar conté None.
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Iterator

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"

log = logging.getLogger("data_loader")


def load_csv(symbol: str) -> dict[int, dict]:
    """Llegeix CSV i retorna {ts_ms: {open, high, low, close, volume}}."""
    path = DATA_DIR / f"m1_{symbol}.csv"
    if not path.exists():
        raise FileNotFoundError(f"CSV no existeix: {path}")

    bars = {}
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = int(row["open_time_ms"])
            bars[ts] = {
                "open":   float(row["open"]),
                "high":   float(row["high"]),
                "low":    float(row["low"]),
                "close":  float(row["close"]),
                "volume": float(row["volume"]),
            }
    return bars


def aligned_iterator(symbols: list[str], start_ms: int = 0, end_ms: int | None = None) -> Iterator[dict]:
    """Itera barres alineades per timestamp.

    Yields: {"ts_ms": int, "BTC_USDT": {...}, "ETH_USDT": {...}, ...}
    """
    log.info(f"Carregant {len(symbols)} CSVs...")
    data = {}
    all_ts = set()
    for sym in symbols:
        bars = load_csv(sym)
        data[sym] = bars
        all_ts.update(bars.keys())
        log.info(f"  {sym}: {len(bars):,} barres")

    sorted_ts = sorted(all_ts)
    if start_ms:
        sorted_ts = [t for t in sorted_ts if t >= start_ms]
    if end_ms:
        sorted_ts = [t for t in sorted_ts if t <= end_ms]

    log.info(f"Total timestamps únics: {len(sorted_ts):,}")

    for ts in sorted_ts:
        out = {"ts_ms": ts}
        for sym in symbols:
            out[sym] = data[sym].get(ts)  # None si no hi ha bar
        yield out


def date_range_info(symbols: list[str]) -> dict:
    """Info de cobertura: data més antiga / més recent per actiu."""
    info = {}
    for sym in symbols:
        try:
            bars = load_csv(sym)
            if bars:
                ts_min = min(bars.keys())
                ts_max = max(bars.keys())
                from datetime import datetime, timezone
                info[sym] = {
                    "from": datetime.fromtimestamp(ts_min/1000, timezone.utc).isoformat(),
                    "to":   datetime.fromtimestamp(ts_max/1000, timezone.utc).isoformat(),
                    "bars": len(bars),
                }
            else:
                info[sym] = {"error": "empty"}
        except Exception as e:
            info[sym] = {"error": str(e)}
    return info


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    info = date_range_info(["BTC_USDT", "ETH_USDT", "SOL_USDT", "PAXG_USDT"])
    for sym, d in info.items():
        print(f"{sym}: {d}")
