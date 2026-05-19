"""Test del TRADE MONITOR — simula 5 turns amb dades fictícies per veure
latència i qualitat de resposta de Haiku."""
import time
import sys
import os

# Setup logging perquè vegem els logs
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("brain")

# Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Importa només el necessari sense iniciar el brain sencer
from trade_monitor import TradeMonitor, get_prompt


# Mock sig_state, account, bars, flow, approach
class MockSigState:
    def __init__(self):
        self._data = {
            'direction': 'BUY',
            'entry_price': 4735.10,
            'opened_at': time.time() - 10,  # fa 10s
            'breakeven_set': False,
            'executor_plan': {
                'tp_target': 4743.0,
                'wick_dynamic_sl': 4731.5,
                'breakeven_trigger': {'price': 4739.0},
                'profit_targets': [
                    {'price': 4739.0, 'close_pct': 50},
                    {'price': 4743.0, 'close_pct': 50},
                ],
                'auto_close_conditions': [
                    {'action': 'FULL_CLOSE', 'level': 4729.0, 'kind': 'bar_close', 'tf': 'M5'},
                ],
            },
        }

    def is_active(self):
        return True

    def save(self):
        pass


def make_bars(price, vol, count=5):
    """Genera bars M1 fictícies acabant a `price`."""
    bars = []
    for i in range(count):
        p = price - (count - i - 1) * 0.3
        bars.append({
            'time': time.time() - (count - i) * 60,
            'open': p - 0.2,
            'high': p + 0.4,
            'low': p - 0.5,
            'close': p,
            'volume': vol,
        })
    return bars


def make_flow(spot_cmf=0.08, fut_cmf=0.12, spot_obv_4h=12000, fut_obv_4h=4500):
    return {
        'spot': {
            'cmf_m15': {'value': spot_cmf},
            'obv_h1': {'change_4h': spot_obv_4h},
        },
        'futures': {
            'cmf_m15': {'value': fut_cmf},
            'obv_h1': {'change_4h': fut_obv_4h},
        },
    }


def make_approach(delta=180, strength=0.5):
    return {'delta_acc': delta, 'signal_strength': strength}


# Mock _call_claude funció — UTILITZAREM la real del trader_brain
def get_real_call_claude():
    """Importa la funció _call_claude real. NO inicialitzem el brain sencer."""
    # Hack: carreguem només el mòdul però no executem el main
    import importlib.util
    spec = importlib.util.spec_from_file_location("trader_brain", os.path.join(os.path.dirname(__file__), "trader_brain.py"))
    if spec and spec.loader:
        # No fem exec_module — és massa pesat. En lloc seu, importem el sub-mòdul
        pass
    # Alternativa: cridem directament via subprocess (com ho fa _call_claude per dins)
    return _direct_call_claude


import subprocess
import json


def _direct_call_claude(prompt_data, system_prompt, label="MONITOR", model="haiku", effort=None, session_role=None):
    """Crida directa al Claude CLI amb mateixa estructura que _call_claude del brain."""
    NODE = "node"
    CLI_PATH = r"C:\nodejs\node-v22.14.0-win-x64\node_modules\@anthropic-ai\claude-code\cli.js"
    if not os.path.exists(CLI_PATH):
        print(f"[ERROR] CLI_PATH no existeix: {CLI_PATH}")
        return None

    cmd = [
        NODE, CLI_PATH,
        "-p",
        "--output-format", "json",
        "--max-turns", "1",
        "--model", model,
        "--tools", "",
        "--system-prompt", system_prompt,
    ]

    t0 = time.time()
    try:
        result = subprocess.run(
            cmd, input=prompt_data, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=60,
        )
    except Exception as e:
        print(f"[ERROR] CLI call failed: {e}")
        return None
    elapsed = time.time() - t0

    print(f"\n[CLI] elapsed={elapsed:.2f}s rc={result.returncode}")
    if result.returncode != 0:
        print(f"[CLI] stderr (full): {result.stderr}")
        print(f"[CLI] stdout (first 800): {result.stdout[:800]}")
        return None

    try:
        wrap = json.loads(result.stdout)
        # El CLI retorna {"type":"result", "result": "STRING_JSON"} o similar
        if isinstance(wrap, dict) and 'result' in wrap:
            inner = wrap['result']
            if isinstance(inner, str):
                # Strip markdown si n'hi ha
                inner = inner.strip()
                if inner.startswith('```'):
                    lines = inner.split('\n')
                    inner = '\n'.join(lines[1:-1] if lines[-1].startswith('```') else lines[1:])
                try:
                    return json.loads(inner)
                except Exception:
                    print(f"[PARSE] Inner not JSON: {inner[:200]}")
                    return {'raw': inner}
            return inner
        return wrap
    except Exception as e:
        print(f"[PARSE] Failed: {e}")
        print(f"[STDOUT] {result.stdout[:500]}")
        return None


# ─── SIMULACIÓ ───
print("=" * 70)
print("TRADE MONITOR SIMULATION")
print("=" * 70)

monitor = TradeMonitor()
monitor.observator_mode = True
prompt = get_prompt()
print(f"\n[INIT] Prompt loaded: {len(prompt)} chars")

sig = MockSigState()
account = {'positions': [{'ticket': 12345, 'volume': 0.25}]}

# Turn 1: trade just obert
print("\n" + "─" * 70)
print("TURN 1 — Trade obert ARA, primera crida (initial payload)")
print("─" * 70)
bars = make_bars(price=4735.30, vol=412)
flow = make_flow()
approach = make_approach(delta=180, strength=0.5)

payload1 = monitor.build_initial_payload(sig, account, bars, flow, approach)
print(f"\n[PAYLOAD turn 1] {len(payload1)} chars:\n{payload1}")

t_total = time.time()
response = _direct_call_claude(payload1, prompt, model='haiku', session_role='TRADE_MONITOR')
print(f"\n[RESPONSE turn 1]: {response}")

# Turn 2: 5s després, preu lleugerament amunt
print("\n" + "─" * 70)
print("TURN 2 — +5s elapsed, delta payload")
print("─" * 70)
sig._data['opened_at'] = time.time() - 15
bars = make_bars(price=4736.5, vol=234)
approach = make_approach(delta=210, strength=0.55)
monitor.turn_count = 1
monitor.last_action = 'HOLD'

payload2 = monitor.build_delta_payload(sig, account, bars, flow, approach)
print(f"\n[PAYLOAD turn 2] {len(payload2)} chars:\n{payload2}")

response = _direct_call_claude(payload2, prompt, model='haiku', session_role='TRADE_MONITOR')
print(f"\n[RESPONSE turn 2]: {response}")

# Turn 3: spike contrari
print("\n" + "─" * 70)
print("TURN 3 — +25s, spike contrari amb vol alt")
print("─" * 70)
sig._data['opened_at'] = time.time() - 25
bars = make_bars(price=4732.8, vol=1245)  # baixada amb vol 3x
approach = make_approach(delta=-450, strength=-0.8)
monitor.turn_count = 2
monitor.last_action = 'HOLD'

payload3 = monitor.build_delta_payload(sig, account, bars, flow, approach)
print(f"\n[PAYLOAD turn 3] {len(payload3)} chars:\n{payload3}")

response = _direct_call_claude(payload3, prompt, model='haiku', session_role='TRADE_MONITOR')
print(f"\n[RESPONSE turn 3]: {response}")

# Turn 4: recovery
print("\n" + "─" * 70)
print("TURN 4 — +35s, recovery però vol decreixent")
print("─" * 70)
sig._data['opened_at'] = time.time() - 35
bars = make_bars(price=4737.2, vol=180)  # recovery amb vol baixa
approach = make_approach(delta=50, strength=0.1)
monitor.turn_count = 3
monitor.last_action = 'HOLD'  # o whatever turn 3 returned

payload4 = monitor.build_delta_payload(sig, account, bars, flow, approach)
print(f"\n[PAYLOAD turn 4] {len(payload4)} chars:\n{payload4}")

response = _direct_call_claude(payload4, prompt, model='haiku', session_role='TRADE_MONITOR')
print(f"\n[RESPONSE turn 4]: {response}")

print("\n" + "=" * 70)
print(f"TOTAL TIME: {time.time() - t_total:.1f}s for 4 turns")
print("=" * 70)
