"""Fetch 5 years EURUSD M5 from Dukascopy."""
import dukascopy_python as dk
from datetime import datetime, timedelta, timezone
import pandas as pd

END = datetime.now(timezone.utc)
START = END - timedelta(days=365*5)

dfs = []
chunk_start = START
while chunk_start < END:
    chunk_end = min(chunk_start + timedelta(days=180), END)
    print(f"chunk {chunk_start.date()} -> {chunk_end.date()}", flush=True)
    try:
        df = dk.fetch(
            instrument="EUR/USD",
            offer_side=dk.OFFER_SIDE_BID,
            interval=dk.INTERVAL_MIN_5,
            start=chunk_start,
            end=chunk_end,
        )
        if df is not None and len(df) > 0:
            dfs.append(df)
            print(f"  got {len(df)}", flush=True)
    except Exception as e:
        print(f"  FAIL {str(e)[:80]}", flush=True)
    chunk_start = chunk_end

full = pd.concat(dfs)
full = full[~full.index.duplicated(keep='first')].sort_index()
print(f"\nTotal: {len(full)} from {full.index[0]} to {full.index[-1]}", flush=True)
full.to_csv("eurusd_m5_5y.csv")
