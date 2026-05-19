// Build LLM contexts for high-interest moments only (score >= 4)
const fs = require('fs');
const path = require('path');

const data = JSON.parse(fs.readFileSync(path.join(__dirname, 'interest_moments.json'), 'utf8'));
const { moments, bars } = data;

// Filter to score >= 4
const highInterest = moments.filter(m => m.score >= 4);
console.log(`High-interest moments (score >= 4): ${highInterest.length}`);

const ctxDir = path.join(__dirname, 'contexts_interest');
if (!fs.existsSync(ctxDir)) fs.mkdirSync(ctxDir);

const LOOKBACK = 30;
const SIM_FORWARD = 8;

highInterest.forEach((m, idx) => {
  const recent = [];
  for (let j = Math.max(0, m.idx - LOOKBACK + 1); j <= m.idx; j++) {
    const b = bars[j];
    const d = new Date(b.time * 1000);
    const dd = String(d.getUTCDate()).padStart(2,'0');
    const hh = String(d.getUTCHours()).padStart(2,'0');
    const mm = String(d.getUTCMinutes()).padStart(2,'0');
    recent.push(`${dd} ${hh}:${mm} O=${b.open.toFixed(2)} H=${b.high.toFixed(2)} L=${b.low.toFixed(2)} C=${b.close.toFixed(2)}`);
  }

  const ctx = `XAUUSD M15 — HIGH INTEREST moment detected
Time: ${m.timeStr} (UTC hour ${m.hourUTC})
Current price: ${m.currentPrice.toFixed(2)}

INTEREST SCORE: ${m.score}/6
Signals firing: ${m.reasons.join(', ')}

Detected because: vol_expand=volatility bar > 1×ATR | aligned=trend EMA21+SMA50 same side | near_swing=close to swing extreme | breakout=cons breakout | active_hour=London/NY | rsi_extreme=RSI>70 or <30

LAST 30 M15 BARS:
  ${recent.join('\n  ')}

INDICATORS NOW:
  EMA21: ${m.ema21.toFixed(2)} (${m.currentPrice > m.ema21 ? 'above' : 'below'})
  SMA50: ${m.sma50.toFixed(2)} (${m.currentPrice > m.sma50 ? 'above' : 'below'})
  ATR(14): ${m.atr14.toFixed(2)}
  RSI(14): ${m.rsi14.toFixed(1)}
  Swing HIGH (30b): ${m.swingH.toFixed(2)} (${(m.swingH - m.currentPrice).toFixed(2)} above)
  Swing LOW (30b):  ${m.swingL.toFixed(2)} (${(m.currentPrice - m.swingL).toFixed(2)} below)

THIS IS NOT random — pre-filter detected ${m.score} signals. Something IS happening here.

YOUR TASK as discretionary intraday trader:
Decide: would you take a trade in next 2 hours (8 M15 bars)?
- LONG / SHORT / NO_TRADE
- Set entry, SL, TP based on what you see
- Be DECISIVE — this is a moment of high signal

REPLY EXACTLY:
DECISION: NO_TRADE/LONG/SHORT
ENTRY: <price> or -
SL: <price> or -
TP: <price> or -
REASON: <max 30 words>`;
  fs.writeFileSync(path.join(ctxDir, `mom_${String(idx).padStart(2,'0')}.txt`), ctx);
});

// Save metadata
fs.writeFileSync(path.join(__dirname, 'high_interest_meta.json'), JSON.stringify({ moments: highInterest, bars }, null, 2));
console.log(`Built ${highInterest.length} contexts in contexts_interest/`);
console.log(`Score distribution:`);
const dist = {};
highInterest.forEach(m => dist[m.score] = (dist[m.score]||0)+1);
Object.keys(dist).sort().forEach(s => console.log(`  Score ${s}: ${dist[s]}`));
