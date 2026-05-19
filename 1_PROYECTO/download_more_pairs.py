"""Descarrega més pairs candidates per mean-rev."""
import dukascopy_python as dk
import pandas as pd
from datetime import datetime, timedelta, timezone
import os

PAIRS = ['GBP/CHF', 'NZD/CAD', 'USD/CAD', 'AUD/CAD', 'EUR/CAD',
         'GBP/JPY', 'NZD/JPY', 'AUD/JPY', 'USD/JPY',
         'GBP/USD', 'NZD/USD', 'AUD/USD',
         'EUR/AUD', 'EUR/NZD', 'GBP/AUD', 'GBP/NZD']

end_dt = datetime.now(timezone.utc) - timedelta(days=2)
start_dt = end_dt - timedelta(days=5*365)

for pair in PAIRS:
    canon = pair.replace('/','')
    out = f"{canon.lower()}_dk_m5_5y.csv"
    if os.path.exists(out) and os.path.getsize(out) > 100000:
        print(f"  {canon}: already exists, skip"); continue
    try:
        print(f"  {canon}...", flush=True)
        df = dk.fetch(pair, dk.INTERVAL_MIN_5, dk.OFFER_SIDE_BID, start_dt, end_dt)
        if df is None or len(df)==0: print(f"    no data"); continue
        df.to_csv(out)
        print(f"    -> {out}: {len(df)} bars")
    except Exception as e:
        print(f"    error: {e}")
print("Done")
