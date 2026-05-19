"""Tests d'escalat de grid per volatilitat: provar tight grid en low-vol i wide grid en high-vol."""
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
InpLevelSpacingUSD={sp}
InpLevelsEachSide=5
InpFluidTPUSD={tp}
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
InpPositionSLSteps=3
InpHarvestWinnerPct=1.5
"""

# Periodes amb diferents vola. Ordenat de menys a més vola
PERIODS_LOW = [
    ("lv1_jun24", "2024.06.03", "2024.06.28"),   # range $98/mes
    ("lv2_jul24", "2024.07.01", "2024.07.31"),   # range $161
    ("lv3_nov24", "2024.11.01", "2024.11.29"),   # range $221
]

PERIODS_HIGH = [
    ("hv1_oct25", "2025.10.01", "2025.10.31"),   # range $559
    ("hv2_feb26", "2026.02.01", "2026.02.28"),   # range $873
]

# Spacing variants (TP=sp per coherencia, V-D=3 steps sempre)
SP_VARIANTS = [
    ("sp1",   1.0,  1.0),    # tight x5
    ("sp2",   2.0,  2.0),    # tight x2.5
    ("sp3",   3.0,  3.0),    # tight x1.7
    ("sp5",   5.0,  5.0),    # baseline (VarB actual)
    ("sp7",   7.0,  7.0),    # wide x1.4
    ("sp10", 10.0, 10.0),    # wide x2
    ("sp15", 15.0, 15.0),    # wide x3
]

# Low-vol: provar tots (especialment tight)
for pid, fr, to in PERIODS_LOW:
    for svar, sp, tp in SP_VARIANTS:
        tid = f"VS_{pid}_{svar}"
        content = TEMPLATE.format(tid=tid, from_date=fr, to_date=to, sp=sp, tp=tp)
        (TESTS_DIR / f"{tid}.ini").write_text(content)

# High-vol: provar tots (especialment wide)
for pid, fr, to in PERIODS_HIGH:
    for svar, sp, tp in SP_VARIANTS:
        tid = f"VS_{pid}_{svar}"
        content = TEMPLATE.format(tid=tid, from_date=fr, to_date=to, sp=sp, tp=tp)
        (TESTS_DIR / f"{tid}.ini").write_text(content)

total = (len(PERIODS_LOW) + len(PERIODS_HIGH)) * len(SP_VARIANTS)
print(f"Generated {total} VolScale tests (5 periodes x 7 spacings)")
