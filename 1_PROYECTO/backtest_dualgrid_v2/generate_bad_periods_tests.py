"""Tests targeted en periodes dolents identificats amb diferents variants."""
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
InpLevelSpacingUSD=5.0
InpLevelsEachSide={lvl}
InpFluidTPUSD={tp}
InpUseVirtualTP=true
InpResetEquityPct={rp}
InpConsolidateAnchor=true
InpMinSecBetweenResets=5
InpMaxDrawdownPct=20.0
InpMaxSpreadPoints={spread}
InpAvoidWeekend=true
InpMinPositionDistance={mindist}
InpMagicNumber=88888
InpResetStateOnInit=true
InpCleanSlateOnInit=true
InpUpdateBaselineOnReset=true
InpMaxLotPerSide={maxlot}
InpEmergencyResetLossPct=0.0
InpPositionSLSteps={sl}
InpHarvestWinnerPct={harvest}
"""

PERIODS = [
    ("p1_jun24",  "2024.06.03", "2024.06.14"),  # 06.07 loss
    ("p2_ago24",  "2024.08.01", "2024.08.10"),  # 08.05 loss
    ("p3_nov24",  "2024.11.05", "2024.11.15"),  # 11.07, 11.11 losses
    ("p4_dec24",  "2024.12.15", "2024.12.22"),  # 12.18 loss
    ("p5_mar25",  "2025.03.10", "2025.03.18"),  # 03.13 loss
    ("p6_may25",  "2025.05.05", "2025.05.22"),  # multiple losses
]

VARIANTS = [
    # (suffix, rp, mindist, lvl, tp, spread, maxlot, sl, harvest)
    ("A_baseline", 1.0, 0.0, 5, 5.0, 200, 0.0,  5, 2.0),  # N11 baseline
    ("B_tight",    0.5, 0.0, 5, 5.0, 200, 0.0,  3, 1.5),  # V-D=3, reset 0.5, V-B 1.5
    ("C_full",     0.5, 2.0, 4, 5.0,  50, 0.10, 3, 1.5),  # +V-A +mindist +less lvl +tighter spread
    ("D_tpfast",   0.5, 0.0, 5, 4.0,  50, 0.0,  3, 1.5),  # TP $4 + tight
]

for pid, from_date, to_date in PERIODS:
    for vsuffix, rp, mindist, lvl, tp, spread, maxlot, sl, harvest in VARIANTS:
        tid = f"BP_{pid}_{vsuffix}"
        content = TEMPLATE.format(tid=tid, from_date=from_date, to_date=to_date,
                                  rp=rp, mindist=mindist, lvl=lvl, tp=tp, spread=spread,
                                  maxlot=maxlot, sl=sl, harvest=harvest)
        (TESTS_DIR / f"{tid}.ini").write_text(content)

print(f"Generated {len(PERIODS)*len(VARIANTS)} bad-period tests")
