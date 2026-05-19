# Backtest Final HONEST — XAUUSD M5 Strategy
**Date:** 2026-05-07
**Engineer:** Claude (autonomous, ~6h work)
**Symbols:** XAUUSD spot (Dukascopy + MT5 broker XAUUSD.crp)
**Sample:** 354,543 M5 bars over **5 calendar years** (2021-05-09 to 2026-05-07)
**Method:** Pine Script v6 (TV) + Python custom backtester with realistic costs
**Cost model:** $1.70/trade (commission $1 + spread $0.5 + slippage $0.2)

---

## 🚨 BOTTOM LINE

**There is NO robust mechanical edge** for the Inside Bar Breakout strategy on XAUUSD M5 over a representative 5-year sample.

The 17-month backtest that initially showed promising results (+$602, PF 2.56) was **regime-favorable** — it caught the 2025-2026 institutional gold bull run and missed the 2021-2024 chop where the strategy LOST money 4 years in a row.

| Period tested | Sample | Result | PF | Verdict |
|---|---|---|---|---|
| 36 days TV | Apr-May 2026 | +$730 | 2.71 | Regime fluke ⚠️ |
| 17 months Python | Dec 2024-May 2026 | +$602 | 2.56 | Regime fluke ⚠️ |
| **5 years Python** | **May 2021 - May 2026** | **+$27** | **1.02** | **NO EDGE** ❌ |
| 5y + LLM filter + Streak sizing (full system) | 5 years | +$98 | 1.12 | Marginal ❌ |

---

## Per-Year Performance (Baseline Strategy on 5y)

| Year | Trades | WR | Net P/L | PF | Verdict |
|---|---|---|---|---|---|
| 2021 | 63 | 17.5% | -$67 | 0.57 | ❌ |
| 2022 | 74 | 8.1% | -$146 | 0.32 | ❌ |
| 2023 | 74 | 4.1% | -$186 | 0.13 | ❌❌ |
| 2024 | 90 | 11.1% | -$152 | 0.47 | ❌ |
| **2025** | 60 | 23.3% | +$470 | 2.89 | ✅ |
| 2026 (5 mo) | 19 | 21.1% | +$108 | 1.54 | ✅ |

**4 of 6 years are negative.** The strategy is regime-dependent, not skill-based.

---

## Tests Performed (7 tests + multiple variants)

### Test 1: Streak-based Position Sizing — PASS ✅
**On 17m sample:** Streak sizing (avg 0.73× lot) beat fixed equivalent by +72% with same risk profile. PF 3.97 vs 2.56.
**On 5y sample:** Marginal improvement (+$11 absolute). Doesn't fix the underlying edge problem.

### Test 2: News Gate Filter — FAIL ❌
Asia session (00-06 UTC) is far from US news (13-19 UTC). 0 setups affected.

### Test 3: DXY Correlation Filter — MARGINAL FAIL ❌
PF improvement +0.19 on 17m sample (just below +0.20 threshold). Direction was correct but impact too small to be meaningful.

### Test 4: LLM Quality Filter (DeepSeek) — PASS on 17m, FAIL on 5y
- **17m:** PF 2.56 → 3.94 (+1.39) ✅✅✅
- **5y:** PF 1.02 → 1.04 (LLM A only) — virtually no improvement
The LLM correctly identifies quality WITHIN a sample where the underlying edge exists. It cannot create edge where none is.

### Test 5: Multi-strategy Ensemble — FAIL ❌
Adding NR4 / Hammer / Engulfing detectors DILUTED the edge. PF dropped from 2.56 to 1.43.

### Test 6: CME Real Volume — SKIP
MT5 broker doesn't expose CME futures (GC1!). Would need Polygon.io or CME datamine for historical real volume.

### Test 7: Trade Monitor M1 Replay — INCONCLUSIVE
Sample alignment bug + only 4 trades in 7-week M1 window. Insufficient data.

### 5-year Variants Tested (10+)

| Variant | Trades | WR | Net | PF | Verdict |
|---|---|---|---|---|---|
| Baseline LONG+Asia+skipWed 15/30 | 379 | 12.7% | +$31 | 1.02 | Break-even |
| LONG no time filter | 951 | 11.4% | +$160 | 1.04 | No edge |
| LONG + skip Wed (no Asia) | 789 | 12.4% | +$279 | 1.09 | No edge |
| Both directions | 790 | 11.1% | -$835 | 0.73 | ❌ Confirms LONG-only |
| LONG + Asia only | 479 | 11.7% | +$199 | 1.12 | No edge |

**Best 5y PF found: 1.12** — far below 1.5 robustness threshold.

---

## Why the 17-Month Sample Was Misleading

The 17-month sample (Dec 2024 - May 2026) coincided with:
1. **Massive gold bull run** (~+25% in the period)
2. **Central bank accumulation** (record FY2025 gold purchases)
3. **Geopolitical hedge demand** spikes
4. **BRICS dedollarization** narrative

In strong trending bull markets, breakout patterns work BETTER because:
- Fewer false breakouts (price keeps going)
- Asymmetric R:R 1:10 captures big runs
- LONG bias matches market direction

In ranging/chop markets (2021-2024 was mostly range-bound for gold):
- Most breakouts are FALSE
- Tight SL gets hit before big TP
- LONG-only lacks SHORT hedging

---

## What Doesn't Work

After 6 hours of testing, these approaches **failed** to produce robust 5-year edge:

1. **Pure mechanical Inside Bar BO** (PF 1.02) ❌
2. **+ Volume filter** ❌
3. **+ Session filter (Asia)** ❌
4. **+ Day filter (skip Wednesday)** ❌
5. **+ ATR-based exits** ❌
6. **+ Wide TPs (1:10/1:20)** ❌
7. **+ EMA50 trend filter** ❌
8. **+ Streak sizing** (marginal) ❌
9. **+ LLM A-grade filter** (helps in trending regime, not in chop) ❌
10. **Multi-pattern ensemble** ❌

---

## What MIGHT Work (Not Tested Due to Time/Data Constraints)

### A) Regime Detection + Strategy
Trade Inside Bar BO **only when** daily/weekly trend confirms strong bull regime:
- Daily slope of EMA200 > +5% in last 60 days
- Weekly close > weekly EMA(20)
- DXY downtrend confirmation

If we'd only operated in bull regime years (2025-2026) the strategy would be PF 2.84 / 1.60. But we'd need a regime detector that activates BEFORE the bull run started, not after.

### B) Mean-Reversion in Range Markets
The opposite strategy — fade Inside Bar Breakouts (counter-trend) might work in 2021-2024 ranging conditions. Untested.

### C) M1 Timeframe with Proportional Distances
Theoretical: smaller TPs (1-3×ATR) on M1 might capture more frequent setups. Would need 5y of M1 data (huge dataset) and is unlikely to scale (M1 noise is even worse).

### D) Real CME Volume + LLM Filter
Real institutional volume from GC1! could distinguish real breakouts. Would require Polygon.io subscription ($50/mo) or CME datamine work.

### E) Hybrid LLM + Mechanical
Use the existing LLM pipeline (INDICATOR/EXECUTOR) but feed it Inside Bar BO setups as candidates. LLM applies regime/macro context to decide whether to execute. The LLM has access to news, multi-TF, sentiment that pure mechanical doesn't.

---

## What the Existing System Should Do Now

**The current LLM-driven system** (with INDICATOR/REVIEWER/EXECUTOR) has been operating with mixed results. Key insights from this exhaustive testing:

1. **Don't chase pure mechanical edges on M5** — they're typically regime-dependent or random after costs.

2. **Wider TPs DO have value** — the asymmetric R:R approach is mathematically sound; just the entry pattern needs to be very high quality.

3. **Volume confirmation is critical** — but tick volume is degraded; real CME volume would be a real upgrade.

4. **Regime awareness is non-negotiable** — strategies that work in trending markets fail in ranging markets and vice versa.

5. **The LLM pipeline IS valuable** — it can incorporate context that pure mechanical cannot. The system shouldn't abandon LLM judgment.

### Concrete recommendations:

1. **Don't deploy Inside Bar BO mechanical** as initially proposed. The 5y data shows it's not a robust edge.

2. **Improve the existing LLM system** by:
   - Feeding Inside Bar BO detections as INPUTS (not auto-execute)
   - Adding a regime detector (EMA200 daily slope) to throttle
   - Wider TPs (10-20×ATR) instead of fixed $4-8 distances
   - LONG bias when daily trend bullish, neutral otherwise

3. **Stop seeking the holy grail mechanical strategy** — 5 years of data shows none of the classic patterns (RSI, BB, Donchian, VWAP, Inside Bar) have robust edge on XAUUSD M5 after realistic costs.

4. **Focus development on:**
   - Better LLM judgment (better prompts, more context)
   - Better risk management (position sizing, drawdown protection)
   - Better trade management (active monitoring, partial closes)

---

## Files Generated (All in 1_PROYECTO/)

- `BACKTEST_FINAL_HONEST_2026-05-07.md` — this report
- `xauusd_m5_5y.csv` — 354,543 M5 bars from Dukascopy (5 years)
- `backtest_trades_5y.csv` — 380 trades from baseline 5y backtest
- `5y_trades_with_scores.csv` — trades with LLM A/B/C scores
- `test1_kelly_sizing.py` through `test7_trade_monitor.py` — individual tests
- `backtest_5y.py` / `backtest_5y_variants.py` / `analyze_5y_with_llm.py` — 5y analysis
- `fetch_dukascopy_5y.py` — data fetcher
- `tg_send.py` — Telegram notifier (used during this session)

## Summary for the User (Catalan)

**El que hem trobat en 5 anys reals (2021-2026):**

❌ La estratègia Inside Bar BO + Asia + LONG **NO té edge robust**
❌ 4 dels 6 anys són perdedors (-$67, -$146, -$186, -$152)
✅ Només 2025-26 són guanyadors (bull run institucional de l'or)
❌ LLM filter NO salva l'estratègia (millora marginal)
❌ Streak sizing NO la salva (millora marginal)
❌ Cap variant testada té PF > 1.15 sobre 5 anys

**La conclusió dura:** No hi ha "estratègia mecànica simple" que funcioni a llarg termini en XAUUSD M5 amb costos reals.

**Què sí pot funcionar:**
- Sistema híbrid: LLM + detector mecànic com input
- Regime detector: només operar en bull markets confirmats
- Acceptar que el cost de spread+slippage és el filtre dur de M5

El sistema actual amb LLM és probablement millor que qualsevol mecànic pur si afegim regime awareness i millors prompts.
