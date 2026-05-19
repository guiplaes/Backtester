// Local engine: Pivot Extension Strategy with multiple TP/SL/Trailing configs
// Uses combined XAU M15 dataset (in-sample + out-of-sample = ~3 weeks)
const fs = require('fs');
const path = require('path');

// ─── Load and merge data ─────────────────────────────────────────────────
const inSample = JSON.parse(fs.readFileSync(path.join(__dirname, 'xauusd_m15.json'), 'utf8')).bars;
const oos = JSON.parse(fs.readFileSync(path.join(__dirname, 'xauusd_m15_oos.json'), 'utf8')).bars;
const merged = new Map();
[...inSample, ...oos].forEach(b => merged.set(b.time, b));
const bars = [...merged.values()].sort((a,b) => a.time - b.time);
console.log(`Combined dataset: ${bars.length} M15 bars`);
console.log(`Range: ${new Date(bars[0].time*1000).toISOString()} → ${new Date(bars[bars.length-1].time*1000).toISOString()}\n`);

// ─── Aggregate to daily ─────────────────────────────────────────────────
function aggregateDaily(bars) {
  const days = new Map();
  for (const b of bars) {
    const d = new Date(b.time*1000);
    const key = `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
    if (!days.has(key)) days.set(key, { date: key, open: b.open, high: b.high, low: b.low, close: b.close, volume: 0, firstIdx: bars.indexOf(b), lastIdx: bars.indexOf(b) });
    const d_ = days.get(key);
    if (b.high > d_.high) d_.high = b.high;
    if (b.low < d_.low) d_.low = b.low;
    d_.close = b.close;
    d_.volume += b.volume;
    d_.lastIdx = bars.indexOf(b);
  }
  return [...days.values()];
}
const daily = aggregateDaily(bars);
console.log(`Daily bars: ${daily.length}`);

// ─── Pivot calculation (classic) ─────────────────────────────────────────
function pivots(d) {
  const pp = (d.high + d.low + d.close) / 3;
  const r1 = 2*pp - d.low;
  const s1 = 2*pp - d.high;
  const r2 = pp + (d.high - d.low);
  const s2 = pp - (d.high - d.low);
  const r3 = d.high + 2*(pp - d.low);
  const s3 = d.low - 2*(d.high - pp);
  return { pp, r1, r2, r3, s1, s2, s3 };
}

// ─── ATR ────────────────────────────────────────────────────────────────
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

// Map: date → prior day's pivots (which is what today's bars use)
const dateToPivots = new Map();
for (let i = 1; i < daily.length; i++) {
  dateToPivots.set(daily[i].date, pivots(daily[i-1]));
}

// ─── Detect entries (Pivot Extension): price breaks R1 → LONG, breaks S1 → SHORT ──
// Stop-and-reverse logic: track active position
function detectEntries() {
  const entries = [];
  let lastSignalIdx = -100;
  for (let i = 1; i < bars.length; i++) {
    const b = bars[i];
    const d = new Date(b.time*1000);
    const hr = d.getUTCHours();
    // Filter: active sessions only (London/NY)
    if (hr < 7 || hr >= 21) continue;
    const dateKey = `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
    const p = dateToPivots.get(dateKey);
    if (!p) continue;
    // Cooldown: 1 bar minimum between signals
    if (i - lastSignalIdx < 1) continue;
    // Entry: bar crosses R1 from below → long; crosses S1 from above → short
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

// ─── Simulators for different exit configs ──────────────────────────────

function simulate(e, config) {
  const dir = e.dir;
  const entry = e.entry;
  const p = e.pivots;
  const a = e.atr || 8;

  // Compute SL and TP per config
  let sl, tp;
  switch(config.name) {
    case 'A_original':
      // Original Pivot Extension: TP = next pivot (R2/S2), SL = prior pivot (PP)
      sl = dir === 'LONG' ? p.pp : p.pp;
      tp = dir === 'LONG' ? p.r2 : p.s2;
      break;
    case 'B_tp2xsl':
      // Same SL, TP = 2× SL distance
      sl = dir === 'LONG' ? p.pp : p.pp;
      const risk_B = Math.abs(entry - sl);
      tp = dir === 'LONG' ? entry + 2*risk_B : entry - 2*risk_B;
      break;
    case 'C_tp3xsl':
      sl = dir === 'LONG' ? p.pp : p.pp;
      const risk_C = Math.abs(entry - sl);
      tp = dir === 'LONG' ? entry + 3*risk_C : entry - 3*risk_C;
      break;
    case 'D_atr':
      // SL = 1.5×ATR, TP = 3×ATR (2R target)
      sl = dir === 'LONG' ? entry - 1.5*a : entry + 1.5*a;
      tp = dir === 'LONG' ? entry + 3*a : entry - 3*a;
      break;
    case 'E_trailing':
      // Initial SL = PP, no fixed TP — trailing SL of N×ATR
      sl = dir === 'LONG' ? p.pp : p.pp;
      tp = null;
      break;
  }
  const risk = Math.abs(entry - sl);

  let outcome = null, exitPrice = null, barsHeld = 0, trailingSL = sl;
  const TRAIL_MULT = 1.5;  // trailing distance for config E
  const MAX_BARS = 40;     // max hold ~10h

  for (let i = e.idx + 1; i <= e.idx + MAX_BARS && i < bars.length; i++) {
    const b = bars[i];
    barsHeld++;

    if (config.name === 'E_trailing') {
      // Update trailing SL each bar
      if (dir === 'LONG') {
        const newTrail = b.high - TRAIL_MULT * (atr14[i] || a);
        if (newTrail > trailingSL) trailingSL = newTrail;
      } else {
        const newTrail = b.low + TRAIL_MULT * (atr14[i] || a);
        if (newTrail < trailingSL) trailingSL = newTrail;
      }
      // Check SL hit
      if (dir === 'LONG' && b.low <= trailingSL) { outcome = 'TRAIL_OUT'; exitPrice = trailingSL; break; }
      if (dir === 'SHORT' && b.high >= trailingSL) { outcome = 'TRAIL_OUT'; exitPrice = trailingSL; break; }
    } else {
      // Fixed TP/SL
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
  const pnlR = move / risk;
  return { outcome, exitPrice, barsHeld, pnlR, move, risk, sl, tp: tp || trailingSL };
}

// ─── Run all configs ────────────────────────────────────────────────────
const configs = [
  { name: 'A_original',  desc: 'TP=next pivot, SL=PP (replica)' },
  { name: 'B_tp2xsl',    desc: 'TP = 2× SL distance' },
  { name: 'C_tp3xsl',    desc: 'TP = 3× SL distance' },
  { name: 'D_atr',       desc: 'SL=1.5×ATR, TP=3×ATR (2R)' },
  { name: 'E_trailing',  desc: 'Trailing SL @ 1.5×ATR, no fixed TP' },
];

const SPREAD_USD = 0.30;  // $/oz typical XAU spread
const POINT_VALUE = 100;  // $ per point per lot (1 lot = 100 oz)

console.log('═══════════════════════════════════════════════════════════════════════════');
console.log('Config | Trades | WR    | PF    | TotR  | AvgR  | NetUSD (w/spread) | DD');
console.log('═══════════════════════════════════════════════════════════════════════════');

const all = {};
for (const cfg of configs) {
  const trades = entries.map(e => simulate(e, cfg));
  const wins = trades.filter(t => t.pnlR > 0);
  const losses = trades.filter(t => t.pnlR < 0);
  const totalR = trades.reduce((s,t) => s+t.pnlR, 0);
  const grossWin = wins.reduce((s,t)=>s+t.pnlR, 0);
  const grossLoss = Math.abs(losses.reduce((s,t)=>s+t.pnlR, 0));
  const pf = grossLoss > 0 ? grossWin / grossLoss : Infinity;

  // PnL in USD assuming 0.01 lot per trade (= 1 oz)
  // 0.01 lot, point_value = $1/point. Spread = $0.30 per round trip per 0.01 lot.
  const LOT = 0.01;
  const totalUSD_gross = trades.reduce((s,t) => s + t.move * 100 * LOT, 0);
  const totalUSD_net = totalUSD_gross - trades.length * SPREAD_USD * LOT * 100;

  // Max DD on R curve
  let equity = 0, peak = 0, maxDD = 0;
  for (const t of trades) {
    equity += t.pnlR;
    if (equity > peak) peak = equity;
    if (peak - equity > maxDD) maxDD = peak - equity;
  }

  all[cfg.name] = { trades, wins: wins.length, totalR, pf, maxDD, totalUSD_gross, totalUSD_net };

  console.log(
    `${cfg.name.padEnd(13)} | ${String(trades.length).padStart(5)} | ` +
    `${(wins.length/trades.length*100).toFixed(0).padStart(3)}% | ` +
    `${pf.toFixed(2).padStart(5)} | ` +
    `${(totalR>=0?'+':'')+totalR.toFixed(2).padStart(5)} | ` +
    `${(totalR/trades.length).toFixed(2).padStart(5)} | ` +
    `gross $${totalUSD_gross.toFixed(0)} / net $${totalUSD_net.toFixed(0)} | ${maxDD.toFixed(1)}R`
  );
}

console.log('\n─── Outcome distribution ────────────────────────────────────');
console.log('Config       | TP   | SL   | TIMEOUT | TRAIL_OUT');
for (const cfg of configs) {
  const t = all[cfg.name].trades;
  const tp = t.filter(x => x.outcome === 'TP').length;
  const sl = t.filter(x => x.outcome === 'SL').length;
  const to = t.filter(x => x.outcome === 'TIMEOUT').length;
  const tr = t.filter(x => x.outcome === 'TRAIL_OUT').length;
  console.log(`${cfg.name.padEnd(12)} | ${String(tp).padStart(4)} | ${String(sl).padStart(4)} | ${String(to).padStart(7)} | ${String(tr).padStart(9)}`);
}

console.log('\n─── Config descriptions ──────────────────────────────────────');
for (const cfg of configs) console.log(`  ${cfg.name}: ${cfg.desc}`);

console.log('\nSpread per trade: $' + (SPREAD_USD * 100 * 0.01).toFixed(2) + ' (0.01 lot)');
console.log('Note: results without filtering by trend/session beyond UTC 7-21');
