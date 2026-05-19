"""Recerca nocturna autonoma: itera fins trobar config que sobrevisqui tics."""
import subprocess, time, re, os, glob
from pathlib import Path

TESTS_DIR = Path(r"C:\Users\Administrator\Desktop\MT4 Claude\1_PROYECTO\backtest_dualgrid_v2\tests")
LOG = Path(r"C:\Users\Administrator\Desktop\MT4 Claude\1_PROYECTO\backtest_dualgrid_v2\NIGHT_LOG.txt")

def log(msg):
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    print(msg, flush=True)

def parse_report_for(test_id):
    """Find and parse report for test_id across all slots."""
    for slot in ['MT5_Tester','MT5_Tester2','MT5_Tester3']:
        path = Path(rf"C:\{slot}\report_{test_id}.htm")
        if not path.exists(): continue
        try:
            with open(path,'r',encoding='utf-16',errors='replace') as f: h=f.read()
            t=re.sub(r'<[^>]+>','|',h)
            p=[x.strip() for x in t.split('|') if x.strip()]
            out={}
            for L in ['Total Net Profit','Balance Drawdown Maximal','Total Trades','Profit Factor']:
                for i,x in enumerate(p):
                    if x==L or x==L+':':
                        for j in range(1,4):
                            v=p[i+j] if i+j<len(p) else ''
                            if v and v not in [':',''] and not v.endswith(':'):
                                out[L]=v; break
                        break
            # Parse numbers
            np = out.get('Total Net Profit','0').replace(' ','').replace(',','.')
            try: net = float(np)
            except: net = 0
            dd_str = out.get('Balance Drawdown Maximal','0 (0%)')
            m = re.search(r'\(([\d.]+)%\)', dd_str)
            dd_pct = float(m.group(1)) if m else 0
            return {'net':net, 'dd_pct':dd_pct, 'raw':out}
        except: pass
    return None

def wait_for_batch(prefix, expected):
    """Wait until all reports for prefix exist or terminals die."""
    log(f"Waiting for {prefix}* batch ({expected} tests)...")
    start = time.time()
    while time.time() - start < 7200:  # 2h max per batch
        reports = []
        for slot in ['MT5_Tester','MT5_Tester2','MT5_Tester3']:
            reports += glob.glob(rf"C:\{slot}\report_{prefix}*.htm")
        if len(reports) >= expected:
            log(f"{prefix}*: {len(reports)}/{expected} ✓")
            return reports
        # Check if any terminals running
        result = subprocess.run(['tasklist'], capture_output=True, text=True)
        if 'metatester64.exe' not in result.stdout and 'MT5_Tester' not in result.stdout:
            log(f"No terminals running, breaking ({len(reports)}/{expected})")
            return reports
        time.sleep(60)
    log(f"Timeout, {len(reports)} reports")
    return reports

def main():
    log("=== NIGHT SEARCH START ===")

    # Phase 1: TK batch already running (32 tests)
    # Wait for it
    reports = wait_for_batch("TK_", 32)
    log(f"TK batch done: {len(reports)} reports")

    # Parse all TK results
    results = {}
    for r in reports:
        tid = Path(r).stem.replace('report_','')
        d = parse_report_for(tid)
        if d:
            results[tid] = d

    # Find configs that DIDN'T blow up (DD < 15%) in ALL periods
    configs_set = set()
    periods_set = set()
    for tid in results:
        parts = tid.split('_', 2)  # TK_p_cfg
        if len(parts) >= 3:
            p = parts[1]; c = parts[2]
            configs_set.add(c); periods_set.add(p)

    log(f"Configs: {sorted(configs_set)}")
    log(f"Periodes: {sorted(periods_set)}")

    # Score each config by sum of profit across periods + max DD
    config_scores = {}
    for c in configs_set:
        total_profit = 0
        max_dd = 0
        all_pass = True
        for p in periods_set:
            tid = f"TK_{p}_{c}"
            r = results.get(tid)
            if not r:
                all_pass = False; continue
            total_profit += r['net']
            max_dd = max(max_dd, r['dd_pct'])
            if r['dd_pct'] >= 19.5:  # blowup
                all_pass = False
        config_scores[c] = (all_pass, total_profit, max_dd)

    # Sort by survives first, then profit
    survivors = [(c, s) for c, s in config_scores.items() if s[0]]
    survivors.sort(key=lambda x: -x[1][1])

    log("\n=== SURVIVORS (no DD>19.5% en cap periode) ===")
    for c, (ok, prof, dd) in survivors[:5]:
        log(f"  {c}: profit ${prof:.0f}  max DD {dd:.1f}%")

    log("\n=== TOTS (incloent fallits) ===")
    for c in sorted(configs_set, key=lambda x: -(config_scores[x][1] if config_scores[x][0] else -99999)):
        ok, prof, dd = config_scores[c]
        marker = "★" if ok else "✗"
        log(f"  {marker} {c}: profit ${prof:.0f}  max DD {dd:.1f}%")

    log("\n=== NIGHT SEARCH DONE ===")

if __name__ == "__main__":
    main()
