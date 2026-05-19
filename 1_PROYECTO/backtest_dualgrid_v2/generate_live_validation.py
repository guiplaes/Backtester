"""LV: validacio top configs amb compte LIVE i XAUUSD-VIPc."""
from pathlib import Path
TESTS_DIR = Path(r"C:\Users\Administrator\Desktop\MT4 Claude\1_PROYECTO\backtest_dualgrid_v2\tests")

TEMPLATE_6MO = """[Tester]
Expert=DualGridEA_v2_Reset
Symbol=XAUUSD-VIPc
Period=M5
Deposit=50000
Currency=USD
ExecutionMode=0
OptimizationMode=0
Model=1
FromDate=2025.11.16
ToDate=2026.05.15
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
InpResetEquityPct={rp}
InpConsolidateAnchor=true
InpMinSecBetweenResets=5
InpMaxDrawdownPct=20.0
InpMaxSpreadPoints=200
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

TESTS = [
    # Top 5 winners on live 6mo
    ("LV01_N11_live",   0.02, 1.0, 0.0,  0.0, 5, 2.0),   # N11 megabest
    ("LV02_B09_live",   0.01, 1.0, 0.0,  0.0, 5, 2.0),   # B09 V-D + V-B
    ("LV03_B11_live",   0.01, 1.0, 0.05, 3.0, 5, 2.0),   # B11 FULL_def
    ("LV04_B20_live",   0.01, 0.25,0.0,  3.0, 0, 2.0),   # reset 0.25% + V-C
    ("LV05_B05_live",   0.01, 1.0, 0.0,  5.0, 0, 2.0),   # V-B + V-C 5%
    # Variants escalats N11
    ("LV06_N11_lot03",  0.03, 1.0, 0.0,  0.0, 5, 2.0),
    ("LV07_B11_lot02",  0.02, 1.0, 0.10, 3.0, 5, 2.0),
    # B11 amb V-A escalat
]

for tid, lot, rp, maxlot, emer, sl, harvest in TESTS:
    content = TEMPLATE_6MO.format(tid=tid, lot=lot, rp=rp, maxlot=maxlot,
                                  emer=emer, sl=sl, harvest=harvest)
    (TESTS_DIR / f"{tid}.ini").write_text(content)

print(f"Generated {len(TESTS)} LV tests (LIVE account)")
