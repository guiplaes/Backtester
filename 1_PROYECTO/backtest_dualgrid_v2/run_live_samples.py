"""Llanca tests sample sp=1 al live terminal seqüencial (1 per cop)."""
import subprocess, time, re
from pathlib import Path

LIVE_TERMINAL = Path(r"C:\Program Files\VT Markets (Pty) MT5 Terminal\terminal64.exe")
DATA_DIR = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\9BB124B7D418C7FB69DF2865535BA9BF")

TEMPLATE = """[Tester]
Expert=DualGridEA_v2_Reset
Symbol=XAUUSD-VIPc
Period=M5
Deposit=50000
Currency=USD
ExecutionMode=0
OptimizationMode=0
Model=1
FromDate={fr}
ToDate={to}
ForwardMode=0
Report=report_{tid}
ReplaceReport=1
ShutdownTerminal=1
Visual=0

[TesterInputs]
InpLotSize=2.0
InpLevelSpacingUSD=1.0
InpLevelsEachSide=5
InpFluidTPUSD=1.0
InpUseVirtualTP=true
InpResetEquityPct=1.0
InpConsolidateAnchor=true
InpMinSecBetweenResets=5
InpMaxDrawdownPct=20.0
InpMaxSpreadPoints=200
InpAvoidWeekend=true
InpMagicNumber=88888
InpResetStateOnInit=true
InpCleanSlateOnInit=true
InpUpdateBaselineOnReset=true
InpMaxLotPerSide=0.0
InpEmergencyResetLossPct=0.0
InpPositionSLSteps=5
InpHarvestWinnerPct=2.0
"""

SAMPLES = [
    ("sample_lv_jun24",     "2024.06.03", "2024.06.28"),  # range $98 LATERAL
    ("sample_lv_jul24_up",  "2024.07.01", "2024.07.31"),  # range $161 UP trend low-vol
    ("sample_lv_nov24",     "2024.11.01", "2024.11.29"),  # range $221 slight bear
    ("sample_mid_mar25_up", "2025.03.01", "2025.03.28"),  # range $276 UP trend mid-vol
    ("sample_mid_may25",    "2025.05.01", "2025.05.30"),  # range $314 LATERAL
    ("sample_hv_oct25",     "2025.10.01", "2025.10.31"),  # range $559 slight up
    ("sample_hv_feb26_up",  "2026.02.01", "2026.02.28"),  # range $873 UP trend fort
    ("sample_hv_mar26_dn",  "2026.03.01", "2026.03.31"),  # range $1319 DOWN trend fort
]

def parse_report(path):
    if not path.exists(): return None
    with open(path,'r',encoding='utf-16',errors='replace') as f:
        h = f.read()
    t = re.sub(r'<[^>]+>','|',h)
    p = [x.strip() for x in t.split('|') if x.strip()]
    out = {}
    for label in ['Total Net Profit','Balance Drawdown Maximal','Total Trades','Profit Factor']:
        for i,x in enumerate(p):
            if x == label+':' or x == label:
                for j in range(1,4):
                    if i+j < len(p):
                        v = p[i+j]
                        if v and v not in [':',''] and not v.endswith(':'):
                            out[label] = v
                            break
                break
    return out

results = []
for tid, fr, to in SAMPLES:
    ini = DATA_DIR / f"test_{tid}.ini"
    ini.write_text(TEMPLATE.format(tid=tid, fr=fr, to=to), encoding='utf-8')

    # Clean previous report
    rep = DATA_DIR / f"report_{tid}.htm"
    if rep.exists(): rep.unlink()

    print(f"\n=== Llancant {tid} ({fr} -> {to}) ===")
    start = time.time()
    proc = subprocess.Popen([str(LIVE_TERMINAL), f"/config:{ini}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    max_wait = 3600
    while time.time() - start < max_wait:
        if rep.exists():
            s1 = rep.stat().st_size
            time.sleep(3)
            s2 = rep.stat().st_size
            if s1 == s2 and s1 > 0: break
        time.sleep(3)
    try: proc.terminate(); proc.wait(timeout=10)
    except:
        try: proc.kill()
        except: pass
    elapsed = time.time() - start
    res = parse_report(rep) or {"error":"timeout"}
    print(f"  Done in {elapsed:.0f}s")
    print(f"  Net Profit: {res.get('Total Net Profit','?')}")
    print(f"  DD: {res.get('Balance Drawdown Maximal','?')}")
    print(f"  Trades: {res.get('Total Trades','?')}")
    results.append((tid, fr, to, res, elapsed))

print("\n\n=== RESUM SAMPLE LIVE sp=1 ===")
for tid, fr, to, res, el in results:
    pnl = res.get('Total Net Profit','?')
    dd = res.get('Balance Drawdown Maximal','?')
    print(f"{tid:<24} {fr}: PnL={pnl} DD={dd}")
