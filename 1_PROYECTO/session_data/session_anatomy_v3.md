# XAUUSD — Anatomia v3 (anàlisi exhaustiva multi-asset, 70 dies)

_Generat: 2026-04-25 16:23 UTC_

**Datasets:**
- XAUUSD H4: 300 bars (~70 dies, finestra principal d'anàlisi)
- XAUUSD H1: 300 bars (~17 dies, fine-grain)
- XAUUSD M15: 300 bars (~3 dies)
- USDJPY H4: 301 bars (~70 dies)
- DXY H4: 302 bars · DXY H1: 300 bars
- Trades reals operats: 245 events des de 2026-04-17

**Bucketing UTC:** ASIA=00-07 · LONDON=07-13 · OVERLAP=13-16 · NY=16-21 · DEAD=21-00

## 1. Volatilitat per sessió (H4, n robust)

| Sessió | n | Range med | Mean | p75 | p90 | Max |
|---|---:|---:|---:|---:|---:|---:|
| **ASIA** | 100 | 43.3 | 53.0 | 57.3 | 96.8 | 281.0 |
| **LONDON** | 50 | 42.6 | 59.6 | 65.9 | 90.3 | 269.2 |
| **OVERLAP** | 50 | 63.3 | 74.0 | 83.6 | 138.8 | 179.4 |
| **NY** | 50 | 43.1 | 49.5 | 64.8 | 89.7 | 123.3 |
| **DEAD** | 50 | 47.9 | 57.4 | 65.7 | 116.3 | 216.5 |

_Range típic d'una H4 bar (4 hores). Per la nostra operació M5 amb objectius $10-15, ranges H4 ≥20$ indiquen entorn ric d'oportunitats._

## 2. Reversió estructural (després d'un H4 amb range ≥20$, retrocedeix ≥50% en 3 bars=12h?)

| Sessió | n triggers | retracen | % reversió |
|---|---:|---:|---:|
| **ASIA** | 99 | 92 | **93%** |
| **LONDON** | 47 | 42 | **89%** |
| **OVERLAP** | 49 | 42 | **86%** |
| **NY** | 45 | 33 | **73%** |
| **DEAD** | 45 | 40 | **89%** |

_>60% = reversió estructural fiable. <40% = trends — sistema actual no encaixaria._

## 3. Persistència de tendència (runs consecutius)

| Sessió | n runs | mediana | màx | %≥3 | %bull | %bear |
|---|---:|---:|---:|---:|---:|---:|
| **ASIA** | 73 | 1 | 2 | 0% | 48% | 49% |
| **LONDON** | 49 | 1 | 1 | 0% | 46% | 52% |
| **OVERLAP** | 50 | 1.0 | 1 | 0% | 54% | 46% |
| **NY** | 48 | 1.0 | 1 | 0% | 52% | 44% |
| **DEAD** | 50 | 1.0 | 1 | 0% | 58% | 42% |

_%≥3 baix = whipsaw (favorable a fade-system). Alt = trending (perillós averaging contra)._

## 4. Sweep rate (% de breaks que reverteixen = fakeouts)

| Sessió | n extrems | swept | %sweep | %high sweep | %low sweep |
|---|---:|---:|---:|---:|---:|
| **ASIA** | 93 | 51 | **55%** | 56% | 53% |
| **LONDON** | 55 | 35 | **64%** | 56% | 70% |
| **OVERLAP** | 57 | 29 | **51%** | 41% | 60% |
| **NY** | 35 | 16 | **46%** | 44% | 47% |
| **DEAD** | 47 | 23 | **49%** | 46% | 52% |

_Sweep alta = espera a confirmació de retorn abans d'entrar. Sweep baixa = breaks més reals._

## 5. Estructura del moviment (body/range)

| Sessió | n | %trending | %range | %mixed |
|---|---:|---:|---:|---:|
| **ASIA** | 100 | 19% | 31% | 50% |
| **LONDON** | 50 | 14% | 44% | 42% |
| **OVERLAP** | 50 | 20% | 34% | 46% |
| **NY** | 50 | 18% | 22% | 60% |
| **DEAD** | 50 | 18% | 30% | 52% |

_Range alt + body baix = ideal nostre. Trending alt = perill._

## 6. Correlació amb DXY (H4)

| Sessió | n | r (Pearson) | %h XAU↑↔DXY↓ |
|---|---:|---:|---:|
| **ASIA** | 18 | -0.287 | 61% |
| **LONDON** | 9 | -0.859 | 56% |
| **OVERLAP** | 9 | -0.440 | 78% |
| **NY** | 9 | -0.452 | 67% |
| **DEAD** | 18 | -0.587 | 56% |

## 7. Correlació amb USDJPY (H4) — driver d'Asia

| Sessió | n | r (Pearson) | %h XAU↑↔USDJPY↓ |
|---|---:|---:|---:|
| **ASIA** | 98 | -0.087 | 58% |
| **LONDON** | 49 | -0.558 | 61% |
| **OVERLAP** | 49 | -0.362 | 67% |
| **NY** | 50 | -0.665 | 76% |
| **DEAD** | 49 | -0.441 | 69% |

_USDJPY r molt diferent entre sessions = informació valuosa. Si Asia r alt amb USDJPY però baix amb DXY, USDJPY és el filtre adequat allà._

## 8. Edge de confluència macro (continua quan està alineat?)

### DXY edge
| Sessió | n alineats | %continuen | n counter | %continuen | edge |
|---|---:|---:|---:|---:|---:|
| **ASIA** | 11 | 55% | 7 | 71% | **-17%** |
| **LONDON** | 5 | 40% | 4 | 0% | **+40%** |
| **OVERLAP** | 7 | 86% | 2 | 0% | **+86%** |
| **NY** | 6 | 50% | 3 | 0% | **+50%** |
| **DEAD** | 10 | 60% | 7 | 29% | **+31%** |

### USDJPY edge
| Sessió | n alineats | %continuen | n counter | %continuen | edge |
|---|---:|---:|---:|---:|---:|
| **ASIA** | 57 | 53% | 41 | 46% | **+6%** |
| **LONDON** | 30 | 47% | 19 | 37% | **+10%** |
| **OVERLAP** | 33 | 58% | 16 | 50% | **+8%** |
| **NY** | 38 | 50% | 11 | 27% | **+23%** |
| **DEAD** | 34 | 41% | 15 | 40% | **+1%** |

_Edge positiu = aquesta confluència aporta valor. Edge ≤0 = soroll._

## 9. Trades reals operats (cross-reference)

| Sessió | OPENs | AVGs | PARTIALs | FULL_CLOSEs | SIGNAL_CLOSEs |
|---|---:|---:|---:|---:|---:|
| **ASIA** | 3 | 2 | 3 | 5 | 1 |
| **LONDON** | 18 | 7 | 8 | 41 | 34 |
| **OVERLAP** | 5 | 1 | 1 | 12 | 6 |
| **NY** | 5 | 1 | 9 | 75 | 8 |
| **DEAD** | 0 | 0 | 0 | 0 | 0 |

**Distribució real per hora UTC:**

| Hora | Sessió | OPENs | AVGs | Range med H1 |
|---:|---|---:|---:|---:|
| 05 | ASIA | 3 | 1 | 17.3 |
| 06 | ASIA | 0 | 1 | 16.1 |
| 07 | LONDON | 2 | 1 | 16.2 |
| 08 | LONDON | 4 | 1 | 16.1 |
| 09 | LONDON | 3 | 1 | 14.4 |
| 10 | LONDON | 4 | 0 | 15.2 |
| 11 | LONDON | 3 | 1 | 22.5 |
| 12 | LONDON | 2 | 3 | 22.8 |
| 13 | OVERLAP | 4 | 0 | 27.8 |
| 14 | OVERLAP | 1 | 0 | 27.6 |
| 15 | OVERLAP | 0 | 1 | 25.2 |
| 17 | NY | 4 | 0 | 15.9 |
| 19 | NY | 1 | 1 | 12.3 |

## 10. Síntesi final per sessió

| Sessió | Score | Vol med H4 | Reversió | Sweep | Range% | Millor confluència macro |
|---|---:|---:|---:|---:|---:|---|
| **LONDON** | 75.9 | 42.6 | 89% | 64% | 44% | DXY (+40% edge) |
| **ASIA** | 72.4 | 43.3 | 93% | 55% | 31% | USDJPY (+6% edge) |
| **DEAD** | 69.5 | 47.9 | 89% | 49% | 30% | DXY (+31% edge) |
| **OVERLAP** | 69.3 | 63.3 | 86% | 51% | 34% | DXY (+86% edge) |
| **NY** | 61.1 | 43.1 | 73% | 46% | 22% | DXY (+50% edge) |

## 11. Anatomia operativa final

### ASIA
- **Arquetip:** MEAN-REVERSION pura
- **Range típic H4:** $43.3 (mean: 53.0, p90: 96.8)
- **Reversió 50% en 12h:** 93% (sobre 99 mostres ≥$20 range)
- **Sweep rate:** 55% dels nous extrems es reverteixen
- **Estructura:** 31% range / 19% trending
- **Bias direccional:** 48% bull / 49% bear
- **Filtre macro recomanat:** USDJPY (edge +6%)
- **Trades reals:** 3 OPENs · 2 AVGs · 3 PARTIALs (en 6 dies de mostra)
- **Veredicte:** Sistema actual encaixa perfectament.

### LONDON
- **Arquetip:** MEAN-REVERSION pura
- **Range típic H4:** $42.6 (mean: 59.6, p90: 90.3)
- **Reversió 50% en 12h:** 89% (sobre 47 mostres ≥$20 range)
- **Sweep rate:** 64% dels nous extrems es reverteixen
- **Estructura:** 44% range / 14% trending
- **Bias direccional:** 46% bull / 52% bear
- **Filtre macro recomanat:** DXY (edge +40%)
- **Trades reals:** 18 OPENs · 7 AVGs · 8 PARTIALs (en 6 dies de mostra)
- **Veredicte:** Sistema actual encaixa perfectament.

### OVERLAP
- **Arquetip:** MEAN-REVERSION pura
- **Range típic H4:** $63.3 (mean: 74.0, p90: 138.8)
- **Reversió 50% en 12h:** 86% (sobre 49 mostres ≥$20 range)
- **Sweep rate:** 51% dels nous extrems es reverteixen
- **Estructura:** 34% range / 20% trending
- **Bias direccional:** 54% bull / 46% bear
- **Filtre macro recomanat:** DXY (edge +86%)
- **Trades reals:** 5 OPENs · 1 AVGs · 1 PARTIALs (en 6 dies de mostra)
- **Veredicte:** Sistema actual encaixa perfectament.

### NY
- **Arquetip:** MEAN-REVERSION pura
- **Range típic H4:** $43.1 (mean: 49.5, p90: 89.7)
- **Reversió 50% en 12h:** 73% (sobre 45 mostres ≥$20 range)
- **Sweep rate:** 46% dels nous extrems es reverteixen
- **Estructura:** 22% range / 18% trending
- **Bias direccional:** 52% bull / 44% bear
- **Filtre macro recomanat:** DXY (edge +50%)
- **Trades reals:** 5 OPENs · 1 AVGs · 9 PARTIALs (en 6 dies de mostra)
- **Veredicte:** Sistema actual encaixa perfectament.

### DEAD
- **Arquetip:** MEAN-REVERSION pura
- **Range típic H4:** $47.9 (mean: 57.4, p90: 116.3)
- **Reversió 50% en 12h:** 89% (sobre 45 mostres ≥$20 range)
- **Sweep rate:** 49% dels nous extrems es reverteixen
- **Estructura:** 30% range / 18% trending
- **Bias direccional:** 58% bull / 42% bear
- **Filtre macro recomanat:** DXY (edge +31%)
- **Trades reals:** 0 OPENs · 0 AVGs · 0 PARTIALs (en 6 dies de mostra)
- **Veredicte:** Sistema actual encaixa perfectament.

## 12. Recomanacions concretes per al sistema

### A) Configuració de session_factor (ja implementat, valors a ajustar):

Baseline LONDON: $42.6

| Sessió | factor v1 actual | factor v3 (anàlisi 70d) | rao |
|---|---:|---:|---|
| **ASIA** | 0.92 | **1.02** | range med $43.3 vs LONDON $42.6 |
| **LONDON** | 1.0 | **1.0** | range med $42.6 vs LONDON $42.6 |
| **OVERLAP** | 1.5 | **1.49** | range med $63.3 vs LONDON $42.6 |
| **NY** | 0.85 | **1.01** | range med $43.1 vs LONDON $42.6 |
| **DEAD** | 0.85 | **1.12** | range med $47.9 vs LONDON $42.6 |

### B) Pesos de confluència macro per sessió:

| Sessió | DXY weight | USDJPY weight | Notes |
|---|---:|---:|---|
| **ASIA** | 0.3 | 1.0 | DXY contraproductiu |
| **LONDON** | 1.2 | 1.0 | DXY útil |
| **OVERLAP** | 1.2 | 1.0 | DXY útil |
| **NY** | 1.2 | 1.2 | DXY útil, USDJPY útil |
| **DEAD** | 1.2 | 0.5 | DXY útil |

## 13. Limitacions

- Mostra H4: 300 bars = ~70 dies. Per OVERLAP n=39 H4 — ajustat. Per DEAD n=26 — petit.
- Mean reversion test usa 3 H4 bars = 12h lookforward — pot ser massa curt per moviments de news.
- Trades reals només 7 dies operats (Apr 17-24). No ASIA dins de la mostra real (00-04 UTC).
- M5 directe no inclòs — la velocitat intra-bar pot diferir.
- US10Y i SPX no inclosos per economia de crides MCP.
- Període Feb-Apr 2026 pot tenir biaixos de regime específics (cal validar amb una segona finestra més tard).
