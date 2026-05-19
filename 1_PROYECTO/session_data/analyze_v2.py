#!/usr/bin/env python3
"""Session anatomy v2 — beyond volatility.

Measures the dimensions that decide if our mean-reversion-scalp system fits
each session: trend persistence, mean reversion strength, sweep rate,
range-vs-trend ratio, and macro confluence efficacy.

Reads existing JSON files from session_data/. No new API calls.
Writes session_anatomy_v2.md.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import median, mean
from collections import defaultdict

DATA = Path(__file__).resolve().parent

def load(name):
    return json.load(open(DATA / name, encoding="utf-8"))["bars"]

xau_h1 = load("xauusd_h1.json")
xau_m15 = load("xauusd_m15.json")
dxy_h1 = load("dxy_h1.json")

# ── Session bucketing ──────────────────────────────────────────────────
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

def date_of(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")

def pct(num, den):
    return (num / den * 100.0) if den else 0.0

def fmt(v, dp=2):
    if v is None: return "-"
    return f"{v:.{dp}f}" if isinstance(v, float) else str(v)

SESSIONS = ("ASIA","LONDON","OVERLAP","NY","DEAD")

# ── 1. Trend persistence ──────────────────────────────────────────────
# Run-length of consecutive same-direction closes per session.
def trend_persistence():
    by_sess = {s: [] for s in SESSIONS}      # collect run lengths
    by_sess_bias = {s: {"bull": 0, "bear": 0, "doji": 0} for s in SESSIONS}
    # Walk H1 bars in order, counting consecutive direction within session+day.
    # Reset run when session changes.
    current_run = 0
    current_dir = None
    current_sess = None
    current_day = None
    for b in xau_h1:
        t, o, h, l, c, v = b
        sess = session_of_ts(t)
        day = date_of(t)
        body = c - o
        d = "bull" if body > 0.5 else ("bear" if body < -0.5 else "doji")
        by_sess_bias[sess][d] += 1

        # Reset run when crossing session/day boundary
        if (current_sess, current_day) != (sess, day):
            if current_run > 0 and current_sess:
                by_sess[current_sess].append(current_run)
            current_run = 1 if d != "doji" else 0
            current_dir = d
            current_sess = sess
            current_day = day
            continue

        if d == current_dir and d != "doji":
            current_run += 1
        else:
            if current_run > 0:
                by_sess[sess].append(current_run)
            current_run = 1 if d != "doji" else 0
            current_dir = d

    if current_run > 0 and current_sess:
        by_sess[current_sess].append(current_run)

    out = {}
    for s in SESSIONS:
        runs = by_sess[s]
        bias = by_sess_bias[s]
        total = sum(bias.values())
        out[s] = {
            "n_runs": len(runs),
            "median_run": median(runs) if runs else 0,
            "max_run": max(runs) if runs else 0,
            "pct_runs_ge3": pct(sum(1 for r in runs if r >= 3), len(runs)),
            "pct_bull": pct(bias["bull"], total),
            "pct_bear": pct(bias["bear"], total),
            "directional_bias": bias["bull"] - bias["bear"],  # net
        }
    return out


# ── 2. Mean reversion strength ────────────────────────────────────────
# After an H1 bar with range ≥ threshold, does price retrace ≥ X% within
# next K hours? Measures how reliably the market gives back recent moves.
def mean_reversion(threshold_range=15.0, retrace_pct=0.5, lookforward_hours=3):
    by_sess = {s: {"trigger": 0, "retraced": 0} for s in SESSIONS}
    n = len(xau_h1)
    for i in range(n - lookforward_hours):
        b = xau_h1[i]
        t, o, h, l, c, v = b
        rng = h - l
        if rng < threshold_range:
            continue
        sess = session_of_ts(t)
        # Direction of the move: dominant push from open to high or low
        bull_push = h - o
        bear_push = o - l
        if bull_push >= bear_push:
            # The bar pushed up. Did price retrace by retrace_pct in next K?
            target = h - rng * retrace_pct
            future_low = min(xau_h1[j][3] for j in range(i+1, i+1+lookforward_hours))
            retraced = future_low <= target
        else:
            target = l + rng * retrace_pct
            future_high = max(xau_h1[j][2] for j in range(i+1, i+1+lookforward_hours))
            retraced = future_high >= target
        by_sess[sess]["trigger"] += 1
        if retraced:
            by_sess[sess]["retraced"] += 1
    out = {}
    for s in SESSIONS:
        d = by_sess[s]
        out[s] = {
            "trigger_count": d["trigger"],
            "retraced": d["retraced"],
            "rev_rate_pct": pct(d["retraced"], d["trigger"]),
        }
    return out


# ── 3. Sweep rate ─────────────────────────────────────────────────────
# When an H1 bar makes a new high/low vs previous, how often does it close
# back inside the previous bar's range? (= sweep+reversion = fakeout)
def sweep_rate():
    by_sess = {s: {"new_high": 0, "high_swept": 0, "new_low": 0, "low_swept": 0} for s in SESSIONS}
    for i in range(1, len(xau_h1)):
        prev = xau_h1[i-1]
        cur = xau_h1[i]
        t, o, h, l, c, v = cur
        prev_h = prev[2]
        prev_l = prev[3]
        sess = session_of_ts(t)
        if h > prev_h:
            by_sess[sess]["new_high"] += 1
            if c < prev_h:  # closed back below = sweep failed
                by_sess[sess]["high_swept"] += 1
        if l < prev_l:
            by_sess[sess]["new_low"] += 1
            if c > prev_l:
                by_sess[sess]["low_swept"] += 1
    out = {}
    for s in SESSIONS:
        d = by_sess[s]
        nh, ns_h = d["new_high"], d["high_swept"]
        nl, ns_l = d["new_low"], d["low_swept"]
        total_breaks = nh + nl
        total_swept = ns_h + ns_l
        out[s] = {
            "new_extremes": total_breaks,
            "swept_back": total_swept,
            "sweep_pct": pct(total_swept, total_breaks),
            "high_sweep_pct": pct(ns_h, nh),
            "low_sweep_pct": pct(ns_l, nl),
        }
    return out


# ── 4. Range-vs-trend ratio ──────────────────────────────────────────
# Per session-day: |close-open|/range. >0.7 = trending bar, <0.3 = range/wicky.
# Aggregate per session across all bars (not just sessions as a unit but
# per-bar classification).
def range_trend_ratio():
    by_sess = {s: {"trending": 0, "range": 0, "mixed": 0, "n": 0, "ratios": []} for s in SESSIONS}
    for b in xau_h1:
        t, o, h, l, c, v = b
        rng = h - l
        if rng < 1.0:
            continue
        body_ratio = abs(c - o) / rng
        sess = session_of_ts(t)
        by_sess[sess]["n"] += 1
        by_sess[sess]["ratios"].append(body_ratio)
        if body_ratio > 0.7:
            by_sess[sess]["trending"] += 1
        elif body_ratio < 0.3:
            by_sess[sess]["range"] += 1
        else:
            by_sess[sess]["mixed"] += 1
    out = {}
    for s in SESSIONS:
        d = by_sess[s]
        n = d["n"]
        out[s] = {
            "n": n,
            "pct_trending": pct(d["trending"], n),
            "pct_range": pct(d["range"], n),
            "pct_mixed": pct(d["mixed"], n),
            "median_body_ratio": median(d["ratios"]) if d["ratios"] else 0,
        }
    return out


# ── 5. Macro confluence efficacy ─────────────────────────────────────
# When XAU direction aligns with DXY-inverse (XAU↑ + DXY↓ or XAU↓ + DXY↑),
# does XAU continue in that direction next hour vs when it doesn't align?
def macro_confluence():
    dxy_idx = {b[0]: b for b in dxy_h1}
    by_sess = {s: {"aligned": {"continued": 0, "reversed": 0},
                   "counter":  {"continued": 0, "reversed": 0}} for s in SESSIONS}
    for i in range(len(xau_h1) - 1):
        cur = xau_h1[i]
        nxt = xau_h1[i+1]
        t = cur[0]
        d = dxy_idx.get(t)
        if not d:
            continue
        xau_dir = 1 if cur[4] > cur[1] else (-1 if cur[4] < cur[1] else 0)
        dxy_dir = 1 if d[4] > d[1] else (-1 if d[4] < d[1] else 0)
        if xau_dir == 0 or dxy_dir == 0:
            continue
        next_dir = 1 if nxt[4] > nxt[1] else (-1 if nxt[4] < nxt[1] else 0)
        if next_dir == 0:
            continue
        sess = session_of_ts(t)
        # Aligned = inverse correlation (XAU up + DXY down OR vice versa)
        is_aligned = (xau_dir != dxy_dir)
        bucket = "aligned" if is_aligned else "counter"
        if next_dir == xau_dir:
            by_sess[sess][bucket]["continued"] += 1
        else:
            by_sess[sess][bucket]["reversed"] += 1
    out = {}
    for s in SESSIONS:
        d = by_sess[s]
        a_total = d["aligned"]["continued"] + d["aligned"]["reversed"]
        c_total = d["counter"]["continued"] + d["counter"]["reversed"]
        out[s] = {
            "n_aligned": a_total,
            "n_counter": c_total,
            "aligned_continue_pct": pct(d["aligned"]["continued"], a_total),
            "counter_continue_pct": pct(d["counter"]["continued"], c_total),
        }
    return out


# ── 6. Velocity ($/minute) ──────────────────────────────────────────
def velocity():
    by_sess = {s: [] for s in SESSIONS}
    for b in xau_h1:
        t, o, h, l, c, v = b
        rng = h - l
        sess = session_of_ts(t)
        # H1 range / 60 min, but really useful is movement-per-hour
        by_sess[sess].append(rng / 60.0)
    return {s: {"median_per_min": median(v_list) if v_list else 0,
                "max_per_min": max(v_list) if v_list else 0}
            for s, v_list in by_sess.items()}


# ── 7. Hour-of-day directional bias ────────────────────────────────
def hourly_bias():
    by_hour = defaultdict(lambda: {"bull": 0, "bear": 0, "doji": 0, "ranges": []})
    for b in xau_h1:
        t, o, h, l, c, v = b
        hr = hour_of(t)
        body = c - o
        if body > 0.5:
            by_hour[hr]["bull"] += 1
        elif body < -0.5:
            by_hour[hr]["bear"] += 1
        else:
            by_hour[hr]["doji"] += 1
        by_hour[hr]["ranges"].append(h - l)
    out = {}
    for h in sorted(by_hour):
        d = by_hour[h]
        total = d["bull"] + d["bear"] + d["doji"]
        out[h] = {
            "bull_pct": pct(d["bull"], total),
            "bear_pct": pct(d["bear"], total),
            "median_range": median(d["ranges"]) if d["ranges"] else 0,
            "bias": "🟢" if d["bull"] > d["bear"] * 1.3 else "🔴" if d["bear"] > d["bull"] * 1.3 else "⚖️",
        }
    return out


# ── Run all ──────────────────────────────────────────────────────────
trend = trend_persistence()
revstr = mean_reversion()
sweeps = sweep_rate()
rt_ratio = range_trend_ratio()
macro = macro_confluence()
vel = velocity()
bias_h = hourly_bias()

# ── Build output ─────────────────────────────────────────────────────
out = []
out.append("# XAUUSD — Anatomia per sessió v2 (què fa el preu, no només quant es mou)\n")
out.append(f"_Generat: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_\n")
out.append(f"**Mostres:** {len(xau_h1)} barres H1 (~17 dies). Sample petit per OVERLAP — interpretar amb cautela.\n")
out.append("**Bucketing UTC:** ASIA=00-07 · LONDON=07-13 · OVERLAP=13-16 · NY=16-21 · DEAD=21-00\n")

# 1. Trend persistence
out.append("## 1. Persistència de tendència\n")
out.append("_Quants closes consecutius en la mateixa direcció? Sessions amb runs llargs = trends. Runs curts = whipsaw/range._\n")
out.append("| Sessió | n runs | run mediana | run màx | %runs≥3 | %bull | %bear | bias net |")
out.append("|---|---:|---:|---:|---:|---:|---:|---:|")
for s in SESSIONS:
    d = trend[s]
    bias = d["directional_bias"]
    bias_tag = f"+{bias}" if bias > 0 else str(bias)
    out.append(f"| **{s}** | {d['n_runs']} | {d['median_run']} | {d['max_run']} | {fmt(d['pct_runs_ge3'],0)}% | {fmt(d['pct_bull'],0)}% | {fmt(d['pct_bear'],0)}% | {bias_tag} |")

out.append("\n_Lectura clau: alta % de runs ≥3 = sessió tendencial (dolent per averaging contra). Baixa = mean-revertable (bo per nosaltres)._\n")

# 2. Mean reversion strength
out.append("## 2. Reversió estructural (després d'un moviment, torna?)\n")
out.append(f"_Després d'una barra H1 amb range ≥15$, el preu retrocedeix ≥50% en les properes 3h?_\n")
out.append("| Sessió | n triggers | retracen ≥50% | % reversió |")
out.append("|---|---:|---:|---:|")
for s in SESSIONS:
    d = revstr[s]
    out.append(f"| **{s}** | {d['trigger_count']} | {d['retraced']} | **{fmt(d['rev_rate_pct'],0)}%** |")

out.append("\n_>60% = clarament reversiu, sistema actual encaixa. <40% = trending, promediar contra-tendència = perdre._\n")

# 3. Sweep rate
out.append("## 3. Sweep rate (% de breaks que són fakeouts)\n")
out.append("_Quan H1 fa nou high/low respecte l'anterior, tanca DINS del rang anterior? = sweep + revert._\n")
out.append("| Sessió | n nous extrems | swept back | % sweep | %high sweep | %low sweep |")
out.append("|---|---:|---:|---:|---:|---:|")
for s in SESSIONS:
    d = sweeps[s]
    out.append(f"| **{s}** | {d['new_extremes']} | {d['swept_back']} | **{fmt(d['sweep_pct'],0)}%** | {fmt(d['high_sweep_pct'],0)}% | {fmt(d['low_sweep_pct'],0)}% |")

out.append("\n_Sweep alta = la majoria de breaks són fakeouts → esperar retorn abans d'entrar = bona estratègia. Sweep baixa = breaks reals → entrar al break, no fade._\n")

# 4. Range vs trend
out.append("## 4. Estructura: trending vs range\n")
out.append("_Per cada barra H1: |close-open|/range. >0.7=trending, <0.3=range/wicky._\n")
out.append("| Sessió | n | %trending | %range | %mixed | body ratio mediana |")
out.append("|---|---:|---:|---:|---:|---:|")
for s in SESSIONS:
    d = rt_ratio[s]
    out.append(f"| **{s}** | {d['n']} | {fmt(d['pct_trending'],0)}% | {fmt(d['pct_range'],0)}% | {fmt(d['pct_mixed'],0)}% | {fmt(d['median_body_ratio'],2)} |")

out.append("\n_Range alt + body ratio baix = entorn ideal nostre (price camina amb wicks, retorna). Trending alt = cuidado, sistema actual no està fet per això._\n")

# 5. Macro confluence
out.append("## 5. Eficàcia de la confluència DXY\n")
out.append("_Quan XAU↑ amb DXY↓ (alineat amb correlació inversa), continua en aquesta direcció l'hora següent? Comparat amb counter (XAU↑ + DXY↑)._\n")
out.append("| Sessió | n alineats | continuen | n counter | continuen | edge alineat |")
out.append("|---|---:|---:|---:|---:|---:|")
for s in SESSIONS:
    d = macro[s]
    edge = d["aligned_continue_pct"] - d["counter_continue_pct"]
    edge_tag = f"+{fmt(edge,0)}%" if edge > 0 else f"{fmt(edge,0)}%"
    out.append(f"| **{s}** | {d['n_aligned']} | {fmt(d['aligned_continue_pct'],0)}% | {d['n_counter']} | {fmt(d['counter_continue_pct'],0)}% | **{edge_tag}** |")

out.append("\n_Edge positiu = DXY com a confluència aporta valor. Edge ~0 = no aporta. Edge negatiu = DXY soroll (no fiar-se)._\n")

# 6. Velocity
out.append("## 6. Velocitat ($/minut)\n")
out.append("| Sessió | mediana $/min | màx $/min |")
out.append("|---|---:|---:|")
for s in SESSIONS:
    d = vel[s]
    out.append(f"| **{s}** | {fmt(d['median_per_min'],3)} | {fmt(d['max_per_min'],3)} |")

out.append("\n_Velocitat alta = FAST engine (3s) pot arribar tard. Sistema actual es calibra implícitament a una velocitat — sessions a velocitat molt diferent poden necessitar parametritzar el FAST._\n")

# 7. Hourly bias
out.append("## 7. Direccionalitat per hora UTC\n")
out.append("_Quines hores tendeixen a tirar amunt vs avall? Bias significatiu (≥30% asimetria)._\n")
out.append("| Hora UTC | Sessió | %bull | %bear | bias | range med |")
out.append("|---:|---|---:|---:|:---:|---:|")
for h in sorted(bias_h):
    d = bias_h[h]
    out.append(f"| {h:02d} | {session_of_hour(h)} | {fmt(d['bull_pct'],0)}% | {fmt(d['bear_pct'],0)}% | {d['bias']} | {fmt(d['median_range'],1)} |")

out.append("\n_Si una hora té bias clar consistent, és informació tradeable._\n")

# ── Synthesis ──────────────────────────────────────────────────────
out.append("## 8. Veredicte: pot el mateix sistema operar a totes les sessions?\n")

# Composite "system fitness" per session
def fitness_score(s):
    """Higher = better fit for our mean-reversion-scalp system."""
    rev = revstr[s]["rev_rate_pct"]
    rng_pct = rt_ratio[s]["pct_range"]
    sweep = sweeps[s]["sweep_pct"]
    trend_runs = trend[s]["pct_runs_ge3"]
    # Reversion good, range good, sweep good, trend runs bad
    score = (rev + rng_pct + sweep) / 3 - trend_runs * 0.5
    return score

scores = {s: fitness_score(s) for s in SESSIONS}

out.append("**Score de fit del sistema** (mean-reversion + range + sweeps menys runs trending):\n")
out.append("| Sessió | Score | Diagnòstic |")
out.append("|---|---:|---|")
for s in sorted(SESSIONS, key=lambda x: scores[x], reverse=True):
    sc = scores[s]
    if sc > 50:
        diag = "✅ Sistema actual encaixa bé"
    elif sc > 30:
        diag = "⚠️ Sistema funciona amb adaptacions"
    else:
        diag = "❌ Sistema actual no encaixa — caldrà canvi de mode"
    out.append(f"| **{s}** | {fmt(sc,1)} | {diag} |")

out.append("")

# Per-session deep verdict
out.append("### Diagnòstic operatiu detallat\n")

for s in SESSIONS:
    rev = revstr[s]
    rt = rt_ratio[s]
    sw = sweeps[s]
    tr = trend[s]
    mc = macro[s]

    out.append(f"#### {s}")
    out.append(f"- **Reversió (>50% retrace en 3h):** {fmt(rev['rev_rate_pct'],0)}% sobre {rev['trigger_count']} mostres")
    out.append(f"- **Estructura:** {fmt(rt['pct_range'],0)}% range / {fmt(rt['pct_trending'],0)}% trending / {fmt(rt['pct_mixed'],0)}% mixed")
    out.append(f"- **Sweep rate:** {fmt(sw['sweep_pct'],0)}% dels nous extrems es reverteixen")
    out.append(f"- **Trend persistence:** {fmt(tr['pct_runs_ge3'],0)}% dels runs són ≥3 closes")
    out.append(f"- **DXY edge:** {fmt(mc['aligned_continue_pct']-mc['counter_continue_pct'],0)}% (alineat continua menys counter continua)")

    # Operational verdict
    rev_pct = rev['rev_rate_pct']
    range_pct = rt['pct_range']
    trend_pct = rt['pct_trending']
    sweep_pct = sw['sweep_pct']

    if rev_pct >= 60 and trend_pct < 35:
        verdict = "**Sistema actual encaixa**: alta reversió + baixa tendència. Operar normal."
    elif rev_pct >= 50 and sweep_pct >= 50:
        verdict = "**Funciona amb adaptacions**: reversió decent + sweeps freqüents. Esperar confirmació de retorn abans d'entrar és clau."
    elif trend_pct >= 50:
        verdict = "**Risc real**: estructura tendencial. Promediar contra-tendència aquí = perdre. Cal mode trend-pullback (entrar amb tendència en pullbacks)."
    elif rev_pct < 40:
        verdict = "**Risc**: poca reversió natural. El sistema espera que el preu torni i sovint no torna. Considerar gate o mode diferent."
    else:
        verdict = "**Mixt**: caràcter ambigu. Operar amb cautela, multipliers conservadors."

    out.append(f"- **Veredicte:** {verdict}\n")

# Final synthesis
out.append("## 9. Conclusió\n")
out.append("Comparant les dimensions reals (no només volatilitat):\n")

# Identify sessions by archetype
archetypes = {}
for s in SESSIONS:
    rev = revstr[s]['rev_rate_pct']
    trend_p = rt_ratio[s]['pct_trending']
    if rev >= 60 and trend_p < 35:
        archetypes[s] = "MEAN-REVERSION (sistema actual ideal)"
    elif trend_p >= 50:
        archetypes[s] = "TRENDING (sistema actual sub-òptim)"
    elif rev >= 45 and trend_p < 50:
        archetypes[s] = "MIXT (sistema actual amb ajustos)"
    else:
        archetypes[s] = "WHIPSAW (cuidado especial)"

out.append("| Sessió | Arquetip de mercat | Mode operatiu suggerit |")
out.append("|---|---|---|")
for s in SESSIONS:
    a = archetypes[s]
    if "MEAN-REVERSION" in a:
        mode = "Sistema actual: zones + averaging + parcials"
    elif "TRENDING" in a:
        mode = "**Trend-pullback**: només entrades amb tendència, averaging a pullbacks dins-tendència"
    elif "MIXT" in a:
        mode = "Sistema actual + multipliers conservadors + esperar sweep+retorn"
    else:
        mode = "Wait-and-see: només setups d'alta convicció"
    out.append(f"| **{s}** | {a} | {mode} |")

out.append("\n### Resposta a la pregunta original")
out.append("**Pot el mateix sistema operar a qualsevol sessió?** Resposta basada en les dades:")
out.append("")
mr_sessions = [s for s,a in archetypes.items() if "MEAN-REVERSION" in a]
trending_sessions = [s for s,a in archetypes.items() if "TRENDING" in a]
mixed_sessions = [s for s,a in archetypes.items() if "MIXT" in a]
whipsaw_sessions = [s for s,a in archetypes.items() if "WHIPSAW" in a]

if mr_sessions:
    out.append(f"- **Sí** per: {', '.join(mr_sessions)} — caràcter de mean-reversion natural")
if mixed_sessions:
    out.append(f"- **Amb ajustos** per: {', '.join(mixed_sessions)} — el sistema funciona amb multipliers conservadors i confirmació")
if trending_sessions:
    out.append(f"- **No directament** per: {', '.join(trending_sessions)} — caràcter trending requereix mode diferent")
if whipsaw_sessions:
    out.append(f"- **Amb cautela** per: {', '.join(whipsaw_sessions)} — caràcter whipsaw, només alta convicció")

out.append("\n## 10. Limitacions\n")
out.append("- 17 dies = mostra petita. OVERLAP n=39 H1 bars. Cal validar amb 60+ dies.")
out.append("- Mean reversion test usa lookforward 3h — pot ser massa curt per moviments grans.")
out.append("- Trend persistence parteix pel canvi de sessió/dia → fragmenta runs reals que creuen sessions.")
out.append("- Macro confluence DXY només; afegint USDJPY i US10Y aclariria la imatge especialment a Asia.")
out.append("- No es mesura velocitat de moviment intra-bar (M5 directe ho aclariria).\n")

out_path = DATA / "session_anatomy_v2.md"
out_path.write_text("\n".join(out), encoding="utf-8")
print(f"Wrote {out_path} ({out_path.stat().st_size} bytes)")
print()
print("=== HEADLINE NUMBERS ===\n")
print(f"{'SESSION':<8} {'Rev%':>5} {'Range%':>7} {'Trend%':>7} {'Sweep%':>7} {'Runs≥3%':>8} {'DXYedge':>8} {'Score':>6}")
for s in SESSIONS:
    print(f"{s:<8} {revstr[s]['rev_rate_pct']:>5.0f} {rt_ratio[s]['pct_range']:>7.0f} {rt_ratio[s]['pct_trending']:>7.0f} {sweeps[s]['sweep_pct']:>7.0f} {trend[s]['pct_runs_ge3']:>8.0f} {macro[s]['aligned_continue_pct']-macro[s]['counter_continue_pct']:>+7.0f}% {scores[s]:>6.1f}")

print()
print("=== ARCHETYPE PER SESSION ===\n")
for s in SESSIONS:
    print(f"  {s:<8} → {archetypes[s]}")
