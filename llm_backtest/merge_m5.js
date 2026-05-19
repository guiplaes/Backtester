const fs = require('fs');
const path = require('path');

const files = [
  'C:\\Users\\Administrator\\.claude\\projects\\C--Users-Administrator-Desktop-MT4-Claude\\2a783527-c3ea-45b8-9a7d-f135d54f01a0\\tool-results\\mcp-tradingview-data_get_ohlcv-1778501522173.txt',
  'C:\\Users\\Administrator\\.claude\\projects\\C--Users-Administrator-Desktop-MT4-Claude\\2a783527-c3ea-45b8-9a7d-f135d54f01a0\\tool-results\\mcp-tradingview-data_get_ohlcv-1778501536174.txt',
];

const seen = new Set();
const all = [];
for (const f of files) {
  const r = JSON.parse(fs.readFileSync(f, 'utf8'));
  for (const b of r.bars) {
    if (seen.has(b.time)) continue;
    seen.add(b.time);
    all.push({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume });
  }
}
all.sort((a,b) => a.time - b.time);
console.log(`Merged ${all.length} M5 bars`);
console.log(`Range: ${new Date(all[0].time*1000).toISOString()} → ${new Date(all[all.length-1].time*1000).toISOString()}`);

// Check intervals
const intervals = {};
for (let i = 1; i < all.length; i++) {
  const dt = all[i].time - all[i-1].time;
  intervals[dt] = (intervals[dt]||0) + 1;
}
console.log('Interval histogram (top 5):');
Object.entries(intervals).sort((a,b)=>b[1]-a[1]).slice(0,5).forEach(([dt,n]) => console.log(`  ${dt}s: ${n}`));

fs.writeFileSync(path.join(__dirname, 'xauusd_m5.json'), JSON.stringify({ bars: all }));
console.log(`\nSaved xauusd_m5.json (${all.length} bars)`);
