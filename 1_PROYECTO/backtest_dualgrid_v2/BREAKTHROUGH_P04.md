# Breakthrough: P04_B04_VB_VC3

**Data:** 2026-05-16

## Resultat

| Mètrica | Valor |
|---|---|
| **Profit net 6 mesos** | +$56,310.86 |
| **Profit %** | +112.6% en 6 mesos (~+18.8%/mes) |
| **Balance DD max** | 22,795 (22.48%) |
| **Dipòsit inicial** | $50,000 |
| **Període** | 2025.11.16 → 2026.05.15 |
| **Símbol** | XAUUSD-VIP |
| **TF backtest** | M5 Model=1 |

## Configuració completa (P04_B04_VB_VC3.ini)

```
InpLotSize=0.01
InpLevelSpacingUSD=5.0       # step $5 entre nivells
InpLevelsEachSide=5          # 5 BUY + 5 SELL pendents
InpFluidTPUSD=5.0            # TP per posicio $5
InpUseVirtualTP=true
InpResetEquityPct=1.0        # reset costat amb profit captat > 1% baseline
InpConsolidateAnchor=true
InpMinSecBetweenResets=5
InpMaxDrawdownPct=20.0       # stop global
InpMaxSpreadPoints=80
InpAvoidWeekend=true
InpMagicNumber=88888
InpResetStateOnInit=true
InpCleanSlateOnInit=true
InpUpdateBaselineOnReset=true

InpMaxLotPerSide=0.0         # V-A OFF
InpEmergencyResetLossPct=3.0 # V-C ON: kill costat si perd > 3%
InpPositionSLSteps=0         # V-D OFF
InpHarvestWinnerPct=2.0      # V-B ON: captura excés guanyador > 2%
```

## Lectura

- **V-B (cap +2%)** captura profit del guanyador quan acumula > 2% baseline.
- **V-C (kill –3%)** tanca el costat perdedor si baixa de –3% baseline → realitza pèrdua, atura sagnia, re-ancora.
- Combinació: en tendència, el guanyador es captura periòdicament, el perdedor es talla quan supera 3%. Balance creix de forma controlada amb DD topat a ~22%.

## Comparativa amb altres configs (mateix 6 mesos)

| Test | Vàlvules | Profit | DD% |
|---|---|---|---|
| **P04_B04 (aquest)** | **V-B 2% + V-C 3%** | **+$56,310** | **22.48%** |
| P03_B25_ultra | V-A 0.05 + V-B 2% + V-C 3% + reset 0.15% | +$6,855 | 4.56% |
| F01-F03 | V-B sol (1.5/2/3%) | -$10k | 40-47% |
| P05_B01_VC2 | V-C –2% sol | -$10,687 | 59% |
| P06_B03_VC5 | V-C –5% sol | -$10,185 | 39% |
| H10_wide_harv5 | V-B 5% sol | -$10,299 | 44% |

**Conclusió clau**: ni V-B ni V-C en solitari serveixen. La combinació **V-B + V-C és l'única que sobreviu el rally XAU 6 mesos.**

## Pendents

- Validació amb Model=4 (real ticks) de P04
- Stress test sobre altres períodes (12 mesos, només-trend, només-rang)
- Avaluar variants properes: V-C 4%, V-B 2.5%, reset 0.75%
- Verificar gap balance-equity al gràfic d'aquest test
