// Detect "Moments of Interest" using 5-criteria score
const fs = require('fs');
const path = require('path');

const data = JSON.parse(fs.readFileSync(path.join(__dirname, 'xauusd_m15.json'), 'utf8'));
const bars = data.bars;

function ema(arr, len) { const k=2/(len+1); const out=[]; let p=arr[0]; for (let i=0;i<arr.length;i++) { p = i===0?arr[i]:arr[i]*k+p*(1-k); out.push(p); } return out; }
function sma(arr, len) { const out=[]; for (let i=0;i<arr.length;i++) { if (i<len-1) {out.push(NaN);continue;} let s=0; for (let j=i-len+1;j<=i;j++) s+=arr[j]; out.push(s/len); } return out; }
function atr(bars, len) { const tr=bars.map((b,i)=>i===0?b.high-b.low:Math.max(b.high-b.low,Math.abs(b.high-bars[i-1].close),Math.abs(b.low-bars[i-1].close))); const out=[]; let p=tr.slice(0,len).reduce((a,b)=>a+b,0)/len; for (let i=0;i<tr.length;i++) { if (i<len-1){out.push(NaN);continue;} if (i===len-1){out.push(p);continue;} p=(p*(len-1)+tr[i])/len; out.push(p); } return out; }
function rsi(arr, len) { const out=[]; let avgGain=0,avgLoss=0; for (let i=0;i<arr.length;i++) { if (i===0){out.push(NaN);continue;} const ch=arr[i]-arr[i-1]; const g=Math.max(ch,0),l=Math.max(-ch,0); if (i<=len){avgGain+=g/len;avgLoss+=l/len;out.push(i<len?NaN:100-100/(1+avgGain/Math.max(avgLoss,1e-10)));} else {avgGain=(avgGain*(len-1)+g)/len; avgLoss=(avgLoss*(len-1)+l)/len; out.push(100-100/(1+avgGain/Math.max(avgLoss,1e-10)));} } return out; }

const closes = bars.map(b => b.close);
const ema21 = ema(closes, 21);
const sma50 = sma(closes, 50);
const atr14 = atr(bars, 14);
const rsi14 = rsi(closes, 14);

const moments = [];

for (let i = 60; i < bars.length - 10; i++) {
  const b = bars[i];
  const a = atr14[i];
  if (isNaN(a)) continue;

  const date = new Date(b.time * 1000);
  const hourUTC = date.getUTCHours();

  // Score components
  let score = 0;
  const reasons = [];

  // 1. Volatilitat actual
  const range = b.high - b.low;
  if (range > a * 1.0) { score++; reasons.push('vol_expand'); }

  // 2. Alineació trend (EMA21 vs SMA50)
  const aboveBoth = b.close > ema21[i] && b.close > sma50[i];
  const belowBoth = b.close < ema21[i] && b.close < sma50[i];
  if (aboveBoth || belowBoth) { score++; reasons.push('aligned'); }

  // 3. Nivell clau proper (swing high/low últimes 30 barres)
  let sH = -Infinity, sL = Infinity;
  for (let j = i - 30; j < i; j++) {
    if (bars[j].high > sH) sH = bars[j].high;
    if (bars[j].low < sL) sL = bars[j].low;
  }
  const distH = Math.abs(b.close - sH);
  const distL = Math.abs(b.close - sL);
  if (distH < a * 0.7 || distL < a * 0.7) { score++; reasons.push('near_swing'); }

  // 4. Breakout de consolidació (últimes 5 barres compactes, actual trenca)
  let consH = -Infinity, consL = Infinity;
  for (let j = i - 5; j < i; j++) {
    if (bars[j].high > consH) consH = bars[j].high;
    if (bars[j].low < consL) consL = bars[j].low;
  }
  const consRange = consH - consL;
  const breakUp = b.close > consH && consRange < a * 1.5;
  const breakDn = b.close < consL && consRange < a * 1.5;
  if (breakUp || breakDn) { score++; reasons.push('breakout'); }

  // 5. Hora activa
  if ((hourUTC >= 7 && hourUTC < 12) || (hourUTC >= 13 && hourUTC < 17)) {
    score++;
    reasons.push('active_hour');
  }

  // RSI extreme (bonus)
  const rsi = rsi14[i];
  if (rsi > 70 || rsi < 30) { score++; reasons.push('rsi_extreme'); }

  if (score >= 3) {
    moments.push({
      idx: i,
      time: b.time,
      timeStr: date.toISOString(),
      hourUTC,
      currentPrice: b.close,
      score,
      reasons,
      atr14: a,
      rsi14: rsi,
      ema21: ema21[i],
      sma50: sma50[i],
      swingH: sH,
      swingL: sL,
    });
  }
}

console.log(`Total bars: ${bars.length}`);
console.log(`Moments of interest (score >= 3): ${moments.length}`);
console.log(`Trade rate if Agent did them all: ${(moments.length/bars.length*100).toFixed(1)}%`);

// Distribution by score
const scoreDist = {};
moments.forEach(m => { scoreDist[m.score] = (scoreDist[m.score]||0)+1; });
console.log('\nScore distribution:');
Object.keys(scoreDist).sort().forEach(s => console.log(`  Score ${s}: ${scoreDist[s]} moments`));

// Sample
console.log('\nFirst 10 moments:');
moments.slice(0, 10).forEach((m,i) => {
  console.log(`  ${String(i).padStart(2)}: ${m.timeStr} score=${m.score} [${m.reasons.join(',')}]`);
});

fs.writeFileSync(path.join(__dirname, 'interest_moments.json'), JSON.stringify({ moments, bars, indicators: { ema21, sma50, atr14, rsi14 } }, null, 2));
console.log(`\nSaved interest_moments.json with ${moments.length} moments`);
