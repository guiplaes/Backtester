// Combined sweep detector on all 3 datasets with relaxed wick threshold
const fs = require('fs');
const path = require('path');

const datasets = [
  { name: 'M5', file: 'xauusd_m5.json', tf: 'M5' },
  { name: 'M15', file: 'xauusd_m15.json', tf: 'M15' },
  { name: 'H1', file: 'xauusd_h1.json', tf: 'H1' },
];

function atr(bars, len) { const tr=bars.map((b,i)=>i===0?b.high-b.low:Math.max(b.high-b.low,Math.abs(b.high-bars[i-1].close),Math.abs(b.low-bars[i-1].close))); const out=[]; let p=tr.slice(0,len).reduce((a,b)=>a+b,0)/len; for (let i=0;i<tr.length;i++) { if (i<len-1){out.push(NaN);continue;} if (i===len-1){out.push(p);continue;} p=(p*(len-1)+tr[i])/len; out.push(p); } return out; }
function rsi(arr, len) { const out=[]; let avgGain=0,avgLoss=0; for (let i=0;i<arr.length;i++) { if (i===0){out.push(NaN);continue;} const ch=arr[i]-arr[i-1]; const g=Math.max(ch,0),l=Math.max(-ch,0); if (i<=len){avgGain+=g/len;avgLoss+=l/len;out.push(i<len?NaN:100-100/(1+avgGain/Math.max(avgLoss,1e-10)));} else {avgGain=(avgGain*(len-1)+g)/len; avgLoss=(avgLoss*(len-1)+l)/len; out.push(100-100/(1+avgGain/Math.max(avgLoss,1e-10)));} } return out; }

const SWING_LOOKBACK = 15;
const WICK_MIN_ATR = 0.1;  // very relaxed
const all = [];

datasets.forEach(ds => {
  const data = JSON.parse(fs.readFileSync(path.join(__dirname, ds.file), 'utf8'));
  const bars = data.bars;
  const atr14 = atr(bars, 14);
  const rsi14 = rsi(bars.map(b=>b.close), 14);
  let dsCount = 0;

  for (let i = SWING_LOOKBACK; i < bars.length - 12; i++) {
    const b = bars[i];
    const a = atr14[i];
    if (isNaN(a)) continue;

    let swingHigh = -Infinity, swingLow = Infinity;
    for (let j = i - SWING_LOOKBACK; j < i; j++) {
      if (bars[j].high > swingHigh) swingHigh = bars[j].high;
      if (bars[j].low < swingLow) swingLow = bars[j].low;
    }

    const date = new Date(b.time * 1000);
    const hourUTC = date.getUTCHours();
    if (hourUTC < 6 || hourUTC >= 20) continue;

    const wickAbove = b.high - swingHigh;
    const shortSetup = b.high > swingHigh && b.close < swingHigh && wickAbove > a * WICK_MIN_ATR && b.close < b.open;

    const wickBelow = swingLow - b.low;
    const longSetup = b.low < swingLow && b.close > swingLow && wickBelow > a * WICK_MIN_ATR && b.close > b.open;

    if (longSetup || shortSetup) {
      const c = {
        tf: ds.tf, idx: i, time: b.time, timeStr: date.toISOString(), hourUTC,
        side: longSetup ? 'LONG' : 'SHORT',
        entry: b.close,
        swingLevel: longSetup ? swingLow : swingHigh,
        wickSize: longSetup ? wickBelow : wickAbove,
        atr14: a, rsi14: rsi14[i],
        bars, // reference
      };
      // Simulate
      const sl = longSetup ? c.swingLevel - a * 0.1 : c.swingLevel + a * 0.1;
      const tp = longSetup ? c.entry + (c.entry - sl) * 2 : c.entry - (sl - c.entry) * 2;
      const r_dist = Math.abs(c.entry - sl);
      let result = { outcome: 'TIMEOUT', pnlR: 0 };
      for (let j = i + 1; j <= i + 12 && j < bars.length; j++) {
        const bb = bars[j];
        if (longSetup) {
          if (bb.low <= sl) { result = { outcome: 'SL', pnlR: -1 }; break; }
          if (bb.high >= tp) { result = { outcome: 'TP', pnlR: 2 }; break; }
        } else {
          if (bb.high >= sl) { result = { outcome: 'SL', pnlR: -1 }; break; }
          if (bb.low <= tp) { result = { outcome: 'TP', pnlR: 2 }; break; }
        }
      }
      if (result.outcome === 'TIMEOUT') {
        const last = bars[Math.min(i + 12, bars.length - 1)];
        const mv = longSetup ? (last.close - c.entry) : (c.entry - last.close);
        result.pnlR = mv / r_dist;
      }
      all.push({ ...c, sl, tp, ...result });
      dsCount++;
    }
  }
  console.log(`${ds.name}: ${dsCount} candidates`);
});

console.log(`\nTotal candidates: ${all.length}`);
const wins = all.filter(t => t.pnlR > 0);
const gw = wins.reduce((s,t)=>s+t.pnlR,0);
const gl = Math.abs(all.filter(t=>t.pnlR<0).reduce((s,t)=>s+t.pnlR,0));
const total = all.reduce((s,t)=>s+t.pnlR,0);
console.log(`Wins: ${wins.length} (${(wins.length/all.length*100).toFixed(1)}%) | Net: ${total.toFixed(2)}R | PF: ${gl>0?(gw/gl).toFixed(2):'∞'}`);

// Save without bars circular reference
const serializable = all.map(({bars, ...rest}) => rest);
fs.writeFileSync(path.join(__dirname, 'sweep_all.json'), JSON.stringify({ candidates: serializable }, null, 2));

// Also build context files for LLM
const ctxDir = path.join(__dirname, 'contexts_sweep');
if (!fs.existsSync(ctxDir)) fs.mkdirSync(ctxDir);

all.forEach((c, idx) => {
  const lookback = 20;
  const recent = [];
  for (let i = Math.max(0, c.idx - lookback + 1); i <= c.idx; i++) {
    const b = c.bars[i];
    const d = new Date(b.time * 1000);
    const hh = String(d.getUTCHours()).padStart(2,'0');
    const mm = String(d.getUTCMinutes()).padStart(2,'0');
    const dd = String(d.getUTCDate()).padStart(2,'0');
    recent.push(`${dd} ${hh}:${mm} O=${b.open.toFixed(2)} H=${b.high.toFixed(2)} L=${b.low.toFixed(2)} C=${b.close.toFixed(2)}`);
  }
  const ctx = `XAUUSD ${c.tf} — LIQUIDITY SWEEP setup
Date/Time UTC: ${c.timeStr}
Direction: ${c.side}
Entry (close): ${c.entry.toFixed(2)}
Swept level (${c.side === 'LONG' ? 'swing LOW' : 'swing HIGH'}): ${c.swingLevel.toFixed(2)}
Wick size: ${c.wickSize.toFixed(2)} (${(c.wickSize/c.atr14).toFixed(2)}×ATR)
ATR(14): ${c.atr14.toFixed(2)}
RSI(14): ${c.rsi14.toFixed(1)}

LAST 20 BARS (oldest → current):
  ${recent.join('\n  ')}

THESIS: This is a potential liquidity sweep — price wicked beyond a recent swing extreme (${c.side === 'LONG' ? 'low' : 'high'}) then closed back inside, suggesting a stop hunt followed by reversal.

YOUR TASK:
Evaluate ONLY whether this is a CLEAN, HIGH-QUALITY liquidity sweep with reversal potential. Consider:
- Was the sweep aggressive/fast (single bar wick) or slow erosion?
- Did the bar close FAR enough back inside (rejection strength)?
- Is there context support (RSI extreme, momentum exhaustion before sweep)?
- Or is this likely the START of a continuation (trend, momentum, no exhaustion)?

Use ONLY data above. NO future info. NO other tools.

Reply EXACTLY 2 lines:
SCORE: <1-10> (10 = textbook clean sweep, 1 = likely continuation)
REASON: <one sentence, max 25 words>`;
  fs.writeFileSync(path.join(ctxDir, `cand_${String(idx).padStart(2,'0')}_${c.tf}_${c.side}.txt`), ctx);
});

console.log(`\nBuilt ${all.length} context files in contexts_sweep/`);
console.log('\nDetail:');
all.forEach((r, i) => console.log(`  ${String(i).padStart(2)}: ${r.tf.padEnd(3)} ${r.timeStr} ${r.side.padEnd(5)} @${r.entry.toFixed(2)} → ${r.outcome.padEnd(8)} (${r.pnlR.toFixed(2)}R)`));
