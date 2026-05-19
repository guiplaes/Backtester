"""
Backtest winner strategy on 1+ year of M5 XAUUSD data from MT5.

Strategy: Inside Bar Breakout + Volume + ATR-based SL/TP
- Entry: previous bar inside its mother bar; current bar breaks mother
  range with vol > 1.3 × SMA(20) and trend (close vs EMA50)
- SL: entry ± 1.5 × ATR(14)
- TP1 (50%): entry ± 10 × ATR(14)
- TP2 (50%): entry ± 20 × ATR(14)

Output: full backtest stats, monthly breakdown, equity curve.
"""
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import sys

SYMBOL = "XAUUSD.crp"
TIMEFRAME = mt5.TIMEFRAME_M5
DAYS_BACK = 400  # ~13 months

# Strategy params (winner from TV backtest)
SL_MULT  = 1.5
TP1_MULT = 10.0
TP2_MULT = 20.0
TP1_PCT  = 0.5  # 50% close at TP1
ATR_LEN  = 14
EMA_LEN  = 50
VOL_MULT = 1.3
VOL_LEN  = 20

# Realistic costs
COMMISSION_PER_SIDE = 0.5     # USD per round trip side
SPREAD = 0.50                 # XAUUSD typical spread $/oz at OANDA
SLIPPAGE = 0.20               # USD slippage on each fill (avg)

def fetch_data():
    if not mt5.initialize():
        print(f"MT5 init failed: {mt5.last_error()}")
        return None

    # Find correct symbol name
    symbols = mt5.symbols_get(SYMBOL)
    if not symbols:
        # Try variants
        for variant in [SYMBOL, "XAUUSDm", "XAUUSD.r", "XAUUSDe", "GOLD", "Gold"]:
            symbols = mt5.symbols_get(variant)
            if symbols:
                print(f"Found symbol: {variant}")
                actual_symbol = variant
                break
        else:
            print("Symbol not found. Available symbols sample:")
            all_syms = mt5.symbols_get()
            for s in all_syms[:30]:
                print(f"  {s.name}")
            mt5.shutdown()
            return None
    else:
        actual_symbol = SYMBOL

    mt5.symbol_select(actual_symbol, True)  # don't fail if select returns False

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=DAYS_BACK)
    print(f"Fetching {actual_symbol} M5 from {start} to {end}")

    # Pull bars in chunks (MT5 has limits on single fetch request)
    bars_needed = min(DAYS_BACK * 24 * 12, 100000)  # cap at 100k
    rates = mt5.copy_rates_from(actual_symbol, TIMEFRAME, end, 50000)
    if rates is None or len(rates) == 0:
        print(f"copy_rates_from failed: {mt5.last_error()}")
        return None
    print(f"First fetch: {len(rates)} bars")
    # Try to extend with another chunk going further back
    if len(rates) >= 50000 and bars_needed > 50000:
        oldest_time = datetime.fromtimestamp(int(rates[0]['time']), tz=timezone.utc)
        rates2 = mt5.copy_rates_from(actual_symbol, TIMEFRAME, oldest_time, 50000)
        if rates2 is not None and len(rates2) > 0:
            print(f"Second fetch: {len(rates2)} bars older")
            import numpy as np
            rates = np.concatenate([rates2, rates])
            # Dedup by time
            _, uniq_idx = np.unique(rates['time'], return_index=True)
            rates = rates[np.sort(uniq_idx)]
            print(f"After dedup: {len(rates)} bars")
    if rates is None or len(rates) == 0:
        print(f"No data: {mt5.last_error()}")
        mt5.shutdown()
        return None

    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df = df.set_index('time')
    print(f"Got {len(df)} bars from {df.index[0]} to {df.index[-1]}")
    mt5.shutdown()
    return df

def compute_indicators(df):
    df = df.copy()
    # EMA50
    df['ema50'] = df['close'].ewm(span=EMA_LEN, adjust=False).mean()
    # ATR(14)
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['atr'] = tr.ewm(alpha=1/ATR_LEN, adjust=False).mean()
    # Volume avg
    df['vol_avg'] = df['tick_volume'].rolling(VOL_LEN).mean()
    # Inside bar = bar within previous bar
    df['inside'] = (df['high'] < df['high'].shift(1)) & (df['low'] > df['low'].shift(1))
    return df

def backtest(df):
    """
    Run inside-bar-breakout strategy bar by bar.
    Returns: trades list (each: entry_time, exit_time, side, entry, sl, tp1, tp2,
             qty1_pnl, qty2_pnl, total_pnl)
    """
    trades = []
    position = None  # {side, entry_price, entry_time, sl, tp1, tp2, atr, qty1_open, qty2_open}

    rows = df.itertuples()
    rows_iter = enumerate(df.iterrows())

    for i, (ts, bar) in rows_iter:
        if i < max(EMA_LEN, ATR_LEN, VOL_LEN) + 5:
            continue

        # === If in position, check exits ===
        if position is not None:
            side = position['side']
            high = bar['high']
            low = bar['low']
            # Apply slippage to fills (worse fill on hit)
            if side == 'L':
                # Check SL hit
                if position['qty1_open'] > 0 or position['qty2_open'] > 0:
                    sl_eff = position['sl']
                    tp1_eff = position['tp1']
                    tp2_eff = position['tp2']

                    # Conservative: assume worst-case sequence
                    # If both SL and TPs in range, assume SL hits first
                    sl_hit = low <= sl_eff
                    tp1_hit = high >= tp1_eff
                    tp2_hit = high >= tp2_eff

                    # Worst case for long: SL hits first if both possible
                    if sl_hit and (tp1_hit or tp2_hit):
                        # Decide based on which is closer to open
                        open_price = bar['open']
                        dist_to_sl = open_price - sl_eff
                        dist_to_tp = tp1_eff - open_price
                        if dist_to_sl < dist_to_tp:
                            # SL closer = SL hits first
                            tp1_hit = False
                            tp2_hit = False

                    # Process fills
                    if tp1_hit and position['qty1_open'] > 0:
                        pnl_qty1 = (tp1_eff - position['entry_price']) * TP1_PCT - SLIPPAGE * TP1_PCT
                        position['qty1_pnl'] = pnl_qty1
                        position['qty1_exit_time'] = ts
                        position['qty1_open'] = 0
                    if tp2_hit and position['qty2_open'] > 0:
                        pnl_qty2 = (tp2_eff - position['entry_price']) * (1-TP1_PCT) - SLIPPAGE * (1-TP1_PCT)
                        position['qty2_pnl'] = pnl_qty2
                        position['qty2_exit_time'] = ts
                        position['qty2_open'] = 0
                    if sl_hit:
                        if position['qty1_open'] > 0:
                            pnl_qty1 = (sl_eff - position['entry_price']) * TP1_PCT - SLIPPAGE * TP1_PCT
                            position['qty1_pnl'] = pnl_qty1
                            position['qty1_exit_time'] = ts
                            position['qty1_open'] = 0
                        if position['qty2_open'] > 0:
                            pnl_qty2 = (sl_eff - position['entry_price']) * (1-TP1_PCT) - SLIPPAGE * (1-TP1_PCT)
                            position['qty2_pnl'] = pnl_qty2
                            position['qty2_exit_time'] = ts
                            position['qty2_open'] = 0
            else:  # SHORT
                sl_eff = position['sl']
                tp1_eff = position['tp1']
                tp2_eff = position['tp2']

                sl_hit = high >= sl_eff
                tp1_hit = low <= tp1_eff
                tp2_hit = low <= tp2_eff

                if sl_hit and (tp1_hit or tp2_hit):
                    open_price = bar['open']
                    dist_to_sl = sl_eff - open_price
                    dist_to_tp = open_price - tp1_eff
                    if dist_to_sl < dist_to_tp:
                        tp1_hit = False
                        tp2_hit = False

                if tp1_hit and position['qty1_open'] > 0:
                    pnl_qty1 = (position['entry_price'] - tp1_eff) * TP1_PCT - SLIPPAGE * TP1_PCT
                    position['qty1_pnl'] = pnl_qty1
                    position['qty1_exit_time'] = ts
                    position['qty1_open'] = 0
                if tp2_hit and position['qty2_open'] > 0:
                    pnl_qty2 = (position['entry_price'] - tp2_eff) * (1-TP1_PCT) - SLIPPAGE * (1-TP1_PCT)
                    position['qty2_pnl'] = pnl_qty2
                    position['qty2_exit_time'] = ts
                    position['qty2_open'] = 0
                if sl_hit:
                    if position['qty1_open'] > 0:
                        pnl_qty1 = (position['entry_price'] - sl_eff) * TP1_PCT - SLIPPAGE * TP1_PCT
                        position['qty1_pnl'] = pnl_qty1
                        position['qty1_exit_time'] = ts
                        position['qty1_open'] = 0
                    if position['qty2_open'] > 0:
                        pnl_qty2 = (position['entry_price'] - sl_eff) * (1-TP1_PCT) - SLIPPAGE * (1-TP1_PCT)
                        position['qty2_pnl'] = pnl_qty2
                        position['qty2_exit_time'] = ts
                        position['qty2_open'] = 0

            # If both legs closed, finalize
            if position['qty1_open'] == 0 and position['qty2_open'] == 0:
                total_pnl = position.get('qty1_pnl', 0) + position.get('qty2_pnl', 0)
                # Subtract commissions
                total_pnl -= COMMISSION_PER_SIDE * 2  # entry + exit (and partials count as multiple)
                # Subtract spread cost
                total_pnl -= SPREAD
                trades.append({
                    'entry_time': position['entry_time'],
                    'exit_time': position.get('qty2_exit_time', position.get('qty1_exit_time')),
                    'side': position['side'],
                    'entry_price': position['entry_price'],
                    'sl': position['sl'],
                    'tp1': position['tp1'],
                    'tp2': position['tp2'],
                    'atr': position['atr'],
                    'qty1_pnl': position.get('qty1_pnl', 0),
                    'qty2_pnl': position.get('qty2_pnl', 0),
                    'pnl': total_pnl,
                })
                position = None

        # === If no position, check entry signals ===
        if position is None and i > 0:
            # Need bar[-1] to be inside (mother is bar[-2])
            prev = df.iloc[i-1]
            prev_prev = df.iloc[i-2] if i >= 2 else None
            if prev_prev is None:
                continue

            prev_was_inside = prev['inside']
            mother_high = prev_prev['high']
            mother_low  = prev_prev['low']

            if not prev_was_inside or pd.isna(bar['ema50']) or pd.isna(bar['atr']) or pd.isna(bar['vol_avg']):
                continue

            vol_ok = bar['tick_volume'] > bar['vol_avg'] * VOL_MULT
            if not vol_ok:
                continue

            # Long signal
            if bar['close'] > bar['ema50'] and bar['high'] > mother_high and bar['close'] > mother_high:
                entry = bar['close']
                position = {
                    'side': 'L',
                    'entry_time': ts,
                    'entry_price': entry,
                    'sl':  entry - bar['atr'] * SL_MULT,
                    'tp1': entry + bar['atr'] * TP1_MULT,
                    'tp2': entry + bar['atr'] * TP2_MULT,
                    'atr': bar['atr'],
                    'qty1_open': TP1_PCT,
                    'qty2_open': 1-TP1_PCT,
                }
            # Short signal
            elif bar['close'] < bar['ema50'] and bar['low'] < mother_low and bar['close'] < mother_low:
                entry = bar['close']
                position = {
                    'side': 'S',
                    'entry_time': ts,
                    'entry_price': entry,
                    'sl':  entry + bar['atr'] * SL_MULT,
                    'tp1': entry - bar['atr'] * TP1_MULT,
                    'tp2': entry - bar['atr'] * TP2_MULT,
                    'atr': bar['atr'],
                    'qty1_open': TP1_PCT,
                    'qty2_open': 1-TP1_PCT,
                }

    return trades

def report(trades, df):
    if not trades:
        print("No trades.")
        return

    tdf = pd.DataFrame(trades)
    n = len(tdf)
    wins = (tdf['pnl'] > 0).sum()
    losses = (tdf['pnl'] <= 0).sum()
    wr = wins / n * 100
    net = tdf['pnl'].sum()
    gross_p = tdf[tdf['pnl']>0]['pnl'].sum()
    gross_l = abs(tdf[tdf['pnl']<=0]['pnl'].sum())
    pf = gross_p / gross_l if gross_l else 0
    avg = tdf['pnl'].mean()

    # Drawdown calc
    eq = 10000 + tdf['pnl'].cumsum()
    peak = eq.cummax()
    dd = (peak - eq).max()

    days = (df.index[-1] - df.index[0]).days
    months = days / 30.44

    print("="*70)
    print(f"BACKTEST RESULT - {SYMBOL} M5  ({df.index[0].date()} to {df.index[-1].date()})")
    print(f"Period: {days} calendar days = {months:.1f} months")
    print(f"Bars: {len(df)}")
    print("="*70)
    print(f"Trades:        {n}")
    print(f"Wins / Losses: {wins} / {losses}")
    print(f"Win Rate:      {wr:.1f}%")
    print(f"Net P/L:       ${net:.2f}  (start $10,000 -> ${10000+net:.2f}, {net/100:.2f}%)")
    print(f"Profit Factor: {pf:.2f}")
    print(f"Max DD:        ${dd:.2f}  ({dd/100:.2f}% of capital)")
    print(f"Avg/Trade:     ${avg:.2f}")
    print(f"Trades/day:    {n/days:.2f}" if days else "")
    print()

    # Monthly breakdown
    print("MONTHLY BREAKDOWN:")
    tdf['month'] = pd.to_datetime(tdf['entry_time']).dt.to_period('M')
    monthly = tdf.groupby('month').agg(
        trades=('pnl', 'count'),
        wins=('pnl', lambda x: (x>0).sum()),
        net=('pnl', 'sum'),
        avg=('pnl', 'mean'),
    )
    monthly['wr%'] = (monthly['wins'] / monthly['trades'] * 100).round(1)
    print(monthly.to_string())
    print()

    # Save trades to CSV
    tdf.to_csv("backtest_trades_1y.csv", index=False)
    print(f"Saved trades to backtest_trades_1y.csv")

    # Equity curve
    print()
    print("EQUITY CURVE (every 10th trade):")
    for i, (ts, e) in enumerate(zip(tdf['exit_time'], eq)):
        if i % 10 == 0 or i == len(eq)-1:
            print(f"  Trade {i+1:4d}: equity ${e:>9.2f}  ({ts})")

def main():
    print(f"Fetching {DAYS_BACK} days of M5 data...")
    df = fetch_data()
    if df is None:
        print("FAILED to get data")
        sys.exit(1)

    print("Computing indicators...")
    df = compute_indicators(df)

    print("Running backtest...")
    trades = backtest(df)

    report(trades, df)

if __name__ == "__main__":
    main()
