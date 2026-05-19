// Comprehensive analysis: Opus trades + skip wisdom
const fs = require('fs');
const path = require('path');

const meta = JSON.parse(fs.readFileSync(path.join(__dirname, 'rich_meta.json'), 'utf8'));
const { moments, bars } = meta;

// All decisions (windows 0-125)
const decisions = {
  // 0-49: Opus v1 (conservative prompt)
  0: { dec: 'NO_TRADE', conf: 3 },
  1: { dec: 'LONG', e: 4551.32, sl: 4541.50, tp: 4570.00, conf: 7 },
  2: { dec: 'NO_TRADE', conf: 3 }, 3: { dec: 'NO_TRADE', conf: 3 },
  4: { dec: 'NO_TRADE', conf: 3 }, 5: { dec: 'NO_TRADE', conf: 3 },
  6: { dec: 'NO_TRADE', conf: 3 }, 7: { dec: 'NO_TRADE', conf: 3 },
  8: { dec: 'NO_TRADE', conf: 3 }, 9: { dec: 'NO_TRADE', conf: 3 },
  10: { dec: 'NO_TRADE', conf: 3 }, 11: { dec: 'NO_TRADE', conf: 3 },
  12: { dec: 'NO_TRADE', conf: 3 }, 13: { dec: 'NO_TRADE', conf: 3 },
  14: { dec: 'NO_TRADE', conf: 6 }, 15: { dec: 'NO_TRADE', conf: 6 },
  16: { dec: 'NO_TRADE', conf: 6 }, 17: { dec: 'NO_TRADE', conf: 6 },
  18: { dec: 'NO_TRADE', conf: 3 }, 19: { dec: 'NO_TRADE', conf: 7 },
  20: { dec: 'NO_TRADE', conf: 6 }, 21: { dec: 'NO_TRADE', conf: 3 },
  22: { dec: 'NO_TRADE', conf: 3 }, 23: { dec: 'NO_TRADE', conf: 3 },
  24: { dec: 'NO_TRADE', conf: 3 }, 25: { dec: 'NO_TRADE', conf: 3 },
  26: { dec: 'NO_TRADE', conf: 3 }, 27: { dec: 'NO_TRADE', conf: 7 },
  28: { dec: 'NO_TRADE', conf: 3 }, 29: { dec: 'NO_TRADE', conf: 7 },
  30: { dec: 'NO_TRADE', conf: 3 }, 31: { dec: 'NO_TRADE', conf: 7 },
  32: { dec: 'NO_TRADE', conf: 7 }, 33: { dec: 'NO_TRADE', conf: 7 },
  34: { dec: 'NO_TRADE', conf: 7 }, 35: { dec: 'NO_TRADE', conf: 7 },
  36: { dec: 'NO_TRADE', conf: 7 }, 37: { dec: 'NO_TRADE', conf: 7 },
  38: { dec: 'NO_TRADE', conf: 7 }, 39: { dec: 'NO_TRADE', conf: 7 },
  40: { dec: 'NO_TRADE', conf: 3 }, 41: { dec: 'NO_TRADE', conf: 3 },
  42: { dec: 'NO_TRADE', conf: 7 }, 43: { dec: 'NO_TRADE', conf: 7 },
  44: { dec: 'NO_TRADE', conf: 7 }, 45: { dec: 'NO_TRADE', conf: 8 },
  46: { dec: 'NO_TRADE', conf: 7 }, 47: { dec: 'NO_TRADE', conf: 7 },
  48: { dec: 'NO_TRADE', conf: 3 }, 49: { dec: 'NO_TRADE', conf: 7 },
  // 50-59: also conservative (still NO_TRADE)
  50: { dec: 'NO_TRADE', conf: 3 }, 51: { dec: 'NO_TRADE', conf: 4 },
  52: { dec: 'NO_TRADE', conf: 3 }, 53: { dec: 'NO_TRADE', conf: 6 },
  54: { dec: 'NO_TRADE', conf: 6 }, 55: { dec: 'NO_TRADE', conf: 4 },
  56: { dec: 'NO_TRADE', conf: 4 }, 57: { dec: 'NO_TRADE', conf: 4 },
  58: { dec: 'NO_TRADE', conf: 6 }, 59: { dec: 'NO_TRADE', conf: 4 },
  // 60-125: Opus v3 MOMENTUM prompt
  60: { dec: 'LONG', e: 4677.02, sl: 4665.00, tp: 4700.00, conf: 7 },
  61: { dec: 'LONG', e: 4677.43, sl: 4665.50, tp: 4701.30, conf: 7 },
  62: { dec: 'LONG', e: 4700.15, sl: 4685.00, tp: 4730.00, conf: 8 },
  63: { dec: 'LONG', e: 4695.46, sl: 4678.00, tp: 4730.00, conf: 7 },
  64: { dec: 'LONG', e: 4700.00, sl: 4688.00, tp: 4724.00, conf: 7 },
  65: { dec: 'LONG', e: 4696.01, sl: 4684.00, tp: 4720.00, conf: 7 },
  66: { dec: 'LONG', e: 4701.63, sl: 4688.50, tp: 4727.00, conf: 7 },
  67: { dec: 'LONG', e: 4704.48, sl: 4691.00, tp: 4730.00, conf: 7 },
  68: { dec: 'LONG', e: 4714.23, sl: 4699.50, tp: 4744.00, conf: 8 },
  69: { dec: 'LONG', e: 4717.79, sl: 4704.00, tp: 4745.00, conf: 8 },
  70: { dec: 'LONG', e: 4711.91, sl: 4697.00, tp: 4740.00, conf: 7 },
  71: { dec: 'SHORT', e: 4699.69, sl: 4716.50, tp: 4670.00, conf: 6 },
  72: { dec: 'SHORT', e: 4691.83, sl: 4708.50, tp: 4665.00, conf: 6 },
  73: { dec: 'LONG', e: 4695.25, sl: 4681.00, tp: 4723.00, conf: 6 },
  74: { dec: 'LONG', e: 4702.05, sl: 4688.30, tp: 4729.55, conf: 7 },
  75: { dec: 'LONG', e: 4711.05, sl: 4694.00, tp: 4745.00, conf: 7 },
  76: { dec: 'LONG', e: 4695.04, sl: 4685.50, tp: 4714.00, conf: 6 },
  77: { dec: 'LONG', e: 4699.50, sl: 4690.50, tp: 4717.50, conf: 6 },
  78: { dec: 'LONG', e: 4712.91, sl: 4699.50, tp: 4735.00, conf: 7 },
  79: { dec: 'LONG', e: 4703.60, sl: 4694.40, tp: 4721.00, conf: 6 },
  80: { dec: 'LONG', e: 4717.15, sl: 4708.50, tp: 4734.00, conf: 7 },
  81: { dec: 'LONG', e: 4745.50, sl: 4730.00, tp: 4776.50, conf: 8 },
  82: { dec: 'LONG', e: 4739.17, sl: 4727.00, tp: 4763.00, conf: 7 },
  83: { dec: 'LONG', e: 4749.10, sl: 4734.00, tp: 4779.00, conf: 7 },
  84: { dec: 'LONG', e: 4739.37, sl: 4724.50, tp: 4769.00, conf: 7 },
  85: { dec: 'LONG', e: 4742.50, sl: 4730.00, tp: 4767.50, conf: 7 },
  86: { dec: 'SHORT', e: 4734.18, sl: 4754.50, tp: 4703.50, conf: 6 },
  87: { dec: 'SHORT', e: 4729.13, sl: 4743.50, tp: 4710.00, conf: 6 },
  88: { dec: 'LONG', e: 4738.84, sl: 4724.00, tp: 4768.50, conf: 7 },
  89: { dec: 'LONG', e: 4733.06, sl: 4721.50, tp: 4753.00, conf: 6 },
  90: { dec: 'LONG', e: 4742.74, sl: 4730.00, tp: 4768.00, conf: 7 },
  91: { dec: 'LONG', e: 4751.11, sl: 4738.00, tp: 4777.00, conf: 7 },
  92: { dec: 'LONG', e: 4745.26, sl: 4730.50, tp: 4774.80, conf: 7 },
  93: { dec: 'LONG', e: 4748.37, sl: 4736.00, tp: 4772.00, conf: 6 },
  94: { dec: 'LONG', e: 4746.11, sl: 4732.00, tp: 4770.00, conf: 7 },
  95: { dec: 'LONG', e: 4751.75, sl: 4738.00, tp: 4778.00, conf: 7 },
  96: { dec: 'LONG', e: 4756.56, sl: 4744.50, tp: 4780.00, conf: 7 },
  97: { dec: 'LONG', e: 4751.77, sl: 4738.00, tp: 4775.00, conf: 7 },
  98: { dec: 'LONG', e: 4762.57, sl: 4747.00, tp: 4790.00, conf: 7 },
  99: { dec: 'SHORT', e: 4729.11, sl: 4742.90, tp: 4707.00, conf: 7 },
  100: { dec: 'SHORT', e: 4715.03, sl: 4732.00, tp: 4688.00, conf: 7 },
  101: { dec: 'SHORT', e: 4720.11, sl: 4740.50, tp: 4690.00, conf: 7 },
  102: { dec: 'SHORT', e: 4711.22, sl: 4725.00, tp: 4685.00, conf: 7 },
  103: { dec: 'SHORT', e: 4698.91, sl: 4720.50, tp: 4660.00, conf: 8 },
  104: { dec: 'SHORT', e: 4685.50, sl: 4702.00, tp: 4655.00, conf: 7 },
  105: { dec: 'LONG', e: 4714.14, sl: 4700.50, tp: 4740.00, conf: 6 },
  106: { dec: 'LONG', e: 4722.71, sl: 4708.50, tp: 4751.00, conf: 7 },
  107: { dec: 'LONG', e: 4723.96, sl: 4712.50, tp: 4746.50, conf: 7 },
  108: { dec: 'LONG', e: 4733.51, sl: 4718.00, tp: 4764.50, conf: 7 },
  109: { dec: 'SHORT', e: 4706.67, sl: 4720.50, tp: 4685.00, conf: 7 },
  110: { dec: 'LONG', e: 4716.86, sl: 4706.00, tp: 4734.50, conf: 6 },
  111: { dec: 'LONG', e: 4719.65, sl: 4708.00, tp: 4742.00, conf: 6 },
  112: { dec: 'LONG', e: 4720.44, sl: 4708.00, tp: 4740.00, conf: 6 },
  113: { dec: 'LONG', e: 4738.03, sl: 4726.50, tp: 4761.00, conf: 7 },
  114: { dec: 'LONG', e: 4747.58, sl: 4736.71, tp: 4769.32, conf: 8 },
  115: { dec: 'LONG', e: 4731.60, sl: 4718.50, tp: 4757.00, conf: 6 },
  116: { dec: 'SHORT', e: 4716.55, sl: 4734.00, tp: 4690.00, conf: 7 },
  117: { dec: 'SHORT', e: 4707.86, sl: 4725.00, tp: 4683.00, conf: 7 },
  118: { dec: 'SHORT', e: 4705.98, sl: 4720.50, tp: 4682.00, conf: 7 },
  119: { dec: 'SHORT', e: 4707.09, sl: 4720.50, tp: 4685.00, conf: 7 },
  120: { dec: 'SHORT', e: 4718.51, sl: 4729.20, tp: 4702.03, conf: 6 },
  121: { dec: 'SHORT', e: 4686.96, sl: 4716.50, tp: 4640.00, conf: 7 },
  122: { dec: 'SHORT', e: 4682.50, sl: 4699.00, tp: 4655.00, conf: 7 },
  123: { dec: 'SHORT', e: 4683.03, sl: 4692.00, tp: 4665.00, conf: 7 },
  124: { dec: 'SHORT', e: 4663.75, sl: 4681.00, tp: 4630.00, conf: 7 },
  125: { dec: 'SHORT', e: 4652.15, sl: 4664.50, tp: 4628.00, conf: 7 },
};

const SIM = 8;
const ACCOUNT = 10000;
const RISK_PCT = 0.5;

function simulate(d, idx) {
  const m = moments[idx];
  const e = d.e, sl = d.sl, tp = d.tp;
  const rDist = Math.abs(e - sl);
  const tpDist = Math.abs(tp - e);
  const rr = tpDist / rDist;
  const maxLossUSD = ACCOUNT * RISK_PCT / 100;
  const lots = maxLossUSD / (rDist * 100);

  let outcome = null, exitPrice = null, exitTime = null, barsHeld = null;
  for (let i = m.idx + 1; i <= m.idx + SIM && i < bars.length; i++) {
    const b = bars[i];
    if (d.dec === 'LONG') {
      if (b.low <= sl) { outcome = 'SL'; exitPrice = sl; exitTime = b.time; barsHeld = i - m.idx; break; }
      if (b.high >= tp) { outcome = 'TP'; exitPrice = tp; exitTime = b.time; barsHeld = i - m.idx; break; }
    } else {
      if (b.high >= sl) { outcome = 'SL'; exitPrice = sl; exitTime = b.time; barsHeld = i - m.idx; break; }
      if (b.low <= tp) { outcome = 'TP'; exitPrice = tp; exitTime = b.time; barsHeld = i - m.idx; break; }
    }
  }
  if (outcome === null) {
    const last = bars[Math.min(m.idx + SIM, bars.length - 1)];
    outcome = 'TIMEOUT';
    exitPrice = last.close;
    exitTime = last.time;
    barsHeld = SIM;
  }
  const priceMove = d.dec === 'LONG' ? (exitPrice - e) : (e - exitPrice);
  const pnlR = priceMove / rDist;
  const pnlUSD = priceMove * 100 * lots;
  const pnlPct = pnlUSD / ACCOUNT * 100;
  return { entry: e, sl, tp, rr, lots, rDist, maxLossUSD, outcome, exitPrice, exitTime, barsHeld, pnlR, pnlUSD, pnlPct };
}

function analyzeSkip(idx) {
  const m = moments[idx];
  const startPrice = m.currentPrice;
  const atr = m.atr14;
  let maxUp = 0, maxDn = 0;
  for (let i = m.idx + 1; i <= m.idx + SIM && i < bars.length; i++) {
    const b = bars[i];
    if (b.high - startPrice > maxUp) maxUp = b.high - startPrice;
    if (startPrice - b.low > maxDn) maxDn = startPrice - b.low;
  }
  const endPrice = bars[Math.min(m.idx + SIM, bars.length - 1)].close;
  const finalMove = endPrice - startPrice;
  // Was there a tradeable move (>1.5×ATR in either direction)?
  const tradeable = maxUp > atr * 1.5 || maxDn > atr * 1.5;
  // Was there a CLEAN move (final move > 1×ATR in one direction without retracement)?
  const cleanUp = finalMove > atr * 1.0 && maxDn < atr * 0.5;
  const cleanDn = finalMove < -atr * 1.0 && maxUp < atr * 0.5;
  return { startPrice, endPrice, maxUp, maxDn, finalMove, atr, tradeable, cleanMove: cleanUp || cleanDn, direction: cleanUp ? 'UP' : cleanDn ? 'DOWN' : null };
}

// Process all
const trades = [], skips = [];
for (let i = 0; i < 126; i++) {
  const d = decisions[i];
  if (d.dec === 'NO_TRADE') skips.push({ idx: i, ...d, ...analyzeSkip(i), time: moments[i].timeStr });
  else trades.push({ idx: i, ...d, ...simulate(d, i), time: moments[i].timeStr });
}

console.log('═══════════════════════════════════════════════════════════════════════════════');
console.log(`OPUS DISCRETIONARY — FULL ANALYSIS (126 windows)`);
console.log('═══════════════════════════════════════════════════════════════════════════════');
console.log(`Account: $${ACCOUNT} | Risk per trade: ${RISK_PCT}%`);
console.log(`\nTotal windows: 126`);
console.log(`Trades taken: ${trades.length}`);
console.log(`No-trades:    ${skips.length}`);
console.log(`Trade rate:   ${(trades.length/126*100).toFixed(1)}%\n`);

// ===== TRADES STATS =====
const wins = trades.filter(t => t.pnlR > 0);
const losses = trades.filter(t => t.pnlR < 0);
const gw = wins.reduce((s,t)=>s+t.pnlR,0);
const gl = Math.abs(losses.reduce((s,t)=>s+t.pnlR,0));
const totalR = trades.reduce((s,t)=>s+t.pnlR,0);
const totalUSD = trades.reduce((s,t)=>s+t.pnlUSD,0);

console.log('═══════════════════════════════════════════════════════════════════════════════');
console.log('TRADES AGGREGATE');
console.log('═══════════════════════════════════════════════════════════════════════════════');
console.log(`Trades:       ${trades.length}`);
console.log(`Wins:         ${wins.length} (${(wins.length/trades.length*100).toFixed(1)}%)`);
console.log(`Losses:       ${losses.length}`);
console.log(`Gross W:      +${gw.toFixed(2)}R`);
console.log(`Gross L:      -${gl.toFixed(2)}R`);
console.log(`Net R:        ${totalR > 0 ? '+' : ''}${totalR.toFixed(2)}R`);
console.log(`Net USD:      ${totalUSD > 0 ? '+' : ''}$${totalUSD.toFixed(2)}`);
console.log(`Net %:        ${(totalUSD/ACCOUNT*100).toFixed(3)}%`);
console.log(`PF:           ${gl>0?(gw/gl).toFixed(2):'∞'}`);
console.log(`Expectancy:   ${(totalR/trades.length).toFixed(3)}R/trade`);

// LONG vs SHORT
const longs = trades.filter(t => t.dec === 'LONG');
const shorts = trades.filter(t => t.dec === 'SHORT');
console.log(`\nLONG: ${longs.length} trades, WR ${(longs.filter(t=>t.pnlR>0).length/longs.length*100).toFixed(1)}%, net ${longs.reduce((s,t)=>s+t.pnlR,0).toFixed(2)}R`);
console.log(`SHORT: ${shorts.length} trades, WR ${(shorts.filter(t=>t.pnlR>0).length/shorts.length*100).toFixed(1)}%, net ${shorts.reduce((s,t)=>s+t.pnlR,0).toFixed(2)}R`);

// Per confidence
console.log('\n--- WR by confidence ---');
const byConf = {};
trades.forEach(t => { byConf[t.conf] = byConf[t.conf] || []; byConf[t.conf].push(t); });
Object.keys(byConf).sort().forEach(c => {
  const arr = byConf[c];
  const w = arr.filter(t => t.pnlR > 0).length;
  const net = arr.reduce((s,t)=>s+t.pnlR,0);
  console.log(`  Conf ${c}: ${arr.length}t, ${w} wins (${(w/arr.length*100).toFixed(0)}%), net ${net.toFixed(2)}R`);
});

// ===== SKIPS ANALYSIS =====
console.log('\n═══════════════════════════════════════════════════════════════════════════════');
console.log('SKIPS WISDOM ANALYSIS');
console.log('═══════════════════════════════════════════════════════════════════════════════');
const skipsTradeable = skips.filter(s => s.tradeable);
const skipsCleanMove = skips.filter(s => s.cleanMove);
const skipsChop = skips.filter(s => !s.tradeable);

console.log(`Skips analyzed:           ${skips.length}`);
console.log(`  → Skip was GOOD (chop): ${skipsChop.length} (${(skipsChop.length/skips.length*100).toFixed(1)}%)`);
console.log(`  → Skip MISSED move >1.5ATR: ${skipsTradeable.length} (${(skipsTradeable.length/skips.length*100).toFixed(1)}%)`);
console.log(`  → Of those, clean directional move: ${skipsCleanMove.length} (${(skipsCleanMove.length/skips.length*100).toFixed(1)}%)`);

// Top missed clean moves
const topMissed = skipsCleanMove.slice().sort((a,b) => Math.max(b.maxUp, b.maxDn) - Math.max(a.maxUp, a.maxDn)).slice(0, 10);
console.log('\nTop 10 missed clean moves:');
topMissed.forEach(s => {
  const mv = s.direction === 'UP' ? s.maxUp : s.maxDn;
  console.log(`  #${s.idx} ${s.time}: ${s.direction} ${mv.toFixed(2)} (${(mv/s.atr).toFixed(2)}xATR), final ${s.finalMove.toFixed(2)}, conf ${s.conf}`);
});

// Skip wisdom score
let wisdomScore = 0;
skips.forEach(s => {
  if (s.cleanMove) wisdomScore--;  // bad skip — missed opportunity
  else if (!s.tradeable) wisdomScore++;  // good skip — was chop
});
console.log(`\nSkip wisdom score: ${wisdomScore} (positive = good skips dominate, negative = missed opportunities dominate)`);

// ===== B&H BASELINE =====
const firstPrice = moments[0].currentPrice;
const lastIdx = moments[moments.length - 1].idx + SIM;
const lastPrice = bars[Math.min(lastIdx, bars.length - 1)].close;
const bhMove = lastPrice - firstPrice;
console.log('\n═══════════════════════════════════════════════════════════════════════════════');
console.log('BUY & HOLD BASELINE same period');
console.log('═══════════════════════════════════════════════════════════════════════════════');
console.log(`Start: ${firstPrice.toFixed(2)} | End: ${lastPrice.toFixed(2)} | Move: ${bhMove.toFixed(2)}`);

// ===== TRADES DETAIL TABLE =====
console.log('\n═══════════════════════════════════════════════════════════════════════════════');
console.log('TRADES DETAIL (sorted by time)');
console.log('═══════════════════════════════════════════════════════════════════════════════');
trades.forEach(t => {
  console.log(`#${String(t.idx).padStart(3)} ${t.time} ${t.dec.padEnd(5)} conf${t.conf} @${t.entry.toFixed(2)} SL${t.sl.toFixed(2)} TP${t.tp.toFixed(2)} RR=${t.rr.toFixed(2)} → ${t.outcome.padEnd(8)} ${t.pnlR > 0 ? '+' : ''}${t.pnlR.toFixed(2)}R ($${t.pnlUSD.toFixed(2)})`);
});

// Save
fs.writeFileSync(path.join(__dirname, 'opus_full_results.json'), JSON.stringify({ trades, skips }, null, 2));
