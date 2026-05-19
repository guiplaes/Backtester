// Detect HIGH-QUALITY trade setups (not generic interest moments)
// Three specific patterns: Pullback-in-trend, Failed-breakout, Compression-breakout
const fs = require('fs');
const path = require('path');

const data = JSON.parse(fs.readFileSync(path.join(__dirname, 'xauusd_m15.json'), 'utf8'));
const bars = data.bars;

function ema(arr, len) { const k=2/(len+1); const out=[]; let p=arr[0]; for (let i=0;i<arr.length;i++) { p = i===0?arr[i]:arr[i]*k+p*(1-k); out.push(p); } return out; }
function sma(arr, len) { const out=[]; for (let i=0;i<arr.length;i++) { if (i<len-1) {out.push(NaN);continue;} let s=0; for (let j=i-len+1;j<=i;j++) s+=arr[j]; out.push(s/len); } return out; }
function atr(bars, len) { const tr=bars.map((b,i)=>i===0?b.high-b.low:Math.max(b.high-b.low,Math.abs(b.high-bars[i-1].close),Math.abs(b.low-bars[i-1].close))); const out=[]; let p=tr.slice(0,len).reduce((a,b)=>a+b,0)/len; for (let i=0;i<tr.length;i++) { if (i<len-1){out.push(NaN);continue;} if (i===len-1){out.push(p);continue;} p=(p*(len-1)+tr[i])/len; out.push(p); } return out; }
function rsi(arr, len) { const out=[]; let avgGain=0,avgLoss=0; for (let i=0;i<arr.length;i++) { if (i===0){out.push(NaN);continue;} const ch=arr[i]-arr[i-1]; const g=Math.max(ch,0),l=Math.max(-ch,0); if (i<=len){avgGain+=g/len;avgLoss+=l/len;out.push(i<len?NaN:100-100/(1+avgGain/Math.max(avgLoss,1e-10)));} else {avgGain=(avgGain*(len-1)+g)/len; avgLoss=(avgLoss*(len-1)+l)/len; out.push(100-100/(1+avgGain/Math.max(avgLoss,1e-10)));} } return out; }

const closes = bars.map(b => b.close);
const ema21Arr = ema(closes, 21);
const sma50Arr = sma(closes, 50);
const atr14Arr = atr(bars, 14);
const rsi14Arr = rsi(closes, 14);

const setups = [];

for (let i = 60; i < bars.length - 10; i++) {
  const b = bars[i];
  const a = atr14Arr[i];
  if (isNaN(a)) continue;
  const e21 = ema21Arr[i];
  const s50 = sma50Arr[i];
  const s50_5 = sma50Arr[i-5];
  const r = rsi14Arr[i];

  const date = new Date(b.time * 1000);
  const hourUTC = date.getUTCHours();

  // ===== SETUP A: PULLBACK IN TREND =====
  // SMA50 clear slope + price touched EMA21 + closed back in trend direction
  const sma50_slope_up = s50 > s50_5 + a * 0.10;
  const sma50_slope_dn = s50 < s50_5 - a * 0.10;
  const above_both = b.close > e21 && b.close > s50;
  const below_both = b.close < e21 && b.close < s50;

  // Touch EMA21
  const low_touched_ema = b.low <= e21 + a * 0.3 && b.low >= e21 - a * 0.5;
  const high_touched_ema = b.high >= e21 - a * 0.3 && b.high <= e21 + a * 0.5;

  // Reversal candle
  const body = Math.abs(b.close - b.open);
  const upper_wick = b.high - Math.max(b.close, b.open);
  const lower_wick = Math.min(b.close, b.open) - b.low;

  const long_pullback = sma50_slope_up && above_both && low_touched_ema && b.close > b.open && lower_wick > body * 0.5;
  const short_pullback = sma50_slope_dn && below_both && high_touched_ema && b.close < b.open && upper_wick > body * 0.5;

  // ===== SETUP B: FAILED BREAKOUT =====
  // Range last 5 bars, current breaks but closes back inside
  let rangeH = -Infinity, rangeL = Infinity;
  for (let j = i - 5; j < i; j++) {
    if (bars[j].high > rangeH) rangeH = bars[j].high;
    if (bars[j].low < rangeL) rangeL = bars[j].low;
  }
  const broke_up = b.high > rangeH && b.close < rangeH && b.close < b.open;
  const broke_dn = b.low < rangeL && b.close > rangeL && b.close > b.open;
  const failed_break_short = broke_up;  // we short the failed up-break
  const failed_break_long = broke_dn;   // we long the failed down-break

  // ===== SETUP C: COMPRESSION BREAKOUT =====
  // Low ATR last 5 bars + current >1.5xATR + volume >1.5x avg
  let compRange = 0;
  for (let j = i - 5; j < i; j++) compRange = Math.max(compRange, bars[j].high - bars[j].low);
  const compressed = compRange < a * 0.8;
  const big_bar = (b.high - b.low) > a * 1.3;

  let avgVol = 0;
  for (let j = i - 20; j <= i; j++) avgVol += bars[j].volume;
  avgVol /= 21;
  const volSurge = b.volume > avgVol * 1.5;

  const compression_break_up = compressed && big_bar && volSurge && b.close > b.open && b.close > rangeH;
  const compression_break_dn = compressed && big_bar && volSurge && b.close < b.open && b.close < rangeL;

  // ===== CLASSIFY =====
  let setupType = null;
  let direction = null;
  if (long_pullback) { setupType = 'PULLBACK_TREND'; direction = 'LONG'; }
  else if (short_pullback) { setupType = 'PULLBACK_TREND'; direction = 'SHORT'; }
  else if (failed_break_short) { setupType = 'FAILED_BREAKOUT'; direction = 'SHORT'; }
  else if (failed_break_long) { setupType = 'FAILED_BREAKOUT'; direction = 'LONG'; }
  else if (compression_break_up) { setupType = 'COMPRESSION_BREAKOUT'; direction = 'LONG'; }
  else if (compression_break_dn) { setupType = 'COMPRESSION_BREAKOUT'; direction = 'SHORT'; }

  if (setupType) {
    // Recent swings
    let swingH = -Infinity, swingL = Infinity;
    for (let j = i - 20; j < i; j++) {
      if (bars[j].high > swingH) swingH = bars[j].high;
      if (bars[j].low < swingL) swingL = bars[j].low;
    }
    const rangePos = (b.close - swingL) / (swingH - swingL);

    setups.push({
      idx: i, time: b.time, timeStr: date.toISOString(), hourUTC,
      setupType, direction,
      currentPrice: b.close,
      atr14: a, rsi14: r,
      ema21: e21, sma50: s50, sma50_slope: s50 - s50_5,
      swingH, swingL, rangePos,
      bar: b,
    });
  }
}

console.log(`Total bars: ${bars.length}`);
console.log(`HIGH-QUALITY setups detected: ${setups.length}`);
const byType = {};
setups.forEach(s => { byType[s.setupType] = (byType[s.setupType]||0)+1; });
Object.keys(byType).forEach(t => console.log(`  ${t}: ${byType[t]}`));

console.log(`\nLong: ${setups.filter(s=>s.direction==='LONG').length} | Short: ${setups.filter(s=>s.direction==='SHORT').length}`);

console.log('\nFirst 10:');
setups.slice(0, 10).forEach((s,i) => {
  console.log(`  ${String(i).padStart(2)}: ${s.timeStr} ${s.setupType.padEnd(22)} ${s.direction} @${s.currentPrice.toFixed(2)} rangePos=${(s.rangePos*100).toFixed(0)}%`);
});

fs.writeFileSync(path.join(__dirname, 'quality_setups.json'), JSON.stringify({ setups, bars, indicators: { ema21: ema21Arr, sma50: sma50Arr, atr14: atr14Arr, rsi14: rsi14Arr } }, null, 2));
