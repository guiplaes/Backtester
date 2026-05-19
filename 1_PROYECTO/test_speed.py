"""Test latencies of DeepSeek models."""
import os, json, time
from urllib.request import Request, urlopen

api_key = None
with open(r"C:\Users\Administrator\Desktop\MT4 Claude\1_PROYECTO\.env") as f:
    for line in f:
        if "DEEPSEEK_API_KEY=" in line:
            api_key = line.split("=", 1)[1].strip()
            break

print(f"API key: {api_key[:10]}..." if api_key else "NO KEY")

system_prompt = """Ets el guardian d'un trade XAUUSD obert. Decideix HOLD/EXIT_NOW.
Respon JSON: {"action": "HOLD"|"EXIT_NOW", "reason": "1 frase", "confidence": 0.0-1.0}"""

user_prompt = """+30s elapsed. Preu: $4736.96 (-$0.04 entry, float +$0.25)
M1: O=4737.00 H=4737.20 L=4736.50 C=4736.96 V=145
delta_acc: +120, CMF spot +0.05, futures +0.10
SL: $4733 BE: NO. Decisió?"""

models = ["deepseek-chat", "deepseek-v4-flash", "deepseek-v4-pro"]

for model in models:
    print(f"\n{'='*50}\nTESTING: {model}\n{'='*50}")
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 500,
    }
    req = Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    try:
        with urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
        elapsed = time.time() - t0
        usage = data.get("usage", {})
        choice = data["choices"][0]
        content = choice["message"]["content"]
        reasoning_tokens = usage.get('completion_tokens_details', {}).get('reasoning_tokens', 0)
        print(f"  Latency: {elapsed:.2f}s")
        print(f"  Tokens in={usage.get('prompt_tokens',0)} out={usage.get('completion_tokens',0)} reasoning={reasoning_tokens}")
        print(f"  Response: {content[:180]}")
    except Exception as e:
        print(f"  ERROR: {e}")
