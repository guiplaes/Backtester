// Simulate interest-moment discretionary results
const fs = require('fs');
const path = require('path');

const meta = JSON.parse(fs.readFileSync(path.join(__dirname, 'high_interest_meta.json'), 'utf8'));
const { moments, bars } = meta;

const decisions = {
  0:  { dec: 'LONG',  e: 4551.32, sl: 4541.00, tp: 4571.00 },
  1:  { dec: 'LONG',  e: 4558.33, sl: 4548.00, tp: 4578.00 },
  2:  { dec: 'LONG',  e: 4563.34, sl: 4551.50, tp: 4587.00 },
  3:  { dec: 'LONG',  e: 4578.59, sl: 4564.00, tp: 4607.00 },
  4:  { dec: 'LONG',  e: 4576.84, sl: 4566.00, tp: 4598.00 },
  5:  { dec: 'LONG',  e: 4578.87, sl: 4569.50, tp: 4598.00 },
  6:  { dec: 'LONG',  e: 4580.00, sl: 4568.00, tp: 4605.00 },
  7:  { dec: 'SHORT', e: 4556.50, sl: 4565.50, tp: 4538.00 },
  8:  { dec: 'LONG',  e: 4598.50, sl: 4584.00, tp: 4625.00 },
  9:  { dec: 'NO_TRADE' },
  10: { dec: 'LONG',  e: 4647.61, sl: 4628.00, tp: 4685.00 },
  11: { dec: 'NO_TRADE' },
  12: { dec: 'LONG',  e: 4654.62, sl: 4644.00, tp: 4680.00 },
  13: { dec: 'LONG',  e: 4663.09, sl: 4644.00, tp: 4700.00 },
  14: { dec: 'LONG',  e: 4658.00, sl: 4647.50, tp: 4683.00 },
  15: { dec: 'NO_TRADE' },
  16: { dec: 'NO_TRADE' },
  17: { dec: 'NO_TRADE' },
  18: { dec: 'LONG',  e: 4681.27, sl: 4669.50, tp: 4704.80 },
  19: { dec: 'LONG',  e: 4676.00, sl: 4666.50, tp: 4699.00 },
  20: { dec: 'NO_TRADE' },
  21: { dec: 'NO_TRADE' },
  22: { dec: 'LONG',  e: 4700.00, sl: 4688.50, tp: 4723.00 },
  23: { dec: 'NO_TRADE' },
  24: { dec: 'LONG',  e: 4700.00, sl: 4688.00, tp: 4724.00 },
  25: { dec: 'LONG',  e: 4714.23, sl: 4699.50, tp: 4743.00 },
  26: { dec: 'NO_TRADE' },
  27: { dec: 'NO_TRADE' },
  28: { dec: 'NO_TRADE' },
  29: { dec: 'NO_TRADE' },
  30: { dec: 'NO_TRADE' },
  31: { dec: 'LONG',  e: 4748.37, sl: 4731.00, tp: 4783.00 },
  32: { dec: 'LONG',  e: 4751.75, sl: 4742.00, tp: 4770.00 },
  33: { dec: 'LONG',  e: 4756.56, sl: 4740.50, tp: 4785.00 },
  34: { dec: 'LONG',  e: 4758.50, sl: 4747.00, tp: 4785.00 },
  35: { dec: 'SHORT', e: 4729.11, sl: 4748.50, tp: 4700.00 },
  36: { dec: 'SHORT', e: 4715.03, sl: 4740.50, tp: 4675.00 },
  37: { dec: 'SHORT', e: 4720.11, sl: 4740.50, tp: 4690.00 },
  38: { dec: 'LONG',  e: 4738.03, sl: 4726.50, tp: 4760.00 },
  39: { dec: 'LONG',  e: 4747.58, sl: 4734.50, tp: 4773.00 },
  40: { dec: 'NO_TRADE' },
  41: { dec: 'NO_TRADE' },
  42: { dec: 'NO_TRADE' },
  43: { dec: 'SHORT', e: 4663.75, sl: 4681.00, tp: 4640.00 },
};

const SIM_FORWARD = 8;

function sim(d, m) {
  const e = d.e, sl = d.sl, tp = d.tp;
  const r_dist = Math.abs(e - sl);
  const reward = Math.abs(tp - e);
  const rr = reward / r_dist;

  for (let i = m.idx + 1; i <= m.idx + SIM_FORWARD && i < bars.length; i++) {
    const b = bars[i];
    if (d.dec === 'LONG') {
      if (b.low <= sl) return { outcome: 'SL', pnlR: -1, rr };
      if (b.high >= tp) return { outcome: 'TP', pnlR: rr, rr };
    } else {
      if (b.high >= sl) return { outcome: 'SL', pnlR: -1, rr };
      if (b.low <= tp) return { outcome: 'TP', pnlR: rr, rr };
    }
  }
  const last = bars[Math.min(m.idx + SIM_FORWARD, bars.length - 1)];
  const mv = d.dec === 'LONG' ? (last.close - e) : (e - last.close);
  return { outcome: 'TIMEOUT', pnlR: mv / r_dist, rr };
}

const trades = [];
for (let i = 0; i < moments.length; i++) {
  const d = decisions[i];
  if (d.dec === 'NO_TRADE') continue;
  const r = sim(d, moments[i]);
  trades.push({ idx: i, time: moments[i].timeStr, ...d, ...r });
}

console.log(`\n=== INTEREST-MOMENT DISCRETIONARY TRADER ===`);
console.log(`Total moments evaluated: ${moments.length}`);
console.log(`Trades taken: ${trades.length} (${(trades.length/moments.length*100).toFixed(1)}%)`);
console.log(`  LONG: ${trades.filter(t=>t.dec==='LONG').length}`);
console.log(`  SHORT: ${trades.filter(t=>t.dec==='SHORT').length}`);
console.log(`  NO_TRADE: ${moments.length - trades.length}`);

const wins = trades.filter(t => t.pnlR > 0);
const losses = trades.filter(t => t.pnlR < 0);
const gw = wins.reduce((s,t)=>s+t.pnlR,0);
const gl = Math.abs(losses.reduce((s,t)=>s+t.pnlR,0));
const total = trades.reduce((s,t)=>s+t.pnlR,0);

console.log(`\nWR: ${wins.length}/${trades.length} (${(wins.length/trades.length*100).toFixed(1)}%)`);
console.log(`Gross W: +${gw.toFixed(2)}R | Gross L: -${gl.toFixed(2)}R`);
console.log(`Net: ${total.toFixed(2)}R | PF: ${gl>0?(gw/gl).toFixed(2):'∞'} | Exp: ${(total/trades.length).toFixed(3)}R/t`);

console.log('\n--- TRADES DETAIL ---');
trades.forEach(t => {
  console.log(`#${String(t.idx).padStart(2)} ${t.time} ${t.dec.padEnd(5)} @${t.e.toFixed(2)} SL${t.sl.toFixed(2)} TP${t.tp.toFixed(2)} RR=${t.rr.toFixed(2)} → ${t.outcome.padEnd(8)} ${t.pnlR.toFixed(2)}R`);
});

// Buy & hold comparison
const firstP = moments[0].currentPrice;
const lastP = bars[moments[moments.length-1].idx + 8].close;
const bhMove = lastP - firstP;
const avgATR = moments[Math.floor(moments.length/2)].atr14;
console.log(`\n--- BUY & HOLD baseline same period ---`);
console.log(`First moment price: ${firstP.toFixed(2)} | Last sim end: ${lastP.toFixed(2)}`);
console.log(`Move: ${bhMove.toFixed(2)} (${(bhMove/avgATR).toFixed(2)}xATR)`);

// Equivalent R for B&H if risking same per trade
const avgR = trades.reduce((s,t)=>s+Math.abs(t.e-t.sl),0)/trades.length;
const bhR = bhMove / avgR;
console.log(`Equivalent in R-units: ${bhR.toFixed(2)}R`);
