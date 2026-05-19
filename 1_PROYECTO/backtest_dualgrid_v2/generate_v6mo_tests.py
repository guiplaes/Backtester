"""Validacio 6 mesos M5 Model=1 de les top configs V-B v3."""
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

# Validacio 6 mesos top V-B v3 configs
# (id, sp, lv, tp, rp, maxlot, sl, harvest)
TESTS = [
    ("F01_6mo_h15",  5.0, 5, 5.0, 1.0, 0.0, 0, 1.5),
    ("F02_6mo_h20",  5.0, 5, 5.0, 1.0, 0.0, 0, 2.0),
    ("F03_6mo_h30",  5.0, 5, 5.0, 1.0, 0.0, 0, 3.0),
    ("F04_6mo_h10",  5.0, 5, 5.0, 1.0, 0.0, 0, 1.0),
]

for tid, sp, lv, tp, rp, maxlot, sl, harvest in TESTS:
    content = TEMPLATE.format(tid=tid, sp=sp, lv=lv, tp=tp, rp=rp,
                              maxlot=maxlot, sl=sl, harvest=harvest)
    (TESTS_DIR / f"{tid}.ini").write_text(content)

print(f"Generated {len(TESTS)} F tests (6 mesos)")
