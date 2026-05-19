"""
Comparem 3 opcions de cartera amb USOX + SPYX afegits.
Buy-and-hold benchmark per veure DD, vol i rendiment 5 anys.
"""
import warnings
warnings.filterwarnings("ignore")
import yfinance as yf
import numpy as np
import pandas as pd

TICKERS = {
    "PAXG": "GLD", "BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD",
    "USOX": "USO", "SPYX": "SPY",
}

PORTFOLIOS = {
    "ACTUAL (40/30/20/10)": {
        "PAXG": 0.40, "BTC": 0.30, "ETH": 0.20, "SOL": 0.10,
    },
    "A) Conservadora": {
        # Redueix cripto, anclatge PAXG, USOX i SPYX defensius
        "PAXG": 0.35, "BTC": 0.20, "ETH": 0.10, "SOL": 0.05,
        "USOX": 0.15, "SPYX": 0.15,
    },
    "B) Equilibrada (recomanada)": {
        "PAXG": 0.30, "BTC": 0.22, "ETH": 0.13, "SOL": 0.05,
        "USOX": 0.18, "SPYX": 0.12,
    },
    "C) Pro-cripto (manté pes cripto)": {
        "PAXG": 0.28, "BTC": 0.25, "ETH": 0.15, "SOL": 0.07,
        "USOX": 0.15, "SPYX": 0.10,
    },
}


def stats(rets, weights):
    port = pd.Series(0.0, index=rets.index)
    for a, w in weights.items():
        if a in rets.columns:
            port += w * rets[a]
    cum = (1 + port).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    return {
        "final_pct": (cum.iloc[-1] - 1) * 100,
        "max_dd_pct": dd.min() * 100,
        "vol_annual": port.std() * np.sqrt(252) * 100,
        "sharpe": (port.mean() * 252) / (port.std() * np.sqrt(252)) if port.std() > 0 else 0,
        "worst_day": dd.idxmin().date(),
    }


def main():
    end = pd.Timestamp.today()
    start = end - pd.Timedelta(days=5*365)
    print(f"Backtest buy-and-hold 5 anys ({start.date()} -> {end.date()})\n")
    data = yf.download(list(TICKERS.values()), start=start, end=end,
                       progress=False, auto_adjust=True)
    close = data["Close"]
    rets = close.pct_change().dropna()
    rets = rets.rename(columns={v: k for k, v in TICKERS.items()})

    print(f"{'Cartera':<32} {'Final':>10} {'MaxDD':>10} {'Vol':>9} {'Sharpe':>8}  PitjorDia")
    print("-" * 90)
    for name, w in PORTFOLIOS.items():
        s = stats(rets, w)
        print(f"{name:<32} {s['final_pct']:>+9.1f}% {s['max_dd_pct']:>+9.1f}% "
              f"{s['vol_annual']:>8.1f}% {s['sharpe']:>+7.2f}  {s['worst_day']}")

    # Exposicio per bloc
    print()
    print("=" * 90)
    print("EXPOSICIO PER BLOC")
    print("=" * 90)
    blocs = {
        "Or/refugi (PAXG)":      ["PAXG"],
        "Cripto":                ["BTC", "ETH", "SOL"],
        "Commodities (USOX)":    ["USOX"],
        "Renda var (SPYX)":      ["SPYX"],
    }
    print(f"{'Cartera':<32}" + " ".join(f"{b[:12]:>13}" for b in blocs.keys()))
    print("-" * 90)
    for name, w in PORTFOLIOS.items():
        line = f"{name:<32}"
        for b, assets in blocs.items():
            tot = sum(w.get(a, 0) for a in assets) * 100
            line += f"{tot:>12.0f}%"
        print(line)


if __name__ == "__main__":
    main()
