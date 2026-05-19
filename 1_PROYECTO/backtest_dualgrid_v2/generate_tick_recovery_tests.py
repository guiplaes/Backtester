"""Recerca nocturna: configs que sobrevisquin tics (Model=0).
Hipotesis: V-D actual mata l'estrategia en tics. Provem variants.
"""
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
InpLevelsEachSide={lvl}
InpFluidTPUSD={tp}
InpUseVirtualTP=true
InpResetEquityPct={rp}
InpConsolidateAnchor=true
InpMinSecBetweenResets=5
InpMaxDrawdownPct=20.0
InpMaxSpreadPoints=200
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

# Periodes per validar (mes representatius):
PERIODS = [
    ("feb26", "2026.02.01", "2026.02.28"),  # high-vol uptrend ($873 range)
    ("nov24", "2024.11.01", "2024.11.29"),  # low-vol slight bear ($221)
    ("oct25", "2025.10.01", "2025.10.31"),  # mid-high vol ($559)
    ("mar26", "2026.03.01", "2026.03.31"),  # high-vol downtrend ($1319)
]

# Hipotesis a provar:
# (id, sp, lvl, tp, rp, maxlot, emer, sl, harvest, descr)
CONFIGS = [
    # H1: NO V-D (deixar perdedors flotar, reset captura)
    ("H1_noVD",       5.0, 5, 5.0, 1.0, 0.0, 0.0,  0, 2.0),
    # H2: V-D molt ample (SL 20 nivells = $100)
    ("H2_VD20",       5.0, 5, 5.0, 1.0, 0.0, 0.0, 20, 2.0),
    # H3: Wide grid sp=10 + V-D=5 (SL $50)
    ("H3_sp10_VD5",  10.0, 5,10.0, 2.0, 0.0, 0.0,  5, 2.0),
    # H4: Wide grid sp=10 + V-C kill 5%
    ("H4_sp10_VC5",  10.0, 5,10.0, 2.0, 0.0, 5.0,  0, 2.0),
    # H5: Wide grid sp=20 + V-D=5 (SL $100)
    ("H5_sp20_VD5",  20.0, 5,20.0, 4.0, 0.0, 0.0,  5, 2.0),
    # H6: V-A cap exposure 0.05 + V-D=5
    ("H6_VA005_VD5",  5.0, 5, 5.0, 1.0, 0.05,0.0,  5, 2.0),
    # H7: Full defense (V-A + V-C + V-D wide)
    ("H7_FULL_def",   5.0, 5, 5.0, 1.0, 0.05,5.0, 10, 2.0),
    # H8: Reset baix + No V-D + V-B agressiu
    ("H8_rp025_noVD", 5.0, 5, 5.0, 0.25,0.0, 0.0,  0, 1.0),
]

for pid, fr, to in PERIODS:
    for cid, sp, lvl, tp, rp, maxlot, emer, sl, harvest in CONFIGS:
        tid = f"TK_{pid}_{cid}"
        content = TEMPLATE.format(tid=tid, fr=fr, to=to, sp=sp, lvl=lvl, tp=tp, rp=rp,
                                  maxlot=maxlot, emer=emer, sl=sl, harvest=harvest)
        (TESTS_DIR / f"{tid}.ini").write_text(content)

total = len(PERIODS) * len(CONFIGS)
print(f"Generated {total} TK tests Model=0 (8 configs x 4 periodes)")
