"""Validacio LONG: 2 anys + Model=4 (real ticks) del top + variants escalades."""
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
FromDate=2024.05.16
ToDate=2026.05.16
ForwardMode=0
Report=report_{tid}
ReplaceReport=1
ShutdownTerminal=1
Visual=0

[TesterInputs]
InpLotSize={lot}
InpLevelSpacingUSD=5.0
InpLevelsEachSide=5
InpFluidTPUSD=5.0
InpUseVirtualTP=true
InpResetEquityPct=1.0
InpConsolidateAnchor=true
InpMinSecBetweenResets=5
InpMaxDrawdownPct=20.0
InpMaxSpreadPoints=80
InpAvoidWeekend=true
InpMagicNumber=88888
InpResetStateOnInit=true
InpCleanSlateOnInit=true
InpUpdateBaselineOnReset=true
InpMaxLotPerSide={maxlot}
InpEmergencyResetLossPct={emer}
InpPositionSLSteps={sl}
InpHarvestWinnerPct={harvest}
"""

# (id, lot, maxlot, emer, sl, harvest)
TESTS = [
    # B11 (FULL_def) escalat
    ("L01_B11_lot01",      0.01, 0.05, 3.0, 5, 2.0),
    ("L02_B11_lot02",      0.02, 0.10, 3.0, 5, 2.0),
    ("L03_B11_lot03",      0.03, 0.15, 3.0, 5, 2.0),
    ("L04_B11_lot05",      0.05, 0.25, 3.0, 5, 2.0),

    # B09 (V-D + V-B) escalat
    ("L05_B09_lot01",      0.01, 0.0,  0.0, 5, 2.0),
    ("L06_B09_lot02",      0.02, 0.0,  0.0, 5, 2.0),
    ("L07_B09_lot03",      0.03, 0.0,  0.0, 5, 2.0),

    # B05 (V-B + V-C) escalat
    ("L08_B05_lot01",      0.01, 0.0,  5.0, 0, 2.0),
    ("L09_B05_lot02",      0.02, 0.0,  5.0, 0, 2.0),
]

for tid, lot, maxlot, emer, sl, harvest in TESTS:
    content = TEMPLATE.format(tid=tid, lot=lot, maxlot=maxlot,
                              emer=emer, sl=sl, harvest=harvest)
    (TESTS_DIR / f"{tid}.ini").write_text(content)

print(f"Generated {len(TESTS)} L tests (2 anys + Model=4)")
