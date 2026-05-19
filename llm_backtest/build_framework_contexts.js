const fs = require('fs');
const path = require('path');

const data = JSON.parse(fs.readFileSync(path.join(__dirname, 'quality_setups.json'), 'utf8'));
const { setups, bars, indicators } = data;

function getSession(h) {
  if (h < 7) return 'Asia (low liq)';
  if (h < 13) return 'London';
  if (h < 17) return 'NY';
  if (h < 21) return 'NY close';
  return 'Late/quiet';
}

function aggregateH1(barsArr, endIdx, count) {
  const h1 = [];
  let idx = endIdx;
  while (h1.length < count && idx >= 3) {
    const slice = barsArr.slice(idx - 3, idx + 1);
    h1.unshift({
      open: slice[0].open,
      high: Math.max(...slice.map(b => b.high)),
      low: Math.min(...slice.map(b => b.low)),
      close: slice[slice.length - 1].close,
    });
    idx -= 4;
  }
  return h1;
}

function pdhPdl(barsArr, currentIdx) {
  const currentDay = new Date(barsArr[currentIdx].time * 1000).getUTCDate();
  let pdh = -Infinity, pdl = Infinity, foundDay = null;
  for (let j = currentIdx - 1; j >= 0; j--) {
    const d = new Date(barsArr[j].time * 1000).getUTCDate();
    if (d === currentDay) continue;
    if (foundDay === null) foundDay = d;
    if (d !== foundDay) break;
    if (barsArr[j].high > pdh) pdh = barsArr[j].high;
    if (barsArr[j].low < pdl) pdl = barsArr[j].low;
  }
  return { pdh, pdl };
}

const ctxDir = path.join(__dirname, 'contexts_quality');
if (!fs.existsSync(ctxDir)) fs.mkdirSync(ctxDir);

setups.forEach((s, idx) => {
  // Last 40 M15 bars
  const recent = [];
  for (let j = Math.max(0, s.idx - 40 + 1); j <= s.idx; j++) {
    const b = bars[j];
    const d = new Date(b.time * 1000);
    const dd = String(d.getUTCDate()).padStart(2,'0');
    const hh = String(d.getUTCHours()).padStart(2,'0');
    const mm = String(d.getUTCMinutes()).padStart(2,'0');
    recent.push(`${dd} ${hh}:${mm} O=${b.open.toFixed(2)} H=${b.high.toFixed(2)} L=${b.low.toFixed(2)} C=${b.close.toFixed(2)} V=${b.volume}`);
  }

  const h1 = aggregateH1(bars, s.idx, 6);
  const h1Str = h1.map(h => `O=${h.open.toFixed(2)} H=${h.high.toFixed(2)} L=${h.low.toFixed(2)} C=${h.close.toFixed(2)} ${h.close > h.open ? 'BULL' : 'BEAR'}`).join('\n  ');

  const { pdh, pdl } = pdhPdl(bars, s.idx);

  let avgVol = 0;
  for (let j = s.idx - 20; j <= s.idx; j++) avgVol += bars[j].volume;
  avgVol /= 21;
  const volRatio = bars[s.idx].volume / avgVol;

  const ctx = `XAUUSD M15 — Trading Decision Context

⏰ TIME: ${s.timeStr} | UTC ${s.hourUTC}:00 | Session: ${getSession(s.hourUTC)}
💰 Current price: ${s.currentPrice.toFixed(2)}

━━━ MECHANICAL FILTER FLAGGED THIS AS: ${s.setupType} ━━━
Suggested direction: ${s.direction}

(But you are the trader. The mechanical filter only sees text patterns. You decide whether this is a REAL high-quality opportunity or a false signal. Skip if the context doesn't support it.)

━━━ KEY LEVELS ━━━
PDH: ${pdh.toFixed(2)} (${(pdh - s.currentPrice >= 0 ? '+' : '')}${(pdh - s.currentPrice).toFixed(2)} from current)
PDL: ${pdl.toFixed(2)} (${(pdl - s.currentPrice >= 0 ? '+' : '')}${(pdl - s.currentPrice).toFixed(2)} from current)
20-bar Swing HIGH: ${s.swingH.toFixed(2)} (+${(s.swingH - s.currentPrice).toFixed(2)})
20-bar Swing LOW:  ${s.swingL.toFixed(2)} (${(s.swingL - s.currentPrice).toFixed(2)})
Position in 20-bar range: ${(s.rangePos * 100).toFixed(0)}%

━━━ TREND CONTEXT ━━━
EMA21:        ${s.ema21.toFixed(2)} (price ${s.currentPrice > s.ema21 ? 'above' : 'below'} by ${Math.abs(s.currentPrice - s.ema21).toFixed(2)})
SMA50:        ${s.sma50.toFixed(2)} (price ${s.currentPrice > s.sma50 ? 'above' : 'below'})
SMA50 slope:  ${s.sma50_slope > 0 ? '+' : ''}${s.sma50_slope.toFixed(2)} over last 5 bars
ATR(14):      ${s.atr14.toFixed(2)}
RSI(14):      ${s.rsi14.toFixed(1)} ${s.rsi14 > 70 ? '(EXTREME high)' : s.rsi14 < 30 ? '(EXTREME low)' : ''}
Volume current: ${bars[s.idx].volume} (${volRatio.toFixed(2)}× avg20)

━━━ H1 CONTEXT (last 6) ━━━
  ${h1Str}

━━━ M15 BARS (last 40) ━━━
  ${recent.join('\n  ')}

═══════════════════════════════════════════════════════════════
🧠 TRADING DECISION FRAMEWORK

You are an XAUUSD intraday trader analyzing this moment. Reason through this systematically:

STEP 1: DOMINANT TREND
  - M15 and H1 — what's the trend direction and strength?
  - Are they aligned or conflicting?

STEP 2: PRICE LOCATION
  - Is price at an extreme (top/bottom of range)?
  - At a key level (PDH/PDL/swing)?
  - In the middle of a move (continuation potential)?
  - At a pullback to EMA (high-probability spot)?

STEP 3: SCENARIO PROBABILITY
  Consider both directional scenarios:
  - CONTINUATION: trend continues — when likely?
  - REVERSAL: trend exhausts — when likely?
  - RANGE BOUNCE: chop continues — when likely?

  Key signals:
  - Vertical move + RSI extreme + at swing = LIKELY exhaustion
  - Pullback to MA + reversal candle + trend intact = LIKELY continuation
  - Failed breakout = LIKELY mean revert
  - Compression then expansion = LIKELY directional move

STEP 4: ASYMMETRY CHECK
  - Where would your SL be? (must be at a logical structure point)
  - Where would your TP be? (must be at next clear level)
  - Is the R:R asymmetric (>1.5)?

STEP 5: ACT or SKIP
  - ACT only if you have clear bias AND asymmetric R:R AND specific scenario
  - SKIP if: ambiguous, no clean entry, poor R:R, or you're talking yourself into it

REPLY EXACTLY in this format:
TREND: <description of trend M15 and H1>
LOCATION: <where price is>
SCENARIO: <most likely scenario, with reasoning>
ASYMMETRY: <where SL would be, where TP would be>
DECISION: NO_TRADE / LONG / SHORT
ENTRY: <price> or -
SL: <price> or -
TP: <price> or -
CONFIDENCE: <1-10>
REASON: <2-3 sentence summary>
`;
  fs.writeFileSync(path.join(ctxDir, `q_${String(idx).padStart(3,'0')}.txt`), ctx);
});

fs.writeFileSync(path.join(__dirname, 'quality_meta.json'), JSON.stringify({ setups, bars }, null, 2));
console.log(`Built ${setups.length} framework contexts in contexts_quality/`);
