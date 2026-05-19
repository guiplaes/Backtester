// Process the TV pull, merge with existing, dedupe, save as extended dataset
const fs = require('fs');
const path = require('path');

const tvPath = 'C:\\Users\\Administrator\\.claude\\projects\\C--Users-Administrator-Desktop-MT4-Claude\\2a783527-c3ea-45b8-9a7d-f135d54f01a0\\tool-results\\mcp-tradingview-data_get_ohlcv-1778499900699.txt';
const tvRaw = JSON.parse(fs.readFileSync(tvPath, 'utf8'));
const tvBars = tvRaw.bars.map(b => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume }));

const existing = JSON.parse(fs.readFileSync(path.join(__dirname, 'xauusd_m15.json'), 'utf8'));
const exBars = existing.bars;

console.log('TV pull:');
console.log(`  bars: ${tvBars.length}`);
console.log(`  from: ${new Date(tvBars[0].time*1000).toISOString()}`);
console.log(`  to:   ${new Date(tvBars[tvBars.length-1].time*1000).toISOString()}`);
console.log(`  total_available on chart: ${tvRaw.total_available}`);

console.log('\nExisting:');
console.log(`  bars: ${exBars.length}`);
console.log(`  from: ${new Date(exBars[0].time*1000).toISOString()}`);
console.log(`  to:   ${new Date(exBars[exBars.length-1].time*1000).toISOString()}`);

// Check TV bar interval (should be 900s = 15min)
const intervals = [];
for (let i = 1; i < Math.min(20, tvBars.length); i++) intervals.push(tvBars[i].time - tvBars[i-1].time);
console.log(`\nTV bar intervals (first 20): unique=${[...new Set(intervals)].join(',')}`);
