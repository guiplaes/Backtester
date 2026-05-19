// Analyze LLM scores vs mechanical outcomes for sweep candidates
const fs = require('fs');
const path = require('path');

const data = JSON.parse(fs.readFileSync(path.join(__dirname, 'sweep_all.json'), 'utf8'));
const cands = data.candidates;

// Scores from Agent calls
const scores = [
  7,4,6,6,7,6,4,6,4,6,  // 00-09
  6,4,4,4,6,4,3,4,4,6,  // 10-19
  6,6,7,4,4,6,6,4,7,6,  // 20-29
  6,6                    // 30-31
];

const enriched = cands.map((c, i) => ({ ...c, score: scores[i] }));

function stats(trades, label) {
  const n = trades.length;
  if (n === 0) return { label, n: 0 };
  const wins = trades.filter(t => t.pnlR > 0);
  const gw = wins.reduce((s,t)=>s+t.pnlR,0);
  const gl = Math.abs(trades.filter(t=>t.pnlR<0).reduce((s,t)=>s+t.pnlR,0));
  const total = trades.reduce((s,t)=>s+t.pnlR,0);
  return { label, n, wins: wins.length, wr:+(wins.length/n*100).toFixed(1), pf: gl>0?+(gw/gl).toFixed(2):999, net:+total.toFixed(2), exp:+(total/n).toFixed(3) };
}

console.log('\n=== LIQUIDITY SWEEP — ALL DATA ===\n');
console.log('Mechanical baseline:', JSON.stringify(stats(enriched, 'all')));

console.log('\n=== Threshold Sweep ===');
console.log('Threshold | n  | WR%  | PF   | Net R | Exp R');
for (let t = 3; t <= 8; t++) {
  const f = enriched.filter(x => x.score >= t);
  const s = stats(f, `>=${t}`);
  console.log(`>= ${t}      | ${String(s.n).padStart(2)} | ${String(s.wr).padStart(4)} | ${String(s.pf).padStart(4)} | ${String(s.net).padStart(5)} | ${String(s.exp).padStart(5)}`);
}

console.log('\n=== Score → outcome distribution ===');
const byScore = {};
enriched.forEach(t => { byScore[t.score] = byScore[t.score] || []; byScore[t.score].push(t.pnlR); });
Object.keys(byScore).sort().forEach(s => {
  const arr = byScore[s];
  const wins = arr.filter(x => x > 0).length;
  const sum = arr.reduce((a,b)=>a+b,0);
  console.log(`  Score ${s}: ${arr.length}t, ${wins} wins (${(wins/arr.length*100).toFixed(0)}%), net ${sum.toFixed(2)}R`);
});

console.log('\n=== Per TF breakdown ===');
['M5','M15','H1'].forEach(tf => {
  console.log(`${tf}:`, JSON.stringify(stats(enriched.filter(t => t.tf === tf), tf)));
});

console.log('\n=== Confusion matrix at score >= 6 ===');
const threshold = 6;
const tp = enriched.filter(r => r.score >= threshold && r.pnlR > 0).length;
const fp = enriched.filter(r => r.score >= threshold && r.pnlR <= 0).length;
const tn = enriched.filter(r => r.score < threshold && r.pnlR <= 0).length;
const fn = enriched.filter(r => r.score < threshold && r.pnlR > 0).length;
console.log(`  Kept winners: ${tp} | Kept losers: ${fp} | Avoided losers: ${tn} | Missed winners: ${fn}`);
console.log(`  Precision: ${tp+fp>0?(tp/(tp+fp)*100).toFixed(1):0}% | Recall: ${tp+fn>0?(tp/(tp+fn)*100).toFixed(1):0}%`);
