const fs = require('fs');
const path = require('path');

const { bars, candidates } = JSON.parse(fs.readFileSync(path.join(__dirname, 'candidates_v2.json'), 'utf8'));

const SL_MULT = 1.5;
const TP_MULT = 3.0;
const MAX_BARS = 8;

function simulate(c) {
  const entry = c.entry;
  const atr = c.atr14;
  let sl, tp;
  if (c.side === 'LONG') { sl = entry - atr * SL_MULT; tp = entry + atr * TP_MULT; }
  else { sl = entry + atr * SL_MULT; tp = entry - atr * TP_MULT; }

  for (let i = c.idx + 1; i <= c.idx + MAX_BARS && i < bars.length; i++) {
    const b = bars[i];
    if (c.side === 'LONG') {
      if (b.low <= sl) return { outcome: 'SL', exitPrice: sl, exitIdx: i, barsHeld: i - c.idx, pnlR: -1 };
      if (b.high >= tp) return { outcome: 'TP', exitPrice: tp, exitIdx: i, barsHeld: i - c.idx, pnlR: TP_MULT/SL_MULT };
    } else {
      if (b.high >= sl) return { outcome: 'SL', exitPrice: sl, exitIdx: i, barsHeld: i - c.idx, pnlR: -1 };
      if (b.low <= tp) return { outcome: 'TP', exitPrice: tp, exitIdx: i, barsHeld: i - c.idx, pnlR: TP_MULT/SL_MULT };
    }
  }
  const last = bars[Math.min(c.idx + MAX_BARS, bars.length - 1)];
  const exitPrice = last.close;
  const movement = c.side === 'LONG' ? (exitPrice - entry) : (entry - exitPrice);
  return { outcome: 'TIMEOUT', exitPrice, exitIdx: Math.min(c.idx + MAX_BARS, bars.length - 1), barsHeld: MAX_BARS, pnlR: movement / (atr * SL_MULT) };
}

const results = candidates.map(c => ({ ...c, ...simulate(c) }));
fs.writeFileSync(path.join(__dirname, 'results_mechanical_v2.json'), JSON.stringify(results, null, 2));

const n = results.length;
const wins = results.filter(t => t.pnlR > 0);
const losses = results.filter(t => t.pnlR < 0);
const gw = wins.reduce((s,t)=>s+t.pnlR,0);
const gl = Math.abs(losses.reduce((s,t)=>s+t.pnlR,0));
const total = results.reduce((s,t)=>s+t.pnlR,0);

console.log(`=== MECHANICAL v2 BASELINE ===`);
console.log(`Trades: ${n} | WR: ${(wins.length/n*100).toFixed(1)}% (${wins.length}/${n})`);
console.log(`Gross W: +${gw.toFixed(2)}R | Gross L: -${gl.toFixed(2)}R`);
console.log(`Net: ${total.toFixed(2)}R | PF: ${gl>0?(gw/gl).toFixed(2):'∞'}`);
console.log(`Expectancy: ${(total/n).toFixed(3)}R/trade\n`);

results.forEach((r,i) => {
  console.log(`${String(i).padStart(2)}: ${r.timeStr} ${r.side.padEnd(5)} @ ${r.entry.toFixed(2)} → ${r.outcome.padEnd(8)} (${r.pnlR.toFixed(2).padStart(5)}R, ${r.barsHeld}b)`);
});
