# OOS Validation — Level-Touch + Binary REJECT/BREAK

## What was tested
- **Same v1 detector, same v1 prompt** (no changes, no overfit)
- **OOS dataset**: 1280 M15 bars from TradingView, 2026-03-30 → 2026-04-27 (~3 weeks across 17 trading days)
- **105 level touches** detected during London/Overlap/NY sessions
- **All 105 sent to fresh Opus agents** (isolated, no memory of in-sample results)

## Headline result

**-24.73R / -12.36% on $10k account over 82 trades. WR 23%. PF 0.60.**

The in-sample SHORT/REJECT edge **collapsed completely**.

## Side-by-side: in-sample vs out-of-sample

| Metric | IS (1 week) | OOS (3 weeks) | Verdict |
|---|---|---|---|
| Trades | 27 | 82 | 3× larger N |
| Win rate | 26% | 23% | similar (no edge) |
| Profit Factor | 0.63 | 0.60 | similar |
| Avg R/trade | -0.28 | -0.30 | similar |
| **SHORT WR** | **50%** | **18%** | **negated** |
| **SHORT total** | **+4.73R** | **-24.12R** | **negated** |
| LONG WR | 7% | 33% | flipped |
| LONG total | -12.20R | -0.61R | nearly flat |
| **REJECT** | **+1.71R** | **-9.80R** | **negated** |
| BREAK | -9.18R | -14.93R | consistent loser |

The "SHORT-bias / fade-the-level" pattern that looked like an edge on 1 week of data was sample noise. On 3 weeks it produces a -24R drawdown.

The only thing that replicated:
- **BREAK calls are net losers** in both IS (-9.18R) and OOS (-14.93R). Opus's tendency to read "aggressive momentum into level" as breakout fuel is consistently wrong.

But fading those moves (REJECT) doesn't work either OOS, so the opposite is not an edge.

## What survives the OOS test?

Almost nothing.

- **C=7 trades** (n=10): +1.22R total, 30% WR. Positive expectancy but n=10 is statistically meaningless.
- **PPDH/PPDL** (n=8): +6.91R combined. Tiny sample, suspicious.

By day, the curve is volatile — some days +3R, others -4R. No structural pattern that would let us pre-filter days.

## Honest conclusion

The level-touch approach with binary REJECT/BREAK prompting **does not produce a robust edge** on XAUUSD M15 across 3 weeks of out-of-sample data. The promising 1-week signal was sample bias, exactly as you warned.

The full landscape of what's been tried:

| Approach | Result |
|---|---|
| Aggressive MOMENTUM prompt | Over-trades, large loss |
| Conservative "expert" on 126 random windows | -10.07R / -1.0%, PF 0.73 |
| Framework prompt on quality setups | 0 trades (refuses everything) |
| Level-touch + binary, 1 week IS | -7.47R / -3.73%, "SHORT looks good" |
| **Level-touch + binary, 3 weeks OOS** | **-24.73R / -12.36%, edge negated** |

None of these have shown an edge that holds out-of-sample. **Opus, in zero-shot single-bar discretionary mode on M15 XAUUSD, doesn't have positive expectancy.**

## What this tells us about the broader question

The original goal was: "can Claude be a profitable retail intraday scalper on XAU?"

**Evidence so far says no, not in this configuration.** The fundamental problems:

1. **No retrieval of relevant context**: each agent sees only 24 M15 bars and 6 H1 bars. No daily, no weekly, no news, no fundamentals.
2. **No memory across decisions**: each touch is isolated. A real trader has running PnL pressure, recent-trade memory, and known regimes.
3. **Calibration is uniform**: confidence 6 trades are 72% of decisions and lose. Confidence 7 is borderline. Opus doesn't seem to know when it's right.
4. **Single-shot binary classification** of "REJECT vs BREAK" at a level is a hard problem with weak base rates (~30-40% accuracy across many traders).

## What the user's production system already has that this experiment doesn't

Looking at `CLAUDE.md`:
- **Brain v3** uses Claude as a **gate on a known-positive mechanical strategy** (DFMO + averaging + dynamic R)
- Has **session R-factor**, **news gate**, **explosion detector**, **executor throttle**
- Validated 2026-04-28 with 80% WR, +0.49%, 0 SL hits

That's the right architecture. **Claude as filter on a proven mechanical edge** ≠ **Claude as primary signal generator from price action alone**.

## Recommendation (honest, not retroactively justified)

**Stop trying to make Claude a primary intraday signal generator on price action.** The evidence across 4 different prompt/filter combos is consistent: it doesn't produce edge.

Instead, the productive question is: **how can Claude's pattern reading improve the existing Brain v3 gate?** Concrete experiments that could matter:

1. **Test if Claude can improve DFMO's averaging-decision quality** (skip vs add at zone end)
2. **Test if Claude can predict trailing-trigger quality** (when to start M5 trailing vs hold)
3. **Test if Claude can flag when current session regime ≠ historical R-factor** (news-like adaptation)

These build on something that works rather than fighting to invent edge from scratch.

## Files generated
- [xauusd_m15_oos.json](xauusd_m15_oos.json) — 1280 OOS bars
- [level_touches_oos.json](level_touches_oos.json) — 105 touches
- [contexts_levels_oos/](contexts_levels_oos/) — 105 prompts (O_000 to O_104)
- [oos_trades.json](oos_trades.json) — full trade list with sim results
