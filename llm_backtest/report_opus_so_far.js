// Report detailed metrics for Opus trades so far
const fs = require('fs');
const path = require('path');

const meta = JSON.parse(fs.readFileSync(path.join(__dirname, 'rich_meta.json'), 'utf8'));
const { moments, bars } = meta;

// Opus decisions (first 50 of 126)
const opusDecisions = {
  0:  { dec: 'NO_TRADE', conf: 3 },
  1:  { dec: 'LONG', e: 4551.32, sl: 4541.50, tp: 4570.00, conf: 7 },
  2:  { dec: 'NO_TRADE', conf: 3 }, 3:  { dec: 'NO_TRADE', conf: 3 },
  4:  { dec: 'NO_TRADE', conf: 3 }, 5:  { dec: 'NO_TRADE', conf: 3 },
  6:  { dec: 'NO_TRADE', conf: 3 }, 7:  { dec: 'NO_TRADE', conf: 3 },
  8:  { dec: 'NO_TRADE', conf: 3 }, 9:  { dec: 'NO_TRADE', conf: 3 },
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
};

const SIM = 8;  // forward bars
const ACCOUNT = 10000;  // hypothetical account
const RISK_PCT = 0.5;   // 0.5% per trade
const PIP_VALUE_XAUUSD = 1;  // $1 per pip per 0.01 lot (CFD MT5 approximate)

function simulate(d, idx) {
  const m = moments[idx];
  const e = d.e, sl = d.sl, tp = d.tp;
  const rDistPrice = Math.abs(e - sl);
  const tpDistPrice = Math.abs(tp - e);
  const rr = tpDistPrice / rDistPrice;

  // Lot sizing: risk RISK_PCT% of account = max loss in $
  const maxLossUSD = ACCOUNT * RISK_PCT / 100;
  // XAUUSD CFD: 1 lot = 100 oz, so $ per point per lot = 100
  // For risk_dist points, lot = maxLossUSD / (rDistPrice * 100)
  const lots = maxLossUSD / (rDistPrice * 100);

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
  const pnlR = priceMove / rDistPrice;
  const pnlUSD = priceMove * 100 * lots; // 1 point = $100 per lot, but we have `lots`
  const pnlPct = pnlUSD / ACCOUNT * 100;

  return { entry: e, sl, tp, rr, lots, rDistPrice, maxLossUSD, outcome, exitPrice, exitTime, barsHeld, pnlR, pnlUSD, pnlPct };
}

console.log('═══════════════════════════════════════════════════════════════════════════════');
console.log('OPUS DISCRETIONARY TRADER — BATCH 1 (50 moments)');
console.log('═══════════════════════════════════════════════════════════════════════════════');
console.log(`Account: $${ACCOUNT} | Risk per trade: ${RISK_PCT}% = $${ACCOUNT*RISK_PCT/100}`);
console.log(`Forward simulation: ${SIM} M15 bars (2 hours)`);
console.log('');

const trades = [];
const skips = [];
for (let i = 0; i < 50; i++) {
  const d = opusDecisions[i];
  if (d.dec === 'NO_TRADE') { skips.push({ idx: i, conf: d.conf, time: moments[i].timeStr }); continue; }
  const sim = simulate(d, i);
  trades.push({ idx: i, time: moments[i].timeStr, dec: d.dec, conf: d.conf, ...sim });
}

console.log(`Decisions: ${trades.length} TRADES | ${skips.length} NO_TRADES`);
console.log(`Trade rate: ${(trades.length/50*100).toFixed(1)}%\n`);

if (trades.length > 0) {
  console.log('═══════════════════════════════════════════════════════════════════════════════');
  console.log('TRADES — DETAILED REPORT');
  console.log('═══════════════════════════════════════════════════════════════════════════════');
  trades.forEach((t, i) => {
    console.log(`\n┌─ Trade #${i+1} (window ${t.idx}) ${'─'.repeat(50)}`);
    console.log(`│ Time entry:     ${t.time}`);
    console.log(`│ Time exit:      ${new Date(t.exitTime*1000).toISOString()}`);
    console.log(`│ Duration:       ${t.barsHeld} M15 bars (${t.barsHeld*15} min)`);
    console.log(`│ Direction:      ${t.dec}`);
    console.log(`│ Confidence:     ${t.conf}/10`);
    console.log(`│ Entry price:    ${t.entry}`);
    console.log(`│ SL price:       ${t.sl} (${t.rDistPrice.toFixed(2)} points away)`);
    console.log(`│ TP price:       ${t.tp} (${(t.tp - t.entry).toFixed(2)} points)`);
    console.log(`│ R:R ratio:      ${t.rr.toFixed(2)}`);
    console.log(`│ Lots:           ${t.lots.toFixed(4)} (XAUUSD)`);
    console.log(`│ Max loss if SL: $${t.maxLossUSD}`);
    console.log(`│ Exit price:     ${t.exitPrice.toFixed(2)}`);
    console.log(`│ Outcome:        ${t.outcome}`);
    console.log(`│ Price moved:    ${(t.dec==='LONG'?t.exitPrice-t.entry:t.entry-t.exitPrice).toFixed(2)} pts`);
    console.log(`│ PnL in R:       ${t.pnlR > 0 ? '+' : ''}${t.pnlR.toFixed(2)}R`);
    console.log(`│ PnL in USD:     ${t.pnlUSD > 0 ? '+' : ''}$${t.pnlUSD.toFixed(2)}`);
    console.log(`│ PnL %:          ${t.pnlPct > 0 ? '+' : ''}${t.pnlPct.toFixed(3)}%`);
    console.log(`└${'─'.repeat(70)}`);
  });

  // Aggregate
  const wins = trades.filter(t => t.pnlR > 0);
  const losses = trades.filter(t => t.pnlR < 0);
  const totalR = trades.reduce((s,t)=>s+t.pnlR, 0);
  const totalUSD = trades.reduce((s,t)=>s+t.pnlUSD, 0);
  console.log('\n═══════════════════════════════════════════════════════════════════════════════');
  console.log('AGGREGATE STATS');
  console.log('═══════════════════════════════════════════════════════════════════════════════');
  console.log(`Total trades:    ${trades.length}`);
  console.log(`Wins:            ${wins.length} (${(wins.length/trades.length*100).toFixed(1)}%)`);
  console.log(`Losses:          ${losses.length}`);
  console.log(`Total R:         ${totalR > 0 ? '+' : ''}${totalR.toFixed(2)}R`);
  console.log(`Total USD:       ${totalUSD > 0 ? '+' : ''}$${totalUSD.toFixed(2)}`);
  console.log(`Total %:         ${(totalUSD/ACCOUNT*100).toFixed(3)}%`);
} else {
  console.log('\nNo trades to report.');
}

console.log('\n═══════════════════════════════════════════════════════════════════════════════');
console.log(`SKIPS (${skips.length}) — by confidence`);
console.log('═══════════════════════════════════════════════════════════════════════════════');
const confDist = {};
skips.forEach(s => { confDist[s.conf] = (confDist[s.conf]||0) + 1; });
Object.keys(confDist).sort().forEach(c => console.log(`  Confidence ${c}: ${confDist[c]} skips`));
