// Merge all OOS TV pulls into a single OOS dataset
const fs = require('fs');
const path = require('path');

const files = [
  'C:\\Users\\Administrator\\.claude\\projects\\C--Users-Administrator-Desktop-MT4-Claude\\2a783527-c3ea-45b8-9a7d-f135d54f01a0\\tool-results\\mcp-tradingview-data_get_ohlcv-1778499977514.txt',  // 04-19 to 04-28
  'C:\\Users\\Administrator\\.claude\\projects\\C--Users-Administrator-Desktop-MT4-Claude\\2a783527-c3ea-45b8-9a7d-f135d54f01a0\\tool-results\\mcp-tradingview-data_get_ohlcv-1778500018276.txt',  // 04-08 to 04-15
  'C:\\Users\\Administrator\\.claude\\projects\\C--Users-Administrator-Desktop-MT4-Claude\\2a783527-c3ea-45b8-9a7d-f135d54f01a0\\tool-results\\mcp-tradingview-data_get_ohlcv-1778500049780.txt',  // 04-04 to 04-08
];

const seen = new Set();
const allBars = [];
for (const f of files) {
  const r = JSON.parse(fs.readFileSync(f, 'utf8'));
  for (const b of r.bars) {
    if (seen.has(b.time)) continue;
    seen.add(b.time);
    allBars.push({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume });
  }
}
allBars.sort((a,b) => a.time - b.time);

// Verify intervals
const intervals = {};
for (let i = 1; i < allBars.length; i++) {
  const dt = allBars[i].time - allBars[i-1].time;
  intervals[dt] = (intervals[dt]||0) + 1;
}
console.log(`Merged ${allBars.length} unique bars`);
console.log(`Date range: ${new Date(allBars[0].time*1000).toISOString()} → ${new Date(allBars[allBars.length-1].time*1000).toISOString()}`);
console.log(`Interval histogram (top 5):`);
Object.entries(intervals).sort((a,b)=>b[1]-a[1]).slice(0,5).forEach(([dt,n]) => console.log(`  ${dt}s: ${n}`));

// Identify week boundaries (gaps > 24h = weekend)
const weeks = [];
let curWeek = [allBars[0]];
for (let i = 1; i < allBars.length; i++) {
  const gap = allBars[i].time - allBars[i-1].time;
  if (gap > 86400) {  // > 24h gap = new week
    weeks.push(curWeek);
    curWeek = [];
  }
  curWeek.push(allBars[i]);
}
if (curWeek.length) weeks.push(curWeek);

console.log(`\nWeeks identified: ${weeks.length}`);
weeks.forEach((w, i) => {
  console.log(`  Week ${i+1}: ${w.length} bars, ${new Date(w[0].time*1000).toISOString().slice(0,16)} → ${new Date(w[w.length-1].time*1000).toISOString().slice(0,16)}`);
});

fs.writeFileSync(path.join(__dirname, 'xauusd_m15_oos.json'), JSON.stringify({ bars: allBars }));
console.log(`\nSaved xauusd_m15_oos.json`);
