"""
Screening dels actius SPOT tokenitzats de Pionex (excloent or).
Mesura per cada un:
  - Max drawdown peak-to-trough en finestres de 3 / 5 / 10 / 20 anys
  - Volatilitat (stdev rendiment diari) en mateixes finestres

Usa Yahoo Finance per dades històriques del SUBJACENT (no del token).
Així tenim 10-20 anys d'historial real.
"""
from __future__ import annotations
import warnings
warnings.filterwarnings("ignore")

import yfinance as yf
import numpy as np
import pandas as pd
from datetime import datetime, timedelta


# Mapeig token Pionex -> ticker subjacent Yahoo Finance
ASSETS = {
    "SPYX  (S&P 500 ETF)":         "SPY",
    "QQQX  (Nasdaq-100 ETF)":      "QQQ",
    "AAPLX (Apple)":               "AAPL",
    "MSFTX (Microsoft)":           "MSFT",
    "GOOGLX (Alphabet)":           "GOOGL",
    "AMZNX (Amazon)":              "AMZN",
    "METAX (Meta)":                "META",
    "NVDAX (NVIDIA)":              "NVDA",
    "TSLAX (Tesla)":               "TSLA",
    "USOX  (Oil ETF)":             "USO",
    "SLVX  (Silver ETF)":          "SLV",
    # Defense (high liquidity equity)
    "LMTX  (Lockheed Martin)":     "LMT",
    "RTXX  (RTX/Raytheon)":        "RTX",
    "LLYX  (Eli Lilly)":           "LLY",
    "UNHX  (UnitedHealth)":        "UNH",
    "CVXX  (Chevron)":             "CVX",
    "NKEX  (Nike)":                "NKE",
    "NOKX  (Nokia)":               "NOK",
    # Geographic / commodity baskets
    "EWJX  (Japan ETF)":           "EWJ",
    "VGKX  (Europe ETF)":          "VGK",
    "CPERX (Copper ETF)":          "CPER",
    "UNGX  (Natural Gas ETF)":     "UNG",
    "URAX  (Uranium miners)":      "URA",
    "GSGX  (Goldman Commodities)": "GSG",
}

WINDOWS_YEARS = [3, 5, 10, 20]


def compute_metrics(prices: pd.Series, years: int) -> dict:
    """Calcula max DD i volatilitat per la finestra dels últims N anys."""
    if prices.empty:
        return {"max_dd": None, "vol_daily": None, "vol_annual": None, "start": None, "end": None, "days": 0}
    end_date = prices.index[-1]
    start_date = end_date - timedelta(days=int(years * 365.25))
    window = prices[prices.index >= start_date]
    if len(window) < 50:
        return {"max_dd": None, "vol_daily": None, "vol_annual": None, "start": None, "end": None, "days": len(window)}

    # Max drawdown peak-to-trough
    cummax = window.cummax()
    dd = (window - cummax) / cummax
    max_dd = dd.min() * 100  # % negatiu

    # Volatilitat (stdev del rendiment diari log)
    rets = np.log(window / window.shift(1)).dropna()
    vol_daily = rets.std() * 100  # %
    vol_annual = vol_daily * np.sqrt(252)  # anualitzat (252 dies de trading)

    return {
        "max_dd": float(max_dd),
        "vol_daily": float(vol_daily),
        "vol_annual": float(vol_annual),
        "start": window.index[0].strftime("%Y-%m-%d"),
        "end": window.index[-1].strftime("%Y-%m-%d"),
        "days": len(window),
    }


def main():
    print("Descarregant dades històriques de Yahoo Finance...")
    print()

    results = {}
    for label, ticker in ASSETS.items():
        try:
            data = yf.download(ticker, period="max", progress=False, auto_adjust=True)
            if data.empty or "Close" not in data.columns:
                print(f"  {label:35s} ({ticker}): NO DATA")
                continue
            prices = data["Close"].squeeze() if hasattr(data["Close"], "squeeze") else data["Close"]
            prices = prices.dropna()
            metrics_per_window = {}
            for y in WINDOWS_YEARS:
                metrics_per_window[y] = compute_metrics(prices, y)
            results[label] = {"ticker": ticker, "windows": metrics_per_window}
            inception = prices.index[0].strftime("%Y-%m-%d")
            full_history_years = (prices.index[-1] - prices.index[0]).days / 365.25
            print(f"  {label:35s} ({ticker:6s}): {len(prices):,} dies, des de {inception} ({full_history_years:.1f}y)")
        except Exception as e:
            print(f"  {label:35s} ({ticker}): ERROR {e}")

    print()
    print("=" * 110)
    print("RESULTATS — Caiguda màxima (max drawdown) i Volatilitat anualitzada per finestra")
    print("=" * 110)

    # Imprim taules per window
    for y in WINDOWS_YEARS:
        print()
        print(f"+-- ULTIMS {y} ANYS -" + "-" * 90)
        print(f"  {'Asset':38s} {'MaxDD%':>10s} {'VolAnnual%':>12s} {'Període':>26s}")
        print("-" * 110)
        # Filtra els que tenen dades en aquesta finestra
        valid = [(label, r) for label, r in results.items() if r["windows"][y]["max_dd"] is not None]
        # Ordena per MaxDD ascendent (menor caiguda = millor)
        valid.sort(key=lambda x: -x[1]["windows"][y]["max_dd"])  # less negative first

        for label, r in valid:
            m = r["windows"][y]
            print(f"  {label:38s} {m['max_dd']:>9.1f}%  {m['vol_annual']:>11.1f}%  {m['start']} -> {m['end']}")
        if not valid:
            print(f"  (cap actiu té {y} anys d'historial)")

    # Score combinat: ranking final
    print()
    print("=" * 110)
    print("RANKING FINAL (score = ½×MaxDD + ½×Vol normalitzats), 3 millors per finestra")
    print("=" * 110)
    for y in WINDOWS_YEARS:
        valid = [(label, r) for label, r in results.items() if r["windows"][y]["max_dd"] is not None]
        if not valid:
            continue
        # Normalitzem
        max_dds = [r["windows"][y]["max_dd"] for _, r in valid]
        vols = [r["windows"][y]["vol_annual"] for _, r in valid]
        # Score: més proper a 0 max_dd = millor; menys vol = millor
        scored = []
        for label, r in valid:
            m = r["windows"][y]
            # Normalitza: max_dd més proper a 0 millor (recordem que és negatiu)
            score_dd = -m["max_dd"]  # convertim negatiu a positiu (menor és millor)
            score_vol = m["vol_annual"]  # menor és millor
            combined = (score_dd + score_vol) / 2
            scored.append((label, m["max_dd"], m["vol_annual"], combined))
        scored.sort(key=lambda x: x[3])
        print(f"\n  ULTIMS {y} ANYS — top 5 més estables:")
        for i, (label, dd, vol, score) in enumerate(scored[:5], 1):
            print(f"    {i}. {label:35s}  MaxDD={dd:>6.1f}%  Vol={vol:>5.1f}%  score={score:.1f}")

    # Save full results
    import json
    out_path = "stable_assets_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nOK Resultats guardats a {out_path}")


if __name__ == "__main__":
    main()
