// Discretionary AI Trader Test
// Windows every 2h on M15 (every 8 bars), Agent decides freely
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

// Generate windows every 8 bars (2h) starting from bar 60
const windows = [];
const STEP = 8;
const LOOKBACK = 50;
const SIM_FORWARD = 8;

for (let i = LOOKBACK + 50; i < bars.length - SIM_FORWARD - 1; i += STEP) {
  // Last 50 bars context
  const ctx = [];
  for (let j = i - LOOKBACK + 1; j <= i; j++) {
    const b = bars[j];
    const d = new Date(b.time * 1000);
    const dd = String(d.getUTCDate()).padStart(2,'0');
    const hh = String(d.getUTCHours()).padStart(2,'0');
    const mm = String(d.getUTCMinutes()).padStart(2,'0');
    ctx.push(`${dd} ${hh}:${mm} O=${b.open.toFixed(2)} H=${b.high.toFixed(2)} L=${b.low.toFixed(2)} C=${b.close.toFixed(2)}`);
  }

  // Recent swings
  let swingH = -Infinity, swingL = Infinity;
  for (let j = i - 30; j <= i; j++) {
    if (bars[j].high > swingH) swingH = bars[j].high;
    if (bars[j].low < swingL) swingL = bars[j].low;
  }

  const b = bars[i];
  const date = new Date(b.time * 1000);
  windows.push({
    idx: i,
    time: b.time,
    timeStr: date.toISOString(),
    hourUTC: date.getUTCHours(),
    currentPrice: b.close,
    contextBars: ctx,
    ema21: ema21[i],
    sma50: sma50[i],
    atr14: atr14[i],
    rsi14: rsi14[i],
    swingH, swingL,
    bars,
  });
}

console.log(`Generated ${windows.length} windows (every 2h, M15, last 4-7 days)`);

// Build context files
const ctxDir = path.join(__dirname, 'contexts_discr');
if (!fs.existsSync(ctxDir)) fs.mkdirSync(ctxDir);

windows.forEach((w, idx) => {
  const ctx = `XAUUSD M15 — Current moment: ${w.timeStr}
You are a discretionary intraday trader looking at this chart RIGHT NOW.

CURRENT PRICE: ${w.currentPrice.toFixed(2)}
Hour UTC: ${w.hourUTC}

LAST 50 M15 BARS (oldest → current, current is last):
  ${w.contextBars.join('\n  ')}

INDICATORS NOW:
  EMA21:   ${w.ema21.toFixed(2)} (${w.currentPrice > w.ema21 ? 'above' : 'below'}, ${(w.currentPrice - w.ema21).toFixed(2)})
  SMA50:   ${w.sma50 ? w.sma50.toFixed(2) : 'NA'} (${w.currentPrice > w.sma50 ? 'above' : 'below'})
  ATR(14): ${w.atr14.toFixed(2)}
  RSI(14): ${w.rsi14.toFixed(1)}
  30-bar swing HIGH: ${w.swingH.toFixed(2)} (${(w.swingH - w.currentPrice).toFixed(2)} above)
  30-bar swing LOW:  ${w.swingL.toFixed(2)} (${(w.currentPrice - w.swingL).toFixed(2)} below)

YOUR TASK as discretionary trader:
Look at this chart and decide: would you take a trade right now?
- You can pick ANY direction (long/short)
- You can SKIP if nothing interesting
- Define your own SL and TP based on what you see
- Use ONLY the data above (NO future info)

Trade will be simulated next ${SIM_FORWARD} bars (next 2 hours). After that, position closes at market regardless.

REPLY in this EXACT format (one line per field):
DECISION: NO_TRADE  (or)  LONG  (or)  SHORT
ENTRY: <price> (or - if NO_TRADE)
SL: <price> (or - if NO_TRADE)
TP: <price> (or - if NO_TRADE)
REASON: <max 30 words>`;
  fs.writeFileSync(path.join(ctxDir, `win_${String(idx).padStart(2,'0')}.txt`), ctx);
});

// Save windows metadata
const meta = windows.map(({ bars, contextBars, ...rest }) => rest);
fs.writeFileSync(path.join(__dirname, 'windows_meta.json'), JSON.stringify({ windows: meta, allBars: bars }, null, 2));

console.log(`Built ${windows.length} contexts in contexts_discr/`);
console.log(`First window: ${windows[0].timeStr}`);
console.log(`Last window:  ${windows[windows.length-1].timeStr}`);
