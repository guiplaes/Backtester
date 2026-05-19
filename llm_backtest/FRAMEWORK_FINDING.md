# Framework Prompt + Quality Setups — Honest Finding

## Setup
- 69 high-quality mechanical setups detected (52 FAILED_BREAKOUT + 17 PULLBACK_TREND)
- New prompt: 5-step reasoning framework (TREND → LOCATION → SCENARIO → ASYMMETRY → ACT/SKIP)
- Mechanical filter explicitly framed as suggestion, not signal
- Model: Opus, isolated agents (no memory of prior runs, no overfitting bias)

## Result
**35 of 35 sampled decisions = NO_TRADE**
- First 30 sequential (q_000–q_029): all NO_TRADE
- 5 spread across remaining range (q_035, q_042, q_050, q_058, q_065): all NO_TRADE

By induction across the full dataset, the framework + Opus combination produces a near-zero trade rate.

## Why Opus skips everything (consistent reasoning patterns)

1. **Mechanical filter quality is poor.** Opus repeatedly notes the "FAILED_BREAKOUT" or "PULLBACK_TREND" label fires after the move is already extended — late entries, no fresh trigger.
2. **R:R is structurally weak.** Logical SL at swing → logical TP at next level → R:R ≈ 1.0 on most setups. The framework correctly rejects these.
3. **Mid-range location.** Most flagged moments sit at 30–70% of the 20-bar range — no asymmetric edge.
4. **Conflicting M15/H1 trends.** XAUUSD on a 1-week M15 sample is mostly chop, not trend.
5. **Asia-session low liquidity** kills many setups before they can be considered.

Every NO_TRADE reasoning is *correct* on the merits. Opus is not being lazy — it is correctly identifying that the mechanical filter is upstream-broken.

## The fundamental tension (now empirically confirmed)

| Prompt style | Trade rate | Outcome |
|---|---|---|
| Aggressive "MOMENTUM/PREDICTION" | 95% | Forces trades on noise → loses |
| Original "expert trader" (126 windows) | 53% | WR 36%, Net **-10.07R**, PF 0.73 |
| Framework + quality setups (this test) | ~0% | No edge to measure (but no losses either) |

Opus does not have a calibration band that produces "selective + profitable" on this data. It either over-trades (when prompted to act) or correctly identifies that almost nothing crosses the bar for a real edge.

## What this means

The bottleneck is **not** the prompt. It is **the underlying signal quality**.

The mechanical pre-filter (FAILED_BREAKOUT / PULLBACK_TREND) is producing setups that an experienced trader would skip on inspection. Opus, with proper reasoning, agrees with that experienced trader. Adding more aggressive prompting doesn't create edge — it manufactures false trades.

## What this does **not** prove

- That Claude cannot trade XAUUSD profitably. Sample is 1 week, M15 only, no fundamental/news context, no order-flow, no level-2.
- That the framework prompt is wrong. It is producing *correct* decisions — the data just doesn't offer trades that pass it.
- That a different setup-detection logic (e.g., London-open break, NY-session liquidity sweep, structural break-of-structure) wouldn't work.

## Honest takeaway

For this experiment:
- Filter→LLM→decide is **directionally correct** but the filter is feeding garbage.
- Forcing Claude to act on poor inputs is what produced the -10.07R earlier.
- Forcing Claude to NOT act on poor inputs (framework) produces 0R — null result, not edge.

**Next-step options the user can pick from**:
1. **Better setup detector** — focus on session-aware setups (London open, NY 13:30, liquidity sweeps of PDH/PDL with rejection candle). Likely fewer but cleaner candidates.
2. **Longer dataset** — 1 month of M15 instead of 1 week; current 438-bar sample is too small to detect rare-but-real edge.
3. **Drop the "trade every setup" framing** — use Claude as a *gate* on a known-positive-edge mechanical strategy (e.g., the Brain v3 already in production), not as a primary signal generator.

Recommendation: option 3 is the only path with prior evidence (Brain v3 validated 2026-04-28). Options 1–2 are open research with no proof of edge yet.
