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
InpLevelSpacingUSD={sp}
InpLevelsEachSide=5
InpFluidTPUSD={tp}
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
InpPositionSLSteps={sl}
InpHarvestWinnerPct=2.0
"""
PERIODS = [
    ("feb26", "2026.02.01", "2026.02.28"),
    ("nov24", "2024.11.01", "2024.11.29"),
    ("mar26", "2026.03.01", "2026.03.31"),
]
CONFIGS = [
    ("W1_sp30tp30",   30.0, 30.0,  0),
    ("W2_sp50tp50",   50.0, 50.0,  0),
    ("W3_sp30tp30VD10", 30.0, 30.0, 10),
    ("W4_sp15tp30",   15.0, 30.0,  0),
    ("W5_sp10tp50",   10.0, 50.0,  0),
    ("W6_sp100tp100", 100.0,100.0, 0),
    ("W7_sp50tp100",  50.0, 100.0, 0),
]
for pid, fr, to in PERIODS:
    for cid, sp, tp, sl in CONFIGS:
        tid = f"WT_{pid}_{cid}"
        content = TEMPLATE.format(tid=tid, fr=fr, to=to, sp=sp, tp=tp, sl=sl)
        (TESTS_DIR / f"{tid}.ini").write_text(content)
print(f"Generated {len(PERIODS)*len(CONFIGS)} WT tests")
