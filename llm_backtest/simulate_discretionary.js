// Simulate discretionary trader results
const fs = require('fs');
const path = require('path');

const meta = JSON.parse(fs.readFileSync(path.join(__dirname, 'windows_meta.json'), 'utf8'));
const { windows, allBars } = meta;

// Agent decisions (manually compiled from outputs)
const decisions = {
  0:  { decision: 'NO_TRADE' },
  1:  { decision: 'NO_TRADE' },
  2:  { decision: 'NO_TRADE' },
  3:  { decision: 'NO_TRADE' },
  4:  { decision: 'NO_TRADE' },
  5:  { decision: 'NO_TRADE' },
  6:  { decision: 'NO_TRADE' },
  7:  { decision: 'NO_TRADE' },
  8:  { decision: 'NO_TRADE' },
  9:  { decision: 'NO_TRADE' },
  10: { decision: 'LONG', entry: 4711.05, sl: 4697.00, tp: 4730.00 },
  11: { decision: 'NO_TRADE' },
  12: { decision: 'NO_TRADE' },
  13: { decision: 'NO_TRADE' },
  14: { decision: 'NO_TRADE' },
  15: { decision: 'NO_TRADE' },
  16: { decision: 'NO_TRADE' },
  17: { decision: 'NO_TRADE' },
  18: { decision: 'NO_TRADE' },
  19: { decision: 'NO_TRADE' },
  20: { decision: 'NO_TRADE' },
  21: { decision: 'NO_TRADE' },
  22: { decision: 'NO_TRADE' },
  23: { decision: 'NO_TRADE' },
  24: { decision: 'NO_TRADE' },
  25: { decision: 'NO_TRADE' },
  26: { decision: 'NO_TRADE' },
  27: { decision: 'NO_TRADE' },
  28: { decision: 'NO_TRADE' },
  29: { decision: 'NO_TRADE' },
  30: { decision: 'NO_TRADE' },
  31: { decision: 'NO_TRADE' },
  32: { decision: 'NO_TRADE' },
  33: { decision: 'NO_TRADE' },
  34: { decision: 'NO_TRADE' },
  35: { decision: 'NO_TRADE' },
  36: { decision: 'NO_TRADE' },
  37: { decision: 'NO_TRADE' },
  38: { decision: 'LONG', entry: 4698.92, sl: 4684.50, tp: 4720.00 },
  39: { decision: 'NO_TRADE' },
  40: { decision: 'NO_TRADE' },
  41: { decision: 'NO_TRADE' },
};

const SIM_FORWARD = 8;

function simulateTrade(d, idx) {
  const w = windows[idx];
  const entry = d.entry, sl = d.sl, tp = d.tp;
  const r_dist = Math.abs(entry - sl);
  const reward = Math.abs(tp - entry);
  const rr = reward / r_dist;

  for (let i = w.idx + 1; i <= w.idx + SIM_FORWARD && i < allBars.length; i++) {
    const b = allBars[i];
    if (d.decision === 'LONG') {
      if (b.low <= sl) return { outcome: 'SL', pnlR: -1, exitPrice: sl, exitIdx: i, rr };
      if (b.high >= tp) return { outcome: 'TP', pnlR: rr, exitPrice: tp, exitIdx: i, rr };
    } else {
      if (b.high >= sl) return { outcome: 'SL', pnlR: -1, exitPrice: sl, exitIdx: i, rr };
      if (b.low <= tp) return { outcome: 'TP', pnlR: rr, exitPrice: tp, exitIdx: i, rr };
    }
  }
  const last = allBars[Math.min(w.idx + SIM_FORWARD, allBars.length - 1)];
  const mv = d.decision === 'LONG' ? (last.close - entry) : (entry - last.close);
  return { outcome: 'TIMEOUT', pnlR: mv / r_dist, exitPrice: last.close, exitIdx: w.idx + SIM_FORWARD, rr };
}

// Forecast what NO_TRADE would've been (movement-based, not entry/SL/TP)
function noTradeAnalysis(idx) {
  const w = windows[idx];
  const startPrice = w.currentPrice;
  let maxUp = 0, maxDn = 0;
  for (let i = w.idx + 1; i <= w.idx + SIM_FORWARD && i < allBars.length; i++) {
    const b = allBars[i];
    if (b.high - startPrice > maxUp) maxUp = b.high - startPrice;
    if (startPrice - b.low > maxDn) maxDn = startPrice - b.low;
  }
  const endPrice = allBars[Math.min(w.idx + SIM_FORWARD, allBars.length - 1)].close;
  return { startPrice, endPrice, maxUp, maxDn, finalMove: endPrice - startPrice };
}

console.log('\n=== DISCRETIONARY AI TRADER RESULTS ===\n');
console.log(`Total windows: ${windows.length}`);

const trades = [];
const skips = [];
for (let i = 0; i < windows.length; i++) {
  const d = decisions[i];
  if (d.decision === 'NO_TRADE') {
    skips.push({ idx: i, ...noTradeAnalysis(i), ...windows[i] });
  } else {
    const sim = simulateTrade(d, i);
    trades.push({ idx: i, decision: d, sim, time: windows[i].timeStr });
  }
}

console.log(`\nTrades taken: ${trades.length}`);
console.log(`No-trades: ${skips.length}`);
console.log(`Trade rate: ${(trades.length/windows.length*100).toFixed(1)}%`);

if (trades.length > 0) {
  console.log('\n--- TRADES DETAIL ---');
  trades.forEach(t => {
    console.log(`#${t.idx} ${t.time} ${t.decision.decision} @${t.decision.entry} SL${t.decision.sl} TP${t.decision.tp} (RR=${t.sim.rr.toFixed(2)}) → ${t.sim.outcome} ${t.sim.pnlR.toFixed(2)}R`);
  });
  const wins = trades.filter(t => t.sim.pnlR > 0).length;
  const totalR = trades.reduce((s,t)=>s+t.sim.pnlR,0);
  console.log(`\nWR: ${wins}/${trades.length} (${(wins/trades.length*100).toFixed(0)}%)`);
  console.log(`Total R: ${totalR.toFixed(2)}R`);
}

console.log('\n--- SKIPS ANALYSIS (was the skip wise?) ---');
let skipWisdomScore = 0;
let bigMoveMissed = 0;
const ATR_THRESHOLD = 1.0; // significant move = >1×ATR
skips.forEach(s => {
  const atr = s.atr14;
  const bigUpMove = s.maxUp > atr * ATR_THRESHOLD;
  const bigDnMove = s.maxDn > atr * ATR_THRESHOLD;
  const tradeable = bigUpMove || bigDnMove;
  if (tradeable) bigMoveMissed++;
  // Could "perfect trader" have profited? Yes if maxUp/maxDn > 2 ATR
  if (s.maxUp > atr * 2 || s.maxDn > atr * 2) {
    skipWisdomScore--; // bad skip — there was opportunity
  } else if (s.maxUp < atr * 1.5 && s.maxDn < atr * 1.5) {
    skipWisdomScore++; // good skip — no clean move
  }
});
console.log(`Wisdom score: ${skipWisdomScore} (positive = mostly good skips, negative = missed opportunities)`);
console.log(`Big moves missed (>1xATR either way): ${bigMoveMissed}/${skips.length}`);

// Top missed moves (largest unidirectional in skipped windows)
const sortedMisses = skips.map(s => ({ ...s, maxMove: Math.max(s.maxUp, s.maxDn), dir: s.maxUp > s.maxDn ? 'UP' : 'DN' })).sort((a,b) => b.maxMove - a.maxMove).slice(0, 5);
console.log('\nTop 5 largest moves missed:');
sortedMisses.forEach(m => {
  console.log(`  #${m.idx} ${m.timeStr}: ${m.dir} ${m.maxMove.toFixed(2)} (${(m.maxMove/m.atr14).toFixed(2)}xATR), final move ${m.finalMove.toFixed(2)}`);
});

console.log('\n--- BUY & HOLD over same period (baseline) ---');
const firstPrice = windows[0].currentPrice;
const lastPrice = windows[windows.length-1].currentPrice;
const bhMove = lastPrice - firstPrice;
const bhATR = windows[Math.floor(windows.length/2)].atr14;
console.log(`Start: ${firstPrice.toFixed(2)} → End: ${lastPrice.toFixed(2)} (${bhMove.toFixed(2)}, ~${(bhMove/bhATR).toFixed(2)}xATR)`);
