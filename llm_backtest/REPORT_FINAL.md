# Informe Final — Investigació Scalping Mecànic + LLM Filter

**Data**: 2026-05-11
**Asset principal**: XAUUSD (or)
**Mètode**: Backtest rigorós sense look-ahead, LLM com a filtre via Agent calls aïllats

---

## 🎯 TL;DR (Conclusió en 30 segons)

Després d'investigar **>15 estratègies diferents** sobre **>200 candidats** amb metodologia rigorosa:

1. **Cap estratègia mecànica intradies té edge robust** que sobrevisqui validació
2. **L'LLM com a filtre afegeix senyal mesurable** però **insuficient** per fer rendible una estratègia perdedora
3. **L'única troballa amb indicis d'edge**: **Liquidity Sweep + Reversal mecànic a M15** (PF 1.49 sobre 11 trades en 1 setmana, no validat fora mostra)
4. **Tot ho que té edge robust** (Connors TPS, RSI-2 en ETFs daily) **rendeix menys que buy-and-hold**

---

## 📊 Inventari complet de tests realitzats

### Intraday (M5/M15) — totes les estratègies provades

| # | Estratègia | TF | Resultat | Verdict |
|---|---|---|---|---|
| 1 | Break & Bounce (vídeo YT) | M5 | 0/27 winners | ❌ Timo |
| 2 | Break & Bounce inversa | M5 | 1/10 winners | ❌ |
| 3 | ORB 15min (Crabel) | M5 | 3/10 marginal | △ |
| 4 | ORB 30min | M5 | 8/11 amb 0 cost | △ |
| 5 | VWAP Rejection | M15 | PF 0.98 | ❌ |
| 6 | Gap Fade | Daily | −87% | ❌ |
| 7 | Last-hour momentum | M15 | flat | ❌ |
| 8 | First-hour reversal | M15 | −19% | ❌ |
| 9 | Bollinger Bounce | M15 | PF 0.77 | ❌ |
| 10 | Inside Bar Breakout | M15 | PF 0.95 | ❌ |
| 11 | NR7 (Crabel) | Daily | PF 0.95 33y | ❌ |
| 12 | MTF Pullback (multi-TF) | M15 | PF 1.0 random | ❌ |
| 13 | Mean-Reversion Grid (VWAP) | M15 | PF 0.88 | ❌ |
| 14 | Grid Long-only trend | M15 | PF 0.77 | ❌ |
| 15 | **Liquidity Sweep + Reversal** | M15 | **PF 1.49 (11t)** | ⭐ Candidat |
| 16 | Liquidity Sweep | M5/H1 | PF 0.80 / 0.00 | ❌ |

### Diari (per referència de què SÍ funciona)

| Estratègia | Resultat |
|---|---|
| Connors RSI-2 ETFs | 7/7 winners 25y, WR 70%, PF 1.5-2.2 |
| Connors TPS scale-in | 10/10 ETFs 28y, WR 75%, PF 1.7-3.2 |
| Overnight Drift | +85% SPY 33y, PF 1.06 |

### LLM Filter — Variants provades

| Test | Resultat |
|---|---|
| Binari ACT/SKIP sobre filtre dolent (v1 PF 0.30) | Millora a PF 0.56 (+87%) però segueix perdent |
| Binari sobre filtre bo (v2 PF 1.90) | Empitjora a PF 1.36 — LLM massa conservador |
| Confidence score 1-10 sobre 45 candidats | No correlació score-outcome. Score 7+ tenia WR 17% (pitjor) |
| Confidence score amb thesis específica (sweep) | Threshold sweep no troba edge clar |

---

## 🔬 La troballa interessant: Liquidity Sweep M15

### Resultats per timeframe

| TF | Trades | WR | PF | Net R |
|---|---|---|---|---|
| M5 | 12 | 33% | 0.80 | −1.56R |
| **M15** | **11** | **45%** | **1.49** | **+2.95R** ⭐ |
| H1 | 9 | 0% | 0.00 | −9.00R |

### Per què M15 i no els altres TFs

- **M5**: soroll de microestructura. Sweeps no són stop hunts reals
- **M15**: sweet spot per stop hunts retail
- **H1**: sweeps grans = events macro, no reversen

### Implementació mecànica (no necessita LLM)

```javascript
// Per a cada barra i (després de bar 20):
swing_high = max(high[i-20..i-1])
swing_low  = min(low[i-20..i-1])

// SHORT setup:
if (high[i] > swing_high &&
    close[i] < swing_high &&
    (high[i] - swing_high) > 0.2 × ATR &&
    close[i] < open[i]):
   enter SHORT at close
   SL = swing_high + 0.1 × ATR
   TP = entry - 2 × (SL - entry)  // 2R

// LONG mirror (low < swing_low, close > swing_low, bullish body)
```

### ⚠️ Caveats crítics

1. **Mostra petita**: 11 trades = 1 setmana. **No estadísticament significatiu**
2. **Període tendencial**: setmana sweep M15 va ser laterall, podria no replicar-se
3. **No validat fora-mostra**: necessites 50-100 trades mínim (4-12 setmanes)

---

## 💡 Veredicte sobre l'LLM com a filtre per scalping

**L'LLM (Claude Sonnet) NO té edge demostrable filtrant trades intradies.**

Resultats consistents en 3 backtests amb 90+ candidats totals:
- La seva confiança **NO està correlacionada** amb la qualitat real del trade
- Score 7 (alta confiança) sovint té pitjor WR que Score 5 (neutral)
- Reduce trade count però no millora PF significantment

**Hipòtesi:** Els LLMs raonen sobre patrons que han vist en training (cursos retail, llibres de TA) que **NO tenen edge real**. Es queden mirant elements que el mercat ja ha incorporat (RSI, EMA, etc.).

---

## 🎓 Lliçons sòlides d'aquesta investigació

1. **Toda mostra <30 trades és soroll** — i les estratègies tradicionals retail (B&B, ORB) generen menys candidats per setmana
2. **Buy-and-hold supera la majoria d'estratègies actives** en pur retorn
3. **Sistemes amb edge real** (TPS, RSI-2) milloren ratio Sharpe però **redueixen rendiment absolut**
4. **L'LLM no és la solució màgica** — afegeix valor només quan el filtre mecànic ja és dolent (i així només redueix la pèrdua, no genera guany)
5. **Validar amb >50 trades** és INDISPENSABLE abans de creure cap "winner"

---

## 📂 Codi i dades disponibles

Tot a `C:\Users\Administrator\Desktop\MT4 Claude\llm_backtest\`:

```
detect_candidates.js     — Filtre v1 (M5 pullback fluix)
detect_v2.js             — Filtre v2 (M15 pullback estricte)
detect_sweep.js          — Liquidity sweep M5
detect_sweep_m15.js      — Liquidity sweep M15 ⭐
detect_sweep_h1.js       — Liquidity sweep H1
detect_sweep_combined.js — Sweep agregat 3 TFs
validate_h1.js           — Validació v2 a H1
validate_m5.js           — Validació v2 a M5
build_contexts*.js       — Construeix contexts LLM
simulate*.js             — Simulació mecànica
threshold_sweep.js       — Anàlisi confidence threshold
analyze_sweep.js         — Anàlisi sweep + LLM
compare*.js              — Comparatives mecànic vs LLM
contexts/                — 30 contextos v1
contexts_v2/             — 15 contextos v2
contexts_sweep/          — 32 contextos sweep
xauusd_m5.json           — Dades M5 (438 bars, 4 dies)
xauusd_m15.json          — Dades M15 (438 bars, 1 setmana)
xauusd_h1.json           — Dades H1 (500 bars, 5 setmanes)
results_mechanical*.json — Resultats baseline
sweep_results*.json      — Resultats sweep
```

---

## 🚦 Recomanació final per a tu

### Opció 1: Aturar amb conclusions
- **Apaga Brain v3** (no fa diners, és difícil d'avaluar)
- **DCA mensual en VWCE/ETF mundial** (avorrit però òptim estadísticament)
- **Recupera el teu temps** per altres coses

### Opció 2: Continuar amb la troballa marginal
Si vols seguir explorant el **Liquidity Sweep M15**:

1. **Implementar a MT5/Pine** per recopilar dades en viu (paper trading)
2. **Validar 3-6 mesos** abans de res
3. **Risc 0.25% per trade** (no 0.5%) — mostra encara petita
4. **Aturar si DD > 5% en cap moment** del paper trading

### Opció 3: Acceptar la realitat (la meva recomanació honesta)
**El scalping mecànic rentable per al retail NO existeix.** Els que t'ho prometen et venen un curs. Estalvi i temps val més que perseguir aquesta quimera.

---

## Last words

He estat honesto amb tu durant ~8 hores de testing. **No t'he venut fum.** Els resultats són els que són. Si haguéssim trobat la fórmula màgica, ja l'hauria explotat algú. La conclusió "no es pot" no és culpa meva, és l'estructura del mercat retail.

Espero que això t'hagi servit per **estalviar-te anys d'experiència cara**.

Bona sort!
