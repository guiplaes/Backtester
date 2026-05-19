"""
TEST 4: LLM Quality Filter
For each historical winning-config Inside Bar setup, send a market snapshot to
DeepSeek-chat and ask: "Quality A/B/C? A=high quality, B=medium, C=skip"
Then test if filtering to A only or A+B improves PF.

Cost: ~83 trades × $0.002 = ~$0.20
"""
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timezone
import json, time
from urllib.request import Request, urlopen

# Load API key
api_key = None
with open(r"C:\Users\Administrator\Desktop\MT4 Claude\1_PROYECTO\.env") as f:
    for line in f:
        if "DEEPSEEK_API_KEY=" in line:
            api_key = line.split("=", 1)[1].strip()
            break

if not api_key:
    print("NO DeepSeek key. FAIL.")
    exit(1)

SYMBOL = "XAUUSD.crp"
ATR_LEN = 14; EMA_LEN = 50; VOL_LEN = 20
COMMISSION = 0.5; SPREAD = 0.50; SLIPPAGE = 0.20
SL_M = 1.5; TP1_M = 15; TP2_M = 30

def fetch():
    mt5.initialize(); mt5.symbol_select(SYMBOL, True)
    end = datetime.now(timezone.utc)
    rates = mt5.copy_rates_from(SYMBOL, mt5.TIMEFRAME_M5, end, 50000)
    if len(rates) >= 50000:
        oldest = datetime.fromtimestamp(int(rates[0]['time']), tz=timezone.utc)
        rates2 = mt5.copy_rates_from(SYMBOL, mt5.TIMEFRAME_M5, oldest, 50000)
        if rates2 is not None:
            rates = np.concatenate([rates2, rates])
            _, idx = np.unique(rates['time'], return_index=True); rates = rates[np.sort(idx)]
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True); df = df.set_index('time')
    mt5.shutdown(); return df

def compute(df):
    df = df.copy()
    df['ema50'] = df['close'].ewm(span=EMA_LEN, adjust=False).mean()
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    hl = df['high']-df['low']; hc = (df['high']-df['close'].shift()).abs(); lc = (df['low']-df['close'].shift()).abs()
    tr = pd.concat([hl,hc,lc],axis=1).max(axis=1)
    df['atr'] = tr.ewm(alpha=1/ATR_LEN, adjust=False).mean()
    df['vol_avg'] = df['tick_volume'].rolling(VOL_LEN).mean()
    df['inside'] = (df['high']<df['high'].shift(1)) & (df['low']>df['low'].shift(1))
    return df

def backtest_collect(df):
    """Collect each trade with FULL context for later LLM analysis."""
    trades = []; pos = None
    for i in range(EMA_LEN+5, len(df)):
        bar = df.iloc[i]; ts = df.index[i]
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
                trades.append({'ts': pos['ts'], 'pnl': tp, 'context': pos['context'], 'idx': pos['idx']})
                pos = None
        if pos is None and i>=2:
            prev = df.iloc[i-1]; pp = df.iloc[i-2]
            if not prev['inside']: continue
            if pd.isna(bar['ema50']) or pd.isna(bar['atr']) or pd.isna(bar['vol_avg']): continue
            if bar['tick_volume'] <= bar['vol_avg']*1.3: continue
            if ts.dayofweek == 2: continue
            if not (0 <= ts.hour <= 6): continue
            if bar['close']<=bar['ema50']: continue
            mh = pp['high']
            if not (bar['high']>mh and bar['close']>mh): continue
            atr = bar['atr']; e = bar['close']

            # Build context (last 20 bars, no future leak)
            ctx_bars = df.iloc[max(0,i-20):i+1][['open','high','low','close','tick_volume']].copy()
            ctx_str = ""
            for j, (t, row) in enumerate(ctx_bars.iterrows()):
                tag = " <-- BREAKOUT" if j == len(ctx_bars)-1 else (" <-- INSIDE" if j == len(ctx_bars)-2 else (" <-- MOTHER" if j == len(ctx_bars)-3 else ""))
                ctx_str += f"  {t.strftime('%H:%M')} O={row['open']:.2f} H={row['high']:.2f} L={row['low']:.2f} C={row['close']:.2f} V={int(row['tick_volume'])}{tag}\n"

            context = {
                'time_utc': str(ts),
                'hour': ts.hour, 'dow': ts.day_name(),
                'price': float(bar['close']),
                'ema50': float(bar['ema50']),
                'ema20': float(bar['ema20']) if not pd.isna(bar['ema20']) else None,
                'atr': float(bar['atr']),
                'vol_now': int(bar['tick_volume']),
                'vol_avg': float(bar['vol_avg']),
                'vol_ratio': float(bar['tick_volume'] / bar['vol_avg']) if bar['vol_avg'] else 0,
                'ema50_dist_pct': (float(bar['close']) - float(bar['ema50'])) / float(bar['close']) * 100,
                'mother_high': float(pp['high']),
                'mother_low': float(pp['low']),
                'breakout_size': (float(bar['close']) - float(pp['high'])),
                'recent_bars': ctx_str,
            }
            pos = {'e':e,'ts':ts,'sl':e-atr*SL_M,'tp1':e+atr*TP1_M,'tp2':e+atr*TP2_M,'q1':0.5,'q2':0.5,
                   'context': context, 'idx': len(trades)}
    return trades

PROMPT_SYSTEM = """Ets un analista quantitatiu expert en XAUUSD.
Et passem un setup detectat: Inside Bar Breakout LONG en sessió Asiàtica.
La regla mecànica diu d'entrar. La teva feina: avaluar QUALITAT del setup
basant-te en context donat. Resposta: A (alta) | B (mitjana) | C (rebutjar).

Criteris:
- A: clarament alcista, EMA50 ascendent fort, volum institucional clar (>1.8×), preu lluny EMA50, breakout decidit
- B: alcista però amb dubtes, volum mitjà, breakout marginal
- C: feble, volum baix, EMA50 plana, breakout poc convincent, preu massa lluny EMA50 (extensió tardana)

Respon NOMÉS JSON: {"score":"A"|"B"|"C","reason":"breu"}"""

def score_with_llm(ctx):
    user = f"""Setup XAUUSD M5 Inside Bar BO LONG (Asia 00-06 UTC):
- Hora: {ctx['time_utc']} ({ctx['dow']})
- Preu entry: ${ctx['price']:.2f}
- EMA50 M5: ${ctx['ema50']:.2f} (preu sobre EMA50: {ctx['ema50_dist_pct']:+.2f}%)
- ATR(14): ${ctx['atr']:.2f}
- Volum bar breakout: {ctx['vol_now']} (avg={ctx['vol_avg']:.0f}, ratio={ctx['vol_ratio']:.2f}×)
- Mother bar high (nivell trencat): ${ctx['mother_high']:.2f}
- Mother bar low: ${ctx['mother_low']:.2f}
- Breakout size: ${ctx['breakout_size']:.2f}

Últimes 20 barres M5:
{ctx['recent_bars']}

Avaluació qualitat A/B/C?"""

    body = {
        "model": "deepseek-chat",
        "messages": [{"role":"system","content":PROMPT_SYSTEM},{"role":"user","content":user}],
        "response_format": {"type":"json_object"},
        "max_tokens": 200,
        "temperature": 0.3,
    }
    req = Request("https://api.deepseek.com/chat/completions",
                  data=json.dumps(body).encode(),
                  headers={"Authorization":f"Bearer {api_key}", "Content-Type":"application/json"},
                  method="POST")
    try:
        with urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        content = data["choices"][0]["message"]["content"]
        result = json.loads(content)
        return result.get("score","B").upper()[:1], result.get("reason","")
    except Exception as e:
        return "B", f"err:{str(e)[:50]}"

print("Fetching..."); df = fetch(); df = compute(df); print(f"{len(df)} bars")
print("Backtesting + collecting context...")
trades = backtest_collect(df)
print(f"Got {len(trades)} trades. Running LLM filter on each...\n")

scores = {}
for i, t in enumerate(trades):
    if i % 10 == 0: print(f"  Trade {i+1}/{len(trades)}...")
    score, reason = score_with_llm(t['context'])
    scores[t['idx']] = (score, reason)
    time.sleep(0.1)  # gentle rate limit

# Apply filters
def stats(subset, name):
    if not subset: print(f"{name}: NO trades"); return None
    arr = np.array([t['pnl'] for t in subset])
    n = len(arr); w = (arr>0).sum(); net = arr.sum()
    pf_p = arr[arr>0].sum(); pf_l = abs(arr[arr<=0].sum())
    pf = pf_p/pf_l if pf_l else 0
    eq = np.cumsum(arr); peak = np.maximum.accumulate(eq); dd = (peak-eq).max()
    print(f"{name:>30}: n={n} | WR {w/n*100:>5.1f}% | Net ${net:>+8.2f} | PF {pf:>5.2f} | DD ${dd:>6.2f}")
    return {'n':n, 'pf':pf, 'net':net}

# Save scores
import csv
with open('llm_scores.csv','w',newline='',encoding='utf-8') as f:
    wr = csv.writer(f)
    wr.writerow(['idx','ts','pnl','score','reason'])
    for t in trades:
        s, r = scores[t['idx']]
        wr.writerow([t['idx'], t['ts'], t['pnl'], s, r])

print(f"\n{'='*100}")
s_all = stats(trades, "All (baseline)")
s_a = stats([t for t in trades if scores[t['idx']][0]=='A'], "Only A grade")
s_ab = stats([t for t in trades if scores[t['idx']][0] in ('A','B')], "A+B grade")
s_b = stats([t for t in trades if scores[t['idx']][0]=='B'], "Only B grade")
s_c = stats([t for t in trades if scores[t['idx']][0]=='C'], "Only C grade")

# Distribution
from collections import Counter
sc = Counter([scores[t['idx']][0] for t in trades])
print(f"\nScore distribution: {dict(sc)}")

# Verdict
delta = (s_a['pf'] if s_a else 0) - (s_all['pf'] if s_all else 0)
delta_ab = (s_ab['pf'] if s_ab else 0) - (s_all['pf'] if s_all else 0)
print(f"\nDelta PF (A only):  {delta:+.2f}")
print(f"Delta PF (A+B):     {delta_ab:+.2f}")
if max(delta, delta_ab) >= 0.40:
    print(">>> LLM FILTER PASSES")
elif max(delta, delta_ab) >= 0.20:
    print(">>> LLM FILTER PARTIAL (improvement but below target)")
else:
    print(">>> LLM FILTER FAILS")
