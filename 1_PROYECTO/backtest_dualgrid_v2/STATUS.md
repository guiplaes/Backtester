# Estat actual de la investigació DualGrid (2026-05-19)

## TL;DR

Grid bidireccional sobre XAU **no funciona amb config tradicional en tics reals**.
Model=1 inflava sistemàticament resultats. **Wide grid (sp=$30-$50) sense V-D** és l'únic que sobreviu en Feb 2026 (high-vol).
Nou mecanisme **Progressive Trim** afegit al EA, pendent validació completa.

## Cronologia clau

1. **Model=1 era enganyós**: tots els primers backtests (Z01, N11, VarB) eren amb Model=1 (1-min OHLC) que infla profit 200-300%
2. **Live N11 2y (Model=1)**: +$1,844 USD però sospitós d'inflació
3. **Validació Model=4 (real ticks) Feb 2026**: N11 dóna **-$10,000 DD 20%** (kill switch)
4. **Confirmació**: Model=0 ≈ Model=4 (per evitar 5-10× temps de Model=4)
5. **TK batch 32 tests**: CAP config tradicional supera 0 en cap mes
6. **WT batch (wide grid)**: **W1 sp=$30 sense V-D = +$2,025 Feb 2026 ★** primer positiu en tic
7. **EGR nou** (Equity Gap Reset, tanca tot si gap>X% i equity>+Y%): no millora sols
8. **Progressive Trim nou** (tanca pitjor pos si gap>X%, grid continua): **EN VALIDACIÓ**

## Resultats Feb 2026 (mes high-vol, el matador)

| Config | Profit | BalDD | EqDD | Trades | Status |
|---|---|---|---|---|---|
| **WT_W1 sp=$30 noVD** | **+$2,025** | 17.6% | 11.6% | 441 | ★★★ MILLOR |
| EGR_sp10_g1_rp05 (agressiu) | +$1,526 | 10.0% | 18.1% | 3,947 | ★★ |
| WT_W2 sp=$50 noVD | +$922 | 12.5% | 7.4% | 166 | ★ |
| WT_W3 sp=$30 V-D=10 | +$566 | 9.2% | 11.2% | 419 | |
| EGR_W2 (sp=$50 + EGR) | -$1,636 | 11.9% | 7.7% | 170 | ✗ EGR ESTRAGA |
| Tota la resta TK (8 configs tradicionals) | -$10k DD 20-30% | | | | KILL |

## Configuració del millor (W1)

```
InpLotSize           = 0.02 (standard demo) / 2.0 (cent live)
InpLevelSpacingUSD   = 30.0
InpLevelsEachSide    = 5
InpFluidTPUSD        = 30.0
InpResetEquityPct    = 1.0
InpMaxLotPerSide     = 0.0 (V-A OFF)
InpEmergencyResetLossPct = 0.0 (V-C OFF)
InpPositionSLSteps   = 0.0 (V-D OFF) ← CLAU
InpHarvestWinnerPct  = 2.0
InpEquityGapResetPct = 0.0 (EGR OFF)
InpProgressiveTrimGapPct = 0.0 (TRIM OFF)
```

## Què queda per fer

1. **Confirmar W1 en altres mesos** (Mar 26 downtrend, Oct 25, Nov 24)
   - Si W1 positiu en tots → estratègia robusta
   - Si només Feb 26 → sort, no estratègia
2. **Validar Progressive Trim** sobre W1
   - Test TRIM_W1_feb26 corrent (slot 1)
   - Hauria de reduir DD de 17% a 10-12% sense matar profit
3. **EGR batch** (36 tests) pendent
4. **Si W1 sobreviu 4 períodes** → validar 6 mesos Model=4
5. **Si tot fail** → necessari modificar EA amb filtre direccional

## Periodes test usats consistentment

- Feb 2026: 2026.02.01 → 2026.02.28 (high-vol uptrend $873 range, $383 trend up)
- Mar 2026: 2026.03.01 → 2026.03.31 (high-vol downtrend $1319 range, $600 trend down)
- Oct 2025: 2025.10.01 → 2025.10.31 (mid-vol $559 range)
- Nov 2024: 2024.11.01 → 2024.11.29 (low-vol $221 range)

## Mecanismes EA actuals (DualGridEA_v2_Reset.mq5)

### Grid base
- Bidireccional (BUYs i SELLs alhora)
- Spacing fix entre nivells (`InpLevelSpacingUSD`)
- TP per posició (`InpFluidTPUSD`)
- Reset costat: tanca costat perdedor si `captured + side_flot > X% baseline`

### Vàlvules (anti-trend)
- **V-A** `InpMaxLotPerSide`: cap d'exposure per costat
- **V-B** `InpHarvestWinnerPct`: captura excés profit guanyador
- **V-C** `InpEmergencyResetLossPct`: kill costat si perd X%
- **V-D** `InpPositionSLSteps`: SL per posicio en passos del grid

### Nous (afegits 2026-05-19)
- **EGR** `InpEquityGapResetPct` + `InpEquityGapMinProfitPct`: tanca TOT si gap balance-equity > X% i equity > +Y%
- **Progressive Trim** `InpProgressiveTrimGapPct`: tanca POSICIO MES NEGATIVA si gap > X% (no closes all, grid continua)

## Per continuar la investigació

```bash
# 1. Setup slots (un cop)
./1_PROYECTO/backtest_dualgrid_v2/SETUP_REMOTE.bat

# 2. Llançar batch
cd 1_PROYECTO/backtest_dualgrid_v2
python run_batch_parallel.py "TRIM_*.ini"

# 3. Analitzar
python show_results.py
```

## Files clau

- `MT5/MQL5/Experts/DualGridEA_v2_Reset.mq5` — EA actual amb EGR + Trim
- `MT5/MQL5/Experts/DualGridEA_v2_Reset.ex5` — compilat
- `1_PROYECTO/backtest_dualgrid_v2/run_batch_parallel.py` — runner paral·lel
- `1_PROYECTO/backtest_dualgrid_v2/tests/` — INIs per llançar
- `1_PROYECTO/backtest_dualgrid_v2/results.csv` — històric resultats
- `1_PROYECTO/backtest_dualgrid_v2/BREAKTHROUGH_P04.md` — anàlisi millor demo Model=1 (obsolet — inflat)
- `1_PROYECTO/backtest_dualgrid_v2/REMOTE_SETUP.md` — instruccions setup nou PC

## Avisos importants per al Claude del nou PC

1. **MAI confiar Model=1**: tot ha de validar-se en Model=0 o Model=4
2. **El compte real (24898681 VTMarkets-Live 3) NO autentica via INI portable** — només funciona si l'usuari està logejat manualment
3. **El compte demo (1110830 VTMarkets-Demo) funciona perfectament**
4. **Cap config tradicional supera 0 en Feb 2026 Model=0/4 — necessites wide grid sp≥$30**
5. **EGR + V-D combinats potser maten l'estratègia**: aïllar mecanismes en tests
6. **El compte XAUUSD-VIPc és cent (1 lot = 1 oz)**, XAUUSD-VIP és standard (1 lot = 100 oz). Ajustar lot accordingly
