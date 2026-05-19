"""Genera tests amb V-B v3 (cap+compensa) - 1 mes per cribar rapid + 6 mesos finalists."""
from pathlib import Path
TESTS_DIR = Path(r"C:\Users\Administrator\Desktop\MT4 Claude\1_PROYECTO\backtest_dualgrid_v2\tests")

TEMPLATE_1MO = """[Tester]
Expert=DualGridEA_v2_Reset
Symbol=XAUUSD-VIP
Period=M5
Deposit=50000
Currency=USD
ExecutionMode=0
OptimizationMode=0
Model=1
FromDate=2026.04.15
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

# Cribage rapid 1 mes — 40 configs
# (id, sp, lv, tp, rp, maxlot, sl_steps, harvest_pct)
TESTS = []

# Block 1: V-B sola, varia threshold (sp=5 baseline)
for h in [0.5, 1.0, 1.5, 2.0, 3.0, 5.0]:
    TESTS.append((f"Z01_sp5_h{int(h*10):02d}", 5.0, 5, 5.0, 1.0, 0.0, 0, h))

# Block 2: V-B + V-A combinacions (sp=5)
for h in [1.0, 2.0, 3.0]:
    for a in [0.05, 0.10, 0.20]:
        TESTS.append((f"Z02_sp5_A{int(a*100):02d}_h{int(h*10):02d}", 5.0, 5, 5.0, 1.0, a, 0, h))

# Block 3: V-B + V-D + V-A (full combo sp=5)
for h in [1.0, 2.0]:
    for sl in [3, 5]:
        TESTS.append((f"Z03_sp5_A05_SL{sl}_h{int(h*10):02d}", 5.0, 5, 5.0, 1.0, 0.05, sl, h))

# Block 4: Spacings 1, 2, 3 amb V-B
for sp in [1.0, 2.0, 3.0]:
    for h in [1.0, 2.0]:
        sp_label = str(int(sp))
        TESTS.append((f"Z04_sp{sp_label}_h{int(h*10):02d}", sp, 5, sp, max(0.25, sp*0.2), 0.05, 0, h))

# Block 5: Wide grids (sp 7-15) amb V-B
for sp in [7.0, 10.0, 15.0]:
    TESTS.append((f"Z05_sp{int(sp):02d}_h2", sp, 5, sp, sp*0.2, 0.0, 0, 2.0))

for tid, sp, lv, tp, rp, maxlot, sl, harvest in TESTS:
    content = TEMPLATE_1MO.format(tid=tid, sp=sp, lv=lv, tp=tp, rp=rp, maxlot=maxlot, sl=sl, harvest=harvest)
    (TESTS_DIR / f"{tid}.ini").write_text(content)

print(f"Generated {len(TESTS)} Z tests (cribage 1 mes)")
