// Build ENRICHED contexts: PDH/PDL, H1 aggregation, structure, volume, sessions
const fs = require('fs');
const path = require('path');

const data = JSON.parse(fs.readFileSync(path.join(__dirname, 'interest_moments.json'), 'utf8'));
const { moments, bars, indicators } = data;

function getSession(hourUTC) {
  if (hourUTC >= 0 && hourUTC < 7) return 'Asia';
  if (hourUTC >= 7 && hourUTC < 12) return 'London';
  if (hourUTC >= 12 && hourUTC < 13) return 'London/NY overlap';
  if (hourUTC >= 13 && hourUTC < 17) return 'NY';
  if (hourUTC >= 17 && hourUTC < 21) return 'NY close';
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
      volume: slice.reduce((s,b) => s + b.volume, 0),
    });
    idx -= 4;
  }
  return h1;
}

function pdhPdl(barsArr, currentIdx) {
  const currentTime = new Date(barsArr[currentIdx].time * 1000);
  const currentDay = currentTime.getUTCDate();
  let pdh = -Infinity, pdl = Infinity, foundDay = null;
  for (let j = currentIdx - 1; j >= 0; j--) {
    const t = new Date(barsArr[j].time * 1000);
    if (t.getUTCDate() === currentDay) continue;
    if (foundDay === null) foundDay = t.getUTCDate();
    if (t.getUTCDate() !== foundDay) break;
    if (barsArr[j].high > pdh) pdh = barsArr[j].high;
    if (barsArr[j].low < pdl) pdl = barsArr[j].low;
  }
  return { pdh, pdl };
}

function characterizeStructure(barsArr, idx) {
  // Look at last 20 bars: identify swings
  const recent = barsArr.slice(idx - 20, idx + 1);
  const closes = recent.map(b => b.close);
  const highs = recent.map(b => b.high);
  const lows = recent.map(b => b.low);

  const maxH = Math.max(...highs);
  const minL = Math.min(...lows);
  const range = maxH - minL;
  const currentP = closes[closes.length - 1];
  const positionInRange = (currentP - minL) / range;

  // Trend slope: simple linear regression on closes
  const n = closes.length;
  const meanX = (n - 1) / 2;
  const meanY = closes.reduce((a,b) => a+b, 0) / n;
  let num = 0, den = 0;
  for (let i = 0; i < n; i++) {
    num += (i - meanX) * (closes[i] - meanY);
    den += (i - meanX) ** 2;
  }
  const slope = num / den;
  const atr = indicators.atr14[idx];
  const slopeATR = slope / atr;

  let trend = 'sideways';
  if (slopeATR > 0.05) trend = 'uptrend';
  else if (slopeATR < -0.05) trend = 'downtrend';

  // HH/HL or LH/LL recent (last 10 bars)
  let highsCount = 0, lowsCount = 0;
  for (let i = recent.length - 5; i < recent.length; i++) {
    if (recent[i].high > recent[i-3].high) highsCount++;
    if (recent[i].low < recent[i-3].low) lowsCount++;
  }

  let structure = '';
  if (highsCount >= 3 && lowsCount <= 1) structure = 'HH/HL (uptrend)';
  else if (lowsCount >= 3 && highsCount <= 1) structure = 'LH/LL (downtrend)';
  else if (highsCount === 0 && lowsCount === 0) structure = 'compression';
  else structure = 'mixed/chop';

  return { trend, structure, range, positionInRange, slopeATR };
}

const LOOKBACK = 50;
const filtered = moments.filter(m => m.score >= 3);
console.log(`Building enriched contexts for ${filtered.length} candidates (score >= 3)`);

const ctxDir = path.join(__dirname, 'contexts_rich');
if (!fs.existsSync(ctxDir)) fs.mkdirSync(ctxDir);

filtered.forEach((m, idx) => {
  const recent = [];
  for (let j = Math.max(0, m.idx - LOOKBACK + 1); j <= m.idx; j++) {
    const b = bars[j];
    const d = new Date(b.time * 1000);
    const dd = String(d.getUTCDate()).padStart(2,'0');
    const hh = String(d.getUTCHours()).padStart(2,'0');
    const mm = String(d.getUTCMinutes()).padStart(2,'0');
    recent.push(`${dd} ${hh}:${mm} O=${b.open.toFixed(2)} H=${b.high.toFixed(2)} L=${b.low.toFixed(2)} C=${b.close.toFixed(2)} V=${b.volume}`);
  }

  const h1 = aggregateH1(bars, m.idx, 8);
  const h1Str = h1.map((h, i) => `H-${h1.length-i}: O=${h.open.toFixed(2)} H=${h.high.toFixed(2)} L=${h.low.toFixed(2)} C=${h.close.toFixed(2)} V=${h.volume} ${h.close > h.open ? 'BULL' : 'BEAR'}`).join('\n  ');

  const { pdh, pdl } = pdhPdl(bars, m.idx);
  const struct = characterizeStructure(bars, m.idx);
  const session = getSession(m.hourUTC);

  // Volume context
  let avgVol = 0;
  for (let j = m.idx - 20; j <= m.idx; j++) avgVol += bars[j].volume;
  avgVol /= 21;
  const currentVol = bars[m.idx].volume;
  const volRatio = currentVol / avgVol;

  const ctx = `XAUUSD M15 — Trade Decision Snapshot
═══════════════════════════════════════════════════════════════

⏰ MOMENT: ${m.timeStr}
   Hour UTC: ${m.hourUTC}:${String(new Date(m.time*1000).getUTCMinutes()).padStart(2,'0')}
   Session: ${session}
   Current price: ${m.currentPrice.toFixed(2)}

🎯 PRE-FILTER SCORE: ${m.score}/6
   Signals firing: ${m.reasons.join(', ')}

━━━ KEY LEVELS ━━━
   Previous Day High (PDH): ${pdh.toFixed(2)} (${(pdh - m.currentPrice >= 0 ? '+' : '')}${(pdh - m.currentPrice).toFixed(2)})
   Previous Day Low (PDL):  ${pdl.toFixed(2)} (${(pdl - m.currentPrice >= 0 ? '+' : '')}${(pdl - m.currentPrice).toFixed(2)})
   30-bar swing HIGH: ${m.swingH.toFixed(2)} (+${(m.swingH - m.currentPrice).toFixed(2)})
   30-bar swing LOW:  ${m.swingL.toFixed(2)} (${(m.swingL - m.currentPrice).toFixed(2)})

━━━ STRUCTURE (last 20 bars) ━━━
   Trend slope: ${struct.trend} (${struct.slopeATR.toFixed(3)} ATR/bar)
   Structure: ${struct.structure}
   Range: ${struct.range.toFixed(2)} (${(struct.range / m.atr14).toFixed(1)}× ATR)
   Position in range: ${(struct.positionInRange * 100).toFixed(0)}% from low

━━━ INDICATORS ━━━
   EMA21: ${m.ema21.toFixed(2)} (${m.currentPrice > m.ema21 ? 'price above' : 'price below'}, dist=${(m.currentPrice - m.ema21).toFixed(2)})
   SMA50: ${m.sma50.toFixed(2)} (${m.currentPrice > m.sma50 ? 'price above' : 'price below'})
   ATR(14): ${m.atr14.toFixed(2)}
   RSI(14): ${m.rsi14.toFixed(1)} ${m.rsi14 > 70 ? '(OVERBOUGHT)' : m.rsi14 < 30 ? '(OVERSOLD)' : ''}
   Volume current: ${currentVol} (avg20: ${avgVol.toFixed(0)}, ratio: ${volRatio.toFixed(2)}×)

━━━ M15 BARS (last ${LOOKBACK}, oldest → current) ━━━
  ${recent.join('\n  ')}

━━━ H1 CONTEXT (last ${h1.length} candles aggregated) ━━━
  ${h1Str}

═══════════════════════════════════════════════════════════════
YOUR TASK:
You are an expert XAUUSD intraday trader analyzing this moment. Decide if you would take a trade in the next 2 hours (8 M15 bars).

CONSIDER:
- Where is price vs key levels (PDH/PDL/swings/EMAs)?
- What's the trend on M15 AND H1? Are they aligned?
- Is structure HH/HL, LL/LH, range, or compression?
- Is volume confirming or fading?
- RSI extreme or neutral?
- Session: is this an active time for moves?
- Risk-reward: where is asymmetric opportunity?

REPLY in this EXACT format:
DECISION: NO_TRADE / LONG / SHORT
ENTRY: <price> or -
SL: <price> or -
TP: <price> or -
CONFIDENCE: 1-10
REASON: <2-3 sentences with key insights driving decision>
`;
  fs.writeFileSync(path.join(ctxDir, `m_${String(idx).padStart(3,'0')}.txt`), ctx);
});

// Save filtered moments + bars
fs.writeFileSync(path.join(__dirname, 'rich_meta.json'), JSON.stringify({ moments: filtered, bars }, null, 2));
console.log(`Built ${filtered.length} rich contexts in contexts_rich/`);
console.log(`First moment: ${filtered[0].timeStr}`);
console.log(`Last moment: ${filtered[filtered.length-1].timeStr}`);
