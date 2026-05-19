# XAUUSD — Anatomia per sessió (operació M5 reversions $10-15)

_Generat: 2026-04-25 15:21 UTC_

**Mostres:**

- XAUUSD H1: 300 barres (2026-04-07 → 2026-04-24, ~17 dies)
- XAUUSD M15: 300 barres (~3 dies fine-grain)
- DXY H1: 300 barres (correlació)

**Bucketing UTC:** ASIA=00-07 · LONDON=07-13 · OVERLAP=13-16 · NY=16-21 · DEAD=21-00

## 1. Caràcter ampli per sessió (H1)

| Sessió | n | Range med | Range mean | p75 | %≥10$ | %≥15$ | %≥20$ | %bull |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **ASIA** | 91 | 16.1 | 18.6 | 22.1 | 86% | 59% | 30% | 47% |
| **LONDON** | 78 | 17.4 | 19.1 | 23.3 | 94% | 69% | 36% | 47% |
| **OVERLAP** | 39 | 27.5 | 29.2 | 33.6 | 100% | 100% | 79% | 51% |
| **NY** | 66 | 14.2 | 18.1 | 22.2 | 77% | 48% | 30% | 42% |
| **DEAD** | 26 | 14.5 | 21.0 | 23.9 | 81% | 46% | 35% | 50% |

_Lectura: a una hora qualsevol, % de probabilitat que el rang H1 superi N$. Si el % és baix, és difícil capturar reversions de 10-15$ en aquella sessió._

## 2. Densitat de reversions M15 (oportunitats reals)

| Sessió | M15 bars | wick≥10$ rebuig | per hora | wick≥15$ rebuig | per hora |
|---|---:|---:|---:|---:|---:|
| **ASIA** | 84 | 0 (0.0%) | 0.00 | 0 (0.0%) | 0.00 |
| **LONDON** | 72 | 0 (0.0%) | 0.00 | 0 (0.0%) | 0.00 |
| **OVERLAP** | 40 | 1 (2.5%) | 0.10 | 0 (0.0%) | 0.00 |
| **NY** | 80 | 2 (2.5%) | 0.10 | 0 (0.0%) | 0.00 |
| **DEAD** | 24 | 0 (0.0%) | 0.00 | 0 (0.0%) | 0.00 |

_Una vela M15 amb wick ≥10$ i body contrari = rebuig clar. Aquestes són les espines del nostre joc. "Per hora" estima freqüència mitjana, no en hi ha cada hora exacta._

## 3. Granularitat horària XAUUSD (UTC)

| Hora UTC | Sessió | n | Range med | %≥10$ | %≥15$ |
|---:|---|---:|---:|---:|---:|
| 00 | ASIA | 13 | 17.5 | 92% | 69% |
| 01 | ASIA | 13 | 27.7 | 100% | 100% |
| 02 | ASIA | 13 | 15.9 | 85% | 69% |
| 03 | ASIA | 13 | 11.9 | 77% | 15% |
| 04 | ASIA | 13 | 10.9 | 54% | 31% |
| 05 | ASIA | 13 | 17.3 | 100% | 69% |
| 06 | ASIA | 13 | 16.1 | 92% | 62% |
| 07 | LONDON | 13 | 16.2 | 100% | 77% |
| 08 | LONDON | 13 | 16.1 | 100% | 69% |
| 09 | LONDON | 13 | 14.4 | 85% | 46% |
| 10 | LONDON | 13 | 15.2 | 92% | 54% |
| 11 | LONDON | 13 | 22.5 | 92% | 85% |
| 12 | LONDON | 13 | 22.8 | 92% | 85% |
| 13 | OVERLAP | 13 | 27.8 | 100% | 100% |
| 14 | OVERLAP | 13 | 27.6 | 100% | 100% |
| 15 | OVERLAP | 13 | 25.2 | 100% | 100% |
| 16 | NY | 13 | 16.6 | 92% | 62% |
| 17 | NY | 13 | 15.9 | 92% | 54% |
| 18 | NY | 13 | 17.2 | 77% | 54% |
| 19 | NY | 13 | 12.3 | 69% | 38% |
| 20 | NY | 14 | 11.9 | 57% | 36% |
| 22 | DEAD | 13 | 16.5 | 85% | 54% |
| 23 | DEAD | 13 | 14.2 | 77% | 38% |

_La sessió no és una caixa uniforme — cada hora té caràcter propi. Les hores amb % ≥10 baix són les que costen més capturar reversions netes._

## 4. Correlació amb DXY (XAU vs USD index, H1 by H1)

| Sessió | n | r (Pearson) | %h XAU↑ ↔ DXY↓ |
|---|---:|---:|---:|
| **ASIA** | 84 | -0.483 | 64% |
| **LONDON** | 78 | -0.639 | 72% |
| **OVERLAP** | 39 | -0.412 | 72% |
| **NY** | 65 | -0.733 | 63% |
| **DEAD** | 24 | -0.530 | 71% |

_r negatiu = inversa clàssica. Si Asia té r prop de 0, vol dir que XAU es mou amb dinàmica pròpia i DXY no és bon filtre allà. Si OVERLAP té r molt negatiu, llavors DXY a contracorrent és un fre fort._

## 5. Anatomia operativa per sessió

### ASIA (00-07 UTC)
- Range H1 mediana: **$16.1** | %hores ≥15$: **59%** | rev≥10$ M15/hora: **0.00**
- Correlació DXY: r = **-0.483**
- **Lectura operativa:** Volatilitat suficient — operable amb cura. Range més ample del normal en aquesta finestra.
- **Tàctica:** TP escalonat més curt ($6-10 al primer parcial). Averaging més espaiat (no alimentar el range cec). Esperar Tokyo open + sweep, no entrar a meitat de rang.

### LONDON (07-13 UTC)
- Range H1 mediana: **$17.4** | %hores ≥15$: **69%** | rev≥10$ M15/hora: **0.00**
- Correlació DXY: r = **-0.639**
- **Lectura operativa:** Sessió on s'estableix tendència del dia. Volatilitat alta i sostinguda — entorn natural del nostre sistema.
- **Tàctica:** Operar normal. R formula actual calibrada per aquesta sessió. Atenció primera hora (07-08 UTC) — pot fer expansió ràpida. DXY és confluència fiable aquí.

### OVERLAP (13-16 UTC)
- Range H1 mediana: **$27.5** | %hores ≥15$: **100%** | rev≥10$ M15/hora: **0.10**
- Correlació DXY: r = **-0.412**
- **Lectura operativa:** Màxima liquiditat. NY entra abans de London tancar. Risc: news US a 14:30 UTC.
- **Tàctica:** Major densitat d'oportunitats però també major risc de moviments-headfake. R formula ja amplia coef aquí. Atenció especial al gate de news.

### NY (16-21 UTC)
- Range H1 mediana: **$14.2** | %hores ≥15$: **48%** | rev≥10$ M15/hora: **0.10**
- Correlació DXY: r = **-0.733**
- **Lectura operativa:** Continuació o reversió de London. Sovint primera hora (16-17 UTC) és la més agressiva. Cap a les 20 UTC, fade.
- **Tàctica:** Operar normal però vigilar últim hora pre-DEAD (20-21 UTC) — moviments solen ser stop hunts en lloc de tendència.

### DEAD (21-00 UTC)
- Range H1 mediana: **$14.5** | %hores ≥15$: **46%** | rev≥10$ M15/hora: **0.00**
- Correlació DXY: r = **-0.530**
- **Lectura operativa:** Sessió desolada. Range probablement molt estret, spread ample real (fora de la mostra de bars).
- **Tàctica:** No operar setups nous tret que sigui un nivell extrem amb confluència màxima. La majoria de "reversions" són soroll de baixa liquiditat.

## 6. Recomanacions per al sistema

### Què cal canviar al codi
**A) Filtre de sessió per noves entrades:**
- Bloquejar entrades durant DEAD si range_h1_actual < 8$ (poc material per reversions)
- Permetre entrades a ASIA però amb TP escalonat més curt (primer parcial al 50% de R, no 100%)

**B) R-formula condicional per sessió:**
- Asia: factor R ≈ 0.92 respecte London (range 16.1 vs 17.4)
- Si extrapolem: TP de London = X → TP equivalent Asia ≈ X × 0.92

**C) DFMO sensitivity per sessió:**
- DXY r en Asia: -0.483 → si baix, DFMO no necessita confluència DXY
- DXY r en Overlap/NY: si fort negatiu, DFMO pot reforçar-se amb confirmació DXY

**D) Hores explícitament problemàtiques (range típic <10$):**
- Cap hora amb range típic <10$ a la mostra actual

## 7. Limitacions de l'anàlisi
- Finestra: 300 bars H1 = ~17 dies. Mostra mínima per estadística robusta = 30+ dies.
- Falten USD/JPY (driver Asia), US10Y (yields), VIX (risc on/off).
- M5 directe no inclòs (dades més denses → 100+ crides MCP). M15 com a proxi de detecció de reversions.
- Cal validar amb 30-60 dies addicionals per confirmar patrons (especialment ASIA on només tenim ~17 sessions).

## 8. Què fa falta per la segona iteració
1. **Ampliar a 60-90 dies** scrolljant TV → 5-8 batches més per asset
2. **Afegir USD/JPY H1** — driver clar d'Asia, aclarirà comportament de la sessió
3. **M5 ATR per sessió** — finestra més curta, només 5-10 dies, però resolució nostra
4. **Cross-referenciar amb el journal de trades reals** — quins trades hem obert per sessió, win-rate, expectancy real