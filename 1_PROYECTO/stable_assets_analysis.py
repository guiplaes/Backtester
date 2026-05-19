"""Analisi comparatiu de volatilitat historica: XAUT (or), SPYX (S&P500), QQQX (Nasdaq100)
+ context WTI per ranking
Dades: Pionex 1D klines (~200 dies, novembre 2025 - maig 2026)
"""
import json
import statistics
from datetime import datetime, timezone

# Carrego klines extreuts (ja parseats en format dict simple)
# Format: (open, close, high, low, volume)
def parse_klines(raw_text):
    """Extreu (open, close, high, low, volume, time) de JSON klines."""
    data = json.loads(raw_text)
    klines = data.get("data", {}).get("data", {}).get("klines", [])
    out = []
    for k in klines:
        # Salta dies amb volum 0 (mercat tancat - festius, caps de setmana equity)
        v = float(k["volume"])
        if v < 1:  # mercat tancat
            continue
        out.append({
            "time": k["time"],
            "open": float(k["open"]),
            "close": float(k["close"]),
            "high": float(k["high"]),
            "low": float(k["low"]),
            "volume": v,
        })
    return out


def analyze(name, klines):
    if len(klines) < 5:
        print(f"\n{name}: dades insuficients")
        return
    # Range diari %
    ranges = []
    for k in klines:
        h, l = k["high"], k["low"]
        mid = (h + l) / 2
        if mid > 0:
            ranges.append((h - l) / mid * 100)
    closes = [k["close"] for k in klines]
    highs = [k["high"] for k in klines]
    lows = [k["low"] for k in klines]
    first_open = klines[-1]["open"]  # cronologic ascendent = el mes vell es el final del array
    # Wait - les klines venen del mes RECENT al mes vell. Reordeno
    klines_sorted = sorted(klines, key=lambda x: x["time"])
    first_open = klines_sorted[0]["open"]
    last_close = klines_sorted[-1]["close"]

    days = len(klines)
    period_chg = (last_close - first_open) / first_open * 100
    avg_r = statistics.mean(ranges)
    med_r = statistics.median(ranges)
    max_r = max(ranges)
    stdev_r = statistics.stdev(ranges) if len(ranges) > 1 else 0
    cv = stdev_r / avg_r if avg_r > 0 else 0

    print(f"\n=== {name} ===")
    print(f"  Dies amb activitat:    {days}")
    print(f"  Periode preu:          {first_open:.2f} -> {last_close:.2f}  ({period_chg:+.1f}%)")
    print(f"  Rang historic:         min {min(lows):.2f}   max {max(highs):.2f}   spread {max(highs)-min(lows):.2f}  ({(max(highs)-min(lows))/min(lows)*100:.1f}%)")
    print(f"  Range diari %:         mitja {avg_r:.2f}%   mediana {med_r:.2f}%   max {max_r:.2f}%   stdev {stdev_r:.2f}%")
    print(f"  Coefficient variacio:  {cv:.2f}  ({'baix' if cv < 0.5 else 'mig' if cv < 0.8 else 'alt'})")
    return {
        "name": name,
        "avg_range": avg_r,
        "median_range": med_r,
        "max_range": max_r,
        "cv": cv,
        "period_change": period_chg,
        "days": days,
        "first": first_open,
        "last": last_close,
        "min": min(lows),
        "max": max(highs),
    }


# Carrego dades directament fent referencia als 3 fitxers de tool-results
# (els acabo de descarregar i hi son al disc — pero per simplicitat hardcodejo aqui els ultims 200)
# En aquest script els llegim DIRECTAMENT del darrer kline call que vam fer
# Estructura: data->data->klines->[{time, open, high, low, close, volume}]

# Faig un fitxer auxiliar perque els JSON eren molt llargs:
# Els llegim del cache del MCP (que va guardar resultats grans)
# Solucio simple: hi ha 3 JSON inline que copio aqui en mini-format
# Per estalviar contexte, llegim del MCP cache files

# En lloc de hardcodejar, llegim els arxius en disc:
import os
cache_dir = r"C:\Users\Administrator\.claude\projects\C--Users-Administrator-Desktop-MT4-Claude\a2b66114-9f86-44d2-9680-6c357aa5c158\tool-results"

results = {}
for fname in os.listdir(cache_dir):
    if "klines" in fname:
        full = os.path.join(cache_dir, fname)
        try:
            with open(full, "r", encoding="utf-8") as f:
                content = f.read()
            j = json.loads(content)
            klines = j.get("data", {}).get("data", {}).get("klines", [])
            if not klines: continue
            # Cerca primer ticker amb cua per identificar
            sample = klines[0]
            # No tenim el symbol als klines — pero per mida + range podem inferir
            # Pero millor: el fitxer no ho diu. Saltem aquest enfoc.
        except Exception:
            pass

# Approach alternatiu: hardcodejo els 3 datasets minim necessari (mostrant simplificat)
# El WTI ja tenim el seu analisi. Per XAUT/SPYX/QQQX construim aqui amb el que m'ha tornat el MCP

# ======================================
# DADES REALS — extretes del MCP klines
# ======================================
# Per estalviar memoria, nomes guardem (high, low, close, vol) que es el que necessitem
# Aquests son els ultims ~60 dies de cada (la finestra que cobreix les 3 etiquetes)

XAUT_RAW = """
4709.95,4687.55,4718.70,4680.38,239
4753.25,4709.95,4758.65,4637.60,1675
4682.09,4753.25,4754.42,4644.10,1884
4707.75,4682.09,4718.70,4671.49,448
4698.55,4707.75,4710.44,4696.10,95
4693.25,4698.55,4734.46,4684.80,772
4684.50,4693.25,4749.30,4668.90,2366
4582.78,4684.50,4709.70,4578.57,2424
4515.75,4582.78,4585.35,4511.09,1555
4603.45,4515.75,4609.10,4501.30,2793
4596.53,4603.45,4631.34,4596.42,227
4600.35,4596.53,4603.20,4595.65,105
4613.19,4600.35,4646.89,4557.14,2477
4557.45,4613.19,4634.89,4540.10,1560
4588.13,4557.45,4602.21,4512.90,1778
4682.60,4588.13,4688.10,4551.29,2229
4671.75,4682.60,4711.41,4654.50,849
4686.53,4671.75,4707.40,4666.08,204
4688.85,4686.53,4697.50,4686.53,98
4682.75,4688.85,4721.12,4647.70,985
4708.45,4682.75,4731.59,4651.64,1750
4723.60,4708.45,4755.23,4705.01,1269
4798.95,4723.60,4807.60,4654.50,1733
4732.90,4798.95,4808.40,4723.28,1057
4773.67,4732.90,4793.10,4718.88,780
4810.75,4773.67,4823.45,4773.20,501
4769.85,4810.75,4862.70,4749.10,1290
4798.80,4769.85,4813.24,4756.05,1245
4808.45,4798.80,4842.80,4762.65,1135
4727.65,4808.45,4821.60,4722.70,1362
4646.80,4727.65,4736.60,4639.76,1248
4722.35,4646.80,4723.60,4618.10,832
4720.05,4722.35,4727.50,4717.27,145
4735.25,4720.05,4764.20,4704.10,1277
4690.75,4735.25,4774.01,4674.10,1242
4784.90,4690.75,4808.30,4670.40,2591
4624.65,4784.90,4786.60,4581.76,3366
4582.65,4624.65,4672.80,4573.44,1859
4632.75,4582.65,4638.54,4576.90,624
4631.13,4632.75,4643.90,4629.92,132
4648.15,4631.13,4649.22,4627.25,485
4745.85,4648.15,4759.30,4531.20,5387
4671.14,4745.85,4761.30,4641.50,3545
4504.46,4671.14,4673.90,4474.50,4772
4444.60,4504.46,4572.70,4419.20,2619
4488.75,4444.60,4509.20,4441.60,645
4492.65,4488.75,4498.10,4477.10,280
4402.55,4492.65,4543.65,4374.80,3155
4493.50,4402.55,4534.60,4354.69,3249
4538.76,4493.50,4587.70,4484.40,4100
4433.80,4538.76,4544.70,4309.80,3851
4465.62,4433.80,4499.15,4153.11,10850
4484.75,4465.62,4532.30,4450.00,1210
4496.24,4484.75,4512.89,4481.80,746
4641.95,4496.24,4729.90,4479.90,4145
4815.55,4641.95,4842.00,4514.41,7213
4965.60,4815.55,4979.49,4784.40,3025
4970.75,4965.60,4997.56,4939.70,1295
4964.80,4970.75,4997.50,4933.50,2472
4989.95,4964.80,4997.52,4937.20,1082
4997.35,4989.95,5007.40,4987.57,476
5079.05,4997.35,5097.60,4980.40,2495
5094.50,5079.05,5148.60,5033.20,2583
5156.34,5094.50,5178.62,5090.80,2387
5102.95,5156.34,5195.20,5090.50,2732
5041.78,5102.95,5117.64,4988.00,3546
5142.35,5041.78,5148.40,5016.85,1681
5138.95,5142.35,5154.80,5127.10,463
5060.65,5138.95,5148.90,5036.70,3479
5129.85,5060.65,5154.01,5025.00,6153
5097.95,5129.85,5167.50,5072.49,5662
5312.11,5097.95,5343.30,4975.55,15488
5363.55,5312.11,5391.10,5217.40,7362
5305.41,5363.55,5369.34,5241.86,4164
5255.75,5305.41,5469.12,5250.95,8623
5161.35,5255.75,5272.84,5147.49,2853
5139.30,5161.35,5175.85,5109.40,1607
5130.35,5139.30,5194.76,5121.10,2482
"""

SPYX_RAW = """
736.93,740.34,741.27,736.77,1407
739.52,736.93,739.53,732.15,3231
736.34,739.52,740.99,735.46,1819
738.90,736.34,739.86,734.63,1302
737.85,738.90,739.12,737.35,998
731.27,737.85,738.76,731.27,2074
733.14,731.27,736.17,728.60,2111
726.53,733.14,734.71,725.00,2567
717.80,726.53,726.75,717.55,1772
720.81,717.80,722.20,715.20,5738
719.98,720.81,722.44,719.09,1727
719.51,719.98,720.40,718.87,2339
719.81,719.51,724.43,719.15,3492
713.70,719.81,720.79,708.41,2131
712.39,713.70,714.15,708.19,1512
716.23,712.39,716.67,709.39,1420
712.28,716.23,716.47,712.20,4003
713.87,712.28,716.77,711.71,932
713.27,713.87,714.09,712.13,939
709.63,713.27,714.23,708.14,4451
710.19,709.63,712.12,702.30,5452
707.04,710.19,711.25,707.03,2523
709.41,707.04,711.99,702.89,3724
709.32,709.41,709.87,704.47,4232
710.33,709.32,710.48,702.52,3767
710.27,710.33,710.66,707.82,1604
701.89,710.27,711.81,701.01,2822
700.07,701.89,702.19,697.91,2577
694.00,700.07,700.18,692.38,3426
686.46,694.00,694.43,685.79,2792
680.60,686.46,686.90,671.80,5314
679.25,680.60,682.01,678.46,3429
674.75,679.25,681.11,672.90,3245
673.50,674.75,678.08,671.75,2609
658.09,673.50,675.83,651.08,7739
655.90,658.09,659.48,652.36,2704
654.73,655.91,657.78,644.17,5671
652.18,654.73,658.50,650.88,4363
630.55,652.18,652.30,628.99,6106
632.90,630.55,641.13,629.32,3150
647.89,632.90,649.74,632.36,3439
655.56,647.89,657.19,644.54,2551
657.61,655.56,660.82,654.32,2845
656.74,657.61,660.24,649.94,4062
653.62,656.74,666.47,641.10,7387
661.11,653.62,661.12,644.79,3914
660.95,661.11,663.25,655.25,3353
670.21,660.95,674.93,659.41,3554
668.77,670.21,674.38,665.39,1936
663.02,668.77,671.89,663.02,2612
666.43,663.01,672.31,661.18,4081
671.20,666.43,674.36,665.61,2976
678.02,671.20,680.51,669.97,2512
675.32,678.02,683.32,674.49,4502
671.62,675.32,679.86,656.98,5203
681.99,671.62,683.39,669.91,4525
686.31,681.99,687.74,675.68,5004
678.74,686.31,687.06,675.29,4265
685.28,678.74,685.54,669.69,4940
684.17,685.28,688.56,674.69,3953
"""

QQQX_RAW = """
705.04,713.21,714.52,704.58,1766
713.72,705.04,713.72,696.95,7859
710.05,713.72,715.08,709.55,3546
712.90,710.05,714.74,709.27,3315
713.61,712.90,713.62,712.29,1314
694.85,713.61,713.64,694.85,6719
694.24,694.85,701.49,691.45,5751
687.12,694.24,697.49,684.33,4314
672.54,687.12,688.11,672.54,14331
674.17,672.54,677.62,669.60,4915
673.45,674.17,676.66,672.23,1492
672.45,673.45,674.38,671.01,1025
667.67,672.45,675.79,667.01,3636
666.84,667.67,669.19,657.80,5678
659.70,666.84,667.03,656.18,4758
665.43,659.70,665.89,654.47,7520
663.35,665.43,667.01,661.22,3767
663.55,663.35,667.00,662.57,2374
663.20,663.55,665.04,662.68,1458
655.48,663.20,664.70,653.99,7337
655.45,655.48,657.16,646.09,4818
647.71,655.45,656.39,647.61,3574
648.20,647.71,650.73,642.69,3639
647.59,648.20,648.54,642.81,3824
648.29,647.59,648.75,640.36,7335
648.58,648.29,648.93,646.01,1631
639.68,648.58,649.77,639.10,5569
637.87,639.68,641.22,634.81,5605
628.95,637.87,637.95,626.55,7141
618.48,628.95,629.35,617.96,8432
612.23,618.48,619.18,603.10,4287
609.48,612.23,613.65,609.10,4008
604.51,609.48,610.49,602.92,3201
605.10,604.51,610.07,602.32,4040
587.08,605.10,605.87,578.44,7485
584.78,587.08,590.23,581.99,2922
583.84,584.80,585.87,571.68,6412
580.21,583.84,587.73,578.32,4205
556.02,580.21,580.25,554.32,8066
560.25,556.02,568.40,555.59,5758
575.89,560.12,578.41,560.07,6543
586.62,575.89,588.86,573.20,8188
588.64,586.62,591.70,585.69,3705
589.48,588.64,591.70,582.01,3540
587.13,589.48,596.69,573.32,6803
594.10,587.16,595.22,578.64,4831
594.06,594.10,596.60,587.12,4444
603.19,594.06,608.21,592.76,6282
600.19,603.19,605.87,596.79,4532
593.41,600.19,603.68,593.41,4218
596.72,593.42,603.57,592.20,6221
602.91,596.72,606.33,596.48,3753
608.49,602.91,612.34,601.73,3359
605.09,608.49,613.24,604.07,3938
598.85,605.09,609.10,583.84,10411
609.17,599.59,611.30,598.50,7212
611.79,609.17,613.44,602.36,6734
600.40,611.79,612.86,595.57,7691
606.84,600.40,607.08,591.94,10862
605.35,606.84,609.86,595.03,7205
"""


def parse_simple(raw):
    out = []
    for line in raw.strip().splitlines():
        parts = line.strip().split(",")
        if len(parts) != 5: continue
        out.append({
            "open": float(parts[0]),
            "close": float(parts[1]),
            "high": float(parts[2]),
            "low": float(parts[3]),
            "volume": float(parts[4]),
            "time": 0,  # no time tracked
        })
    return out


print("=" * 80)
print("ANALISI VOLATILITAT - ACTIUS RWA TOKENITZATS PIONEX")
print("Comparativa: Or (XAUT), S&P500 (SPYX), Nasdaq100 (QQQX), WTI (Petroli)")
print("=" * 80)

xaut = parse_simple(XAUT_RAW)
spyx = parse_simple(SPYX_RAW)
qqqx = parse_simple(QQQX_RAW)

# Klines venen del MES RECENT al MES VELL en el JSON original — ja les invertim
xaut.reverse()
spyx.reverse()
qqqx.reverse()

r_xaut = analyze("XAUT_USDT_PERP (Or)", xaut)
r_spyx = analyze("SPYX_USDT_PERP (S&P 500)", spyx)
r_qqqx = analyze("QQQX_USDT_PERP (Nasdaq 100)", qqqx)

# WTI - hardcoded summary del analisi previ
print("\n=== WTI_USDT_PERP (Petroli) — del analisi previ ===")
print(f"  Dies amb activitat:    66")
print(f"  Periode preu:          99.85 -> 99.04 (-0.8%)")
print(f"  Rang historic:         min 76.91   max 116.33   spread 39.42 (51.3%)")
print(f"  Range diari %:         mitja 7.11%   mediana 6.14%   max 25.03%   stdev 4.88%")
print(f"  Coefficient variacio:  0.69 (mig)")

print("\n" + "=" * 80)
print("RANKING per ESTABILITAT (range diari mitja, menor = mes estable)")
print("=" * 80)
print(f"{'Asset':<28} {'Range%':>8} {'Mediana':>9} {'Max%':>8} {'StdDev':>8} {'CV':>5}  Veredicte")
print("-" * 80)
ranked = sorted([r_spyx, r_xaut, r_qqqx], key=lambda x: x["avg_range"])
for r in ranked:
    v = "MOLT estable" if r["avg_range"] < 1.5 else "Estable" if r["avg_range"] < 2.5 else "Moderat" if r["avg_range"] < 4 else "Volatil"
    print(f"{r['name']:<28} {r['avg_range']:>7.2f}%  {r['median_range']:>7.2f}%  {r['max_range']:>6.2f}%  {r['cv']:>4.2f}  {v}")
print(f"{'WTI_USDT_PERP (Petroli)':<28} {7.11:>7.2f}%  {6.14:>7.2f}%  {25.03:>6.2f}%  {0.69:>4.2f}  Volatil EXTREM")

# Context cripto per comparar (BTC ~3-4% diari tipic)
print(f"\n[CONTEXT cripto] BTC/USDT diari mitja ~3-4%   ETH/USDT ~4-5%   SOL/USDT ~5-7%")
