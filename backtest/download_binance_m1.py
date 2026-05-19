"""
Descarrega barres M1 OHLCV de Binance per als 4 actius del nostre portfolio.
Sense API key (endpoint públic), rate limit 1200 req/min.

Output: CSV per actiu a backtest/data/m1_{symbol}.csv
Format: open_time_ms, open, high, low, close, volume, close_time_ms

Binance kline API:
  GET https://api.binance.com/api/v3/klines
  ?symbol=BTCUSDT&interval=1m&startTime={ms}&limit=1000

Estratègia: descàrrega per blocs de 1000 barres (= 1000 minuts ≈ 16,7h),
amb pausa de 0,5s entre crides per estar molt sota del rate limit.

12 mesos × 525.600 min ≈ 525k barres × 4 actius = ~2.1M barres total.
Estimació temps: 525.600/1000 × 0,5s × 4 = ~1.050s = ~18 min descàrrega total.

NOTA PAXG: Binance té PAXGUSDT (Paxos Gold). Cobertura ~3-4 anys.
"""
from __future__ import annotations

import csv
import gzip
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Symbols: Binance fa servir mateixos noms però sense underscore (BTCUSDT no BTC_USDT)
SYMBOLS = {
    "BTC_USDT":  "BTCUSDT",
    "ETH_USDT":  "ETHUSDT",
    "SOL_USDT":  "SOLUSDT",
    "PAXG_USDT": "PAXGUSDT",
}

API_URL = "https://api.binance.com/api/v3/klines"
BATCH_LIMIT = 1000  # max bars per request
INTER_REQ_DELAY = 0.4  # seg, ~150 req/min molt per sota del 1200 limit

# Configuració
MONTHS_BACK = 12  # 12 mesos enrere
NOW_MS = int(datetime.now(timezone.utc).timestamp() * 1000)
START_MS = int((datetime.now(timezone.utc) - timedelta(days=30 * MONTHS_BACK)).timestamp() * 1000)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(BASE / "logs" / "download.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
(BASE / "logs").mkdir(parents=True, exist_ok=True)
log = logging.getLogger("downloader")


def fetch_batch(binance_symbol: str, start_ms: int) -> list[list]:
    """Una crida a /klines per agafar fins a 1000 barres a partir de start_ms."""
    params = {
        "symbol": binance_symbol,
        "interval": "1m",
        "startTime": start_ms,
        "limit": BATCH_LIMIT,
    }
    for attempt in range(5):
        try:
            r = requests.get(API_URL, params=params, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 418):  # rate limit
                wait = 2 ** attempt
                log.warning(f"Rate limit (status {r.status_code}), waiting {wait}s")
                time.sleep(wait)
                continue
            log.error(f"HTTP {r.status_code}: {r.text[:200]}")
            return []
        except Exception as e:
            log.warning(f"Request failed (attempt {attempt+1}/5): {e}")
            time.sleep(2)
    log.error("Failed after 5 retries")
    return []


def download_symbol(local_symbol: str, binance_symbol: str, start_ms: int, end_ms: int):
    """Descarrega tot el rang M1 i guarda a CSV."""
    out_path = DATA_DIR / f"m1_{local_symbol}.csv"

    # Skip si ja existeix i és recent
    if out_path.exists():
        size_mb = out_path.stat().st_size / 1024 / 1024
        log.info(f"[{local_symbol}] CSV ja existeix ({size_mb:.1f} MB), skip. Esborra'l per re-descarregar.")
        return

    log.info(f"[{local_symbol}] Començant descàrrega de {binance_symbol}...")
    start_iso = datetime.fromtimestamp(start_ms / 1000, timezone.utc).isoformat()
    end_iso = datetime.fromtimestamp(end_ms / 1000, timezone.utc).isoformat()
    log.info(f"[{local_symbol}] Rang: {start_iso} → {end_iso}")

    all_bars = []
    cursor_ms = start_ms
    batch_count = 0
    last_print = time.time()

    while cursor_ms < end_ms:
        bars = fetch_batch(binance_symbol, cursor_ms)
        if not bars:
            log.warning(f"[{local_symbol}] Batch buit a {cursor_ms}, saltem 1000 minuts")
            cursor_ms += 60_000 * BATCH_LIMIT
            continue

        # Filtra barres dins el rang
        for b in bars:
            if b[0] >= end_ms:
                break
            all_bars.append(b)

        # Avança el cursor al següent ms post-last bar
        cursor_ms = bars[-1][0] + 60_000  # next minute

        batch_count += 1

        # Progress cada 30s
        if time.time() - last_print > 30:
            done_pct = (cursor_ms - start_ms) / (end_ms - start_ms) * 100
            log.info(f"[{local_symbol}] {len(all_bars):,} barres · {done_pct:.1f}% · batch {batch_count}")
            last_print = time.time()

        time.sleep(INTER_REQ_DELAY)

    # Guarda CSV
    log.info(f"[{local_symbol}] Total barres descarregades: {len(all_bars):,}. Escrivint CSV...")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["open_time_ms", "open", "high", "low", "close", "volume", "close_time_ms"])
        for b in all_bars:
            # Binance kline: [open_time, open, high, low, close, volume, close_time, ...]
            w.writerow([b[0], b[1], b[2], b[3], b[4], b[5], b[6]])

    size_mb = out_path.stat().st_size / 1024 / 1024
    log.info(f"[{local_symbol}] ✅ Guardat a {out_path.name} ({size_mb:.1f} MB)")


def main():
    log.info("=" * 70)
    log.info(f"Binance M1 download — {MONTHS_BACK} mesos enrere")
    log.info(f"Start: {datetime.fromtimestamp(START_MS / 1000, timezone.utc).isoformat()}")
    log.info(f"End:   {datetime.fromtimestamp(NOW_MS / 1000, timezone.utc).isoformat()}")
    log.info(f"Símbols: {list(SYMBOLS.keys())}")
    log.info("=" * 70)

    for local, binance in SYMBOLS.items():
        try:
            download_symbol(local, binance, START_MS, NOW_MS)
        except Exception as e:
            log.exception(f"[{local}] FATAL: {e}")

    log.info("=" * 70)
    log.info("DOWNLOAD COMPLET")
    log.info("=" * 70)
    # Summary
    for local in SYMBOLS:
        p = DATA_DIR / f"m1_{local}.csv"
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                lines = sum(1 for _ in f) - 1  # -1 header
            size_mb = p.stat().st_size / 1024 / 1024
            log.info(f"  {local}: {lines:,} barres · {size_mb:.1f} MB")


if __name__ == "__main__":
    main()
