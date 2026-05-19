// Validate v2 filter on XAUUSD H1 (5 weeks of data)
// SAME parameters as v2 — no tuning, no curve-fitting.

const fs = require('fs');
const path = require('path');

const data = JSON.parse(fs.readFileSync(path.join(__dirname, 'xauusd_h1.json'), 'utf8'));
const bars = data.bars;

function ema(arr, len) { const k = 2/(len+1); const out = []; let prev = arr[0]; for (let i = 0; i < arr.length; i++) { prev = i === 0 ? arr[i] : arr[i]*k + prev*(1-k); out.push(prev); } return out; }
function sma(arr, len) { const out = []; for (let i = 0; i < arr.length; i++) { if (i < len-1) { out.push(NaN); continue; } let s = 0; for (let j = i-len+1; j <= i; j++) s += arr[j]; out.push(s/len); } return out; }
function atr(bars, len) { const tr = bars.map((b, i) => i === 0 ? b.high - b.low : Math.max(b.high - b.low, Math.abs(b.high - bars[i-1].close), Math.abs(b.low - bars[i-1].close))); const out = []; let prev = tr.slice(0, len).reduce((a,b)=>a+b,0)/len; for (let i = 0; i < tr.length; i++) { if (i < len-1) { out.push(NaN); continue; } if (i === len-1) { out.push(prev); continue; } prev = (prev*(len-1) + tr[i])/len; out.push(prev); } return out; }

const closes = bars.map(b => b.close);
const ema21Arr = ema(closes, 21);
const sma50Arr = sma(closes, 50);
const atr14Arr = atr(bars, 14);

const candidates = [];

// Same v2 logic
for (let i = 60; i < bars.length - 8; i++) {
  const b = bars[i];
  const e = ema21Arr[i];
  const s = sma50Arr[i];
  const s5 = sma50Arr[i-5];
  const a = atr14Arr[i];
  if (isNaN(a) || isNaN(s) || isNaN(s5)) continue;

  const date = new Date(b.time * 1000);
  const hourUTC = date.getUTCHours();
  if (hourUTC < 6 || hourUTC >= 20) continue;

  let sh = -Infinity, sl = Infinity;
  for (let j = Math.max(0, i-20); j < i; j++) {
    if (bars[j].high > sh) sh = bars[j].high;
    if (bars[j].low < sl) sl = bars[j].low;
  }

  const trendUp   = s > s5 + a * 0.05 && b.close > s;
  const trendDown = s < s5 - a * 0.05 && b.close < s;

  const body = Math.abs(b.close - b.open);
  const bodyOk = body >= a * 0.15;

  const longTouch = b.low <= e + a * 0.4 && b.low >= e - a * 0.7;
  const longClose = b.close > e;
  const longBull = b.close > b.open;
  const longRoom = (sh - b.close) > a * 1.5;
  const longSetup = trendUp && longTouch && longClose && longBull && bodyOk && longRoom;

  const shortTouch = b.high >= e - a * 0.4 && b.high <= e + a * 0.7;
  const shortClose = b.close < e;
  const shortBear = b.close < b.open;
  const shortRoom = (b.close - sl) > a * 1.5;
  const shortSetup = trendDown && shortTouch && shortClose && shortBear && bodyOk && shortRoom;

  if (longSetup || shortSetup) {
    candidates.push({
      idx: i, time: b.time, timeStr: date.toISOString(), hourUTC,
      side: longSetup ? 'LONG' : 'SHORT',
      entry: b.close, atr14: a,
    });
  }
}

// Simulate
function simulate(c) {
  const entry = c.entry; const atr = c.atr14;
  const SL = 1.5, TP = 3.0, MAX = 8;
  let sl, tp;
  if (c.side === 'LONG') { sl = entry - atr*SL; tp = entry + atr*TP; }
  else { sl = entry + atr*SL; tp = entry - atr*TP; }
  for (let i = c.idx + 1; i <= c.idx + MAX && i < bars.length; i++) {
    const b = bars[i];
    if (c.side === 'LONG') {
      if (b.low <= sl) return { outcome: 'SL', pnlR: -1, barsHeld: i - c.idx };
      if (b.high >= tp) return { outcome: 'TP', pnlR: TP/SL, barsHeld: i - c.idx };
    } else {
      if (b.high >= sl) return { outcome: 'SL', pnlR: -1, barsHeld: i - c.idx };
      if (b.low <= tp) return { outcome: 'TP', pnlR: TP/SL, barsHeld: i - c.idx };
    }
  }
  const last = bars[Math.min(c.idx + MAX, bars.length - 1)];
  const exitPrice = last.close;
  const mv = c.side === 'LONG' ? (exitPrice - entry) : (entry - exitPrice);
  return { outcome: 'TIMEOUT', pnlR: mv / (atr*SL), barsHeld: MAX };
}

const results = candidates.map(c => ({ ...c, ...simulate(c) }));

const n = results.length;
const wins = results.filter(t => t.pnlR > 0);
const losses = results.filter(t => t.pnlR < 0);
const gw = wins.reduce((s,t)=>s+t.pnlR,0);
const gl = Math.abs(losses.reduce((s,t)=>s+t.pnlR,0));
const total = results.reduce((s,t)=>s+t.pnlR,0);

console.log(`\n=== XAUUSD H1 — v2 filter on ${bars.length} bars (~5 weeks) ===`);
console.log(`Period: ${new Date(bars[0].time*1000).toISOString()} → ${new Date(bars[bars.length-1].time*1000).toISOString()}`);
console.log(`\nCandidates: ${n} (LONG: ${candidates.filter(c=>c.side==='LONG').length}, SHORT: ${candidates.filter(c=>c.side==='SHORT').length})`);
console.log(`Wins: ${wins.length} (${(wins.length/n*100).toFixed(1)}%)`);
console.log(`Gross W: +${gw.toFixed(2)}R | Gross L: -${gl.toFixed(2)}R`);
console.log(`Net total: ${total.toFixed(2)}R | PF: ${gl > 0 ? (gw/gl).toFixed(2) : '∞'}`);
console.log(`Expectancy: ${(total/n).toFixed(3)}R/trade\n`);

results.forEach((r,i) => {
  console.log(`${String(i).padStart(2)}: ${r.timeStr} ${r.side.padEnd(5)} @ ${r.entry.toFixed(2)} → ${r.outcome.padEnd(8)} (${r.pnlR.toFixed(2).padStart(5)}R)`);
});

// Per-week breakdown
console.log(`\n=== Per-week breakdown ===`);
const byWeek = {};
results.forEach(r => {
  const d = new Date(r.time*1000);
  const wkStart = new Date(d.getTime() - d.getUTCDay()*86400000);
  const wkKey = wkStart.toISOString().slice(0,10);
  if (!byWeek[wkKey]) byWeek[wkKey] = [];
  byWeek[wkKey].push(r);
});
Object.entries(byWeek).forEach(([wk, trs]) => {
  const t = trs.reduce((s,r)=>s+r.pnlR,0);
  const w = trs.filter(r=>r.pnlR>0).length;
  console.log(`  ${wk}: ${trs.length}t, ${w} wins (${(w/trs.length*100).toFixed(0)}%), ${t.toFixed(2)}R`);
});

fs.writeFileSync(path.join(__dirname, 'results_h1.json'), JSON.stringify(results, null, 2));
