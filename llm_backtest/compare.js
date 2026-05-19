// Compare mechanical baseline vs LLM-filtered results
const fs = require('fs');
const path = require('path');

const mechanical = JSON.parse(fs.readFileSync(path.join(__dirname, 'results_mechanical.json'), 'utf8'));

// LLM decisions (from Agent runs, manually compiled)
const llmDecisions = {
  0:  'SKIP', 1:  'ACT',  2:  'ACT',  3:  'SKIP', 4:  'SKIP',
  5:  'SKIP', 6:  'SKIP', 7:  'SKIP', 8:  'ACT',  9:  'SKIP',
  10: 'ACT',  11: 'SKIP', 12: 'SKIP', 13: 'ACT',  14: 'ACT',
  15: 'SKIP', 16: 'ACT',  17: 'SKIP', 18: 'SKIP', 19: 'SKIP',
  20: 'SKIP', 21: 'SKIP', 22: 'SKIP', 23: 'SKIP', 24: 'SKIP',
  25: 'ACT',  26: 'ACT',  27: 'ACT',  28: 'ACT',  29: 'ACT',
};

const enriched = mechanical.map((m, i) => ({ ...m, llm: llmDecisions[i] }));

function stats(trades, label) {
  const n = trades.length;
  if (n === 0) return console.log(`\n=== ${label} ===\n(empty)`);
  const wins = trades.filter(t => t.pnlR > 0);
  const losses = trades.filter(t => t.pnlR < 0);
  const grossWin = wins.reduce((s, t) => s + t.pnlR, 0);
  const grossLoss = Math.abs(losses.reduce((s, t) => s + t.pnlR, 0));
  const totalR = trades.reduce((s, t) => s + t.pnlR, 0);
  console.log(`\n=== ${label} ===`);
  console.log(`  Trades:    ${n}`);
  console.log(`  Wins:      ${wins.length} (${(wins.length/n*100).toFixed(1)}%)`);
  console.log(`  Losses:    ${losses.length}`);
  console.log(`  Gross win: +${grossWin.toFixed(2)}R`);
  console.log(`  Gross loss: -${grossLoss.toFixed(2)}R`);
  console.log(`  Net total: ${totalR.toFixed(2)}R`);
  console.log(`  PF:        ${grossLoss > 0 ? (grossWin/grossLoss).toFixed(2) : '∞'}`);
  console.log(`  Expectancy: ${(totalR/n).toFixed(3)}R/trade`);
}

stats(enriched, 'MECHANICAL (take all 30)');
stats(enriched.filter(t => t.llm === 'ACT'), 'LLM-FILTERED (only ACT)');
stats(enriched.filter(t => t.llm === 'SKIP'), 'LLM-SKIPPED (what we avoided)');

console.log('\n=== Decision Matrix ===');
console.log('idx | side  | outcome  | pnlR  | LLM   | result');
console.log('----+-------+----------+-------+-------+-------');
enriched.forEach((r, i) => {
  const llmTook = r.llm === 'ACT';
  const goodTrade = r.pnlR > 0;
  let verdict;
  if (llmTook && goodTrade) verdict = '✓ KEPT WINNER';
  else if (llmTook && !goodTrade) verdict = '✗ KEPT LOSER';
  else if (!llmTook && goodTrade) verdict = '✗ SKIPPED WINNER';
  else verdict = '✓ AVOIDED LOSER';
  console.log(`${String(i).padStart(2,' ')}  | ${r.side.padEnd(5,' ')} | ${r.outcome.padEnd(8,' ')} | ${r.pnlR.toFixed(2).padStart(5,' ')} | ${r.llm.padEnd(4,' ')}  | ${verdict}`);
});

console.log('\n=== Confusion matrix ===');
const tp = enriched.filter(r => r.llm === 'ACT' && r.pnlR > 0).length;  // True positive (kept winner)
const fp = enriched.filter(r => r.llm === 'ACT' && r.pnlR <= 0).length; // False positive (kept loser)
const tn = enriched.filter(r => r.llm === 'SKIP' && r.pnlR <= 0).length; // True negative (avoided loser)
const fn = enriched.filter(r => r.llm === 'SKIP' && r.pnlR > 0).length;  // False negative (missed winner)
console.log(`  Kept winners (TP): ${tp}`);
console.log(`  Kept losers (FP):  ${fp}`);
console.log(`  Avoided losers (TN): ${tn}`);
console.log(`  Missed winners (FN): ${fn}`);
console.log(`  Precision (winners among ACT): ${tp+fp>0 ? (tp/(tp+fp)*100).toFixed(1) : 0}%`);
console.log(`  Recall (winners caught): ${tp+fn>0 ? (tp/(tp+fn)*100).toFixed(1) : 0}%`);
