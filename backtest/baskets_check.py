"""
Mirem ETFs/baskets descorrelats amb cripto (i amb risc idiosincràtic baix).
"""
import warnings
warnings.filterwarnings("ignore")
import yfinance as yf
import numpy as np
import pandas as pd

# Baskets/ETFs reals (no single stocks) a Pionex
BASKETS = {
    "SPYX (S&P 500)":          "SPY",
    "QQQX (Nasdaq 100)":       "QQQ",
    "EWJX (Japan ETF)":        "EWJ",
    "VGKX (Europe ETF)":       "VGK",
    "GSGX (Goldman Commod)":   "GSG",     # basket commodities
    "CPERX (Copper ETF)":      "CPER",
    "URAX (Uranium ETF)":      "URA",
    "SLVX (Silver ETF)":       "SLV",
    "USOX (Oil ETF)":          "USO",
    "UNGX (Nat Gas)":          "UNG",
    "SMHX (Semiconductors)":   "SMH",
    "SOXXX (Semiconductors)":  "SOXX",
}

# Baskets de single stocks ponderats (com a backup pel cas que volguem stocks)
# Defense basket: LMT + RTX + GD (no a Pionex, però RTX yes)
# Pharma basket: LLY + JNJ (JNJ no a Pionex) — només LLY
# Healthcare insurer: UNH

REFS = {"PAXG (Or)": "GLD", "BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD"}


def main():
    end = pd.Timestamp.today()
    start = end - pd.Timedelta(days=5*365)
    print(f"Descarregant 5 anys ({start.date()} -> {end.date()})...\n")
    tickers = list(BASKETS.values()) + list(REFS.values())
    data = yf.download(tickers, start=start, end=end, progress=False, auto_adjust=True)
    close = data["Close"]
    rets = close.pct_change().dropna()

    cripto_avg = rets[["BTC-USD", "ETH-USD", "SOL-USD"]].mean(axis=1)

    print("=" * 95)
    print("CORRELACIONS — basket/ETF vs CRIPTO i OR (5 anys)")
    print("=" * 95)
    print(f"{'Asset':<28} {'vs Cripto':>11} {'vs Or':>11} {'MaxDD 5y':>11}  Veredicte")
    print("-" * 95)

    rows = []
    for label, t in BASKETS.items():
        if t not in rets.columns:
            continue
        corr_cripto = rets[t].corr(cripto_avg)
        corr_gold = rets[t].corr(rets["GLD"])
        # Max drawdown
        cumret = (1 + rets[t]).cumprod()
        peak = cumret.cummax()
        dd = (cumret - peak) / peak
        max_dd = dd.min() * 100
        rows.append((label, corr_cripto, corr_gold, max_dd))

    # Ordenar per descorrelació amb cripto
    rows.sort(key=lambda x: abs(x[1]))
    for label, cc, cg, dd in rows:
        verdict = "OK descorrelat" if abs(cc) < 0.25 else ("Moderat" if abs(cc) < 0.40 else "CORRELAT")
        # Avis si MaxDD massa gran
        if dd < -80:
            verdict += " | DD letal"
        elif dd < -50:
            verdict += " | DD alt"
        print(f"{label:<28} {cc:>+10.3f}  {cg:>+10.3f}  {dd:>10.1f}%  {verdict}")

    # Recomanats: descorrelats amb cripto AND MaxDD<-50%
    print()
    print("=" * 95)
    print("CANDIDATS VIABLES (descorrelat amb cripto AND MaxDD raonable < -55%)")
    print("=" * 95)
    print(f"{'Asset':<28} {'vs Cripto':>11} {'MaxDD':>10}")
    print("-" * 95)
    viables = [r for r in rows if abs(r[1]) < 0.25 and r[3] > -55]
    if not viables:
        viables = [r for r in rows if abs(r[1]) < 0.30 and r[3] > -60]
    for label, cc, cg, dd in viables:
        print(f"{label:<28} {cc:>+10.3f}  {dd:>9.1f}%")


if __name__ == "__main__":
    main()
