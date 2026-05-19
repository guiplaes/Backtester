#!/usr/bin/env python3
"""Session anatomy analyzer — characterize each market session for XAUUSD
focused on the operational use case: M5 reversion scalps targeting 10-15 USD
moves with averaging.

Reads: xauusd_h1.json, xauusd_m15.json, dxy_h1.json
Writes: session_anatomy.md
"""
import json
import os
from datetime import datetime, timezone
from statistics import median, mean
from pathlib import Path

DATA = Path(__file__).resolve().parent

# ── Session bucketing (UTC hour) ──────────────────────────────────────
def session_of_hour(h):
    if 0 <= h < 7:   return "ASIA"
    if 7 <= h < 13:  return "LONDON"
    if 13 <= h < 16: return "OVERLAP"
    if 16 <= h < 21: return "NY"
    return "DEAD"

def session_of_ts(ts):
    return session_of_hour(datetime.fromtimestamp(ts, tz=timezone.utc).hour)

def hour_of_ts(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).hour

# ── Loaders ──────────────────────────────────────────────────────────
def load(name):
    with open(DATA / name, encoding="utf-8") as f:
        return json.load(f)

xau_h1 = load("xauusd_h1.json")["bars"]      # [t,o,h,l,c,v]
xau_m15 = load("xauusd_m15.json")["bars"]    # [t,o,h,l,c,v]
dxy_h1 = load("dxy_h1.json")["bars"]         # [t,o,h,l,c]   (volume always 0)

# ── Helpers ──────────────────────────────────────────────────────────
def pct(num, den):
    return (num / den * 100.0) if den else 0.0

def fmt(v, dp=2):
    if v is None: return "-"
    if isinstance(v, float): return f"{v:.{dp}f}"
    return str(v)

def stats_block(values):
    """Return min/p25/median/mean/p75/max for a list."""
    if not values: return None
    s = sorted(values)
    n = len(s)
    return {
        "n": n,
        "min": s[0],
        "p25": s[max(0, n // 4 - 1)],
        "median": s[n // 2],
        "mean": sum(s) / n,
        "p75": s[min(n - 1, 3 * n // 4)],
        "max": s[-1],
    }

# ── Per-session metrics ─────────────────────────────────────────────
def analyze_xauusd_h1():
    by_sess = {"ASIA": [], "LONDON": [], "OVERLAP": [], "NY": [], "DEAD": []}
    by_hour = {h: [] for h in range(24)}
    for b in xau_h1:
        t, o, h, l, c, v = b
        rng = h - l
        body = abs(c - o)
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l
        bullish = c > o
        sess = session_of_ts(t)
        hr = hour_of_ts(t)
        rec = {
            "rng": rng, "body": body, "upper": upper_wick, "lower": lower_wick,
            "bullish": bullish, "vol": v, "ts": t,
        }
        by_sess[sess].append(rec)
        by_hour[hr].append(rec)
    return by_sess, by_hour

def analyze_xauusd_m15():
    by_sess = {"ASIA": [], "LONDON": [], "OVERLAP": [], "NY": [], "DEAD": []}
    by_hour = {h: [] for h in range(24)}
    for b in xau_m15:
        t, o, h, l, c, v = b
        rng = h - l
        body = abs(c - o)
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l
        bullish = c > o
        sess = session_of_ts(t)
        hr = hour_of_ts(t)
        rec = {
            "rng": rng, "body": body, "upper": upper_wick, "lower": lower_wick,
            "bullish": bullish, "vol": v, "ts": t,
        }
        by_sess[sess].append(rec)
        by_hour[hr].append(rec)
    return by_sess, by_hour

# ── Reversion detection ─────────────────────────────────────────────
def detect_m15_reversions(min_move_usd=10.0):
    """A 'reversion' M15 candle: wick (one side) ≥ min_move and body in opposite
    direction. The wick measures the portion of move that reversed. Returns
    counts per session."""
    rev_by_sess = {"ASIA": 0, "LONDON": 0, "OVERLAP": 0, "NY": 0, "DEAD": 0}
    bars_by_sess = {"ASIA": 0, "LONDON": 0, "OVERLAP": 0, "NY": 0, "DEAD": 0}
    for b in xau_m15:
        t, o, h, l, c, v = b
        upper = h - max(o, c)
        lower = min(o, c) - l
        sess = session_of_ts(t)
        bars_by_sess[sess] += 1
        # Bull rejection: lower wick ≥ X AND close > open (bull body)
        # Bear rejection: upper wick ≥ X AND close < open (bear body)
        if (lower >= min_move_usd and c > o) or (upper >= min_move_usd and c < o):
            rev_by_sess[sess] += 1
    return rev_by_sess, bars_by_sess

# ── DXY correlation per session ─────────────────────────────────────
def dxy_xau_corr_per_session():
    # Index DXY by timestamp
    dxy_idx = {b[0]: b for b in dxy_h1}
    by_sess = {"ASIA": [], "LONDON": [], "OVERLAP": [], "NY": [], "DEAD": []}
    for b in xau_h1:
        t, o, h, l, c, v = b
        d = dxy_idx.get(t)
        if not d:
            continue
        xau_chg = c - o
        dxy_chg = d[4] - d[1]  # dxy close - open
        by_sess[session_of_ts(t)].append((xau_chg, dxy_chg))
    out = {}
    for sess, pairs in by_sess.items():
        if len(pairs) < 5:
            out[sess] = None
            continue
        n = len(pairs)
        mx = sum(p[0] for p in pairs) / n
        my = sum(p[1] for p in pairs) / n
        cov = sum((p[0] - mx) * (p[1] - my) for p in pairs)
        sxx = sum((p[0] - mx) ** 2 for p in pairs)
        syy = sum((p[1] - my) ** 2 for p in pairs)
        if sxx <= 0 or syy <= 0:
            out[sess] = None
            continue
        r = cov / (sxx ** 0.5 * syy ** 0.5)
        # Concordance: % of bars where XAU and DXY move in opposite direction
        # (the textbook negative correlation)
        concordant_neg = sum(1 for p in pairs if (p[0] > 0 and p[1] < 0) or (p[0] < 0 and p[1] > 0))
        out[sess] = {"n": n, "r": r, "neg_concord_pct": concordant_neg / n * 100}
    return out

# ── Compute everything ──────────────────────────────────────────────
h1_by_sess, h1_by_hour = analyze_xauusd_h1()
m15_by_sess, m15_by_hour = analyze_xauusd_m15()
rev10, bars_per_sess = detect_m15_reversions(10.0)
rev15, _ = detect_m15_reversions(15.0)
dxy_corr = dxy_xau_corr_per_session()

# ── Summary stats per session ──────────────────────────────────────
def session_summary(records, prefix=""):
    if not records: return {}
    rngs = [r["rng"] for r in records]
    bodies = [r["body"] for r in records]
    uppers = [r["upper"] for r in records]
    lowers = [r["lower"] for r in records]
    pct_bull = pct(sum(1 for r in records if r["bullish"]), len(records))
    return {
        "n": len(records),
        "range_med": median(rngs),
        "range_mean": mean(rngs),
        "range_p75": sorted(rngs)[3 * len(rngs) // 4] if len(rngs) >= 4 else max(rngs),
        "body_med": median(bodies),
        "wick_avg": (mean(uppers) + mean(lowers)) / 2,
        "pct_bull": pct_bull,
        "pct_range_ge_10": pct(sum(1 for r in rngs if r >= 10), len(rngs)),
        "pct_range_ge_15": pct(sum(1 for r in rngs if r >= 15), len(rngs)),
        "pct_range_ge_20": pct(sum(1 for r in rngs if r >= 20), len(rngs)),
    }

h1_summary = {s: session_summary(h1_by_sess[s]) for s in ("ASIA","LONDON","OVERLAP","NY","DEAD")}
m15_summary = {s: session_summary(m15_by_sess[s]) for s in ("ASIA","LONDON","OVERLAP","NY","DEAD")}

# ── Build markdown ─────────────────────────────────────────────────
out = []
out.append("# XAUUSD — Anatomia per sessió (operació M5 reversions $10-15)\n")
out.append(f"_Generat: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_\n")
out.append("**Mostres:**\n")
out.append(f"- XAUUSD H1: {len(xau_h1)} barres ({datetime.fromtimestamp(xau_h1[0][0], tz=timezone.utc).strftime('%Y-%m-%d')} → {datetime.fromtimestamp(xau_h1[-1][0], tz=timezone.utc).strftime('%Y-%m-%d')}, ~17 dies)")
out.append(f"- XAUUSD M15: {len(xau_m15)} barres (~3 dies fine-grain)")
out.append(f"- DXY H1: {len(dxy_h1)} barres (correlació)\n")
out.append("**Bucketing UTC:** ASIA=00-07 · LONDON=07-13 · OVERLAP=13-16 · NY=16-21 · DEAD=21-00\n")

# H1 table
out.append("## 1. Caràcter ampli per sessió (H1)\n")
out.append("| Sessió | n | Range med | Range mean | p75 | %≥10$ | %≥15$ | %≥20$ | %bull |")
out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
for s in ("ASIA","LONDON","OVERLAP","NY","DEAD"):
    d = h1_summary[s]
    if not d: continue
    out.append(f"| **{s}** | {d['n']} | {fmt(d['range_med'],1)} | {fmt(d['range_mean'],1)} | {fmt(d['range_p75'],1)} | {fmt(d['pct_range_ge_10'],0)}% | {fmt(d['pct_range_ge_15'],0)}% | {fmt(d['pct_range_ge_20'],0)}% | {fmt(d['pct_bull'],0)}% |")

out.append("\n_Lectura: a una hora qualsevol, % de probabilitat que el rang H1 superi N$. Si el % és baix, és difícil capturar reversions de 10-15$ en aquella sessió._\n")

# M15 reversion table
out.append("## 2. Densitat de reversions M15 (oportunitats reals)\n")
out.append("| Sessió | M15 bars | wick≥10$ rebuig | per hora | wick≥15$ rebuig | per hora |")
out.append("|---|---:|---:|---:|---:|---:|")
for s in ("ASIA","LONDON","OVERLAP","NY","DEAD"):
    n = bars_per_sess[s]
    r10 = rev10[s]
    r15 = rev15[s]
    h_per_sess = {"ASIA":7,"LONDON":6,"OVERLAP":3,"NY":5,"DEAD":3}[s]
    days = n / (4 * h_per_sess) if h_per_sess else 0  # 4 M15 bars/hour
    rev10_per_h = (r10 / (days * h_per_sess)) if days else 0
    rev15_per_h = (r15 / (days * h_per_sess)) if days else 0
    out.append(f"| **{s}** | {n} | {r10} ({pct(r10,n):.1f}%) | {fmt(rev10_per_h,2)} | {r15} ({pct(r15,n):.1f}%) | {fmt(rev15_per_h,2)} |")

out.append("\n_Una vela M15 amb wick ≥10$ i body contrari = rebuig clar. Aquestes són les espines del nostre joc. \"Per hora\" estima freqüència mitjana, no en hi ha cada hora exacta._\n")

# Hourly XAUUSD H1 breakdown
out.append("## 3. Granularitat horària XAUUSD (UTC)\n")
out.append("| Hora UTC | Sessió | n | Range med | %≥10$ | %≥15$ |")
out.append("|---:|---|---:|---:|---:|---:|")
for h in range(24):
    recs = h1_by_hour[h]
    if not recs: continue
    rngs = [r["rng"] for r in recs]
    p10 = pct(sum(1 for r in rngs if r >= 10), len(rngs))
    p15 = pct(sum(1 for r in rngs if r >= 15), len(rngs))
    out.append(f"| {h:02d} | {session_of_hour(h)} | {len(recs)} | {fmt(median(rngs),1)} | {fmt(p10,0)}% | {fmt(p15,0)}% |")

out.append("\n_La sessió no és una caixa uniforme — cada hora té caràcter propi. Les hores amb % ≥10 baix són les que costen més capturar reversions netes._\n")

# DXY correlation
out.append("## 4. Correlació amb DXY (XAU vs USD index, H1 by H1)\n")
out.append("| Sessió | n | r (Pearson) | %h XAU↑ ↔ DXY↓ |")
out.append("|---|---:|---:|---:|")
for s in ("ASIA","LONDON","OVERLAP","NY","DEAD"):
    d = dxy_corr.get(s)
    if not d:
        out.append(f"| {s} | - | - | - |")
        continue
    out.append(f"| **{s}** | {d['n']} | {fmt(d['r'],3)} | {fmt(d['neg_concord_pct'],0)}% |")

out.append("\n_r negatiu = inversa clàssica. Si Asia té r prop de 0, vol dir que XAU es mou amb dinàmica pròpia i DXY no és bon filtre allà. Si OVERLAP té r molt negatiu, llavors DXY a contracorrent és un fre fort._\n")

# Insight section per session
out.append("## 5. Anatomia operativa per sessió\n")

def sess_insight(s):
    d = h1_summary[s]
    md15 = m15_summary[s]
    n = bars_per_sess[s]
    r10 = rev10[s]
    r15 = rev15[s]
    dxy = dxy_corr.get(s)
    h_per_sess = {"ASIA":7,"LONDON":6,"OVERLAP":3,"NY":5,"DEAD":3}[s]
    days = n / (4 * h_per_sess) if h_per_sess else 0
    rev10_per_h = (r10 / (days * h_per_sess)) if days else 0

    return {
        "h1_range_med": d['range_med'],
        "h1_pct_ge_15": d['pct_range_ge_15'],
        "m15_range_med": md15['range_med'],
        "rev10_per_h": rev10_per_h,
        "rev15_per_h": (r15 / (days * h_per_sess)) if days else 0,
        "dxy_r": dxy['r'] if dxy else None,
        "pct_bull": d['pct_bull'],
    }

# Sub-section per session with concrete operational guidance
sess_text = {}

asia = sess_insight("ASIA")
london = sess_insight("LONDON")
overlap = sess_insight("OVERLAP")
ny = sess_insight("NY")
dead = sess_insight("DEAD")

out.append(f"### ASIA (00-07 UTC)")
out.append(f"- Range H1 mediana: **${asia['h1_range_med']:.1f}** | %hores ≥15$: **{asia['h1_pct_ge_15']:.0f}%** | rev≥10$ M15/hora: **{asia['rev10_per_h']:.2f}**")
out.append(f"- Correlació DXY: r = **{fmt(asia['dxy_r'],3) if asia['dxy_r'] is not None else 'n/a'}**")
out.append(f"- **Lectura operativa:** {'Range estret — molts intents de 10-15$ moriran. Buscar només zones extremes (highs/lows previs) amb confluència. Sweep+rebot és el patró net. Esperar moviment, no anticipar.' if asia['h1_range_med']<10 else 'Volatilitat suficient — operable amb cura. Range més ample del normal en aquesta finestra.'}")
out.append(f"- **Tàctica:** TP escalonat més curt ($6-10 al primer parcial). Averaging més espaiat (no alimentar el range cec). Esperar Tokyo open + sweep, no entrar a meitat de rang.\n")

out.append(f"### LONDON (07-13 UTC)")
out.append(f"- Range H1 mediana: **${london['h1_range_med']:.1f}** | %hores ≥15$: **{london['h1_pct_ge_15']:.0f}%** | rev≥10$ M15/hora: **{london['rev10_per_h']:.2f}**")
out.append(f"- Correlació DXY: r = **{fmt(london['dxy_r'],3) if london['dxy_r'] is not None else 'n/a'}**")
out.append(f"- **Lectura operativa:** Sessió on s'estableix tendència del dia. {'Volatilitat alta i sostinguda — entorn natural del nostre sistema.' if london['h1_range_med']>=10 else 'Volatilitat lleugerament reduïda en aquesta finestra.'}")
out.append(f"- **Tàctica:** Operar normal. R formula actual calibrada per aquesta sessió. Atenció primera hora (07-08 UTC) — pot fer expansió ràpida. DXY és confluència fiable aquí.\n")

out.append(f"### OVERLAP (13-16 UTC)")
out.append(f"- Range H1 mediana: **${overlap['h1_range_med']:.1f}** | %hores ≥15$: **{overlap['h1_pct_ge_15']:.0f}%** | rev≥10$ M15/hora: **{overlap['rev10_per_h']:.2f}**")
out.append(f"- Correlació DXY: r = **{fmt(overlap['dxy_r'],3) if overlap['dxy_r'] is not None else 'n/a'}**")
out.append(f"- **Lectura operativa:** Màxima liquiditat. NY entra abans de London tancar. Risc: news US a 14:30 UTC.")
out.append(f"- **Tàctica:** Major densitat d'oportunitats però també major risc de moviments-headfake. R formula ja amplia coef aquí. Atenció especial al gate de news.\n")

out.append(f"### NY (16-21 UTC)")
out.append(f"- Range H1 mediana: **${ny['h1_range_med']:.1f}** | %hores ≥15$: **{ny['h1_pct_ge_15']:.0f}%** | rev≥10$ M15/hora: **{ny['rev10_per_h']:.2f}**")
out.append(f"- Correlació DXY: r = **{fmt(ny['dxy_r'],3) if ny['dxy_r'] is not None else 'n/a'}**")
out.append(f"- **Lectura operativa:** {'Continuació o reversió de London. Sovint primera hora (16-17 UTC) és la més agressiva.' if ny['h1_range_med']>=10 else 'Volatilitat decent.'} Cap a les 20 UTC, fade.")
out.append(f"- **Tàctica:** Operar normal però vigilar últim hora pre-DEAD (20-21 UTC) — moviments solen ser stop hunts en lloc de tendència.\n")

out.append(f"### DEAD (21-00 UTC)")
out.append(f"- Range H1 mediana: **${dead['h1_range_med']:.1f}** | %hores ≥15$: **{dead['h1_pct_ge_15']:.0f}%** | rev≥10$ M15/hora: **{dead['rev10_per_h']:.2f}**")
out.append(f"- Correlació DXY: r = **{fmt(dead['dxy_r'],3) if dead['dxy_r'] is not None else 'n/a'}**")
out.append(f"- **Lectura operativa:** Sessió desolada. Range probablement molt estret, spread ample real (fora de la mostra de bars).")
out.append(f"- **Tàctica:** No operar setups nous tret que sigui un nivell extrem amb confluència màxima. La majoria de \"reversions\" són soroll de baixa liquiditat.\n")

# Conclusions / recommendations
out.append("## 6. Recomanacions per al sistema\n")
out.append("### Què cal canviar al codi")
out.append("**A) Filtre de sessió per noves entrades:**")
out.append("- Bloquejar entrades durant DEAD si range_h1_actual < 8$ (poc material per reversions)")
out.append("- Permetre entrades a ASIA però amb TP escalonat més curt (primer parcial al 50% de R, no 100%)\n")

out.append("**B) R-formula condicional per sessió:**")
asia_factor = asia['h1_range_med'] / london['h1_range_med'] if london['h1_range_med'] > 0 else 1
out.append(f"- Asia: factor R ≈ {fmt(asia_factor,2)} respecte London (range {fmt(asia['h1_range_med'],1)} vs {fmt(london['h1_range_med'],1)})")
out.append(f"- Si extrapolem: TP de London = X → TP equivalent Asia ≈ X × {fmt(asia_factor,2)}\n")

out.append("**C) DFMO sensitivity per sessió:**")
out.append(f"- DXY r en Asia: {fmt(asia['dxy_r'],3) if asia['dxy_r'] is not None else 'n/a'} → si baix, DFMO no necessita confluència DXY")
out.append(f"- DXY r en Overlap/NY: si fort negatiu, DFMO pot reforçar-se amb confirmació DXY\n")

out.append("**D) Hores explícitament problemàtiques (range típic <10$):**")
weak_hours = [h for h in range(24) if h1_by_hour[h] and median([r["rng"] for r in h1_by_hour[h]]) < 10]
if weak_hours:
    out.append(f"- Hores UTC: {', '.join(f'{h:02d}' for h in weak_hours)} — considerar gate específic\n")
else:
    out.append("- Cap hora amb range típic <10$ a la mostra actual\n")

out.append("## 7. Limitacions de l'anàlisi")
out.append(f"- Finestra: {len(xau_h1)} bars H1 = ~17 dies. Mostra mínima per estadística robusta = 30+ dies.")
out.append("- Falten USD/JPY (driver Asia), US10Y (yields), VIX (risc on/off).")
out.append("- M5 directe no inclòs (dades més denses → 100+ crides MCP). M15 com a proxi de detecció de reversions.")
out.append("- Cal validar amb 30-60 dies addicionals per confirmar patrons (especialment ASIA on només tenim ~17 sessions).\n")

out.append("## 8. Què fa falta per la segona iteració")
out.append("1. **Ampliar a 60-90 dies** scrolljant TV → 5-8 batches més per asset")
out.append("2. **Afegir USD/JPY H1** — driver clar d'Asia, aclarirà comportament de la sessió")
out.append("3. **M5 ATR per sessió** — finestra més curta, només 5-10 dies, però resolució nostra")
out.append("4. **Cross-referenciar amb el journal de trades reals** — quins trades hem obert per sessió, win-rate, expectancy real")

out_path = DATA / "session_anatomy.md"
out_path.write_text("\n".join(out), encoding="utf-8")
print(f"Wrote {out_path} ({out_path.stat().st_size} bytes)")
print()
print("=== KEY NUMBERS ===")
print(f"ASIA   H1 med range: ${asia['h1_range_med']:.1f}  | rev≥10/h: {asia['rev10_per_h']:.2f}  | DXY r: {fmt(asia['dxy_r'],3)}")
print(f"LONDON H1 med range: ${london['h1_range_med']:.1f}  | rev≥10/h: {london['rev10_per_h']:.2f}  | DXY r: {fmt(london['dxy_r'],3)}")
print(f"OVRLAP H1 med range: ${overlap['h1_range_med']:.1f}  | rev≥10/h: {overlap['rev10_per_h']:.2f}  | DXY r: {fmt(overlap['dxy_r'],3)}")
print(f"NY     H1 med range: ${ny['h1_range_med']:.1f}  | rev≥10/h: {ny['rev10_per_h']:.2f}  | DXY r: {fmt(ny['dxy_r'],3)}")
print(f"DEAD   H1 med range: ${dead['h1_range_med']:.1f}  | rev≥10/h: {dead['rev10_per_h']:.2f}  | DXY r: {fmt(dead['dxy_r'],3)}")
