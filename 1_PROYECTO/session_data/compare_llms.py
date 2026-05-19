#!/usr/bin/env python3
"""Compare Claude CLI vs DeepSeek V4-Pro on the SAME Indicator + Executor inputs.

Uses the last-known system state (zones, signal, account from 2026-04-24) +
recent bars to construct realistic payloads. Sends to both LLMs in parallel,
saves outputs for human comparison.

Usage:
    python compare_llms.py
"""
import json, time, subprocess, urllib.request, threading
from pathlib import Path
from datetime import datetime, timezone

DATA = Path(__file__).resolve().parent
PROJECT = DATA.parent
COMMON = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files")
PROMPTS = PROJECT / "prompts"
OUT_DIR = DATA / "llm_comparison"
OUT_DIR.mkdir(exist_ok=True)

NODE = "node"
CLAUDE_CLI = r"C:\nodejs\node-v22.14.0-win-x64\node_modules\@anthropic-ai\claude-code\cli.js"
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

# Load API key
DEEPSEEK_KEY = None
with open(PROJECT / ".env", encoding='utf-8') as f:
    for line in f:
        if line.startswith("DEEPSEEK_API_KEY="):
            DEEPSEEK_KEY = line.split("=",1)[1].strip().strip('"').strip("'")

# ── Load data ──
def load_json(p):
    with open(p, encoding='utf-8') as f: return json.load(f)

xau_h1 = load_json(DATA / "xauusd_h1.json")["bars"]
xau_m15 = load_json(DATA / "xauusd_m15.json")["bars"]
dxy_h1 = load_json(DATA / "dxy_h1.json")["bars"]
zones_data = load_json(COMMON / "brain_zones.legacy.json")
positions = load_json(COMMON / "brain_positions.json")
signal_state = load_json(COMMON / "brain_signal_state.json")

# Last bar = current price
last_h1 = xau_h1[-1]
current_price = last_h1[4]  # close
current_ts = last_h1[0]
now = datetime.fromtimestamp(current_ts, tz=timezone.utc)
hour_utc = now.hour

# Session
def session_of_h(h):
    if 0 <= h < 7: return "ASIA"
    if 7 <= h < 13: return "LONDON"
    if 13 <= h < 16: return "OVERLAP"
    if 16 <= h < 21: return "NY"
    return "DEAD"

session_name = session_of_h(hour_utc)

# DXY current
dxy_last = dxy_h1[-1]
dxy_close = dxy_last[4]
dxy_5h_change = (dxy_close - dxy_h1[-6][4]) if len(dxy_h1) >= 6 else 0
dxy_trend = "UP" if dxy_5h_change > 0.05 else ("DOWN" if dxy_5h_change < -0.05 else "FLAT")

# Recent M15 (last 30)
m15_recent = xau_m15[-30:]
# Recent H1 (last 24)
h1_recent = xau_h1[-24:]

# Compute simple ATR M15 (14)
def atr14(bars):
    trs = []
    for i in range(1, min(15, len(bars))):
        h, l, prev_c = bars[i][2], bars[i][3], bars[i-1][4]
        tr = max(h-l, abs(h-prev_c), abs(l-prev_c))
        trs.append(tr)
    return sum(trs)/len(trs) if trs else 0

atr_m15 = atr14(xau_m15)

# Simple RSI(14)
def rsi14(closes):
    if len(closes) < 15: return 50
    gains = []; losses = []
    for i in range(1, 15):
        d = closes[i] - closes[i-1]
        gains.append(max(0, d)); losses.append(max(0, -d))
    avg_g = sum(gains)/14; avg_l = sum(losses)/14
    if avg_l == 0: return 100
    rs = avg_g / avg_l
    return 100 - 100/(1+rs)

rsi_m15 = rsi14([b[4] for b in xau_m15[-15:]])

# ── Build INDICATOR payload ──
def fmt_bar(b, prefix=""):
    return f"{datetime.fromtimestamp(b[0], tz=timezone.utc).strftime('%H:%M')} O={b[1]:.2f} H={b[2]:.2f} L={b[3]:.2f} C={b[4]:.2f}"

ind_payload = f"""=== CONTEXT ACTUAL ===
Símbol: XAUUSD
Preu actual: {current_price:.2f}
Timestamp: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}
Sessió: {session_name} (hora UTC: {hour_utc:02d})
ATR(14) M15: ${atr_m15:.2f}
RSI(14) M15: {rsi_m15:.1f}

=== CONTEXT EXTERN ===
DXY: {dxy_close:.3f} (canvi 5h: {dxy_5h_change:+.3f}, trend H1: {dxy_trend})

=== ÚLTIMS 24 H1 BARS ===
{chr(10).join(fmt_bar(b) for b in h1_recent)}

=== ÚLTIMS 30 M15 BARS ===
{chr(10).join(fmt_bar(b) for b in m15_recent)}

=== ZONES PRÈVIES (de la última anàlisi Indicator 2026-04-24 18:31) ===
{json.dumps(zones_data.get('reversal_zones', []), indent=2, ensure_ascii=False)}

=== TASCA ===
Identifica les zones de reversió actuals (3-7 zones òptimes) per al sistema
de scalping XAUUSD. Indica règim, bias, i zones amb strength + bounce_direction.
"""

# ── Build EXECUTOR (IDLE mode) payload ──
exec_payload = json.dumps({
    "mode": "idle",  # No signal active → staging mode
    "signal": None,
    "account": positions["account"],
    "market_context": {
        "timestamp_utc": now.strftime('%H:%M:%S'),
        "price": current_price,
        "session": {"name": session_name, "hour_utc": hour_utc},
        "atr_m15": round(atr_m15, 2),
        "rsi_m15": round(rsi_m15, 1),
    },
    "external": {
        "dxy": {"price": dxy_close, "trend_h1": dxy_trend, "change_5h": dxy_5h_change},
    },
    "zones": zones_data.get('reversal_zones', []),
    "indicator_bias": zones_data.get('bias', 'NEUTRAL'),
    "previous_state": None,
    "trigger_events": [{"type": "DASHBOARD_FORCE", "details": {}}],
    "last_30_m5_candles": [
        {"t": datetime.fromtimestamp(b[0], tz=timezone.utc).strftime('%H:%M'),
         "o": b[1], "h": b[2], "l": b[3], "c": b[4]}
        for b in m15_recent[-15:]  # using M15 as M5 proxy
    ],
}, indent=2, ensure_ascii=False)

# Load prompts
indicator_prompt = (PROMPTS / "indicator.txt").read_text(encoding='utf-8')
executor_prompt = (PROMPTS / "executor.txt").read_text(encoding='utf-8')

# ── Caller functions ──
def call_claude(system_prompt, user_payload, label, model="sonnet"):
    # Combine system + user as single stdin payload to avoid WinError 206
    # (system_prompt as CLI arg can be too long on Windows for executor.txt)
    combined = f"<<SYSTEM PROMPT>>\n{system_prompt}\n<<END SYSTEM>>\n<<USER PAYLOAD>>\n{user_payload}"
    cmd = [NODE, CLAUDE_CLI, "-p",
           "--output-format", "text",
           "--max-turns", "1",
           "--model", model,
           "--tools", ""]
    print(f"[{label}/Claude-{model}] Starting (combined {len(combined)} chars via stdin)...")
    t0 = time.time()
    try:
        r = subprocess.run(cmd, input=combined,
                           capture_output=True, timeout=300,
                           encoding='utf-8', errors='replace')
        elapsed = time.time() - t0
        return {
            "provider": f"claude-{model}",
            "elapsed": elapsed,
            "rc": r.returncode,
            "stdout": r.stdout or "",
            "stderr": r.stderr or "",
        }
    except subprocess.TimeoutExpired:
        return {"provider": f"claude-{model}", "elapsed": time.time()-t0, "rc": -1,
                "stdout": "", "stderr": "TIMEOUT 300s"}
    except Exception as e:
        return {"provider": f"claude-{model}", "elapsed": time.time()-t0, "rc": -1,
                "stdout": "", "stderr": str(e)}

def call_deepseek(system_prompt, user_payload, label):
    body = json.dumps({
        "model": "deepseek-v4-pro",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_payload},
        ],
        "max_tokens": 16000,
    }).encode('utf-8')
    req = urllib.request.Request(DEEPSEEK_URL, data=body, headers={
        'Authorization': f'Bearer {DEEPSEEK_KEY}',
        'Content-Type': 'application/json',
    })
    print(f"[{label}/DeepSeek-V4-Pro] Starting (prompt {len(user_payload)} chars)...")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            d = json.loads(resp.read().decode('utf-8'))
        elapsed = time.time() - t0
        msg = d['choices'][0]['message']
        usage = d.get('usage', {})
        return {
            "provider": "deepseek-v4-pro",
            "elapsed": elapsed,
            "rc": 0,
            "stdout": msg.get('content', ''),
            "reasoning_content": msg.get('reasoning_content', ''),
            "tokens": {
                "in": usage.get('prompt_tokens', 0),
                "out": usage.get('completion_tokens', 0),
                "reasoning": usage.get('completion_tokens_details', {}).get('reasoning_tokens', 0),
                "cache_hit": usage.get('prompt_cache_hit_tokens', 0),
            },
        }
    except urllib.error.HTTPError as e:
        return {"provider": "deepseek-v4-pro", "elapsed": time.time()-t0, "rc": e.code,
                "stdout": "", "stderr": e.read().decode('utf-8', errors='replace')[:500]}
    except Exception as e:
        return {"provider": "deepseek-v4-pro", "elapsed": time.time()-t0, "rc": -1,
                "stdout": "", "stderr": str(e)}

# ── Run all 4 calls in parallel ──
results = {}
def run(key, fn, *args):
    results[key] = fn(*args)

threads = [
    threading.Thread(target=run, args=("IND_claude", call_claude, indicator_prompt, ind_payload, "IND")),
    threading.Thread(target=run, args=("IND_ds", call_deepseek, indicator_prompt, ind_payload, "IND")),
    threading.Thread(target=run, args=("EXE_claude", call_claude, executor_prompt, exec_payload, "EXE")),
    threading.Thread(target=run, args=("EXE_ds", call_deepseek, executor_prompt, exec_payload, "EXE")),
]
print(f"\nLaunching 4 LLM calls in parallel at {datetime.now().strftime('%H:%M:%S')}...\n")
for t in threads: t.start()
for t in threads: t.join()
print(f"\nAll 4 calls complete at {datetime.now().strftime('%H:%M:%S')}.\n")

# ── Save raw + summary ──
ts_tag = datetime.now().strftime('%Y%m%d_%H%M%S')
raw_path = OUT_DIR / f"compare_{ts_tag}_raw.json"
raw_path.write_text(json.dumps({
    "context": {
        "current_price": current_price,
        "session": session_name,
        "hour_utc": hour_utc,
        "dxy": dxy_close,
        "atr_m15": atr_m15,
        "rsi_m15": rsi_m15,
        "n_zones": len(zones_data.get('reversal_zones', [])),
    },
    "results": results,
}, indent=2, ensure_ascii=False), encoding='utf-8')
print(f"Raw saved: {raw_path}\n")

# ── Build comparison markdown ──
md = []
md.append(f"# LLM comparison — Indicator + Executor\n")
md.append(f"_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_\n")
md.append(f"**Snapshot:** XAU @ ${current_price:.2f}, sessió {session_name} ({hour_utc:02d} UTC), DXY {dxy_close:.2f}, ATR M15 ${atr_m15:.1f}, RSI {rsi_m15:.0f}, {len(zones_data.get('reversal_zones', []))} zones prèvies\n")

for stage in ("IND", "EXE"):
    title = "INDICATOR" if stage == "IND" else "EXECUTOR (IDLE mode)"
    md.append(f"\n## {title}\n")
    for provider in ("claude", "ds"):
        r = results[f"{stage}_{provider}"]
        prov_name = r['provider']
        md.append(f"### {prov_name} ({r['elapsed']:.1f}s, rc={r['rc']})\n")
        if r.get('tokens'):
            t = r['tokens']
            md.append(f"_tokens: in={t['in']} · out={t['out']} · reasoning={t['reasoning']} · cache_hit={t['cache_hit']}_\n")
        if r['rc'] == 0 and r['stdout']:
            md.append("```")
            md.append(r['stdout'][:6000])
            md.append("```")
        else:
            md.append(f"❌ Error: {r.get('stderr', 'no output')[:500]}\n")
        # For DeepSeek also include a reasoning sample
        if r.get('reasoning_content') and len(r['reasoning_content']) > 0:
            md.append(f"<details><summary>Reasoning ({len(r['reasoning_content'])} chars)</summary>\n\n```\n{r['reasoning_content'][:3000]}\n```\n</details>\n")

md_path = OUT_DIR / f"compare_{ts_tag}.md"
md_path.write_text("\n".join(md), encoding='utf-8')
print(f"Markdown saved: {md_path}")
print(f"\nQuick summary:")
for k, r in results.items():
    print(f"  {k}: {r['provider']} {r['elapsed']:.1f}s rc={r['rc']} stdout={len(r.get('stdout',''))} chars")
