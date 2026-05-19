# 🎯 REALITAT 10 ANYS — Honest Assessment

## TL;DR
El backtest 5y va ser **enganyós** perquè no contenia cap crisi major. El 10y inclou **Brexit 2016 i COVID 2020** i revela el risc real.

**Recomanació actualitzada**: V6 HYBRID lot 0.05-0.10 (no V4 com la versió 5y suggeria).

---

## Comparació 5y vs 10y

| Versió | 5y Calmar | 10y Calmar | 10y DD% | 10y +%/any | Pitjor any |
|---|---|---|---|---|---|
| V4 OPTIMIZED (8 pairs) | **2.45** ⭐ 5y winner | 0.43 ❌ | 78.9% | +34.3% | 2019 -32.0% |
| V5 BALANCED (15 pairs) | 1.60 | 0.62 | 69.7% | +43.2% | 2016 -21.5% |
| V6 HYBRID (10 pairs) | 4.58 | **0.64** ✅ 10y winner | 57.7% | +37.0% | 2016 -24.1% |

**Conclusió**: El 5y va veure V4 com guanyador (millor Calmar 2.45). El 10y veu V6 com guanyador (Calmar 0.64). V4 a 10y és el PITJOR per concentració excessiva en CAD (44%).

---

## Per què V4 falla a 10y i V6 aguanta

**V4** = 8 pairs, concentrat en AUDCAD (44% CAD).
- 2019: el CAD va patir → V4 va perdre **-32%** en un sol any
- 5y test no incloïa 2019 com any sec aïllat

**V6** = 10 pairs, diversificació millorada.
- 2016 (Brexit): -24% (suportable)
- 2020 (COVID): +59% (mean-rev funciona bé en crisi vol)
- Calmar 0.64 estable

---

## V6 HYBRID — Configuració deployment

| Lot | Anual | DD | Pitjor any | Calmar |
|---|---|---|---|---|
| **0.05** | +12.3%/any | 19.7% | -24% | 0.62 |
| **0.10** | +24.6%/any | 38.9% | -47% (extrap) | 0.63 |
| 0.15 | +37.0%/any | 57.7% | -71% (extrap) | 0.64 |

**Recomanació conservadora**: lot 0.05.
- **+12%/any en compte $63k = +$7,560/any**
- **DD pitjor cas: -19.7%**
- **Pitjor any: -24%** (suportable, no destrueix compte)

**Recomanació agressiva**: lot 0.10.
- **+24%/any = +$15k/any**
- **DD: -38.9%**
- Acceptable si tolerància psicològica alta

---

## Anys per any V6 lot 0.15

| Any | Resultat | Notes |
|---|---|---|
| 2016 | -24.05% | Brexit |
| 2017 | +33.40% | Recuperació |
| 2018 | -10.50% | Volatilitat baixa |
| 2019 | +11.17% | OK |
| 2020 | +59.37% | COVID — mean-rev brilla |
| 2021 | +29.75% | OK |
| 2022 | +68.10% | Inflació |
| 2023 | +81.54% | Top |
| 2024 | +50.10% | OK |
| 2025 | +59.05% | OK |
| 2026 | +11.39% | YTD |

**9 anys positius / 11. 2 anys negatius però controlats.**

---

## Decisions clau

1. ❌ **Descartem V4**: 2019 -32% és inacceptable per concentració
2. ❌ **Descartem JPY pairs**: trending fort, no mean-revertants
3. ✅ **Adoptem V6 HYBRID**: 10 pairs, balanç anual, 9/11 anys positius
4. ✅ **Lot inicial 0.05**: deploy conservador, escalar si va bé 3-6 mesos

---

## Pròxims passos

1. Pine Script V6 a TradingView (per visualitzar)
2. EA MT5 V6 amb 21 strats per VT Markets
3. Paper trading 4 setmanes amb lot 0.05
4. Live amb $63k si paper match backtest
