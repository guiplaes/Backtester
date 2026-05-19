"""
TEST 7: Trade Monitor active management via M1 replay.
For each historical trade (from winning config A grade), replay the trade
bar-by-bar at M1 resolution. Every 5-10 M1 bars, send context to LLM
asking HOLD/EXIT_NOW/MOVE_BE.

Apply LLM decisions and compare to passive (no monitor) result.

PASS: WR improvement >= 5pp OR Net P/L >= 1.2× passive baseline.
"""
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timezone
import json, time
from urllib.request import Request, urlopen

api_key = None
with open(r"C:\Users\Administrator\Desktop\MT4 Claude\1_PROYECTO\.env") as f:
    for line in f:
        if "DEEPSEEK_API_KEY=" in line:
            api_key = line.split("=", 1)[1].strip()
            break

SYMBOL = "XAUUSD.crp"
ATR_LEN = 14; EMA_LEN = 50; VOL_LEN = 20
COMMISSION = 0.5; SPREAD = 0.50; SLIPPAGE = 0.20
SL_M = 1.5; TP1_M = 15; TP2_M = 30
CHECK_INTERVAL_M1 = 5  # check LLM every 5 M1 bars (= 5 min)
MAX_LLM_CALLS_PER_TRADE = 12  # cap to control cost

import sys
def log(msg):
    print(msg, flush=True)

def fetch(tf, max_bars=50000):
    mt5.initialize(); mt5.symbol_select(SYMBOL, True)
    end = datetime.now(timezone.utc)
    rates = mt5.copy_rates_from(SYMBOL, tf, end, max_bars)
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True); df = df.set_index('time')
    mt5.shutdown(); return df

log("Fetching M1 (last ~5 weeks 50k bars)...")
m1 = fetch(mt5.TIMEFRAME_M1, max_bars=50000)
log(f"M1: {len(m1)} bars from {m1.index[0]} to {m1.index[-1]}")
# Align M5 to overlap fully with M1 range — only fetch M5 within M1 window
m1_start = m1.index[0]
log("Fetching M5 (aligned to M1 range)...")
m5 = fetch(mt5.TIMEFRAME_M5, max_bars=50000)
m5 = m5[m5.index >= m1_start]
log(f"M5 (filtered to align): {len(m5)} bars from {m5.index[0]} to {m5.index[-1]}")

def compute_m5(df):
    df = df.copy()
    df['ema50'] = df['close'].ewm(span=EMA_LEN, adjust=False).mean()
    hl = df['high']-df['low']; hc = (df['high']-df['close'].shift()).abs(); lc = (df['low']-df['close'].shift()).abs()
    tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
    df['atr'] = tr.ewm(alpha=1/ATR_LEN, adjust=False).mean()
    df['vol_avg'] = df['tick_volume'].rolling(VOL_LEN).mean()
    df['inside'] = (df['high']<df['high'].shift(1)) & (df['low']>df['low'].shift(1))
    return df

m5 = compute_m5(m5)

# Detect entries (winning config)
def detect_entries(df):
    entries = []
    for i in range(EMA_LEN+5, len(df)):
        bar = df.iloc[i]; ts = df.index[i]
        if i < 2: continue
        prev = df.iloc[i-1]; pp = df.iloc[i-2]
        if not prev['inside']: continue
        if pd.isna(bar['ema50']) or pd.isna(bar['atr']) or pd.isna(bar['vol_avg']): continue
        if bar['tick_volume'] <= bar['vol_avg']*1.3: continue
        if ts.dayofweek == 2: continue
        if not (0 <= ts.hour <= 6): continue
        if bar['close']<=bar['ema50']: continue
        mh = pp['high']
        if not (bar['high']>mh and bar['close']>mh): continue
        atr = bar['atr']
        e = bar['close']
        entries.append({
            'ts': ts, 'entry': e, 'sl': e-atr*SL_M, 'tp1': e+atr*TP1_M, 'tp2': e+atr*TP2_M,
            'atr': atr,
        })
    return entries

entries = detect_entries(m5)
print(f"Entries detected: {len(entries)}")

PROMPT = """Ets el guardian d'un trade XAUUSD obert (LONG).
Cada cycle reps dades vives. Decideix:
- HOLD: tot va segons plan
- EXIT_NOW: tesi morta, tanca tot
- MOVE_BE: profit raonable acumulat (>=2×ATR), mou SL a entry

Resposta JSON: {"action":"HOLD"|"EXIT_NOW"|"MOVE_BE","reason":"breu"}"""

def llm_decide(snapshot):
    body = {
        "model": "deepseek-chat",
        "messages": [{"role":"system","content":PROMPT},{"role":"user","content":snapshot}],
        "response_format": {"type":"json_object"},
        "max_tokens": 100,
        "temperature": 0.2,
    }
    req = Request("https://api.deepseek.com/chat/completions",
                  data=json.dumps(body).encode(),
                  headers={"Authorization":f"Bearer {api_key}", "Content-Type":"application/json"},
                  method="POST")
    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        content = data["choices"][0]["message"]["content"]
        result = json.loads(content)
        return result.get("action","HOLD"), result.get("reason","")
    except Exception as e:
        return "HOLD", f"err:{str(e)[:30]}"

def replay_passive(entry):
    """No monitor: hit SL or TP normally."""
    after = m1[m1.index > entry['ts']]
    sl = entry['sl']; tp1 = entry['tp1']; tp2 = entry['tp2']
    e = entry['entry']
    qty1 = 0.5; qty2 = 0.5
    pnl1 = pnl2 = 0
    for ts, bar in after.iterrows():
        if pd.Timestamp(ts) - pd.Timestamp(entry['ts']) > pd.Timedelta(hours=72):
            # Time stop: close at market after 72h
            cur = bar['close']
            if qty1 > 0: pnl1 = (cur - e) * 0.5 - SLIPPAGE * 0.5; qty1 = 0
            if qty2 > 0: pnl2 = (cur - e) * 0.5 - SLIPPAGE * 0.5; qty2 = 0
            break
        sl_h = bar['low'] <= sl
        tp1_h = bar['high'] >= tp1
        tp2_h = bar['high'] >= tp2
        if sl_h and (tp1_h or tp2_h):
            if (bar['open']-sl) < (tp1-bar['open']): tp1_h = False; tp2_h = False
        if tp1_h and qty1 > 0: pnl1 = (tp1 - e) * 0.5 - SLIPPAGE * 0.5; qty1 = 0
        if tp2_h and qty2 > 0: pnl2 = (tp2 - e) * 0.5 - SLIPPAGE * 0.5; qty2 = 0
        if sl_h:
            if qty1 > 0: pnl1 = (sl - e) * 0.5 - SLIPPAGE * 0.5; qty1 = 0
            if qty2 > 0: pnl2 = (sl - e) * 0.5 - SLIPPAGE * 0.5; qty2 = 0
        if qty1 == 0 and qty2 == 0: break
    return pnl1 + pnl2 - COMMISSION*2 - SPREAD

def replay_with_monitor(entry, max_calls=MAX_LLM_CALLS_PER_TRADE):
    """With LLM monitor checking every 5 M1 bars."""
    after = m1[m1.index > entry['ts']]
    sl = entry['sl']; tp1 = entry['tp1']; tp2 = entry['tp2']
    e = entry['entry']; atr = entry['atr']
    qty1 = 0.5; qty2 = 0.5
    pnl1 = pnl2 = 0
    moved_be = False
    calls = 0
    bars_since_entry = 0

    last_check_bar = -CHECK_INTERVAL_M1
    after_list = list(after.iterrows())

    for k, (ts, bar) in enumerate(after_list):
        bars_since_entry = k

        if pd.Timestamp(ts) - pd.Timestamp(entry['ts']) > pd.Timedelta(hours=72):
            cur = bar['close']
            if qty1 > 0: pnl1 = (cur - e) * 0.5 - SLIPPAGE * 0.5; qty1 = 0
            if qty2 > 0: pnl2 = (cur - e) * 0.5 - SLIPPAGE * 0.5; qty2 = 0
            break

        # Check SL/TP first
        sl_h = bar['low'] <= sl
        tp1_h = bar['high'] >= tp1
        tp2_h = bar['high'] >= tp2
        if sl_h and (tp1_h or tp2_h):
            if (bar['open']-sl) < (tp1-bar['open']): tp1_h = False; tp2_h = False
        if tp1_h and qty1 > 0: pnl1 = (tp1 - e) * 0.5 - SLIPPAGE * 0.5; qty1 = 0
        if tp2_h and qty2 > 0: pnl2 = (tp2 - e) * 0.5 - SLIPPAGE * 0.5; qty2 = 0
        if sl_h:
            if qty1 > 0: pnl1 = (sl - e) * 0.5 - SLIPPAGE * 0.5; qty1 = 0
            if qty2 > 0: pnl2 = (sl - e) * 0.5 - SLIPPAGE * 0.5; qty2 = 0
        if qty1 == 0 and qty2 == 0: break

        # Periodic LLM check
        if k - last_check_bar >= CHECK_INTERVAL_M1 and calls < max_calls:
            cur = bar['close']
            unrealized = cur - e
            r_units = unrealized / atr if atr else 0
            snapshot = (f"+{(k+1)*60}s elapsed. "
                        f"Preu actual: ${cur:.2f} (entry ${e:.2f}, +{unrealized:+.2f}, {r_units:+.2f}R)\n"
                        f"M1 last: O={bar['open']:.2f} H={bar['high']:.2f} L={bar['low']:.2f} C={bar['close']:.2f} V={int(bar['tick_volume'])}\n"
                        f"SL: ${sl:.2f} (BE={'YES' if moved_be else 'NO'}) TP1: ${tp1:.2f} TP2: ${tp2:.2f}\n"
                        f"ATR: ${atr:.2f}")
            action, _ = llm_decide(snapshot)
            calls += 1
            last_check_bar = k

            if action == "EXIT_NOW":
                # Close all at current price
                cur = bar['close']
                if qty1 > 0: pnl1 = (cur - e) * 0.5 - SLIPPAGE * 0.5; qty1 = 0
                if qty2 > 0: pnl2 = (cur - e) * 0.5 - SLIPPAGE * 0.5; qty2 = 0
                break
            elif action == "MOVE_BE" and not moved_be and r_units >= 1.5:
                sl = e + 0.1  # small buffer above entry
                moved_be = True

    return pnl1 + pnl2 - COMMISSION*2 - SPREAD

# Run on subset for cost control
import csv
print(f"\nRunning passive baseline on {len(entries)} trades...")
passive_pnls = []
for i, e in enumerate(entries):
    if i % 20 == 0: print(f"  Passive {i}/{len(entries)}")
    pnl = replay_passive(e)
    passive_pnls.append(pnl)

print(f"\nRunning monitored on {len(entries)} trades (LLM calls)...")
monitor_pnls = []
for i, e in enumerate(entries):
    if i % 5 == 0: print(f"  Monitor {i}/{len(entries)}")
    pnl = replay_with_monitor(e)
    monitor_pnls.append(pnl)
    time.sleep(0.05)

def stats_arr(arr, name):
    arr = np.array(arr)
    n = len(arr); w = (arr>0).sum(); net = arr.sum()
    pf_p = arr[arr>0].sum(); pf_l = abs(arr[arr<=0].sum())
    pf = pf_p/pf_l if pf_l else 0
    eq = np.cumsum(arr); peak = np.maximum.accumulate(eq); dd = (peak-eq).max()
    print(f"{name:>30}: n={n} | WR {w/n*100:>5.1f}% | Net ${net:>+8.2f} | PF {pf:>5.2f} | DD ${dd:>6.2f}")
    return n, w/n*100, net, pf

print()
sn, swr, snet, spf = stats_arr(passive_pnls, "Passive (no monitor)")
mn, mwr, mnet, mpf = stats_arr(monitor_pnls, "With LLM monitor")

with open('test7_results.csv', 'w', newline='') as f:
    wr = csv.writer(f)
    wr.writerow(['idx','ts','passive','monitored'])
    for i, e in enumerate(entries):
        wr.writerow([i, e['ts'], passive_pnls[i], monitor_pnls[i]])

print()
delta_wr = mwr - swr
delta_net = mnet - snet
ratio = mnet / snet if snet > 0 else 0
print(f"Delta WR: {delta_wr:+.1f}pp")
print(f"Delta Net: ${delta_net:+.2f} (ratio {ratio:.2f}×)")
if delta_wr >= 5 or ratio >= 1.20:
    print(">>> TRADE MONITOR PASSES")
else:
    print(">>> TRADE MONITOR FAILS")
