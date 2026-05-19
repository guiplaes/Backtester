// Threshold sweep over combined v1+v2 datasets with LLM confidence scores
const fs = require('fs');
const path = require('path');

const v1 = JSON.parse(fs.readFileSync(path.join(__dirname, 'results_mechanical.json'), 'utf8'));
const v2 = JSON.parse(fs.readFileSync(path.join(__dirname, 'results_mechanical_v2.json'), 'utf8'));

// Scores from Agent calls (v1 0-29, v2 0-14)
const v1Scores = [6,6,6,5,5,6,6,4,7,6, 7,6,5,6,7,7,6,4,6,4, 4,6,5,4,6,6,7,6,6,6];
const v2Scores = [6,7,4,6,4,5,6,5,5,5, 4,4,6,4,6];

const v1Enriched = v1.map((t, i) => ({ ...t, score: v1Scores[i], dataset: 'v1' }));
const v2Enriched = v2.map((t, i) => ({ ...t, score: v2Scores[i], dataset: 'v2' }));
const all = [...v1Enriched, ...v2Enriched];

function stats(trades, label) {
  const n = trades.length;
  if (n === 0) return { label, n: 0 };
  const wins = trades.filter(t => t.pnlR > 0);
  const losses = trades.filter(t => t.pnlR < 0);
  const gw = wins.reduce((s,t)=>s+t.pnlR,0);
  const gl = Math.abs(losses.reduce((s,t)=>s+t.pnlR,0));
  const total = trades.reduce((s,t)=>s+t.pnlR,0);
  return {
    label, n,
    wins: wins.length,
    wr: +(wins.length/n*100).toFixed(1),
    pf: gl > 0 ? +(gw/gl).toFixed(2) : 999,
    net: +total.toFixed(2),
    exp: +(total/n).toFixed(3),
  };
}

console.log('\n=== COMBINED DATASET (v1 + v2 = 45 trades) ===\n');
console.log('Mechanical baseline:', JSON.stringify(stats(all, 'all')));
console.log('\n=== Threshold Sweep ===');
console.log('Threshold | Filter | n | WR% | PF | Net R | Exp R');
console.log('----------+--------+---+-----+----+-------+------');
for (let t = 3; t <= 8; t++) {
  const filtered = all.filter(x => x.score >= t);
  const s = stats(filtered, `score>=${t}`);
  console.log(`>= ${t}      | ACT    | ${String(s.n).padStart(2)} | ${String(s.wr).padStart(4)} | ${String(s.pf).padStart(4)} | ${String(s.net).padStart(5)} | ${String(s.exp).padStart(5)}`);
}

console.log('\n=== By dataset ===');
console.log('v1 (M5, weak filter):', JSON.stringify(stats(v1Enriched, 'v1')));
console.log('v2 (M15, strong filter):', JSON.stringify(stats(v2Enriched, 'v2')));

console.log('\n=== Score → outcome distribution ===');
const byScore = {};
all.forEach(t => {
  byScore[t.score] = byScore[t.score] || [];
  byScore[t.score].push(t.pnlR);
});
Object.keys(byScore).sort().forEach(s => {
  const arr = byScore[s];
  const wins = arr.filter(x => x > 0).length;
  const sum = arr.reduce((a,b)=>a+b,0);
  console.log(`  Score ${s}: ${arr.length}t, ${wins} wins (${(wins/arr.length*100).toFixed(0)}%), net ${sum.toFixed(2)}R`);
});
