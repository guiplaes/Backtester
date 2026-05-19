"""Genera tests M5 6-mesos amb Valvules A i C."""
from pathlib import Path
TESTS_DIR = Path(r"C:\Users\Administrator\Desktop\MT4 Claude\1_PROYECTO\backtest_dualgrid_v2\tests")

TEMPLATE = """[Tester]
Expert=DualGridEA_v2_Reset
Symbol=XAUUSD-VIP
Period=M5
Deposit=50000
Currency=USD
ExecutionMode=0
OptimizationMode=0
Model=4
FromDate=2025.11.16
ToDate=2026.05.15
ForwardMode=0
Report=report_{tid}
ReplaceReport=1
ShutdownTerminal=1
Visual=0

[TesterInputs]
InpLotSize=0.01
InpLevelSpacingUSD={sp}
InpLevelsEachSide={lv}
InpFluidTPUSD={tp}
InpUseVirtualTP=true
InpResetEquityPct={rp}
InpConsolidateAnchor=true
InpMinSecBetweenResets=5
InpMaxDrawdownPct=20.0
InpMaxSpreadPoints=80
InpAvoidWeekend=true
InpMinPositionDistance=0.0
InpMagicNumber=88888
InpComment=DGv2R_BT
InpDrawDashboard=false
InpVerboseLog=false
InpHeartbeatSec=0
InpResetStateOnInit=true
InpCleanSlateOnInit=true
InpStartBalanceOverride=0.0
InpUpdateBaselineOnReset=true
InpMaxLotPerSide={maxlot}
InpEmergencyResetLossPct={emrg}
"""

# Configs prometedors del M5 6mo run + variations amb valvules
# Vàlvula A only: maxlot 0.05, 0.10, 0.20, 0.30
# Vàlvula C only: emrg 3%, 5%, 8%
# Both combined

TESTS = [
    # === V-A only: cap d'exposure ===
    ("V01_baseline_A05",   1.0,  5, 1.0, 0.25, 0.05, 0.0),
    ("V02_baseline_A10",   1.0,  5, 1.0, 0.25, 0.10, 0.0),
    ("V03_baseline_A20",   1.0,  5, 1.0, 0.25, 0.20, 0.0),
    ("V04_wide_A05",       5.0, 10, 5.0, 1.0,  0.05, 0.0),
    ("V05_wide_A10",       5.0, 10, 5.0, 1.0,  0.10, 0.0),
    ("V06_wide_A20",       5.0, 10, 5.0, 1.0,  0.20, 0.0),
    ("V07_sp2_A05",        2.0,  5, 2.0, 0.5,  0.05, 0.0),
    ("V08_sp2_A10",        2.0,  5, 2.0, 0.5,  0.10, 0.0),
    ("V09_sp3_A05",        3.0,  5, 3.0, 0.5,  0.05, 0.0),
    ("V10_sp3_A10",        3.0,  5, 3.0, 0.5,  0.10, 0.0),

    # === V-C only: emergency reset ===
    ("V11_baseline_C3",    1.0,  5, 1.0, 0.25, 0.0,  3.0),
    ("V12_baseline_C5",    1.0,  5, 1.0, 0.25, 0.0,  5.0),
    ("V13_baseline_C8",    1.0,  5, 1.0, 0.25, 0.0,  8.0),
    ("V14_wide_C5",        5.0, 10, 5.0, 1.0,  0.0,  5.0),
    ("V15_sp2_C5",         2.0,  5, 2.0, 0.5,  0.0,  5.0),

    # === A+C combined ===
    ("V16_baseline_A10_C5", 1.0,  5, 1.0, 0.25, 0.10, 5.0),
    ("V17_wide_A10_C5",     5.0, 10, 5.0, 1.0,  0.10, 5.0),
    ("V18_sp2_A05_C3",      2.0,  5, 2.0, 0.5,  0.05, 3.0),
    ("V19_sp3_A05_C5",      3.0,  5, 3.0, 0.5,  0.05, 5.0),
    ("V20_baseline_A05_C5", 1.0,  5, 1.0, 0.5,  0.05, 5.0),
]

for tid, sp, lv, tp, rp, maxlot, emrg in TESTS:
    content = TEMPLATE.format(tid=tid, sp=sp, lv=lv, tp=tp, rp=rp, maxlot=maxlot, emrg=emrg)
    path = TESTS_DIR / f"{tid}.ini"
    path.write_text(content)

print(f"Generated {len(TESTS)} valve tests")
