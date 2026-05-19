"""Bateria B: combinacions de control de risc sobre 6 mesos (nov2025-maig2026)."""
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
InpEmergencyResetLossPct={emer}
InpPositionSLSteps={sl}
InpHarvestWinnerPct={harvest}
"""

# (id, sp, lv, tp, reset_pct, maxlot_VA, emergency_VC, sl_steps_VD, harvest_VB)
# Tots 6 mesos. Hipòtesi: trobar combinació que sobrevisqui el rally XAU
TESTS = [
    # Grup 1: V-C sol (kill costat si perd X%)
    ("B01_VC2",        5.0, 5, 5.0, 1.0, 0.0,  2.0, 0, 0.0),
    ("B02_VC3",        5.0, 5, 5.0, 1.0, 0.0,  3.0, 0, 0.0),
    ("B03_VC5",        5.0, 5, 5.0, 1.0, 0.0,  5.0, 0, 0.0),

    # Grup 2: V-B + V-C combo (captura guany + atura pèrdua)
    ("B04_VB2_VC3",    5.0, 5, 5.0, 1.0, 0.0,  3.0, 0, 2.0),
    ("B05_VB2_VC5",    5.0, 5, 5.0, 1.0, 0.0,  5.0, 0, 2.0),
    ("B06_VB3_VC5",    5.0, 5, 5.0, 1.0, 0.0,  5.0, 0, 3.0),

    # Grup 3: V-A (cap exposure) + V-B
    ("B07_VA005_VB2",  5.0, 5, 5.0, 1.0, 0.05, 0.0, 0, 2.0),
    ("B08_VA010_VB2",  5.0, 5, 5.0, 1.0, 0.10, 0.0, 0, 2.0),

    # Grup 4: V-D (SL per posicio) + V-B
    ("B09_VD5_VB2",    5.0, 5, 5.0, 1.0, 0.0,  0.0, 5, 2.0),
    ("B10_VD8_VB2",    5.0, 5, 5.0, 1.0, 0.0,  0.0, 8, 2.0),

    # Grup 5: Full defense (totes les valvules)
    ("B11_FULL_def",   5.0, 5, 5.0, 1.0, 0.05, 3.0, 5, 2.0),
    ("B12_FULL_med",   5.0, 5, 5.0, 1.0, 0.10, 5.0, 8, 3.0),

    # Grup 6: Wide spacing + risk control (menys entrades en trend)
    ("B13_sp10_VB2_VC5",  10.0, 5, 10.0, 2.0, 0.0,  5.0, 0, 2.0),
    ("B14_sp15_VB2_VC5",  15.0, 5, 15.0, 3.0, 0.0,  5.0, 0, 2.0),

    # Grup 7: sp1 (la millor en 1 mes) + V-C aggressive
    ("B15_sp1_VB2_VC3",   1.0, 5, 1.0, 0.25, 0.0, 3.0, 0, 2.0),
]

for tid, sp, lv, tp, rp, maxlot, emer, sl, harvest in TESTS:
    content = TEMPLATE.format(tid=tid, sp=sp, lv=lv, tp=tp, rp=rp,
                              maxlot=maxlot, emer=emer, sl=sl, harvest=harvest)
    (TESTS_DIR / f"{tid}.ini").write_text(content)

print(f"Generated {len(TESTS)} B tests (6 mesos, risk control hypotheses)")
