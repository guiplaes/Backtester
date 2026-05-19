"""
Grid search of strategy parameters on 17-month sample.
Tests different SL/TP combinations to find robust edge.
"""
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
import sys

SYMBOL = "XAUUSD.crp"
TIMEFRAME = mt5.TIMEFRAME_M5
ATR_LEN = 14
EMA_LEN = 50
VOL_LEN = 20

# Cost params
COMMISSION = 0.5
SPREAD = 0.50
SLIPPAGE = 0.20

def fetch_data(bars=100000):
    mt5.initialize()
    mt5.symbol_select(SYMBOL, True)
    end = datetime.now(timezone.utc)
    rates = mt5.copy_rates_from(SYMBOL, TIMEFRAME, end, 50000)
    if len(rates) >= 50000:
        oldest = datetime.fromtimestamp(int(rates[0]['time']), tz=timezone.utc)
        rates2 = mt5.copy_rates_from(SYMBOL, TIMEFRAME, oldest, 50000)
        if rates2 is not None and len(rates2) > 0:
            rates = np.concatenate([rates2, rates])
            _, idx = np.unique(rates['time'], return_index=True)
            rates = rates[np.sort(idx)]
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df = df.set_index('time')
    mt5.shutdown()
    return df

def compute_indicators(df):
    df = df.copy()
    df['ema50'] = df['close'].ewm(span=EMA_LEN, adjust=False).mean()
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['atr'] = tr.ewm(alpha=1/ATR_LEN, adjust=False).mean()
    df['vol_avg'] = df['tick_volume'].rolling(VOL_LEN).mean()
    df['inside'] = (df['high'] < df['high'].shift(1)) & (df['low'] > df['low'].shift(1))
    return df

def backtest(df, sl_mult, tp1_mult, tp2_mult, vol_mult=1.3, costs=True):
    """Returns: (n_trades, wins, losses, net_pnl, max_dd)"""
    trades = []
    pos = None

    for i in range(max(EMA_LEN, ATR_LEN, VOL_LEN) + 5, len(df)):
        bar = df.iloc[i]
        ts = df.index[i]

        if pos is not None:
            side = pos['side']
            high = bar['high']
            low = bar['low']
            sl_eff = pos['sl']
            tp1_eff = pos['tp1']
            tp2_eff = pos['tp2']

            if side == 'L':
                sl_hit = low <= sl_eff
                tp1_hit = high >= tp1_eff
                tp2_hit = high >= tp2_eff
                if sl_hit and (tp1_hit or tp2_hit):
                    op = bar['open']
                    if (op - sl_eff) < (tp1_eff - op):
                        tp1_hit = False; tp2_hit = False
                if tp1_hit and pos['qty1_open'] > 0:
                    pos['qty1_pnl'] = (tp1_eff - pos['entry'])*0.5 - (SLIPPAGE*0.5 if costs else 0)
                    pos['qty1_open'] = 0
                if tp2_hit and pos['qty2_open'] > 0:
                    pos['qty2_pnl'] = (tp2_eff - pos['entry'])*0.5 - (SLIPPAGE*0.5 if costs else 0)
                    pos['qty2_open'] = 0
                if sl_hit:
                    if pos['qty1_open']>0:
                        pos['qty1_pnl'] = (sl_eff - pos['entry'])*0.5 - (SLIPPAGE*0.5 if costs else 0)
                        pos['qty1_open']=0
                    if pos['qty2_open']>0:
                        pos['qty2_pnl'] = (sl_eff - pos['entry'])*0.5 - (SLIPPAGE*0.5 if costs else 0)
                        pos['qty2_open']=0
            else:
                sl_hit = high >= sl_eff
                tp1_hit = low <= tp1_eff
                tp2_hit = low <= tp2_eff
                if sl_hit and (tp1_hit or tp2_hit):
                    op = bar['open']
                    if (sl_eff - op) < (op - tp1_eff):
                        tp1_hit = False; tp2_hit = False
                if tp1_hit and pos['qty1_open'] > 0:
                    pos['qty1_pnl'] = (pos['entry'] - tp1_eff)*0.5 - (SLIPPAGE*0.5 if costs else 0)
                    pos['qty1_open'] = 0
                if tp2_hit and pos['qty2_open'] > 0:
                    pos['qty2_pnl'] = (pos['entry'] - tp2_eff)*0.5 - (SLIPPAGE*0.5 if costs else 0)
                    pos['qty2_open'] = 0
                if sl_hit:
                    if pos['qty1_open']>0:
                        pos['qty1_pnl'] = (pos['entry'] - sl_eff)*0.5 - (SLIPPAGE*0.5 if costs else 0)
                        pos['qty1_open']=0
                    if pos['qty2_open']>0:
                        pos['qty2_pnl'] = (pos['entry'] - sl_eff)*0.5 - (SLIPPAGE*0.5 if costs else 0)
                        pos['qty2_open']=0

            if pos['qty1_open'] == 0 and pos['qty2_open'] == 0:
                tp = pos.get('qty1_pnl',0) + pos.get('qty2_pnl',0)
                if costs:
                    tp -= COMMISSION*2 + SPREAD
                trades.append(tp)
                pos = None

        if pos is None and i >= 2:
            prev = df.iloc[i-1]
            prev_prev = df.iloc[i-2]
            if not prev['inside']: continue
            if pd.isna(bar['ema50']) or pd.isna(bar['atr']) or pd.isna(bar['vol_avg']): continue
            if bar['tick_volume'] <= bar['vol_avg'] * vol_mult: continue

            mh = prev_prev['high']
            ml = prev_prev['low']
            atr = bar['atr']
            entry = bar['close']

            if bar['close'] > bar['ema50'] and bar['high'] > mh and bar['close'] > mh:
                pos = {'side':'L','entry':entry,'sl':entry-atr*sl_mult,
                       'tp1':entry+atr*tp1_mult,'tp2':entry+atr*tp2_mult,
                       'qty1_open':0.5,'qty2_open':0.5}
            elif bar['close'] < bar['ema50'] and bar['low'] < ml and bar['close'] < ml:
                pos = {'side':'S','entry':entry,'sl':entry+atr*sl_mult,
                       'tp1':entry-atr*tp1_mult,'tp2':entry-atr*tp2_mult,
                       'qty1_open':0.5,'qty2_open':0.5}

    if not trades:
        return 0, 0, 0, 0, 0, 0
    arr = np.array(trades)
    wins = (arr > 0).sum()
    losses = (arr <= 0).sum()
    net = arr.sum()
    eq = np.cumsum(arr)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq).max()
    pf_p = arr[arr>0].sum()
    pf_l = abs(arr[arr<=0].sum())
    pf = pf_p/pf_l if pf_l else 0
    return len(trades), wins, losses, net, dd, pf

def main():
    print("Fetching data...")
    df = fetch_data()
    print(f"Got {len(df)} bars from {df.index[0].date()} to {df.index[-1].date()}")
    print("Computing indicators...")
    df = compute_indicators(df)

    # Grid search
    print("\nGRID SEARCH (with realistic costs):")
    print("-"*100)
    print(f"{'SL':>5} | {'TP1':>5} | {'TP2':>5} | {'Vol':>4} | {'Trades':>6} | {'WR':>5} | {'NetP/L':>10} | {'PF':>6} | {'MaxDD':>8}")
    print("-"*100)

    grids = [
        (1.0, 2, 4, 1.3),
        (1.0, 3, 6, 1.3),
        (1.0, 5, 10, 1.3),
        (1.0, 8, 16, 1.3),
        (1.0, 10, 20, 1.3),
        (1.5, 5, 10, 1.3),
        (1.5, 10, 20, 1.3),
        (1.5, 15, 30, 1.3),
        (2.0, 10, 20, 1.3),
        (2.0, 15, 30, 1.3),
        # Higher vol filter
        (1.0, 5, 10, 1.5),
        (1.5, 10, 20, 1.5),
        (1.5, 10, 20, 2.0),
        # Tight SL
        (0.7, 5, 10, 1.3),
        (0.7, 10, 20, 1.3),
    ]

    best_pf = 0
    best_cfg = None
    for sl, tp1, tp2, vol in grids:
        n, w, l, net, dd, pf = backtest(df, sl, tp1, tp2, vol, costs=True)
        wr = w/n*100 if n else 0
        marker = ""
        if net > 0 and pf > best_pf:
            best_pf = pf
            best_cfg = (sl, tp1, tp2, vol)
            marker = " <-- BEST"
        print(f"{sl:>5.1f} | {tp1:>5.0f} | {tp2:>5.0f} | {vol:>4.1f} | {n:>6} | {wr:>4.1f}% | {net:>+10.2f} | {pf:>6.2f} | {dd:>8.2f}{marker}")

    print("\nGRID SEARCH (NO COSTS - to see raw edge):")
    print("-"*100)
    for sl, tp1, tp2, vol in grids:
        n, w, l, net, dd, pf = backtest(df, sl, tp1, tp2, vol, costs=False)
        wr = w/n*100 if n else 0
        print(f"{sl:>5.1f} | {tp1:>5.0f} | {tp2:>5.0f} | {vol:>4.1f} | {n:>6} | {wr:>4.1f}% | {net:>+10.2f} | {pf:>6.2f} | {dd:>8.2f}")

    if best_cfg:
        print(f"\nBest profitable config: SL={best_cfg[0]} TP1={best_cfg[1]} TP2={best_cfg[2]} Vol={best_cfg[3]}")
    else:
        print("\nNO PROFITABLE config found in grid (with realistic costs)")

if __name__ == "__main__":
    main()
