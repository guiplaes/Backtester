"""
Correlacions entre els actius actuals de la cartera (PAXG, BTC, ETH, SOL)
i els candidats nous (USOX = USO, CPERX = CPER).
Període: 5 anys de daily returns.
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
    "CPERX": "CPER",
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
    print(f"Periode real: {rets.index[0].date()} -> {rets.index[-1].date()}\n")

    corr = rets.corr()

    print("=" * 70)
    print("MATRIU COMPLETA DE CORRELACIONS (5 anys, log-returns daily)")
    print("=" * 70)
    print(f"{'':<8}" + " ".join(f"{c:>8}" for c in order))
    print("-" * 70)
    for idx in order:
        vals = " ".join(f"{corr.loc[idx, c]:>+8.3f}" for c in order)
        print(f"{idx:<8} {vals}")

    print()
    print("=" * 70)
    print("CORRELACIO de USOX i CPERX vs cada actiu actual")
    print("=" * 70)
    print(f"{'Actiu actual':<14} {'vs USOX':>10} {'vs CPERX':>11}  Veredicte")
    print("-" * 70)
    for a in ["PAXG", "BTC", "ETH", "SOL"]:
        cu = corr.loc[a, "USOX"]
        cc = corr.loc[a, "CPERX"]
        def verd(c):
            ac = abs(c)
            if ac < 0.15: return "MOLT descorrelat"
            if ac < 0.30: return "Descorrelat"
            if ac < 0.50: return "Moderat"
            return "CORRELAT"
        print(f"{a:<14} {cu:>+10.3f} {cc:>+11.3f}  USOX={verd(cu)} | CPERX={verd(cc)}")

    # Correlacio entre USOX i CPERX
    print()
    cuc = corr.loc["USOX", "CPERX"]
    print(f"USOX <-> CPERX: {cuc:+.3f}  (entre ells)")

    # Mitjana correlacio de cada nou candidat amb el bloc actual
    print()
    print("=" * 70)
    print("RESUM: correlacio mitjana de cada candidat amb la cartera actual")
    print("=" * 70)
    blocs_actuals = ["PAXG", "BTC", "ETH", "SOL"]
    for cand in ["USOX", "CPERX"]:
        avg = np.mean([abs(corr.loc[cand, a]) for a in blocs_actuals])
        print(f"  {cand}: |corr| mitjana amb cartera = {avg:.3f}")

    # Tail correlation: dies de stress (BTC < -5%)
    print()
    print("=" * 70)
    print("TAIL CORRELATION: dies amb BTC <-5% (stress cripto)")
    print("=" * 70)
    stress = rets["BTC"] < -0.05
    n_stress = stress.sum()
    print(f"Dies stress (BTC <-5%): {n_stress}")
    print(f"{'Actiu':<8} {'Tail corr BTC':>15} {'Mediana ret':>14} {'% positiu':>11}")
    print("-" * 70)
    for a in ["PAXG", "USOX", "CPERX"]:
        sub = rets[a][stress]
        tc = sub.corr(rets["BTC"][stress])
        med = sub.median() * 100
        pos = (sub > 0).sum() / n_stress * 100
        print(f"{a:<8} {tc:>+15.3f} {med:>+13.2f}% {pos:>10.1f}%")


if __name__ == "__main__":
    main()
