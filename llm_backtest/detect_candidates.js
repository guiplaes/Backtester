// Detect mechanical candidates on XAUUSD M5 data
// Setup: pullback to EMA21 in mini-trend, during London+NY hours

const fs = require('fs');
const path = require('path');

const data = JSON.parse(fs.readFileSync(path.join(__dirname, 'xauusd_m5.json'), 'utf8'));
const bars = data.bars;

// ===== Indicators =====
function ema(arr, len) {
  const k = 2 / (len + 1);
  const out = [];
  let prev = arr[0];
  for (let i = 0; i < arr.length; i++) {
    prev = i === 0 ? arr[i] : arr[i] * k + prev * (1 - k);
    out.push(prev);
  }
  return out;
}

function atr(bars, len) {
  const tr = bars.map((b, i) => {
    if (i === 0) return b.high - b.low;
    return Math.max(
      b.high - b.low,
      Math.abs(b.high - bars[i - 1].close),
      Math.abs(b.low - bars[i - 1].close)
    );
  });
  // Wilder smoothing
  const out = [];
  let prev = tr.slice(0, len).reduce((a, b) => a + b, 0) / len;
  for (let i = 0; i < tr.length; i++) {
    if (i < len - 1) { out.push(NaN); continue; }
    if (i === len - 1) { out.push(prev); continue; }
    prev = (prev * (len - 1) + tr[i]) / len;
    out.push(prev);
  }
  return out;
}

function rsi(arr, len) {
  const out = [];
  let avgGain = 0, avgLoss = 0;
  for (let i = 0; i < arr.length; i++) {
    if (i === 0) { out.push(NaN); continue; }
    const change = arr[i] - arr[i - 1];
    const gain = Math.max(change, 0);
    const loss = Math.max(-change, 0);
    if (i <= len) {
      avgGain += gain / len;
      avgLoss += loss / len;
      out.push(i < len ? NaN : (100 - 100 / (1 + avgGain / Math.max(avgLoss, 1e-10))));
    } else {
      avgGain = (avgGain * (len - 1) + gain) / len;
      avgLoss = (avgLoss * (len - 1) + loss) / len;
      out.push(100 - 100 / (1 + avgGain / Math.max(avgLoss, 1e-10)));
    }
  }
  return out;
}

const closes = bars.map(b => b.close);
const ema21Arr = ema(closes, 21);
const atr14Arr = atr(bars, 14);
const rsi14Arr = rsi(closes, 14);

// ===== Candidate detection =====
const candidates = [];

for (let i = 25; i < bars.length - 12; i++) {
  const b = bars[i];
  const ema21 = ema21Arr[i];
  const ema21_5ago = ema21Arr[i - 5];
  const atr14 = atr14Arr[i];
  if (isNaN(atr14)) continue;

  // Time filter: 06:00-20:00 UTC (London + NY active)
  const date = new Date(b.time * 1000);
  const hourUTC = date.getUTCHours();
  if (hourUTC < 6 || hourUTC >= 20) continue;

  // Trend filter: EMA21 slope (relaxed)
  const slopeUp = ema21 > ema21_5ago + atr14 * 0.02;
  const slopeDn = ema21 < ema21_5ago - atr14 * 0.02;

  // Body
  const body = Math.abs(b.close - b.open);
  const bodyOk = body >= atr14 * 0.2;

  // LONG: low touched EMA21 within 0.4×ATR band
  const longTouch = b.low <= ema21 + atr14 * 0.3 && b.low >= ema21 - atr14 * 0.7;
  const longClose = b.close > ema21;
  const longBull  = b.close > b.open;
  const longSetup = slopeUp && longTouch && longClose && longBull && bodyOk;

  // SHORT mirror
  const shortTouch = b.high >= ema21 - atr14 * 0.3 && b.high <= ema21 + atr14 * 0.7;
  const shortClose = b.close < ema21;
  const shortBear  = b.close < b.open;
  const shortSetup = slopeDn && shortTouch && shortClose && shortBear && bodyOk;

  if (longSetup || shortSetup) {
    candidates.push({
      idx: i,
      time: b.time,
      timeStr: date.toISOString(),
      hourUTC,
      side: longSetup ? 'LONG' : 'SHORT',
      entry: b.close,
      ema21,
      atr14,
      rsi14: rsi14Arr[i],
      bar: b,
    });
  }
}

console.log('Total bars:', bars.length);
console.log('Candidates detected:', candidates.length);
console.log('LONG:', candidates.filter(c => c.side === 'LONG').length);
console.log('SHORT:', candidates.filter(c => c.side === 'SHORT').length);

fs.writeFileSync(
  path.join(__dirname, 'candidates.json'),
  JSON.stringify({ bars, indicators: { ema21: ema21Arr, atr14: atr14Arr, rsi14: rsi14Arr }, candidates }, null, 2)
);

console.log('\nSaved candidates.json');
console.log('\nFirst 5 candidates:');
candidates.slice(0, 5).forEach(c => {
  console.log(`  ${c.timeStr} ${c.side} @ ${c.entry.toFixed(2)} ATR=${c.atr14.toFixed(2)} RSI=${c.rsi14.toFixed(1)}`);
});
