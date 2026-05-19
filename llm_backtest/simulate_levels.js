// Simulate forward 8 M15 bars for Opus level-touch decisions
const fs = require('fs');
const path = require('path');

const meta = JSON.parse(fs.readFileSync(path.join(__dirname, 'level_meta.json'), 'utf8'));
const { touches, bars } = meta;

const decisions = {
  0:  { d: 'LONG',  e: 4553.94, sl: 4548.50, tp: 4565.00, c: 6 },
  1:  { d: 'SHORT', e: 4549.00, sl: 4556.00, tp: 4535.00, c: 6 },
  2:  { d: 'LONG',  e: 4558.50, sl: 4553.80, tp: 4568.00, c: 6 },
  3:  { d: 'SHORT', e: 4558.50, sl: 4561.50, tp: 4549.00, c: 6 },
  4:  { d: 'LONG',  e: 4552.15, sl: 4546.20, tp: 4565.00, c: 6 },
  5:  { d: 'LONG',  e: 4566.50, sl: 4561.50, tp: 4580.00, c: 6 },
  6:  { d: 'LONG',  e: 4562.85, sl: 4555.00, tp: 4575.00, c: 6 },
  7:  { d: 'LONG',  e: 4564.90, sl: 4559.00, tp: 4575.50, c: 6 },
  8:  { d: 'LONG',  e: 4583.91, sl: 4573.50, tp: 4604.50, c: 6 },
  9:  { d: 'SHORT', e: 4568.47, sl: 4585.50, tp: 4543.70, c: 6 },
  10: { d: 'SHORT', e: 4670.04, sl: 4671.80, tp: 4658.40, c: 6 },
  11: { d: 'SHORT', e: 4679.51, sl: 4683.50, tp: 4663.00, c: 6 },
  12: { d: 'SHORT', e: 4700.15, sl: 4705.00, tp: 4681.00, c: 6 },
  13: { d: 'LONG',  e: 4717.79, sl: 4707.00, tp: 4740.00, c: 6 },
  14: { d: 'SHORT', e: 4699.69, sl: 4723.50, tp: 4670.00, c: 7 },
  15: { d: 'SHORT', e: 4699.50, sl: 4724.00, tp: 4660.00, c: 6 },
  16: { d: 'SHORT', e: 4749.10, sl: 4751.30, tp: 4739.20, c: 6 },
  17: { d: 'SHORT', e: 4749.10, sl: 4752.50, tp: 4735.00, c: 7 },
  18: { d: 'LONG',  e: 4753.60, sl: 4747.20, tp: 4767.00, c: 6 },
  19: { d: 'NO_TRADE', c: 4 },
  20: { d: 'LONG',  e: 4758.50, sl: 4751.50, tp: 4772.00, c: 6 },
  21: { d: 'LONG',  e: 4751.77, sl: 4744.50, tp: 4770.00, c: 6 },
  22: { d: 'LONG',  e: 4762.57, sl: 4755.00, tp: 4780.00, c: 7 },
  23: { d: 'SHORT', e: 4722.00, sl: 4728.50, tp: 4700.00, c: 7 },
  24: { d: 'SHORT', e: 4720.34, sl: 4725.20, tp: 4707.00, c: 6 },
  25: { d: 'NO_TRADE', c: 7 },
  26: { d: 'LONG',  e: 4738.03, sl: 4730.50, tp: 4755.00, c: 6 },
  27: { d: 'LONG',  e: 4747.58, sl: 4738.00, tp: 4766.00, c: 6 },
  28: { d: 'LONG',  e: 4750.50, sl: 4744.00, tp: 4763.00, c: 7 },
  29: { d: 'SHORT', e: 4722.71, sl: 4728.50, tp: 4710.00, c: 6 },
  30: { d: 'SHORT', e: 4675.84, sl: 4681.20, tp: 4665.00, c: 6 },
};

const SIM = 8;
const ACCOUNT = 10000;
const RISK_PCT = 0.5;

function simulate(d, idx) {
  const m = touches[idx];
  const e = d.e, sl = d.sl, tp = d.tp;
  const risk = Math.abs(e - sl);
  const reward = Math.abs(tp - e);
  const rr = reward / risk;

  // Check entry: did price actually reach entry within next 3 bars (limit order)?
  // For simplicity, use market entry at first forward bar's open if entry is reasonably close,
  // otherwise wait for limit fill within first 3 bars.
  let filled = false, fillBar = null;
  for (let i = m.idx + 1; i <= m.idx + 3 && i < bars.length; i++) {
    const b = bars[i];
    if (d.d === 'LONG') {
      // For LONG: filled if price came down to entry (b.low <= e) OR opened above entry (market take)
      if (b.low <= e) { filled = true; fillBar = i; break; }
    } else {
      if (b.high >= e) { filled = true; fillBar = i; break; }
    }
  }
  if (!filled) return { outcome: 'NO_FILL', pnlR: 0, pnlUSD: 0, rr, risk };

  // Now simulate SL/TP from fillBar onward
  let outcome = null, exitPrice = null, exitTime = null, barsHeld = 0;
  for (let i = fillBar; i <= m.idx + SIM && i < bars.length; i++) {
    const b = bars[i];
    if (d.d === 'LONG') {
      if (b.low <= sl) { outcome = 'SL'; exitPrice = sl; exitTime = b.time; barsHeld = i - m.idx; break; }
      if (b.high >= tp) { outcome = 'TP'; exitPrice = tp; exitTime = b.time; barsHeld = i - m.idx; break; }
    } else {
      if (b.high >= sl) { outcome = 'SL'; exitPrice = sl; exitTime = b.time; barsHeld = i - m.idx; break; }
      if (b.low <= tp) { outcome = 'TP'; exitPrice = tp; exitTime = b.time; barsHeld = i - m.idx; break; }
    }
  }
  if (!outcome) {
    const last = bars[Math.min(m.idx + SIM, bars.length - 1)];
    outcome = 'TIMEOUT'; exitPrice = last.close; exitTime = last.time; barsHeld = SIM;
  }

  const move = d.d === 'LONG' ? (exitPrice - e) : (e - exitPrice);
  const pnlR = move / risk;
  const maxLossUSD = ACCOUNT * RISK_PCT / 100;
  const lots = maxLossUSD / (risk * 100);
  const pnlUSD = move * 100 * lots;
  return { outcome, exitPrice, exitTime, barsHeld, pnlR, pnlUSD, rr, risk, lots };
}

// Run
const trades = [];
const skips = [];
const nofills = [];
for (let i = 0; i < 31; i++) {
  const d = decisions[i];
  if (d.d === 'NO_TRADE') { skips.push({ idx: i, conf: d.c }); continue; }
  const sim = simulate(d, i);
  if (sim.outcome === 'NO_FILL') { nofills.push({ idx: i, ...d, ...sim }); continue; }
  trades.push({ idx: i, time: touches[i].timeStr, level: touches[i].level, ...d, ...sim });
}

console.log('═══════════════════════════════════════════════════════════════════════════════');
console.log('LEVEL-TOUCH + REJECT/BREAK BINARY PROMPT — 31 decisions');
console.log('═══════════════════════════════════════════════════════════════════════════════');
console.log(`Total candidates: 31`);
console.log(`Trades placed: ${trades.length}`);
console.log(`NO_TRADE skips: ${skips.length}`);
console.log(`No-fill (price didn't reach entry): ${nofills.length}`);
console.log('');

if (trades.length > 0) {
  console.log('─── INDIVIDUAL TRADES ────────────────────────────────────────────────────');
  trades.forEach((t, i) => {
    const sign = t.pnlR > 0 ? '+' : '';
    console.log(
      `#${String(i+1).padStart(2)} L_${String(t.idx).padStart(3,'0')} ${t.time.slice(5,16)} ` +
      `${t.d.padEnd(5)} ${t.level.name.padEnd(8)}@${t.e.toFixed(2)} ` +
      `SL${t.sl.toFixed(2)} TP${t.tp.toFixed(2)} R:R=${t.rr.toFixed(2)} ` +
      `→ ${t.outcome.padEnd(7)} ${sign}${t.pnlR.toFixed(2)}R (${sign}$${t.pnlUSD.toFixed(2)})`
    );
  });

  // Aggregate
  const wins = trades.filter(t => t.pnlR > 0);
  const losses = trades.filter(t => t.pnlR < 0);
  const tos = trades.filter(t => t.outcome === 'TIMEOUT');
  const totalR = trades.reduce((s,t) => s + t.pnlR, 0);
  const totalUSD = trades.reduce((s,t) => s + t.pnlUSD, 0);
  const grossWin = wins.reduce((s,t) => s + t.pnlR, 0);
  const grossLoss = Math.abs(losses.reduce((s,t) => s + t.pnlR, 0));
  const pf = grossLoss > 0 ? grossWin / grossLoss : Infinity;

  console.log('\n─── AGGREGATE ───────────────────────────────────────────────────────────');
  console.log(`Trades: ${trades.length} | Wins: ${wins.length} (${(wins.length/trades.length*100).toFixed(0)}%) | Losses: ${losses.length} | Timeouts: ${tos.length}`);
  console.log(`Total R: ${totalR >= 0 ? '+' : ''}${totalR.toFixed(2)}R`);
  console.log(`Total USD: ${totalUSD >= 0 ? '+' : ''}$${totalUSD.toFixed(2)} (${(totalUSD/ACCOUNT*100).toFixed(2)}% of $10k)`);
  console.log(`Profit Factor: ${pf.toFixed(2)}`);
  console.log(`Avg R per trade: ${(totalR/trades.length).toFixed(2)}R`);

  // By direction
  const longs = trades.filter(t => t.d === 'LONG');
  const shorts = trades.filter(t => t.d === 'SHORT');
  console.log(`\nLONG  (${longs.length}): ${longs.reduce((s,t)=>s+t.pnlR,0).toFixed(2)}R, WR ${(longs.filter(t=>t.pnlR>0).length/longs.length*100).toFixed(0)}%`);
  console.log(`SHORT (${shorts.length}): ${shorts.reduce((s,t)=>s+t.pnlR,0).toFixed(2)}R, WR ${(shorts.filter(t=>t.pnlR>0).length/shorts.length*100).toFixed(0)}%`);

  // By level type
  const byLevel = {};
  trades.forEach(t => {
    const lt = t.level.name.replace(/_\d+$/, '_RND');
    if (!byLevel[lt]) byLevel[lt] = { n: 0, w: 0, r: 0 };
    byLevel[lt].n++;
    if (t.pnlR > 0) byLevel[lt].w++;
    byLevel[lt].r += t.pnlR;
  });
  console.log('\nBy level type:');
  Object.keys(byLevel).forEach(k => {
    const o = byLevel[k];
    console.log(`  ${k.padEnd(10)} n=${o.n}, WR=${(o.w/o.n*100).toFixed(0)}%, totR=${o.r >= 0 ? '+' : ''}${o.r.toFixed(2)}`);
  });

  // By bias (REJECT vs BREAK derived from direction vs level type)
  const rejects = trades.filter(t => (t.level.type === 'resistance' && t.d === 'SHORT') || (t.level.type === 'support' && t.d === 'LONG'));
  const breaks = trades.filter(t => (t.level.type === 'resistance' && t.d === 'LONG') || (t.level.type === 'support' && t.d === 'SHORT'));
  console.log(`\nREJECT trades (${rejects.length}): ${rejects.reduce((s,t)=>s+t.pnlR,0).toFixed(2)}R, WR ${(rejects.filter(t=>t.pnlR>0).length/Math.max(rejects.length,1)*100).toFixed(0)}%`);
  console.log(`BREAK trades  (${breaks.length}): ${breaks.reduce((s,t)=>s+t.pnlR,0).toFixed(2)}R, WR ${(breaks.filter(t=>t.pnlR>0).length/Math.max(breaks.length,1)*100).toFixed(0)}%`);

  // By confidence
  const c6 = trades.filter(t => t.c === 6);
  const c7 = trades.filter(t => t.c === 7);
  console.log(`\nConfidence 6 (${c6.length}): ${c6.reduce((s,t)=>s+t.pnlR,0).toFixed(2)}R, WR ${(c6.filter(t=>t.pnlR>0).length/Math.max(c6.length,1)*100).toFixed(0)}%`);
  console.log(`Confidence 7 (${c7.length}): ${c7.reduce((s,t)=>s+t.pnlR,0).toFixed(2)}R, WR ${(c7.filter(t=>t.pnlR>0).length/Math.max(c7.length,1)*100).toFixed(0)}%`);
}

if (nofills.length > 0) {
  console.log('\n─── NO_FILL (entry never reached) ───');
  nofills.forEach(n => console.log(`  L_${String(n.idx).padStart(3,'0')} ${n.d} @${n.e}`));
}
if (skips.length > 0) {
  console.log(`\n─── SKIPS: ${skips.map(s => 'L_'+String(s.idx).padStart(3,'0')+'(c='+s.c+')').join(', ')}`);
}
