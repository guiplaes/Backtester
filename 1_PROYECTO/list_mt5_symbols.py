"""List all available MT5 symbols, focus on forex."""
import MetaTrader5 as mt5

if not mt5.initialize():
    print(f"Init failed: {mt5.last_error()}")
    quit()

symbols = mt5.symbols_get()
print(f"Total symbols: {len(symbols)}")

forex = [s for s in symbols if s.path.startswith('Forex') or 'Forex' in s.path]
print(f"Forex symbols: {len(forex)}")
print()
print("All forex symbols:")
for s in forex:
    print(f"  {s.name:<12} | path={s.path}")

print()
print("All symbol names with EUR or CHF or NZD or AUD or JPY:")
for s in symbols:
    name = s.name.upper()
    if any(c in name for c in ['EUR','CHF','NZD','AUD','JPY','GBP','CAD','CNH']):
        print(f"  {s.name:<15} path={s.path}")

mt5.shutdown()
