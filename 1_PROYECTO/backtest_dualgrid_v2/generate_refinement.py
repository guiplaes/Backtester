"""Genera tests de refinament al voltant dels top configs."""
from pathlib import Path

TESTS_DIR = Path(r"C:\Users\Administrator\Desktop\MT4 Claude\1_PROYECTO\backtest_dualgrid_v2\tests")

TEMPLATE = """[Tester]
Expert=DualGridEA_v2_Reset
Symbol=XAUUSD-VIP
Period=M1
Deposit=50000
Currency=USD
ExecutionMode=0
OptimizationMode=0
Model=4
FromDate=2026.04.15
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
InpHeartbeatFile=
InpResetStateOnInit=true
InpCleanSlateOnInit=true
InpStartBalanceOverride=0.0
InpUpdateBaselineOnReset=true
"""

# Refinements (variants al voltant dels millors)
TESTS = [
    # Variants 03_wide (sp=5, lvl=10, TP=5, reset=1)
    ("R01_sp4_lvl10",  4.0, 10, 4.0, 1.0,  "sp=4 vs sp=5"),
    ("R02_sp6_lvl10",  6.0, 10, 6.0, 1.0,  "sp=6 vs sp=5"),
    ("R03_sp5_lvl5",   5.0,  5, 5.0, 1.0,  "less levels"),
    ("R04_sp5_lvl15",  5.0, 15, 5.0, 1.0,  "more levels"),
    ("R05_sp5_tp3",    5.0, 10, 3.0, 1.0,  "TP<spacing"),
    ("R06_sp5_tp7",    5.0, 10, 7.0, 1.0,  "TP>spacing"),
    ("R07_sp5_r05",    5.0, 10, 5.0, 0.5,  "reset menor"),
    ("R08_sp5_r2",     5.0, 10, 5.0, 2.0,  "reset major"),

    # Variants 07_high_reset (sp=1, lvl=5, TP=1, reset=1)
    ("R09_sp1_r075",   1.0,  5, 1.0, 0.75, "reset 0.75"),
    ("R10_sp1_r125",   1.0,  5, 1.0, 1.25, "reset 1.25"),
    ("R11_sp1_r15",    1.0,  5, 1.0, 1.5,  "reset 1.5"),
    ("R12_sp1_tp15",   1.0,  5, 1.5, 1.0,  "TP 1.5 sp 1"),

    # Sweet spot hunting (sp 2-3)
    ("R13_sp2_r075",   2.0,  5, 2.0, 0.75, "sp2 reset 0.75"),
    ("R14_sp25_r1",    2.5,  8, 2.5, 1.0,  "sp 2.5"),
    ("R15_sp3_r075",   3.0,  8, 3.0, 0.75, "sp3 reset 0.75"),
    ("R16_sp3_r125",   3.0, 10, 3.0, 1.25, "sp3 reset 1.25"),

    # Extra defensives
    ("R17_sp4_lvl5",   4.0,  5, 4.0, 1.0,  "sp4 amb 5 lvl"),
    ("R18_sp4_r075",   4.0, 10, 4.0, 0.75, "sp4 reset 0.75"),

    # Asimetrics (TP gran amb spacing molt menor)
    ("R19_sp1_tp3",    1.0,  5, 3.0, 1.0,  "TP triple sp"),
    ("R20_sp05_tp2",   0.5,  5, 2.0, 1.0,  "sp 0.5 TP 2"),
]

for tid, sp, lv, tp, rp, purpose in TESTS:
    content = TEMPLATE.format(tid=tid, sp=sp, lv=lv, tp=tp, rp=rp)
    path = TESTS_DIR / f"{tid}.ini"
    path.write_text(content)

print(f"Generated {len(TESTS)} refinement test .ini files")
