// Simulate 105 OOS Opus level-touch decisions
const fs = require('fs');
const path = require('path');

const meta = JSON.parse(fs.readFileSync(path.join(__dirname, 'level_meta_oos.json'), 'utf8'));
const { touches, bars } = meta;

const D = {
  0: {d:'LONG', e:4552.75, sl:4547.50, tp:4568.00, c:6},
  1: {d:'SHORT', e:4549.00, sl:4555.00, tp:4530.00, c:6},
  2: {d:'SHORT', e:4577.50, sl:4584.50, tp:4560.00, c:6},
  3: {d:'LONG', e:4619.50, sl:4609.00, tp:4640.00, c:7},
  4: {d:'NO_TRADE', c:4},
  5: {d:'LONG', e:4730.69, sl:4714.00, tp:4763.00, c:7},
  6: {d:'SHORT', e:4745.19, sl:4751.50, tp:4725.00, c:6},
  7: {d:'SHORT', e:4752.56, sl:4763.50, tp:4731.00, c:6},
  8: {d:'SHORT', e:4744.64, sl:4763.50, tp:4715.00, c:6},
  9: {d:'LONG', e:4760.89, sl:4750.50, tp:4785.00, c:6},
  10:{d:'LONG', e:4769.50, sl:4760.50, tp:4790.00, c:6},
  11:{d:'LONG', e:4775.50, sl:4766.00, tp:4795.00, c:6},
  12:{d:'SHORT', e:4786.70, sl:4789.00, tp:4760.50, c:6},
  13:{d:'SHORT', e:4787.30, sl:4792.50, tp:4768.00, c:6},
  14:{d:'SHORT', e:4648.50, sl:4660.00, tp:4605.00, c:7},
  15:{d:'SHORT', e:4600.00, sl:4609.00, tp:4582.00, c:6},
  16:{d:'NO_TRADE', c:3},
  17:{d:'SHORT', e:4648.50, sl:4658.50, tp:4612.00, c:6},
  18:{d:'SHORT', e:4665.30, sl:4677.50, tp:4633.00, c:6},
  19:{d:'SHORT', e:4697.00, sl:4704.50, tp:4660.00, c:6},
  20:{d:'NO_TRADE', c:3},
  21:{d:'LONG', e:4654.99, sl:4644.50, tp:4685.00, c:6},
  22:{d:'SHORT', e:4652.24, sl:4667.50, tp:4625.00, c:6},
  23:{d:'SHORT', e:4652.24, sl:4660.50, tp:4642.93, c:6},
  24:{d:'LONG', e:4689.70, sl:4680.00, tp:4710.00, c:6},
  25:{d:'NO_TRADE', c:3},
  26:{d:'NO_TRADE', c:4},
  27:{d:'SHORT', e:4695.86, sl:4700.50, tp:4684.00, c:7},
  28:{d:'SHORT', e:4699.50, sl:4702.50, tp:4684.00, c:6},
  29:{d:'LONG', e:4701.31, sl:4694.50, tp:4715.00, c:6},
  30:{d:'SHORT', e:4702.62, sl:4704.40, tp:4690.00, c:6},
  31:{d:'SHORT', e:4703.54, sl:4707.50, tp:4691.00, c:6},
  32:{d:'SHORT', e:4651.54, sl:4658.50, tp:4635.00, c:6},
  33:{d:'LONG', e:4651.54, sl:4644.50, tp:4664.00, c:6},
  34:{d:'SHORT', e:4663.77, sl:4667.50, tp:4651.50, c:6},
  35:{d:'SHORT', e:4652.33, sl:4656.50, tp:4632.00, c:7},
  36:{d:'LONG', e:4652.33, sl:4646.00, tp:4670.00, c:6},
  37:{d:'SHORT', e:4654.59, sl:4660.00, tp:4635.00, c:6},
  38:{d:'LONG', e:4651.67, sl:4646.50, tp:4668.00, c:6},
  39:{d:'SHORT', e:4616.53, sl:4628.00, tp:4585.00, c:6},
  40:{d:'SHORT', e:4648.50, sl:4656.20, tp:4625.00, c:6},
  41:{d:'NO_TRADE', c:3},
  42:{d:'LONG', e:4755.72, sl:4744.00, tp:4800.00, c:6},
  43:{d:'SHORT', e:4761.94, sl:4767.50, tp:4737.00, c:7},
  44:{d:'NO_TRADE', c:3},
  45:{d:'SHORT', e:4762.56, sl:4772.50, tp:4753.38, c:6},
  46:{d:'SHORT', e:4768.35, sl:4773.50, tp:4745.00, c:6},
  47:{d:'SHORT', e:4766.85, sl:4773.50, tp:4735.00, c:6},
  48:{d:'SHORT', e:4762.93, sl:4772.50, tp:4735.65, c:7},
  49:{d:'LONG', e:4772.80, sl:4762.00, tp:4795.00, c:6},
  50:{d:'NO_TRADE', c:3},
  51:{d:'LONG', e:4730.95, sl:4724.50, tp:4745.00, c:6},
  52:{d:'SHORT', e:4699.50, sl:4710.50, tp:4680.00, c:6},
  53:{d:'SHORT', e:4766.50, sl:4774.50, tp:4751.00, c:6},
  54:{d:'SHORT', e:4794.81, sl:4804.20, tp:4775.00, c:6},
  55:{d:'SHORT', e:4809.49, sl:4814.50, tp:4790.00, c:6},
  56:{d:'NO_TRADE', c:3},
  57:{d:'NO_TRADE', c:3},
  58:{d:'SHORT', e:4818.50, sl:4821.50, tp:4802.50, c:6},
  59:{d:'LONG', e:4812.84, sl:4803.00, tp:4835.00, c:6},
  60:{d:'NO_TRADE', c:3},
  61:{d:'SHORT', e:4798.31, sl:4806.20, tp:4782.00, c:6},
  62:{d:'SHORT', e:4811.33, sl:4816.80, tp:4800.00, c:6},
  63:{d:'LONG', e:4807.00, sl:4803.10, tp:4818.50, c:6},
  64:{d:'SHORT', e:4805.00, sl:4810.30, tp:4794.00, c:6},
  65:{d:'SHORT', e:4799.35, sl:4806.20, tp:4786.00, c:6},
  66:{d:'SHORT', e:4788.86, sl:4805.80, tp:4760.00, c:6},
  67:{d:'SHORT', e:4788.86, sl:4806.50, tp:4760.00, c:6},
  68:{d:'SHORT', e:4799.41, sl:4811.50, tp:4775.00, c:7},
  69:{d:'NO_TRADE', c:3},
  70:{d:'LONG', e:4787.49, sl:4778.30, tp:4800.99, c:6},
  71:{d:'SHORT', e:4799.13, sl:4812.50, tp:4785.00, c:6},
  72:{d:'NO_TRADE', c:4},
  73:{d:'SHORT', e:4848.50, sl:4854.50, tp:4816.00, c:6},
  74:{d:'SHORT', e:4847.00, sl:4852.00, tp:4816.00, c:6},
  75:{d:'SHORT', e:4871.62, sl:4886.20, tp:4836.00, c:6},
  76:{d:'SHORT', e:4870.50, sl:4878.00, tp:4851.00, c:6},
  77:{d:'LONG', e:4798.06, sl:4791.00, tp:4812.00, c:6},
  78:{d:'SHORT', e:4799.50, sl:4806.50, tp:4779.50, c:6},
  79:{d:'LONG', e:4814.80, sl:4807.50, tp:4829.00, c:6},
  80:{d:'LONG', e:4801.50, sl:4793.00, tp:4820.00, c:6},
  81:{d:'LONG', e:4824.48, sl:4818.20, tp:4837.50, c:7},
  82:{d:'LONG', e:4826.50, sl:4818.00, tp:4845.00, c:6},
  83:{d:'SHORT', e:4799.95, sl:4810.90, tp:4788.88, c:6},
  84:{d:'SHORT', e:4794.62, sl:4801.20, tp:4781.50, c:6},
  85:{d:'NO_TRADE', c:3},
  86:{d:'SHORT', e:4739.02, sl:4755.50, tp:4710.00, c:6},
  87:{d:'SHORT', e:4720.88, sl:4731.50, tp:4699.50, c:6},
  88:{d:'SHORT', e:4750.52, sl:4756.20, tp:4735.00, c:6},
  89:{d:'NO_TRADE', c:3},
  90:{d:'SHORT', e:4736.11, sl:4748.00, tp:4712.00, c:6},
  91:{d:'SHORT', e:4727.68, sl:4736.50, tp:4710.00, c:7},
  92:{d:'SHORT', e:4727.22, sl:4736.50, tp:4710.00, c:6},
  93:{d:'NO_TRADE', c:3},
  94:{d:'NO_TRADE', c:3},
  95:{d:'NO_TRADE', c:4},
  96:{d:'SHORT', e:4720.65, sl:4727.50, tp:4706.00, c:6},
  97:{d:'LONG', e:4715.29, sl:4708.50, tp:4729.00, c:6},
  98:{d:'SHORT', e:4700.64, sl:4712.00, tp:4678.00, c:6},
  99:{d:'NO_TRADE', c:3},
  100:{d:'LONG', e:4702.35, sl:4695.50, tp:4716.00, c:6},
  101:{d:'NO_TRADE', c:4},
  102:{d:'LONG', e:4700.15, sl:4693.00, tp:4715.50, c:6},
  103:{d:'NO_TRADE', c:3},
  104:{d:'LONG', e:4670.23, sl:4665.50, tp:4696.80, c:6},
};

const SIM = 8, ACCOUNT = 10000, RISK_PCT = 0.5;

function simulate(d, idx) {
  const m = touches[idx];
  const e=d.e, sl=d.sl, tp=d.tp;
  const risk = Math.abs(e - sl);
  const rr = Math.abs(tp - e) / risk;

  let filled = false, fillBar = null;
  for (let i = m.idx + 1; i <= m.idx + 3 && i < bars.length; i++) {
    const b = bars[i];
    if (d.d === 'LONG') { if (b.low <= e) { filled = true; fillBar = i; break; } }
    else { if (b.high >= e) { filled = true; fillBar = i; break; } }
  }
  if (!filled) return { outcome: 'NO_FILL', pnlR: 0, pnlUSD: 0, rr, risk };

  let outcome = null, exitPrice = null, barsHeld = 0;
  for (let i = fillBar; i <= m.idx + SIM && i < bars.length; i++) {
    const b = bars[i];
    if (d.d === 'LONG') {
      if (b.low <= sl) { outcome = 'SL'; exitPrice = sl; barsHeld = i - m.idx; break; }
      if (b.high >= tp) { outcome = 'TP'; exitPrice = tp; barsHeld = i - m.idx; break; }
    } else {
      if (b.high >= sl) { outcome = 'SL'; exitPrice = sl; barsHeld = i - m.idx; break; }
      if (b.low <= tp) { outcome = 'TP'; exitPrice = tp; barsHeld = i - m.idx; break; }
    }
  }
  if (!outcome) {
    const last = bars[Math.min(m.idx + SIM, bars.length - 1)];
    outcome = 'TIMEOUT'; exitPrice = last.close; barsHeld = SIM;
  }
  const move = d.d === 'LONG' ? (exitPrice - e) : (e - exitPrice);
  const pnlR = move / risk;
  const maxLossUSD = ACCOUNT * RISK_PCT / 100;
  const lots = maxLossUSD / (risk * 100);
  const pnlUSD = move * 100 * lots;
  return { outcome, exitPrice, barsHeld, pnlR, pnlUSD, rr, risk };
}

const trades = [], skips = [], nofills = [];
for (let i = 0; i < 105; i++) {
  const d = D[i]; if (!d) continue;
  if (d.d === 'NO_TRADE') { skips.push({i, c:d.c}); continue; }
  const sim = simulate(d, i);
  if (sim.outcome === 'NO_FILL') { nofills.push({i, ...d, ...sim}); continue; }
  trades.push({i, time: touches[i].timeStr, level: touches[i].level, ...d, ...sim});
}

console.log('═══════════════════════════════════════════════════════════════════════════════');
console.log('OOS VALIDATION — 105 level touches, SAME v1 detector + prompt');
console.log('═══════════════════════════════════════════════════════════════════════════════');
console.log(`Total: 105 | Trades: ${trades.length} | NO_TRADE: ${skips.length} | NO_FILL: ${nofills.length}\n`);

const wins = trades.filter(t => t.pnlR > 0);
const losses = trades.filter(t => t.pnlR < 0);
const tos = trades.filter(t => t.outcome === 'TIMEOUT');
const totalR = trades.reduce((s,t) => s+t.pnlR, 0);
const totalUSD = trades.reduce((s,t) => s+t.pnlUSD, 0);
const grossWin = wins.reduce((s,t)=>s+t.pnlR,0);
const grossLoss = Math.abs(losses.reduce((s,t)=>s+t.pnlR,0));
const pf = grossLoss > 0 ? grossWin / grossLoss : Infinity;

console.log('─── AGGREGATE ─────────────────────────────────────────────────');
console.log(`Wins: ${wins.length} (${(wins.length/trades.length*100).toFixed(0)}%) | Losses: ${losses.length} | Timeouts: ${tos.length}`);
console.log(`Total R: ${totalR>=0?'+':''}${totalR.toFixed(2)}R`);
console.log(`Total USD: ${totalUSD>=0?'+':''}$${totalUSD.toFixed(2)} (${(totalUSD/ACCOUNT*100).toFixed(2)}%)`);
console.log(`Profit Factor: ${pf.toFixed(2)}`);
console.log(`Avg R/trade: ${(totalR/trades.length).toFixed(2)}R`);

// By direction
const longs = trades.filter(t => t.d === 'LONG');
const shorts = trades.filter(t => t.d === 'SHORT');
const longR = longs.reduce((s,t)=>s+t.pnlR,0);
const shortR = shorts.reduce((s,t)=>s+t.pnlR,0);
console.log(`\n─── BY DIRECTION ─────────────────────────────────────────────`);
console.log(`LONG  (${longs.length}): WR ${(longs.filter(t=>t.pnlR>0).length/Math.max(longs.length,1)*100).toFixed(0)}% | Total ${longR>=0?'+':''}${longR.toFixed(2)}R | Avg ${(longR/Math.max(longs.length,1)).toFixed(2)}R`);
console.log(`SHORT (${shorts.length}): WR ${(shorts.filter(t=>t.pnlR>0).length/Math.max(shorts.length,1)*100).toFixed(0)}% | Total ${shortR>=0?'+':''}${shortR.toFixed(2)}R | Avg ${(shortR/Math.max(shorts.length,1)).toFixed(2)}R`);

// By bias (REJECT vs BREAK)
const rejects = trades.filter(t => (t.level.type === 'resistance' && t.d === 'SHORT') || (t.level.type === 'support' && t.d === 'LONG'));
const breaks = trades.filter(t => (t.level.type === 'resistance' && t.d === 'LONG') || (t.level.type === 'support' && t.d === 'SHORT'));
const rejR = rejects.reduce((s,t)=>s+t.pnlR,0);
const brkR = breaks.reduce((s,t)=>s+t.pnlR,0);
console.log(`\n─── BY BIAS ─────────────────────────────────────────────────`);
console.log(`REJECT (${rejects.length}): WR ${(rejects.filter(t=>t.pnlR>0).length/Math.max(rejects.length,1)*100).toFixed(0)}% | Total ${rejR>=0?'+':''}${rejR.toFixed(2)}R`);
console.log(`BREAK  (${breaks.length}): WR ${(breaks.filter(t=>t.pnlR>0).length/Math.max(breaks.length,1)*100).toFixed(0)}% | Total ${brkR>=0?'+':''}${brkR.toFixed(2)}R`);

// By level type
const byLevel = {};
trades.forEach(t => {
  const lt = t.level.name.replace(/_\d+$/, '_RND');
  if (!byLevel[lt]) byLevel[lt] = { n:0, w:0, r:0 };
  byLevel[lt].n++; if (t.pnlR > 0) byLevel[lt].w++; byLevel[lt].r += t.pnlR;
});
console.log(`\n─── BY LEVEL TYPE ───────────────────────────────────────────`);
Object.keys(byLevel).forEach(k => {
  const o = byLevel[k];
  console.log(`  ${k.padEnd(10)} n=${o.n}, WR=${(o.w/o.n*100).toFixed(0)}%, Total ${o.r>=0?'+':''}${o.r.toFixed(2)}R`);
});

// By confidence
console.log(`\n─── BY CONFIDENCE ────────────────────────────────────────────`);
[6,7].forEach(c => {
  const ts = trades.filter(t => t.c === c);
  if (ts.length === 0) return;
  const tR = ts.reduce((s,t)=>s+t.pnlR,0);
  console.log(`  C=${c} n=${ts.length}, WR=${(ts.filter(t=>t.pnlR>0).length/ts.length*100).toFixed(0)}%, Total ${tR>=0?'+':''}${tR.toFixed(2)}R`);
});

// By date (week)
const byDay = {};
trades.forEach(t => {
  const d = t.time.slice(0,10);
  if (!byDay[d]) byDay[d] = { n:0, w:0, r:0 };
  byDay[d].n++; if (t.pnlR > 0) byDay[d].w++; byDay[d].r += t.pnlR;
});
console.log(`\n─── BY DAY ─────────────────────────────────────────────────`);
Object.keys(byDay).sort().forEach(d => {
  const o = byDay[d];
  console.log(`  ${d}: n=${o.n}, WR=${(o.w/o.n*100).toFixed(0)}%, R=${o.r>=0?'+':''}${o.r.toFixed(2)}`);
});

// Save individual trades for reference
fs.writeFileSync(path.join(__dirname, 'oos_trades.json'), JSON.stringify(trades, null, 2));
