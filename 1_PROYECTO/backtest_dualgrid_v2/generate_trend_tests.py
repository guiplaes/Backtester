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
Model=0
FromDate={fr}
ToDate={to}
ForwardMode=0
Report=report_{tid}
ReplaceReport=1
ShutdownTerminal=1
Visual=0

[TesterInputs]
InpLotSize=0.02
InpLevelSpacingUSD=30.0
InpLevelsEachSide=5
InpFluidTPUSD=30.0
InpUseVirtualTP=true
InpResetEquityPct=1.0
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
InpPositionSLSteps=0
InpHarvestWinnerPct=2.0
InpTrendFilterEnabled={enabled}
InpTrendTF={tf}
InpTrendFastEMA={fast}
InpTrendSlowEMA={slow}
InpTrendThresholdPct=0.1
InpTrendAllowCounter={counter}
"""

PERIODS = [
    ("feb26", "2026.02.01", "2026.02.28"),
    ("mar26", "2026.03.01", "2026.03.31"),
    ("oct25", "2025.10.01", "2025.10.31"),
    ("nov24", "2024.11.01", "2024.11.29"),
]

# (id, enabled, tf, fast, slow, counter)
CONFIGS = [
    ("T0_NoFilter",      "false", "PERIOD_H4",  "20", "50", "false"),  # baseline (W1)
    ("T1_H4_2050",       "true",  "PERIOD_H4",  "20", "50", "false"),  # H4 standard
    ("T2_H1_2050",       "true",  "PERIOD_H1",  "20", "50", "false"),  # H1 standard
    ("T3_D1_2050",       "true",  "PERIOD_D1",  "20", "50", "false"),  # D1 (slow)
    ("T4_H4_2050_counter","true", "PERIOD_H4",  "20", "50", "true"),   # H4 + allow counter
    ("T5_H4_1030",       "true",  "PERIOD_H4",  "10", "30", "false"),  # H4 mes rapid
    ("T6_H1_50100",      "true",  "PERIOD_H1",  "50", "100","false"),  # H1 mes lent
]

for pid, fr, to in PERIODS:
    for cid, en, tf, fast, slow, ctr in CONFIGS:
        tid = f"TR_{pid}_{cid}"
        content = TEMPLATE.format(tid=tid, fr=fr, to=to, enabled=en, tf=tf, fast=fast, slow=slow, counter=ctr)
        (TESTS_DIR / f"{tid}.ini").write_text(content)
print(f"Generated {len(PERIODS)*len(CONFIGS)} TR tests")
