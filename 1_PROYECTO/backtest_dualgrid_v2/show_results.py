"""Mostra resultats normalitzats en % diari/mensual/total + DD."""
import csv
from pathlib import Path

RESULTS = Path(r"C:\Users\Administrator\Desktop\MT4 Claude\1_PROYECTO\backtest_dualgrid_v2\results.csv")
DEPOSIT = 50000.0

# Duracio aproximada per prefix
DAYS = {
    "Z": 30,   # 1 mes
    "S": 30,   # 1 mes
    "H": 180,  # 6 mesos
    "F": 180,  # 6 mesos
    "M": 180,
    "V": 30,
    "W": 30,
}

def parse_num(s):
    if not s: return 0.0
    try:
        return float(str(s).replace(' ', '').replace(',', '').split('(')[0].strip() or 0)
    except ValueError:
        return 0.0

def pct(s):
    if not s: return 0.0
    if '(' in str(s):
        return float(str(s).split('(')[1].rstrip('%)').strip())
    return 0.0

def main():
    rows = []
    seen = set()
    with open(RESULTS, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            tid = r['test_id']
            if tid in seen: continue
            seen.add(tid)
            np = parse_num(r.get('net_profit'))
            if np == 0 and parse_num(r.get('total_trades')) == 0:
                continue
            days = DAYS.get(tid[0], 30)
            months = days/30.0
            prof_pct = np/DEPOSIT*100
            rows.append({
                'id': tid,
                'days': days,
                'profit_usd': np,
                'profit_pct_total': prof_pct,
                'profit_pct_month': prof_pct/months,
                'profit_pct_day': prof_pct/days,
                'bal_dd_pct': pct(r.get('balance_dd_max')),
                'eq_dd_pct': pct(r.get('equity_dd_max')),
                'pf': parse_num(r.get('profit_factor')),
                'trades': int(parse_num(r.get('total_trades'))),
            })

    rows.sort(key=lambda x: -x['profit_pct_month'])

    print(f"{'TEST':<25} {'DIES':>5} {'TOTAL%':>8} {'MES%':>7} {'DIA%':>7} {'BalDD%':>7} {'EqDD%':>7} {'GAP%':>6} {'PF':>5} {'TRADES':>7}")
    print("-" * 100)
    for r in rows[:30]:
        gap = r['eq_dd_pct'] - r['bal_dd_pct']
        print(f"{r['id']:<25} {r['days']:>5} {r['profit_pct_total']:>7.2f}% {r['profit_pct_month']:>6.2f}% {r['profit_pct_day']:>6.3f}% {r['bal_dd_pct']:>6.2f}% {r['eq_dd_pct']:>6.2f}% {gap:>+5.2f}% {r['pf']:>5.2f} {r['trades']:>7}")

if __name__ == "__main__":
    main()
