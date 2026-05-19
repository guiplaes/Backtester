// Liquidity Sweep + Reversal detector for XAUUSD M5
const fs = require('fs');
const path = require('path');

const data = JSON.parse(fs.readFileSync(path.join(__dirname, 'xauusd_m5.json'), 'utf8'));
const bars = data.bars;

function atr(bars, len) {
  const tr = bars.map((b, i) => i === 0 ? b.high - b.low : Math.max(b.high - b.low, Math.abs(b.high - bars[i-1].close), Math.abs(b.low - bars[i-1].close)));
  const out = [];
  let prev = tr.slice(0, len).reduce((a,b)=>a+b,0)/len;
  for (let i = 0; i < tr.length; i++) {
    if (i < len-1) { out.push(NaN); continue; }
    if (i === len-1) { out.push(prev); continue; }
    prev = (prev*(len-1)+tr[i])/len;
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
    else { avgGain = (avgGain*(len-1)+g)/len; avgLoss = (avgLoss*(len-1)+l)/len; out.push(100 - 100/(1 + avgGain/Math.max(avgLoss, 1e-10))); }
  }
  return out;
}

const atr14 = atr(bars, 14);
const rsi14 = rsi(bars.map(b => b.close), 14);

const SWING_LOOKBACK = 20;
const WICK_MIN_ATR = 0.3;
const candidates = [];

for (let i = SWING_LOOKBACK; i < bars.length - 12; i++) {
  const b = bars[i];
  const a = atr14[i];
  if (isNaN(a)) continue;

  // Swing high/low from previous N bars (exclude current)
  let swingHigh = -Infinity, swingLow = Infinity;
  for (let j = i - SWING_LOOKBACK; j < i; j++) {
    if (bars[j].high > swingHigh) swingHigh = bars[j].high;
    if (bars[j].low < swingLow) swingLow = bars[j].low;
  }

  // Time filter: active hours
  const date = new Date(b.time * 1000);
  const hourUTC = date.getUTCHours();
  if (hourUTC < 6 || hourUTC >= 20) continue;

  // SHORT setup: wick swept swing high, closed back below
  const sweptHigh = b.high > swingHigh;
  const closedBelow = b.close < swingHigh;
  const wickAbove = b.high - swingHigh;
  const wickOk = wickAbove > a * WICK_MIN_ATR;
  const bearishClose = b.close < b.open;
  const shortSetup = sweptHigh && closedBelow && wickOk && bearishClose;

  // LONG setup: wick swept swing low, closed back above
  const sweptLow = b.low < swingLow;
  const closedAbove = b.close > swingLow;
  const wickBelow = swingLow - b.low;
  const wickOkLong = wickBelow > a * WICK_MIN_ATR;
  const bullishClose = b.close > b.open;
  const longSetup = sweptLow && closedAbove && wickOkLong && bullishClose;

  if (longSetup || shortSetup) {
    candidates.push({
      idx: i, time: b.time, timeStr: date.toISOString(), hourUTC,
      side: longSetup ? 'LONG' : 'SHORT',
      entry: b.close,
      swingLevel: longSetup ? swingLow : swingHigh,
      wickSize: longSetup ? wickBelow : wickAbove,
      atr14: a, rsi14: rsi14[i],
    });
  }
}

console.log('Total bars:', bars.length);
console.log('Period:', new Date(bars[0].time*1000).toISOString(), '→', new Date(bars[bars.length-1].time*1000).toISOString());
console.log('Liquidity Sweep candidates:', candidates.length, '(LONG:', candidates.filter(c=>c.side==='LONG').length, 'SHORT:', candidates.filter(c=>c.side==='SHORT').length+')');

candidates.forEach((c,i) => {
  const wickAtr = (c.wickSize / c.atr14).toFixed(2);
  console.log(`  ${String(i).padStart(2)}: ${c.timeStr} ${c.side.padEnd(5)} @ ${c.entry.toFixed(2)} swept ${c.swingLevel.toFixed(2)} (wick ${c.wickSize.toFixed(2)}=${wickAtr}×ATR)`);
});

// Simulate
function simulate(c) {
  const entry = c.entry, atr = c.atr14;
  let sl, tp;
  if (c.side === 'LONG') {
    sl = c.swingLevel - atr * 0.1;  // just below swept low
    tp = entry + (entry - sl) * 2.0;  // 2R
  } else {
    sl = c.swingLevel + atr * 0.1;  // just above swept high
    tp = entry - (sl - entry) * 2.0;
  }
  const r_dist = Math.abs(entry - sl);
  for (let i = c.idx + 1; i <= c.idx + 12 && i < bars.length; i++) {
    const b = bars[i];
    if (c.side === 'LONG') {
      if (b.low <= sl) return { outcome: 'SL', pnlR: -1, barsHeld: i - c.idx, sl, tp };
      if (b.high >= tp) return { outcome: 'TP', pnlR: 2, barsHeld: i - c.idx, sl, tp };
    } else {
      if (b.high >= sl) return { outcome: 'SL', pnlR: -1, barsHeld: i - c.idx, sl, tp };
      if (b.low <= tp) return { outcome: 'TP', pnlR: 2, barsHeld: i - c.idx, sl, tp };
    }
  }
  const last = bars[Math.min(c.idx + 12, bars.length - 1)];
  const mv = c.side === 'LONG' ? (last.close - entry) : (entry - last.close);
  return { outcome: 'TIMEOUT', pnlR: mv / r_dist, barsHeld: 12, sl, tp };
}

const results = candidates.map(c => ({ ...c, ...simulate(c) }));
const n = results.length;
const wins = results.filter(t => t.pnlR > 0);
const losses = results.filter(t => t.pnlR < 0);
const gw = wins.reduce((s,t)=>s+t.pnlR,0);
const gl = Math.abs(losses.reduce((s,t)=>s+t.pnlR,0));
const total = results.reduce((s,t)=>s+t.pnlR,0);

console.log(`\n=== Liquidity Sweep MECHANICAL BASELINE ===`);
console.log(`Trades: ${n} | WR: ${(wins.length/n*100).toFixed(1)}% (${wins.length}/${n})`);
console.log(`Gross W: +${gw.toFixed(2)}R | Gross L: -${gl.toFixed(2)}R`);
console.log(`Net: ${total.toFixed(2)}R | PF: ${gl>0?(gw/gl).toFixed(2):'∞'} | Exp: ${(total/n).toFixed(3)}R/t`);

console.log('\nDetail:');
results.forEach((r,i) => console.log(`  ${String(i).padStart(2)}: ${r.timeStr} ${r.side.padEnd(5)} @${r.entry.toFixed(2)} → ${r.outcome.padEnd(8)} (${r.pnlR.toFixed(2).padStart(5)}R, SL ${r.sl.toFixed(2)})`));

fs.writeFileSync(path.join(__dirname, 'sweep_results.json'), JSON.stringify({ bars, atr14, rsi14, results }, null, 2));
