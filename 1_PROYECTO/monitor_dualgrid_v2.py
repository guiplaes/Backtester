"""
Monitor del DualGridEA_v2_Reset

Llegeix el heartbeat JSON que escriu l'EA cada 5s i mostra l'estat actual
amb format colorit a la terminal. Detecta esdeveniments (resets, PROT.BE, kill)
i els marca.

Us:
    python monitor_dualgrid_v2.py
    O via dualgrid_v2_monitor.bat
"""
import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime

# Path al heartbeat (Common\Files de MT5)
HEARTBEAT_PATH = Path(
    r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files\dualgrid_v2_status.json"
)
REFRESH_SEC = 2

# ANSI colors
RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
GREEN   = "\033[92m"
RED     = "\033[91m"
YELLOW  = "\033[93m"
BLUE    = "\033[94m"
CYAN    = "\033[96m"
WHITE   = "\033[97m"
GRAY    = "\033[90m"

# Background colors
BG_GREEN = "\033[42m"
BG_RED   = "\033[41m"
BG_YELLOW= "\033[43m"


def enable_ansi_windows():
    """Habilita ANSI escape codes a Windows 10+."""
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def fmt_money(value, width=10, color_pos=GREEN, color_neg=RED):
    sign = "+" if value >= 0 else ""
    color = color_pos if value >= 0 else color_neg
    return f"{color}{sign}{value:>{width-1}.2f}{RESET}"


def fmt_pct(value, width=8, color_pos=GREEN, color_neg=RED):
    sign = "+" if value >= 0 else ""
    color = color_pos if value >= 0 else color_neg
    return f"{color}{sign}{value:>{width-1}.4f}%{RESET}"


def state_color(state):
    if state == "ACTIVO":
        return f"{GREEN}ACTIVO{RESET}"
    if state == "PROT.BE":
        return f"{YELLOW}PROT.BE{RESET}"
    if state == "KILLED":
        return f"{BG_RED}{WHITE} KILLED {RESET}"
    return f"{WHITE}{state}{RESET}"


def render(data, prev=None, alerts=None):
    clear_screen()
    ts = data.get("ts", 0)
    ts_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "N/A"
    age = int(time.time() - ts) if ts else 0
    age_clr = GREEN if age < 10 else (YELLOW if age < 30 else RED)

    print(f"{BOLD}{CYAN}╔═══════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{CYAN}║  DUAL GRID v2 MONITOR  —  {data.get('symbol', '?'):20s}            ║{RESET}")
    print(f"{BOLD}{CYAN}╚═══════════════════════════════════════════════════════════════╝{RESET}")
    print(f"  Heartbeat: {ts_str}  {age_clr}(fa {age}s){RESET}   Magic: {data.get('magic', '?')}")
    print()

    # === ALERTS ===
    if alerts:
        for alert in alerts:
            print(f"  {BG_YELLOW}{WHITE} ALERT {RESET}  {alert}")
        print()

    # === GLOBAL ===
    bal = data.get("balance", 0)
    eq  = data.get("equity", 0)
    flo = data.get("floating", 0)
    profit_total = data.get("profit_total", 0)
    eq_pct = data.get("equity_vs_start_pct", 0)
    margin_lvl = data.get("margin_level", 0)
    start_bal = data.get("start_balance", 0)
    price = data.get("current_price", 0)
    killed = data.get("killed", False)

    print(f"{BOLD}{WHITE}── GLOBAL ──{RESET}")
    print(f"  Start balance     {DIM}{start_bal:>12.2f}${RESET}")
    print(f"  Balance           {bal:>12.2f}$")
    print(f"  Equity            {eq:>12.2f}$")
    print(f"  Flotant TOTAL    {fmt_money(flo, 13)}")
    print(f"  Profit total     {fmt_money(profit_total, 13)}  (realitzat des inici)")
    print(f"  Equity vs inici  {fmt_pct(eq_pct, 13)}")
    print(f"  Margin level      {margin_lvl:>10.0f}%")
    print(f"  Preu actual       {price:>12.2f}")
    if killed:
        print(f"  {BG_RED}{WHITE} >>> KILL SWITCH ACTIU <<< {RESET}")
    print()

    # === SHARED CYCLE ===
    cyc_start_bal = data.get("cycle_start_balance", 0)
    cyc_threshold = data.get("cycle_threshold_usd", 0)
    captured = data.get("profit_cycle_real", 0)
    reset_pct = data.get("reset_equity_pct", 0)

    print(f"{BOLD}{WHITE}── CICLE COMPARTIT (des de últim reset) ──{RESET}")
    print(f"  Cycle start bal   {DIM}{cyc_start_bal:>12.2f}${RESET}")
    print(f"  Capturat (TPs)   {fmt_money(captured, 13)}")
    print(f"  Threshold         {cyc_threshold:>12.2f}$  ({reset_pct}%)")
    print()

    # === LONG ===
    lc = data.get("long_count", 0)
    lp = data.get("long_pending", 0)
    lf = data.get("long_floatant", 0)
    lbe = data.get("long_be", 0)
    long_state = data.get("long_state", "?")
    long_resets = data.get("long_resets", 0)
    long_metric = captured + lf
    long_trigger_armed = (lf < 0) and (long_metric > cyc_threshold)

    print(f"{BOLD}{BLUE}── LONG ──{RESET}  ({lc} pos, {lp} pend, resets:{long_resets})")
    print(f"  Flotant          {fmt_money(lf, 13)}")
    print(f"  BE                {lbe:>12.2f}")
    print(f"  Metric (cap+L)   {fmt_money(long_metric, 13)}  vs +{cyc_threshold:.2f}")
    cond1 = f"{GREEN}negatiu{RESET}" if lf < 0 else f"{GRAY}positiu{RESET}"
    cond2 = f"{GREEN}>thresh{RESET}" if long_metric > cyc_threshold else f"{GRAY}<thresh{RESET}"
    trigger_str = f"{BG_GREEN}{WHITE} ARMAT {RESET}" if long_trigger_armed else f"{DIM}no armat{RESET}"
    print(f"  Trigger          {trigger_str}  ({cond1}, {cond2})")
    print(f"  Estat            {state_color(long_state)}")
    print()

    # === SHORT ===
    sc = data.get("short_count", 0)
    sp = data.get("short_pending", 0)
    sf = data.get("short_floatant", 0)
    sbe = data.get("short_be", 0)
    short_state = data.get("short_state", "?")
    short_resets = data.get("short_resets", 0)
    short_metric = captured + sf
    short_trigger_armed = (sf < 0) and (short_metric > cyc_threshold)

    print(f"{BOLD}{RED}── SHORT ──{RESET} ({sc} pos, {sp} pend, resets:{short_resets})")
    print(f"  Flotant          {fmt_money(sf, 13)}")
    print(f"  BE                {sbe:>12.2f}")
    print(f"  Metric (cap+S)   {fmt_money(short_metric, 13)}  vs +{cyc_threshold:.2f}")
    cond1 = f"{GREEN}negatiu{RESET}" if sf < 0 else f"{GRAY}positiu{RESET}"
    cond2 = f"{GREEN}>thresh{RESET}" if short_metric > cyc_threshold else f"{GRAY}<thresh{RESET}"
    trigger_str = f"{BG_GREEN}{WHITE} ARMAT {RESET}" if short_trigger_armed else f"{DIM}no armat{RESET}"
    print(f"  Trigger          {trigger_str}  ({cond1}, {cond2})")
    print(f"  Estat            {state_color(short_state)}")
    print()

    # === DELTA respecte lectura anterior ===
    if prev:
        d_bal = bal - prev.get("balance", bal)
        d_eq  = eq - prev.get("equity", eq)
        d_pt  = profit_total - prev.get("profit_total", profit_total)
        print(f"{DIM}── Δ vs lectura anterior ──{RESET}")
        print(f"  Δ Balance   {fmt_money(d_bal, 13)}    Δ Equity {fmt_money(d_eq, 13)}    Δ Profit total {fmt_money(d_pt, 13)}")
        print()

    print(f"{DIM}Refresh cada {REFRESH_SEC}s. Ctrl+C per sortir.{RESET}")


def detect_alerts(data, prev):
    """Retorna llista d'alertes basades en canvis."""
    alerts = []
    if prev is None:
        return alerts

    # Nous resets
    lr = data.get("long_resets", 0)
    pr = prev.get("long_resets", 0)
    if lr > pr:
        alerts.append(f"{GREEN}LONG reset #{lr} disparat{RESET}")

    sr = data.get("short_resets", 0)
    psr = prev.get("short_resets", 0)
    if sr > psr:
        alerts.append(f"{GREEN}SHORT reset #{sr} disparat{RESET}")

    # Canvis d'estat
    ls = data.get("long_state", "?")
    pls = prev.get("long_state", ls)
    if ls != pls:
        alerts.append(f"LONG state: {pls} → {state_color(ls)}")

    ss = data.get("short_state", "?")
    pss = prev.get("short_state", ss)
    if ss != pss:
        alerts.append(f"SHORT state: {pss} → {state_color(ss)}")

    # Kill switch
    if data.get("killed") and not prev.get("killed"):
        alerts.append(f"{BG_RED}{WHITE} KILL SWITCH FIRED {RESET}")

    # Margin level baix
    ml = data.get("margin_level", 0)
    if 0 < ml < 200:
        alerts.append(f"{RED}Margin level baix: {ml:.0f}%{RESET}")

    return alerts


def main():
    enable_ansi_windows()
    prev_data = None
    last_alerts = []

    if not HEARTBEAT_PATH.exists():
        print(f"{RED}ERROR: Heartbeat file no trobat:{RESET}")
        print(f"  {HEARTBEAT_PATH}")
        print()
        print("Verifica que l'EA DualGridEA_v2_Reset estigui carregat al MT5.")
        sys.exit(1)

    while True:
        try:
            with open(HEARTBEAT_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)

            new_alerts = detect_alerts(data, prev_data)
            # Mantén alertes les ultimes 2 lectures perquè es vegin
            display_alerts = new_alerts + last_alerts
            display_alerts = display_alerts[:5]  # max 5

            render(data, prev_data, display_alerts)

            prev_data = data
            last_alerts = new_alerts  # ja s'ha mostrat aquesta lectura

        except json.JSONDecodeError:
            # Lectura en curs (EA escrivint), reintenta
            pass
        except FileNotFoundError:
            print(f"{RED}Heartbeat file desaparegut. EA pot estar parat.{RESET}")
        except KeyboardInterrupt:
            print(f"\n{CYAN}Monitor aturat per l'usuari.{RESET}")
            sys.exit(0)
        except Exception as e:
            print(f"{RED}Error: {e}{RESET}")

        time.sleep(REFRESH_SEC)


if __name__ == "__main__":
    main()
