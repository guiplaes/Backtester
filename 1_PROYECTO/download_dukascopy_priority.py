"""Descarrega prioritzada — només 5 pairs TIER-S amb spread baix.
EURCHF, EURGBP ja descarregats. Aquí: AUDNZD, USDCHF, EURJPY."""
import dukascopy_python as dk
import pandas as pd
from datetime import datetime, timedelta, timezone
import os

PRIORITY_PAIRS = ['AUD/NZD', 'USD/CHF', 'EUR/JPY']

end_dt = datetime.now(timezone.utc) - timedelta(days=2)
start_dt = end_dt - timedelta(days=5*365)
print(f"From {start_dt.date()} to {end_dt.date()}")

for pair in PRIORITY_PAIRS:
    canon = pair.replace('/','')
    out = f"{canon.lower()}_dk_m5_5y.csv"
    if os.path.exists(out) and os.path.getsize(out) > 100000:
        print(f"  {canon}: already exists, skip")
        continue
    try:
        print(f"  {canon}...", flush=True)
        df = dk.fetch(pair, dk.INTERVAL_MIN_5, dk.OFFER_SIDE_BID, start_dt, end_dt)
        if df is None or len(df)==0:
            print(f"    no data"); continue
        df.to_csv(out)
        print(f"    -> {out}: {len(df)} bars from {df.index[0]} to {df.index[-1]}")
    except Exception as e:
        print(f"    error: {e}")
print("Done")
