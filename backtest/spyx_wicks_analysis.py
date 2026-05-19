"""
Analisi de metxes / spikes M5 de SPYX_USDT.
Mirar: ratio (H-L)/|C-O|, distribucio de wicks, frequencia de bars violents.
"""
import json
import math
import statistics
from pathlib import Path

ROOT = Path(r"C:\Users\Administrator\.claude\projects\C--Users-Administrator-Desktop-MT4-Claude\a2b66114-9f86-44d2-9680-6c357aa5c158\tool-results")

spyx_m5 = json.loads((ROOT / "toolu_017Wd4HJNAKpqvsT7upPWQGf.txt").read_text(encoding="utf-8"))
usox_m5 = json.loads((ROOT / "toolu_01Q7VzNLLwBwEh5fp16JE5i9.txt").read_text(encoding="utf-8"))


def analyze(klines_raw, name):
    klines = klines_raw["data"]["data"]["klines"]
    klines = sorted(klines, key=lambda b: int(b["time"]))
    print(f"\n{'=' * 75}")
    print(f"{name} - M5 WICKS / SPIKES ANALYSIS ({len(klines)} bars)")
    print(f"{'=' * 75}")

    # Categoritzar bars
    full_ranges = []
    bodies = []
    upper_wicks = []
    lower_wicks = []
    wick_ratios = []  # (H-L) / max(0.001, |C-O|)
    spike_bars = []  # bars amb wick >> body

    for i, b in enumerate(klines):
        h, l, o, c = float(b["high"]), float(b["low"]), float(b["open"]), float(b["close"])
        vol = float(b["volume"])
        rng = h - l
        body = abs(c - o)
        if rng < 1e-9:
            continue
        up_wick = h - max(o, c)
        dn_wick = min(o, c) - l

        full_ranges.append(rng / c * 100)
        bodies.append(body / c * 100)
        upper_wicks.append(up_wick / c * 100)
        lower_wicks.append(dn_wick / c * 100)

        # Wick ratio: quantes vegades més gran és el range total que el body
        if body > 0:
            ratio = rng / body
            wick_ratios.append(ratio)
        else:
            wick_ratios.append(99)  # doji absolut

        # Spike = range > 3× body i range > median*2
        # (definicio pragmatica: bars amb metxa significativa)
        is_spike = body > 0 and rng > body * 3 and rng / c > 0.0005  # >0.05%
        if is_spike:
            spike_bars.append({
                "time": int(b["time"]),
                "rng_pct": rng / c * 100,
                "body_pct": body / c * 100,
                "ratio": rng / body,
                "vol": vol,
                "high": h,
                "low": l,
                "close": c,
            })

    def p(lst, pct):
        return sorted(lst)[int(len(lst) * pct / 100)]

    print(f"\nFull range (H-L)/C:")
    print(f"  mean: {statistics.mean(full_ranges):.4f}%   median: {p(full_ranges, 50):.4f}%")
    print(f"  p75: {p(full_ranges, 75):.4f}%   p90: {p(full_ranges, 90):.4f}%")
    print(f"  p95: {p(full_ranges, 95):.4f}%   p99: {p(full_ranges, 99):.4f}%   max: {max(full_ranges):.4f}%")

    print(f"\nBody |C-O|/C:")
    print(f"  mean: {statistics.mean(bodies):.4f}%   median: {p(bodies, 50):.4f}%")
    print(f"  p75: {p(bodies, 75):.4f}%   p90: {p(bodies, 90):.4f}%")

    print(f"\nWick ratio (range/body) - quant més espigada que body:")
    print(f"  median: {p(wick_ratios, 50):.2f}×   p75: {p(wick_ratios, 75):.2f}×")
    print(f"  p90: {p(wick_ratios, 90):.2f}×   p95: {p(wick_ratios, 95):.2f}×")

    print(f"\n*** SPIKES (range > 3× body, range > 0.05%): ***")
    print(f"  Total spikes: {len(spike_bars)} / {len(full_ranges)} bars ({len(spike_bars)/len(full_ranges)*100:.1f}%)")
    if spike_bars:
        spike_bars.sort(key=lambda x: -x["rng_pct"])
        print(f"\n  Top 15 spikes (per range %):")
        print(f"  {'Hora UTC':<22} {'Range%':<10} {'Body%':<10} {'Ratio':<10} {'Volume':<12} {'High':<10} {'Low':<10}")
        from datetime import datetime, timezone
        for s in spike_bars[:15]:
            dt = datetime.fromtimestamp(s["time"]/1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            print(f"  {dt:<22} {s['rng_pct']:<10.3f} {s['body_pct']:<10.3f} {s['ratio']:<10.1f} {s['vol']:<12.4f} {s['high']:<10.2f} {s['low']:<10.2f}")

    # Range absolut de spikes — quants USDT?
    if spike_bars:
        avg_range_usdt = sum(s["high"] - s["low"] for s in spike_bars) / len(spike_bars)
        avg_close = sum(s["close"] for s in spike_bars) / len(spike_bars)
        print(f"\n  Avg spike size: {avg_range_usdt:.3f} USDT ({avg_range_usdt/avg_close*100:.3f}%)")

    # Frequencia per franja horaria UTC (cada hora de les 24h)
    from collections import Counter
    spike_by_hour = Counter()
    bar_by_hour = Counter()
    from datetime import datetime, timezone
    for i, b in enumerate(klines):
        h_utc = datetime.fromtimestamp(int(b["time"])/1000, timezone.utc).hour
        bar_by_hour[h_utc] += 1
    for s in spike_bars:
        h_utc = datetime.fromtimestamp(s["time"]/1000, timezone.utc).hour
        spike_by_hour[h_utc] += 1
    print(f"\n  Spikes per hora UTC (top hores):")
    by_hour_sorted = sorted(spike_by_hour.items(), key=lambda x: -x[1])
    for h_utc, cnt in by_hour_sorted[:8]:
        total_bars = bar_by_hour[h_utc]
        print(f"    {h_utc:02d}:00 UTC  {cnt} spikes / {total_bars} bars  ({cnt/total_bars*100:.1f}%)")


analyze(spyx_m5, "SPYX_USDT")
analyze(usox_m5, "USOX_USDT")
