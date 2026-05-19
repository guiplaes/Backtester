"""
Script master — espera descarregues, llança els 3 backtests V4/V5/V6 sobre 10y, envia TG.
"""
import time
import os
import subprocess
import sys
sys.path.insert(0, '.')
from tg_send import send as tg_send

# Files needed for V6 (10 pairs)
V6_FILES = ['eurgbp','eurchf','gbpchf','audcad','usdcad',
            'usdchf','nzdcad','audnzd','gbpnzd','eurnzd']
V5_EXTRAS = ['euraud','eurcad','audusd','nzdusd','gbpaud']

def all_ready(files, suffix='_dk_m5_10y.csv'):
    return all(os.path.exists(f"{p}{suffix}") and os.path.getsize(f"{p}{suffix}") > 100000 for p in files)

# Wait for V6 files
print("Waiting for V6 files...", flush=True)
tg_send("⏳ Esperant descarrega V6 (10 pairs)...")

t_start = time.time()
last_notify = t_start
while not all_ready(V6_FILES):
    elapsed = time.time() - t_start
    if elapsed > 7200:  # 2h timeout
        tg_send("⚠️ Timeout descarrega V6, continuo amb el que hi ha")
        break
    if time.time() - last_notify > 600:  # cada 10 min
        ready_count = sum(1 for p in V6_FILES if os.path.exists(f"{p}_dk_m5_10y.csv"))
        print(f"  Ready {ready_count}/{len(V6_FILES)} V6 pairs ({elapsed/60:.0f}min)")
        last_notify = time.time()
    time.sleep(30)

print("V6 files ready, launching V6 backtest...", flush=True)
tg_send("✅ V6 pairs descarregats. Llançant V6 backtest 10 anys...")

# Launch V6 backtest
v6_proc = subprocess.Popen(['python', 'backtest_V6_10YEARS.py'],
                           stdout=open('v6_10y_results.txt','w'), stderr=subprocess.STDOUT)

# Also launch V4 (uses subset of V6 files)
print("Launching V4 backtest...", flush=True)
tg_send("🔄 Llançant V4 backtest 10 anys (paral·lel)")
v4_proc = subprocess.Popen(['python', 'backtest_V4_10YEARS.py'],
                           stdout=open('v4_10y_results.txt','w'), stderr=subprocess.STDOUT)

# Wait for V5 extras (15 pairs needed)
print("Waiting for V5 extras...", flush=True)
all_v5 = V6_FILES + V5_EXTRAS
t_start = time.time()
while not all_ready(all_v5):
    elapsed = time.time() - t_start
    if elapsed > 7200:
        tg_send("⚠️ Timeout V5 extras. V5 corre amb dades parcials")
        break
    if time.time() - last_notify > 600:
        ready = sum(1 for p in all_v5 if os.path.exists(f"{p}_dk_m5_10y.csv"))
        print(f"  Ready {ready}/{len(all_v5)} for V5 ({elapsed/60:.0f}min)")
        last_notify = time.time()
    time.sleep(30)

print("V5 ready, launching V5 backtest...", flush=True)
tg_send("✅ V5 pairs descarregats. Llançant V5 backtest 10 anys...")
v5_proc = subprocess.Popen(['python', 'backtest_V5_10YEARS.py'],
                           stdout=open('v5_10y_results.txt','w'), stderr=subprocess.STDOUT)

# Wait for all 3
tg_send("⏳ Esperant els 3 backtests (V4, V5, V6) sobre 10 anys...")
print("Waiting V4...")
v4_proc.wait()
print("V4 done")
tg_send("✅ V4 10y completat")

print("Waiting V6...")
v6_proc.wait()
print("V6 done")
tg_send("✅ V6 10y completat")

print("Waiting V5...")
v5_proc.wait()
print("V5 done")
tg_send("✅ V5 10y completat")

# Final summary
tg_send("🏁 <b>TOTS els backtests 10 anys ACABATS</b>%0A%0AResultats a:%0A• v4_10y_results.txt%0A• v5_10y_results.txt%0A• v6_10y_results.txt")
print("ALL DONE")
