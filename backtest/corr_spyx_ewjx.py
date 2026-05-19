"""
SPYX (S&P 500) i EWJX (Japan ETF):
- Correlacio amb cartera actual + USOX
- Anàlisi off-hours: el subjacent (SPY / EWJ) cotitza només horari NYSE,
  pero el token Pionex segueix futurs (ES, NKD) que sí cotitzen 24h.
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
    "USOX":  "USO",
    "SPYX":  "SPY",
    "EWJX":  "EWJ",
}


def main():
    end = pd.Timestamp.today()
    start = end - pd.Timedelta(days=5*365)
    print(f"Descarregant 5 anys ({start.date()} -> {end.date()})...\n")
    data = yf.download(list(TICKERS.values()), start=start, end=end,
                       progress=False, auto_adjust=True)
    close = data["Close"]
    rets = np.log(close / close.shift(1)).dropna()
    rets = rets.rename(columns={v: k for k, v in TICKERS.items()})
    order = list(TICKERS.keys())
    rets = rets[order]
    n = len(rets)
    print(f"Dies amb dades comunes: {n:,}")
    print(f"Periode: {rets.index[0].date()} -> {rets.index[-1].date()}\n")

    corr = rets.corr()

    print("=" * 80)
    print("MATRIU COMPLETA DE CORRELACIONS (5 anys, log-returns daily)")
    print("=" * 80)
    print(f"{'':<8}" + " ".join(f"{c:>8}" for c in order))
    print("-" * 80)
    for idx in order:
        vals = " ".join(f"{corr.loc[idx, c]:>+8.3f}" for c in order)
        print(f"{idx:<8} {vals}")

    print()
    print("=" * 80)
    print("CORRELACIO de SPYX i EWJX vs cartera actual + USOX")
    print("=" * 80)
    print(f"{'Actiu':<10} {'vs SPYX':>10} {'vs EWJX':>10}  Veredicte")
    print("-" * 80)
    def verd(c):
        ac = abs(c)
        if ac < 0.15: return "MOLT descorrelat"
        if ac < 0.30: return "Descorrelat"
        if ac < 0.50: return "Moderat"
        if ac < 0.70: return "Correlat"
        return "MOLT correlat"
    for a in ["PAXG", "BTC", "ETH", "SOL", "USOX"]:
        cs = corr.loc[a, "SPYX"]
        ce = corr.loc[a, "EWJX"]
        print(f"{a:<10} {cs:>+10.3f} {ce:>+10.3f}  SPYX={verd(cs)} | EWJX={verd(ce)}")

    cse = corr.loc["SPYX", "EWJX"]
    print(f"\nSPYX <-> EWJX entre ells: {cse:+.3f}")

    # Volatilitat anual
    print()
    print("=" * 80)
    print("VOLATILITAT ANUALITZADA (sd * sqrt(252))")
    print("=" * 80)
    for a in order:
        vol = rets[a].std() * np.sqrt(252) * 100
        cumret = (1 + rets[a]).cumprod()
        peak = cumret.cummax()
        dd = ((cumret - peak) / peak).min() * 100
        print(f"  {a:<8} vol={vol:>5.1f}%   MaxDD 5y={dd:>6.1f}%")

    # Mitjana |corr| amb cartera actual (PAXG,BTC,ETH,SOL)
    print()
    print("=" * 80)
    print("DIVERSIFICACIO: |corr| mitjana amb la cartera actual (PAXG+BTC+ETH+SOL)")
    print("=" * 80)
    bloc = ["PAXG", "BTC", "ETH", "SOL"]
    for cand in ["USOX", "SPYX", "EWJX"]:
        avg = np.mean([abs(corr.loc[cand, a]) for a in bloc])
        print(f"  {cand}: |corr| mitja = {avg:.3f}")

    # Tail correlation en stress cripto
    print()
    print("=" * 80)
    print("TAIL: dies amb BTC <-5% (51 dies stress cripto)")
    print("=" * 80)
    stress = rets["BTC"] < -0.05
    ns = stress.sum()
    print(f"Dies stress: {ns}")
    print(f"{'Actiu':<8} {'Tail corr BTC':>15} {'Mediana ret':>14} {'% positiu':>11}")
    print("-" * 80)
    for a in ["PAXG", "USOX", "SPYX", "EWJX"]:
        sub = rets[a][stress]
        tc = sub.corr(rets["BTC"][stress])
        med = sub.median() * 100
        pos = (sub > 0).sum() / ns * 100
        print(f"{a:<8} {tc:>+15.3f} {med:>+13.2f}% {pos:>10.1f}%")


if __name__ == "__main__":
    main()
