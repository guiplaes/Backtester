"""Genera tests amb V-B (Harvest del guanyador) i combinacions."""
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
InpMagicNumber=88888
InpResetStateOnInit=true
InpCleanSlateOnInit=true
InpUpdateBaselineOnReset=true
InpMaxLotPerSide={maxlot}
InpEmergencyResetLossPct=0.0
InpPositionSLSteps={sl}
InpHarvestWinnerPct={harvest}
"""

# Tests amb V-B (harvest winner) variant percentatges + combinacions
# Format: (id, sp, lv, tp, rp, maxlot, sl_steps, harvest_pct)
TESTS = [
    # V-B sol (harvest winner amb config baseline)
    ("H01_base_harv05",    1.0,  5, 1.0, 0.25, 0.0,  0, 0.5),
    ("H02_base_harv1",     1.0,  5, 1.0, 0.25, 0.0,  0, 1.0),
    ("H03_base_harv2",     1.0,  5, 1.0, 0.25, 0.0,  0, 2.0),
    ("H04_base_harv3",     1.0,  5, 1.0, 0.25, 0.0,  0, 3.0),
    ("H05_base_harv5",     1.0,  5, 1.0, 0.25, 0.0,  0, 5.0),

    # V-B sobre wide (sp=5)
    ("H06_wide_harv05",    5.0,  5, 5.0, 1.0,  0.0,  0, 0.5),
    ("H07_wide_harv1",     5.0,  5, 5.0, 1.0,  0.0,  0, 1.0),
    ("H08_wide_harv2",     5.0,  5, 5.0, 1.0,  0.0,  0, 2.0),
    ("H09_wide_harv3",     5.0,  5, 5.0, 1.0,  0.0,  0, 3.0),
    ("H10_wide_harv5",     5.0,  5, 5.0, 1.0,  0.0,  0, 5.0),

    # V-A + V-B combinades (cap exposure + harvest)
    ("H11_wide_A05_harv1", 5.0,  5, 5.0, 1.0, 0.05,  0, 1.0),
    ("H12_wide_A05_harv2", 5.0,  5, 5.0, 1.0, 0.05,  0, 2.0),
    ("H13_wide_A10_harv1", 5.0, 10, 5.0, 1.0, 0.10,  0, 1.0),
    ("H14_wide_A10_harv2", 5.0, 10, 5.0, 1.0, 0.10,  0, 2.0),
    ("H15_sp3_A05_harv1",  3.0,  5, 3.0, 0.5, 0.05,  0, 1.0),

    # V-D + V-B (SL + harvest)
    ("H16_wide_SL4_harv1", 5.0,  5, 5.0, 1.0, 0.0,   4, 1.0),
    ("H17_wide_SL5_harv2", 5.0,  5, 5.0, 1.0, 0.0,   5, 2.0),

    # Triple combo: V-A + V-D + V-B
    ("H18_full_v04like",   5.0,  5, 5.0, 1.0, 0.05,  4, 1.0),
    ("H19_full_sp3",       3.0,  5, 3.0, 0.5, 0.05,  4, 1.5),
    ("H20_full_sp10",     10.0,  5,10.0, 2.0, 0.05,  3, 2.0),
]

for tid, sp, lv, tp, rp, maxlot, sl, harvest in TESTS:
    content = TEMPLATE.format(tid=tid, sp=sp, lv=lv, tp=tp, rp=rp, maxlot=maxlot, sl=sl, harvest=harvest)
    (TESTS_DIR / f"{tid}.ini").write_text(content)

print(f"Generated {len(TESTS)} harvest tests")
