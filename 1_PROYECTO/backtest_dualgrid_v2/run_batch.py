"""
Batch runner per MT5 Strategy Tester.
Itera tots els .ini de tests/ amb credentials embedded i recull resultats a CSV.
"""
import csv
import os
import re
import subprocess
import time
from pathlib import Path

TESTS_DIR = Path(r"C:\Users\Administrator\Desktop\MT4 Claude\1_PROYECTO\backtest_dualgrid_v2\tests")
MT5_TESTER = Path(r"C:\MT5_Tester")
RESULTS_CSV = Path(r"C:\Users\Administrator\Desktop\MT4 Claude\1_PROYECTO\backtest_dualgrid_v2\results.csv")
TERMINAL_EXE = MT5_TESTER / "terminal64.exe"

CREDENTIALS = """[Common]
Login=1110830
Password=lN5V7&QK
Server=VTMarkets-Demo

"""

# Patch every .ini file to:
# 1. Add [Common] credentials at top
# 2. Change Currency=USC -> USD (fix from before)
def patch_ini(src_path: Path, dst_path: Path):
    content = src_path.read_text(encoding='utf-8')
    # Replace Currency=USC with Currency=USD
    content = content.replace('Currency=USC', 'Currency=USD')
    # Ensure report name matches the file id
    test_id = src_path.stem
    content = re.sub(r'Report=report_\S+', f'Report=report_{test_id}', content)
    # Add credentials at start
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


def run_test(test_id: str) -> dict:
    src = TESTS_DIR / f"{test_id}.ini"
    dst = MT5_TESTER / f"test_{test_id}.ini"
    if not src.exists():
        return {"error": f"src .ini not found: {src}"}
    patch_ini(src, dst)

    # Clean previous report + logs
    for f in MT5_TESTER.glob(f"report_{test_id}*"):
        try: f.unlink()
        except: pass
    log_dir = MT5_TESTER / "logs"
    for f in log_dir.glob("*.log"):
        try: f.unlink()
        except: pass

    # Launch
    start = time.time()
    proc = subprocess.Popen([str(TERMINAL_EXE), "/portable",
                              f"/config:{dst}"],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    # Wait for completion (max 30 min per test — V-C can be slow)
    max_wait = 1800
    report_path = MT5_TESTER / f"report_{test_id}.htm"
    while time.time() - start < max_wait:
        if report_path.exists():
            # Check if file is stable (not being written)
            size1 = report_path.stat().st_size
            time.sleep(2)
            size2 = report_path.stat().st_size
            if size1 == size2 and size1 > 0:
                break
        time.sleep(2)

    # Try to terminate cleanly
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
    return result


def main():
    # Get test list ordered by ID — pick up both numeric and R-prefixed refinements
    import sys
    pattern = sys.argv[1] if len(sys.argv) > 1 else "*.ini"
    test_files = sorted([p for p in TESTS_DIR.glob(pattern) if p.name != "README.md"])
    if not test_files:
        print("No test .ini files found")
        return

    # Init CSV with header
    headers = ['test_id', 'duration_sec', 'initial_deposit', 'net_profit', 'profit_factor',
               'balance_dd_max', 'equity_dd_max', 'total_trades', 'total_deals',
               'profit_trades', 'loss_trades', 'gross_profit', 'gross_loss',
               'recovery_factor', 'sharpe', 'largest_profit', 'largest_loss']

    # Append to existing results file
    mode = 'a' if RESULTS_CSV.exists() else 'w'
    write_header = mode == 'w'
    with open(RESULTS_CSV, mode, newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction='ignore')
        if write_header:
            writer.writeheader()

        for i, test_file in enumerate(test_files):
            test_id = test_file.stem
            print(f"\n[{i+1}/{len(test_files)}] Running {test_id}...")
            t0 = time.time()
            result = run_test(test_id)
            elapsed = time.time() - t0
            print(f"  Done in {elapsed:.0f}s")
            print(f"  Net profit: {result.get('net_profit', '?')}")
            print(f"  Drawdown: {result.get('balance_dd_max', '?')}")
            print(f"  Trades: {result.get('total_trades', '?')}")
            writer.writerow(result)
            f.flush()

    print(f"\n\nAll done. Results: {RESULTS_CSV}")


if __name__ == "__main__":
    main()
