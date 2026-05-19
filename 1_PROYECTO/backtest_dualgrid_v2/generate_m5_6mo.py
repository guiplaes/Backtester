"""Genera tests M5 6-mesos dels top configs."""
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
"""

# Top configs from M1 30d test + a few defensives
# Format: (id, sp, lv, tp, rp)
TESTS = [
    ("M01_baseline_6mo",   1.0,  5, 1.0, 0.25),
    ("M02_tight_6mo",      0.5, 10, 0.5, 0.10),
    ("M03_wide_6mo",       5.0, 10, 5.0, 1.0),
    ("M05_few_wide_6mo",   2.0,  5, 2.0, 0.5),
    ("M06_low_reset_6mo",  1.0,  5, 1.0, 0.05),
    ("M07_high_reset_6mo", 1.0,  5, 1.0, 1.0),
    ("M09_smTP_bigSp_6mo", 2.0,  5, 0.5, 0.25),
    ("M10_mid_6mo",        2.0, 10, 2.0, 0.5),
    ("M13_sp3_6mo",        3.0, 10, 3.0, 0.5),
    ("M17_sp05_tp1_6mo",   0.5,  5, 1.0, 0.25),
    ("M18_sp2_lvl3_6mo",   2.0,  3, 2.0, 0.3),
    ("M20_conserv_6mo",    3.0,  5, 3.0, 1.5),
    # Variants noves per 6mo
    ("M21_sp4_lvl5_r1",    4.0,  5, 4.0, 1.0),
    ("M22_sp4_lvl10_r05",  4.0, 10, 4.0, 0.5),
    ("M23_sp6_lvl10_r1",   6.0, 10, 6.0, 1.0),
    ("M24_sp8_lvl10_r1",   8.0, 10, 8.0, 1.0),
    ("M25_sp10_lvl5_r2",  10.0,  5,10.0, 2.0),
    ("M26_sp2_lvl5_r1",    2.0,  5, 2.0, 1.0),
    ("M27_sp3_lvl5_r1",    3.0,  5, 3.0, 1.0),
    ("M28_sp5_lvl5_r2",    5.0,  5, 5.0, 2.0),
]

for tid, sp, lv, tp, rp in TESTS:
    content = TEMPLATE.format(tid=tid, sp=sp, lv=lv, tp=tp, rp=rp)
    path = TESTS_DIR / f"{tid}.ini"
    path.write_text(content)

print(f"Generated {len(TESTS)} M5 6-month test .ini files")
