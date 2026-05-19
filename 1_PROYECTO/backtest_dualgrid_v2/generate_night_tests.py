"""Bateria N: hipotesis noves basades en analisi de B09/B11 - 6 mesos."""
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
InpLotSize={lot}
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
InpEmergencyResetLossPct={emer}
InpPositionSLSteps={sl}
InpHarvestWinnerPct={harvest}
"""

# Hipotesis: explorar dimensions que NO hem cobert
# (id, lot, sp, lv, tp, rp, maxlot, emer, sl, harvest)
TESTS = [
    # Spacing variants amb V-D 5 + V-B 2% (clau B09)
    ("N01_sp3_VD5_VB2",    0.01, 3.0, 5, 3.0, 0.6, 0.0, 0.0, 5, 2.0),
    ("N02_sp4_VD5_VB2",    0.01, 4.0, 5, 4.0, 0.8, 0.0, 0.0, 5, 2.0),
    ("N03_sp7_VD5_VB2",    0.01, 7.0, 5, 7.0, 1.4, 0.0, 0.0, 5, 2.0),
    ("N04_sp10_VD5_VB2",   0.01,10.0, 5,10.0, 2.0, 0.0, 0.0, 5, 2.0),

    # Mes nivells de grid amb V-D + V-B
    ("N05_lvl10_VD5_VB2",  0.01, 5.0,10, 5.0, 1.0, 0.0, 0.0, 5, 2.0),
    ("N06_lvl15_VD5_VB2",  0.01, 5.0,15, 5.0, 1.0, 0.0, 0.0, 5, 2.0),

    # TP/SP ratios
    ("N07_TPbaixVD5",      0.01, 5.0, 5, 3.0, 0.6, 0.0, 0.0, 5, 2.0),
    ("N08_TPaltVD5",       0.01, 5.0, 5, 8.0, 1.6, 0.0, 0.0, 5, 2.0),

    # V-D + reset baix combinats
    ("N09_VD5_rp025",      0.01, 5.0, 5, 5.0, 0.25,0.0, 0.0, 5, 2.0),
    ("N10_VD5_rp05_VB15",  0.01, 5.0, 5, 5.0, 0.5, 0.0, 0.0, 5, 1.5),

    # Mes lot inicial sense V-A (test escalat directe sense cap)
    ("N11_lot02_VD5_VB2",  0.02, 5.0, 5, 5.0, 1.0, 0.0, 0.0, 5, 2.0),
    ("N12_lot003_VD5_VB2", 0.005,5.0, 5, 5.0, 1.0, 0.0, 0.0, 5, 2.0),

    # B11 amb spacing diferent
    ("N13_FULL_sp3",       0.01, 3.0, 5, 3.0, 0.6, 0.05,3.0, 5, 2.0),
    ("N14_FULL_sp10",      0.01,10.0, 5,10.0, 2.0, 0.05,3.0, 5, 2.0),

    # Hibrid: V-D + V-A petit (defensa doble per posicio)
    ("N15_VD5_VA003",      0.01, 5.0, 5, 5.0, 1.0, 0.03,0.0, 5, 2.0),
    ("N16_VD5_VA005",      0.01, 5.0, 5, 5.0, 1.0, 0.05,0.0, 5, 2.0),

    # V-D mes agresiu
    ("N17_VD2_VB2",        0.01, 5.0, 5, 5.0, 1.0, 0.0, 0.0, 2, 2.0),
    ("N18_VD10_VB2",       0.01, 5.0, 5, 5.0, 1.0, 0.0, 0.0,10, 2.0),

    # Ultra-defensiu nou
    ("N19_ULTRA",          0.01, 5.0, 5, 5.0, 0.5, 0.05,2.0, 3, 1.5),
    ("N20_ULTRA2",         0.01, 5.0, 5, 5.0, 0.25,0.03,2.0, 3, 1.0),
]

for tid, lot, sp, lv, tp, rp, maxlot, emer, sl, harvest in TESTS:
    content = TEMPLATE.format(tid=tid, lot=lot, sp=sp, lv=lv, tp=tp, rp=rp,
                              maxlot=maxlot, emer=emer, sl=sl, harvest=harvest)
    (TESTS_DIR / f"{tid}.ini").write_text(content)

print(f"Generated {len(TESTS)} N tests (nocturn)")
