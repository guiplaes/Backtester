// Build per-candidate context (only past+current data, no future)
const fs = require('fs');
const path = require('path');

const { bars, indicators, candidates } = JSON.parse(fs.readFileSync(path.join(__dirname, 'candidates.json'), 'utf8'));

function buildContext(c) {
  const idx = c.idx;
  const lookback = 20;
  const recent = [];
  for (let i = Math.max(0, idx - lookback + 1); i <= idx; i++) {
    const b = bars[i];
    const d = new Date(b.time * 1000);
    const hh = String(d.getUTCHours()).padStart(2, '0');
    const mm = String(d.getUTCMinutes()).padStart(2, '0');
    recent.push(`${hh}:${mm} O=${b.open.toFixed(2)} H=${b.high.toFixed(2)} L=${b.low.toFixed(2)} C=${b.close.toFixed(2)} V=${b.volume}`);
  }

  // HTF context: M15 = aggregate every 3 M5 bars, M30 = every 6
  function aggregate(start, end, n) {
    if (start < 0) start = 0;
    const slice = bars.slice(start, end + 1);
    if (slice.length === 0) return null;
    return {
      open: slice[0].open,
      high: Math.max(...slice.map(b => b.high)),
      low: Math.min(...slice.map(b => b.low)),
      close: slice[slice.length - 1].close,
    };
  }

  const m15 = [];
  // last 5 M15 candles using 3 M5 bars each, aligned backwards from current
  let endIdx = idx;
  for (let i = 0; i < 5; i++) {
    const startIdx = endIdx - 2;
    const agg = aggregate(startIdx, endIdx, 3);
    if (agg) m15.unshift(agg);
    endIdx -= 3;
  }
  const m15Str = m15.map((m, i) => `${i+1}: O=${m.open.toFixed(2)} H=${m.high.toFixed(2)} L=${m.low.toFixed(2)} C=${m.close.toFixed(2)} ${m.close > m.open ? 'UP' : 'DN'}`).join('\n  ');

  const ema21 = indicators.ema21[idx];
  const ema21_5ago = indicators.ema21[idx - 5];
  const atr14 = indicators.atr14[idx];
  const rsi14 = indicators.rsi14[idx];

  // Recent swing high/low (last 30 bars)
  let swingH = -Infinity, swingL = Infinity;
  for (let i = Math.max(0, idx - 30); i <= idx; i++) {
    if (bars[i].high > swingH) swingH = bars[i].high;
    if (bars[i].low < swingL) swingL = bars[i].low;
  }

  const distSwingH = (swingH - c.entry).toFixed(2);
  const distSwingL = (c.entry - swingL).toFixed(2);

  return `XAUUSD M5 — Setup detection
Date/Time UTC: ${c.timeStr}
Hour UTC: ${c.hourUTC}:${String(new Date(c.time*1000).getUTCMinutes()).padStart(2,'0')}
Setup direction (mechanical filter triggered): ${c.side}
Entry price (current bar close): ${c.entry.toFixed(2)}

LAST 20 M5 BARS (oldest → current):
  ${recent.join('\n  ')}

LAST 5 M15 CANDLES (oldest → current):
  ${m15Str}

INDICATORS NOW:
  EMA21: ${ema21.toFixed(2)} (slope vs 5 bars ago: ${(ema21 - ema21_5ago).toFixed(2)})
  ATR(14): ${atr14.toFixed(2)}
  RSI(14): ${rsi14.toFixed(1)}
  Last 30-bar swing HIGH: ${swingH.toFixed(2)} (dist ${distSwingH} above entry)
  Last 30-bar swing LOW: ${swingL.toFixed(2)} (dist ${distSwingL} below entry)

MECHANICAL TRADE PARAMETERS (already locked):
  SL: 1.5×ATR = ${(atr14*1.5).toFixed(2)}
  TP: 3.0×ATR = ${(atr14*3.0).toFixed(2)} (R:R = 2:1)
  Timeout: 12 bars (1 hour)

YOUR TASK:
Given ONLY the data above (NO future information), evaluate if this is a HIGH-QUALITY setup worth taking.
Consider: trend quality, structure (HH/HL or LH/LL), distance to extremes, volatility regime, time of day, candle quality, multi-TF alignment.

REPLY FORMAT (strict, one line only):
DECISION: ACT | SKIP
REASON: (one short sentence, max 20 words)
`;
}

const contexts = candidates.map(c => ({ idx: c.idx, side: c.side, time: c.timeStr, prompt: buildContext(c) }));

fs.writeFileSync(path.join(__dirname, 'contexts.json'), JSON.stringify(contexts, null, 2));

// Also write each context to its own file for review
const ctxDir = path.join(__dirname, 'contexts');
if (!fs.existsSync(ctxDir)) fs.mkdirSync(ctxDir);
contexts.forEach((c, i) => {
  fs.writeFileSync(path.join(ctxDir, `cand_${String(i).padStart(2,'0')}_${c.side}.txt`), c.prompt);
});

console.log(`Built ${contexts.length} contexts in contexts/`);
console.log('\nSample context (first candidate):');
console.log(contexts[0].prompt);
