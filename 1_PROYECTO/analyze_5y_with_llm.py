"""
Fast: load trades CSV + 5y data, generate context for each trade,
send to LLM, evaluate.
"""
import pandas as pd
import numpy as np
import json, time
from urllib.request import Request, urlopen

api_key = None
with open(r"C:\Users\Administrator\Desktop\MT4 Claude\1_PROYECTO\.env") as f:
    for line in f:
        if "DEEPSEEK_API_KEY=" in line:
            api_key = line.split("=", 1)[1].strip()

# Load 5y data
print("Loading 5y...", flush=True)
df = pd.read_csv("xauusd_m5_5y.csv", index_col=0, parse_dates=True)
df.index = pd.to_datetime(df.index, utc=True)
df.columns = [c.lower() for c in df.columns]
if 'tick_volume' not in df.columns: df['tick_volume'] = df['volume']
df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
hl = df['high']-df['low']; hc = (df['high']-df['close'].shift()).abs(); lc = (df['low']-df['close'].shift()).abs()
tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
df['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
df['vol_avg'] = df['tick_volume'].rolling(20).mean()
print(f"{len(df)} bars", flush=True)

# Load trades
trades = pd.read_csv("backtest_trades_5y.csv", parse_dates=['ts'])
trades['ts'] = pd.to_datetime(trades['ts'], utc=True)
print(f"{len(trades)} trades", flush=True)

PROMPT = """Avalua qualitat setup XAUUSD M5 Inside Bar BO LONG en Asia.
A=alt potencial · B=mig · C=feble.
JSON: {"score":"A"|"B"|"C","reason":""}"""

def score_llm(ctx):
    body = {"model":"deepseek-chat",
            "messages":[{"role":"system","content":PROMPT},{"role":"user","content":ctx}],
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
    except: return "B"

# Build context for each trade by looking up bar at trade ts
print("Building contexts + LLM scoring...", flush=True)
scores = []
for i, row in trades.iterrows():
    if i % 30 == 0: print(f"  {i}/{len(trades)}", flush=True)
    ts = row['ts']
    # Find bar at or just before this timestamp
    idx = df.index.searchsorted(ts, side='right') - 1
    if idx < 50:
        scores.append('B'); continue
    bar = df.iloc[idx]
    pp = df.iloc[idx-2] if idx >= 2 else bar
    ema50_dist = (bar['close']-bar['ema50'])/bar['close']*100
    vol_ratio = bar['tick_volume']/bar['vol_avg'] if bar['vol_avg'] else 0
    breakout = bar['close']-pp['high']

    ctx = (f"Time: {ts}\nPrice: ${bar['close']:.2f}\n"
           f"EMA50 dist: {ema50_dist:+.2f}%\nATR: ${bar['atr']:.2f}\n"
           f"Vol ratio: {vol_ratio:.2f}×\nBreakout size: ${breakout:.2f}")

    s = score_llm(ctx)
    scores.append(s)
    time.sleep(0.05)

# Save scores
with open('5y_scores.json','w') as f: json.dump(scores, f)
trades['llm_score'] = scores
trades.to_csv('5y_trades_with_scores.csv', index=False)

def stats(arr, name):
    if len(arr)==0: print(f"{name:>40}: 0"); return
    arr = np.array(arr)
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    eq=np.cumsum(arr); peak=np.maximum.accumulate(eq); dd=(peak-eq).max()
    print(f"{name:>40}: n={n:>4} | WR {w/n*100:>5.1f}% | Net ${net:>+9.2f} | PF {pf:>5.2f} | DD ${dd:>7.2f}", flush=True)

print()
print("="*100)
print("5-YEAR RESULTS WITH IMPROVEMENTS:")
print("="*100)
print(f"Score distribution: A={scores.count('A')}, B={scores.count('B')}, C={scores.count('C')}", flush=True)

stats(trades['pnl'].values, "1) Baseline")
stats(trades[trades['llm_score']=='A']['pnl'].values, "2) LLM A only")
stats(trades[trades['llm_score'].isin(['A','B'])]['pnl'].values, "3) LLM A+B")
stats(trades[trades['llm_score']!='C']['pnl'].values, "4) Skip C only")

# Streak sizing on baseline
def streak_apply(pnls):
    out = []
    size = 1.0; consec_l = consec_w = 0
    for pnl in pnls:
        out.append(pnl * size)
        if pnl > 0:
            consec_w += 1; consec_l = 0
            if consec_w >= 3 and size < 2.0: size = min(2.0, size*1.3)
            if consec_w == 1 and size < 1.0: size = 1.0
        else:
            consec_l += 1; consec_w = 0
            if consec_l >= 2: size = max(0.5, size*0.7)
    return out

stats(streak_apply(trades['pnl'].values), "5) Baseline + Streak sizing")
stats(streak_apply(trades[trades['llm_score']=='A']['pnl'].values), "6) LLM A + Streak sizing")

# Per-year breakdown for LLM A only
print("\nPer-year (LLM A only):", flush=True)
a_trades = trades[trades['llm_score']=='A']
for yr in sorted(a_trades['year'].unique()):
    ydf = a_trades[a_trades['year']==yr]
    arr = ydf['pnl'].values
    if len(arr) == 0: continue
    n=len(arr); w=(arr>0).sum(); net=arr.sum()
    pf_p=arr[arr>0].sum(); pf_l=abs(arr[arr<=0].sum())
    pf=pf_p/pf_l if pf_l else 0
    print(f"  {yr}: n={n:>3} | WR {w/n*100 if n else 0:>5.1f}% | Net ${net:>+9.2f} | PF {pf:>5.2f}", flush=True)
