// v2: trend-day pullback on M15
const fs = require('fs');
const path = require('path');

const data = JSON.parse(fs.readFileSync(path.join(__dirname, 'xauusd_m15.json'), 'utf8'));
const bars = data.bars;

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
function sma(arr, len) {
  const out = [];
  for (let i = 0; i < arr.length; i++) {
    if (i < len - 1) { out.push(NaN); continue; }
    let s = 0; for (let j = i - len + 1; j <= i; j++) s += arr[j];
    out.push(s / len);
  }
  return out;
}
function atr(bars, len) {
  const tr = bars.map((b, i) => i === 0 ? b.high - b.low : Math.max(b.high - b.low, Math.abs(b.high - bars[i-1].close), Math.abs(b.low - bars[i-1].close)));
  const out = [];
  let prev = tr.slice(0, len).reduce((a,b)=>a+b,0)/len;
  for (let i = 0; i < tr.length; i++) {
    if (i < len - 1) { out.push(NaN); continue; }
    if (i === len - 1) { out.push(prev); continue; }
    prev = (prev*(len-1) + tr[i])/len;
    out.push(prev);
  }
  return out;
}
function rsi(arr, len) {
  const out = [];
  let avgGain = 0, avgLoss = 0;
  for (let i = 0; i < arr.length; i++) {
    if (i === 0) { out.push(NaN); continue; }
    const ch = arr[i] - arr[i-1];
    const g = Math.max(ch, 0), l = Math.max(-ch, 0);
    if (i <= len) { avgGain += g/len; avgLoss += l/len; out.push(i < len ? NaN : 100 - 100/(1 + avgGain/Math.max(avgLoss, 1e-10))); }
    else { avgGain = (avgGain*(len-1)+g)/len; avgLoss = (avgLoss*(len-1)+l)/len; out.push(100 - 100/(1 + avgGain/Math.max(avgLoss,1e-10))); }
  }
  return out;
}

const closes = bars.map(b => b.close);
const ema21 = ema(closes, 21);
const sma50 = sma(closes, 50);
const atr14 = atr(bars, 14);
const rsi14 = rsi(closes, 14);

const candidates = [];

for (let i = 60; i < bars.length - 8; i++) {
  const b = bars[i];
  const e = ema21[i];
  const s = sma50[i];
  const s5 = sma50[i-5];
  const a = atr14[i];
  if (isNaN(a) || isNaN(s) || isNaN(s5)) continue;

  const date = new Date(b.time * 1000);
  const hourUTC = date.getUTCHours();
  if (hourUTC < 6 || hourUTC >= 20) continue;

  // Recent swing high/low over last 20 bars (excluding current)
  let sh = -Infinity, sl = Infinity;
  for (let j = Math.max(0, i-20); j < i; j++) {
    if (bars[j].high > sh) sh = bars[j].high;
    if (bars[j].low < sl) sl = bars[j].low;
  }

  // Trend regime: SMA50 slope + price relative to SMA50 (relaxed)
  const trendUp   = s > s5 + a * 0.05 && b.close > s;
  const trendDown = s < s5 - a * 0.05 && b.close < s;

  const body = Math.abs(b.close - b.open);
  const upperWick = b.high - Math.max(b.close, b.open);
  const lowerWick = Math.min(b.close, b.open) - b.low;
  const bodyOk = body >= a * 0.15;

  // LONG: trend up, pullback to EMA21, bullish reversal
  const longTouch = b.low <= e + a * 0.4 && b.low >= e - a * 0.7;
  const longClose = b.close > e;
  const longBull = b.close > b.open;
  const longRoom = (sh - b.close) > a * 1.5;

  const longSetup = trendUp && longTouch && longClose && longBull && bodyOk && longRoom;

  // SHORT mirror
  const shortTouch = b.high >= e - a * 0.4 && b.high <= e + a * 0.7;
  const shortClose = b.close < e;
  const shortBear = b.close < b.open;
  const shortRoom = (b.close - sl) > a * 1.5;

  const shortSetup = trendDown && shortTouch && shortClose && shortBear && bodyOk && shortRoom;

  if (longSetup || shortSetup) {
    candidates.push({
      idx: i, time: b.time, timeStr: date.toISOString(), hourUTC,
      side: longSetup ? 'LONG' : 'SHORT',
      entry: b.close, ema21: e, sma50: s, atr14: a, rsi14: rsi14[i],
      swingHigh: sh, swingLow: sl,
      bar: b,
    });
  }
}

console.log('Total bars:', bars.length);
console.log('Candidates:', candidates.length, '(LONG:', candidates.filter(c=>c.side==='LONG').length, 'SHORT:', candidates.filter(c=>c.side==='SHORT').length+')');
candidates.forEach((c,i) => console.log(`  ${String(i).padStart(2)}: ${c.timeStr} ${c.side} @ ${c.entry.toFixed(2)} ATR=${c.atr14.toFixed(2)} RSI=${c.rsi14.toFixed(1)}`));

fs.writeFileSync(path.join(__dirname, 'candidates_v2.json'), JSON.stringify({ bars, indicators: { ema21, sma50, atr14, rsi14 }, candidates }, null, 2));
