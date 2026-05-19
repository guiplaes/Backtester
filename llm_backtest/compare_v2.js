const fs = require('fs');
const path = require('path');

const mechanical = JSON.parse(fs.readFileSync(path.join(__dirname, 'results_mechanical_v2.json'), 'utf8'));

const llm = {
  0:'SKIP', 1:'SKIP', 2:'SKIP', 3:'SKIP', 4:'SKIP',
  5:'SKIP', 6:'SKIP', 7:'SKIP', 8:'SKIP', 9:'SKIP',
  10:'SKIP', 11:'SKIP', 12:'ACT', 13:'SKIP', 14:'ACT',
};

const enriched = mechanical.map((m, i) => ({ ...m, llm: llm[i] }));

function stats(trades, label) {
  const n = trades.length;
  if (n === 0) { console.log(`\n=== ${label} ===\n(empty)`); return; }
  const wins = trades.filter(t => t.pnlR > 0);
  const losses = trades.filter(t => t.pnlR < 0);
  const gw = wins.reduce((s,t)=>s+t.pnlR,0);
  const gl = Math.abs(losses.reduce((s,t)=>s+t.pnlR,0));
  const total = trades.reduce((s,t)=>s+t.pnlR,0);
  console.log(`\n=== ${label} ===`);
  console.log(`  Trades: ${n} | WR: ${(wins.length/n*100).toFixed(1)}% (${wins.length}/${n})`);
  console.log(`  Gross W: +${gw.toFixed(2)}R | Gross L: -${gl.toFixed(2)}R`);
  console.log(`  Net: ${total.toFixed(2)}R | PF: ${gl>0?(gw/gl).toFixed(2):'∞'} | Exp: ${(total/n).toFixed(3)}R/t`);
}

stats(enriched, 'MECHANICAL (take all 15)');
stats(enriched.filter(t => t.llm === 'ACT'), 'LLM-FILTERED (only ACT)');
stats(enriched.filter(t => t.llm === 'SKIP'), 'LLM-SKIPPED (what avoided)');

console.log('\n=== Matrix ===');
console.log('idx | side  | outcome  | pnlR  | LLM   | result');
console.log('----+-------+----------+-------+-------+-------');
enriched.forEach((r,i) => {
  const tookGood = r.llm === 'ACT' && r.pnlR > 0;
  const tookBad  = r.llm === 'ACT' && r.pnlR <= 0;
  const skipGood = r.llm === 'SKIP' && r.pnlR <= 0;
  const skipBad  = r.llm === 'SKIP' && r.pnlR > 0;
  const verdict = tookGood ? '✓ KEPT WINNER' : tookBad ? '✗ KEPT LOSER' : skipGood ? '✓ AVOIDED LOSER' : '✗ SKIPPED WINNER';
  console.log(`${String(i).padStart(2)}  | ${r.side.padEnd(5)} | ${r.outcome.padEnd(8)} | ${r.pnlR.toFixed(2).padStart(5)} | ${r.llm.padEnd(4)}  | ${verdict}`);
});

const tp = enriched.filter(r => r.llm === 'ACT' && r.pnlR > 0).length;
const fp = enriched.filter(r => r.llm === 'ACT' && r.pnlR <= 0).length;
const tn = enriched.filter(r => r.llm === 'SKIP' && r.pnlR <= 0).length;
const fn = enriched.filter(r => r.llm === 'SKIP' && r.pnlR > 0).length;
console.log('\n=== Confusion ===');
console.log(`  Kept winners: ${tp} | Kept losers: ${fp} | Avoided losers: ${tn} | Missed winners: ${fn}`);
console.log(`  Precision (winners among ACT): ${tp+fp>0?(tp/(tp+fp)*100).toFixed(1):0}%`);
console.log(`  Recall (winners caught): ${tp+fn>0?(tp/(tp+fn)*100).toFixed(1):0}%`);
