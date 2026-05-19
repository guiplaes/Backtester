"""Fetch 5 years of XAUUSD M5 from Dukascopy in yearly chunks."""
import dukascopy_python as dk
from datetime import datetime, timedelta, timezone
import pandas as pd
import sys

END = datetime.now(timezone.utc)
START = END - timedelta(days=365*5)

print(f"Fetching XAU/USD M5 from {START} to {END}", flush=True)

dfs = []
chunk_start = START
while chunk_start < END:
    chunk_end = min(chunk_start + timedelta(days=180), END)
    print(f"  chunk {chunk_start.date()} -> {chunk_end.date()}", flush=True)
    try:
        df = dk.fetch(
            instrument="XAU/USD",
            offer_side=dk.OFFER_SIDE_BID,
            interval=dk.INTERVAL_MIN_5,
            start=chunk_start,
            end=chunk_end,
        )
        if df is not None and len(df) > 0:
            dfs.append(df)
            print(f"    got {len(df)} bars", flush=True)
    except Exception as e:
        print(f"    FAIL: {str(e)[:100]}", flush=True)
    chunk_start = chunk_end

if not dfs:
    print("NO DATA"); sys.exit(1)

full = pd.concat(dfs)
full = full[~full.index.duplicated(keep='first')]
full = full.sort_index()
print(f"\nTotal: {len(full)} bars from {full.index[0]} to {full.index[-1]}", flush=True)
full.to_csv("xauusd_m5_5y.csv")
print("Saved to xauusd_m5_5y.csv", flush=True)
