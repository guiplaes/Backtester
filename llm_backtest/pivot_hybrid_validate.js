// Local validator: same logic as Pine script "Pivot Ext Hybrid v2"
// Multi-level entries (R1+R2+R3, S1+S2+S3) + stop-and-reverse + 50% TP + 50% trailing
const fs = require('fs');
const path = require('path');

const dataset = process.argv[2] || 'xauusd_m5_clean.json';
const data = JSON.parse(fs.readFileSync(path.join(__dirname, dataset), 'utf8'));
const bars = data.bars;
console.log(`Dataset: ${dataset}, ${bars.length} bars`);
console.log(`Range: ${new Date(bars[0].time*1000).toISOString()} → ${new Date(bars[bars.length-1].time*1000).toISOString()}\n`);

function aggregateDaily(bars) {
  const days = new Map();
  for (const b of bars) {
    const d = new Date(b.time*1000);
    const key = `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
    if (!days.has(key)) days.set(key, { date: key, open: b.open, high: b.high, low: b.low, close: b.close });
    const d_ = days.get(key);
    if (b.high > d_.high) d_.high = b.high;
    if (b.low < d_.low) d_.low = b.low;
    d_.close = b.close;
  }
  return [...days.values()];
}
const daily = aggregateDaily(bars);

function pivots(d) {
  const pp = (d.high + d.low + d.close) / 3;
  return {
    pp, r1: 2*pp - d.low, s1: 2*pp - d.high,
    r2: pp + (d.high - d.low), s2: pp - (d.high - d.low),
    r3: d.high + 2*(pp - d.low), s3: d.low - 2*(d.high - pp),
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

// Map date → prior-day pivots (no look-ahead)
const dateToPivots = new Map();
for (let i = 1; i < daily.length; i++) {
  dateToPivots.set(daily[i].date, pivots(daily[i-1]));
}

// ─── SIMULATOR ───────────────────────────────────────────────────────────
// State machine: walk bars, manage one position at a time, stop-and-reverse on opposite signals
const trades = [];
let pos = null;  // { dir, entry, entryIdx, sl, tp1, trailingSL, qtyRemaining }
const TRAIL_ATR = 1.5;

function findNextAbove(price, p) {
  let lvl = null;
  for (const v of [p.r1, p.r2, p.r3]) if (v > price && (lvl === null || v < lvl)) lvl = v;
  return lvl;
}
function findNextBelow(price, p) {
  let lvl = null;
  for (const v of [p.s1, p.s2, p.s3]) if (v < price && (lvl === null || v > lvl)) lvl = v;
  return lvl;
}

// Track which levels have been "crossed" today to prevent re-triggers on same level
let crossedToday = { date: null, longLevels: new Set(), shortLevels: new Set() };

let bar_count_in_pos_check = 0;
let entry_attempts = 0;

for (let i = 1; i < bars.length; i++) {
  const b = bars[i];
  const prev = bars[i-1];
  const d = new Date(b.time*1000);
  const dateKey = `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
  const p = dateToPivots.get(dateKey);
  if (!p) continue;

  // Reset crossed levels at new day
  if (crossedToday.date !== dateKey) {
    crossedToday = { date: dateKey, longLevels: new Set(), shortLevels: new Set() };
  }

  // ─── PROCESS EXITS FIRST (intra-bar) ───
  if (pos) {
    bar_count_in_pos_check++;
    if (pos.dir === 'LONG') {
      // Check TP1 first (limit) — fills if high >= tp1
      if (pos.qtyRemaining === 1.0 && b.high >= pos.tp1) {
        // TP1 hit: close 50%
        trades.push({
          dir: pos.dir, entry: pos.entry, exit: pos.tp1, qty: 0.5,
          outcome: 'TP1', entryIdx: pos.entryIdx, exitIdx: i, pnl: (pos.tp1 - pos.entry) * 0.5,
          risk: Math.abs(pos.entry - pos.sl), pnlR: ((pos.tp1 - pos.entry) / Math.abs(pos.entry - pos.sl)) * 0.5,
        });
        pos.qtyRemaining = 0.5;
        // Trailing kicks in for remaining
      }
      // Update trailing
      const atrNow = atr14[i] || pos.atr;
      const newT = b.high - TRAIL_ATR * atrNow;
      if (newT > pos.trailingSL) pos.trailingSL = newT;
      // Never below initial SL
      pos.trailingSL = Math.max(pos.trailingSL, pos.sl);
      // Check trailing/SL hit
      if (b.low <= pos.trailingSL) {
        trades.push({
          dir: pos.dir, entry: pos.entry, exit: pos.trailingSL, qty: pos.qtyRemaining,
          outcome: pos.qtyRemaining === 1.0 ? 'SL' : 'Trail',
          entryIdx: pos.entryIdx, exitIdx: i,
          pnl: (pos.trailingSL - pos.entry) * pos.qtyRemaining,
          risk: Math.abs(pos.entry - pos.sl),
          pnlR: ((pos.trailingSL - pos.entry) / Math.abs(pos.entry - pos.sl)) * pos.qtyRemaining,
        });
        pos = null;
      }
    } else {
      // SHORT
      if (pos.qtyRemaining === 1.0 && b.low <= pos.tp1) {
        trades.push({
          dir: pos.dir, entry: pos.entry, exit: pos.tp1, qty: 0.5,
          outcome: 'TP1', entryIdx: pos.entryIdx, exitIdx: i, pnl: (pos.entry - pos.tp1) * 0.5,
          risk: Math.abs(pos.entry - pos.sl), pnlR: ((pos.entry - pos.tp1) / Math.abs(pos.entry - pos.sl)) * 0.5,
        });
        pos.qtyRemaining = 0.5;
      }
      const atrNow = atr14[i] || pos.atr;
      const newT = b.low + TRAIL_ATR * atrNow;
      if (newT < pos.trailingSL) pos.trailingSL = newT;
      pos.trailingSL = Math.min(pos.trailingSL, pos.sl);
      if (b.high >= pos.trailingSL) {
        trades.push({
          dir: pos.dir, entry: pos.entry, exit: pos.trailingSL, qty: pos.qtyRemaining,
          outcome: pos.qtyRemaining === 1.0 ? 'SL' : 'Trail',
          entryIdx: pos.entryIdx, exitIdx: i,
          pnl: (pos.entry - pos.trailingSL) * pos.qtyRemaining,
          risk: Math.abs(pos.entry - pos.sl),
          pnlR: ((pos.entry - pos.trailingSL) / Math.abs(pos.entry - pos.sl)) * pos.qtyRemaining,
        });
        pos = null;
      }
    }
  }

  // ─── ENTRY SIGNALS (crossover style, prevent re-trigger same level same day) ───
  const longLevels = [p.r1, p.r2, p.r3];
  const shortLevels = [p.s1, p.s2, p.s3];
  let longSig = false, shortSig = false, triggerLevel = null;

  // Check each level for crossover (b.high crosses level)
  for (const lvl of longLevels) {
    if (prev.high <= lvl && b.high > lvl && !crossedToday.longLevels.has(lvl)) {
      longSig = true; triggerLevel = lvl;
      crossedToday.longLevels.add(lvl);
      break;
    }
  }
  for (const lvl of shortLevels) {
    if (prev.low >= lvl && b.low < lvl && !crossedToday.shortLevels.has(lvl)) {
      shortSig = true; triggerLevel = lvl;
      crossedToday.shortLevels.add(lvl);
      break;
    }
  }

  // ─── ENTRY / STOP & REVERSE ───
  if (longSig) {
    entry_attempts++;
    // Close existing short (stop & reverse)
    if (pos && pos.dir === 'SHORT') {
      const exitPrice = b.close;
      trades.push({
        dir: pos.dir, entry: pos.entry, exit: exitPrice, qty: pos.qtyRemaining,
        outcome: 'Reverse', entryIdx: pos.entryIdx, exitIdx: i,
        pnl: (pos.entry - exitPrice) * pos.qtyRemaining,
        risk: Math.abs(pos.entry - pos.sl),
        pnlR: ((pos.entry - exitPrice) / Math.abs(pos.entry - pos.sl)) * pos.qtyRemaining,
      });
      pos = null;
    }
    // Open long if flat
    if (!pos) {
      const slPrice = findNextBelow(b.close, p) || (b.close - (atr14[i] || 5) * 2);
      const tp1Price = findNextAbove(b.close, p) || (b.close + (atr14[i] || 5) * 2);
      pos = {
        dir: 'LONG', entry: b.close, entryIdx: i,
        sl: slPrice, tp1: tp1Price, trailingSL: slPrice,
        qtyRemaining: 1.0, atr: atr14[i] || 5,
      };
    }
  }

  if (shortSig) {
    entry_attempts++;
    if (pos && pos.dir === 'LONG') {
      const exitPrice = b.close;
      trades.push({
        dir: pos.dir, entry: pos.entry, exit: exitPrice, qty: pos.qtyRemaining,
        outcome: 'Reverse', entryIdx: pos.entryIdx, exitIdx: i,
        pnl: (exitPrice - pos.entry) * pos.qtyRemaining,
        risk: Math.abs(pos.entry - pos.sl),
        pnlR: ((exitPrice - pos.entry) / Math.abs(pos.entry - pos.sl)) * pos.qtyRemaining,
      });
      pos = null;
    }
    if (!pos) {
      const slPrice = findNextAbove(b.close, p) || (b.close + (atr14[i] || 5) * 2);
      const tp1Price = findNextBelow(b.close, p) || (b.close - (atr14[i] || 5) * 2);
      pos = {
        dir: 'SHORT', entry: b.close, entryIdx: i,
        sl: slPrice, tp1: tp1Price, trailingSL: slPrice,
        qtyRemaining: 1.0, atr: atr14[i] || 5,
      };
    }
  }
}

// ─── STATS ────────────────────────────────────────────────────────────────
const wins = trades.filter(t => t.pnlR > 0);
const losses = trades.filter(t => t.pnlR < 0);
const totalR = trades.reduce((s,t) => s+t.pnlR, 0);
const grossWin = wins.reduce((s,t)=>s+t.pnlR, 0);
const grossLoss = Math.abs(losses.reduce((s,t)=>s+t.pnlR, 0));
const pf = grossLoss > 0 ? grossWin / grossLoss : Infinity;

console.log('═══════════════════════════════════════════════════════════════════════════');
console.log('LOCAL VALIDATION — Pivot Ext Hybrid v2 (multi-level + 50%TP + 50%trail)');
console.log('═══════════════════════════════════════════════════════════════════════════');
console.log(`Total trade events (entries + scale-outs): ${trades.length}`);
console.log(`Win rate: ${(wins.length/trades.length*100).toFixed(1)}% (${wins.length}/${trades.length})`);
console.log(`Profit factor: ${pf.toFixed(2)}`);
console.log(`Total R: ${totalR>=0?'+':''}${totalR.toFixed(2)}R`);
console.log(`Avg R/trade: ${(totalR/trades.length).toFixed(2)}R`);
console.log('');
console.log(`Entry attempts: ${entry_attempts}`);
console.log(`Bars with position active: ${bar_count_in_pos_check}`);
console.log('');
console.log('Outcome breakdown:');
const outcomes = {};
for (const t of trades) outcomes[t.outcome] = (outcomes[t.outcome]||0) + 1;
for (const [o, n] of Object.entries(outcomes)) console.log(`  ${o}: ${n}`);

// Sample first 10 trades
console.log('\nFirst 10 trade events:');
trades.slice(0, 10).forEach((t, i) => {
  console.log(`  ${i+1}. ${t.dir} @${t.entry.toFixed(2)} → ${t.outcome} @${t.exit.toFixed(2)} (qty=${t.qty}) | pnlR=${t.pnlR.toFixed(2)}`);
});
