"""
Anàlisi USOX i SPYX a Pionex i proposta paràmetres de grid.
Dades daily extretes directament de l'output MCP (capturades a la conversa).
M5 carregades des dels fitxers persistits.
"""
import json
import math
import statistics
from pathlib import Path

ROOT = Path(r"C:\Users\Administrator\.claude\projects\C--Users-Administrator-Desktop-MT4-Claude\a2b66114-9f86-44d2-9680-6c357aa5c158\tool-results")

# Daily klines USOX (90d, llisades de MCP)
USOX_DAILY = [
    {"time":1778716800000,"open":"141.65","close":"142.23","high":"143.56","low":"139.90","volume":"218.097"},
    {"time":1778630400000,"open":"143.72","close":"141.65","high":"144.96","low":"141.32","volume":"512.420"},
    {"time":1778544000000,"open":"138.68","close":"143.72","high":"144.77","low":"138.68","volume":"562.348"},
    {"time":1778457600000,"open":"133.17","close":"138.68","high":"140.88","low":"133.17","volume":"836.636"},
    {"time":1778371200000,"open":"133.17","close":"133.17","high":"133.17","low":"133.17","volume":"0"},
    {"time":1778284800000,"open":"133.17","close":"133.17","high":"133.17","low":"133.17","volume":"0"},
    {"time":1778198400000,"open":"135.75","close":"133.17","high":"136.21","low":"132.13","volume":"882.847"},
    {"time":1778112000000,"open":"134.45","close":"135.75","high":"137.66","low":"126.93","volume":"1323.523"},
    {"time":1778025600000,"open":"140.17","close":"134.45","high":"142.19","low":"125.51","volume":"1284.225"},
    {"time":1777939200000,"open":"147.13","close":"140.17","high":"147.50","low":"139.31","volume":"721.877"},
    {"time":1777852800000,"open":"144.47","close":"147.13","high":"150.11","low":"140.35","volume":"1200.837"},
    {"time":1777766400000,"open":"144.47","close":"144.47","high":"144.47","low":"144.47","volume":"0"},
    {"time":1777680000000,"open":"144.47","close":"144.47","high":"144.47","low":"144.47","volume":"0"},
    {"time":1777593600000,"open":"147.67","close":"144.47","high":"148.87","low":"138.87","volume":"858.168"},
    {"time":1777507200000,"open":"150.97","close":"147.67","high":"154.93","low":"144.57","volume":"1103.900"},
    {"time":1777420800000,"open":"139.21","close":"150.97","high":"152.07","low":"137.79","volume":"939.887"},
    {"time":1777334400000,"open":"135.16","close":"139.21","high":"142.29","low":"134.61","volume":"927.302"},
    {"time":1777248000000,"open":"132.57","close":"135.16","high":"136.45","low":"132.24","volume":"883.736"},
    {"time":1777161600000,"open":"132.57","close":"132.57","high":"132.57","low":"132.57","volume":"0"},
    {"time":1777075200000,"open":"132.57","close":"132.57","high":"132.57","low":"132.57","volume":"0"},
    {"time":1776988800000,"open":"135.21","close":"132.57","high":"136.66","low":"129.61","volume":"1150.336"},
    {"time":1776902400000,"open":"129.74","close":"135.21","high":"137.40","low":"129.18","volume":"1333.838"},
    {"time":1776816000000,"open":"126.55","close":"129.74","high":"130.90","low":"122.53","volume":"1263.329"},
    {"time":1776729600000,"open":"120.58","close":"126.55","high":"128.72","low":"119.73","volume":"1287.234"},
    {"time":1776643200000,"open":"116.69","close":"120.58","high":"124.10","low":"116.69","volume":"1425.926"},
    {"time":1776556800000,"open":"116.69","close":"116.69","high":"116.69","low":"116.69","volume":"0"},
    {"time":1776470400000,"open":"116.69","close":"116.69","high":"116.69","low":"116.69","volume":"0"},
    {"time":1776384000000,"open":"125.88","close":"116.69","high":"126.09","low":"110.38","volume":"1702.887"},
    {"time":1776297600000,"open":"122.57","close":"125.88","high":"128.12","low":"121.98","volume":"868.184"},
    {"time":1776211200000,"open":"122.03","close":"122.57","high":"126.22","low":"122.00","volume":"983.546"},
    {"time":1776124800000,"open":"126.81","close":"122.03","high":"129.33","low":"118.73","volume":"1089.919"},
    {"time":1776038400000,"open":"124.34","close":"126.81","high":"134.50","low":"124.34","volume":"1073.003"},
    {"time":1775779200000,"open":"126.55","close":"124.34","high":"128.10","low":"123.87","volume":"1020.794"},
    {"time":1775692800000,"open":"125.74","close":"126.55","high":"129.62","low":"123.13","volume":"1616.760"},
    {"time":1775606400000,"open":"122.62","close":"125.74","high":"126.36","low":"117.49","volume":"1981.152"},
    {"time":1775520000000,"open":"140.16","close":"122.62","high":"143.95","low":"117.49","volume":"2425.926"},
    {"time":1775433600000,"open":"138.83","close":"140.16","high":"141.42","low":"135.21","volume":"1172.085"},
    {"time":1775088000000,"open":"123.29","close":"138.83","high":"140.54","low":"121.75","volume":"1915.766"},
    {"time":1775001600000,"open":"126.74","close":"123.29","high":"128.75","low":"120.47","volume":"1484.135"},
    {"time":1774915200000,"open":"132.60","close":"126.74","high":"132.74","low":"124.28","volume":"1713.908"},
    {"time":1774828800000,"open":"126.10","close":"132.60","high":"133.12","low":"124.10","volume":"2540.542"},
    {"time":1774569600000,"open":"116.31","close":"126.10","high":"126.32","low":"114.99","volume":"1513.357"},
    {"time":1774483200000,"open":"113.55","close":"116.31","high":"118.93","low":"112.02","volume":"3264.416"},
    {"time":1774396800000,"open":"110.62","close":"113.55","high":"113.98","low":"107.85","volume":"1785.201"},
    {"time":1774310400000,"open":"111.44","close":"110.62","high":"116.27","low":"107.71","volume":"1899.242"},
    {"time":1774224000000,"open":"119.58","close":"111.44","high":"126.63","low":"106.57","volume":"2747.281"},
    {"time":1773964800000,"open":"117.14","close":"119.58","high":"122.98","low":"115.24","volume":"1559.643"},
    {"time":1773878400000,"open":"122.39","close":"117.14","high":"125.05","low":"114.79","volume":"2180.587"},
    {"time":1773792000000,"open":"118.82","close":"122.39","high":"124.19","low":"113.98","volume":"2100.429"},
    {"time":1773705600000,"open":"116.85","close":"118.82","high":"121.51","low":"116.10","volume":"1966.762"},
    {"time":1773619200000,"open":"119.00","close":"116.85","high":"123.50","low":"114.41","volume":"2165.295"},
    {"time":1773360000000,"open":"118.85","close":"119.00","high":"122.37","low":"113.69","volume":"2590.774"},
    {"time":1773273600000,"open":"113.86","close":"118.85","high":"120.41","low":"110.58","volume":"4459.687"},
    {"time":1773187200000,"open":"105.02","close":"113.86","high":"116.17","low":"100.49","volume":"4965.164"},
    {"time":1773100800000,"open":"106.66","close":"105.02","high":"110.03","low":"94.40","volume":"6683.474"},
    {"time":1773014400000,"open":"112.26","close":"106.66","high":"133.78","low":"98.63","volume":"6590.708"},
    {"time":1772841600000,"open":"109.54","close":"112.26","high":"112.40","low":"109.39","volume":"97.598"},
    {"time":1772755200000,"open":"96.05","close":"109.54","high":"113.49","low":"94.91","volume":"4401.098"},
    {"time":1772668800000,"open":"92.14","close":"96.05","high":"98.75","low":"91.30","volume":"2556.361"},
    {"time":1772582400000,"open":"91.86","close":"92.14","high":"93.86","low":"89.19","volume":"2555.501"},
    {"time":1772496000000,"open":"86.30","close":"91.86","high":"94.74","low":"86.30","volume":"5047.201"},
    {"time":1772409600000,"open":"81.98","close":"86.30","high":"89.32","low":"81.98","volume":"3887.751"},
    {"time":1772150400000,"open":"79.60","close":"82.03","high":"82.57","low":"79.07","volume":"1084.820"},
    {"time":1772064000000,"open":"79.93","close":"79.60","high":"81.20","low":"77.55","volume":"1549.969"},
    {"time":1771977600000,"open":"80.42","close":"79.93","high":"80.93","low":"79.34","volume":"834.206"},
    {"time":1771891200000,"open":"80.84","close":"80.42","high":"81.75","low":"79.84","volume":"789.454"},
    {"time":1771804800000,"open":"81.11","close":"80.84","high":"81.92","low":"79.83","volume":"840.176"},
    {"time":1771545600000,"open":"80.90","close":"81.11","high":"81.58","low":"80.20","volume":"1045.737"},
    {"time":1771459200000,"open":"79.15","close":"80.90","high":"81.50","low":"79.03","volume":"1094.703"},
    {"time":1771372800000,"open":"75.69","close":"79.15","high":"79.60","low":"75.60","volume":"1100.244"},
    {"time":1771286400000,"open":"76.24","close":"75.69","high":"77.83","low":"75.18","volume":"1070.490"},
]

SPYX_DAILY = [
    {"time":1778716800000,"open":"743.56","close":"744.19","high":"744.62","low":"742.71","volume":"14.1084"},
    {"time":1778630400000,"open":"736.74","close":"743.56","high":"743.87","low":"735.54","volume":"85.3663"},
    {"time":1778544000000,"open":"739.27","close":"736.74","high":"740.78","low":"731.86","volume":"47.4951"},
    {"time":1778457600000,"open":"737.75","close":"739.27","high":"740.66","low":"735.22","volume":"54.5145"},
    {"time":1778198400000,"open":"731.42","close":"737.74","high":"737.95","low":"731.42","volume":"37.6026"},
    {"time":1778112000000,"open":"733.03","close":"731.42","high":"736.10","low":"728.53","volume":"53.7864"},
    {"time":1778025600000,"open":"726.43","close":"733.03","high":"734.52","low":"725.23","volume":"121.5553"},
    {"time":1777939200000,"open":"717.77","close":"726.43","high":"726.90","low":"717.50","volume":"46.7904"},
    {"time":1777852800000,"open":"720.08","close":"717.77","high":"722.29","low":"715.36","volume":"63.2984"},
    {"time":1777593600000,"open":"719.82","close":"720.05","high":"724.78","low":"719.53","volume":"56.5516"},
    {"time":1777507200000,"open":"713.71","close":"719.82","high":"720.73","low":"708.26","volume":"54.9234"},
    {"time":1777420800000,"open":"712.49","close":"713.71","high":"714.17","low":"707.92","volume":"157.6813"},
    {"time":1777334400000,"open":"716.23","close":"712.49","high":"716.83","low":"709.30","volume":"41.0717"},
    {"time":1777248000000,"open":"714.50","close":"716.23","high":"716.47","low":"712.37","volume":"42.0601"},
    {"time":1776988800000,"open":"709.52","close":"714.26","high":"714.50","low":"708.13","volume":"42.6407"},
    {"time":1776902400000,"open":"710.45","close":"709.52","high":"712.33","low":"702.37","volume":"64.6931"},
    {"time":1776816000000,"open":"707.01","close":"710.45","high":"711.37","low":"706.99","volume":"32.6974"},
    {"time":1776729600000,"open":"709.48","close":"707.01","high":"712.24","low":"702.81","volume":"94.3046"},
    {"time":1776643200000,"open":"710.87","close":"709.48","high":"710.87","low":"704.35","volume":"39.0267"},
    {"time":1776384000000,"open":"702.24","close":"710.88","high":"712.30","low":"701.71","volume":"78.8710"},
    {"time":1776297600000,"open":"700.62","close":"702.24","high":"702.74","low":"698.61","volume":"25.1046"},
    {"time":1776211200000,"open":"694.63","close":"700.62","high":"700.75","low":"693.07","volume":"87.3069"},
    {"time":1776124800000,"open":"686.43","close":"694.63","high":"695.10","low":"685.98","volume":"2327.3756"},
    {"time":1776038400000,"open":"680.66","close":"686.43","high":"686.94","low":"671.71","volume":"2243.3648"},
    {"time":1775779200000,"open":"679.20","close":"680.61","high":"682.03","low":"678.45","volume":"1685.1395"},
    {"time":1775692800000,"open":"674.75","close":"679.20","high":"681.12","low":"672.88","volume":"2107.6509"},
    {"time":1775606400000,"open":"673.43","close":"674.75","high":"678.08","low":"671.73","volume":"3385.1885"},
    {"time":1775520000000,"open":"657.87","close":"673.43","high":"675.08","low":"651.09","volume":"2713.5754"},
    {"time":1775433600000,"open":"655.91","close":"657.87","high":"659.47","low":"652.82","volume":"1635.0878"},
    {"time":1775088000000,"open":"654.73","close":"655.90","high":"657.88","low":"644.17","volume":"2625.3514"},
    {"time":1775001600000,"open":"652.26","close":"654.73","high":"658.50","low":"650.87","volume":"3390.2269"},
    {"time":1774915200000,"open":"630.55","close":"652.26","high":"652.32","low":"628.97","volume":"5932.7860"},
    {"time":1774828800000,"open":"632.89","close":"630.55","high":"640.98","low":"629.32","volume":"3860.6503"},
    {"time":1774569600000,"open":"647.88","close":"632.90","high":"649.75","low":"632.30","volume":"4130.5320"},
    {"time":1774483200000,"open":"655.58","close":"647.88","high":"657.18","low":"644.54","volume":"3944.7280"},
    {"time":1774396800000,"open":"657.60","close":"655.58","high":"660.83","low":"654.29","volume":"3273.8290"},
    {"time":1774310400000,"open":"656.74","close":"657.60","high":"660.21","low":"649.94","volume":"3733.6563"},
    {"time":1774224000000,"open":"653.60","close":"656.74","high":"666.57","low":"641.08","volume":"5221.7874"},
    {"time":1773964800000,"open":"661.11","close":"653.56","high":"661.16","low":"644.79","volume":"6236.9521"},
    {"time":1773878400000,"open":"660.95","close":"661.11","high":"663.26","low":"655.17","volume":"4218.9668"},
    {"time":1773792000000,"open":"670.24","close":"660.95","high":"674.92","low":"659.41","volume":"3387.1071"},
    {"time":1773705600000,"open":"668.82","close":"670.24","high":"674.38","low":"665.36","volume":"3724.9640"},
    {"time":1773619200000,"open":"663.18","close":"668.82","high":"671.90","low":"663.18","volume":"3290.8320"},
    {"time":1773360000000,"open":"666.42","close":"663.13","high":"672.33","low":"661.16","volume":"4133.1431"},
    {"time":1773273600000,"open":"671.21","close":"666.42","high":"674.39","low":"665.59","volume":"4242.5651"},
    {"time":1773187200000,"open":"677.98","close":"671.21","high":"680.53","low":"669.95","volume":"2863.3004"},
    {"time":1773100800000,"open":"675.32","close":"677.98","high":"683.34","low":"674.48","volume":"3166.2833"},
    {"time":1773014400000,"open":"671.62","close":"675.32","high":"679.86","low":"656.93","volume":"3609.8332"},
    {"time":1772755200000,"open":"681.98","close":"671.65","high":"683.37","low":"669.81","volume":"3989.6697"},
    {"time":1772668800000,"open":"686.28","close":"681.98","high":"687.74","low":"675.68","volume":"4068.2976"},
    {"time":1772582400000,"open":"678.70","close":"686.28","high":"687.07","low":"675.25","volume":"3026.7111"},
    {"time":1772496000000,"open":"685.27","close":"678.70","high":"685.53","low":"669.67","volume":"4019.1105"},
    {"time":1772409600000,"open":"683.99","close":"685.27","high":"688.57","low":"674.73","volume":"3342.4922"},
    {"time":1772150400000,"open":"686.41","close":"684.11","high":"688.98","low":"681.68","volume":"3387.5962"},
    {"time":1772064000000,"open":"692.00","close":"686.41","high":"694.17","low":"684.44","volume":"2832.1039"},
    {"time":1771977600000,"open":"687.52","close":"692.00","high":"695.11","low":"687.05","volume":"2207.7249"},
    {"time":1771891200000,"open":"682.77","close":"687.52","high":"688.24","low":"680.08","volume":"3001.0903"},
    {"time":1771804800000,"open":"689.65","close":"682.77","high":"689.93","low":"680.44","volume":"3567.7935"},
    {"time":1771545600000,"open":"684.76","close":"689.66","high":"689.90","low":"681.85","volume":"3595.3660"},
    {"time":1771459200000,"open":"686.14","close":"684.76","high":"687.85","low":"681.58","volume":"2219.0122"},
    {"time":1771372800000,"open":"681.57","close":"686.14","high":"689.13","low":"681.54","volume":"2858.7358"},
    {"time":1771286400000,"open":"681.16","close":"681.57","high":"684.90","low":"675.81","volume":"3091.1774"},
]


def is_synth(b):
    return float(b["volume"]) < 0.5 and float(b["high"]) - float(b["low"]) < 0.01 * float(b["close"])


def analyze_daily(daily, name):
    daily = [b for b in daily if float(b["volume"]) > 0]  # filtrar volum 0
    daily = sorted(daily, key=lambda b: int(b["time"]))
    print(f"\n{'=' * 75}")
    print(f"{name} - DAILY (n={len(daily)} dies amb volum real)")
    print(f"{'=' * 75}")
    ranges = [(float(b["high"])-float(b["low"]))/float(b["close"])*100 for b in daily]
    logs = [math.log(float(b["close"])/float(b["open"])) for b in daily]

    def pstats(label, lst):
        s = sorted(lst)
        n = len(s)
        return f"{label}: mean={statistics.mean(s):.2f}%  median={s[n//2]:.2f}%  p25={s[n//4]:.2f}%  p75={s[3*n//4]:.2f}%  p90={s[int(n*0.9)]:.2f}%"

    print(f"\nTot el periode (~90d, exclos festius):")
    print(f"  {pstats('Daily range (H-L)/C', ranges)}")
    sd = statistics.stdev(logs)
    print(f"  Vol log diari: {sd*100:.2f}%  -> annualitzada: {sd*math.sqrt(252)*100:.1f}%")

    # Ultims 30 dies
    last30 = daily[-30:]
    r30 = [(float(b["high"])-float(b["low"]))/float(b["close"])*100 for b in last30]
    print(f"\nUltims {len(last30)} dies reals:")
    print(f"  {pstats('Daily range', r30)}")
    l30 = [math.log(float(b["close"])/float(b["open"])) for b in last30]
    sd30 = statistics.stdev(l30)
    print(f"  Vol 30d: {sd30*100:.2f}%/dia  -> annualitzada: {sd30*math.sqrt(252)*100:.1f}%")

    # Ultims 7 dies
    last7 = daily[-7:]
    r7 = [(float(b["high"])-float(b["low"]))/float(b["close"])*100 for b in last7]
    print(f"\nUltims {len(last7)} dies reals:")
    print(f"  {pstats('Daily range', r7)}")
    print(f"  Daily ranges raw: {[f'{r:.2f}%' for r in r7]}")

    return {
        "current_price": float(daily[-1]["close"]),
        "median_range_all": statistics.median(ranges),
        "median_range_30d": statistics.median(r30),
        "p75_range_30d": sorted(r30)[3*len(r30)//4],
        "median_range_7d": statistics.median(r7),
        "annual_vol_30d": sd30 * math.sqrt(252) * 100,
        "annual_vol_all": sd * math.sqrt(252) * 100,
    }


def analyze_m5(name, fname):
    raw = (ROOT / fname).read_text(encoding="utf-8")
    obj = json.loads(raw)
    klines = obj["data"]["data"]["klines"]
    real = [b for b in klines if float(b["volume"]) > 0]
    print(f"\n--- {name} M5 (n={len(klines)} total, {len(real)} amb volum) ---")
    if not real:
        return None
    ranges = [(float(b["high"])-float(b["low"]))/float(b["close"])*100 for b in real]
    s = sorted(ranges)
    n = len(s)
    print(f"  M5 range: mean={statistics.mean(s):.3f}%  median={s[n//2]:.3f}%  p75={s[3*n//4]:.3f}%  p90={s[int(n*0.9)]:.3f}%")
    return {"m5_median": s[n//2], "m5_p75": s[3*n//4], "m5_p90": s[int(n*0.9)]}


def propose_grid(name, d, m5, allocation_usd, fee_per_trade_pct=0.05, min_order_usd=10):
    p = d["current_price"]
    median_range_30d = d["median_range_30d"]
    p75_30d = d["p75_range_30d"]
    annual_vol = d["annual_vol_30d"]

    # Width: cobreix ~p75 daily range * 2 per costat (4× total) - prou per absorbir swings normals
    # pero no tan ampli que els bars no facin cycles
    width_pct = 2.5 * p75_30d
    width_pct = max(width_pct, 8.0)
    width_pct = min(width_pct, 40.0)

    lower = p * (1 - width_pct / 200)
    upper = p * (1 + width_pct / 200)

    # Step: minim 0.40%, idealment 2-3× M5 p75 per fer ~3-5 cycles/dia
    fee_rt = 2 * fee_per_trade_pct
    min_step_econ = max(0.40, fee_rt * 4)  # 4× fees = profit decent
    if m5:
        # Step proporcional a M5 movement, perque cada cycle es realista
        step_pct = max(min_step_econ, 2.5 * m5["m5_p75"])
    else:
        step_pct = min_step_econ

    # Ajustar rows segons capital
    rows = max(2, int(width_pct / step_pct))
    cap_per_row = allocation_usd / rows
    if cap_per_row < 2 * min_order_usd:  # cada row necessita buy + sell = $20 min
        rows = int(allocation_usd / (2 * min_order_usd))
        if rows < 2:
            rows = 2
        step_pct = width_pct / rows
        cap_per_row = allocation_usd / rows

    gross_per_cycle_pct = step_pct - fee_rt
    cycles_per_day_est = median_range_30d / step_pct  # aprox

    print(f"\n{'#' * 75}")
    print(f"# PROPOSTA GRID {name}  -  capital ${allocation_usd}")
    print(f"{'#' * 75}")
    print(f"  Preu actual:       ${p:.2f}")
    print(f"  Median range 30d:  {median_range_30d:.2f}%")
    print(f"  P75 range 30d:     {p75_30d:.2f}%")
    print(f"  Vol annual (30d):  {annual_vol:.1f}%")
    print()
    print(f"  >> WIDTH:          {width_pct:.1f}%   [${lower:.2f}  -  ${upper:.2f}]")
    print(f"  >> STEP:           {step_pct:.2f}%   ({step_pct/100*p:.3f} USDT abs)")
    print(f"  >> ROWS:           {rows}")
    print(f"  >> $/row:          ${cap_per_row:.2f}")
    print(f"  >> Gross/cycle:    {gross_per_cycle_pct:.3f}% (step - 2×fee 0.10%)")
    print(f"  >> Cycles/dia est: ~{cycles_per_day_est:.1f}")
    print(f"  >> Profit/dia est: ~${cycles_per_day_est * gross_per_cycle_pct * allocation_usd / 100:.3f}")
    print(f"  >> Profit/mes est: ~${cycles_per_day_est * gross_per_cycle_pct * allocation_usd / 100 * 30:.2f}")


def main():
    # Capital total user (last known: $59933) - aprox $60000
    CAPITAL = 60000
    usox_alloc = CAPITAL * 0.10
    spyx_alloc = CAPITAL * 0.15

    print(f"Capital total assumit: ${CAPITAL:,}")
    print(f"USOX (10%) -> ${usox_alloc:,.0f}")
    print(f"SPYX (15%) -> ${spyx_alloc:,.0f}")

    d_usox = analyze_daily(USOX_DAILY, "USOX")
    d_spyx = analyze_daily(SPYX_DAILY, "SPYX")

    m5_usox = analyze_m5("USOX", "toolu_01Q7VzNLLwBwEh5fp16JE5i9.txt")
    m5_spyx = analyze_m5("SPYX", "toolu_017Wd4HJNAKpqvsT7upPWQGf.txt")

    propose_grid("USOX_USDT", d_usox, m5_usox, usox_alloc)
    propose_grid("SPYX_USDT", d_spyx, m5_spyx, spyx_alloc)


if __name__ == "__main__":
    main()
