"""Tests variant reset_pct (0.15, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0) amb sp=5 cap=2%. 1 mes."""
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
InpLevelSpacingUSD=5.0
InpLevelsEachSide=5
InpFluidTPUSD=5.0
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

TESTS = [
    ("R10_rp015", 0.15),
    ("R11_rp025", 0.25),
    ("R12_rp050", 0.50),
    ("R13_rp075", 0.75),
    # rp=1.0 ja correu com Z01_sp5_h20
    ("R14_rp150", 1.50),
    ("R15_rp200", 2.00),
]

for tid, rp in TESTS:
    content = TEMPLATE.format(tid=tid, rp=rp)
    (TESTS_DIR / f"{tid}.ini").write_text(content)

print(f"Generated {len(TESTS)} R-reset tests")
