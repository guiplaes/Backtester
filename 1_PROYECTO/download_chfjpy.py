"""Descarrega CHFJPY M5 5y de Dukascopy (el guanyador del MT5 sweep)."""
import dukascopy_python as dk
from datetime import datetime, timedelta, timezone
import os

PAIRS = ['CHF/JPY']
end_dt = datetime.now(timezone.utc) - timedelta(days=2)
start_dt = end_dt - timedelta(days=5*365)

for pair in PAIRS:
    canon = pair.replace('/','')
    out = f"{canon.lower()}_dk_m5_5y.csv"
    if os.path.exists(out) and os.path.getsize(out) > 100000:
        print(f"  {canon}: exists, skip"); continue
    print(f"  {canon}...", flush=True)
    df = dk.fetch(pair, dk.INTERVAL_MIN_5, dk.OFFER_SIDE_BID, start_dt, end_dt)
    if df is None or len(df)==0:
        print(f"    no data"); continue
    df.to_csv(out)
    print(f"    -> {out}: {len(df)} bars")
print("Done")
