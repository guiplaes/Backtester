"""Parallel batch runner: distributes tests across multiple MT5_Tester slots."""
import csv
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

TESTS_DIR = Path(r"C:\Users\Administrator\Desktop\MT4 Claude\1_PROYECTO\backtest_dualgrid_v2\tests")
RESULTS_CSV = Path(r"C:\Users\Administrator\Desktop\MT4 Claude\1_PROYECTO\backtest_dualgrid_v2\results.csv")

SLOTS = [
    Path(r"C:\MT5_Tester"),
    Path(r"C:\MT5_Tester2"),
    Path(r"C:\MT5_Tester3"),
]

CREDENTIALS = """[Common]
Login=1110830
Password=lN5V7&QK
Server=VTMarkets-Demo

"""

import threading
csv_lock = threading.Lock()


def patch_ini(src_path: Path, dst_path: Path):
    content = src_path.read_text(encoding='utf-8')
    content = content.replace('Currency=USC', 'Currency=USD')
    content = content.replace('Symbol=XAUUSD-VIPc\n', 'Symbol=XAUUSD-VIP\n')
    test_id = src_path.stem
    content = re.sub(r'Report=report_\S+', f'Report=report_{test_id}', content)
    content = CREDENTIALS + content
    dst_path.write_text(content, encoding='utf-8')


def parse_report(html_path: Path) -> dict:
    if not html_path.exists():
        return {"error": "report_not_found"}
    with open(html_path, 'r', encoding='utf-16', errors='replace') as f:
        html = f.read()
    text = re.sub(r'<[^>]+>', '|', html)
    parts = [p.strip() for p in text.split('|') if p.strip()]
    result = {}
    labels = {
        'Initial Deposit': 'initial_deposit',
        'Total Net Profit': 'net_profit',
        'Profit Factor': 'profit_factor',
        'Recovery Factor': 'recovery_factor',
        'Sharpe Ratio': 'sharpe',
        'Balance Drawdown Maximal': 'balance_dd_max',
        'Equity Drawdown Maximal': 'equity_dd_max',
        'Total Trades': 'total_trades',
        'Total Deals': 'total_deals',
        'Profit Trades (% of total)': 'profit_trades',
        'Loss Trades (% of total)': 'loss_trades',
        'Largest profit trade': 'largest_profit',
        'Largest loss trade': 'largest_loss',
        'Gross Profit': 'gross_profit',
        'Gross Loss': 'gross_loss',
    }
    for label, key in labels.items():
        for i, p in enumerate(parts):
            if p == label + ':' or p == label:
                for j in range(1, 4):
                    if i+j < len(parts):
                        v = parts[i+j]
                        if v and v not in [':', ''] and not v.endswith(':'):
                            result[key] = v
                            break
                break
    return result


def run_test_on_slot(test_id: str, slot: Path) -> dict:
    src = TESTS_DIR / f"{test_id}.ini"
    dst = slot / f"test_{test_id}.ini"
    if not src.exists():
        return {"test_id": test_id, "error": f"src not found"}
    patch_ini(src, dst)

    # Clean previous report
    for f in slot.glob(f"report_{test_id}*"):
        try: f.unlink()
        except: pass

    terminal = slot / "terminal64.exe"
    start = time.time()
    proc = subprocess.Popen([str(terminal), "/portable", f"/config:{dst}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    max_wait = 3600
    hard_max = 14400  # 4h hard ceiling
    report_path = slot / f"report_{test_id}.htm"
    log_dir = slot / "tester" / "logs"
    last_log_size = -1
    stuck_since = None
    last_check = time.time()
    while time.time() - start < hard_max:
        # Report present and stable -> done
        if report_path.exists():
            size1 = report_path.stat().st_size
            time.sleep(2)
            size2 = report_path.stat().st_size
            if size1 == size2 and size1 > 0:
                break
        # After max_wait, check if test is still progressing via log growth
        if time.time() - start > max_wait and time.time() - last_check > 60:
            last_check = time.time()
            try:
                logs = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
                cur_log_size = logs[0].stat().st_size if logs else 0
            except Exception:
                cur_log_size = 0
            if cur_log_size > last_log_size:
                last_log_size = cur_log_size
                stuck_since = None  # still working
            else:
                if stuck_since is None:
                    stuck_since = time.time()
                elif time.time() - stuck_since > 300:  # 5 min sense creixer = mort
                    break
        time.sleep(2)
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except:
        try: proc.kill()
        except: pass

    duration = time.time() - start
    result = parse_report(report_path)
    result['test_id'] = test_id
    result['duration_sec'] = round(duration, 1)
    result['slot'] = slot.name
    return result


def worker(slot: Path, queue: list, writer, csv_file):
    while True:
        with csv_lock:
            if not queue: return
            test_id = queue.pop(0)
        print(f"[{slot.name}] Running {test_id}...", flush=True)
        t0 = time.time()
        result = run_test_on_slot(test_id, slot)
        elapsed = time.time() - t0
        np = result.get('net_profit', '?')
        dd = result.get('balance_dd_max', '?')
        print(f"[{slot.name}] {test_id} done in {elapsed:.0f}s | profit={np} DD={dd}", flush=True)
        with csv_lock:
            writer.writerow(result)
            csv_file.flush()


def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else "*.ini"
    test_files = sorted([p for p in TESTS_DIR.glob(pattern) if p.name != "README.md"])
    if not test_files:
        print("No tests"); return
    queue = [p.stem for p in test_files]
    print(f"Pending: {len(queue)} tests across {len(SLOTS)} slots")

    headers = ['test_id', 'slot', 'duration_sec', 'initial_deposit', 'net_profit', 'profit_factor',
               'balance_dd_max', 'equity_dd_max', 'total_trades', 'total_deals',
               'profit_trades', 'loss_trades', 'gross_profit', 'gross_loss',
               'recovery_factor', 'sharpe', 'largest_profit', 'largest_loss']
    mode = 'a' if RESULTS_CSV.exists() else 'w'
    write_header = mode == 'w'
    with open(RESULTS_CSV, mode, newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction='ignore')
        if write_header: writer.writeheader()
        with ThreadPoolExecutor(max_workers=len(SLOTS)) as pool:
            futures = [pool.submit(worker, slot, queue, writer, f) for slot in SLOTS]
            for fut in as_completed(futures):
                fut.result()
    print("\nAll done")


if __name__ == "__main__":
    main()
