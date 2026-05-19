"""Dashboard visual de XiscoMirror — finestra tkinter que actualitza cada 2s.

Read-only: nomes monitoritza. NO envia ordres, NO atura processos.
Per arrencar/aturar fes servir els shortcuts del desktop.

Mostra:
  - Estat del proces (viu/mort) amb llum verda/vermella gran
  - Telegram connection status
  - Counters (aperturas/modificacions/cierres rebuts i replicats)
  - Sizing actual (ratio + balance)
  - Tickets oberts (xisco -> local map)
  - Log tail (ultimes 25 linies)
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from tkinter import ttk

BASE = Path(__file__).resolve().parent
LOG_PATH = BASE / "logs" / "xisco_mirror.log"
TICKET_MAP_PATH = BASE / "xisco_ticket_map.json"
PNL_STATE_PATH = BASE / "xisco_pnl_state.json"

MT5_COMMON = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files")
POSITIONS_FILE = MT5_COMMON / "xisco_positions.json"
ORDERS_FILE = MT5_COMMON / "xisco_orders.json"

XISCO_BALANCE_REF_USC = 229_792.42

REFRESH_MS = 2000

# Llindar fresc: si xisco_positions.json te mes de N segons -> EA stale
TG_LOG_FRESH_SEC = 600       # log activity dins 10 min -> TG actiu
MIRROR_EA_FRESH_SEC = 30     # xisco_positions.json dins 30s -> EA mirror viu


# ─── PROBES ─────────────────────────────────────────────────────────
def find_python_process() -> dict | None:
    """Cerca el procés pythonw.exe que executa xisco_mirror.py."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"name='pythonw.exe' or name='python.exe'\" | "
             "Where-Object { $_.CommandLine -like '*xisco_mirror*' } | "
             "Select-Object ProcessId, CreationDate, WorkingSetSize | ConvertTo-Json"],
            capture_output=True, text=True, timeout=10,
            creationflags=0x08000000,
        )
        if not out.stdout.strip():
            return None
        data = json.loads(out.stdout)
        if isinstance(data, list):
            data = data[0] if data else None
        if not data:
            return None
        # CreationDate ve com a string format especial — la parsegem
        return {
            "pid": data.get("ProcessId"),
            "created_raw": data.get("CreationDate"),
            "ws_mb": (data.get("WorkingSetSize", 0) or 0) / (1024 * 1024),
        }
    except Exception:
        return None


def read_account() -> dict | None:
    """Unica font de balance/equity: el nostre XiscoMirrorEA via xisco_positions.json.
    Retorna None si l'EA no esta atacat al xart o el fitxer es stale."""
    if not POSITIONS_FILE.exists():
        return None
    try:
        age = time.time() - POSITIONS_FILE.stat().st_mtime
        if age > MIRROR_EA_FRESH_SEC * 4:  # fitxer molt vell -> ignorem
            return None
        d = json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
        acc = d.get("account", {})
        if acc.get("balance"):
            return acc
    except Exception:
        return None
    return None


def read_balance_usc() -> float | None:
    acc = read_account()
    return float(acc["balance"]) if acc and acc.get("balance") else None


def read_equity_usc() -> float | None:
    acc = read_account()
    return float(acc["equity"]) if acc and acc.get("equity") else None


def read_ticket_map() -> dict:
    if not TICKET_MAP_PATH.exists():
        return {}
    try:
        return json.loads(TICKET_MAP_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_xisco_positions() -> list[dict]:
    """Posicions actualment obertes amb magic 88888 (filtrat per l'EA)."""
    if not POSITIONS_FILE.exists():
        return []
    try:
        d = json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
        return d.get("positions", []) or []
    except Exception:
        return []


def parse_log_counters() -> dict:
    """Compta des del log les ultimes accions (aproximat)."""
    counters = {
        "aperturas_rcv": 0,
        "aperturas_mir": 0,
        "cierres_rcv": 0,
        "cierres_mir": 0,
        "modif_rcv": 0,
        "modif_mir": 0,
        "errors": 0,
    }
    if not LOG_PATH.exists():
        return counters
    try:
        # Llegim només l'ultim 1MB per velocitat (poden ser logs llargs)
        size = LOG_PATH.stat().st_size
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            if size > 1_000_000:
                f.seek(size - 1_000_000)
                f.readline()
            for line in f:
                if "APERTURA xisco_ticket=" in line:
                    counters["aperturas_rcv"] += 1
                if "ORDER written" in line and "MARKET" in line:
                    counters["aperturas_mir"] += 1
                if "CIERRE xisco=" in line:
                    counters["cierres_rcv"] += 1
                if "ORDER written" in line and "CLOSE_TICKET" in line:
                    counters["cierres_mir"] += 1
                if "MODIFICACION xisco=" in line:
                    counters["modif_rcv"] += 1
                if "ORDER written" in line and "MODIFY_" in line:
                    counters["modif_mir"] += 1
                if "[ERROR]" in line or "FATAL" in line or "FAILED" in line:
                    counters["errors"] += 1
    except Exception:
        pass
    return counters


# ─── P&L STATE ──────────────────────────────────────────────────────
def load_pnl_state() -> dict:
    """Estat persistit: start_balance del projecte + open balance per dia."""
    if PNL_STATE_PATH.exists():
        try:
            return json.loads(PNL_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"start_balance": None, "start_ts": None, "daily_opens": {}}


def save_pnl_state(state: dict):
    try:
        PNL_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        pass


def update_pnl_state(state: dict, current_balance: float) -> dict:
    """Captura start_balance la primera vegada i daily_open de cada dia UTC."""
    if current_balance is None or current_balance <= 0:
        return state
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    changed = False
    if state.get("start_balance") is None:
        state["start_balance"] = current_balance
        state["start_ts"] = time.time()
        changed = True
    if today not in state.get("daily_opens", {}):
        state.setdefault("daily_opens", {})[today] = current_balance
        changed = True
    if changed:
        save_pnl_state(state)
    return state


def compute_pnl(state: dict, current_equity: float | None) -> dict:
    """Retorna percentatges (diari i total) + equivalents USC."""
    result = {
        "total_pct": None, "total_usc": None,
        "day_pct": None, "day_usc": None,
        "start_balance": state.get("start_balance"),
        "day_open": None,
    }
    if not current_equity:
        return result
    sb = state.get("start_balance")
    if sb and sb > 0:
        result["total_usc"] = current_equity - sb
        result["total_pct"] = (current_equity - sb) / sb * 100
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    do = state.get("daily_opens", {}).get(today)
    if do and do > 0:
        result["day_open"] = do
        result["day_usc"] = current_equity - do
        result["day_pct"] = (current_equity - do) / do * 100
    return result


# ─── HEALTH PROBES ──────────────────────────────────────────────────
def probe_tg_listener() -> tuple[str, str]:
    """OK si el log s'ha actualitzat fa < TG_LOG_FRESH_SEC.
    'WARNING' si fresh pero sense missatges, 'FAIL' si mort/no log."""
    if not LOG_PATH.exists():
        return "FAIL", "log no existeix"
    try:
        mtime = LOG_PATH.stat().st_mtime
        age = time.time() - mtime
        if age > TG_LOG_FRESH_SEC:
            return "FAIL", f"log inactiu fa {int(age/60)}min"
        # Verifica que hi ha 'RUNNING' o connection en el log recent
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            size = LOG_PATH.stat().st_size
            if size > 200_000:
                f.seek(size - 200_000)
                f.readline()
            tail = f.read()
        if "RUNNING" in tail or "New message" in tail or "APERTURA" in tail or "Connected" in tail:
            return "OK", f"actiu (log fa {int(age)}s)"
        return "WARN", "log fresh pero sense activitat clar"
    except Exception as e:
        return "FAIL", str(e)


def probe_mt5_account() -> tuple[str, str]:
    """OK si XiscoMirrorEA esta atacat i escrivint balance."""
    if not POSITIONS_FILE.exists():
        return "FAIL", "ataca XiscoMirrorEA a un xart MT5"
    try:
        age = time.time() - POSITIONS_FILE.stat().st_mtime
        if age > MIRROR_EA_FRESH_SEC:
            return "FAIL", f"EA stale fa {int(age)}s — xart tancat?"
        acc = read_account()
        if not acc:
            return "WARN", f"EA escrivint pero sense balance al JSON"
        return "OK", f"actiu (fa {int(age)}s)"
    except Exception as e:
        return "FAIL", str(e)


def probe_xisco_ea() -> tuple[str, str]:
    """OK si xisco_positions.json es fresh.
    Si el fitxer NO existeix -> EA no atacat al xart MAI.
    Si existeix pero stale -> EA va caure / xart tancat."""
    if not POSITIONS_FILE.exists():
        return "FAIL", "EA no atacat al xart (positions.json no existeix)"
    try:
        mtime = POSITIONS_FILE.stat().st_mtime
        age = time.time() - mtime
        if age > MIRROR_EA_FRESH_SEC:
            return "FAIL", f"EA stale (positions.json fa {int(age)}s)"
        return "OK", f"actiu (echo fa {int(age)}s)"
    except Exception as e:
        return "FAIL", str(e)


def parse_log_events(limit: int = 200) -> list[dict]:
    """Parseja el log per extreure events TG (APERTURA/MODIFICACION/CIERRE)
    amb el seu resultat (replicat/skipped). Retorna llista cronològica."""
    events: list[dict] = []
    if not LOG_PATH.exists():
        return events
    try:
        # Llegim nomes l'ultim 2MB per velocitat
        size = LOG_PATH.stat().st_size
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            if size > 2_000_000:
                f.seek(size - 2_000_000)
                f.readline()
            lines = f.readlines()
    except Exception:
        return events

    # Patrons del log
    # 2026-05-13 10:05:03,944 [INFO]   CIERRE 198593668 skipped (no mapping...)
    # APERTURA xisco_ticket=198650123 SELL 0.20@4710.5 (XAUUSD-VIPc)
    # MODIFICACION xisco=382633305 SL=4683.24
    # CIERRE xisco=198650123 -> local=87654321
    re_apertura_full = re.compile(
        r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*APERTURA xisco_ticket=(\d+)\s+(BUY|SELL)\s+([\d.]+)@([\d.]+)"
    )
    re_apertura_skip = re.compile(
        r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*APERTURA (\d+) skipped \(([^)]+)\)"
    )
    re_modif = re.compile(
        r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*MODIFICACION xisco=(\d+)\s+(SL|TP)=([\w.]+)"
    )
    re_modif_skip = re.compile(
        r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*MODIFICACION (\d+) skipped \(([^)]+)\)"
    )
    re_cierre = re.compile(
        r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*CIERRE xisco=(\d+)\s+->\s+local=(\S+)"
    )
    re_cierre_skip = re.compile(
        r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*CIERRE (\d+) skipped \(([^)]+)\)"
    )

    for line in lines:
        m = re_apertura_full.match(line)
        if m:
            events.append({
                "ts": m.group(1), "type": "APERTURA", "ticket": m.group(2),
                "info": f"{m.group(3)} {m.group(4)}@{m.group(5)}",
                "result": "REPLICAT",
            })
            continue
        m = re_apertura_skip.match(line)
        if m:
            events.append({
                "ts": m.group(1), "type": "APERTURA", "ticket": m.group(2),
                "info": "(saltada)", "result": f"SKIP: {m.group(3)}",
            })
            continue
        m = re_modif.match(line)
        if m:
            events.append({
                "ts": m.group(1), "type": "MODIFIC", "ticket": m.group(2),
                "info": f"{m.group(3)}={m.group(4)}", "result": "REPLICAT",
            })
            continue
        m = re_modif_skip.match(line)
        if m:
            events.append({
                "ts": m.group(1), "type": "MODIFIC", "ticket": m.group(2),
                "info": "(saltada)", "result": f"SKIP: {m.group(3)}",
            })
            continue
        m = re_cierre.match(line)
        if m:
            events.append({
                "ts": m.group(1), "type": "CIERRE", "ticket": m.group(2),
                "info": f"local={m.group(3)}", "result": "REPLICAT",
            })
            continue
        m = re_cierre_skip.match(line)
        if m:
            events.append({
                "ts": m.group(1), "type": "CIERRE", "ticket": m.group(2),
                "info": "(saltada)", "result": f"SKIP: {m.group(3)}",
            })
    return events[-limit:]


def tail_log(n: int = 25) -> list[str]:
    if not LOG_PATH.exists():
        return ["(log encara no existeix)"]
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return lines[-n:]
    except Exception as e:
        return [f"(error llegint log: {e})"]


# ─── GUI ────────────────────────────────────────────────────────────
class Dashboard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("XiscoMirror — Monitor")
        # Adaptat a pantalla 1600x852 - usem ~1200x800
        self.geometry("1280x820")
        self.minsize(900, 700)
        self.configure(bg="#1e1e1e")
        self.option_add("*Font", "Consolas 10")
        self._build()
        self.refresh()

    def _label(self, parent, text, **kw):
        kw.setdefault("bg", "#1e1e1e")
        kw.setdefault("fg", "#e0e0e0")
        return tk.Label(parent, text=text, **kw)

    def _build(self):
        # Top frame: estat + balance
        top = tk.Frame(self, bg="#1e1e1e")
        top.pack(fill=tk.X, padx=10, pady=10)

        self.status_dot = tk.Canvas(top, width=40, height=40, bg="#1e1e1e", highlightthickness=0)
        self.status_dot.pack(side=tk.LEFT, padx=(0, 10))
        self.status_dot_id = self.status_dot.create_oval(5, 5, 35, 35, fill="#555", outline="")

        self.status_txt = self._label(top, "Comprovant...", font=("Segoe UI", 16, "bold"))
        self.status_txt.pack(side=tk.LEFT)

        # Balance frame (right side)
        balance_fr = tk.Frame(top, bg="#1e1e1e")
        balance_fr.pack(side=tk.RIGHT)
        self.balance_lbl = self._label(balance_fr, "Balance: —", font=("Segoe UI", 12))
        self.balance_lbl.pack(anchor=tk.E)
        self.equity_lbl = self._label(balance_fr, "Equity: —", font=("Segoe UI", 12))
        self.equity_lbl.pack(anchor=tk.E)
        self.ratio_lbl = self._label(balance_fr, "Ratio vs Xisco: —", font=("Segoe UI", 10), fg="#999")
        self.ratio_lbl.pack(anchor=tk.E)

        # Proc info inline (PID/Uptime/RAM/Errors)
        proc_fr = tk.Frame(self, bg="#1e1e1e")
        proc_fr.pack(fill=tk.X, padx=10, pady=(0, 2))
        self.pid_lbl = self._label(proc_fr, "PID: —", font=("Consolas", 9))
        self.pid_lbl.pack(side=tk.LEFT, padx=5)
        self.uptime_lbl = self._label(proc_fr, "Uptime: —", font=("Consolas", 9))
        self.uptime_lbl.pack(side=tk.LEFT, padx=20)
        self.ram_lbl = self._label(proc_fr, "RAM: —", font=("Consolas", 9))
        self.ram_lbl.pack(side=tk.LEFT, padx=20)
        self.err_lbl = self._label(proc_fr, "Errors: 0", fg="#999", font=("Consolas", 9))
        self.err_lbl.pack(side=tk.RIGHT, padx=5)

        # ── HEALTH PILLS ──
        sep_h = tk.Frame(self, bg="#333", height=1); sep_h.pack(fill=tk.X, pady=4)
        pills_fr = tk.Frame(self, bg="#1e1e1e")
        pills_fr.pack(fill=tk.X, padx=10, pady=2)
        self.pill_tg = self._make_pill(pills_fr, "🎧 Telegram", col=0)
        self.pill_mt5 = self._make_pill(pills_fr, "📈 MT5 Compte", col=1)
        self.pill_ea = self._make_pill(pills_fr, "🪞 EA Mirror", col=2)

        # ── 2-COL: P&L (esquerra) + COMPTADORS (dreta) ──
        sep_m = tk.Frame(self, bg="#333", height=1); sep_m.pack(fill=tk.X, pady=4)
        mid_fr = tk.Frame(self, bg="#1e1e1e")
        mid_fr.pack(fill=tk.X, padx=10, pady=2)
        mid_fr.grid_columnconfigure(0, weight=1)
        mid_fr.grid_columnconfigure(1, weight=1)
        # esquerra: P&L
        pnl_outer = tk.Frame(mid_fr, bg="#1e1e1e")
        pnl_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self._label(pnl_outer, "💰  P&L PROJECTE", font=("Segoe UI", 10, "bold"), fg="#88ccff").pack(anchor=tk.W)
        pnl_fr = tk.Frame(pnl_outer, bg="#1e1e1e")
        pnl_fr.pack(fill=tk.X, pady=2)
        self.pnl_total = self._make_pnl(pnl_fr, "TOTAL", col=0)
        self.pnl_day = self._make_pnl(pnl_fr, "AVUI (UTC)", col=1)
        # dreta: comptadors
        ct_outer = tk.Frame(mid_fr, bg="#1e1e1e")
        ct_outer.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        self._label(ct_outer, "📊  COMPTADORS (rebut / replicat)", font=("Segoe UI", 10, "bold"), fg="#88ccff").pack(anchor=tk.W)
        counters_fr = tk.Frame(ct_outer, bg="#1e1e1e")
        counters_fr.pack(fill=tk.X, pady=2)
        self.c_aperturas = self._counter_widget(counters_fr, "APERTURES", "#7ec46c", col=0)
        self.c_modif     = self._counter_widget(counters_fr, "MODIFIC", "#dcb464", col=1)
        self.c_cierres   = self._counter_widget(counters_fr, "CIERRES", "#e07070", col=2)

        # Posicions obertes (compactes)
        sep2 = tk.Frame(self, bg="#333", height=1); sep2.pack(fill=tk.X, pady=4)
        self._label(self, "📂  POSICIONS OBERTES (magic 88888)", font=("Segoe UI", 10, "bold"), fg="#88ccff").pack(anchor=tk.W, padx=10)
        self.pos_tree = ttk.Treeview(self, columns=("type", "vol", "price", "profit"), show="headings", height=3)
        self.pos_tree.heading("type", text="Tipus")
        self.pos_tree.heading("vol", text="Volum")
        self.pos_tree.heading("price", text="Preu obertura")
        self.pos_tree.heading("profit", text="P/L (USC)")
        self.pos_tree.column("type", width=80)
        self.pos_tree.column("vol", width=80)
        self.pos_tree.column("price", width=120)
        self.pos_tree.column("profit", width=120)
        self.pos_tree.pack(fill=tk.X, padx=10, pady=(2, 0))

        # Pestanyes: Historial missatges TG + Log raw
        sep3 = tk.Frame(self, bg="#333", height=1); sep3.pack(fill=tk.X, pady=4)
        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=(2, 4))

        # — Tab 1: Historial missatges TG (taula) —
        tab_hist = tk.Frame(nb, bg="#1e1e1e")
        nb.add(tab_hist, text="  📜 Historial missatges TG  ")
        hist_inner = tk.Frame(tab_hist, bg="#1e1e1e")
        hist_inner.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.hist_tree = ttk.Treeview(
            hist_inner,
            columns=("ts", "type", "ticket", "info", "result"),
            show="headings",
            height=15,
        )
        self.hist_tree.heading("ts", text="Hora")
        self.hist_tree.heading("type", text="Tipus")
        self.hist_tree.heading("ticket", text="Ticket Xisco")
        self.hist_tree.heading("info", text="Detall")
        self.hist_tree.heading("result", text="Resultat")
        self.hist_tree.column("ts", width=140)
        self.hist_tree.column("type", width=90)
        self.hist_tree.column("ticket", width=110)
        self.hist_tree.column("info", width=200)
        self.hist_tree.column("result", width=240)
        # Tags colors per tipus
        self.hist_tree.tag_configure("REPLICAT", foreground="#7ec46c")
        self.hist_tree.tag_configure("SKIP", foreground="#999")
        self.hist_tree.tag_configure("CIERRE_OK", foreground="#e07070")
        sb_h = ttk.Scrollbar(hist_inner, orient="vertical", command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=sb_h.set)
        self.hist_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb_h.pack(side=tk.RIGHT, fill=tk.Y)

        # — Tab 2: Log raw —
        tab_log = tk.Frame(nb, bg="#1e1e1e")
        nb.add(tab_log, text="  📋 Log raw  ")
        log_fr = tk.Frame(tab_log, bg="#1e1e1e")
        log_fr.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.log_text = tk.Text(log_fr, bg="#0d0d0d", fg="#cccccc", font=("Consolas", 9),
                                wrap=tk.NONE, relief=tk.FLAT, bd=0)
        self.log_text.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        sb = tk.Scrollbar(log_fr, command=self.log_text.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=sb.set, state=tk.DISABLED)
        self.log_text.tag_configure("INFO", foreground="#aaaaaa")
        self.log_text.tag_configure("WARN", foreground="#dcb464")
        self.log_text.tag_configure("ERROR", foreground="#ff6060")
        self.log_text.tag_configure("APERTURA", foreground="#7ec46c")
        self.log_text.tag_configure("CIERRE", foreground="#e07070")
        self.log_text.tag_configure("ORDER", foreground="#88ccff")

        # Bottom footer
        foot = tk.Frame(self, bg="#1e1e1e")
        foot.pack(fill=tk.X, padx=10, pady=(0, 5))
        self.last_update = self._label(foot, "Última actualització: —", fg="#666", font=("Segoe UI", 9))
        self.last_update.pack(side=tk.LEFT)
        self._label(foot, "Refresc cada 2s", fg="#666", font=("Segoe UI", 9)).pack(side=tk.RIGHT)

    def _make_pill(self, parent, title, col):
        """Pill amb dot d'estat + nom + missatge curt."""
        fr = tk.Frame(parent, bg="#252525")
        fr.grid(row=0, column=col, sticky="nsew", padx=4, pady=2)
        parent.grid_columnconfigure(col, weight=1)
        inner = tk.Frame(fr, bg="#252525")
        inner.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        # Dot
        dot = tk.Canvas(inner, width=18, height=18, bg="#252525", highlightthickness=0)
        dot.pack(side=tk.LEFT, padx=(0, 8))
        dot_id = dot.create_oval(2, 2, 16, 16, fill="#555", outline="")
        # Text col
        txt_fr = tk.Frame(inner, bg="#252525")
        txt_fr.pack(side=tk.LEFT, fill=tk.X, expand=True)
        title_lbl = tk.Label(txt_fr, text=title, bg="#252525", fg="#cccccc",
                              font=("Segoe UI", 10, "bold"), anchor=tk.W)
        title_lbl.pack(anchor=tk.W)
        msg_lbl = tk.Label(txt_fr, text="comprovant...", bg="#252525", fg="#888",
                            font=("Segoe UI", 9), anchor=tk.W)
        msg_lbl.pack(anchor=tk.W)
        return {"dot": dot, "dot_id": dot_id, "msg": msg_lbl}

    def _make_pnl(self, parent, title, col):
        """Bloc P&L gran amb % + valor USC."""
        fr = tk.Frame(parent, bg="#252525")
        fr.grid(row=0, column=col, sticky="nsew", padx=3)
        parent.grid_columnconfigure(col, weight=1)
        tk.Label(fr, text=title, bg="#252525", fg="#888",
                 font=("Segoe UI", 9)).pack(pady=(4, 0))
        pct = tk.Label(fr, text="—", bg="#252525", fg="#cccccc",
                        font=("Segoe UI", 18, "bold"))
        pct.pack()
        usc = tk.Label(fr, text="—", bg="#252525", fg="#888",
                        font=("Consolas", 9))
        usc.pack()
        base = tk.Label(fr, text="", bg="#252525", fg="#666",
                         font=("Segoe UI", 8))
        base.pack(pady=(0, 4))
        return {"pct": pct, "usc": usc, "base": base}

    def _counter_widget(self, parent, title, color, col):
        fr = tk.Frame(parent, bg="#252525", relief=tk.FLAT, bd=0)
        fr.grid(row=0, column=col, sticky="nsew", padx=3)
        parent.grid_columnconfigure(col, weight=1)
        tk.Label(fr, text=title, bg="#252525", fg=color, font=("Segoe UI", 9, "bold")).pack(pady=(4, 0))
        big = tk.Label(fr, text="0 / 0", bg="#252525", fg="#ffffff", font=("Segoe UI", 16, "bold"))
        big.pack(pady=(0, 4))
        return {"big": big}

    # ── REFRESH ────────────────────────────────────────────────────
    def refresh(self):
        try:
            self._update_status()
            self._update_balance()
            self._update_health_pills()
            self._update_pnl()
            self._update_counters()
            self._update_positions()
            self._update_history()
            self._update_log()
            self.last_update.config(text=f"Última actualització: {datetime.now().strftime('%H:%M:%S')}")
        except Exception as e:
            print(f"refresh error: {e}", file=sys.stderr)
        self.after(REFRESH_MS, self.refresh)

    def _update_health_pills(self):
        # TG
        status, msg = probe_tg_listener()
        self._set_pill(self.pill_tg, status, msg)
        # MT5 Compte (balance/equity readable)
        status, msg = probe_mt5_account()
        self._set_pill(self.pill_mt5, status, msg)
        # Xisco mirror EA
        status, msg = probe_xisco_ea()
        self._set_pill(self.pill_ea, status, msg)

    def _set_pill(self, pill, status, msg):
        color = {"OK": "#22cc44", "WARN": "#dcb464", "FAIL": "#cc2244"}.get(status, "#555")
        pill["dot"].itemconfig(pill["dot_id"], fill=color)
        pill["msg"].config(text=msg)

    def _update_pnl(self):
        bal = read_balance_usc()
        eq = read_equity_usc()
        # Auto-captura start_balance la primera vegada
        state = load_pnl_state()
        if bal:
            state = update_pnl_state(state, bal)
        pnl = compute_pnl(state, eq)

        def _fmt_pnl_block(block, pct, usc, base, label):
            if pct is None:
                block["pct"].config(text="—", fg="#888")
                block["usc"].config(text="(esperant balance)")
                block["base"].config(text="")
                return
            color = "#7ec46c" if pct >= 0 else "#ff6060"
            sign = "+" if pct >= 0 else ""
            block["pct"].config(text=f"{sign}{pct:.2f}%", fg=color)
            usc_color = "#7ec46c" if usc >= 0 else "#ff6060"
            block["usc"].config(text=f"{sign}{usc:,.0f} USC", fg=usc_color)
            block["base"].config(text=label)

        if pnl["start_balance"]:
            sb_dt = datetime.fromtimestamp(state.get("start_ts", 0), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            _fmt_pnl_block(self.pnl_total, pnl["total_pct"], pnl["total_usc"], None,
                           f"base: {pnl['start_balance']:,.0f} USC ({sb_dt})")
        if pnl["day_open"]:
            today = datetime.now(timezone.utc).strftime("%d/%m/%Y")
            _fmt_pnl_block(self.pnl_day, pnl["day_pct"], pnl["day_usc"], None,
                           f"obertura {today}: {pnl['day_open']:,.0f} USC")

    def _update_status(self):
        p = find_python_process()
        if p:
            self.status_dot.itemconfig(self.status_dot_id, fill="#22cc44")
            self.status_txt.config(text="XiscoMirror ACTIU", fg="#7ec46c")
            self.pid_lbl.config(text=f"PID: {p['pid']}")
            self.ram_lbl.config(text=f"RAM: {p['ws_mb']:.1f} MB")
            # Uptime aprox des de mtime del log
            if LOG_PATH.exists():
                # Cerquem ultim "Startup ts:" al log per uptime real
                try:
                    with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    matches = re.findall(r"Startup ts: ([\d.]+)", content)
                    if matches:
                        startup = float(matches[-1])
                        elapsed = time.time() - startup
                        mins = int(elapsed / 60)
                        hrs = mins // 60
                        if hrs > 0:
                            self.uptime_lbl.config(text=f"Uptime: {hrs}h {mins%60}m")
                        else:
                            self.uptime_lbl.config(text=f"Uptime: {mins}m {int(elapsed%60)}s")
                except Exception:
                    pass
        else:
            self.status_dot.itemconfig(self.status_dot_id, fill="#cc2244")
            self.status_txt.config(text="XiscoMirror ATURAT", fg="#ff7070")
            self.pid_lbl.config(text="PID: —")
            self.uptime_lbl.config(text="Uptime: —")
            self.ram_lbl.config(text="RAM: —")

    def _update_balance(self):
        bal = read_balance_usc()
        eq = read_equity_usc()
        if bal:
            self.balance_lbl.config(text=f"Balance: {bal:,.0f} USC")
        else:
            self.balance_lbl.config(text="Balance: (sense lectura)")
        if eq:
            self.equity_lbl.config(text=f"Equity:  {eq:,.0f} USC")
            color = "#7ec46c" if eq >= (bal or 0) else "#dcb464"
            self.equity_lbl.config(fg=color)
        else:
            self.equity_lbl.config(text="Equity: (sense lectura)")
        if bal:
            ratio = bal / XISCO_BALANCE_REF_USC
            self.ratio_lbl.config(text=f"Ratio vs Xisco ({XISCO_BALANCE_REF_USC:,.0f}): {ratio:.4f}")

    def _update_counters(self):
        c = parse_log_counters()
        self.c_aperturas["big"].config(text=f"{c['aperturas_rcv']} / {c['aperturas_mir']}")
        self.c_modif["big"].config(text=f"{c['modif_rcv']} / {c['modif_mir']}")
        self.c_cierres["big"].config(text=f"{c['cierres_rcv']} / {c['cierres_mir']}")
        self.err_lbl.config(text=f"Errors: {c['errors']}",
                            fg="#ff6060" if c['errors'] > 0 else "#999")

    def _update_positions(self):
        positions = read_xisco_positions()
        # Neteja
        for item in self.pos_tree.get_children():
            self.pos_tree.delete(item)
        if not positions:
            self.pos_tree.insert("", tk.END, values=("(cap)", "", "", ""))
            return
        for p in positions:
            self.pos_tree.insert("", tk.END, values=(
                p.get("type", "?"),
                f"{p.get('volume', 0):.2f}",
                f"{p.get('price_open', 0):.2f}",
                f"{p.get('profit', 0):+.2f}",
            ))

    def _update_history(self):
        events = parse_log_events(limit=200)
        # Snapshot dels IDs actuals al tree per detectar canvis
        existing = set(self.hist_tree.get_children())
        # Cas simple: reconstruim de zero (200 files, no es notarà)
        for item in existing:
            self.hist_tree.delete(item)
        if not events:
            self.hist_tree.insert("", tk.END, values=("—", "—", "—", "(esperant primer missatge)", "—"))
            return
        # Inserim crono inversa (més recent a dalt)
        for ev in reversed(events):
            # Tag color segons resultat
            if ev["result"] == "REPLICAT" and ev["type"] == "CIERRE":
                tag = "CIERRE_OK"
            elif ev["result"] == "REPLICAT":
                tag = "REPLICAT"
            else:
                tag = "SKIP"
            self.hist_tree.insert("", tk.END,
                values=(ev["ts"][-8:], ev["type"], ev["ticket"], ev["info"], ev["result"]),
                tags=(tag,))

    def _update_log(self):
        lines = tail_log(25)
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        for line in lines:
            # Detectem el tag
            tag = "INFO"
            if "[ERROR]" in line or "FATAL" in line:
                tag = "ERROR"
            elif "[WARNING]" in line:
                tag = "WARN"
            elif "APERTURA" in line:
                tag = "APERTURA"
            elif "CIERRE" in line:
                tag = "CIERRE"
            elif "ORDER written" in line:
                tag = "ORDER"
            self.log_text.insert(tk.END, line, tag)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)


if __name__ == "__main__":
    app = Dashboard()
    app.mainloop()
