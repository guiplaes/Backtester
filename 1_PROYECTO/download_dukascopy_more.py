"""Descarrega M5 5y addicional de més actius per testar mean-reversion."""
import dukascopy_python as dk
import pandas as pd
from datetime import datetime, timedelta, timezone
import os

# Descobreix tots els instruments forex disponibles
all_attrs = [a for a in dir(dk) if a.startswith('INSTRUMENT_')]
print(f"Total instruments disponibles: {len(all_attrs)}")
print()

# Mapping addicional — els que no he descarregat encara
EXTRA_PAIRS = {}
for attr in all_attrs:
    name_lower = attr.lower()
    # Pairs candidates per mean-reversion (crosses + minors)
    candidates = [
        'aud_chf','cad_chf','aud_jpy','nzd_jpy','gbp_chf','gbp_aud','gbp_nzd',
        'eur_aud','eur_nzd','eur_cad','eur_sek','eur_nok','usd_sek','usd_nok',
        'usd_cad','usd_jpy','gbp_jpy','aud_cad','nzd_cad','sgd_jpy',
        'usd_sgd','usd_zar','usd_mxn','usd_pln','usd_czk','usd_cnh','usd_hkd',
    ]
    for c in candidates:
        if c in name_lower:
            try:
                pair_name = c.upper().replace('_','')
                EXTRA_PAIRS[pair_name] = getattr(dk, attr)
                break
            except: pass

print(f"Pairs addicionals a descarregar: {len(EXTRA_PAIRS)}")
for p in EXTRA_PAIRS: print(f"  {p}")

end_dt = datetime.now(timezone.utc)
start_dt = end_dt - timedelta(days=5*365)
print(f"\nFrom {start_dt.date()} to {end_dt.date()}")

for canon, instr in EXTRA_PAIRS.items():
    out = f"{canon.lower()}_dk_m5_5y.csv"
    if os.path.exists(out):
        print(f"  {canon}: already exists, skip")
        continue
    try:
        print(f"  {canon}...", flush=True)
        df = dk.fetch(instr, dk.INTERVAL_MIN_5, dk.OFFER_SIDE_BID, start_dt, end_dt)
        if df is None or len(df)==0:
            print(f"    no data")
            continue
        df.to_csv(out)
        print(f"    -> {out}: {len(df)} bars")
    except Exception as e:
        print(f"    error: {e}")

print("Done extra")
