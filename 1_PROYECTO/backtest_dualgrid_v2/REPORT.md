# DualGridEA v2 — Resultats Backtest (overnight 2026-05-15 → 16)

## CONCLUSIÓ BRUTAL (6 mesos M5)

**🚨 El v2 com està dissenyat NO ÉS VIABLE en mercat trendós.**

- 20 configs testats sobre **6 mesos de XAU** (2025-11-16 → 2026-05-15)
- Període: XAU $3000 → $4889 = **+63% trend sostingut**
- **19/20 configs van disparar kill switch (-20%) i perdre $10k**
- **Només 1 config va sobreviure**: M25_sp10_lvl5_r2 (+$4,187 amb equity DD 77.96%)

El problema NO és la configuració. **És el disseny de l'estratègia**. Els grids bidireccionals **moren en trends sostinguts**. Falten **vàlvules de protecció**.

## CONTRAST AMB 30-DIES (per què semblava funcionar)

| Config | M1 30d (abril-maig 2026) | M5 6mo (nov 25 → maig 26) |
|---|---|---|
| 01_baseline (sp=1, r=0.25) | -$10,193 (loss already) | -$10,009 (loss) |
| 03_wide (sp=5, r=1.0) | **+$7,170 profit** | **-$10,045 LOSS** |
| 07_high_reset (sp=1, r=1.0) | **+$42,553 profit** | -$10,303 LOSS |
| 02_tight (sp=0.5, r=0.1) | **+$59,328 profit** | -$10,636 LOSS |
| 17_sp05_tp1 | +$72,024 profit | -$11,009 LOSS |
| 20_conservative (sp=3, r=1.5) | +$10,348 profit | -$10,071 LOSS |

**Conclusió**: el rang abril-maig 2026 era **mercat oscil·lant**. Els profits eren artifacts d'aquell període concret. Quan es validen contra **6 mesos amb trend**, **TOTS perden**.

## EL ÚNIC SUPERVIVENT

**M25_sp10_lvl5_r2**:
- Spacing: 10 USD (molt ampli)
- Levels: 5
- TP: 10 USD
- Reset threshold: 2.0%
- **Profit 6 mesos: +$4,187 (+8.4%)**
- **Balance DD: 19.99%** (just sota kill switch)
- ⚠️ **Equity DD: 77.96%** (durant el trend, quasi liquidat)

És "viable" per pèl. **Un altre trend més fort i moria també.**

## CAUSA ROOT

Els grids bidireccionals tenen una **fragilitat estructural** davant de trends sostinguts:
1. Banda contra el trend acumula pèrdues sense reset (no hi ha cushion per realitzar-les)
2. Banda a favor del trend genera pocs TPs (preu se'n va lluny del seu range)
3. Balance erosiona → kill switch fires → joc acabat

## QUÈ FALTA AL V2 (vàlvules urgents)

Sense aquestes el bot **no és viable** per a mercats reals:

### Vàlvula A — Hard cap d'exposició per costat
Si un costat acumula més de X lots oberts → pausa noves obertures d'aquell costat. Evita acumulació il·limitada.

### Vàlvula B — Trend detector
Mida tendència via EMA slope o ATR direcció. Si trend fort detectat:
- Pausa el costat contra-tendència
- O harvest del costat a favor més agressivament

### Vàlvula C — Reset asimètric per nivell de pèrdua
Si la pèrdua d'un costat supera Y% del balance, força un reset parcial AMB AVORT (no esperar trigger normal). Realitza pèrdua abans que sigui catastròfica.

### Vàlvula D — Pause direccional (com NO obrir contra-trend)
Detector simple: si EMA(50) > EMA(200) pendent positiva → no col·loquis SELL pendents. Només deixa LONGs operar. Inverteix en downtrend.

## RECOMANACIÓ TÈCNICA

**No continuïs amb l'EA v2 actual en demo seriós**. La probabilitat que destrueixi el compte demo en un trend és **alta** (testimoniat per 19/20 configs).

**Pla**:
1. **CURT TERMINI (avui)**: para el bot live. Tanca posicions obertes. No torna a engegar fins haver implementat almenys Vàlvula A i C.
2. **MITJÀ TERMINI (3-5 dies)**: implementa Vàlvules A+C al codi MQL5. Recompila. Re-testa el lot complet a M5 6 mesos. Compara amb aquest run actual.
3. **LLARG TERMINI**: si supera el 6-mesos test amb >+10% net i DD <30%, considera demo amb $1000 per a 2 setmanes. Si sobreviu, demo real.

## NOMBRE DE TESTS FETS AQUESTA NIT

- **20 tests base** (M1 30-dies abril-maig 2026)
- **20 tests M5 6-mesos** (novembre 2025 - maig 2026) ← **els autoritzats**
- **7 refinements** (M1 30-dies) abans d'interrupció

**Total: 47 tests reals al MT5 Strategy Tester**.

## RESULTATS COMPLETS

`results.csv` té totes les dades:
- Tests `01_*` a `20_*`: M1 30 dies (rang abril-maig 2026)
- Tests `R*`: refinements M1 30 dies (parcial)
- Tests `M*`: M5 6 mesos (novembre 2025 - maig 2026)

## DECISIÓ QUE NECESSITO

**Vols implementi les vàlvules ara?** O prefereixes:
- A) Implementar Vàlvula A+C al codi i re-testar
- B) Provar més períodes (anys diferents, més estables)
- C) Aturar el projecte v2 i investigar altra estratègia

L'opció A és **on hi ha la informació nova**. Amb les vàlvules, els resultats podrien canviar dramàticament. Sense, la decisió és evident: no és viable.
