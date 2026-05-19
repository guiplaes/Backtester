"""
Comparem 4 carteres en 5 anys reals: drawdown + rendiment.
"""
import warnings
warnings.filterwarnings("ignore")
import yfinance as yf
import numpy as np
import pandas as pd

TICKERS = {
    "PAXG": "GLD", "BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD",
    "GSGX": "GSG", "CPERX": "CPER", "USOX": "USO",
    "LMTX": "LMT", "LLYX": "LLY", "CVXX": "CVX",
    "SPYX": "SPY",
}

PORTFOLIOS = {
    "ACTUAL (40-30-20-10)": {
        "PAXG": 0.40, "BTC": 0.30, "ETH": 0.20, "SOL": 0.10,
    },
    "A) Conservadora 100% ETFs/baskets": {
        "PAXG": 0.35, "BTC": 0.15, "ETH": 0.05, "SOL": 0.05,
        "GSGX": 0.20, "CPERX": 0.10, "USOX": 0.05,
        # 5% cash implícit
    },
    "B) Mixed (defensa amb single stocks 20%)": {
        "PAXG": 0.30, "BTC": 0.15, "ETH": 0.05, "SOL": 0.05,
        "GSGX": 0.15, "LMTX": 0.10, "LLYX": 0.10, "CVXX": 0.05,
        # 5% cash implícit
    },
    "C) Diversificació màxima (single stocks baixos)": {
        "PAXG": 0.25, "BTC": 0.10, "ETH": 0.05, "SOL": 0.05,
        "GSGX": 0.15, "CPERX": 0.05, "LMTX": 0.08, "LLYX": 0.08, "CVXX": 0.07,
        # 12% cash implícit
    },
}


def main():
    end = pd.Timestamp.today()
    start = end - pd.Timedelta(days=5*365)
    print(f"Backtest 5 anys ({start.date()} -> {end.date()})\n")
    tickers = list(set(TICKERS.values()))
    data = yf.download(tickers, start=start, end=end, progress=False, auto_adjust=True)
    close = data["Close"]
    rets = close.pct_change().dropna()
    rets = rets.rename(columns={v: k for k, v in TICKERS.items()})

    print(f"{'Portfolio':<55} {'Final':>10} {'MaxDD':>10} {'Vol annual':>12} {'Sharpe':>9}")
    print("-" * 100)

    for name, w in PORTFOLIOS.items():
        # Validate sum
        total_w = sum(w.values())
        if total_w > 1:
            print(f"  WARNING {name}: weights sum {total_w}")
            continue
        # Daily return ponderat
        port_rets = pd.Series(0, index=rets.index)
        for asset, weight in w.items():
            if asset in rets.columns:
                port_rets += weight * rets[asset]
        # Cash gives 0 return (or could add 5% APY ~ 0.013% daily)
        cash_w = 1 - total_w
        port_rets += cash_w * 0.00013  # 5% APY = ~0.013%/day

        # Cumulative + drawdown
        cum = (1 + port_rets).cumprod()
        peak = cum.cummax()
        dd = (cum - peak) / peak
        max_dd = dd.min() * 100
        final = (cum.iloc[-1] - 1) * 100
        vol = port_rets.std() * np.sqrt(252) * 100
        mean_annual = port_rets.mean() * 252 * 100
        sharpe = mean_annual / vol if vol > 0 else 0

        print(f"{name:<55} {final:>+9.1f}% {max_dd:>+9.1f}% {vol:>11.1f}% {sharpe:>8.2f}")

    print()
    print("=" * 100)
    print("DETALL — pitjor moment de cada portfolio")
    print("=" * 100)
    for name, w in PORTFOLIOS.items():
        port_rets = pd.Series(0, index=rets.index)
        for a, wt in w.items():
            if a in rets.columns: port_rets += wt * rets[a]
        port_rets += (1 - sum(w.values())) * 0.00013
        cum = (1 + port_rets).cumprod()
        peak = cum.cummax()
        dd = (cum - peak) / peak
        worst = dd.idxmin()
        print(f"\n{name}")
        print(f"  Max DD: {dd.min()*100:.1f}% a {worst.date()}")
        # Què passava aquell dia?
        print(f"  Aquell dia: PAXG={rets['PAXG'][worst]*100:+.1f}%  BTC={rets['BTC'][worst]*100:+.1f}%  GSGX={rets['GSGX'][worst]*100:+.1f}%")


if __name__ == "__main__":
    main()
