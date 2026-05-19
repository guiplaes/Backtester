// Local validator for Pivot Ext Hybrid v3 — Realistic
// Same logic as Pine: multi-level entries, stop-and-reverse, TP1+TP2, SL→BE after TP1
const fs = require('fs');
const path = require('path');

const dataset = process.argv[2] || 'xauusd_m5_clean.json';
const data = JSON.parse(fs.readFileSync(path.join(__dirname, dataset), 'utf8'));
const bars = data.bars;
console.log(`Dataset: ${dataset}, ${bars.length} bars`);
console.log(`Range: ${new Date(bars[0].time*1000).toISOString()} → ${new Date(bars[bars.length-1].time*1000).toISOString()}\n`);

const TP2_MULT = 2.0;
const MOVE_SL_TO_BE = true;
const COMMISSION_PCT = 0.03 / 100;  // 0.03% per side
const SLIPPAGE_PTS = 2;               // 2 points slippage
const LOT = 0.10;
const POINT_VALUE = 100;              // $100/point per 1.0 lot XAU
const ACCOUNT = 10000;

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

const dateToPivots = new Map();
for (let i = 1; i < daily.length; i++) {
  dateToPivots.set(daily[i].date, pivots(daily[i-1]));
}

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

// Simulator — NO crossedToday filter (Pine doesn't deduplicate either)
const trades = [];
let pos = null;

function recordExit(t, outcome, exitPrice, qtyFraction) {
  // Apply slippage (worst case for trader)
  const adjustedExit = t.dir === 'LONG' ? exitPrice - SLIPPAGE_PTS * 0.01 : exitPrice + SLIPPAGE_PTS * 0.01;
  const moveRaw = t.dir === 'LONG' ? (adjustedExit - t.entry) : (t.entry - adjustedExit);
  const moveUSD = moveRaw * POINT_VALUE * LOT * qtyFraction;
  // Commission: 0.03% of notional per side (entry side already paid at entry; exit side here)
  const notional = t.entry * POINT_VALUE * LOT * qtyFraction;
  const commission = notional * COMMISSION_PCT;
  const netUSD = moveUSD - commission;
  const risk = Math.abs(t.entry - t.sl);
  const pnlR = (moveRaw / risk) * qtyFraction;
  trades.push({ ...t, outcome, exitPrice: adjustedExit, qty: qtyFraction, pnlR, pnlUSD: netUSD, commission });
}

for (let i = 1; i < bars.length; i++) {
  const b = bars[i];
  const prev = bars[i-1];
  const d = new Date(b.time*1000);
  const dateKey = `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
  const p = dateToPivots.get(dateKey);
  if (!p) continue;

  // EXITS (process before entries within this bar)
  if (pos) {
    if (pos.dir === 'LONG') {
      // TP1 first if not yet hit (limit fills if high reaches it)
      if (!pos.tp1Hit && b.high >= pos.tp1) {
        recordExit(pos, 'TP1', pos.tp1, 0.5);
        pos.tp1Hit = true;
        pos.qtyRemaining = 0.5;
        if (MOVE_SL_TO_BE) pos.effSL = pos.entry;
      }
      // TP2 if reached
      if (pos.qtyRemaining > 0 && b.high >= pos.tp2) {
        recordExit(pos, 'TP2', pos.tp2, pos.qtyRemaining);
        pos = null;
        continue;
      }
      // SL check
      if (pos && b.low <= pos.effSL) {
        recordExit(pos, pos.tp1Hit ? 'BE' : 'SL', pos.effSL, pos.qtyRemaining);
        pos = null;
        continue;
      }
    } else {
      if (!pos.tp1Hit && b.low <= pos.tp1) {
        recordExit(pos, 'TP1', pos.tp1, 0.5);
        pos.tp1Hit = true;
        pos.qtyRemaining = 0.5;
        if (MOVE_SL_TO_BE) pos.effSL = pos.entry;
      }
      if (pos.qtyRemaining > 0 && b.low <= pos.tp2) {
        recordExit(pos, 'TP2', pos.tp2, pos.qtyRemaining);
        pos = null;
        continue;
      }
      if (pos && b.high >= pos.effSL) {
        recordExit(pos, pos.tp1Hit ? 'BE' : 'SL', pos.effSL, pos.qtyRemaining);
        pos = null;
        continue;
      }
    }
  }

  // ENTRIES (crossover-based, multi-level, one entry per level per day)
  const longLevels = [p.r1, p.r2, p.r3];
  const shortLevels = [p.s1, p.s2, p.s3];
  let longSig = false, shortSig = false;
  for (const lvl of longLevels) {
    if (prev.high <= lvl && b.high > lvl) {
      longSig = true; break;
    }
  }
  for (const lvl of shortLevels) {
    if (prev.low >= lvl && b.low < lvl) {
      shortSig = true; break;
    }
  }

  // Stop & Reverse
  if (longSig) {
    if (pos && pos.dir === 'SHORT') {
      recordExit(pos, 'Reverse', b.close, pos.qtyRemaining);
      pos = null;
    }
    if (!pos) {
      const slPx = findNextBelow(b.close, p) || p.pp;
      const tp1Px = findNextAbove(b.close, p) || (b.close + Math.abs(b.close - slPx));
      const tp2Px = b.close + (tp1Px - b.close) * TP2_MULT;
      pos = { dir: 'LONG', entry: b.close, entryIdx: i, sl: slPx, effSL: slPx, tp1: tp1Px, tp2: tp2Px, tp1Hit: false, qtyRemaining: 1.0 };
    }
  }
  if (shortSig) {
    if (pos && pos.dir === 'LONG') {
      recordExit(pos, 'Reverse', b.close, pos.qtyRemaining);
      pos = null;
    }
    if (!pos) {
      const slPx = findNextAbove(b.close, p) || p.pp;
      const tp1Px = findNextBelow(b.close, p) || (b.close - Math.abs(b.close - slPx));
      const tp2Px = b.close - (b.close - tp1Px) * TP2_MULT;
      pos = { dir: 'SHORT', entry: b.close, entryIdx: i, sl: slPx, effSL: slPx, tp1: tp1Px, tp2: tp2Px, tp1Hit: false, qtyRemaining: 1.0 };
    }
  }
}

// Stats
const wins = trades.filter(t => t.pnlUSD > 0);
const losses = trades.filter(t => t.pnlUSD < 0);
const totalUSD = trades.reduce((s,t) => s+t.pnlUSD, 0);
const totalR = trades.reduce((s,t) => s+t.pnlR, 0);
const grossWin = wins.reduce((s,t)=>s+t.pnlUSD, 0);
const grossLoss = Math.abs(losses.reduce((s,t)=>s+t.pnlUSD, 0));
const pf = grossLoss > 0 ? grossWin / grossLoss : Infinity;
let eq = 0, peak = 0, maxDD = 0;
for (const t of trades) { eq += t.pnlUSD; if (eq > peak) peak = eq; if (peak - eq > maxDD) maxDD = peak - eq; }

console.log('═══════════════════════════════════════════════════════════════════════════');
console.log('LOCAL VALIDATION v3 — multi-level + TP1 + TP2 + SL→BE + realistic costs');
console.log('═══════════════════════════════════════════════════════════════════════════');
console.log(`Config: lot=${LOT}, commission=${COMMISSION_PCT*100}% per side, slippage=${SLIPPAGE_PTS}pts, TP2 mult=${TP2_MULT}`);
console.log('');
console.log(`Total trade events: ${trades.length}`);
console.log(`Win rate: ${(wins.length/trades.length*100).toFixed(1)}% (${wins.length}/${trades.length})`);
console.log(`Profit factor: ${pf.toFixed(2)}`);
console.log(`Total R: ${totalR>=0?'+':''}${totalR.toFixed(2)}R`);
console.log(`Total USD: ${totalUSD>=0?'+':''}$${totalUSD.toFixed(2)} (${(totalUSD/ACCOUNT*100).toFixed(2)}% on $${ACCOUNT})`);
console.log(`Max DD: $${maxDD.toFixed(2)} (${(maxDD/ACCOUNT*100).toFixed(2)}%)`);
console.log(`Profit/DD ratio: ${(totalUSD/Math.max(maxDD,1)).toFixed(2)}×`);
console.log('');
console.log('Outcome breakdown:');
const out = {};
for (const t of trades) out[t.outcome] = (out[t.outcome]||0) + 1;
for (const [o, n] of Object.entries(out)) console.log(`  ${o}: ${n}`);
