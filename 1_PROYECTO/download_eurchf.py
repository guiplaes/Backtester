"""
Download EURCHF + altres parells mean-revertibles del MT5.
"""
import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta, timezone

# Connect to MT5
if not mt5.initialize():
    print(f"Init failed: {mt5.last_error()}")
    quit()

print(f"Connected. Account: {mt5.account_info()._asdict() if mt5.account_info() else 'no info'}")

ALIASES = {
    'EURCHF': ['EURCHF-VIPc'],
    'EURGBP': ['EURGBP-VIPc'],
    'AUDNZD': ['AUDNZD-VIPc'],
    'USDCHF': ['USDCHF-VIPc'],
    'EURJPY': ['EURJPY-VIPc'],
    'CHFJPY': ['CHFJPY-VIPc'],
    'USDCNH': ['USDCNH-VIPc'],
    'NZDCHF': ['NZDCHF-VIPc'],
    'CADCHF': ['CADCHF-VIPc'],
    'AUDCHF': ['AUDCHF-VIPc'],
    'USDHKD': ['USDHKD'],
}

# Find available symbols
print("\nSearching for available symbols...")
available = {}
for canonical, aliases in ALIASES.items():
    for s in aliases:
        info = mt5.symbol_info(s)
        if info is not None:
            available[canonical] = s
            print(f"  Found {canonical} as '{s}'")
            break
    if canonical not in available:
        print(f"  {canonical}: NOT FOUND")

print(f"\nAvailable: {available}")

# Download M5 5y
end_dt = datetime.now(timezone.utc)
start_dt = end_dt - timedelta(days=5*365)
print(f"\nDownloading from {start_dt.date()} to {end_dt.date()}")

CHUNK = 50000  # max bars per call
for canonical, symbol in available.items():
    if not mt5.symbol_select(symbol, True):
        print(f"  {symbol}: select failed")
        continue
    # Get total available
    all_rates = []
    pos = 0
    while True:
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, pos, CHUNK)
        if rates is None or len(rates) == 0:
            break
        all_rates.append(rates)
        if len(rates) < CHUNK:
            break
        pos += CHUNK
        if pos >= 600000: break  # safety cap
    if not all_rates:
        print(f"  {symbol}: no data")
        continue
    import numpy as np
    combined = np.concatenate(all_rates)
    df = pd.DataFrame(combined)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df = df.drop_duplicates(subset=['time']).sort_values('time')
    df = df.set_index('time')
    df = df.rename(columns={'tick_volume':'volume'})
    df = df[['open','high','low','close','volume']]
    # Cut to last 5y
    cutoff = df.index[-1] - pd.Timedelta(days=5*365)
    df = df[df.index >= cutoff]
    out_csv = f"{canonical.lower()}_m5_5y.csv"
    df.to_csv(out_csv)
    print(f"  {symbol} -> {out_csv}: {len(df)} bars from {df.index[0]} to {df.index[-1]}")

mt5.shutdown()
print("\nDone")
