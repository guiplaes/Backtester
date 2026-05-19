// Apply v2 filter to original M5 data (different sample, different feed)
const fs = require('fs');
const path = require('path');

const data = JSON.parse(fs.readFileSync(path.join(__dirname, 'xauusd_m5.json'), 'utf8'));
const bars = data.bars;

function ema(arr, len) { const k=2/(len+1); const out=[]; let p=arr[0]; for (let i=0;i<arr.length;i++) { p = i===0?arr[i]:arr[i]*k+p*(1-k); out.push(p); } return out; }
function sma(arr, len) { const out=[]; for (let i=0;i<arr.length;i++) { if (i<len-1) {out.push(NaN);continue;} let s=0; for (let j=i-len+1;j<=i;j++) s+=arr[j]; out.push(s/len); } return out; }
function atr(bars, len) { const tr=bars.map((b,i)=>i===0?b.high-b.low:Math.max(b.high-b.low,Math.abs(b.high-bars[i-1].close),Math.abs(b.low-bars[i-1].close))); const out=[]; let p=tr.slice(0,len).reduce((a,b)=>a+b,0)/len; for (let i=0;i<tr.length;i++) { if (i<len-1){out.push(NaN);continue;} if (i===len-1){out.push(p);continue;} p=(p*(len-1)+tr[i])/len; out.push(p); } return out; }

const closes = bars.map(b => b.close);
const ema21Arr = ema(closes, 21);
const sma50Arr = sma(closes, 50);
const atr14Arr = atr(bars, 14);

const candidates = [];
for (let i = 60; i < bars.length - 8; i++) {
  const b = bars[i];
  const e = ema21Arr[i], s = sma50Arr[i], s5 = sma50Arr[i-5], a = atr14Arr[i];
  if (isNaN(a) || isNaN(s) || isNaN(s5)) continue;
  const date = new Date(b.time * 1000);
  const hourUTC = date.getUTCHours();
  if (hourUTC < 6 || hourUTC >= 20) continue;

  let sh = -Infinity, sl = Infinity;
  for (let j = Math.max(0, i-20); j < i; j++) { if (bars[j].high > sh) sh = bars[j].high; if (bars[j].low < sl) sl = bars[j].low; }

  const trendUp = s > s5 + a * 0.05 && b.close > s;
  const trendDown = s < s5 - a * 0.05 && b.close < s;
  const body = Math.abs(b.close - b.open);
  const bodyOk = body >= a * 0.15;

  const longTouch = b.low <= e + a * 0.4 && b.low >= e - a * 0.7;
  const longSetup = trendUp && longTouch && b.close > e && b.close > b.open && bodyOk && (sh - b.close) > a * 1.5;

  const shortTouch = b.high >= e - a * 0.4 && b.high <= e + a * 0.7;
  const shortSetup = trendDown && shortTouch && b.close < e && b.close < b.open && bodyOk && (b.close - sl) > a * 1.5;

  if (longSetup || shortSetup) candidates.push({ idx: i, time: b.time, timeStr: date.toISOString(), side: longSetup?'LONG':'SHORT', entry: b.close, atr14: a });
}

function simulate(c) {
  const entry = c.entry, atr = c.atr14;
  const SL = 1.5, TP = 3.0, MAX = 8;
  let sl, tp;
  if (c.side === 'LONG') { sl = entry - atr*SL; tp = entry + atr*TP; } else { sl = entry + atr*SL; tp = entry - atr*TP; }
  for (let i = c.idx + 1; i <= c.idx + MAX && i < bars.length; i++) {
    const b = bars[i];
    if (c.side === 'LONG') {
      if (b.low <= sl) return { outcome: 'SL', pnlR: -1 };
      if (b.high >= tp) return { outcome: 'TP', pnlR: TP/SL };
    } else {
      if (b.high >= sl) return { outcome: 'SL', pnlR: -1 };
      if (b.low <= tp) return { outcome: 'TP', pnlR: TP/SL };
    }
  }
  const last = bars[Math.min(c.idx + MAX, bars.length - 1)];
  const mv = c.side === 'LONG' ? (last.close - entry) : (entry - last.close);
  return { outcome: 'TIMEOUT', pnlR: mv / (atr*SL) };
}

const results = candidates.map(c => ({ ...c, ...simulate(c) }));
const n = results.length;
const wins = results.filter(t => t.pnlR > 0);
const gw = wins.reduce((s,t)=>s+t.pnlR,0);
const gl = Math.abs(results.filter(t => t.pnlR < 0).reduce((s,t)=>s+t.pnlR,0));
const total = results.reduce((s,t)=>s+t.pnlR,0);

console.log(`\n=== XAUUSD M5 — v2 filter on ${bars.length} bars ===`);
console.log(`Period: ${new Date(bars[0].time*1000).toISOString()} → ${new Date(bars[bars.length-1].time*1000).toISOString()}`);
console.log(`Candidates: ${n} (LONG: ${candidates.filter(c=>c.side==='LONG').length}, SHORT: ${candidates.filter(c=>c.side==='SHORT').length})`);
if (n > 0) {
  console.log(`Wins: ${wins.length} (${(wins.length/n*100).toFixed(1)}%)`);
  console.log(`Net: ${total.toFixed(2)}R | PF: ${gl>0?(gw/gl).toFixed(2):'∞'} | Exp: ${(total/n).toFixed(3)}R/t\n`);
  results.forEach((r,i) => console.log(`${String(i).padStart(2)}: ${r.timeStr} ${r.side.padEnd(5)} @ ${r.entry.toFixed(2)} → ${r.outcome.padEnd(8)} (${r.pnlR.toFixed(2)}R)`));
}
