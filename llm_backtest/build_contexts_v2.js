const fs = require('fs');
const path = require('path');

const { bars, indicators, candidates } = JSON.parse(fs.readFileSync(path.join(__dirname, 'candidates_v2.json'), 'utf8'));

function buildContext(c) {
  const idx = c.idx;
  const lookback = 20;
  const recent = [];
  for (let i = Math.max(0, idx - lookback + 1); i <= idx; i++) {
    const b = bars[i];
    const d = new Date(b.time * 1000);
    const hh = String(d.getUTCHours()).padStart(2, '0');
    const mm = String(d.getUTCMinutes()).padStart(2, '0');
    const dd = String(d.getUTCDate()).padStart(2, '0');
    recent.push(`${dd} ${hh}:${mm} O=${b.open.toFixed(2)} H=${b.high.toFixed(2)} L=${b.low.toFixed(2)} C=${b.close.toFixed(2)} V=${b.volume}`);
  }

  // H1 aggregation: 4 M15 = 1 H1
  function aggregate(start, end) {
    if (start < 0) start = 0;
    const slice = bars.slice(start, end + 1);
    if (slice.length === 0) return null;
    return { open: slice[0].open, high: Math.max(...slice.map(b => b.high)), low: Math.min(...slice.map(b => b.low)), close: slice[slice.length - 1].close };
  }

  const h1 = [];
  let endIdx = idx;
  for (let i = 0; i < 6; i++) {
    const startIdx = endIdx - 3;
    const agg = aggregate(startIdx, endIdx);
    if (agg) h1.unshift(agg);
    endIdx -= 4;
  }
  const h1Str = h1.map((m, i) => `${i+1}: O=${m.open.toFixed(2)} H=${m.high.toFixed(2)} L=${m.low.toFixed(2)} C=${m.close.toFixed(2)} ${m.close > m.open ? 'UP' : 'DN'}`).join('\n  ');

  const ema21 = indicators.ema21[idx];
  const sma50 = indicators.sma50[idx];
  const atr14 = indicators.atr14[idx];
  const rsi14 = indicators.rsi14[idx];

  let swingH = -Infinity, swingL = Infinity;
  for (let i = Math.max(0, idx - 20); i < idx; i++) {
    if (bars[i].high > swingH) swingH = bars[i].high;
    if (bars[i].low < swingL) swingL = bars[i].low;
  }

  return `XAUUSD M15 — Trade Setup Snapshot
Date/Time UTC: ${c.timeStr}
Setup direction (mechanical filter triggered): ${c.side}
Entry price (current bar close): ${c.entry.toFixed(2)}

LAST 20 M15 BARS (oldest → current):
  ${recent.join('\n  ')}

LAST 6 H1 CANDLES (aggregated, oldest → current):
  ${h1Str}

INDICATORS NOW:
  EMA21:   ${ema21.toFixed(2)} (current price ${(c.entry - ema21).toFixed(2)} ${c.entry > ema21 ? 'above' : 'below'})
  SMA50:   ${sma50.toFixed(2)} (current price ${(c.entry - sma50).toFixed(2)} ${c.entry > sma50 ? 'above' : 'below'})
  ATR(14): ${atr14.toFixed(2)}
  RSI(14): ${rsi14.toFixed(1)}
  Last 20-bar swing HIGH: ${swingH.toFixed(2)} (${(swingH - c.entry).toFixed(2)} above entry)
  Last 20-bar swing LOW:  ${swingL.toFixed(2)} (${(c.entry - swingL).toFixed(2)} below entry)

MECHANICAL TRADE PARAMETERS (already locked):
  SL: 1.5×ATR = ${(atr14*1.5).toFixed(2)} ${c.side === 'LONG' ? 'below' : 'above'} entry
  TP: 3.0×ATR = ${(atr14*3.0).toFixed(2)} ${c.side === 'LONG' ? 'above' : 'below'} entry
  Timeout: 8 bars (2h)

YOUR TASK:
You are an expert intraday gold trader. Given ONLY the data above (NO future information), decide if this is a HIGH-QUALITY setup worth taking.

Consider:
- Trend quality across M15 and H1
- Structure (HH/HL or LH/LL pattern)
- Distance to swing extremes vs TP target
- Volatility regime (ATR appropriate?)
- RSI overextended or healthy?
- Candle quality (clean reversal vs choppy?)
- Time of day (London session ~7-12 UTC, NY ~13-20 UTC)

REPLY FORMAT (strict, two lines only, nothing else):
DECISION: ACT
or
DECISION: SKIP
REASON: <one short sentence, max 25 words>
`;
}

const contexts = candidates.map(c => ({ idx: c.idx, side: c.side, time: c.timeStr, prompt: buildContext(c) }));

fs.writeFileSync(path.join(__dirname, 'contexts_v2.json'), JSON.stringify(contexts, null, 2));

const ctxDir = path.join(__dirname, 'contexts_v2');
if (!fs.existsSync(ctxDir)) fs.mkdirSync(ctxDir);
contexts.forEach((c, i) => fs.writeFileSync(path.join(ctxDir, `cand_${String(i).padStart(2,'0')}_${c.side}.txt`), c.prompt));

console.log(`Built ${contexts.length} contexts in contexts_v2/`);
