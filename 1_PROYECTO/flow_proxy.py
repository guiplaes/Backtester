"""flow_proxy.py — Flux dual: spot (retail) + futures CME (institucional).

Des de la subscripció CME a TradingView (€7/mes, activa 2026-05-03), el
sistema rep DOS feeds paral·lels:

  - SPOT (OANDA:XAUUSD): tick-volume del broker. Cada "vol" és un canvi
    de quote, no un contracte. Útil com a proxy retail d'activitat.
  - FUTURES (COMEX:GC1!): contractes reals (1 contracte = 100 oz).
    Reflecteix flux INSTITUCIONAL — hedge funds, bancs, dealers,
    dipositaris, arbitratge físic.

Aquest mòdul calcula les MATEIXES tres mètriques (volum burst, CMF, OBV)
sobre els dos feeds i les exposa al payload de l'INDICATOR. La
interpretació la fa l'LLM dins del camp `condition` — aquí no hi ha cap
regla de decisió.

A més, calcula el spread spot−futures (contango normal ~+$10-12). Una
eixamplada amb volum institucional indica pressió real; una compressió
forta sol precedir mean-reversion.

Filosofia: dades, no booleans. L'LLM compara les dues fonts i articula
qualitativament al `condition`. Quan spot i futures es contradiuen,
AIXÒ és el senyal — el LLM rep la informació crua i ho descobreix.

Frames usats per font:
  - M5: bursts de volum (5min — equilibri noise/signal)
  - M15: CMF (Chaikin Money Flow)
  - H1: OBV trend + divergència estructural amb preu

Tots els camps poden faltar (mercat tancat, fetch fallit, històric
insuficient) — l'LLM els ignora.
"""
from __future__ import annotations

from typing import Optional


def _safe_float(x, default=0.0) -> float:
    try:
        return float(x) if x is not None else default
    except (TypeError, ValueError):
        return default


def _volume_burst_m5(bars_m5) -> dict:
    """Última barra M5 tancada vs mitjana de les 72 barres prèvies (~6h).

    Retorna {pct_vs_6h_avg, zscore, contracts_last_bar} o {} si no hi
    ha prou dades. `contracts_last_bar` és el volum cru de la última
    barra tancada — útil quan la font són contractes reals (futures);
    en spot és tick count.
    """
    if not bars_m5 or len(bars_m5) < 25:
        return {}
    closed = bars_m5[:-1] if len(bars_m5) >= 2 else bars_m5
    if len(closed) < 24:
        return {}
    last_v = _safe_float(closed[-1].get("volume"))
    if last_v <= 0:
        return {}
    ref = [_safe_float(b.get("volume")) for b in closed[-73:-1]]
    ref = [v for v in ref if v > 0]
    if len(ref) < 12:
        return {}
    avg = sum(ref) / len(ref)
    if avg <= 0:
        return {}
    var = sum((v - avg) ** 2 for v in ref) / len(ref)
    std = var ** 0.5
    out = {
        "pct_vs_6h_avg": round(last_v / avg * 100, 1),
        "contracts_last_bar": int(last_v),
    }
    if std > 0:
        out["zscore"] = round((last_v - avg) / std, 2)
    return out


def _cmf_series(bars, period: int = 20) -> list:
    """Sèrie CMF (Chaikin Money Flow) per cada barra tancada amb finestra
    de `period` barres."""
    out = []
    if not bars or len(bars) < period:
        return out
    for end in range(period, len(bars) + 1):
        window = bars[end - period:end]
        mfv_sum = 0.0
        vol_sum = 0.0
        for b in window:
            h = _safe_float(b.get("high"))
            l = _safe_float(b.get("low"))
            c = _safe_float(b.get("close"))
            v = _safe_float(b.get("volume"))
            if h > l and v > 0:
                mfv = ((c - l) - (h - c)) / (h - l) * v
                mfv_sum += mfv
                vol_sum += v
        out.append(mfv_sum / vol_sum if vol_sum > 0 else 0.0)
    return out


def _cmf_m15(bars_m15) -> dict:
    """CMF M15 actual + streak de barres consecutives amb el mateix signe."""
    if not bars_m15 or len(bars_m15) < 25:
        return {}
    series = _cmf_series(bars_m15, period=20)
    if not series:
        return {}
    cur = series[-1]
    sign_pos = cur >= 0
    streak = 0
    for v in reversed(series):
        if (v >= 0) == sign_pos:
            streak += 1
        else:
            break
    return {
        "value": round(cur, 3),
        "streak_bars": streak,
    }


def _obv_series(bars) -> list:
    """OBV cumulatiu. obv[0]=0, després ±vol segons close vs close anterior."""
    if not bars or len(bars) < 2:
        return []
    obv = [0.0]
    for i in range(1, len(bars)):
        prev_c = _safe_float(bars[i - 1].get("close"))
        cur_c = _safe_float(bars[i].get("close"))
        v = _safe_float(bars[i].get("volume"))
        if cur_c > prev_c:
            obv.append(obv[-1] + v)
        elif cur_c < prev_c:
            obv.append(obv[-1] - v)
        else:
            obv.append(obv[-1])
    return obv


def _cvd_proxy(bars_m5) -> dict:
    """CVD (Cumulative Volume Delta) PROXY a partir d'OHLCV M5.

    True CVD requereix dades a nivell de tick (buy aggressor vs sell
    aggressor). Sense això, aproximem per barra:

        delta = volume * (close - open) / max(high - low, 0.01)

    Això signa el volum per direcció + força del cos. Una vela verda
    plena de rang dóna ~+volume. Una doji ~0. Una vela vermella plena
    ~−volume.

    Sobre futures (volum real en contractes), aquest valor és proxy
    útil de flux institucional agressiu. Sobre spot tick-volume, és
    degradat (el mateix avís que CMF).

    Retorna {cvd_4h, cvd_last_bar, bullish_bar_ratio_1h} o {}.
    """
    if not bars_m5 or len(bars_m5) < 50:
        return {}
    closed = bars_m5[:-1] if len(bars_m5) >= 2 else bars_m5
    if len(closed) < 49:
        return {}
    last_48 = closed[-48:]

    deltas = []
    for b in last_48:
        h = _safe_float(b.get("high"))
        l = _safe_float(b.get("low"))
        c = _safe_float(b.get("close"))
        o = _safe_float(b.get("open"))
        v = _safe_float(b.get("volume"))
        rng = h - l
        if rng > 0 and v > 0:
            d = v * (c - o) / rng
        else:
            d = 0.0
        deltas.append(d)

    cvd_4h = sum(deltas)
    last_delta = deltas[-1] if deltas else 0.0

    # Últimes 12 barres (1h) — % de barres bullish
    last_12 = deltas[-12:] if len(deltas) >= 12 else deltas
    if last_12:
        bullish_count = sum(1 for d in last_12 if d > 0)
        bullish_ratio = bullish_count / len(last_12)
    else:
        bullish_ratio = 0.5

    # CVD evolution per hora — l'LLM veu trajectòria, no només snapshot
    # Cada bucket = 12 barres M5 = 1 hora. 4 buckets = última 4h.
    evolution = []
    for hour_idx in range(4):
        # Bucket 0 = més antic (-4h), 3 = més recent (-1h)
        start = hour_idx * 12
        end = start + 12
        bucket_sum = sum(deltas[start:end]) if end <= len(deltas) else 0
        evolution.append(round(bucket_sum, 0))

    # CVD divergència preu/CVD sobre les últimes 48 barres (~4h M5)
    # Mateix algoritme que OBV divergence però amb CVD signat
    cvd_div = "none"
    if len(last_48) >= 48 and len(deltas) >= 48:
        # Reconstruim cumulative CVD per cada barra
        cvd_cum = []
        running = 0.0
        for d in deltas:
            running += d
            cvd_cum.append(running)
        prices = [_safe_float(b.get("close")) for b in last_48]
        if prices and cvd_cum:
            imax_p = max(range(48), key=lambda i: prices[i])
            imax_c = max(range(48), key=lambda i: cvd_cum[i])
            imin_p = min(range(48), key=lambda i: prices[i])
            imin_c = min(range(48), key=lambda i: cvd_cum[i])
            # Bearish divergence: preu fa màxim recent (>=40), CVD fa màxim molt abans
            if imax_p >= 40 and imax_c < 30:
                cvd_div = "bear"
            elif imin_p >= 40 and imin_c < 30:
                cvd_div = "bull"

    return {
        "cvd_4h": round(cvd_4h, 0),
        "cvd_last_bar": round(last_delta, 0),
        "bullish_bar_ratio_1h": round(bullish_ratio, 2),
        # NOU: trajectòria CVD per cada hora de les últimes 4 (de més antic a més recent)
        "cvd_evolution_hourly": evolution,
        # NOU: divergència preu/CVD igual que OBV — més fiable que OBV en futures
        "cvd_divergence_48h": cvd_div,
    }


def _obv_h1(bars_h1) -> dict:
    """OBV H1: canvi sobre les últimes 4h + divergència preu/OBV sobre 48h."""
    if not bars_h1 or len(bars_h1) < 6:
        return {}
    obv = _obv_series(bars_h1)
    if len(obv) < 5:
        return {}
    out = {"change_4h": round(obv[-1] - obv[-5], 0)}

    if len(obv) >= 48 and len(bars_h1) >= 48:
        win_obv = obv[-48:]
        win_price = [_safe_float(b.get("close")) for b in bars_h1[-48:]]
        if win_price and win_obv:
            imax_p = max(range(48), key=lambda i: win_price[i])
            imax_o = max(range(48), key=lambda i: win_obv[i])
            imin_p = min(range(48), key=lambda i: win_price[i])
            imin_o = min(range(48), key=lambda i: win_obv[i])
            div = "none"
            if imax_p >= 40 and imax_o < 30:
                div = "bear"
            elif imin_p >= 40 and imin_o < 30:
                div = "bull"
            out["divergence_48h"] = div
    return out


def _compression_metric(bars_m5) -> dict:
    """Compressió temporal: detecta acumulació d'energia abans d'explosió.

    Calcula l'ATR M5 actual (última barra com a True Range) i el compara
    amb la mitjana dels últims 6h. També mira el rang absolut mitjà últim
    hour vs 6h. Si AMBDÓS són baixos durant un temps sostingut, hi ha
    compressió — energia acumulant-se per a un break.

    Retorna {atr_ratio_vs_6h, range_ratio_vs_6h, compression_streak_min,
    compression_state} o {} si insuficients dades.
    """
    if not bars_m5 or len(bars_m5) < 80:
        return {}
    closed = bars_m5[:-1] if len(bars_m5) >= 2 else bars_m5
    if len(closed) < 73:
        return {}

    def _bar_range(b):
        return _safe_float(b.get("high")) - _safe_float(b.get("low"))

    ranges = [_bar_range(b) for b in closed]
    ranges = [r for r in ranges if r > 0]
    if len(ranges) < 73:
        return {}

    # Range mitjà últims 12 barres (1h) vs 72 barres (6h)
    last_1h_avg = sum(ranges[-12:]) / 12
    last_6h_avg = sum(ranges[-72:]) / 72
    range_ratio = last_1h_avg / last_6h_avg if last_6h_avg > 0 else 1.0

    # ATR aproximat per últimes 14 barres
    atr_14 = sum(ranges[-14:]) / 14
    atr_ratio = atr_14 / last_6h_avg if last_6h_avg > 0 else 1.0

    # Streak: quantes barres consecutives recents tenen range < 80% de la mitjana 6h
    streak_bars = 0
    threshold = last_6h_avg * 0.80
    for r in reversed(ranges):
        if r < threshold:
            streak_bars += 1
        else:
            break
    streak_min = streak_bars * 5  # M5 → minuts

    # Estat de compressió
    if range_ratio < 0.6 and streak_min >= 30:
        state = "tight"        # compressió forta sostinguda
    elif range_ratio < 0.8 and streak_min >= 15:
        state = "compressing"  # començant a comprimir-se
    elif range_ratio > 1.3:
        state = "expanding"    # ja s'està expandint
    else:
        state = "normal"

    return {
        "atr_ratio_vs_6h": round(atr_ratio, 2),
        "range_ratio_vs_6h": round(range_ratio, 2),
        "compression_streak_min": streak_min,
        "compression_state": state,
    }


def _day_session_delta(bars_m5) -> dict:
    """Acumulat de delta des de l'obertura de sessió actual UTC (00:00).

    Permet veure: "des de l'obertura del dia, hem tingut net +45K contractes
    de buying". Útil per detectar acumulació institucional intradia.

    Retorna {session_delta, bars_count, avg_per_bar} o {} si no hi ha dades.
    """
    if not bars_m5 or len(bars_m5) < 5:
        return {}
    from datetime import datetime, timezone as _tz
    now_utc = datetime.now(_tz.utc)
    today_start = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=_tz.utc).timestamp()

    # Filtre bars d'avui
    today_bars = [b for b in bars_m5 if _safe_float(b.get("time")) >= today_start]
    if not today_bars:
        return {}

    total_delta = 0.0
    for b in today_bars:
        h = _safe_float(b.get("high"))
        l = _safe_float(b.get("low"))
        c = _safe_float(b.get("close"))
        o = _safe_float(b.get("open"))
        v = _safe_float(b.get("volume"))
        rng = h - l
        if rng > 0 and v > 0:
            total_delta += v * (c - o) / rng

    return {
        "session_delta": round(total_delta, 0),
        "bars_count": len(today_bars),
        "avg_per_bar": round(total_delta / len(today_bars), 1) if today_bars else 0,
    }


def _build_source_metrics(bars_m5, bars_m15, bars_h1) -> dict:
    """Aplica les 4 mètriques sobre un feed (spot O futures). Retorna
    només els camps que tenen dades. Si no hi ha res, retorna {}."""
    out = {}
    try:
        v = _volume_burst_m5(bars_m5)
        if v:
            out["m5_volume_burst"] = v
    except Exception:
        pass
    try:
        v = _cmf_m15(bars_m15)
        if v:
            out["m15_cmf"] = v
    except Exception:
        pass
    try:
        v = _obv_h1(bars_h1)
        if v:
            out["h1_obv"] = v
    except Exception:
        pass
    try:
        v = _cvd_proxy(bars_m5)
        if v:
            out["m5_cvd_proxy"] = v
    except Exception:
        pass
    try:
        v = _compression_metric(bars_m5)
        if v:
            out["m5_compression"] = v
    except Exception:
        pass
    try:
        v = _day_session_delta(bars_m5)
        if v:
            out["session_delta"] = v
    except Exception:
        pass
    return out


def _build_spread(spot_price, gc_price) -> dict:
    """Spread spot−futures. Retorna {} si falta algun preu."""
    if spot_price is None or gc_price is None:
        return {}
    try:
        sp = float(spot_price)
        gp = float(gc_price)
    except (TypeError, ValueError):
        return {}
    if sp <= 0 or gp <= 0:
        return {}
    return {
        "spread_usd": round(gp - sp, 2),
        "spot_price": round(sp, 2),
        "gc_price": round(gp, 2),
    }


def build_flow_proxy(bars_m5, bars_m15, bars_h1,
                     gc_m5=None, gc_m15=None, gc_h1=None,
                     spot_price=None, gc_price=None) -> dict:
    """Construeix el flow proxy dual-feed.

    Args:
        bars_m5/m15/h1: bars de spot (OANDA:XAUUSD) — sempre disponibles.
        gc_m5/m15/h1: bars de futures (COMEX:GC1!) — opcionals; si None
            o llistes buides, només es retorna la part `spot`.
        spot_price/gc_price: preus actuals per calcular el spread; si
            falten, no es retorna `spread_spot_futures`.

    Retorna estructura niada:
      {
        "spot": {"m5_volume_burst", "m15_cmf", "h1_obv"},
        "futures": {...},                      # només si gc_* presents
        "spread_spot_futures": {...},          # només si tots dos preus
        # alies top-level (backward compat amb delta payload)
        "m5_volume_burst", "m15_cmf", "h1_obv": apunten a spot.*
      }
    """
    out = {}
    spot = _build_source_metrics(bars_m5, bars_m15, bars_h1)
    if spot:
        out["spot"] = spot
        # Alies top-level per a backward compat amb _build_indicator_delta
        # i altres consumidors que feien fp.get("m5_volume_burst") directe.
        # Eliminar quan tots els consumidors estiguin migrats a fp["spot"].
        for k, v in spot.items():
            out[k] = v

    futures = _build_source_metrics(gc_m5, gc_m15, gc_h1) if (gc_m5 or gc_m15 or gc_h1) else {}
    if futures:
        out["futures"] = futures

    spread = _build_spread(spot_price, gc_price)
    if spread:
        out["spread_spot_futures"] = spread

    return out


def render_flow_proxy(fp: dict) -> str:
    """Format text per al prompt de l'INDICATOR. Retorna '' si no hi ha
    cap font de dades."""
    if not fp:
        return ""

    spot = fp.get("spot") or {}
    futures = fp.get("futures") or {}
    spread = fp.get("spread_spot_futures") or {}

    if not spot and not futures:
        return ""

    lines = ["═══ FLUX (dual-feed: spot tick-volume + GC1! contractes reals) ═══"]

    if spot:
        lines.append("SPOT (OANDA:XAUUSD — proxy retail, tick-volume del broker):")
        vb = spot.get("m5_volume_burst") or {}
        if vb:
            z = vb.get("zscore")
            z_txt = f" · z={z:+.2f}σ" if z is not None else ""
            lines.append(
                f"  Volum M5: {vb.get('pct_vs_6h_avg', 0):.0f}% / mitjana 6h{z_txt}"
            )
        cm = spot.get("m15_cmf") or {}
        if cm:
            lines.append(
                f"  CMF M15: {cm.get('value', 0):+.3f} ({cm.get('streak_bars', 0)} b mateix signe)"
            )
        ob = spot.get("h1_obv") or {}
        if ob:
            div = ob.get("divergence_48h", "n/a")
            lines.append(
                f"  OBV H1: Δ4h={ob.get('change_4h', 0):+,.0f} · div(48h)={div}"
            )
        cvd = spot.get("m5_cvd_proxy") or {}
        if cvd:
            evo = cvd.get("cvd_evolution_hourly") or []
            evo_txt = ""
            if len(evo) == 4:
                evo_txt = f" · trajectòria 4h→1h: {evo[0]:+.0f}/{evo[1]:+.0f}/{evo[2]:+.0f}/{evo[3]:+.0f}"
            div_txt = ""
            if cvd.get("cvd_divergence_48h", "none") != "none":
                div_txt = f" · div={cvd.get('cvd_divergence_48h')}"
            lines.append(
                f"  CVD M5: 4h={cvd.get('cvd_4h', 0):+,.0f} · "
                f"última={cvd.get('cvd_last_bar', 0):+,.0f} · "
                f"bullish bars 1h={cvd.get('bullish_bar_ratio_1h', 0):.0%}{evo_txt}{div_txt}"
            )
        comp = spot.get("m5_compression") or {}
        if comp:
            state = comp.get("compression_state", "?")
            streak = comp.get("compression_streak_min", 0)
            lines.append(
                f"  Compressió M5: {state} · range 1h vs 6h={comp.get('range_ratio_vs_6h', 1):.2f}× · "
                f"ATR ratio={comp.get('atr_ratio_vs_6h', 1):.2f}× · streak {streak}min"
            )
        sd = spot.get("session_delta") or {}
        if sd:
            lines.append(
                f"  Delta des d'obertura UTC: {sd.get('session_delta', 0):+,.0f} "
                f"({sd.get('bars_count', 0)} barres, {sd.get('avg_per_bar', 0):+.1f}/barra)"
            )

    if futures:
        lines.append("FUTURES (COMEX:GC1! — contractes reals CME, institucional):")
        vb = futures.get("m5_volume_burst") or {}
        if vb:
            z = vb.get("zscore")
            z_txt = f" · z={z:+.2f}σ" if z is not None else ""
            n = vb.get("contracts_last_bar", 0)
            lines.append(
                f"  Volum M5: {n:,} contractes ({vb.get('pct_vs_6h_avg', 0):.0f}% / 6h{z_txt})"
            )
        cm = futures.get("m15_cmf") or {}
        if cm:
            lines.append(
                f"  CMF M15: {cm.get('value', 0):+.3f} ({cm.get('streak_bars', 0)} b mateix signe)"
            )
        ob = futures.get("h1_obv") or {}
        if ob:
            div = ob.get("divergence_48h", "n/a")
            lines.append(
                f"  OBV H1: Δ4h={ob.get('change_4h', 0):+,.0f} · div(48h)={div}"
            )
        cvd = futures.get("m5_cvd_proxy") or {}
        if cvd:
            evo = cvd.get("cvd_evolution_hourly") or []
            evo_txt = ""
            if len(evo) == 4:
                evo_txt = f" · trajectòria 4h→1h: {evo[0]:+,.0f}/{evo[1]:+,.0f}/{evo[2]:+,.0f}/{evo[3]:+,.0f}"
            div_txt = ""
            if cvd.get("cvd_divergence_48h", "none") != "none":
                div_txt = f" · div={cvd.get('cvd_divergence_48h')}"
            lines.append(
                f"  CVD M5: 4h={cvd.get('cvd_4h', 0):+,.0f} contractes nets · "
                f"última={cvd.get('cvd_last_bar', 0):+,.0f} · "
                f"bullish bars 1h={cvd.get('bullish_bar_ratio_1h', 0):.0%}{evo_txt}{div_txt}"
            )
        comp = futures.get("m5_compression") or {}
        if comp:
            state = comp.get("compression_state", "?")
            streak = comp.get("compression_streak_min", 0)
            lines.append(
                f"  Compressió M5: {state} · range 1h vs 6h={comp.get('range_ratio_vs_6h', 1):.2f}× · "
                f"ATR ratio={comp.get('atr_ratio_vs_6h', 1):.2f}× · streak {streak}min"
            )
        sd = futures.get("session_delta") or {}
        if sd:
            lines.append(
                f"  Delta institucional des d'obertura UTC: {sd.get('session_delta', 0):+,.0f} contractes "
                f"({sd.get('bars_count', 0)} barres, {sd.get('avg_per_bar', 0):+.1f}/barra)"
            )

    if spread:
        lines.append(
            f"SPREAD GC1!−spot: {spread.get('spread_usd', 0):+.2f}$ "
            f"(spot={spread.get('spot_price', 0):.2f}, gc={spread.get('gc_price', 0):.2f})"
        )

    if not futures:
        lines.append(
            "Nota: només feed SPOT disponible (futures absents — mercat CME tancat o fetch fallit). "
            "Tick-volume retail, no és footprint institucional."
        )

    return "\n".join(lines)


# ───────────── self-test ─────────────
if __name__ == "__main__":
    import random
    random.seed(0)

    def mkbar(i, vol):
        price = 4600.0 + i * 0.05
        return {
            "time": i * 60,
            "open": price,
            "high": price + random.uniform(0, 1.0),
            "low":  price - random.uniform(0, 1.0),
            "close": price + random.uniform(-0.5, 0.5),
            "volume": vol,
        }

    spot = [mkbar(i, random.randint(200, 600)) for i in range(288)]
    gc   = [mkbar(i, random.randint(80, 250))  for i in range(288)]
    # Burst institucional a la última barra tancada de futures
    gc[-2]["volume"] = 14500

    out = build_flow_proxy(
        spot, spot[::3], spot[::12],
        gc_m5=gc, gc_m15=gc[::3], gc_h1=gc[::12],
        spot_price=4615.0, gc_price=4625.4,
    )
    print("=== build_flow_proxy output ===")
    print("keys:", list(out.keys()))
    print("spot:", out.get("spot"))
    print("futures:", out.get("futures"))
    print("spread_spot_futures:", out.get("spread_spot_futures"))
    print()
    print("=== render_flow_proxy output ===")
    rendered = render_flow_proxy(out)
    # ASCII fallback per consola Windows cp1252
    print(rendered.encode("ascii", "replace").decode("ascii"))

    # Smoke: només spot
    out2 = build_flow_proxy(spot, spot[::3], spot[::12])
    print()
    print("=== spot-only (no futures) ===")
    print("keys:", list(out2.keys()))
    assert "spot" in out2
    assert "futures" not in out2
    assert "spread_spot_futures" not in out2

    # Smoke: backward compat top-level keys
    assert "m5_volume_burst" in out, "top-level alias missing — delta payload trencat"
    print("OK backward compat")
