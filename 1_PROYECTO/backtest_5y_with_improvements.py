"""
5-year backtest combining ALL detected improvements:
1. Baseline Inside Bar BO + LONG + Asia + skip Wed + TP 15/30 + SL 1.5
2. + LLM filter (T4 PASS)
3. + Streak sizing (T1 PASS)
4. + Both combined

Cost: ~380 LLM calls × $0.002 = ~$0.80
"""
import pandas as pd
import numpy as np
import json
import time
from urllib.request import Request, urlopen

api_key = None
with open(r"C:\Users\Administrator\Desktop\MT4 Claude\1_PROYECTO\.env") as f:
    for line in f:
        if "DEEPSEEK_API_KEY=" in line:
            api_key = line.split("=", 1)[1].strip()

CSV = "xauusd_m5_5y.csv"
ATR_LEN = 14; EMA_LEN = 50; VOL_LEN = 20
COMMISSION = 0.5; SPREAD = 0.50; SLIPPAGE = 0.20
SL_M = 1.5; TP1_M = 15; TP2_M = 30

print("Loading 5y...", flush=True)
df = pd.read_csv(CSV, index_col=0, parse_dates=True)
df.index = pd.to_datetime(df.index, utc=True)
df.columns = [c.lower() for c in df.columns]
if 'tick_volume' not in df.columns: df['tick_volume'] = df['volume']
print(f"{len(df)} bars", flush=True)

print("Indicators...", flush=True)
df['ema50'] = df['close'].ewm(span=EMA_LEN, adjust=False).mean()
df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
hl = df['high']-df['low']; hc = (df['high']-df['close'].shift()).abs(); lc = (df['low']-df['close'].shift()).abs()
tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
df['atr'] = tr.ewm(alpha=1/ATR_LEN, adjust=False).mean()
df['vol_avg'] = df['tick_volume'].rolling(VOL_LEN).mean()
df['inside'] = (df['high']<df['high'].shift(1)) & (df['low']>df['low'].shift(1))

def backtest_collect(df_):
    trades = []; pos = None
    for i in range(EMA_LEN+5, len(df_)):
        bar = df_.iloc[i]; ts = df_.index[i]
        if pos is not None:
            sl_h = bar['low'] <= pos['sl']
            tp1_h = bar['high'] >= pos['tp1']
            tp2_h = bar['high'] >= pos['tp2']
            if sl_h and (tp1_h or tp2_h):
                if (bar['open']-pos['sl']) < (pos['tp1']-bar['open']): tp1_h=False; tp2_h=False
            if tp1_h and pos['q1']>0: pos['pnl1'] = (pos['tp1']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q1']=0
            if tp2_h and pos['q2']>0: pos['pnl2'] = (pos['tp2']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q2']=0
            if sl_h:
                if pos['q1']>0: pos['pnl1'] = (pos['sl']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q1']=0
                if pos['q2']>0: pos['pnl2'] = (pos['sl']-pos['e'])*0.5 - SLIPPAGE*0.5; pos['q2']=0
            if pos['q1']==0 and pos['q2']==0:
                tp = pos.get('pnl1',0)+pos.get('pnl2',0)-COMMISSION*2-SPREAD
                trades.append({'ts': pos['ts'], 'pnl': tp, 'context': pos['ctx']})
                pos = None
        if pos is None and i>=2:
            prev = df_.iloc[i-1]; pp = df_.iloc[i-2]
            if not prev['inside']: continue
            if pd.isna(bar['ema50']) or pd.isna(bar['atr']) or pd.isna(bar['vol_avg']): continue
            if bar['tick_volume'] <= bar['vol_avg']*1.3: continue
            if ts.dayofweek == 2: continue
            if not (0 <= ts.hour <= 6): continue
            if bar['close']<=bar['ema50']: continue
            mh = pp['high']
            if not (bar['high']>mh and bar['close']>mh): continue
            atr = bar['atr']; e = bar['close']
            ctx = {
                'ts': str(ts), 'price': float(e), 'ema50': float(bar['ema50']),
                'atr': float(atr), 'vol_ratio': float(bar['tick_volume']/bar['vol_avg']),
                'mother_high': float(pp['high']),
                'breakout_size': float(e - pp['high']),
                'ema50_dist_pct': (float(e) - float(bar['ema50']))/float(e)*100,
            }
            pos = {'e':e,'ts':ts,'sl':e-atr*SL_M,'tp1':e+atr*TP1_M,'tp2':e+atr*TP2_M,'q1':0.5,'q2':0.5,'ctx':ctx}
    return trades

PROMPT = """Avalua qualitat d'un setup XAUUSD M5 Inside Bar BO LONG en sessió Asiàtica.
A=alt potencial, B=mig, C=rebutjar.
Criteris: Volum, distancia EMA50, mida breakout, ATR sensible.
JSON: {"score":"A"|"B"|"C","reason":""}"""

def score_llm(ctx):
    user = (f"Time: {ctx['ts']}\nPrice: ${ctx['price']:.2f}\n"
            f"EMA50: ${ctx['ema50']:.2f} (dist {ctx['ema50_dist_pct']:+.2f}%)\n"
            f"ATR: ${ctx['atr']:.2f}\n"
            f"Volume ratio: {ctx['vol_ratio']:.2f}×\n"
            f"Mother high: ${ctx['mother_high']:.2f}\n"
            f"Breakout size: ${ctx['breakout_size']:.2f}")
    body = {"model":"deepseek-chat",
            "messages":[{"role":"system","content":PROMPT},{"role":"user","content":user}],
            "response_format":{"type":"json_object"},
            "max_tokens":80,"temperature":0.3}
    req = Request("https://api.deepseek.com/chat/completions",
                  data=json.dumps(body).encode(),
                  headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"},
                  method="POST")
    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        result = json.loads(data["choices"][0]["message"]["content"])
        return result.get("score","B").upper()[:1]
    except Exception as e:
        return "B"

def stats(arr, name):
    if len(arr)==0: print(f"{name}: 0 trades"); return None
    arr = np.array(arr)
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    print(f"{name:>40}: n={n:>4} | WR {w/n*100:>5.1f}% | Net ${net:>+9.2f} | PF {pf:>5.2f} | DD ${dd:>7.2f}", flush=True)
    return {'n':n,'wr':w/n*100,'net':net,'pf':pf,'dd':dd,'arr':arr}

print("Backtest...", flush=True)
trades = backtest_collect(df)
print(f"{len(trades)} trades total", flush=True)

# Save trades for safety
import pickle
with open('5y_trades.pkl','wb') as f: pickle.dump(trades, f)

# Baseline
arr_base = [t['pnl'] for t in trades]
s_base = stats(arr_base, "1) Baseline 5y")

# Streak sizing
streak_pnls = []
size = 1.0; consec_l = consec_w = 0
for t in trades:
    p = t['pnl'] * size
    streak_pnls.append(p)
    if t['pnl'] > 0:
        consec_w += 1; consec_l = 0
        if consec_w >= 3 and size < 2.0: size = min(2.0, size*1.3)
        if consec_w == 1 and size < 1.0: size = 1.0
    else:
        consec_l += 1; consec_w = 0
        if consec_l >= 2: size = max(0.5, size*0.7)
s_streak = stats(streak_pnls, "2) Baseline + Streak sizing")

# LLM filter
print("Running LLM filter on 380 trades (~6 min)...", flush=True)
scores = []
for i, t in enumerate(trades):
    if i % 50 == 0: print(f"  LLM {i}/{len(trades)}", flush=True)
    s = score_llm(t['context'])
    scores.append(s)
    time.sleep(0.05)

with open('5y_scores.json','w') as f: json.dump(scores, f)

a_only = [t['pnl'] for t,s in zip(trades, scores) if s=='A']
ab = [t['pnl'] for t,s in zip(trades, scores) if s in ('A','B')]
print(f"\nScore distribution: A={scores.count('A')}, B={scores.count('B')}, C={scores.count('C')}", flush=True)
s_a = stats(a_only, "3) Baseline + LLM A-only")
s_ab = stats(ab, "4) Baseline + LLM A+B")

# Combined: A-grade + Streak
arr_combo = []
size = 1.0; consec_l = consec_w = 0
for t, s in zip(trades, scores):
    if s != 'A': continue
    p = t['pnl'] * size
    arr_combo.append(p)
    if t['pnl'] > 0:
        consec_w += 1; consec_l = 0
        if consec_w >= 3 and size < 2.0: size = min(2.0, size*1.3)
        if consec_w == 1 and size < 1.0: size = 1.0
    else:
        consec_l += 1; consec_w = 0
        if consec_l >= 2: size = max(0.5, size*0.7)
s_combo = stats(arr_combo, "5) Baseline + LLM A-only + Streak")

# Per year breakdown of combined
print("\nPer year — combined (LLM A-only + Streak):", flush=True)
years = {}
size = 1.0; consec_l = consec_w = 0
for t, s in zip(trades, scores):
    if s != 'A': continue
    p = t['pnl'] * size
    yr = pd.to_datetime(t['ts']).year
    years.setdefault(yr, []).append(p)
    if t['pnl'] > 0:
        consec_w += 1; consec_l = 0
        if consec_w >= 3 and size < 2.0: size = min(2.0, size*1.3)
        if consec_w == 1 and size < 1.0: size = 1.0
    else:
        consec_l += 1; consec_w = 0
        if consec_l >= 2: size = max(0.5, size*0.7)
for y in sorted(years):
    arr = np.array(years[y])
    if len(arr) == 0: continue
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf = pf_p/pf_l if pf_l else 0
    print(f"  {y}: n={n:>3} | WR {w/n*100 if n else 0:>5.1f}% | Net ${net:>+9.2f} | PF {pf:>5.2f}", flush=True)
