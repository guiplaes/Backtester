"""
Co-drop analysis: quan tots els actius cauen alhora?
+ Tail correlation: correlació en els dies pitjors.
"""
import warnings
warnings.filterwarnings("ignore")
import yfinance as yf
import numpy as np
import pandas as pd

TICKERS = {
    "PAXG":  "GLD",
    "BTC":   "BTC-USD",
    "ETH":   "ETH-USD",
    "SOL":   "SOL-USD",
    "LMTX":  "LMT",
    "LLYX":  "LLY",
    "CVXX":  "CVX",
}


def main():
    end = pd.Timestamp.today()
    start = end - pd.Timedelta(days=5*365)
    print(f"Descarregant 5 anys ({start.date()} -> {end.date()})...")
    data = yf.download(list(TICKERS.values()), start=start, end=end, progress=False, auto_adjust=True)
    close = data["Close"]
    rets = close.pct_change().dropna()
    rets = rets.rename(columns={v: k for k, v in TICKERS.items()})
    rets = rets[list(TICKERS.keys())]
    n = len(rets)
    print(f"Dies amb dades comunes: {n:,}\n")

    # ── 1) DESCORRELACIÓ AMB CRIPTO (only) ──
    cripto_avg = rets[["BTC", "ETH", "SOL"]].mean(axis=1)
    print("=" * 90)
    print("CORRELACIÓ amb el bloc cripto (mitja BTC+ETH+SOL)")
    print("=" * 90)
    print(f"{'Asset':<10} {'Corr':>10}  Veredicte")
    print("-" * 90)
    others = ["PAXG", "LMTX", "LLYX", "CVXX"]
    for asset in others:
        c = rets[asset].corr(cripto_avg)
        verdict = "MOLT descorrelat" if abs(c) < 0.15 else ("Descorrelat" if abs(c) < 0.30 else "Correlat")
        print(f"{asset:<10} {c:>+10.3f}  {verdict}")

    # ── 2) CO-DROP DAYS: dies on BTC i [each asset] cauen tots dos ──
    print()
    print("=" * 90)
    print("CO-DROP: dies on BTC cau i l'altre asset TAMBÉ cau (vs total dies bear BTC)")
    print("=" * 90)
    btc_down = rets["BTC"] < 0
    btc_down_count = btc_down.sum()
    print(f"Total dies BTC negatiu: {btc_down_count} ({btc_down_count/n*100:.1f}%)")
    print()
    print(f"{'Asset':<10} {'Dies caient amb BTC':>20} {'% dels BTC down':>18}  {'corr en BTC down':>18}")
    print("-" * 90)
    for asset in ["PAXG", "LMTX", "LLYX", "CVXX"]:
        also_down = ((rets[asset] < 0) & btc_down).sum()
        pct = also_down / btc_down_count * 100
        # Tail correlation (only BTC-down days)
        tail_corr = rets[asset][btc_down].corr(rets["BTC"][btc_down])
        print(f"{asset:<10} {also_down:>20} {pct:>17.1f}% {tail_corr:>+18.3f}")

    # ── 3) STRESS DAYS: BTC cau > 5% ──
    print()
    print("=" * 90)
    print("STRESS BTC: dies amb BTC <-5% — com es comporten els altres?")
    print("=" * 90)
    stress = rets["BTC"] < -0.05
    n_stress = stress.sum()
    print(f"Total dies stress BTC (<-5%): {n_stress}")
    print()
    print(f"{'Asset':<10} {'Mediana rendiment':>18} {'% dies també <-2%':>20} {'% dies POSITIU':>18}")
    print("-" * 90)
    for asset in ["PAXG", "LMTX", "LLYX", "CVXX", "ETH", "SOL"]:
        sub = rets[asset][stress]
        med = sub.median() * 100
        also_bad = (sub < -0.02).sum() / n_stress * 100
        positive = (sub > 0).sum() / n_stress * 100
        print(f"{asset:<10} {med:>+17.2f}% {also_bad:>19.1f}% {positive:>17.1f}%")

    # ── 4) TOTS els actius cauen alhora ──
    print()
    print("=" * 90)
    print("TOTS-CAIGUTS: dies en què TOTS els actius non-cripto cauen")
    print("=" * 90)
    all_others_down = (rets["PAXG"] < 0) & (rets["LMTX"] < 0) & (rets["LLYX"] < 0) & (rets["CVXX"] < 0)
    n_all_others_down = all_others_down.sum()
    print(f"Dies amb PAXG+LMTX+LLYX+CVXX tots negatius: {n_all_others_down}/{n} ({n_all_others_down/n*100:.1f}%)")
    # I de quants dies també BTC cau?
    everyone_down = all_others_down & (rets["BTC"] < 0)
    n_everyone = everyone_down.sum()
    print(f"  d'aquests, també amb BTC negatiu:        {n_everyone}/{n} ({n_everyone/n*100:.1f}%)")
    print(f"  pure tots-caiguts (incloent BTC):        {n_everyone} dies en 5 anys")
    print()

    # Quins dies han sigut?
    if n_everyone > 0:
        print("Pitjors 10 dies amb tots cayent:")
        sub = rets[everyone_down].copy()
        sub["total"] = sub.sum(axis=1)
        sub = sub.sort_values("total").head(10)
        print(f"\n{'Data':<12} {'PAXG':>8} {'BTC':>8} {'ETH':>8} {'SOL':>8} {'LMTX':>8} {'LLYX':>8} {'CVXX':>8} {'Total':>8}")
        print("-" * 90)
        for d, r in sub.iterrows():
            print(f"{d.date()} " +
                  " ".join(f"{r[c]*100:>+7.1f}%" for c in ["PAXG", "BTC", "ETH", "SOL", "LMTX", "LLYX", "CVXX"]) +
                  f" {r['total']*100:>+7.1f}%")

    # ── 5) Drawdown conjunt: pèrdua màxima del portfolio teòric ──
    print()
    print("=" * 90)
    print("PORTFOLIO PROPOSAT — Max Drawdown agregat")
    print("=" * 90)
    weights = {"PAXG": 0.25, "BTC": 0.20, "LMTX": 0.15, "LLYX": 0.15, "CVXX": 0.15, "ETH": 0, "SOL": 0}
    # Cumulative return per asset
    cumret = (1 + rets[list(weights.keys())]).cumprod()
    port_value = sum(weights[a] * cumret[a] for a in weights)
    peak = port_value.cummax()
    dd = (port_value - peak) / peak
    max_dd = dd.min() * 100
    print(f"Portfolio PROPOSAT (PAXG 25% + BTC 20% + LMTX 15% + LLYX 15% + CVXX 15% + cash 10%):")
    print(f"  Max drawdown 5y: {max_dd:.1f}%")
    print(f"  Dia max DD:      {dd.idxmin().date()}")
    print(f"  Rendiment final: {(port_value.iloc[-1]/port_value.iloc[0]-1)*100*0.9:.1f}% (90% invertit)")

    # Compara amb cartera actual (40 PAXG, 30 BTC, 20 ETH, 10 SOL)
    weights2 = {"PAXG": 0.40, "BTC": 0.30, "ETH": 0.20, "SOL": 0.10, "LMTX": 0, "LLYX": 0, "CVXX": 0}
    cumret2 = (1 + rets[list(weights2.keys())]).cumprod()
    port2 = sum(weights2[a] * cumret2[a] for a in weights2)
    peak2 = port2.cummax()
    dd2 = (port2 - peak2) / peak2
    max_dd2 = dd2.min() * 100
    print(f"\nPortfolio ACTUAL (PAXG 40% + BTC 30% + ETH 20% + SOL 10%):")
    print(f"  Max drawdown 5y: {max_dd2:.1f}%")
    print(f"  Dia max DD:      {dd2.idxmin().date()}")
    print(f"  Rendiment final: {(port2.iloc[-1]/port2.iloc[0]-1)*100:.1f}%")


if __name__ == "__main__":
    main()
