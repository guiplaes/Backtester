// Save EURUSD + GBPUSD daily data to JSON
const fs = require('fs');
const path = require('path');

// EURUSD data fetched from TV (in main message)
// Save as separate files for later use
// Since I have both datasets inline, just verify what we have

// Find latest pulls in tool-results
const dir = 'C:/Users/Administrator/.claude/projects/C--Users-Administrator-Desktop-MT4-Claude/2a783527-c3ea-45b8-9a7d-f135d54f01a0/tool-results/';
const files = fs.readdirSync(dir).filter(f => f.startsWith('mcp-tradingview-data_get_ohlcv'));
files.sort();
console.log(`Found ${files.length} ohlcv files`);
console.log('Latest 5:');
files.slice(-5).forEach(f => {
  try {
    const r = JSON.parse(fs.readFileSync(dir + f, 'utf8'));
    if (r.bars && r.bars.length > 0) {
      const first = new Date(r.bars[0].time*1000).toISOString().slice(0,10);
      const last = new Date(r.bars[r.bars.length-1].time*1000).toISOString().slice(0,10);
      console.log(`  ${f.slice(-25)}: ${r.bars.length} bars [${first} → ${last}] price~${r.bars[0].close.toFixed(4)}`);
    }
  } catch(e) {}
});
