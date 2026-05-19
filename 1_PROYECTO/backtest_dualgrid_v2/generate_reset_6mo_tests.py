"""Bateria B': reset baixos sobre 6 mesos per testar conservadorisme."""
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
InpPositionSLSteps=0
InpHarvestWinnerPct={harvest}
"""

# (id, rp, maxlot, emer, harvest)
# Idea: reset baix = mes conservador, captura abans
TESTS = [
    # B16-B19: reset baixos SENSE altres valvules (validar reset agressiu per ell mateix)
    ("B16_rp015_VB2", 0.15, 0.0, 0.0, 2.0),
    ("B17_rp025_VB2", 0.25, 0.0, 0.0, 2.0),
    ("B18_rp050_VB2", 0.50, 0.0, 0.0, 2.0),
    ("B19_rp075_VB2", 0.75, 0.0, 0.0, 2.0),

    # B20-B22: reset baix + V-C (kill perdedor)
    ("B20_rp025_VB2_VC3", 0.25, 0.0, 3.0, 2.0),
    ("B21_rp050_VB2_VC3", 0.50, 0.0, 3.0, 2.0),
    ("B22_rp025_VB2_VC5", 0.25, 0.0, 5.0, 2.0),

    # B23-B24: reset baix + V-A (cap exposure)
    ("B23_rp025_VA005_VB2", 0.25, 0.05, 0.0, 2.0),
    ("B24_rp050_VA005_VB2", 0.50, 0.05, 0.0, 2.0),

    # B25: ULTRA-conservador (reset 0.15% + V-C 3% + V-A 0.05)
    ("B25_rp015_VA005_VC3_VB2", 0.15, 0.05, 3.0, 2.0),
]

for tid, rp, maxlot, emer, harvest in TESTS:
    content = TEMPLATE.format(tid=tid, rp=rp, maxlot=maxlot, emer=emer, harvest=harvest)
    (TESTS_DIR / f"{tid}.ini").write_text(content)

print(f"Generated {len(TESTS)} B' tests (reset baix 6 mesos)")
