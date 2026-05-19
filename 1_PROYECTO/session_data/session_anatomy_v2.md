# XAUUSD — Anatomia per sessió v2 (què fa el preu, no només quant es mou)

_Generat: 2026-04-25 16:11 UTC_

**Mostres:** 300 barres H1 (~17 dies). Sample petit per OVERLAP — interpretar amb cautela.

**Bucketing UTC:** ASIA=00-07 · LONDON=07-13 · OVERLAP=13-16 · NY=16-21 · DEAD=21-00

## 1. Persistència de tendència

_Quants closes consecutius en la mateixa direcció? Sessions amb runs llargs = trends. Runs curts = whipsaw/range._

| Sessió | n runs | run mediana | run màx | %runs≥3 | %bull | %bear | bias net |
|---|---:|---:|---:|---:|---:|---:|---:|
| **ASIA** | 57 | 1 | 6 | 11% | 45% | 51% | -5 |
| **LONDON** | 47 | 1 | 5 | 15% | 47% | 49% | -1 |
| **OVERLAP** | 24 | 1.0 | 3 | 12% | 49% | 49% | 0 |
| **NY** | 37 | 1 | 4 | 16% | 38% | 52% | -9 |
| **DEAD** | 21 | 1 | 2 | 0% | 50% | 42% | +2 |

_Lectura clau: alta % de runs ≥3 = sessió tendencial (dolent per averaging contra). Baixa = mean-revertable (bo per nosaltres)._

## 2. Reversió estructural (després d'un moviment, torna?)

_Després d'una barra H1 amb range ≥15$, el preu retrocedeix ≥50% en les properes 3h?_

| Sessió | n triggers | retracen ≥50% | % reversió |
|---|---:|---:|---:|
| **ASIA** | 54 | 43 | **80%** |
| **LONDON** | 54 | 48 | **89%** |
| **OVERLAP** | 39 | 35 | **90%** |
| **NY** | 32 | 24 | **75%** |
| **DEAD** | 12 | 9 | **75%** |

_>60% = clarament reversiu, sistema actual encaixa. <40% = trending, promediar contra-tendència = perdre._

## 3. Sweep rate (% de breaks que són fakeouts)

_Quan H1 fa nou high/low respecte l'anterior, tanca DINS del rang anterior? = sweep + revert._

| Sessió | n nous extrems | swept back | % sweep | %high sweep | %low sweep |
|---|---:|---:|---:|---:|---:|
| **ASIA** | 91 | 49 | **54%** | 50% | 57% |
| **LONDON** | 80 | 44 | **55%** | 55% | 55% |
| **OVERLAP** | 36 | 19 | **53%** | 47% | 58% |
| **NY** | 59 | 34 | **58%** | 65% | 52% |
| **DEAD** | 26 | 12 | **46%** | 43% | 50% |

_Sweep alta = la majoria de breaks són fakeouts → esperar retorn abans d'entrar = bona estratègia. Sweep baixa = breaks reals → entrar al break, no fade._

## 4. Estructura: trending vs range

_Per cada barra H1: |close-open|/range. >0.7=trending, <0.3=range/wicky._

| Sessió | n | %trending | %range | %mixed | body ratio mediana |
|---|---:|---:|---:|---:|---:|
| **ASIA** | 91 | 18% | 29% | 54% | 0.48 |
| **LONDON** | 78 | 12% | 28% | 60% | 0.41 |
| **OVERLAP** | 39 | 13% | 28% | 59% | 0.48 |
| **NY** | 66 | 18% | 42% | 39% | 0.37 |
| **DEAD** | 26 | 23% | 42% | 35% | 0.43 |

_Range alt + body ratio baix = entorn ideal nostre (price camina amb wicks, retorna). Trending alt = cuidado, sistema actual no està fet per això._

## 5. Eficàcia de la confluència DXY

_Quan XAU↑ amb DXY↓ (alineat amb correlació inversa), continua en aquesta direcció l'hora següent? Comparat amb counter (XAU↑ + DXY↑)._

| Sessió | n alineats | continuen | n counter | continuen | edge alineat |
|---|---:|---:|---:|---:|---:|
| **ASIA** | 54 | 37% | 30 | 43% | **-6%** |
| **LONDON** | 56 | 46% | 22 | 36% | **+10%** |
| **OVERLAP** | 28 | 32% | 11 | 73% | **-41%** |
| **NY** | 41 | 59% | 22 | 55% | **+4%** |
| **DEAD** | 17 | 47% | 7 | 29% | **+18%** |

_Edge positiu = DXY com a confluència aporta valor. Edge ~0 = no aporta. Edge negatiu = DXY soroll (no fiar-se)._

## 6. Velocitat ($/minut)

| Sessió | mediana $/min | màx $/min |
|---|---:|---:|
| **ASIA** | 0.268 | 1.001 |
| **LONDON** | 0.290 | 0.839 |
| **OVERLAP** | 0.459 | 0.997 |
| **NY** | 0.237 | 1.170 |
| **DEAD** | 0.242 | 1.784 |

_Velocitat alta = FAST engine (3s) pot arribar tard. Sistema actual es calibra implícitament a una velocitat — sessions a velocitat molt diferent poden necessitar parametritzar el FAST._

## 7. Direccionalitat per hora UTC

_Quines hores tendeixen a tirar amunt vs avall? Bias significatiu (≥30% asimetria)._

| Hora UTC | Sessió | %bull | %bear | bias | range med |
|---:|---|---:|---:|:---:|---:|
| 00 | ASIA | 69% | 31% | 🟢 | 17.5 |
| 01 | ASIA | 46% | 54% | ⚖️ | 27.7 |
| 02 | ASIA | 23% | 77% | 🔴 | 15.9 |
| 03 | ASIA | 38% | 54% | 🔴 | 11.9 |
| 04 | ASIA | 62% | 31% | 🟢 | 10.9 |
| 05 | ASIA | 54% | 46% | ⚖️ | 17.3 |
| 06 | ASIA | 23% | 62% | 🔴 | 16.1 |
| 07 | LONDON | 46% | 46% | ⚖️ | 16.2 |
| 08 | LONDON | 38% | 54% | 🔴 | 16.1 |
| 09 | LONDON | 15% | 85% | 🔴 | 14.4 |
| 10 | LONDON | 62% | 31% | 🟢 | 15.2 |
| 11 | LONDON | 85% | 15% | 🟢 | 22.5 |
| 12 | LONDON | 38% | 62% | 🔴 | 22.8 |
| 13 | OVERLAP | 69% | 23% | 🟢 | 27.8 |
| 14 | OVERLAP | 31% | 69% | 🔴 | 27.6 |
| 15 | OVERLAP | 46% | 54% | ⚖️ | 25.2 |
| 16 | NY | 62% | 23% | 🟢 | 16.6 |
| 17 | NY | 31% | 62% | 🔴 | 15.9 |
| 18 | NY | 46% | 38% | ⚖️ | 17.2 |
| 19 | NY | 31% | 62% | 🔴 | 12.3 |
| 20 | NY | 21% | 71% | 🔴 | 11.9 |
| 22 | DEAD | 46% | 54% | ⚖️ | 16.5 |
| 23 | DEAD | 54% | 31% | 🟢 | 14.2 |

_Si una hora té bias clar consistent, és informació tradeable._

## 8. Veredicte: pot el mateix sistema operar a totes les sessions?

**Score de fit del sistema** (mean-reversion + range + sweeps menys runs trending):

| Sessió | Score | Diagnòstic |
|---|---:|---|
| **DEAD** | 54.5 | ✅ Sistema actual encaixa bé |
| **OVERLAP** | 50.7 | ✅ Sistema actual encaixa bé |
| **NY** | 50.2 | ✅ Sistema actual encaixa bé |
| **LONDON** | 49.9 | ⚠️ Sistema funciona amb adaptacions |
| **ASIA** | 48.8 | ⚠️ Sistema funciona amb adaptacions |

### Diagnòstic operatiu detallat

#### ASIA
- **Reversió (>50% retrace en 3h):** 80% sobre 54 mostres
- **Estructura:** 29% range / 18% trending / 54% mixed
- **Sweep rate:** 54% dels nous extrems es reverteixen
- **Trend persistence:** 11% dels runs són ≥3 closes
- **DXY edge:** -6% (alineat continua menys counter continua)
- **Veredicte:** **Sistema actual encaixa**: alta reversió + baixa tendència. Operar normal.

#### LONDON
- **Reversió (>50% retrace en 3h):** 89% sobre 54 mostres
- **Estructura:** 28% range / 12% trending / 60% mixed
- **Sweep rate:** 55% dels nous extrems es reverteixen
- **Trend persistence:** 15% dels runs són ≥3 closes
- **DXY edge:** 10% (alineat continua menys counter continua)
- **Veredicte:** **Sistema actual encaixa**: alta reversió + baixa tendència. Operar normal.

#### OVERLAP
- **Reversió (>50% retrace en 3h):** 90% sobre 39 mostres
- **Estructura:** 28% range / 13% trending / 59% mixed
- **Sweep rate:** 53% dels nous extrems es reverteixen
- **Trend persistence:** 12% dels runs són ≥3 closes
- **DXY edge:** -41% (alineat continua menys counter continua)
- **Veredicte:** **Sistema actual encaixa**: alta reversió + baixa tendència. Operar normal.

#### NY
- **Reversió (>50% retrace en 3h):** 75% sobre 32 mostres
- **Estructura:** 42% range / 18% trending / 39% mixed
- **Sweep rate:** 58% dels nous extrems es reverteixen
- **Trend persistence:** 16% dels runs són ≥3 closes
- **DXY edge:** 4% (alineat continua menys counter continua)
- **Veredicte:** **Sistema actual encaixa**: alta reversió + baixa tendència. Operar normal.

#### DEAD
- **Reversió (>50% retrace en 3h):** 75% sobre 12 mostres
- **Estructura:** 42% range / 23% trending / 35% mixed
- **Sweep rate:** 46% dels nous extrems es reverteixen
- **Trend persistence:** 0% dels runs són ≥3 closes
- **DXY edge:** 18% (alineat continua menys counter continua)
- **Veredicte:** **Sistema actual encaixa**: alta reversió + baixa tendència. Operar normal.

## 9. Conclusió

Comparant les dimensions reals (no només volatilitat):

| Sessió | Arquetip de mercat | Mode operatiu suggerit |
|---|---|---|
| **ASIA** | MEAN-REVERSION (sistema actual ideal) | Sistema actual: zones + averaging + parcials |
| **LONDON** | MEAN-REVERSION (sistema actual ideal) | Sistema actual: zones + averaging + parcials |
| **OVERLAP** | MEAN-REVERSION (sistema actual ideal) | Sistema actual: zones + averaging + parcials |
| **NY** | MEAN-REVERSION (sistema actual ideal) | Sistema actual: zones + averaging + parcials |
| **DEAD** | MEAN-REVERSION (sistema actual ideal) | Sistema actual: zones + averaging + parcials |

### Resposta a la pregunta original
**Pot el mateix sistema operar a qualsevol sessió?** Resposta basada en les dades:

- **Sí** per: ASIA, LONDON, OVERLAP, NY, DEAD — caràcter de mean-reversion natural

## 10. Limitacions

- 17 dies = mostra petita. OVERLAP n=39 H1 bars. Cal validar amb 60+ dies.
- Mean reversion test usa lookforward 3h — pot ser massa curt per moviments grans.
- Trend persistence parteix pel canvi de sessió/dia → fragmenta runs reals que creuen sessions.
- Macro confluence DXY només; afegint USDJPY i US10Y aclariria la imatge especialment a Asia.
- No es mesura velocitat de moviment intra-bar (M5 directe ho aclariria).
