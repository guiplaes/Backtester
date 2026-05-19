"""Tests sp 1/2/3 USD amb cap 2% (V-B v3 sol, sense V-A). 1 mes cribage."""
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
Model=1
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
InpMagicNumber=88888
InpResetStateOnInit=true
InpCleanSlateOnInit=true
InpUpdateBaselineOnReset=true
InpMaxLotPerSide=0.0
InpEmergencyResetLossPct=0.0
InpPositionSLSteps=0
InpHarvestWinnerPct=2.0
"""

# Format: (id, sp, lv, tp, reset_pct)
# Reset escalat amb spacing per equivalencia
TESTS = [
    ("S01_sp1_h20",  1.0, 5, 1.0, 0.25),
    ("S02_sp2_h20",  2.0, 5, 2.0, 0.4),
    ("S03_sp3_h20",  3.0, 5, 3.0, 0.6),
    ("S04_sp4_h20",  4.0, 5, 4.0, 0.8),
    ("S05_sp7_h20",  7.0, 5, 7.0, 1.4),
    ("S06_sp10_h20",10.0, 5,10.0, 2.0),
]

for tid, sp, lv, tp, rp in TESTS:
    content = TEMPLATE.format(tid=tid, sp=sp, lv=lv, tp=tp, rp=rp)
    (TESTS_DIR / f"{tid}.ini").write_text(content)

print(f"Generated {len(TESTS)} S tests (sp variants)")
