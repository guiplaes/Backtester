"""Download H1 data 5y for forex pairs (fits in single calls)."""
import MetaTrader5 as mt5
import pandas as pd
import numpy as np

mt5.initialize()

PAIRS = {
    'EURCHF': 'EURCHF-VIPc',
    'EURGBP': 'EURGBP-VIPc',
    'AUDNZD': 'AUDNZD-VIPc',
    'USDCHF': 'USDCHF-VIPc',
    'EURJPY': 'EURJPY-VIPc',
    'CHFJPY': 'CHFJPY-VIPc',
    'USDCNH': 'USDCNH-VIPc',
    'NZDCHF': 'NZDCHF-VIPc',
    'CADCHF': 'CADCHF-VIPc',
    'AUDCHF': 'AUDCHF-VIPc',
    'USDHKD': 'USDHKD',
}

# 5y H1 = 43800 bars, well within 50k limit
COUNT = 50000

for canon, sym in PAIRS.items():
    if not mt5.symbol_select(sym, True): continue
    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, COUNT)
    if rates is None or len(rates)==0:
        print(f"  {sym}: no data ({mt5.last_error()})")
        continue
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df = df.drop_duplicates(subset=['time']).sort_values('time').set_index('time')
    df = df.rename(columns={'tick_volume':'volume'})
    df = df[['open','high','low','close','volume']]
    out = f"{canon.lower()}_h1_5y.csv"
    df.to_csv(out)
    print(f"  {canon} -> {out}: {len(df)} bars from {df.index[0]} to {df.index[-1]}")

mt5.shutdown()
print("Done")
