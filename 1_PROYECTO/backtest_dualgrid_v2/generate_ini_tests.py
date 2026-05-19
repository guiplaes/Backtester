"""Genera fitxers .ini per a MT5 Strategy Tester batch."""
import os
from pathlib import Path

TESTS_DIR = Path(r"C:\Users\Administrator\Desktop\MT4 Claude\1_PROYECTO\backtest_dualgrid_v2\tests")
TESTS_DIR.mkdir(exist_ok=True)

TEMPLATE = """[Tester]
Expert=DualGridEA_v2_Reset
Symbol={symbol}
Period=M1
Deposit={deposit}
Currency=USC
ExecutionMode=0
OptimizationMode=0
Model=4
FromDate={from_date}
ToDate={to_date}
ForwardMode=0
Report={report_name}
ReplaceReport=1
ShutdownTerminal=1
Visual=0

[TesterInputs]
InpLotSize={lot}
InpLevelSpacingUSD={spacing}
InpLevelsEachSide={levels}
InpFluidTPUSD={tp}
InpUseVirtualTP=true
InpResetEquityPct={reset_pct}
InpConsolidateAnchor=true
InpMinSecBetweenResets=5
InpMaxDrawdownPct=20.0
InpMaxSpreadPoints=80
InpAvoidWeekend=true
InpMinPositionDistance=0.0
InpMagicNumber=88888
InpComment=DGv2R_BT
InpDrawDashboard=false
InpVerboseLog=false
InpHeartbeatSec=0
InpHeartbeatFile=
InpResetStateOnInit=true
InpCleanSlateOnInit=true
InpStartBalanceOverride=0.0
InpUpdateBaselineOnReset=true
"""

# Format: (id, label, spacing, levels, tp, reset_pct, purpose)
TESTS = [
    ("01_baseline", "Baseline current",   1.0,  5, 1.0, 0.25, "Configuracio actual del bot live"),
    ("02_tight",    "Tight grid",         0.5, 10, 0.5, 0.10, "Grid molt dens, threshold baix"),
    ("03_wide",     "Wide grid",          5.0, 10, 5.0, 1.00, "Grid ample, threshold alt"),
    ("04_many",     "Many narrow lvls",   0.5, 20, 0.5, 0.25, "Molts nivells densament"),
    ("05_few_wide", "Few wide lvls",      2.0,  5, 2.0, 0.50, "Pocs nivells, espaiats"),
    ("06_low_reset",  "Low reset",        1.0,  5, 1.0, 0.05, "Reset agressiu (com pre-fix)"),
    ("07_high_reset", "High reset",       1.0,  5, 1.0, 1.00, "Reset conservador (compounding)"),
    ("08_bigTP_smSpac","Big TP small sp.",0.5, 10, 2.0, 0.50, "TP gran, grid dens"),
    ("09_smTP_bigSpac","Small TP big sp.",2.0,  5, 0.5, 0.25, "TP petit, grid ample"),
    ("10_mid",       "Mid-range",         2.0, 10, 2.0, 0.50, "Mig en tots els eixos"),
    ("11_lvl10",     "10 levels",         1.0, 10, 1.0, 0.25, "Mateix que baseline pero amb 10 lvl"),
    ("12_lvl20",     "20 levels",         1.0, 20, 1.0, 0.25, "Lots de levels, baseline params"),
    ("13_sp3",       "Spacing 3$",        3.0, 10, 3.0, 0.50, "Grid mes ample que current"),
    ("14_tp05_r01",  "TP 0.5 reset 0.1",  1.0,  5, 0.5, 0.10, "Variant escratch agresiva"),
    ("15_tp2_r05",   "TP 2 reset 0.5",    1.0,  5, 2.0, 0.50, "Variant conservadora"),
    ("16_sp1_lvl15", "Sp 1 lvl 15",       1.0, 15, 1.0, 0.30, "Density mitjana, threshold mig"),
    ("17_sp05_tp1",  "Sp 0.5 TP 1",       0.5,  5, 1.0, 0.25, "Asimetric: TP > spacing"),
    ("18_sp2_lvl3",  "Sp 2 lvl 3",        2.0,  3, 2.0, 0.30, "Pocs levels, threshold baix"),
    ("19_aggressive","Aggressive scratch",0.5, 15, 0.3, 0.05, "Tot al maxim agressiu"),
    ("20_conservative","Very conservative",3.0,5, 3.0, 1.50, "Tot conservador, espera grans"),
]

SYMBOL = "XAUUSD-VIP"
DEPOSIT = 50000
FROM_DATE = "2026.04.15"
TO_DATE   = "2026.05.15"

for tid, label, spacing, levels, tp, reset_pct, purpose in TESTS:
    content = TEMPLATE.format(
        symbol=SYMBOL,
        deposit=DEPOSIT,
        from_date=FROM_DATE,
        to_date=TO_DATE,
        report_name=f"report_{tid}",
        lot=0.01,
        spacing=spacing,
        levels=levels,
        tp=tp,
        reset_pct=reset_pct,
    )
    path = TESTS_DIR / f"{tid}.ini"
    path.write_text(content)

# Genera README
readme = "# MT5 Strategy Tester - Bateria de tests DualGridEA v2\n\n"
readme += "Cada `.ini` es un test independent. Per executar al MT5:\n\n"
readme += "1. Obre MT5 demo (on tens el v2)\n"
readme += "2. View -> Strategy Tester (o Ctrl+R)\n"
readme += "3. Manually configura (o usa Tester -> View -> Settings -> Load Config):\n"
readme += "   - Expert: DualGridEA_v2_Reset\n"
readme += "   - Symbol: XAUUSD-VIP\n"
readme += "   - Period: M1\n"
readme += "   - From: 2026.04.15  To: 2026.05.15\n"
readme += "   - Modeling: Every tick based on real ticks (o Every tick)\n"
readme += "   - Deposit: 50000 USC\n"
readme += "   - Inputs: copia els valors de l'ini corresponent\n"
readme += "4. Start. Cada test 5-15 min depenent del modeling.\n\n"
readme += "Resultats: Tester guarda report dins MT5. Anota Final Balance + Max DD.\n\n"
readme += "## Llista de tests\n\n"
readme += "| ID | Label | Spacing | Levels | TP | Reset% | Propòsit |\n"
readme += "|---|---|---|---|---|---|---|\n"
for tid, label, sp, lv, tp, rp, purpose in TESTS:
    readme += f"| {tid} | {label} | {sp} | {lv} | {tp} | {rp}% | {purpose} |\n"

readme += "\n## Quins corres primer (ordre recomanat)\n\n"
readme += "1. `01_baseline` — referencia, sap el que dona\n"
readme += "2. `06_low_reset` — comprovar que el bug del bucle infinit no torna (deuria funcionar amb fix nou)\n"
readme += "3. `07_high_reset` — l'oposat, conservador\n"
readme += "4. `02_tight` vs `03_wide` — quin extrem es millor?\n"
readme += "5. `19_aggressive` vs `20_conservative` — eixos al maxim\n"
readme += "6. Resta segons resultats anteriors\n\n"
readme += "## Per cada test, anota:\n\n"
readme += "- Final Balance ($)\n"
readme += "- Net Profit ($)\n"
readme += "- Max Drawdown (% + $)\n"
readme += "- Profit Factor\n"
readme += "- Total Trades\n"
readme += "- Total Resets (mira als log Experts)\n"
readme += "- Kill switch? (yes/no)\n\n"
readme += "Pots usar la taula `results_template.csv` per omplir.\n"

(TESTS_DIR / "README.md").write_text(readme)

# Genera CSV template per resultats
csv = "test_id,label,spacing,levels,tp,reset_pct,final_balance,net_profit,max_dd_pct,max_dd_usd,profit_factor,total_trades,total_resets,killed,notes\n"
for tid, label, sp, lv, tp, rp, _ in TESTS:
    csv += f"{tid},{label},{sp},{lv},{tp},{rp},,,,,,,,,\n"
(TESTS_DIR / "results_template.csv").write_text(csv)

print(f"Generated {len(TESTS)} .ini files in {TESTS_DIR}")
print("README.md + results_template.csv created")
