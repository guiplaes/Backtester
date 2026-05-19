"""
Matriu de correlació completa: PAXG + BTC + ETH + SOL + LMTX + LLYX + CVXX
Últims 5 anys de daily returns.
"""
import warnings
warnings.filterwarnings("ignore")
import yfinance as yf
import numpy as np
import pandas as pd

TICKERS = {
    "PAXG (Or)":     "GLD",
    "BTC":           "BTC-USD",
    "ETH":           "ETH-USD",
    "SOL":           "SOL-USD",
    "LMTX (Lockheed)": "LMT",
    "LLYX (Eli Lilly)": "LLY",
    "CVXX (Chevron)": "CVX",
    # Bonus per context
    "SPYX (S&P500)": "SPY",
    "QQQX (Nasdaq)": "QQQ",
}


def main():
    end = pd.Timestamp.today()
    start = end - pd.Timedelta(days=5*365)
    print(f"Descarregant 5 anys ({start.date()} -> {end.date()})...\n")
    tickers = list(TICKERS.values())
    data = yf.download(tickers, start=start, end=end, progress=False, auto_adjust=True)
    close = data["Close"] if "Close" in data.columns.get_level_values(0) else data
    rets = np.log(close / close.shift(1)).dropna()
    # Renamear columnes amb noms amigables
    inv_map = {v: k for k, v in TICKERS.items()}
    rets = rets.rename(columns=inv_map)
    # Ordenar columnes
    order = list(TICKERS.keys())
    rets = rets[order]

    print(f"Dies amb dades comunes: {len(rets):,}")
    print(f"Període real: {rets.index[0].date()} -> {rets.index[-1].date()}\n")

    corr = rets.corr()
    print("=" * 100)
    print("MATRIU DE CORRELACIÓ (rendiments diaris log, 5 anys)")
    print("=" * 100)
    print()
    # Imprimeix matriu
    cols_short = [c.split(" ")[0] for c in corr.columns]
    print(f"{'':<20}" + " ".join(f"{c:>9}" for c in cols_short))
    print("-" * 100)
    for idx, row in corr.iterrows():
        idx_short = idx.split(" ")[0] if " " in idx else idx
        vals = " ".join(f"{v:>+9.3f}" for v in row.values)
        print(f"{idx:<20} {vals}")
    print()

    # Heatmap simplificat amb colors per correlació
    print("=" * 100)
    print("INTERPRETACIÓ (correlació mitjana entre cada parell, valor absolut)")
    print("=" * 100)
    # Diagonal triangular sup
    print()
    print("PARELLS més DESCORRELATS (|corr| < 0.2):")
    descorrelats = []
    for i in range(len(order)):
        for j in range(i+1, len(order)):
            c = corr.iloc[i, j]
            descorrelats.append((order[i], order[j], c))
    descorrelats.sort(key=lambda x: abs(x[2]))
    for a, b, c in descorrelats[:10]:
        verdict = "MOLT descorrelat" if abs(c) < 0.2 else "Moderat"
        print(f"  {a:<22} vs {b:<22}  corr = {c:+.3f}  ({verdict})")

    print()
    print("PARELLS més CORRELATS (|corr| > 0.5):")
    sorted_corr = sorted(descorrelats, key=lambda x: -abs(x[2]))
    for a, b, c in sorted_corr[:8]:
        print(f"  {a:<22} vs {b:<22}  corr = {c:+.3f}")


if __name__ == "__main__":
    main()
