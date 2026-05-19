// Same engine as pivot_extension_test.js but on M5 data
const fs = require('fs');
const path = require('path');

const dataset = process.argv[2] || 'xauusd_m5.json';
const tfMin = parseInt(process.argv[3] || '5'); // minutes per bar
const data = JSON.parse(fs.readFileSync(path.join(__dirname, dataset), 'utf8'));
const bars = data.bars;
console.log(`Dataset: ${dataset} (${tfMin}m), ${bars.length} bars`);
console.log(`Range: ${new Date(bars[0].time*1000).toISOString()} → ${new Date(bars[bars.length-1].time*1000).toISOString()}\n`);

function aggregateDaily(bars) {
  const days = new Map();
  for (const b of bars) {
    const d = new Date(b.time*1000);
    const key = `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
    if (!days.has(key)) days.set(key, { date: key, open: b.open, high: b.high, low: b.low, close: b.close, volume: 0 });
    const d_ = days.get(key);
    if (b.high > d_.high) d_.high = b.high;
    if (b.low < d_.low) d_.low = b.low;
    d_.close = b.close;
    d_.volume += b.volume;
  }
  return [...days.values()];
}
const daily = aggregateDaily(bars);
console.log(`Daily bars: ${daily.length}`);

function pivots(d) {
  const pp = (d.high + d.low + d.close) / 3;
  return {
    pp,
    r1: 2*pp - d.low,
    s1: 2*pp - d.high,
    r2: pp + (d.high - d.low),
    s2: pp - (d.high - d.low),
  };
}

function atr(bars, len) {
  const tr = bars.map((b,i) => i===0?b.high-b.low:Math.max(b.high-b.low,Math.abs(b.high-bars[i-1].close),Math.abs(b.low-bars[i-1].close)));
  const out=[]; let p=tr.slice(0,len).reduce((a,b)=>a+b,0)/len;
  for (let i=0;i<tr.length;i++) {
    if (i<len-1){out.push(NaN);continue;}
    if (i===len-1){out.push(p);continue;}
    p=(p*(len-1)+tr[i])/len; out.push(p);
  }
  return out;
}
const atr14 = atr(bars, 14);

const dateToPivots = new Map();
for (let i = 1; i < daily.length; i++) {
  dateToPivots.set(daily[i].date, pivots(daily[i-1]));
}

function detectEntries() {
  const entries = [];
  let lastSignalIdx = -100;
  for (let i = 1; i < bars.length; i++) {
    const b = bars[i];
    const d = new Date(b.time*1000);
    const hr = d.getUTCHours();
    if (hr < 7 || hr >= 21) continue;
    const dateKey = `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
    const p = dateToPivots.get(dateKey);
    if (!p) continue;
    if (i - lastSignalIdx < 1) continue;
    const prev = bars[i-1];
    if (prev.close < p.r1 && b.close > p.r1) {
      entries.push({ idx: i, dir: 'LONG', entry: p.r1, pivots: p, atr: atr14[i] });
      lastSignalIdx = i;
    } else if (prev.close > p.s1 && b.close < p.s1) {
      entries.push({ idx: i, dir: 'SHORT', entry: p.s1, pivots: p, atr: atr14[i] });
      lastSignalIdx = i;
    }
  }
  return entries;
}

const entries = detectEntries();
console.log(`Pivot Extension entries detected: ${entries.length}\n`);

function simulate(e, config) {
  const dir = e.dir, entry = e.entry, p = e.pivots, a = e.atr || 5;
  let sl, tp;
  switch(config.name) {
    case 'A_original':
      sl = dir === 'LONG' ? p.pp : p.pp;
      tp = dir === 'LONG' ? p.r2 : p.s2;
      break;
    case 'B_tp2xsl':
      sl = p.pp;
      const r_B = Math.abs(entry - sl);
      tp = dir === 'LONG' ? entry + 2*r_B : entry - 2*r_B;
      break;
    case 'C_tp3xsl':
      sl = p.pp;
      const r_C = Math.abs(entry - sl);
      tp = dir === 'LONG' ? entry + 3*r_C : entry - 3*r_C;
      break;
    case 'D_atr':
      sl = dir === 'LONG' ? entry - 1.5*a : entry + 1.5*a;
      tp = dir === 'LONG' ? entry + 3*a : entry - 3*a;
      break;
    case 'E_trailing':
      sl = p.pp; tp = null;
      break;
  }
  const risk = Math.abs(entry - sl);

  let outcome = null, exitPrice = null, barsHeld = 0, trailingSL = sl;
  const TRAIL_MULT = 1.5;
  const MAX_BARS = Math.round(40 * (15 / tfMin)); // scale max-hold by timeframe

  for (let i = e.idx + 1; i <= e.idx + MAX_BARS && i < bars.length; i++) {
    const b = bars[i]; barsHeld++;
    if (config.name === 'E_trailing') {
      if (dir === 'LONG') {
        const t = b.high - TRAIL_MULT * (atr14[i] || a);
        if (t > trailingSL) trailingSL = t;
        if (b.low <= trailingSL) { outcome = 'TRAIL_OUT'; exitPrice = trailingSL; break; }
      } else {
        const t = b.low + TRAIL_MULT * (atr14[i] || a);
        if (t < trailingSL) trailingSL = t;
        if (b.high >= trailingSL) { outcome = 'TRAIL_OUT'; exitPrice = trailingSL; break; }
      }
    } else {
      if (dir === 'LONG') {
        if (b.low <= sl) { outcome = 'SL'; exitPrice = sl; break; }
        if (b.high >= tp) { outcome = 'TP'; exitPrice = tp; break; }
      } else {
        if (b.high >= sl) { outcome = 'SL'; exitPrice = sl; break; }
        if (b.low <= tp) { outcome = 'TP'; exitPrice = tp; break; }
      }
    }
  }
  if (!outcome) {
    const last = bars[Math.min(e.idx + MAX_BARS, bars.length - 1)];
    outcome = 'TIMEOUT'; exitPrice = last.close;
  }
  const move = dir === 'LONG' ? (exitPrice - entry) : (entry - exitPrice);
  return { outcome, exitPrice, barsHeld, pnlR: move / risk, move, risk };
}

const configs = [
  { name: 'A_original' },
  { name: 'B_tp2xsl' },
  { name: 'C_tp3xsl' },
  { name: 'D_atr' },
  { name: 'E_trailing' },
];
const SPREAD = 0.30, LOT = 0.01;

console.log('═══════════════════════════════════════════════════════════════════════════');
console.log('Config         | Trades | WR    | PF    | TotR  | AvgR  | Gross/Net USD     | DD');
console.log('═══════════════════════════════════════════════════════════════════════════');

for (const cfg of configs) {
  const trades = entries.map(e => simulate(e, cfg));
  const wins = trades.filter(t => t.pnlR > 0);
  const losses = trades.filter(t => t.pnlR < 0);
  const totalR = trades.reduce((s,t) => s+t.pnlR, 0);
  const grossWin = wins.reduce((s,t)=>s+t.pnlR,0);
  const grossLoss = Math.abs(losses.reduce((s,t)=>s+t.pnlR,0));
  const pf = grossLoss > 0 ? grossWin / grossLoss : Infinity;
  const totalUSD = trades.reduce((s,t) => s + t.move * 100 * LOT, 0);
  const netUSD = totalUSD - trades.length * SPREAD * 100 * LOT;
  let eq = 0, peak = 0, maxDD = 0;
  for (const t of trades) { eq += t.pnlR; if (eq > peak) peak = eq; if (peak - eq > maxDD) maxDD = peak - eq; }
  console.log(
    `${cfg.name.padEnd(14)} | ${String(trades.length).padStart(6)} | ${(wins.length/Math.max(trades.length,1)*100).toFixed(0).padStart(3)}% | ` +
    `${pf.toFixed(2).padStart(5)} | ${(totalR>=0?'+':'')+totalR.toFixed(2).padStart(5)} | ` +
    `${(totalR/Math.max(trades.length,1)).toFixed(2).padStart(5)} | gross $${totalUSD.toFixed(0).padStart(4)} / net $${netUSD.toFixed(0).padStart(4)} | ${maxDD.toFixed(1)}R`
  );
}

console.log('\n─── Outcome distribution ────────────────────────────────────');
for (const cfg of configs) {
  const t = entries.map(e => simulate(e, cfg));
  const tp = t.filter(x => x.outcome === 'TP').length;
  const sl = t.filter(x => x.outcome === 'SL').length;
  const to = t.filter(x => x.outcome === 'TIMEOUT').length;
  const tr = t.filter(x => x.outcome === 'TRAIL_OUT').length;
  console.log(`  ${cfg.name.padEnd(13)}: TP=${tp}, SL=${sl}, TIMEOUT=${to}, TRAIL=${tr}`);
}
