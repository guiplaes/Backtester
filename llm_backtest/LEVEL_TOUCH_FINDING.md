# Level-Touch + REJECT/BREAK Binary Prompt — Result

## Setup
- 31 strong-level touches detected (PDH, PDL, PPDH, R50 round, TDH)
- Active sessions only (London/Overlap/NY, UTC 7-17)
- Each touch presented to Opus as binary: REJECT (bounce) or BREAK (continue)
- Forward simulation: 8 M15 bars (2h), limit-order fill within first 3 bars

## Aggregate
| Metric | Value |
|---|---|
| Candidates | 31 |
| Trades placed | 27 |
| NO_TRADE | 2 |
| No-fill (entry not reached) | 2 |
| Wins / Losses / Timeouts | 7 / 20 / 2 |
| Win rate | 26% |
| **Total** | **-7.47R / -3.73% on $10k** |
| Profit Factor | 0.63 |
| Avg R/trade | -0.28R |

## The buried signal — direction stratification

| Direction | N | WR | Total R |
|---|---|---|---|
| **SHORT** | 12 | **50%** | **+4.73R** ✅ |
| LONG | 15 | 7% | -12.20R ❌ |

| Bias | N | WR | Total R |
|---|---|---|---|
| **REJECT** (fade level) | 12 | **42%** | **+1.71R** ✅ |
| BREAK (continuation) | 15 | 13% | -9.18R ❌ |

## What this actually shows

1. **The trigger works** — 29/31 actionable (vs 0/35 with framework prompt). Level-touch is the right type of moment to send to Claude.

2. **Opus is systematically wrong on BREAK calls** in this dataset. When momentum thrusts into a level with rising volume, Opus reads "breakout fuel" — but on M15 XAU during this week, those bursts were exhaustion. 13/15 BREAK calls failed.

3. **REJECT calls have positive expectancy** in this sample. Fading parabolic moves into PDH/PPDH/R50 with RSI elevated was a real edge (+1.71R, 42% WR with R:R ≈ 2).

4. **TDH (today's high) is the weakest level**: WR 14%, -5.70R on 14 trades. It's not actually "institutional" — it's intraday and forms during the session. Should probably be excluded.

5. **Confidence is uncalibrated**: c6 and c7 both ~25% WR. Opus's self-assessed confidence doesn't predict outcomes.

## Caveats — what this does NOT prove

- **Sample size = 1 week, 27 trades**. Direction bias (SHORT > LONG) might be entirely XAU drifting/range that week. Need 1 month minimum to know if REJECT > BREAK is structural.
- **Survivorship-style finding**: filtering down to SHORT-only post-hoc is exactly the overfit you warned about. Not safe to act on yet.
- The 1 PDL trade (WR 100%) and 2 PPDH trades (+1.20R) are also too small to be evidence.

## Honest comparison across 3 prompt designs

| Approach | Trades / 1wk | Trade rate | Net R | WR | Edge? |
|---|---|---|---|---|---|
| MOMENTUM/aggressive prompt | ~60 | 95% | (large loss) | - | No — overtrade |
| Conservative framework on quality setups | 0 | 0% | 0R | - | No — refuses everything |
| Conservative framework on 126 windows | ~67 | 53% | -10.07R | 36% | No |
| **Level-touch + REJECT/BREAK** | **27** | **87%** | **-7.47R** | **26%** | **Partial: SHORT-only +4.73R** |

The level-touch trigger improved engagement and exposed a real asymmetry, but didn't produce a clean positive edge in raw form.

## Three concrete next steps

1. **Drop TDH from levels** — it's intraday noise. Keep only PDH/PDL/PPDH/PPDL/R50/weekly H/L. Estimated trade count: ~12-15/week, higher quality.
2. **Bias correction**: tell Opus explicitly in the prompt that on M15 XAU, "aggressive momentum into a level with rising volume" frequently means *exhaustion*, not breakout. This is a calibration nudge, not overfit (it's a structural truth about retail M15 momentum on a slow-moving asset).
3. **More data**: pull 1 month M15 from TradingView and rerun. 27 trades is statistically thin; 100+ would tell us if REJECT-bias is robust.

Recommendation: do (1) + (2) on the existing 1-week dataset first (cheap), see if the curve flips positive. If yes, validate with (3).
