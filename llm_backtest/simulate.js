// Simulate mechanical execution per candidate
// Entry: close of candidate bar
// SL: 1.5×ATR
// TP: 3×ATR (R:R = 2:1)
// Timeout: 12 bars (1 hour)

const fs = require('fs');
const path = require('path');

const { bars, candidates } = JSON.parse(fs.readFileSync(path.join(__dirname, 'candidates.json'), 'utf8'));

const SL_MULT = 1.5;
const TP_MULT = 3.0;
const MAX_BARS = 12;

function simulate(c) {
  const entry = c.entry;
  const atr = c.atr14;
  let sl, tp;
  if (c.side === 'LONG') {
    sl = entry - atr * SL_MULT;
    tp = entry + atr * TP_MULT;
  } else {
    sl = entry + atr * SL_MULT;
    tp = entry - atr * TP_MULT;
  }

  // Iterate next bars
  for (let i = c.idx + 1; i <= c.idx + MAX_BARS && i < bars.length; i++) {
    const b = bars[i];
    if (c.side === 'LONG') {
      if (b.low <= sl) return { outcome: 'SL', exitPrice: sl, exitIdx: i, barsHeld: i - c.idx, pnlR: -1 };
      if (b.high >= tp) return { outcome: 'TP', exitPrice: tp, exitIdx: i, barsHeld: i - c.idx, pnlR: TP_MULT / SL_MULT };
    } else {
      if (b.high >= sl) return { outcome: 'SL', exitPrice: sl, exitIdx: i, barsHeld: i - c.idx, pnlR: -1 };
      if (b.low <= tp) return { outcome: 'TP', exitPrice: tp, exitIdx: i, barsHeld: i - c.idx, pnlR: TP_MULT / SL_MULT };
    }
  }
  // Timeout
  const last = bars[Math.min(c.idx + MAX_BARS, bars.length - 1)];
  const exitPrice = last.close;
  const movement = c.side === 'LONG' ? (exitPrice - entry) : (entry - exitPrice);
  const pnlR = movement / (atr * SL_MULT);
  return { outcome: 'TIMEOUT', exitPrice, exitIdx: Math.min(c.idx + MAX_BARS, bars.length - 1), barsHeld: MAX_BARS, pnlR };
}

const results = candidates.map(c => ({ ...c, ...simulate(c) }));

// Stats
function stats(trades) {
  const n = trades.length;
  if (n === 0) return { n: 0 };
  const wins = trades.filter(t => t.pnlR > 0);
  const losses = trades.filter(t => t.pnlR < 0);
  const grossWin = wins.reduce((s, t) => s + t.pnlR, 0);
  const grossLoss = Math.abs(losses.reduce((s, t) => s + t.pnlR, 0));
  return {
    n,
    wins: wins.length,
    losses: losses.length,
    timeouts: trades.filter(t => t.outcome === 'TIMEOUT').length,
    wr: (wins.length / n * 100).toFixed(1),
    pf: grossLoss > 0 ? (grossWin / grossLoss).toFixed(2) : '∞',
    expectancyR: ((grossWin - grossLoss) / n).toFixed(3),
    totalR: (grossWin - grossLoss).toFixed(2),
    avgBarsHeld: (trades.reduce((s, t) => s + t.barsHeld, 0) / n).toFixed(1),
  };
}

console.log('\n=== MECHANICAL BASELINE (all candidates) ===');
console.log(JSON.stringify(stats(results), null, 2));

console.log('\n=== LONG only ===');
console.log(JSON.stringify(stats(results.filter(r => r.side === 'LONG')), null, 2));

console.log('\n=== SHORT only ===');
console.log(JSON.stringify(stats(results.filter(r => r.side === 'SHORT')), null, 2));

console.log('\n=== Detail ===');
results.forEach(r => {
  console.log(`${r.timeStr} ${r.side} @ ${r.entry.toFixed(2)} → ${r.outcome} @ ${r.exitPrice.toFixed(2)} (${r.pnlR.toFixed(2)}R, ${r.barsHeld}b)`);
});

fs.writeFileSync(path.join(__dirname, 'results_mechanical.json'), JSON.stringify(results, null, 2));
