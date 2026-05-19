"""
Backtest 5y i 10y de carteres amb diferents ponderacions logiques.
Actius: PAXG, BTC, ETH, SOL, USOX, SPYX.
SOL no existeix abans 2020-04, per al 10y se substitueix per BTC.
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

# Diferents combinacions logiques
PORTFOLIOS = {
    "ACTUAL (sense USOX/SPYX)":         {"PAXG": 0.40, "BTC": 0.30, "ETH": 0.20, "SOL": 0.10},
    "PROPOSADA (30/22/15/8/10/15)":     {"PAXG": 0.30, "BTC": 0.22, "ETH": 0.15, "SOL": 0.08, "USOX": 0.10, "SPYX": 0.15},
    "OR-pesat (40/15/10/5/10/20)":      {"PAXG": 0.40, "BTC": 0.15, "ETH": 0.10, "SOL": 0.05, "USOX": 0.10, "SPYX": 0.20},
    "OR-pesat-SPYpoc (40/20/12/8/10/10)": {"PAXG": 0.40, "BTC": 0.20, "ETH": 0.12, "SOL": 0.08, "USOX": 0.10, "SPYX": 0.10},
    "BTC-dominant (25/30/10/5/10/20)":  {"PAXG": 0.25, "BTC": 0.30, "ETH": 0.10, "SOL": 0.05, "USOX": 0.10, "SPYX": 0.20},
    "Balancejada-25 (25/20/15/10/10/20)": {"PAXG": 0.25, "BTC": 0.20, "ETH": 0.15, "SOL": 0.10, "USOX": 0.10, "SPYX": 0.20},
    "Defensiva-max (35/15/10/5/10/25)": {"PAXG": 0.35, "BTC": 0.15, "ETH": 0.10, "SOL": 0.05, "USOX": 0.10, "SPYX": 0.25},
    "Cripto-light (30/15/10/5/15/25)":  {"PAXG": 0.30, "BTC": 0.15, "ETH": 0.10, "SOL": 0.05, "USOX": 0.15, "SPYX": 0.25},
    "Cripto-heavy (20/30/20/10/10/10)": {"PAXG": 0.20, "BTC": 0.30, "ETH": 0.20, "SOL": 0.10, "USOX": 0.10, "SPYX": 0.10},
    "USOX-max (30/20/12/8/15/15)":      {"PAXG": 0.30, "BTC": 0.20, "ETH": 0.12, "SOL": 0.08, "USOX": 0.15, "SPYX": 0.15},
}


def stats(rets, weights):
    """Retorna stats d'una cartera. Si un actiu no esta en rets, redistribueix proporcionalment."""
    avail = {a: w for a, w in weights.items() if a in rets.columns and not rets[a].isna().all()}
    if not avail:
        return None
    s = sum(avail.values())
    avail = {a: w/s for a, w in avail.items()}  # normalitza

    port = pd.Series(0.0, index=rets.index)
    for a, w in avail.items():
        port += w * rets[a].fillna(0)
    port = port.dropna()
    cum = (1 + port).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    annual_ret = (cum.iloc[-1] ** (252 / len(port)) - 1) * 100
    return {
        "final_pct": (cum.iloc[-1] - 1) * 100,
        "annual_pct": annual_ret,
        "max_dd_pct": dd.min() * 100,
        "vol_annual": port.std() * np.sqrt(252) * 100,
        "sharpe": (port.mean() * 252) / (port.std() * np.sqrt(252)) if port.std() > 0 else 0,
        "worst_day": dd.idxmin().date(),
        "n_days": len(port),
    }


def run_backtest(years, all_data):
    end = pd.Timestamp.today()
    start = end - pd.Timedelta(days=years*365)
    close = all_data["Close"].loc[start:end]
    rets = close.pct_change().dropna(how="all")
    rets = rets.rename(columns={v: k for k, v in TICKERS.items()})

    real_start = rets.dropna(how="all").index[0]
    real_end = rets.dropna(how="all").index[-1]
    print(f"\n{'=' * 110}")
    print(f"BACKTEST {years} ANYS ({real_start.date()} -> {real_end.date()})")
    print(f"{'=' * 110}")

    # Anota quins actius tenen dades en tot el periode
    available = {}
    for a in TICKERS.keys():
        if a in rets.columns:
            valid = rets[a].dropna()
            if len(valid) > 0:
                available[a] = (valid.index[0].date(), valid.index[-1].date(), len(valid))

    print(f"\nDisponibilitat dades:")
    for a, (s, e, n) in available.items():
        print(f"  {a:<6} {s} -> {e}  ({n:,} dies)")

    print(f"\n{'Cartera':<42} {'Final':>10} {'Anual':>9} {'MaxDD':>10} {'Vol':>8} {'Sharpe':>8}  PitjorDia")
    print("-" * 110)
    for name, w in PORTFOLIOS.items():
        s = stats(rets, w)
        if s is None:
            print(f"{name:<42}  (no hi ha dades)")
            continue
        print(f"{name:<42} {s['final_pct']:>+9.1f}% {s['annual_pct']:>+8.1f}% "
              f"{s['max_dd_pct']:>+9.1f}% {s['vol_annual']:>7.1f}% {s['sharpe']:>+7.2f}  {s['worst_day']}")


def main():
    end = pd.Timestamp.today()
    start = end - pd.Timedelta(days=11*365)  # carrega 11y per si de cas
    print(f"Descarregant dades fins {start.date()} -> {end.date()}...")
    data = yf.download(list(TICKERS.values()), start=start, end=end,
                       progress=False, auto_adjust=True)

    for years in [5, 10]:
        run_backtest(years, data)

    print()
    print("=" * 110)
    print("EXPOSICIONS PER BLOC")
    print("=" * 110)
    blocs = {
        "Or": ["PAXG"],
        "Cripto": ["BTC", "ETH", "SOL"],
        "Petroli": ["USOX"],
        "SP500": ["SPYX"],
    }
    print(f"{'Cartera':<42}" + " ".join(f"{b:>10}" for b in blocs.keys()) + f"  {'Cripto:Defensiu':>18}")
    print("-" * 110)
    for name, w in PORTFOLIOS.items():
        line = f"{name:<42}"
        cripto = sum(w.get(a, 0) for a in blocs["Cripto"])
        defensiu = sum(w.get(a, 0) for a in blocs["Or"] + blocs["Petroli"] + blocs["SP500"])
        for b, assets in blocs.items():
            tot = sum(w.get(a, 0) for a in assets) * 100
            line += f"{tot:>9.0f}%"
        ratio = f"{cripto*100:.0f}:{defensiu*100:.0f}"
        line += f"  {ratio:>18}"
        print(line)


if __name__ == "__main__":
    main()
