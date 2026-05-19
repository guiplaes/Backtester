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
InpFluidTPUSD={sp}
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
InpEquityGapResetPct={egr_gap}
InpEquityGapMinProfitPct={egr_prof}
InpEquityGapMinSec=30
"""

PERIODS = [
    ("feb26", "2026.02.01", "2026.02.28"),
    ("mar26", "2026.03.01", "2026.03.31"),
    ("oct25", "2025.10.01", "2025.10.31"),
    ("nov24", "2024.11.01", "2024.11.29"),
]

# (id, sp, sl, egr_gap, egr_prof)
CONFIGS = [
    # Base sp=5 V-D=5 + EGR variants
    ("E1_sp5_g5p0",   5,  5,  5.0, 0.0),    # gap 5%, qualsevol equity
    ("E2_sp5_g5p3",   5,  5,  5.0, 3.0),    # gap 5%, profit >= 3%
    ("E3_sp5_g3p1",   5,  5,  3.0, 1.0),    # gap 3%, profit >= 1%
    ("E4_sp5_g7p0",   5,  5,  7.0, 0.0),    # gap 7%, qualsevol
    ("E5_sp5_g5pn2",  5,  5,  5.0, -2.0),   # gap 5%, acepta -2% loss
    # Wider grid + EGR
    ("E6_sp10_g5p0",  10, 5,  5.0, 0.0),
    ("E7_sp10_g5p3",  10, 5,  5.0, 3.0),
    # Sense V-D + EGR (deixar EGR ser l'unic stop)
    ("E8_sp5_noVD_g5p0",  5, 0, 5.0, 0.0),
    ("E9_sp10_noVD_g5p3", 10, 0, 5.0, 3.0),
]

for pid, fr, to in PERIODS:
    for cid, sp, sl, gg, gp in CONFIGS:
        tid = f"EGR_{pid}_{cid}"
        content = TEMPLATE.format(tid=tid, fr=fr, to=to, sp=sp, sl=sl, egr_gap=gg, egr_prof=gp)
        (TESTS_DIR / f"{tid}.ini").write_text(content)

print(f"Generated {len(PERIODS)*len(CONFIGS)} EGR tests")
