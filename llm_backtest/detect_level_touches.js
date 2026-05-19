// Detect price touches of institutional levels during active sessions
const fs = require('fs');
const path = require('path');

const data = JSON.parse(fs.readFileSync(path.join(__dirname, 'xauusd_m15.json'), 'utf8'));
const bars = data.bars;

// === Indicators ===
function atr(bars, len) {
  const tr = bars.map((b, i) => i === 0 ? b.high - b.low : Math.max(b.high - b.low, Math.abs(b.high - bars[i-1].close), Math.abs(b.low - bars[i-1].close)));
  const out = []; let p = tr.slice(0, len).reduce((a,b)=>a+b,0)/len;
  for (let i = 0; i < tr.length; i++) {
    if (i < len-1) { out.push(NaN); continue; }
    if (i === len-1) { out.push(p); continue; }
    p = (p*(len-1) + tr[i])/len; out.push(p);
  }
  return out;
}
function ema(arr, len) { const k=2/(len+1); const out=[]; let p=arr[0]; for (let i=0;i<arr.length;i++) { p=i===0?arr[i]:arr[i]*k+p*(1-k); out.push(p); } return out; }
function rsi(arr, len) {
  const out=[]; let avgGain=0, avgLoss=0;
  for (let i=0;i<arr.length;i++) {
    if (i===0) { out.push(NaN); continue; }
    const ch = arr[i]-arr[i-1]; const g = Math.max(ch,0), l = Math.max(-ch,0);
    if (i<=len) { avgGain+=g/len; avgLoss+=l/len; out.push(i<len?NaN:100-100/(1+avgGain/Math.max(avgLoss,1e-10))); }
    else { avgGain=(avgGain*(len-1)+g)/len; avgLoss=(avgLoss*(len-1)+l)/len; out.push(100-100/(1+avgGain/Math.max(avgLoss,1e-10))); }
  }
  return out;
}

const closes = bars.map(b => b.close);
const atr14 = atr(bars, 14);
const ema21 = ema(closes, 21);
const rsi14 = rsi(closes, 14);

// === Build level map per day ===
// PDH/PDL for each day, swing highs/lows from prior 3 days, round $50 levels
function getLevelsAt(barIdx) {
  const cur = bars[barIdx];
  const curDay = new Date(cur.time*1000).getUTCDate();
  const curPrice = cur.close;

  // PDH/PDL: scan back, find most recent prior day
  let pdh = -Infinity, pdl = Infinity, pdDay = null;
  for (let j = barIdx - 1; j >= 0; j--) {
    const d = new Date(bars[j].time*1000).getUTCDate();
    if (d === curDay) continue;
    if (pdDay === null) pdDay = d;
    if (d !== pdDay) break;
    if (bars[j].high > pdh) pdh = bars[j].high;
    if (bars[j].low < pdl) pdl = bars[j].low;
  }

  // Prior-prior day H/L (2 days back)
  let ppdh = -Infinity, ppdl = Infinity, ppdDay = null, passedFirst = false;
  for (let j = barIdx - 1; j >= 0; j--) {
    const d = new Date(bars[j].time*1000).getUTCDate();
    if (d === curDay) continue;
    if (!passedFirst) {
      if (pdDay === null || d === pdDay) { if (pdDay === null) pdDay = d; continue; }
      passedFirst = true;
    }
    if (ppdDay === null) ppdDay = d;
    if (d !== ppdDay) break;
    if (bars[j].high > ppdh) ppdh = bars[j].high;
    if (bars[j].low < ppdl) ppdl = bars[j].low;
  }

  // Today's session H/L (so far)
  let tdh = -Infinity, tdl = Infinity;
  for (let j = barIdx - 1; j >= 0; j--) {
    const d = new Date(bars[j].time*1000).getUTCDate();
    if (d !== curDay) break;
    if (bars[j].high > tdh) tdh = bars[j].high;
    if (bars[j].low < tdl) tdl = bars[j].low;
  }

  // Round $50 levels near price
  const round50Above = Math.ceil(curPrice / 50) * 50;
  const round50Below = Math.floor(curPrice / 50) * 50;

  // Recent untested swings: pivot high/low in last 60 bars
  const swings = [];
  for (let j = Math.max(3, barIdx - 60); j < barIdx - 2; j++) {
    const h = bars[j].high, l = bars[j].low;
    const isHigh = bars[j-1].high < h && bars[j-2].high < h && bars[j+1].high < h && bars[j+2].high < h;
    const isLow = bars[j-1].low > l && bars[j-2].low > l && bars[j+1].low > l && bars[j+2].low > l;
    if (isHigh) swings.push({ type: 'swingH', price: h, idx: j });
    if (isLow) swings.push({ type: 'swingL', price: l, idx: j });
  }

  // v2: dropped TDH/TDL (intraday, weakest — empirically 14% WR in v1)
  const levels = [
    pdh > 0 ? { name: 'PDH', price: pdh, type: 'resistance' } : null,
    pdl < Infinity ? { name: 'PDL', price: pdl, type: 'support' } : null,
    ppdh > 0 ? { name: 'PPDH', price: ppdh, type: 'resistance' } : null,
    ppdl < Infinity ? { name: 'PPDL', price: ppdl, type: 'support' } : null,
    { name: `R50_${round50Above}`, price: round50Above, type: 'resistance' },
    { name: `R50_${round50Below}`, price: round50Below, type: 'support' },
  ].filter(Boolean);

  // Add unique swings (dedupe within 0.3×ATR)
  const a = atr14[barIdx] || 5;
  swings.forEach(s => {
    const dup = levels.some(l => Math.abs(l.price - s.price) < a * 0.3);
    if (!dup) levels.push({ name: s.type === 'swingH' ? 'PrSwingH' : 'PrSwingL', price: s.price, type: s.type === 'swingH' ? 'resistance' : 'support' });
  });

  return levels;
}

// === Touch detection ===
const touches = [];
const PROX = 0.35; // touch = within 0.35×ATR
const lastTouchByLevel = {};

for (let i = 60; i < bars.length - 10; i++) {
  const b = bars[i];
  const a = atr14[i];
  if (isNaN(a)) continue;

  const date = new Date(b.time*1000);
  const hr = date.getUTCHours();

  // Active sessions only: London (7-12 UTC), NY (13-17 UTC), Overlap (12-13). Skip Asia (0-6) and late (21-23).
  if (hr < 7 || hr >= 17) continue;

  const levels = getLevelsAt(i);

  // Strong levels only (drop micro PrSwing unless confluent)
  const strong = levels.filter(l => !l.name.startsWith('PrSwing'));
  const weak = levels.filter(l => l.name.startsWith('PrSwing'));

  for (const lvl of strong) {
    const dist = Math.abs(b.close - lvl.price);
    const wickReached = (lvl.type === 'resistance' && b.high >= lvl.price - a * PROX) ||
                        (lvl.type === 'support' && b.low <= lvl.price + a * PROX);
    if (!wickReached) continue;
    if (dist > a * PROX) continue;

    const key = lvl.name + '_' + lvl.price.toFixed(1);
    if (lastTouchByLevel[key] !== undefined && i - lastTouchByLevel[key] < 8) continue;
    lastTouchByLevel[key] = i;

    // Confluence: any other strong/weak level within 0.5×ATR
    const conf = [...strong, ...weak].filter(l2 => l2 !== lvl && Math.abs(l2.price - lvl.price) < a * 0.5);

    touches.push({
      idx: i, time: b.time, timeStr: date.toISOString(), hourUTC: hr,
      level: lvl, confluence: conf, levelDist: lvl.price - b.close,
      currentPrice: b.close, atr14: a, rsi14: rsi14[i], ema21: ema21[i],
    });
  }
}

console.log(`Bars: ${bars.length}`);
console.log(`Level touches detected: ${touches.length}`);
const byLevel = {};
touches.forEach(t => { const n = t.level.name.replace(/_\d+$/, '_RND'); byLevel[n] = (byLevel[n]||0)+1; });
Object.keys(byLevel).sort().forEach(n => console.log(`  ${n}: ${byLevel[n]}`));

console.log('\nFirst 10:');
touches.slice(0, 10).forEach((t,i) => {
  console.log(`  ${String(i).padStart(2)}: ${t.timeStr} @${t.currentPrice.toFixed(2)} → ${t.level.name} ${t.level.price.toFixed(2)} (${t.level.type})`);
});

fs.writeFileSync(path.join(__dirname, 'level_touches.json'), JSON.stringify({ touches, bars, atr14, ema21, rsi14 }));
