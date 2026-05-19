// Build OOS contexts with SAME v1 prompt
const fs = require('fs');
const path = require('path');

const data = JSON.parse(fs.readFileSync(path.join(__dirname, 'level_touches_oos.json'), 'utf8'));
const { touches, bars, atr14, ema21, rsi14 } = data;

function getSession(h) {
  if (h < 7) return 'Asia';
  if (h < 12) return 'London';
  if (h < 13) return 'London/NY overlap';
  if (h < 17) return 'NY';
  return 'Late';
}
function aggregateH1(barsArr, endIdx, count) {
  const h1 = []; let idx = endIdx;
  while (h1.length < count && idx >= 3) {
    const slice = barsArr.slice(idx - 3, idx + 1);
    h1.unshift({
      open: slice[0].open,
      high: Math.max(...slice.map(b => b.high)),
      low: Math.min(...slice.map(b => b.low)),
      close: slice[slice.length - 1].close,
    });
    idx -= 4;
  }
  return h1;
}
function approachChar(barsArr, idx) {
  const recent = barsArr.slice(Math.max(0, idx - 8), idx + 1);
  let netMove = recent[recent.length-1].close - recent[0].close;
  let volSum = recent.reduce((s,b) => s + b.volume, 0);
  let avgVol = volSum / recent.length;
  let lastBars = recent.slice(-4);
  let lastVol = lastBars.reduce((s,b)=>s+b.volume,0) / lastBars.length;
  let volTrend = lastVol > avgVol * 1.2 ? 'INCREASING (momentum)' :
                 lastVol < avgVol * 0.8 ? 'FADING (exhaustion?)' : 'stable';
  return { netMove, volTrend, avgVol };
}
function priorTests(barsArr, idx, levelPrice, atr) {
  let tests = 0;
  const proxRange = atr * 0.4;
  const curDay = new Date(barsArr[idx].time*1000).getUTCDate();
  for (let j = Math.max(0, idx - 40); j < idx; j++) {
    const d = new Date(barsArr[j].time*1000).getUTCDate();
    if (d !== curDay) continue;
    if (Math.abs(barsArr[j].high - levelPrice) < proxRange || Math.abs(barsArr[j].low - levelPrice) < proxRange) tests++;
  }
  return tests;
}

const ctxDir = path.join(__dirname, 'contexts_levels_oos');
if (!fs.existsSync(ctxDir)) fs.mkdirSync(ctxDir);

touches.forEach((t, idx) => {
  const i = t.idx;
  const lvl = t.level;
  const a = t.atr14;
  const ap = approachChar(bars, i);
  const tests = priorTests(bars, i, lvl.price, a);
  const h1 = aggregateH1(bars, i, 6);
  const h1Str = h1.map(h => `O=${h.open.toFixed(2)} H=${h.high.toFixed(2)} L=${h.low.toFixed(2)} C=${h.close.toFixed(2)} ${h.close > h.open ? 'BULL' : 'BEAR'}`).join('\n  ');
  const recent = [];
  for (let j = Math.max(0, i - 23); j <= i; j++) {
    const b = bars[j];
    const d = new Date(b.time*1000);
    const dd = String(d.getUTCDate()).padStart(2,'0');
    const hh = String(d.getUTCHours()).padStart(2,'0');
    const mm = String(d.getUTCMinutes()).padStart(2,'0');
    recent.push(`${dd} ${hh}:${mm} O=${b.open.toFixed(2)} H=${b.high.toFixed(2)} L=${b.low.toFixed(2)} C=${b.close.toFixed(2)} V=${b.volume}`);
  }
  const isResistance = lvl.type === 'resistance';
  const directionIfReject = isResistance ? 'SHORT' : 'LONG';
  const directionIfBreak = isResistance ? 'LONG' : 'SHORT';
  const confStr = t.confluence.length > 0 ?
    t.confluence.map(c => `${c.name}@${c.price.toFixed(2)}`).join(', ') : 'none';

  const ctx = `XAUUSD M15 — LEVEL TOUCH DECISION

⏰ TIME: ${t.timeStr} | UTC ${t.hourUTC}:00 | Session: ${getSession(t.hourUTC)}
💰 Current price: ${t.currentPrice.toFixed(2)}

━━━ THE LEVEL ━━━
Touched: ${lvl.name} @ ${lvl.price.toFixed(2)} (${lvl.type})
Distance: ${(lvl.price - t.currentPrice >= 0 ? '+' : '')}${(lvl.price - t.currentPrice).toFixed(2)} pts
Confluence with other levels (within 0.5×ATR): ${confStr}
Prior tests today: ${tests}

━━━ APPROACH CONTEXT ━━━
Net move last 8 bars (2h): ${ap.netMove >= 0 ? '+' : ''}${ap.netMove.toFixed(2)} pts (${(ap.netMove / a).toFixed(1)}× ATR)
Volume trend approaching: ${ap.volTrend}
ATR(14): ${a.toFixed(2)}
RSI(14): ${t.rsi14.toFixed(1)} ${t.rsi14 > 70 ? '(extreme high)' : t.rsi14 < 30 ? '(extreme low)' : ''}

━━━ H1 CONTEXT (last 6) ━━━
  ${h1Str}

━━━ M15 BARS (last 24, oldest → current) ━━━
  ${recent.join('\n  ')}

═══════════════════════════════════════════════════════════════
🎯 SINGLE QUESTION: REJECT or BREAK?

Price has just touched ${lvl.name} at ${lvl.price.toFixed(2)}. In the next 8 M15 bars (2 hours), will the level:
  A) REJECT — price bounces away → trade ${directionIfReject}
  B) BREAK — price continues through → trade ${directionIfBreak}
  C) UNCLEAR — no clean read, skip

The level is the bias. You are NOT inventing a trade — you are reading what this specific level is telling you.

REJECT is more likely when:
  - Strong momentum INTO level (exhaustion at resistance/support)
  - Volume fading on approach
  - RSI extreme + at significant level
  - Multiple prior tests today (zone holding)
  - Counter-trend H1 (level fits broader structure)

BREAK is more likely when:
  - Aggressive momentum WITH volume into level
  - H1 trend aligned with break direction
  - First test of the day (no defenders yet)
  - Approach was a clean consolidation, not exhaustion

UNCLEAR is the correct answer when:
  - Mixed signals
  - Approach was choppy/sideways (no information)
  - Level is weak in context

For asymmetric R:R:
  REJECT trade: SL just beyond level (small risk), TP at next opposing level (large reward)
  BREAK trade: SL back inside on close-through, TP at next level beyond

REPLY EXACTLY in this format:
READ: <1-sentence read of what the level is showing>
BIAS: REJECT / BREAK / UNCLEAR
RATIONALE: <2-3 sentences explaining the read>
DECISION: NO_TRADE / LONG / SHORT
ENTRY: <price> or -
SL: <price> or -
TP: <price> or -
CONFIDENCE: <1-10>
`;
  fs.writeFileSync(path.join(ctxDir, `O_${String(idx).padStart(3,'0')}.txt`), ctx);
});

fs.writeFileSync(path.join(__dirname, 'level_meta_oos.json'), JSON.stringify({ touches, bars }));
console.log(`Built ${touches.length} OOS contexts in contexts_levels_oos/`);
