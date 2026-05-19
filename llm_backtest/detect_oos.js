// Run v1 detector on OOS dataset — SAME logic as detect_level_touches.js (v1 before TDH was dropped)
const fs = require('fs');
const path = require('path');

const data = JSON.parse(fs.readFileSync(path.join(__dirname, 'xauusd_m15_oos.json'), 'utf8'));
const bars = data.bars;

function atr(bars, len) {
  const tr = bars.map((b,i) => i===0?b.high-b.low:Math.max(b.high-b.low,Math.abs(b.high-bars[i-1].close),Math.abs(b.low-bars[i-1].close)));
  const out=[]; let p=tr.slice(0,len).reduce((a,b)=>a+b,0)/len;
  for (let i=0;i<tr.length;i++) {
    if (i<len-1){out.push(NaN);continue;}
    if (i===len-1){out.push(p);continue;}
    p=(p*(len-1)+tr[i])/len; out.push(p);
  }
  return out;
}
function ema(arr,len){const k=2/(len+1);const out=[];let p=arr[0];for(let i=0;i<arr.length;i++){p=i===0?arr[i]:arr[i]*k+p*(1-k);out.push(p);}return out;}
function rsi(arr,len){const out=[];let avgGain=0,avgLoss=0;for(let i=0;i<arr.length;i++){if(i===0){out.push(NaN);continue;}const ch=arr[i]-arr[i-1];const g=Math.max(ch,0),l=Math.max(-ch,0);if(i<=len){avgGain+=g/len;avgLoss+=l/len;out.push(i<len?NaN:100-100/(1+avgGain/Math.max(avgLoss,1e-10)));}else{avgGain=(avgGain*(len-1)+g)/len;avgLoss=(avgLoss*(len-1)+l)/len;out.push(100-100/(1+avgGain/Math.max(avgLoss,1e-10)));}}return out;}

const closes = bars.map(b=>b.close);
const atr14 = atr(bars,14);
const ema21 = ema(closes,21);
const rsi14 = rsi(closes,14);

function getLevelsAt(barIdx) {
  const cur = bars[barIdx];
  const curDay = new Date(cur.time*1000).getUTCDate();
  const curPrice = cur.close;
  let pdh=-Infinity,pdl=Infinity,pdDay=null;
  for (let j=barIdx-1;j>=0;j--) {
    const d=new Date(bars[j].time*1000).getUTCDate();
    if (d===curDay) continue;
    if (pdDay===null) pdDay=d;
    if (d!==pdDay) break;
    if (bars[j].high>pdh) pdh=bars[j].high;
    if (bars[j].low<pdl) pdl=bars[j].low;
  }
  let ppdh=-Infinity,ppdl=Infinity,ppdDay=null,pf=false;
  for (let j=barIdx-1;j>=0;j--) {
    const d=new Date(bars[j].time*1000).getUTCDate();
    if (d===curDay) continue;
    if (!pf) {if (pdDay===null||d===pdDay){if(pdDay===null)pdDay=d;continue;} pf=true;}
    if (ppdDay===null) ppdDay=d;
    if (d!==ppdDay) break;
    if (bars[j].high>ppdh) ppdh=bars[j].high;
    if (bars[j].low<ppdl) ppdl=bars[j].low;
  }
  let tdh=-Infinity,tdl=Infinity;
  for (let j=barIdx-1;j>=0;j--){const d=new Date(bars[j].time*1000).getUTCDate();if(d!==curDay)break;if(bars[j].high>tdh)tdh=bars[j].high;if(bars[j].low<tdl)tdl=bars[j].low;}
  const r50A=Math.ceil(curPrice/50)*50, r50B=Math.floor(curPrice/50)*50;
  const swings=[];
  for (let j=Math.max(3,barIdx-60);j<barIdx-2;j++){
    const h=bars[j].high,l=bars[j].low;
    const isH=bars[j-1].high<h&&bars[j-2].high<h&&bars[j+1].high<h&&bars[j+2].high<h;
    const isL=bars[j-1].low>l&&bars[j-2].low>l&&bars[j+1].low>l&&bars[j+2].low>l;
    if (isH) swings.push({type:'swingH',price:h,idx:j});
    if (isL) swings.push({type:'swingL',price:l,idx:j});
  }
  const levels=[
    pdh>0?{name:'PDH',price:pdh,type:'resistance'}:null,
    pdl<Infinity?{name:'PDL',price:pdl,type:'support'}:null,
    ppdh>0?{name:'PPDH',price:ppdh,type:'resistance'}:null,
    ppdl<Infinity?{name:'PPDL',price:ppdl,type:'support'}:null,
    tdh>0?{name:'TDH',price:tdh,type:'resistance'}:null,
    tdl<Infinity?{name:'TDL',price:tdl,type:'support'}:null,
    {name:`R50_${r50A}`,price:r50A,type:'resistance'},
    {name:`R50_${r50B}`,price:r50B,type:'support'},
  ].filter(Boolean);
  const a=atr14[barIdx]||5;
  swings.forEach(s=>{const dup=levels.some(l=>Math.abs(l.price-s.price)<a*0.3);if(!dup)levels.push({name:s.type==='swingH'?'PrSwingH':'PrSwingL',price:s.price,type:s.type==='swingH'?'resistance':'support'});});
  return levels;
}

const touches=[];const PROX=0.35;const lastTouchByLevel={};

for (let i=60;i<bars.length-10;i++){
  const b=bars[i];const a=atr14[i];if(isNaN(a))continue;
  const date=new Date(b.time*1000);const hr=date.getUTCHours();
  if (hr<7||hr>=17) continue;
  const levels=getLevelsAt(i);
  const strong=levels.filter(l=>!l.name.startsWith('PrSwing'));
  const weak=levels.filter(l=>l.name.startsWith('PrSwing'));
  for (const lvl of strong){
    const dist=Math.abs(b.close-lvl.price);
    const wickReached=(lvl.type==='resistance'&&b.high>=lvl.price-a*PROX)||(lvl.type==='support'&&b.low<=lvl.price+a*PROX);
    if (!wickReached) continue;
    if (dist>a*PROX) continue;
    const key=lvl.name+'_'+lvl.price.toFixed(1);
    if (lastTouchByLevel[key]!==undefined&&i-lastTouchByLevel[key]<8) continue;
    lastTouchByLevel[key]=i;
    const conf=[...strong,...weak].filter(l2=>l2!==lvl&&Math.abs(l2.price-lvl.price)<a*0.5);
    touches.push({idx:i,time:b.time,timeStr:date.toISOString(),hourUTC:hr,level:lvl,confluence:conf,levelDist:lvl.price-b.close,currentPrice:b.close,atr14:a,rsi14:rsi14[i],ema21:ema21[i]});
  }
}

console.log(`OOS bars: ${bars.length}`);
console.log(`Level touches detected: ${touches.length}`);
const byLevel={};touches.forEach(t=>{const n=t.level.name.replace(/_\d+$/,'_RND');byLevel[n]=(byLevel[n]||0)+1;});
Object.keys(byLevel).sort().forEach(n=>console.log(`  ${n}: ${byLevel[n]}`));

// Distribution by week (separating by date)
const byDay={};
touches.forEach(t=>{const d=t.timeStr.slice(0,10);byDay[d]=(byDay[d]||0)+1;});
console.log('\nTouches by day:');
Object.keys(byDay).sort().forEach(d=>console.log(`  ${d}: ${byDay[d]}`));

fs.writeFileSync(path.join(__dirname,'level_touches_oos.json'),JSON.stringify({touches,bars,atr14,ema21,rsi14}));
console.log(`\nSaved level_touches_oos.json`);
