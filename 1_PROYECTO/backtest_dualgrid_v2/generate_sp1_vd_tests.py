"""Tests sp=1 amb diferents V-D per trobar el SL optim als 5 periodes."""
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
FromDate={from_date}
ToDate={to_date}
ForwardMode=0
Report=report_{tid}
ReplaceReport=1
ShutdownTerminal=1
Visual=0

[TesterInputs]
InpLotSize=0.02
InpLevelSpacingUSD=1.0
InpLevelsEachSide=5
InpFluidTPUSD=1.0
InpUseVirtualTP=true
InpResetEquityPct=0.5
InpConsolidateAnchor=true
InpMinSecBetweenResets=5
InpMaxDrawdownPct=20.0
InpMaxSpreadPoints=200
InpAvoidWeekend=true
InpMagicNumber=88888
InpResetStateOnInit=true
InpCleanSlateOnInit=true
InpUpdateBaselineOnReset=true
InpMaxLotPerSide=0.0
InpEmergencyResetLossPct=0.0
InpPositionSLSteps={sl}
InpHarvestWinnerPct=1.5
"""

PERIODS = [
    ("p_lv_jun24", "2024.06.03", "2024.06.28"),
    ("p_lv_jul24", "2024.07.01", "2024.07.31"),
    ("p_lv_nov24", "2024.11.01", "2024.11.29"),
    ("p_hv_oct25", "2025.10.01", "2025.10.31"),
    ("p_hv_feb26", "2026.02.01", "2026.02.28"),
]

SL_VARIANTS = [2, 3, 4, 5, 7, 10]

for pid, fr, to in PERIODS:
    for sl in SL_VARIANTS:
        tid = f"SD_{pid}_VD{sl}"
        content = TEMPLATE.format(tid=tid, from_date=fr, to_date=to, sl=sl)
        (TESTS_DIR / f"{tid}.ini").write_text(content)

total = len(PERIODS) * len(SL_VARIANTS)
print(f"Generated {total} SP1+VD tests")
