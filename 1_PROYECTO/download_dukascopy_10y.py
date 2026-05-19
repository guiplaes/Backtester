"""Descarrega 10y de Dukascopy per als V6 pairs."""
import dukascopy_python as dk
import pandas as pd
from datetime import datetime, timedelta, timezone
import os
import sys
sys.path.insert(0, '.')
try: from tg_send import send as tg_send
except: tg_send = lambda x: None

# V6 pairs (10 estables)
PAIRS = ['EUR/GBP','EUR/CHF','GBP/CHF','AUD/CAD','USD/CAD',
         'NZD/CAD','USD/CHF','AUD/NZD','GBP/NZD','EUR/NZD']

end_dt = datetime.now(timezone.utc) - timedelta(days=2)
start_dt = end_dt - timedelta(days=10*365)  # 10 ANYS!

print(f"From {start_dt.date()} to {end_dt.date()}")
tg_send(f"📥 Descarregant 10 ANYS de dades dels {len(PAIRS)} V6 pairs (Dukascopy)...")

for pair in PAIRS:
    canon = pair.replace('/','')
    out = f"{canon.lower()}_dk_m5_10y.csv"
    if os.path.exists(out) and os.path.getsize(out) > 100000:
        sz_mb = os.path.getsize(out) / (1024*1024)
        print(f"  {canon}: existeix ({sz_mb:.0f}MB), skip")
        continue
    try:
        print(f"  {canon}...", flush=True)
        df = dk.fetch(pair, dk.INTERVAL_MIN_5, dk.OFFER_SIDE_BID, start_dt, end_dt)
        if df is None or len(df)==0:
            print(f"    no data"); continue
        df.to_csv(out)
        print(f"    -> {out}: {len(df)} bars from {df.index[0].date()} to {df.index[-1].date()}")
    except Exception as e:
        print(f"    error: {e}")

# Final notify
tg_send(f"✅ Descarrega 10y completada. Llançant backtest sobre 10 anys (2015-2026 inclou SNB break, Brexit, COVID).")
print("\nDONE")
