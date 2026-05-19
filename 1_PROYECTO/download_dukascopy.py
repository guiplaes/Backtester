"""Descarrega M5 5y de Dukascopy — format EUR/CHF (amb slash)."""
import dukascopy_python as dk
import pandas as pd
from datetime import datetime, timedelta, timezone
import os

PAIRS = [
    'EUR/CHF', 'EUR/GBP', 'AUD/NZD', 'USD/CHF', 'EUR/JPY', 'CHF/JPY',
    'NZD/CHF', 'CAD/CHF', 'AUD/CHF', 'USD/CAD', 'USD/JPY', 'GBP/USD',
    'GBP/CHF', 'GBP/JPY', 'AUD/USD', 'NZD/USD', 'AUD/JPY', 'NZD/JPY',
    'EUR/AUD', 'EUR/NZD', 'EUR/CAD', 'GBP/AUD', 'GBP/NZD', 'GBP/CAD',
    'AUD/CAD', 'NZD/CAD', 'CAD/JPY', 'SGD/JPY',
]

end_dt = datetime.now(timezone.utc) - timedelta(days=2)
start_dt = end_dt - timedelta(days=5*365)
print(f"From {start_dt.date()} to {end_dt.date()}")

for pair in PAIRS:
    canon = pair.replace('/','')
    out = f"{canon.lower()}_dk_m5_5y.csv"
    if os.path.exists(out):
        sz = os.path.getsize(out)
        if sz > 100000:
            print(f"  {canon}: already exists ({sz//1024}KB), skip")
            continue
    try:
        print(f"  {canon}...", flush=True)
        df = dk.fetch(pair, dk.INTERVAL_MIN_5, dk.OFFER_SIDE_BID, start_dt, end_dt)
        if df is None or len(df)==0:
            print(f"    no data")
            continue
        df.to_csv(out)
        print(f"    -> {out}: {len(df)} bars from {df.index[0]} to {df.index[-1]}")
    except Exception as e:
        print(f"    error: {e}")

print("Done")
