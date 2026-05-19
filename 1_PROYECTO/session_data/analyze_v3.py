#!/usr/bin/env python3
"""Anatomia v3 — el millor anàlisi possible amb les dades disponibles.

Datasets:
  XAUUSD H1 (~17 dies, 300 bars) — sessió fina
  XAUUSD M15 (~3 dies, 300 bars) — reversions intra-bar
  XAUUSD H4 (~70 dies, 300 bars) — caràcter de fons + estadística robusta
  USDJPY H4 (~70 dies, 301 bars) — driver d'Asia
  DXY H4    (~70 dies, 302 bars) — pressió USD
  DXY H1    (~17 dies, 300 bars) — fine-grain DXY
  brain_trade_history.json — trades reals operats

Mètriques a la vegada:
  Estructurals (per H4 sample, n més robust):
    1. Trend persistence (runs ≥3 closes)
    2. Mean reversion strength (50% retrace en 3 bars)
    3. Sweep rate (% de breaks que reverteixen)
    4. Range vs trending body ratio
    5. Hourly direction bias (per H1)
  Macro (correlacions per sessió × per timeframe):
    6. DXY r + edge per sessió
    7. USDJPY r + edge per sessió (especialment per Asia)
  Real-trade alignment:
    8. Distribució de trades reals per sessió × hora
    9. Ratios reals d'averaging per sessió
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import median, mean, stdev
from collections import Counter, defaultdict

DATA = Path(__file__).resolve().parent
COMMON = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files")

def load(name):
    with open(DATA / name, encoding="utf-8") as f:
        return json.load(f)["bars"]

xau_h1 = load("xauusd_h1.json")
xau_m15 = load("xauusd_m15.json")
xau_h4 = load("xauusd_h4.json")
usdjpy_h4 = load("usdjpy_h4.json")
dxy_h4 = load("dxy_h4.json")
dxy_h1 = load("dxy_h1.json")

# Trade history
trade_events = []
try:
    with open(COMMON / "brain_trade_history.json", encoding="utf-8") as f:
        trade_events = json.load(f).get("events", [])
except Exception:
    pass

def session_of_hour(h):
    if 0 <= h < 7:   return "ASIA"
    if 7 <= h < 13:  return "LONDON"
    if 13 <= h < 16: return "OVERLAP"
    if 16 <= h < 21: return "NY"
    return "DEAD"

def session_of_ts(ts):
    return session_of_hour(datetime.fromtimestamp(ts, tz=timezone.utc).hour)

def hour_of(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).hour

SESSIONS = ("ASIA","LONDON","OVERLAP","NY","DEAD")
def pct(num, den): return (num/den*100.0) if den else 0.0
def fmt(v, dp=2):
    if v is None: return "-"
    return f"{v:.{dp}f}" if isinstance(v, float) else str(v)

# ── For H4 bars: bucket by START hour. H4 spans 4h so bucket is approximate.
# Use the START hour to assign session.

# ── 1. Mean reversion (H4, ~70 days = robust) ──────────────────────
def mean_reversion_h4(bars, threshold=20.0, retrace_pct=0.5, lookforward=3):
    by_sess = {s: {"trigger": 0, "retraced": 0} for s in SESSIONS}
    n = len(bars)
    for i in range(n - lookforward):
        b = bars[i]
        rng = b[2] - b[3]
        if rng < threshold:
            continue
        sess = session_of_ts(b[0])
        bull_push = b[2] - b[1]
        bear_push = b[1] - b[3]
        if bull_push >= bear_push:
            target = b[2] - rng * retrace_pct
            future_low = min(bars[j][3] for j in range(i+1, i+1+lookforward))
            retraced = future_low <= target
        else:
            target = b[3] + rng * retrace_pct
            future_high = max(bars[j][2] for j in range(i+1, i+1+lookforward))
            retraced = future_high >= target
        by_sess[sess]["trigger"] += 1
        if retraced:
            by_sess[sess]["retraced"] += 1
    return {s: {"trigger": d["trigger"], "retraced": d["retraced"],
                "rev_pct": pct(d["retraced"], d["trigger"])}
            for s, d in by_sess.items()}

# ── 2. Trend persistence H4 (with carry across bars within session) ──
def trend_runs_h4(bars):
    by_sess = {s: [] for s in SESSIONS}
    by_sess_bias = {s: {"bull":0,"bear":0,"doji":0} for s in SESSIONS}
    cur_run = 0; cur_dir = None; cur_sess = None
    for b in bars:
        body = b[4] - b[1]
        d = "bull" if body > 0.5 else ("bear" if body < -0.5 else "doji")
        sess = session_of_ts(b[0])
        by_sess_bias[sess][d] += 1
        if sess != cur_sess:
            if cur_run > 0 and cur_sess:
                by_sess[cur_sess].append(cur_run)
            cur_run = 1 if d != "doji" else 0
            cur_dir = d
            cur_sess = sess
            continue
        if d == cur_dir and d != "doji":
            cur_run += 1
        else:
            if cur_run > 0:
                by_sess[sess].append(cur_run)
            cur_run = 1 if d != "doji" else 0
            cur_dir = d
    if cur_run > 0 and cur_sess:
        by_sess[cur_sess].append(cur_run)
    out = {}
    for s in SESSIONS:
        runs = by_sess[s]
        bias = by_sess_bias[s]
        total = sum(bias.values())
        out[s] = {
            "n_runs": len(runs),
            "median_run": median(runs) if runs else 0,
            "max_run": max(runs) if runs else 0,
            "pct_runs_ge3": pct(sum(1 for r in runs if r>=3), len(runs)),
            "pct_bull": pct(bias["bull"], total),
            "pct_bear": pct(bias["bear"], total),
        }
    return out

# ── 3. Sweep rate H4 ──
def sweeps_h4(bars):
    by_sess = {s: {"new_high":0,"high_swept":0,"new_low":0,"low_swept":0} for s in SESSIONS}
    for i in range(1, len(bars)):
        prev = bars[i-1]; cur = bars[i]
        sess = session_of_ts(cur[0])
        if cur[2] > prev[2]:
            by_sess[sess]["new_high"] += 1
            if cur[4] < prev[2]:
                by_sess[sess]["high_swept"] += 1
        if cur[3] < prev[3]:
            by_sess[sess]["new_low"] += 1
            if cur[4] > prev[3]:
                by_sess[sess]["low_swept"] += 1
    out = {}
    for s in SESSIONS:
        d = by_sess[s]
        nh = d["new_high"]; nl = d["new_low"]
        out[s] = {
            "extremes": nh+nl,
            "swept": d["high_swept"]+d["low_swept"],
            "sweep_pct": pct(d["high_swept"]+d["low_swept"], nh+nl),
            "high_sweep_pct": pct(d["high_swept"], nh),
            "low_sweep_pct": pct(d["low_swept"], nl),
        }
    return out

# ── 4. Range vs trend H4 ──
def range_vs_trend_h4(bars):
    by_sess = {s:{"trending":0,"range":0,"mixed":0,"n":0} for s in SESSIONS}
    for b in bars:
        rng = b[2]-b[3]
        if rng < 1: continue
        body_ratio = abs(b[4]-b[1])/rng
        sess = session_of_ts(b[0])
        by_sess[sess]["n"] += 1
        if body_ratio > 0.7: by_sess[sess]["trending"] += 1
        elif body_ratio < 0.3: by_sess[sess]["range"] += 1
        else: by_sess[sess]["mixed"] += 1
    return {s: {"n": d["n"],
                "pct_trending": pct(d["trending"], d["n"]),
                "pct_range": pct(d["range"], d["n"]),
                "pct_mixed": pct(d["mixed"], d["n"])}
            for s, d in by_sess.items()}

# ── 5. Volatility per session H4 ──
def volatility_h4(bars):
    by_sess = {s: [] for s in SESSIONS}
    for b in bars:
        sess = session_of_ts(b[0])
        by_sess[sess].append(b[2]-b[3])
    out = {}
    for s in SESSIONS:
        rs = by_sess[s]
        if not rs: out[s] = None; continue
        out[s] = {
            "n": len(rs),
            "median": median(rs),
            "mean": mean(rs),
            "p75": sorted(rs)[3*len(rs)//4] if len(rs)>=4 else max(rs),
            "p90": sorted(rs)[int(len(rs)*0.9)] if len(rs)>=10 else max(rs),
            "max": max(rs),
            "stdev": stdev(rs) if len(rs)>1 else 0,
        }
    return out

# ── 6. Cross-asset correlation per session ──
def correlation_h4(xau_bars, other_bars, label):
    other_idx = {b[0]: b for b in other_bars}
    by_sess = {s: [] for s in SESSIONS}
    for b in xau_bars:
        o = other_idx.get(b[0])
        if not o: continue
        by_sess[session_of_ts(b[0])].append((b[4]-b[1], o[4]-o[1]))  # close-open
    out = {}
    for s in SESSIONS:
        pairs = by_sess[s]
        if len(pairs) < 5:
            out[s] = None; continue
        n = len(pairs)
        mx = sum(p[0] for p in pairs)/n; my = sum(p[1] for p in pairs)/n
        cov = sum((p[0]-mx)*(p[1]-my) for p in pairs)
        sxx = sum((p[0]-mx)**2 for p in pairs); syy = sum((p[1]-my)**2 for p in pairs)
        if sxx<=0 or syy<=0:
            out[s] = None; continue
        r = cov / (sxx**0.5 * syy**0.5)
        # Concordance: % of times moves are aligned vs opposite
        # For DXY: "aligned" = inverse (XAU↑ + DXY↓) — textbook negative correlation
        # For USDJPY: similarly inverse usually (USD strength moves XAU lower)
        concordant_neg = sum(1 for p in pairs if (p[0]>0 and p[1]<0) or (p[0]<0 and p[1]>0))
        out[s] = {"n": n, "r": r, "neg_concord_pct": pct(concordant_neg, n)}
    return out

# ── 7. Macro confluence efficacy: when XAU + macro aligned (inverse), continuation? ──
def confluence_efficacy(xau_bars, other_bars):
    other_idx = {b[0]: b for b in other_bars}
    by_sess = {s: {"aligned":{"continued":0,"reversed":0},
                   "counter":{"continued":0,"reversed":0}} for s in SESSIONS}
    for i in range(len(xau_bars)-1):
        cur = xau_bars[i]; nxt = xau_bars[i+1]
        o = other_idx.get(cur[0])
        if not o: continue
        x_dir = 1 if cur[4]>cur[1] else (-1 if cur[4]<cur[1] else 0)
        o_dir = 1 if o[4]>o[1] else (-1 if o[4]<o[1] else 0)
        if x_dir==0 or o_dir==0: continue
        n_dir = 1 if nxt[4]>nxt[1] else (-1 if nxt[4]<nxt[1] else 0)
        if n_dir==0: continue
        sess = session_of_ts(cur[0])
        is_aligned = (x_dir != o_dir)  # inverse correlation = aligned
        bucket = "aligned" if is_aligned else "counter"
        if n_dir == x_dir: by_sess[sess][bucket]["continued"] += 1
        else: by_sess[sess][bucket]["reversed"] += 1
    out = {}
    for s in SESSIONS:
        d = by_sess[s]
        a = d["aligned"]["continued"]+d["aligned"]["reversed"]
        c = d["counter"]["continued"]+d["counter"]["reversed"]
        out[s] = {
            "n_aligned": a, "n_counter": c,
            "aligned_continue_pct": pct(d["aligned"]["continued"], a),
            "counter_continue_pct": pct(d["counter"]["continued"], c),
        }
    return out

# ── 8. Real trade analysis ──
def real_trades_per_session():
    by_sess = {s: {"opens":0,"avgs":0,"partials":0,"full_closes":0,"sig_closes":0} for s in SESSIONS}
    by_hour = defaultdict(lambda: {"opens":0,"avgs":0})
    open_to_session = {}
    for e in trade_events:
        utc_str = e.get("utc","")
        try:
            dt = datetime.fromisoformat(utc_str.replace("Z","+00:00"))
        except: continue
        sess = session_of_hour(dt.hour)
        t = e.get("type","")
        if t == "OPEN":
            by_sess[sess]["opens"] += 1
            by_hour[dt.hour]["opens"] += 1
        elif t == "AVERAGE":
            by_sess[sess]["avgs"] += 1
            by_hour[dt.hour]["avgs"] += 1
        elif t == "PARTIAL_CLOSE":
            by_sess[sess]["partials"] += 1
        elif t == "FULL_CLOSE":
            by_sess[sess]["full_closes"] += 1
        elif t == "SIGNAL_CLOSE":
            by_sess[sess]["sig_closes"] += 1
    return by_sess, dict(by_hour)

# ── Run all ──
print("Running anatomy v3...")
mr = mean_reversion_h4(xau_h4)
runs = trend_runs_h4(xau_h4)
sw = sweeps_h4(xau_h4)
rt = range_vs_trend_h4(xau_h4)
vol = volatility_h4(xau_h4)
corr_dxy = correlation_h4(xau_h4, dxy_h4, "DXY")
corr_jpy = correlation_h4(xau_h4, usdjpy_h4, "USDJPY")
edge_dxy = confluence_efficacy(xau_h4, dxy_h4)
edge_jpy = confluence_efficacy(xau_h4, usdjpy_h4)
trades_sess, trades_hour = real_trades_per_session()

# ── Output markdown ──
out = []
out.append("# XAUUSD — Anatomia v3 (anàlisi exhaustiva multi-asset, 70 dies)\n")
out.append(f"_Generat: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_\n")
out.append("**Datasets:**")
out.append(f"- XAUUSD H4: {len(xau_h4)} bars (~70 dies, finestra principal d'anàlisi)")
out.append(f"- XAUUSD H1: {len(xau_h1)} bars (~17 dies, fine-grain)")
out.append(f"- XAUUSD M15: {len(xau_m15)} bars (~3 dies)")
out.append(f"- USDJPY H4: {len(usdjpy_h4)} bars (~70 dies)")
out.append(f"- DXY H4: {len(dxy_h4)} bars · DXY H1: {len(dxy_h1)} bars")
out.append(f"- Trades reals operats: {len(trade_events)} events des de {trade_events[0]['utc'][:10] if trade_events else 'n/a'}")
out.append("")
out.append("**Bucketing UTC:** ASIA=00-07 · LONDON=07-13 · OVERLAP=13-16 · NY=16-21 · DEAD=21-00\n")

# 1. Volatility
out.append("## 1. Volatilitat per sessió (H4, n robust)\n")
out.append("| Sessió | n | Range med | Mean | p75 | p90 | Max |")
out.append("|---|---:|---:|---:|---:|---:|---:|")
for s in SESSIONS:
    v = vol[s]
    if not v: continue
    out.append(f"| **{s}** | {v['n']} | {v['median']:.1f} | {v['mean']:.1f} | {v['p75']:.1f} | {v['p90']:.1f} | {v['max']:.1f} |")
out.append("\n_Range típic d'una H4 bar (4 hores). Per la nostra operació M5 amb objectius $10-15, ranges H4 ≥20$ indiquen entorn ric d'oportunitats._\n")

# 2. Mean reversion
out.append("## 2. Reversió estructural (després d'un H4 amb range ≥20$, retrocedeix ≥50% en 3 bars=12h?)\n")
out.append("| Sessió | n triggers | retracen | % reversió |")
out.append("|---|---:|---:|---:|")
for s in SESSIONS:
    m = mr[s]
    out.append(f"| **{s}** | {m['trigger']} | {m['retraced']} | **{m['rev_pct']:.0f}%** |")
out.append("\n_>60% = reversió estructural fiable. <40% = trends — sistema actual no encaixaria._\n")

# 3. Trend persistence
out.append("## 3. Persistència de tendència (runs consecutius)\n")
out.append("| Sessió | n runs | mediana | màx | %≥3 | %bull | %bear |")
out.append("|---|---:|---:|---:|---:|---:|---:|")
for s in SESSIONS:
    r = runs[s]
    out.append(f"| **{s}** | {r['n_runs']} | {r['median_run']} | {r['max_run']} | {r['pct_runs_ge3']:.0f}% | {r['pct_bull']:.0f}% | {r['pct_bear']:.0f}% |")
out.append("\n_%≥3 baix = whipsaw (favorable a fade-system). Alt = trending (perillós averaging contra)._\n")

# 4. Sweep rate
out.append("## 4. Sweep rate (% de breaks que reverteixen = fakeouts)\n")
out.append("| Sessió | n extrems | swept | %sweep | %high sweep | %low sweep |")
out.append("|---|---:|---:|---:|---:|---:|")
for s in SESSIONS:
    sw_d = sw[s]
    out.append(f"| **{s}** | {sw_d['extremes']} | {sw_d['swept']} | **{sw_d['sweep_pct']:.0f}%** | {sw_d['high_sweep_pct']:.0f}% | {sw_d['low_sweep_pct']:.0f}% |")
out.append("\n_Sweep alta = espera a confirmació de retorn abans d'entrar. Sweep baixa = breaks més reals._\n")

# 5. Range vs trend
out.append("## 5. Estructura del moviment (body/range)\n")
out.append("| Sessió | n | %trending | %range | %mixed |")
out.append("|---|---:|---:|---:|---:|")
for s in SESSIONS:
    rt_d = rt[s]
    out.append(f"| **{s}** | {rt_d['n']} | {rt_d['pct_trending']:.0f}% | {rt_d['pct_range']:.0f}% | {rt_d['pct_mixed']:.0f}% |")
out.append("\n_Range alt + body baix = ideal nostre. Trending alt = perill._\n")

# 6. DXY correlation
out.append("## 6. Correlació amb DXY (H4)\n")
out.append("| Sessió | n | r (Pearson) | %h XAU↑↔DXY↓ |")
out.append("|---|---:|---:|---:|")
for s in SESSIONS:
    c = corr_dxy.get(s)
    if c is None: out.append(f"| {s} | - | - | - |"); continue
    out.append(f"| **{s}** | {c['n']} | {c['r']:.3f} | {c['neg_concord_pct']:.0f}% |")
out.append("")

# USDJPY correlation
out.append("## 7. Correlació amb USDJPY (H4) — driver d'Asia\n")
out.append("| Sessió | n | r (Pearson) | %h XAU↑↔USDJPY↓ |")
out.append("|---|---:|---:|---:|")
for s in SESSIONS:
    c = corr_jpy.get(s)
    if c is None: out.append(f"| {s} | - | - | - |"); continue
    out.append(f"| **{s}** | {c['n']} | {c['r']:.3f} | {c['neg_concord_pct']:.0f}% |")
out.append("\n_USDJPY r molt diferent entre sessions = informació valuosa. Si Asia r alt amb USDJPY però baix amb DXY, USDJPY és el filtre adequat allà._\n")

# Confluence edge
out.append("## 8. Edge de confluència macro (continua quan està alineat?)\n")
out.append("### DXY edge")
out.append("| Sessió | n alineats | %continuen | n counter | %continuen | edge |")
out.append("|---|---:|---:|---:|---:|---:|")
for s in SESSIONS:
    e = edge_dxy[s]
    edge = e["aligned_continue_pct"] - e["counter_continue_pct"]
    out.append(f"| **{s}** | {e['n_aligned']} | {e['aligned_continue_pct']:.0f}% | {e['n_counter']} | {e['counter_continue_pct']:.0f}% | **{edge:+.0f}%** |")
out.append("")
out.append("### USDJPY edge")
out.append("| Sessió | n alineats | %continuen | n counter | %continuen | edge |")
out.append("|---|---:|---:|---:|---:|---:|")
for s in SESSIONS:
    e = edge_jpy[s]
    edge = e["aligned_continue_pct"] - e["counter_continue_pct"]
    out.append(f"| **{s}** | {e['n_aligned']} | {e['aligned_continue_pct']:.0f}% | {e['n_counter']} | {e['counter_continue_pct']:.0f}% | **{edge:+.0f}%** |")
out.append("\n_Edge positiu = aquesta confluència aporta valor. Edge ≤0 = soroll._\n")

# 9. Real trades
out.append("## 9. Trades reals operats (cross-reference)\n")
out.append("| Sessió | OPENs | AVGs | PARTIALs | FULL_CLOSEs | SIGNAL_CLOSEs |")
out.append("|---|---:|---:|---:|---:|---:|")
for s in SESSIONS:
    t = trades_sess[s]
    out.append(f"| **{s}** | {t['opens']} | {t['avgs']} | {t['partials']} | {t['full_closes']} | {t['sig_closes']} |")
out.append("")
out.append("**Distribució real per hora UTC:**\n")
out.append("| Hora | Sessió | OPENs | AVGs | Range med H1 |")
out.append("|---:|---|---:|---:|---:|")
# Compute median range per hour from H1
h1_ranges = defaultdict(list)
for b in xau_h1:
    h1_ranges[hour_of(b[0])].append(b[2]-b[3])
for h in sorted(trades_hour.keys()):
    rng_med = median(h1_ranges[h]) if h1_ranges.get(h) else 0
    out.append(f"| {h:02d} | {session_of_hour(h)} | {trades_hour[h]['opens']} | {trades_hour[h]['avgs']} | {rng_med:.1f} |")
out.append("")

# Final synthesis
out.append("## 10. Síntesi final per sessió\n")

# Compute composite "fitness" for our system
def fitness(s):
    rev = mr[s]["rev_pct"]
    rng_pct = rt[s]["pct_range"]
    sweep = sw[s]["sweep_pct"]
    trend_pct = rt[s]["pct_trending"]
    runs_ge3 = runs[s]["pct_runs_ge3"]
    return (rev*0.4 + rng_pct*0.2 + sweep*0.2 + (100-trend_pct)*0.1 + (100-runs_ge3)*0.1)

scores = {s: fitness(s) for s in SESSIONS}

# Identify best macro filter per session
best_filter = {}
for s in SESSIONS:
    edge_d = edge_dxy[s]["aligned_continue_pct"] - edge_dxy[s]["counter_continue_pct"]
    edge_j = edge_jpy[s]["aligned_continue_pct"] - edge_jpy[s]["counter_continue_pct"]
    if max(edge_d, edge_j) <= 5:
        best_filter[s] = ("none", 0)
    elif edge_d >= edge_j:
        best_filter[s] = ("DXY", edge_d)
    else:
        best_filter[s] = ("USDJPY", edge_j)

out.append("| Sessió | Score | Vol med H4 | Reversió | Sweep | Range% | Millor confluència macro |")
out.append("|---|---:|---:|---:|---:|---:|---|")
for s in sorted(SESSIONS, key=lambda x: scores[x], reverse=True):
    f = best_filter[s]
    filter_txt = f"{f[0]} ({f[1]:+.0f}% edge)" if f[0]!="none" else "cap (edge ≤5%)"
    out.append(f"| **{s}** | {scores[s]:.1f} | {vol[s]['median']:.1f} | {mr[s]['rev_pct']:.0f}% | {sw[s]['sweep_pct']:.0f}% | {rt[s]['pct_range']:.0f}% | {filter_txt} |")
out.append("")

# Per session deep verdict
out.append("## 11. Anatomia operativa final\n")

for s in SESSIONS:
    rev = mr[s]["rev_pct"]
    trend_p = rt[s]["pct_trending"]
    rng_med = vol[s]["median"]
    sw_pct = sw[s]["sweep_pct"]
    real = trades_sess[s]
    f = best_filter[s]

    # Archetype
    if rev >= 60 and trend_p < 35:
        archetype = "MEAN-REVERSION pura"
        verdict = "Sistema actual encaixa perfectament."
    elif rev >= 50 and sw_pct >= 50:
        archetype = "REVERSIÓ amb sweeps"
        verdict = "Sistema actual + esperar confirmació de sweep abans d'entrar."
    elif trend_p >= 40:
        archetype = "TENDENCIAL"
        verdict = "Sistema actual sub-òptim. Cal mode trend-pullback."
    elif rev < 45:
        archetype = "WHIPSAW"
        verdict = "Cuidado: poca reversió natural. Multipliers conservadors o gate."
    else:
        archetype = "MIXT"
        verdict = "Operar amb cautela, multipliers conservadors."

    out.append(f"### {s}")
    out.append(f"- **Arquetip:** {archetype}")
    out.append(f"- **Range típic H4:** ${rng_med:.1f} (mean: {vol[s]['mean']:.1f}, p90: {vol[s]['p90']:.1f})")
    out.append(f"- **Reversió 50% en 12h:** {rev:.0f}% (sobre {mr[s]['trigger']} mostres ≥$20 range)")
    out.append(f"- **Sweep rate:** {sw_pct:.0f}% dels nous extrems es reverteixen")
    out.append(f"- **Estructura:** {rt[s]['pct_range']:.0f}% range / {trend_p:.0f}% trending")
    out.append(f"- **Bias direccional:** {runs[s]['pct_bull']:.0f}% bull / {runs[s]['pct_bear']:.0f}% bear")
    out.append(f"- **Filtre macro recomanat:** {f[0] if f[0]!='none' else 'cap fiable'} (edge {f[1]:+.0f}%)")
    out.append(f"- **Trades reals:** {real['opens']} OPENs · {real['avgs']} AVGs · {real['partials']} PARTIALs (en {len(set(e['utc'][:10] for e in trade_events))} dies de mostra)")
    out.append(f"- **Veredicte:** {verdict}")
    out.append("")

# Final recommendations
out.append("## 12. Recomanacions concretes per al sistema\n")
out.append("### A) Configuració de session_factor (ja implementat, valors a ajustar):\n")
baseline_med = vol["LONDON"]["median"]
out.append(f"Baseline LONDON: ${baseline_med:.1f}\n")
out.append("| Sessió | factor v1 actual | factor v3 (anàlisi 70d) | rao |")
out.append("|---|---:|---:|---|")
for s in SESSIONS:
    actual = {"ASIA":0.92,"LONDON":1.00,"OVERLAP":1.50,"NY":0.85,"DEAD":0.85}[s]
    new_factor = round(vol[s]["median"]/baseline_med, 2)
    out.append(f"| **{s}** | {actual} | **{new_factor}** | range med ${vol[s]['median']:.1f} vs LONDON ${baseline_med:.1f} |")
out.append("")

out.append("### B) Pesos de confluència macro per sessió:\n")
out.append("| Sessió | DXY weight | USDJPY weight | Notes |")
out.append("|---|---:|---:|---|")
for s in SESSIONS:
    e_d = edge_dxy[s]["aligned_continue_pct"] - edge_dxy[s]["counter_continue_pct"]
    e_j = edge_jpy[s]["aligned_continue_pct"] - edge_jpy[s]["counter_continue_pct"]
    # Translate edge to weight: edge>15 = 1.2, 5-15=1.0, <5=0.5, <0=0.3
    def edge2weight(e):
        if e >= 15: return 1.2
        elif e >= 5: return 1.0
        elif e >= -5: return 0.5
        else: return 0.3
    w_d = edge2weight(e_d); w_j = edge2weight(e_j)
    notes = []
    if e_d > 10: notes.append("DXY útil")
    if e_j > 10: notes.append("USDJPY útil")
    if e_d < 0: notes.append("DXY contraproductiu")
    if e_j < 0: notes.append("USDJPY contraproductiu")
    out.append(f"| **{s}** | {w_d} | {w_j} | {', '.join(notes) if notes else '-'} |")
out.append("")

# Limitations
out.append("## 13. Limitacions\n")
out.append(f"- Mostra H4: {len(xau_h4)} bars = ~70 dies. Per OVERLAP n=39 H4 — ajustat. Per DEAD n=26 — petit.")
out.append("- Mean reversion test usa 3 H4 bars = 12h lookforward — pot ser massa curt per moviments de news.")
out.append("- Trades reals només 7 dies operats (Apr 17-24). No ASIA dins de la mostra real (00-04 UTC).")
out.append("- M5 directe no inclòs — la velocitat intra-bar pot diferir.")
out.append("- US10Y i SPX no inclosos per economia de crides MCP.")
out.append("- Període Feb-Apr 2026 pot tenir biaixos de regime específics (cal validar amb una segona finestra més tard).\n")

out_path = DATA / "session_anatomy_v3.md"
out_path.write_text("\n".join(out), encoding="utf-8")
print(f"Wrote {out_path} ({out_path.stat().st_size} bytes)\n")

# Print summary table
print("=== SUMMARY (sorted by fitness score) ===\n")
print(f"{'SESS':<8} {'Score':>6} {'VolMed':>7} {'Rev%':>5} {'Sweep%':>7} {'Range%':>7} {'Trend%':>7} {'DXYedge':>8} {'JPYedge':>8} {'BestFilter':<15}")
for s in sorted(SESSIONS, key=lambda x: scores[x], reverse=True):
    e_d = edge_dxy[s]["aligned_continue_pct"] - edge_dxy[s]["counter_continue_pct"]
    e_j = edge_jpy[s]["aligned_continue_pct"] - edge_jpy[s]["counter_continue_pct"]
    f = best_filter[s]
    print(f"{s:<8} {scores[s]:>6.1f} {vol[s]['median']:>7.1f} {mr[s]['rev_pct']:>5.0f} {sw[s]['sweep_pct']:>7.0f} {rt[s]['pct_range']:>7.0f} {rt[s]['pct_trending']:>7.0f} {e_d:>+7.0f}% {e_j:>+7.0f}% {f[0]:<15}")

print("\n=== RECOMMENDED session_factor v3 (vs v1) ===\n")
print(f"{'SESS':<8} {'v1':>6} {'v3':>6} {'RangeMed':>10}")
for s in SESSIONS:
    actual = {"ASIA":0.92,"LONDON":1.00,"OVERLAP":1.50,"NY":0.85,"DEAD":0.85}[s]
    new_factor = round(vol[s]["median"]/baseline_med, 2)
    print(f"{s:<8} {actual:>6.2f} {new_factor:>6.2f} {vol[s]['median']:>10.1f}")
