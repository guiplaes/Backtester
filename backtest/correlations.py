"""
Calcula correlació diària dels candidats vs PAXG (or) i BTC (cripto).
Objectiu: trobar actius DESCORRELATS amb ambdós.
"""
import warnings
warnings.filterwarnings("ignore")
import yfinance as yf
import numpy as np
import pandas as pd

# Tots els candidats de Pionex (sense or i sense cripto-natives)
CANDIDATES = {
    "SPYX (S&P 500)":            "SPY",
    "QQQX (Nasdaq 100)":         "QQQ",
    "EWJX (Japan)":              "EWJ",
    "VGKX (Europe)":             "VGK",
    "LMTX (Lockheed)":           "LMT",
    "RTXX (Raytheon)":           "RTX",
    "LLYX (Eli Lilly)":          "LLY",
    "UNHX (UnitedHealth)":       "UNH",
    "CVXX (Chevron)":            "CVX",
    "NKEX (Nike)":               "NKE",
    "AAPLX (Apple)":             "AAPL",
    "MSFTX (Microsoft)":         "MSFT",
    "GOOGLX (Alphabet)":         "GOOGL",
    "AMZNX (Amazon)":            "AMZN",
    "METAX (Meta)":              "META",
    "NVDAX (NVIDIA)":            "NVDA",
    "TSLAX (Tesla)":             "TSLA",
    "ASMLX (ASML)":              "ASML",
    "AVGOX (Broadcom)":          "AVGO",
    "ORCLX (Oracle)":            "ORCL",
    "TXNX (Texas Instr)":        "TXN",
    "INTCX (Intel)":             "INTC",
    "AMDX (AMD)":                "AMD",
    "PLTRX (Palantir)":          "PLTR",
    "HIMSX (Hims & Hers)":       "HIMS",
    "NFLXX (Netflix)":           "NFLX",
    "HOODX (Robinhood)":         "HOOD",
    "MSTRX (Strategy/MSTR)":     "MSTR",
    "COINX (Coinbase)":          "COIN",
    "USOX (Oil)":                "USO",
    "SLVX (Silver)":             "SLV",
    "UNGX (Nat Gas)":            "UNG",
    "CPERX (Copper)":            "CPER",
    "URAX (Uranium)":            "URA",
    "GSGX (Commodities)":        "GSG",
}

BENCHMARK_REF = {
    "PAXG (or)":   "GLD",      # SPDR Gold Trust (proxy de PAXG)
    "BTC (cripto)": "BTC-USD",
}


def main():
    print("Descarregant dades dels últims 5 anys (suficient per correlació)...")
    end = pd.Timestamp.today()
    start = end - pd.Timedelta(days=5*365)

    all_tickers = list(set(list(CANDIDATES.values()) + list(BENCHMARK_REF.values())))
    data = yf.download(all_tickers, start=start, end=end, progress=False, auto_adjust=True)
    if "Close" in data.columns.get_level_values(0):
        close = data["Close"]
    else:
        close = data
    rets = np.log(close / close.shift(1)).dropna()
    print(f"Dies de dades: {len(rets):,}")
    print()

    # Correlation amb PAXG (GLD) i BTC
    results = []
    for label, ticker in CANDIDATES.items():
        if ticker not in rets.columns:
            continue
        try:
            corr_gold = rets[ticker].corr(rets["GLD"])
            corr_btc  = rets[ticker].corr(rets["BTC-USD"])
            results.append((label, ticker, corr_gold, corr_btc))
        except Exception as e:
            pass

    # Score: més proper a 0 amb ambdós = millor descorrelat
    results.sort(key=lambda x: abs(x[2]) + abs(x[3]))

    print("=" * 90)
    print("CORRELACIÓ amb PAXG (GLD) i BTC — TOP 15 més descorrelats")
    print("=" * 90)
    print(f"{'Asset':<28} {'vs Or':>10} {'vs BTC':>10} {'Score':>10}  Verdict")
    print("-" * 90)
    for label, ticker, cg, cb in results[:15]:
        score = abs(cg) + abs(cb)
        verdict = "MOLT descorrelat" if score < 0.4 else ("Descorrelat" if score < 0.7 else "Correlat")
        print(f"{label:<28} {cg:>+10.3f} {cb:>+10.3f} {score:>10.3f}  {verdict}")

    print()
    print("=" * 90)
    print("RESTA (per referència, més correlats)")
    print("=" * 90)
    for label, ticker, cg, cb in results[15:]:
        score = abs(cg) + abs(cb)
        verdict = "Correlat" if score > 0.7 else "Moderada"
        print(f"{label:<28} {cg:>+10.3f} {cb:>+10.3f} {score:>10.3f}  {verdict}")


if __name__ == "__main__":
    main()
