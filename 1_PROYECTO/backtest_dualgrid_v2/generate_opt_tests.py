"""Bateria O: optimitzacio fina al voltant de B09 i B11 (6 mesos)."""
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
InpMaxLotPerSide={maxlot}
InpEmergencyResetLossPct={emer}
InpPositionSLSteps={sl}
InpHarvestWinnerPct={harvest}
"""

# (id, rp, maxlot, emer, sl, harvest)
# Variants al voltant de B09 (V-D 5 + V-B 2%) i B11 (all on)
TESTS = [
    # Variants V-D (SL per posicio)
    ("O01_VD3_VB2",      1.0, 0.0,  0.0, 3, 2.0),
    ("O02_VD4_VB2",      1.0, 0.0,  0.0, 4, 2.0),
    ("O03_VD6_VB2",      1.0, 0.0,  0.0, 6, 2.0),
    ("O04_VD7_VB2",      1.0, 0.0,  0.0, 7, 2.0),

    # Variants V-B amb V-D 5
    ("O05_VD5_VB15",     1.0, 0.0,  0.0, 5, 1.5),
    ("O06_VD5_VB25",     1.0, 0.0,  0.0, 5, 2.5),
    ("O07_VD5_VB3",      1.0, 0.0,  0.0, 5, 3.0),

    # B09 + reset baix
    ("O08_VD5_VB2_rp05", 0.5, 0.0,  0.0, 5, 2.0),
    ("O09_VD5_VB2_rp025",0.25,0.0,  0.0, 5, 2.0),

    # B09 + V-C lleuger
    ("O10_VD5_VB2_VC5",  1.0, 0.0,  5.0, 5, 2.0),
    ("O11_VD5_VB2_VC8",  1.0, 0.0,  8.0, 5, 2.0),

    # B09 + V-A modest
    ("O12_VD5_VB2_VA010",1.0, 0.10, 0.0, 5, 2.0),
    ("O13_VD5_VB2_VA015",1.0, 0.15, 0.0, 5, 2.0),

    # B11 variants
    ("O14_FULL_VA03",    1.0, 0.03, 3.0, 5, 2.0),
    ("O15_FULL_VA10",    1.0, 0.10, 3.0, 5, 2.0),
    ("O16_FULL_VC2",     1.0, 0.05, 2.0, 5, 2.0),
    ("O17_FULL_VC5",     1.0, 0.05, 5.0, 5, 2.0),
    ("O18_FULL_rp05",    0.5, 0.05, 3.0, 5, 2.0),

    # Combos prometedors
    ("O19_VD4_VB15_rp05",0.5, 0.0,  0.0, 4, 1.5),
    ("O20_VD5_VB2_VC5_rp05",0.5,0.0,5.0, 5, 2.0),
]

for tid, rp, maxlot, emer, sl, harvest in TESTS:
    content = TEMPLATE.format(tid=tid, rp=rp, maxlot=maxlot, emer=emer, sl=sl, harvest=harvest)
    (TESTS_DIR / f"{tid}.ini").write_text(content)

print(f"Generated {len(TESTS)} O tests (optimitzacio B09/B11)")
