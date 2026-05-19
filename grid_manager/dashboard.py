"""
Grid Manager — Multi-bot Portfolio Dashboard

Disseny clar i visual amb seccions:
  1. HEADER  — KPIs principals
  2. EVOLUCIO — grafic temporal
  3. ALLOCATION — distribucio vs target
  4. PER BOT — detall individual
  5. HISTORY — preus + decisions + epochs
  6. MANUAL — deposits/withdrawals
  + MT5 tab — daily/cumulatiu del DualGridEA
"""
# CRITIC numpy fix: el sistema te numpy a 2 rutes amb versions diferents (2.4.2 i 2.4.3).
# NO esborrem cap path (jinja2 i altres viuen a PythonPackages), nomes prioritzem
# user-site al principi perque carregui PRIMER el numpy 2.4.2 (mateix lloc que pandas).
import sys as _sys
_USER_SITE = r"C:\Users\Administrator\AppData\Roaming\Python\Python312\site-packages"
if _USER_SITE in _sys.path:
    _sys.path.remove(_USER_SITE)
_sys.path.insert(0, _USER_SITE)

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import altair as alt
import pandas as pd
import streamlit as st

from config import DB_PATH, BOTS, EDGE_TRIGGER_PCT, TARGET_WEIGHTS
from db import (
    compute_true_total_profit,
    get_all_epochs,
    get_all_transactions,
    get_cumulative_stats_for_symbol,
    log_deposit,
    log_withdrawal,
    log_snapshot,
)

try:
    from pionex_client import get_bot_range, get_current_price
    PIONEX_OK = True
    IMPORT_ERR = None
except Exception as e:
    PIONEX_OK = False
    IMPORT_ERR = str(e)

try:
    from mt5_grid_client import get_mt5_grid_state
    MT5_OK = True
except Exception as e:
    MT5_OK = False
    MT5_ERR = str(e)

# ─── Page config ─────────────────────────────────────────────────────
st.set_page_config(
    page_title="Grid Portfolio",
    layout="wide",
    page_icon="🪙",
    initial_sidebar_state="collapsed",
)


# ═══════════════════════════════════════════════════════════════════════
# AUTH GATE — bloqueja accés sense contrasenya correcta
# ═══════════════════════════════════════════════════════════════════════
def _check_auth():
    import json as _ja
    import bcrypt as _bc
    from pathlib import Path as _Pa

    auth_file = _Pa(__file__).parent / ".auth_state.json"
    if not auth_file.exists():
        st.error("⚠️ Fitxer d'autenticació no trobat. Sistema bloqejat.")
        st.stop()
    try:
        auth_state = _ja.loads(auth_file.read_text())
        pwd_hash = auth_state["password_hash"].encode()
    except Exception as e:
        st.error(f"⚠️ Error llegint auth config: {e}")
        st.stop()

    # Session state inicial
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False
    if "login_attempts" not in st.session_state:
        st.session_state["login_attempts"] = 0

    if st.session_state["authenticated"]:
        # Botó logout discret a sidebar
        with st.sidebar:
            if st.button("🚪 Sortir", key="logout_btn"):
                st.session_state["authenticated"] = False
                st.rerun()
        return  # passa el gate

    # Rate-limit: 5 intents per session
    if st.session_state["login_attempts"] >= 5:
        st.error("🔒 Massa intents fallits. Reinicia el navegador.")
        st.stop()

    # Pantalla de login
    st.markdown("# 🔐 Grid Portfolio")
    st.markdown("### Login requerit")
    pwd_in = st.text_input("Contrasenya", type="password", key="pwd_input")
    col_l, col_r = st.columns([1, 4])
    with col_l:
        login_clicked = st.button("Entrar", type="primary", key="login_btn")
    with col_r:
        if st.session_state["login_attempts"] > 0:
            st.caption(f"Intents: {st.session_state['login_attempts']}/5")

    if login_clicked:
        if pwd_in and _bc.checkpw(pwd_in.encode(), pwd_hash):
            st.session_state["authenticated"] = True
            st.session_state["login_attempts"] = 0
            st.rerun()
        else:
            st.session_state["login_attempts"] += 1
            st.error("❌ Contrasenya incorrecta")

    st.stop()  # bloqueja tot el contingut fins autenticar


_check_auth()
# A partir d'aquí només arriba codi si l'usuari està autenticat


# Custom CSS for cleaner look
st.markdown("""
<style>
.stMetric { background-color: rgba(255,255,255,0.03); padding: 12px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.05); }
.stMetric label { font-size: 0.85rem; opacity: 0.8; }
[data-testid="stMetricValue"] { font-size: 1.4rem; font-weight: 600; }
[data-testid="stMetricDelta"] { font-size: 0.8rem; }
div[data-baseweb="tab-list"] button { font-size: 0.95rem; }
.bot-header { padding: 10px 14px; border-radius: 8px; background: linear-gradient(90deg, rgba(100,180,255,0.08), rgba(100,180,255,0.02)); margin-bottom: 16px; }
</style>
""", unsafe_allow_html=True)


# ─── Cached fetchers ─────────────────────────────────────────────────
# TTL=90s perquè l'auto-refresh és cada 60s → garantit cache hit després
# del primer load. Botó "Refresh ara" neteja el cache.
@st.cache_data(ttl=90)
def cached_btc_price():
    return get_current_price("BTC_USDT")


@st.cache_data(ttl=90)
def cached_bot_state(bot_id, symbol):
    return get_bot_range(bot_id, symbol=symbol)


@st.cache_data(ttl=120)
def cached_cum_stats(symbol):
    """Cache historic stats per symbol — només canvia quan es tanca un epoch."""
    return get_cumulative_stats_for_symbol(symbol)


def _fetch_all_bots_parallel(bots_dict):
    """Fetch tots els bot_range en paral·lel (4 threads).
    Reduix first-load de ~5.5s a ~1.7s.
    """
    def _one(item):
        name, cfg = item
        try:
            return name, cached_bot_state(cfg["id"], cfg["symbol"]), None
        except Exception as e:
            return name, None, str(e)
    with ThreadPoolExecutor(max_workers=max(2, len(bots_dict))) as ex:
        return list(ex.map(_one, bots_dict.items()))


btc_usdt_price = 0.0
if PIONEX_OK:
    try:
        btc_usdt_price = cached_btc_price()
    except Exception:
        pass


def quote_to_usdt(q: str, amt: float) -> float:
    if q == "USDT":
        return amt
    if q == "BTC":
        return amt * btc_usdt_price
    return amt


def fmt_num(v: float, threshold: float = 1.0, decimals_small: int = 6, decimals_big: int = 2) -> str:
    """Smart number formatting based on magnitude."""
    if abs(v) < threshold:
        return f"{v:.{decimals_small}g}"
    return f"{v:,.{decimals_big}f}"


# ─── Fetch bot states + enrich with historic data ────────────────────
# Fetch paral·lel (4 threads) → reduix first-load drasticament
bot_states = {}
fetch_errors = {}
if PIONEX_OK:
    parallel_results = _fetch_all_bots_parallel(BOTS)
    for name, raw_state, err in parallel_results:
        if err is not None or raw_state is None:
            fetch_errors[name] = err or "no state"
            continue
        try:
            cfg = BOTS[name]
            s = raw_state
            s["cfg"] = cfg
            s["quote"] = cfg["quote"]
            s["bot_name"] = name
            s["symbol"] = cfg["symbol"]
            s["grid_profit_usdt"] = quote_to_usdt(cfg["quote"], s.get("grid_profit", 0))
            quote_in_bot = s.get("quote_in_bot", 0)
            base_in_bot = s.get("base_in_bot", 0)
            s["quote_value_usdt"] = quote_to_usdt(cfg["quote"], quote_in_bot)
            base_value_in_quote = base_in_bot * s["price"]
            s["base_value_usdt"] = quote_to_usdt(cfg["quote"], base_value_in_quote)
            s["total_value_usdt"] = s["quote_value_usdt"] + s["base_value_usdt"]

            # Historic from closed epochs (cached — només canvia quan es tanca un epoch)
            hist = cached_cum_stats(cfg["symbol"])
            s["historic_profit_quote"] = hist["profit"]
            s["historic_profit_usdt"] = quote_to_usdt(cfg["quote"], hist["profit"])
            s["historic_cycles"] = hist["cycles"]
            s["historic_epochs"] = hist["epochs_count"]
            s["cum_grid_profit"] = s.get("grid_profit", 0) + hist["profit"]
            s["cum_grid_profit_usdt"] = s["grid_profit_usdt"] + s["historic_profit_usdt"]
            s["cum_paired_cycles"] = s.get("paired_cycles", 0) + hist["cycles"]

            # Efficiency metric (spacing / price — beats fees if > ~0.1%)
            grid_step = (s["top"] - s["bottom"]) / max(s.get("grid_rows", 1), 1)
            s["grid_step"] = grid_step
            s["spacing_pct"] = (grid_step / s["price"]) * 100 if s["price"] else 0
            s["fee_ratio"] = s["spacing_pct"] / 0.10 if s["spacing_pct"] else 0

            # NOTA: log_snapshot eliminat del render — el monitor.py ja l'escriu
            # cada 5min via Task Scheduler (evita 4 writes SQLite per cada càrrega)
            bot_states[name] = s
        except Exception as e:
            fetch_errors[name] = str(e)

# ─── MT5 Dual Grid (XAUUSD) — separat, en pestanya propia ─────────────
mt5_state = None
if MT5_OK:
    try:
        mt5_state = get_mt5_grid_state()
    except Exception as e:
        mt5_state = {"available": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════
# 1) HEADER — Title + global KPIs
# ═══════════════════════════════════════════════════════════════════════
header_l, header_r = st.columns([3, 1])
with header_l:
    st.title("🪙 Grid Portfolio")
    st.caption(
        f"**{len(bot_states)}/{len(BOTS)}** bots live · "
        f"BTC/USDT ref: **${btc_usdt_price:,.0f}** · "
        f"Estàtic (sense auto-refresh) · "
        f"Última càrrega: {datetime.now().strftime('%H:%M:%S')}"
    )
with header_r:
    if st.button("🔄 Refresh ara", use_container_width=True, type="primary"):
        st.cache_data.clear()
        st.rerun()

if fetch_errors:
    with st.expander(f"⚠️ {len(fetch_errors)} bot(s) amb error de fetch"):
        for k, v in fetch_errors.items():
            st.error(f"**{k}**: {v}")

if not bot_states:
    st.error("Cap bot accessible. Verifica connexio Pionex i config.")
    st.stop()

# Aggregate metrics
tot_realized = sum(s["cum_grid_profit_usdt"] for s in bot_states.values())
tot_realized_current_epoch = sum(s["grid_profit_usdt"] for s in bot_states.values())
tot_value = sum(s["total_value_usdt"] for s in bot_states.values())  # NOMÉS bots (matching Pionex)
tot_cycles = sum(s["cum_paired_cycles"] for s in bot_states.values())
tot_cycles_current = sum(s.get("paired_cycles", 0) for s in bot_states.values())
tot_filled = sum(s.get("filled_orders", 0) for s in bot_states.values())

# Capital invertit als bots — del NOSTRE tracker (incloe rebalanceigs, no del camp Pionex que no s'actualitza)
try:
    from investment_tracker import get_total_invested as _git
    tot_invested = sum(_git(name, fallback_initial=s.get("usdt_investment", 0))
                       for name, s in bot_states.items())
except Exception:
    tot_invested = sum(s.get("usdt_investment", 0) for s in bot_states.values())

roi_pct = (tot_value - tot_invested) / tot_invested * 100 if tot_invested else 0
realized_pct = tot_realized / tot_invested * 100 if tot_invested else 0

# Cost real de TOTES les recolocacions registrades (tracking explícit nostre)
try:
    from db import get_total_recolocation_cost
    _reloc_stats = get_total_recolocation_cost()
    tot_reloc_cost = _reloc_stats["total_usdt"]
    tot_reloc_count = _reloc_stats["count"]
except Exception:
    tot_reloc_cost = 0.0
    tot_reloc_count = 0

# Grid Alpha VERITABLE NET = el que mostra Pionex MENYS el cost real de recolocacions
tot_realized_true_net = tot_realized - tot_reloc_cost
true_net_pct = (tot_realized_true_net / tot_invested * 100) if tot_invested else 0

# Inventari MTM REAL = Net P&L total MENYS Grid Alpha VERITABLE.
# Aixi la descomposicio Net_PnL = Grid_Alpha_VERITABLE + Inventari_MTM_real es coherent
# i les fees de recolocacio NO apareixen barrejades amb el moviment de preu.
tot_mtm = (tot_value - tot_invested) - tot_realized_true_net
mtm_pct = (tot_mtm / tot_invested * 100) if tot_invested else 0

# ─── Days running per bot (per a calcular % diari mig) ───────────────
# Usa l'epoch open_ts mes antic per bot; fallback a 1 dia si no n'hi ha.
bot_days_running = {}
days_running_max = 1.0
try:
    _con_age = sqlite3.connect(DB_PATH)
    for _name, _cfg in BOTS.items():
        _cur = _con_age.execute(
            "SELECT MIN(opened_ts) FROM epochs WHERE bot_id = ?",
            (_cfg["id"],),
        )
        _oldest = _cur.fetchone()[0]
        if _oldest:
            try:
                _oldest_dt = datetime.fromisoformat(_oldest.replace("Z", "+00:00"))
                if _oldest_dt.tzinfo is None:
                    _oldest_dt = _oldest_dt.replace(tzinfo=timezone.utc)
                _days = (datetime.now(timezone.utc) - _oldest_dt).total_seconds() / 86400
                bot_days_running[_name] = max(1 / 24, _days)  # min 1h per evitar div/0
                days_running_max = max(days_running_max, _days)
            except Exception:
                bot_days_running[_name] = 1.0
        else:
            bot_days_running[_name] = 1.0
    _con_age.close()
except Exception:
    pass

# % diari mig (Grid Alpha veritable / capital / dies des primer epoch)
true_net_pct_daily = true_net_pct / days_running_max if days_running_max > 0 else 0
realized_pct_daily_agg = realized_pct / days_running_max if days_running_max > 0 else 0

st.markdown("### 💰 Portfolio Overview")
st.caption(
    "🎯 **Grid Alpha VERITABLE** = Grid Alpha brut (Pionex) − Cost real recolocacions (snapshot abans/després). "
    "L'única mètrica que mesura SI el sistema funciona, NET de tot.  \n"
    "🔄 **Cost Recolocacions** = sumatori dels costos mesurats amb precisió a cada recolocació."
)
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric(
    "Capital Invertit",
    f"${tot_invested:,.2f}",
    help="Suma de usdt_investment de cada bot (capital desplegat ACTUAL, inclou rebalanceigs).",
)
k2.metric(
    "Valor Total (Bots)",
    f"${tot_value:,.2f}",
    delta=f"{roi_pct:+.2f}% Net P&L",
    delta_color="normal",  # + verd / − vermell (natural)
    help="Suma de bot.total_value_usdt — matching Pionex. Combina Grid Alpha + Inventari MTM.",
)
_realized_pct = (tot_realized / tot_invested * 100) if tot_invested > 0 else 0
k3.metric(
    "🎯 Grid Alpha VERITABLE",
    f"${tot_realized_true_net:+.4f}",
    delta=f"{true_net_pct:+.3f}% total · {true_net_pct_daily:+.4f}%/dia",
    delta_color="normal",  # + verd / − vermell
    help=(
        f"Grid Alpha NET DESCOMPTANT el cost real de cada recolocació (tracking nostre).\n\n"
        f"  Grid Alpha brut (Pionex):     ${tot_realized:.4f}\n"
        f"  − Cost recolocacions ({tot_reloc_count}):  ${tot_reloc_cost:.4f}\n"
        f"  = Veritable NET:              ${tot_realized_true_net:.4f}\n\n"
        f"  Dies running (max):           {days_running_max:.2f} dies\n"
        f"  % total:                      {true_net_pct:+.4f}%\n"
        f"  % diari mig:                  {true_net_pct_daily:+.4f}%/dia\n"
        f"  APR anualitzat aprox:         {true_net_pct_daily * 365:+.2f}%/any\n\n"
        f"Aquesta és la rendibilitat REAL del sistema. Si és positiva, el grid funciona."
    ),
)
k4.metric(
    "🔄 Cost Recolocacions",
    f"${tot_reloc_cost:.4f}",
    delta=f"{tot_reloc_count} recolocacions",
    delta_color="off",  # gris (és informatiu, ni bo ni dolent)
    help=(
        "Cost REAL acumulat de totes les recolocacions, mesurat amb snapshot abans/després.\n\n"
        "Inclou fees consumides + qualsevol reducció de gridProfit que faci Pionex."
    ),
)
k5.metric(
    "Cicles Tancats",
    f"{tot_cycles}",
    delta=f"+{tot_cycles_current} epoch actual" if tot_cycles > tot_cycles_current else None,
    help="Total cicles buy+sell complerts des de l'inici (sobreviu recreacions de bot).",
)
k6.metric(
    "Bots Actius",
    f"{len(bot_states)} / {len(BOTS)}",
    delta=f"{tot_filled} fills epoch actual",
)

st.divider()


# ═══════════════════════════════════════════════════════════════════════
# TOP-LEVEL TABS: Pionex (current dashboard) vs MT5 XAUUSD (new)
# ═══════════════════════════════════════════════════════════════════════
top_tab_pionex, top_tab_mt5 = st.tabs([
    "📊  Pionex Crypto Grids",
    "💎  MT5 XAUUSD Dual Grid",
])

# Truc: alias 'st' temporalment al container del tab. Aixi tot el codi original
# que crida st.markdown(), st.metric(), etc. va al tab sense haver d'identar.
_st_orig = st
st = top_tab_pionex  # type: ignore  (Streamlit DeltaGenerator te els mateixos metodes)


# ═══════════════════════════════════════════════════════════════════════
# 1b) SYSTEM HEALTH — Monitor status + auto-trail activity
# ═══════════════════════════════════════════════════════════════════════
### ── Reserva del sistema ────────────────────────────────────────────
st.markdown("### 💰 Reserva del sistema")
try:
    # Wallet USDT real (font de veritat)
    _real_wallet = 0.0
    _btc_free_usd = 0.0
    try:
        from pionex_client import get_balance as _gb
        _bal = _gb() or {}
        _real_wallet = float(_bal.get("USDT", 0))
        _btc_free_units = float(_bal.get("BTC", 0))
        _btc_free_usd = _btc_free_units * btc_usdt_price
    except Exception:
        pass

    rcol1, rcol2, rcol3, rcol4 = st.columns(4)
    rcol1.metric("💵 USDT lliure (wallet)", f"${_real_wallet:,.2f}",
                 help="USDT efectiu al wallet de Pionex, fora de bots.")
    rcol2.metric("₿ BTC lliure", f"${_btc_free_usd:,.2f}",
                 delta=f"{_btc_free_units:.6f} BTC" if _btc_free_usd > 0 else None,
                 delta_color="off",
                 help="BTC al wallet fora del bot — convertible a USDT si cal.")
    rcol3.metric("📦 Invertit a bots", f"${tot_invested:,.2f}",
                 help="Suma de capital actiu a tots els grids.")
    rcol4.metric("🏦 Patrimoni total", f"${tot_invested + _real_wallet + _btc_free_usd:,.2f}",
                 help="bots + USDT lliure + BTC lliure (equivalent USDT).")
except Exception as _e:
    st.info(f"No s'ha pogut llegir el wallet: {_e}")

### ── Salut del sistema ───────────────────────────────────────────────
st.markdown("### 🩺 Salut del sistema")

from pathlib import Path as _Path
_log_path = _Path(__file__).parent / "logs" / "monitor.log"
_status_data = {"last_run": None, "last_cycle_ok": False, "last_cycle_age_min": None,
                "triggers_today": 0, "adjusts_ok_today": 0, "adjusts_fail_today": 0,
                "last_trigger": None, "last_adjust": None, "recent_lines": []}

if _log_path.exists():
    try:
        with open(_log_path, "r", encoding="utf-8", errors="ignore") as _f:
            _lines = _f.readlines()
        # Get last "Monitor cycle done"
        _today = datetime.now().strftime("%Y-%m-%d")
        for ln in reversed(_lines[-500:]):
            if "Monitor cycle done" in ln:
                _ts_str = ln.split(" |")[0].strip()
                try:
                    _last_run = datetime.strptime(_ts_str, "%Y-%m-%d %H:%M:%S,%f")
                    _status_data["last_run"] = _last_run
                    _status_data["last_cycle_ok"] = True
                    _status_data["last_cycle_age_min"] = (datetime.now() - _last_run).total_seconds() / 60
                except Exception:
                    pass
                break
        # Count today's triggers/adjusts
        _today_lines = [l for l in _lines if l.startswith(_today)]
        _status_data["triggers_today"] = sum(1 for l in _today_lines if "TRIGGER:" in l)
        _status_data["adjusts_ok_today"] = sum(1 for l in _today_lines if "adjust_params executed" in l and "result=True" in l)
        _status_data["adjusts_fail_today"] = sum(1 for l in _today_lines if ("adjust_params executed" in l and "result=False" in l) or "adjust_params FAILED" in l)
        # Last trigger and adjust
        for ln in reversed(_lines[-2000:]):
            if "TRIGGER:" in ln and not _status_data["last_trigger"]:
                _status_data["last_trigger"] = ln.strip()
            if "adjust_params executed" in ln and not _status_data["last_adjust"]:
                _status_data["last_adjust"] = ln.strip()
            if _status_data["last_trigger"] and _status_data["last_adjust"]:
                break
        _status_data["recent_lines"] = [l.rstrip() for l in _lines[-15:]]
    except Exception as _e:
        st.error(f"Error llegint log: {_e}")
else:
    st.warning(f"No s'ha trobat el log del monitor: {_log_path}")

# Row 1: status indicators
sh1, sh2, sh3, sh4, sh5 = st.columns(5)
_age = _status_data["last_cycle_age_min"]
if _age is None:
    sh1.metric("Monitor", "❌ inactiu", delta="cap log trobat", delta_color="inverse")
elif _age < 7:
    sh1.metric("Monitor", "✅ actiu", delta=f"fa {_age:.1f} min")
elif _age < 15:
    sh1.metric("Monitor", "⚠️ retard", delta=f"fa {_age:.1f} min", delta_color="inverse")
else:
    sh1.metric("Monitor", "🔴 aturat", delta=f"fa {_age:.0f} min", delta_color="inverse")

sh2.metric("Triggers avui", f"{_status_data['triggers_today']}",
           help="Cops que algun bot ha tocat la zona de trail (≤10% del límit)")
sh3.metric("Adjusts OK avui", f"{_status_data['adjusts_ok_today']}", delta="zero cost (in-place)",
           help="Recolocacions automàtiques amb `adjustParams` (no ven inventari)")
if _status_data["adjusts_fail_today"] > 0:
    sh4.metric("Adjusts fallits", f"{_status_data['adjusts_fail_today']}",
               delta="revisa log", delta_color="inverse")
else:
    sh4.metric("Adjusts fallits", "0", delta_color="off")
sh5.metric("Frequencia", "5 min", help="Cicle de polling del monitor (Task Scheduler)")

# Row 2: last events
ev1, ev2 = st.columns(2)
with ev1:
    st.markdown("**Últim trigger:**")
    if _status_data["last_trigger"]:
        # Format: 2026-05-12 12:01:20,952 | WARNING | [PAXG_USDT] TRIGGER: near_upper_edge at price 4696.78
        st.code(_status_data["last_trigger"][:200], language=None)
    else:
        st.info("Sense triggers recents")
with ev2:
    st.markdown("**Últim adjust:**")
    if _status_data["last_adjust"]:
        st.code(_status_data["last_adjust"][:200], language=None)
    else:
        st.info("Sense adjusts recents")

with st.expander("📃 Veure últimes 15 línies del log del monitor"):
    if _status_data["recent_lines"]:
        st.code("\n".join(_status_data["recent_lines"]), language=None)
    else:
        st.info("Log buit")

st.divider()


# ═══════════════════════════════════════════════════════════════════════
# 2) EVOLUTION CHART — ELIMINAT (no es veia bé, donava errors de cache_data
#    al estar dins el tab on `st` apunta a DeltaGenerator).
#    Substituït pel pie chart d'Allocation (secció 3 a sota).
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# 3) ALLOCATION — Pie chart + Current vs Target weights
# ═══════════════════════════════════════════════════════════════════════
st.markdown("### 🎯 Composició del portfolio")

# Color scale global (reutilitzable per altres seccions)
_bot_order_global = ["PAXG", "BTC", "ETH", "SOL", "USOX", "SPYX"]
_color_scale_global = alt.Scale(
    domain=_bot_order_global,
    range=["#F4B400", "#F58518", "#5B8FF9", "#A05BF9", "#22C55E", "#EF4444"],
)

# Build allocation dataframe
_alloc_data = []
for name, cfg in BOTS.items():
    s = bot_states.get(name)
    target = TARGET_WEIGHTS.get(name, 0) * 100
    if s and tot_value > 0:
        cur_val = s["total_value_usdt"]
        cur_pct = cur_val / tot_value * 100
        _alloc_data.append({
            "Bot": name.replace("_USDT", ""),
            "Valor": cur_val,
            "Pct": cur_pct,
            "Target": target,
            "Desviacio": cur_pct - target,
        })

if _alloc_data:
    _alloc_df = pd.DataFrame(_alloc_data)
    pie_col, table_col = st.columns([1.2, 1])

    with pie_col:
        # Donut chart amb labels de %
        _pie_base = alt.Chart(_alloc_df).encode(
            theta=alt.Theta("Valor:Q", stack=True),
            color=alt.Color("Bot:N", scale=_color_scale_global,
                            legend=alt.Legend(title="Bot", orient="right",
                                              labelFontSize=12, symbolSize=120)),
            tooltip=[
                alt.Tooltip("Bot:N", title="Bot"),
                alt.Tooltip("Valor:Q", title="Valor", format="$,.2f"),
                alt.Tooltip("Pct:Q", title="% Actual", format=".1f"),
                alt.Tooltip("Target:Q", title="% Target", format=".1f"),
                alt.Tooltip("Desviacio:Q", title="Δ vs target", format="+.1f"),
            ],
        )
        _donut = _pie_base.mark_arc(innerRadius=70, outerRadius=140, stroke="#000", strokeWidth=2)
        _labels = _pie_base.mark_text(radius=160, size=13, fontWeight="bold").encode(
            text=alt.Text("Pct:Q", format=".1f"),
            color=alt.value("#FFFFFF"),
        )
        _chart = (_donut + _labels).properties(height=360)
        st.altair_chart(_chart, use_container_width=True)
        st.caption(f"Total portfolio: **${tot_value:,.2f}** · "
                   f"{len(_alloc_data)} bots actius")

    with table_col:
        st.markdown("**Allocation vs Target**")
        # Compact table view
        _display_df = _alloc_df.copy()
        _display_df["Actual"] = _display_df.apply(
            lambda r: f"{r['Pct']:.1f}% (${r['Valor']:,.0f})", axis=1)
        _display_df["Target"] = _display_df["Target"].apply(lambda x: f"{x:.0f}%")
        _display_df["Δ"] = _display_df["Desviacio"].apply(
            lambda x: f"{x:+.1f}%" if abs(x) > 0.1 else "—")
        st.dataframe(
            _display_df[["Bot", "Actual", "Target", "Δ"]],
            use_container_width=True,
            hide_index=True,
            height=min(360, 50 + 38 * len(_alloc_df)),
        )

        # Resum desviacio
        max_dev = _alloc_df["Desviacio"].abs().max()
        if max_dev > 5:
            st.warning(f"⚠️ Desviació màxima: {max_dev:.1f}% — considera rebalanceig")
        elif max_dev > 2:
            st.info(f"ℹ️ Desviació màxima: {max_dev:.1f}%")
        else:
            st.success(f"✅ Portfolio balancejat (max Δ {max_dev:.1f}%)")
else:
    st.info("Sense dades d'allocation (cap bot actiu).")

st.divider()


# ═══════════════════════════════════════════════════════════════════════
# 4) PER-BOT TABS — Tot el detall individual
# ═══════════════════════════════════════════════════════════════════════
st.markdown("### 🤖 Detall per bot")

bot_tabs = st.tabs([f"  {name.replace('_', '/')}  " for name in BOTS.keys()])
for tab, (name, cfg) in zip(bot_tabs, BOTS.items()):
    with tab:
        s = bot_states.get(name)
        if not s:
            st.error(f"No state: {fetch_errors.get(name, 'unknown')}")
            continue

        # ─── Bot header amb ID i historic ─────────────────────────────
        hist_eps = s.get("historic_epochs", 0)
        epoch_info = (
            f"📦 Bot actual: `{cfg['id']}` · Inversio: {fmt_num(s.get('usdt_investment', 0), 100, 4, 2)} {cfg['quote']} · "
            f"Status: **{s['status']}** · "
            f"📜 Historic: {hist_eps} epoch(s) tancat(s) anteriors"
        )
        st.markdown(f"<div class='bot-header'>{epoch_info}</div>", unsafe_allow_html=True)

        # ─── Row 1: Profit / Cycles (acumulat) ────────────────────────
        c1, c2, c3, c4 = st.columns(4)
        # Calcula % diari mig per aquest bot
        bot_days = bot_days_running.get(name, 1.0)
        bot_invested = s.get('usdt_investment', 0) or 1
        bot_pct_total = (s['cum_grid_profit_usdt'] / bot_invested) * 100
        bot_pct_daily = bot_pct_total / bot_days if bot_days > 0 else 0
        c1.metric(
            "💰 Realitzat TOTAL",
            f"${s['cum_grid_profit_usdt']:+.4f}",
            delta=f"{bot_pct_total:+.3f}% · {bot_pct_daily:+.4f}%/dia",
            delta_color="normal",  # + verd / − vermell
            help=(
                f"Acumulat de {hist_eps} epoch(s) anteriors + epoch actual.\n\n"
                f"  Quote raw:               {s['cum_grid_profit']:.4f} {cfg['quote']}\n"
                f"  Capital invertit:        ${bot_invested:,.2f}\n"
                f"  Dies running:            {bot_days:.2f} dies\n"
                f"  % total:                 {bot_pct_total:+.4f}%\n"
                f"  % diari mig:             {bot_pct_daily:+.4f}%/dia\n"
                f"  APR aprox:               {bot_pct_daily * 365:+.2f}%/any\n\n"
                f"Epoch actual sol: {s.get('grid_profit', 0):.4g} {cfg['quote']} / {s.get('paired_cycles', 0)} cicles"
            ),
        )
        c2.metric(
            "🔄 Cicles TOTAL",
            f"{s['cum_paired_cycles']}",
            delta=f"+{s.get('paired_cycles', 0)} actual" if hist_eps > 0 else None,
        )
        c3.metric(
            "📊 Valor Bot (USDT)",
            f"${s['total_value_usdt']:,.2f}",
            help="Inventari (base × preu) + quote actual.",
        )
        c4.metric(
            "📋 Ordres al book",
            f"{s.get('placed_orders', 0)}",
            delta=f"{s.get('filled_orders', 0)} filled",
        )

        # ─── Row 2: Eficiencia + Range info ───────────────────────────
        e1, e2, e3, e4 = st.columns(4)
        fee_color = "off" if s["fee_ratio"] > 3 else ("inverse" if s["fee_ratio"] < 2 else "normal")
        e1.metric(
            "⚡ Spacing efectiu",
            f"{fmt_num(s['grid_step'], 1, 6, 4)} {cfg['quote']}",
            delta=f"{s['spacing_pct']:.3f}% del preu",
            help="Diferencia entre nivells consecutius. spacing/preu > 0,1% per superar fees.",
        )
        e2.metric(
            "🎯 Fee ratio",
            f"{s['fee_ratio']:.2f}×",
            delta="OK (>3×)" if s["fee_ratio"] > 3 else ("⚠️ baix" if s["fee_ratio"] > 1.5 else "🔴 marginal"),
            delta_color=fee_color,
            help="Quants cops el profit per cicle supera les fees (0.1% RT). Sweet spot >3×.",
        )
        e3.metric(
            "📈 Preu actual",
            fmt_num(s["price"], 1, 6, 2),
        )
        e4.metric(
            "📐 Rang",
            f"{fmt_num(s['bottom'], 1, 4, 0)} → {fmt_num(s['top'], 1, 4, 0)}",
            delta=f"{s.get('grid_rows', 0)} nivells",
        )

        # ─── Row 3: Position-in-range visual ──────────────────────────
        width = s["top"] - s["bottom"]
        pct_in_range = (s["price"] - s["bottom"]) / width if width > 0 else 0.5
        pct_in_range = max(0.0, min(1.0, pct_in_range))

        bar_col_l, bar_col_r = st.columns([4, 1])
        with bar_col_l:
            st.markdown(f"**Posicio del preu dins del rang**")
            st.progress(pct_in_range)
            top_alert = "⚠️" if s["dist_to_top_pct"] < EDGE_TRIGGER_PCT else "✓"
            bot_alert = "⚠️" if s["dist_to_bottom_pct"] < EDGE_TRIGGER_PCT else "✓"
            st.caption(
                f"⬇️ {bot_alert} Dist bottom: **{s['dist_to_bottom_pct']:.1%}** · "
                f"⬆️ {top_alert} Dist top: **{s['dist_to_top_pct']:.1%}** · "
                f"Posicio: {pct_in_range:.0%} · "
                f"Trigger trailing si <{EDGE_TRIGGER_PCT:.0%}"
            )

        # ─── Row 4: Inventari ─────────────────────────────────────────
        st.markdown("**📦 Inventari actual**")
        inv1, inv2, inv3 = st.columns(3)
        inv1.metric(
            f"{cfg['quote']} (cash)",
            fmt_num(s.get('quote_in_bot', 0), 1, 6, 4),
        )
        inv2.metric(
            f"{cfg['base']} (token)",
            fmt_num(s.get('base_in_bot', 0), 1, 6, 4),
            delta=f"${s['base_value_usdt']:,.2f} USDT-eq",
        )
        inv3.metric(
            "💵 Cost mig",
            fmt_num(s.get('avg_cost', 0), 1, 6, 2),
        )

        # ─── Row 5: Grid Map visual (tots els nivells amb estat) ───────
        st.markdown(
            f"<h4 style='margin-top:24px; margin-bottom:6px;'>"
            f"📐 Mapa del grid — <span style='color:#5B8FF9;'>{name.replace('_', '/')}</span> "
            f"<span style='color:#888; font-size:0.75em; font-weight:normal;'>"
            f"({cfg['base']} · preu ${s['price']:,.4f})</span>"
            f"</h4>"
            f"<div style='color:#888; font-size:0.9em; margin-bottom:8px;'>"
            f"Tots els nivells del grid · groc fluorescent = inventari comprat (💎 HELD) · "
            f"blau = SELL pendent · verd = BUY pendent · vermell = preu actual</div>",
            unsafe_allow_html=True,
        )
        try:
            n_rows = int(s.get("grid_rows", 0))
            step = s["grid_step"]
            bottom = s["bottom"]
            top = s["top"]
            current = s["price"]
            avg_cost = float(s.get("avg_cost", 0) or 0)
            base_in = float(s.get("base_in_bot", 0) or 0)
            quote_in = float(s.get("quote_in_bot", 0) or 0)
            invest = float(s.get("usdt_investment", 0) or 0)

            if n_rows >= 1 and step > 0:
                # Quantitat per nivell aprox: capital / (2 × n_rows) en cada costat
                qty_per_level = (invest / max(2 * n_rows, 1)) / current if current > 0 else 0
                # Inventari estimat per nivell HELD (les SELLs pendents tenen el seu inventari)
                # Sells pendents = base_in_bot / qty_per_level (aprox)
                # held_levels = int round
                n_held = round(base_in / qty_per_level) if qty_per_level > 0 else 0
                n_held = max(0, min(n_held, n_rows))

                # Construeix HTML table de tots els nivells
                rows_html = ["<div style='font-family: monospace; font-size: 13px;'>"]
                rows_html.append("<table style='width:100%; border-collapse:collapse;'>")
                rows_html.append("<thead style='background:rgba(255,255,255,0.05);'>"
                                 "<tr>"
                                 "<th style='padding:6px 10px; text-align:left;'>Estat</th>"
                                 "<th style='padding:6px 10px; text-align:left;'>L</th>"
                                 "<th style='padding:6px 10px; text-align:right;'>Preu</th>"
                                 "<th style='padding:6px 10px; text-align:right;'>Δ vs actual</th>"
                                 "<th style='padding:6px 10px; text-align:right;'>Qty/cel·la</th>"
                                 "<th style='padding:6px 10px; text-align:left;'>Visual</th>"
                                 "</tr></thead><tbody>")

                # Iterar de més alt a més baix
                # Determinar quins nivells per damunt del preu actual són SELL_PENDING vs HELD
                # SELLS pendents corresponen a inventari ja comprat: els n_held nivells JUST PER DAMUNT del preu actual
                # son SELL amb inventari, la resta superior son SELL sense inventari (esperant cycle previ)
                level_prices = [bottom + i * step for i in range(n_rows + 1)]
                # Index del nivell més proper al preu actual
                closest_idx = min(range(len(level_prices)),
                                  key=lambda i: abs(level_prices[i] - current))

                for idx in range(len(level_prices) - 1, -1, -1):
                    price = level_prices[idx]
                    dist_pct = (price - current) / current * 100 if current > 0 else 0

                    # Determinar tipus i estil
                    if abs(dist_pct) < (step / current * 100) / 2 and idx == closest_idx:
                        # Nivell del preu actual
                        kind = "⚡ ACTUAL"
                        row_bg = "rgba(239, 68, 68, 0.35)"
                        color = "#FFFFFF"
                        bold = "bold"
                        bar_char = "█" * 30
                        bar_color = "#EF4444"
                    elif price > current:
                        # Nivells SELL (per damunt del preu)
                        levels_above_idx = idx - closest_idx  # 1, 2, ...
                        if 0 < levels_above_idx <= n_held:
                            # SELL amb inventari (té qty per vendre = HELD)
                            kind = "💎 HELD"
                            row_bg = "rgba(250, 204, 21, 0.30)"  # groc fluorescent
                            color = "#FACC15"
                            bold = "bold"
                            bar_char = "▰" * 22
                            bar_color = "#FACC15"
                        else:
                            kind = "🔵 SELL"
                            row_bg = "rgba(59, 130, 246, 0.06)"
                            color = "#60A5FA"
                            bold = "normal"
                            bar_char = "─" * 18
                            bar_color = "#60A5FA"
                    else:
                        # Nivells BUY (per sota del preu, esperant fill)
                        kind = "🟢 BUY"
                        row_bg = "rgba(16, 185, 129, 0.06)"
                        color = "#34D399"
                        bold = "normal"
                        bar_char = "─" * 18
                        bar_color = "#34D399"

                    rows_html.append(
                        f"<tr style='background:{row_bg};'>"
                        f"<td style='padding:5px 10px; color:{color}; font-weight:{bold};'>{kind}</td>"
                        f"<td style='padding:5px 10px;'>L{idx}</td>"
                        f"<td style='padding:5px 10px; text-align:right; font-weight:{bold};'>${price:,.4f}</td>"
                        f"<td style='padding:5px 10px; text-align:right; color:#888;'>{dist_pct:+.2f}%</td>"
                        f"<td style='padding:5px 10px; text-align:right;'>{qty_per_level:.4g} {cfg['base']}</td>"
                        f"<td style='padding:5px 10px; color:{bar_color};'>{bar_char}</td>"
                        f"</tr>"
                    )
                rows_html.append("</tbody></table></div>")
                st.markdown("".join(rows_html), unsafe_allow_html=True)

                # Resum sota la taula
                _resum_cols = st.columns(3)
                _resum_cols[0].markdown(
                    f"💎 **Nivells amb inventari (HELD)**: {n_held} / {n_rows}  \n"
                    f"📦 Inventari total: **{base_in:.4g} {cfg['base']}**  \n"
                    f"💵 Cost mig: **${avg_cost:,.4f}**"
                )
                _resum_cols[1].markdown(
                    f"🔵 SELL pendents: **{n_rows - closest_idx - n_held}**  \n"
                    f"💎 SELL amb inventari: **{n_held}**  \n"
                    f"🟢 BUY pendents: **{closest_idx}**"
                )
                _resum_cols[2].markdown(
                    f"⚡ Preu actual: **${current:,.4f}**  \n"
                    f"📐 Step: **${step:,.4f}** ({s['spacing_pct']:.3f}%)  \n"
                    f"💰 Qty/cel·la: **{qty_per_level:.4g} {cfg['base']}** (~${qty_per_level * current:.2f})"
                )
        except Exception as _grid_err:
            st.error(f"Error renderitzant mapa del grid: {_grid_err}")


st.divider()


# ═══════════════════════════════════════════════════════════════════════
# 5) HISTORY — Price charts + decisions + epochs + transactions
# ═══════════════════════════════════════════════════════════════════════
st.markdown("### 📜 Historic")
ht1, ht2, ht3, ht4 = st.tabs([
    "📈 Preus per bot",
    "🤖 Decisions trailing",
    "🧬 Epochs (vida dels bots)",
    "💸 Transaccions manuals",
])


def _query_df(con, sql, cols, params=()):
    cur = con.execute(sql, params)
    rows = cur.fetchall()
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows, columns=cols)


if DB_PATH.exists():
    con = sqlite3.connect(DB_PATH)

    with ht1:
        show_all_epochs = st.checkbox(
            "Mostrar TOTS els epochs (pot tenir salts on s'ha recreat el bot)",
            value=False, key="ht1_all_epochs",
        )
        for name, cfg in BOTS.items():
            with st.expander(f"📈 {name.replace('_','/')}", expanded=True):
                # Per defecte filtrar a l'epoch actual (evita salts en bot_top/bottom)
                params = [f'%"symbol": "{name}"%']
                sql_extra = ""
                epoch_label = "tots els epochs"
                if not show_all_epochs:
                    cur = con.execute(
                        "SELECT opened_ts FROM epochs WHERE bot_id = ? "
                        "AND closed_ts IS NULL ORDER BY id DESC LIMIT 1",
                        (cfg["id"],),
                    )
                    row = cur.fetchone()
                    if row and row[0]:
                        sql_extra = " AND ts >= ?"
                        params.append(row[0])
                        epoch_label = f"epoch actual (des de {row[0][:16].replace('T',' ')})"

                df = _query_df(
                    con,
                    f"SELECT ts, price, bot_top, bot_bottom, grid_profit "
                    f"FROM state_snapshots WHERE raw_json LIKE ?{sql_extra} "
                    f"ORDER BY ts ASC LIMIT 2000",
                    ["ts", "price", "bot_top", "bot_bottom", "grid_profit"],
                    tuple(params),
                )
                if df.empty:
                    st.info(f"Encara no hi ha snapshots de {name}.")
                    continue

                df["ts"] = pd.to_datetime(df["ts"])
                df = df.sort_values("ts")
                # Filtra files invàlides (algun snapshot vell sense top/bottom)
                df = df[(df["bot_top"] > 0) & (df["bot_bottom"] > 0) & (df["price"] > 0)]
                # Filtra outliers (snapshots corruptes amb valors d'altres assets):
                # exigeix que el rang del snapshot sigui de l'ordre del rang actual del bot
                _s = bot_states.get(name)
                if _s and _s.get("price", 0) > 0:
                    _ref = _s["price"]
                    df = df[
                        df["price"].between(_ref * 0.3, _ref * 3) &
                        df["bot_top"].between(_ref * 0.3, _ref * 3) &
                        df["bot_bottom"].between(_ref * 0.3, _ref * 3)
                    ]
                if df.empty:
                    st.info(f"Snapshots sense dades vàlides per {name}.")
                    continue

                # Calcula EDGE TRIGGER bands (10% del rang per dins de top/bottom)
                df["edge_top"] = df["bot_top"] - (df["bot_top"] - df["bot_bottom"]) * EDGE_TRIGGER_PCT
                df["edge_bot"] = df["bot_bottom"] + (df["bot_top"] - df["bot_bottom"]) * EDGE_TRIGGER_PCT

                base = alt.Chart(df).encode(
                    x=alt.X("ts:T", title=None,
                            axis=alt.Axis(format="%d %b %H:%M", labelAngle=-30))
                )

                # Zona de grid completa (rang del bot) — àrea blava clara
                range_area = base.mark_area(opacity=0.10, color="#5B8FF9").encode(
                    y=alt.Y("bot_bottom:Q", title="Preu (USDT)",
                            scale=alt.Scale(zero=False, nice=True),
                            axis=alt.Axis(format="$,.2f")),
                    y2="bot_top:Q",
                )
                # Edge trigger bands (zones d'alerta vora els límits)
                edge_top_area = base.mark_area(opacity=0.18, color="#FFA500").encode(
                    y="edge_top:Q", y2="bot_top:Q",
                )
                edge_bot_area = base.mark_area(opacity=0.18, color="#FFA500").encode(
                    y="bot_bottom:Q", y2="edge_bot:Q",
                )

                # Línies dels límits del bot
                top_line = base.mark_line(
                    strokeDash=[3, 3], color="#5B8FF9", opacity=0.7, strokeWidth=1
                ).encode(y="bot_top:Q")
                bot_line = base.mark_line(
                    strokeDash=[3, 3], color="#5B8FF9", opacity=0.7, strokeWidth=1
                ).encode(y="bot_bottom:Q")

                # Línia del preu — destacada
                price_line = base.mark_line(color="#FF4757", strokeWidth=2.2).encode(
                    y="price:Q",
                    tooltip=[
                        alt.Tooltip("ts:T", title="Temps", format="%Y-%m-%d %H:%M"),
                        alt.Tooltip("price:Q", title="Preu", format="$,.4f"),
                        alt.Tooltip("bot_top:Q", title="Top", format="$,.4f"),
                        alt.Tooltip("bot_bottom:Q", title="Bottom", format="$,.4f"),
                        alt.Tooltip("grid_profit:Q", title="Grid profit", format=",.4f"),
                    ],
                )

                chart = (
                    (range_area + edge_top_area + edge_bot_area + bot_line + top_line + price_line)
                    .properties(height=300)
                    .resolve_scale(y="shared")
                    .configure_view(strokeWidth=0)
                    .interactive(bind_y=False)
                )
                st.altair_chart(chart, use_container_width=True)

                # Caption amb estadístiques clau
                price_min, price_max = df["price"].min(), df["price"].max()
                top_val, bot_val = df["bot_top"].iloc[-1], df["bot_bottom"].iloc[-1]
                in_range = ((df["price"] >= df["bot_bottom"]) &
                            (df["price"] <= df["bot_top"])).sum()
                pct_in = in_range / len(df) * 100
                st.caption(
                    f"📊 {len(df)} snapshots · {epoch_label} · "
                    f"preu {price_min:.4f}→{price_max:.4f} · "
                    f"rang actual {bot_val:.4f}–{top_val:.4f} · "
                    f"⏱ {pct_in:.0f}% del temps dins del rang · "
                    f"🟠 zones taronja = edge trigger (<10% del límit → trail)"
                )

    with ht2:
        df_dec = _query_df(
            con,
            "SELECT ts, trigger, action, claude_reasoning, new_top, new_bottom, bot_id "
            "FROM decisions ORDER BY ts DESC LIMIT 50",
            ["ts", "trigger", "action", "reasoning", "new_top", "new_bottom", "bot_id"],
        )
        if not df_dec.empty:
            st.dataframe(df_dec, use_container_width=True, hide_index=True)
        else:
            st.info("Cap decisio de trailing registrada encara.")

    with ht3:
        epochs = get_all_epochs()
        if epochs:
            df_ep = pd.DataFrame(epochs)
            # Format friendly
            display_cols = [c for c in [
                "id", "symbol", "bot_id", "opened_ts", "closed_ts",
                "initial_capital_usdt", "cycles_completed",
                "grid_profit_reported", "cost_to_close", "true_net_pnl", "notes"
            ] if c in df_ep.columns]
            st.dataframe(df_ep[display_cols], use_container_width=True, hide_index=True)
            # Resum agregat per symbol
            st.markdown("**Resum agregat per parell:**")
            agg = df_ep.groupby("symbol").agg(
                epochs=("id", "count"),
                cycles=("cycles_completed", "sum"),
                profit=("grid_profit_reported", "sum"),
                net_pnl=("true_net_pnl", "sum"),
            ).reset_index()
            st.dataframe(agg, use_container_width=True, hide_index=True)
        else:
            st.info("Cap epoch registrat. Crea un bot per inicialitzar.")

    with ht4:
        txs = get_all_transactions()
        if txs:
            df_tx = pd.DataFrame(txs)
            st.dataframe(df_tx, use_container_width=True, hide_index=True)
        else:
            st.info("Cap transaccio manual registrada.")

    con.close()
else:
    st.info("BD no inicialitzada.")

st.divider()


# ═══════════════════════════════════════════════════════════════════════
# 6) MANUAL — Deposits / Withdrawals
# ═══════════════════════════════════════════════════════════════════════
with st.expander("➕ Registrar deposit / withdrawal manual", expanded=False):
    mt_d, mt_w = st.tabs(["📥 Deposit", "📤 Withdrawal"])
    with mt_d:
        d_usdt = st.number_input("USDT amount", min_value=0.0, value=0.0, key="d_usdt")
        d_btc = st.number_input("BTC amount", min_value=0.0, value=0.0, key="d_btc")
        d_price = st.number_input("BTC/USDT price at deposit", min_value=0.0, value=float(btc_usdt_price), key="d_price")
        d_notes = st.text_input("Notes", key="d_notes")
        if st.button("Record Deposit", key="btn_deposit", type="primary"):
            log_deposit(d_usdt, d_btc, d_price, d_notes)
            st.success("Deposit registrat")
            _st_orig.rerun()
    with mt_w:
        w_usdt = st.number_input("USDT amount", min_value=0.0, value=0.0, key="w_usdt")
        w_btc = st.number_input("BTC amount", min_value=0.0, value=0.0, key="w_btc")
        w_price = st.number_input("BTC/USDT price at withdrawal", min_value=0.0, value=float(btc_usdt_price), key="w_price")
        w_notes = st.text_input("Notes", key="w_notes")
        if st.button("Record Withdrawal", key="btn_withdraw"):
            log_withdrawal(w_usdt, w_btc, w_price, w_notes)
            st.success("Withdrawal registrat")
            _st_orig.rerun()


# ─── Restaura st al modul + Tab MT5 + Auto-refresh ──────────────────
st = _st_orig

# ═══════════════════════════════════════════════════════════════════════
# TAB MT5 XAUUSD — Daily P&L + cumulatiu des de l'inici de l'estrategia
# ═══════════════════════════════════════════════════════════════════════
import json as _json_mt5
from pathlib import Path as _Path_mt5

_baseline_path = _Path_mt5(__file__).parent / "db" / "mt5_baseline.json"


def _load_mt5_baseline():
    if _baseline_path.exists():
        try:
            return _json_mt5.loads(_baseline_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_mt5_baseline(d):
    _baseline_path.parent.mkdir(parents=True, exist_ok=True)
    _baseline_path.write_text(_json_mt5.dumps(d, indent=2), encoding="utf-8")


with top_tab_mt5:
    st.markdown("## 💎 MT5 XAUUSD Dual Grid")

    if not MT5_OK or mt5_state is None:
        st.error(f"Client MT5 no disponible: {MT5_ERR if not MT5_OK else 'sense estat'}")
    elif not mt5_state.get("available"):
        age = mt5_state.get("last_seen_age_sec")
        age_str = f"{age:.0f}s" if age else "?"
        st.warning(
            f"⚠️ **EA MT5 inactiu o sense heartbeat.** "
            f"({mt5_state.get('error', 'sense dades')}, edat: {age_str})\n\n"
            "Recarrega l'EA `DualGridEA_v1` al gràfic XAUUSD per activar el monitor."
        )
        st.info("📋 Quan estigui actiu, aquí veuràs: profit diari, profit acumulat des de l'inici, "
                "estat de les posicions, anchors, progrés del cicle actual.")
    else:
        m = mt5_state
        cur_balance = float(m["raw"].get("balance", 0))
        cur_equity = float(m["raw"].get("equity", 0))

        # Gestio del baseline: inceptio (mai canvia) + checkpoint diari (00:00 UTC)
        # Trackem EQUITY (no balance) per capturar floating també — Net real
        baseline = _load_mt5_baseline()
        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        baseline_updated = False
        if "inception_equity" not in baseline or baseline.get("inception_equity", 0) <= 0:
            baseline["inception_equity"] = cur_equity
            baseline["inception_balance"] = cur_balance  # backup
            baseline["inception_date"] = datetime.now(timezone.utc).isoformat()
            baseline_updated = True
        if baseline.get("midnight_date") != today_utc:
            baseline["midnight_equity"] = cur_equity
            baseline["midnight_balance"] = cur_balance  # backup
            baseline["midnight_date"] = today_utc
            baseline_updated = True
        if baseline_updated:
            _save_mt5_baseline(baseline)

        inception_eq = float(baseline.get("inception_equity", baseline.get("inception_balance", cur_equity)))
        midnight_eq = float(baseline.get("midnight_equity", baseline.get("midnight_balance", cur_equity)))

        # NET real = realitzat + flotant = canvi total d'equity
        cum_profit_net = cur_equity - inception_eq
        cum_profit_pct = (cum_profit_net / inception_eq * 100) if inception_eq else 0
        daily_profit_net = cur_equity - midnight_eq
        daily_profit_pct = (daily_profit_net / midnight_eq * 100) if midnight_eq else 0

        # Per a referencia: només realitzat
        realized_today = cur_balance - float(baseline.get("midnight_balance", cur_balance))
        realized_cum = cur_balance - float(baseline.get("inception_balance", cur_balance))
        floating = cur_equity - cur_balance

        # KPIs principals MT5 (color automatic: verd si +, vermell si -)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric(
            "💰 Profit DIARI (Net)",
            f"${daily_profit_net:+,.2f}",
            delta=f"{daily_profit_pct:+.3f}%",
            delta_color="normal",  # verd si +, vermell si -
            help=(f"NET = realitzat + flotant. Des de 00:00 UTC d'avui ({today_utc}).\n\n"
                  f"Realitzat avui: ${realized_today:+,.2f}\n"
                  f"Flotant ara: ${floating:+,.2f}"),
        )
        m2.metric(
            "📈 Profit ACUMULAT (Net)",
            f"${cum_profit_net:+,.2f}",
            delta=f"{cum_profit_pct:+.3f}%",
            delta_color="normal",
            help=(f"NET = realitzat + flotant. Des de l'inici de l'estratègia ({baseline['inception_date'][:10]}). "
                  f"Baseline equity: ${inception_eq:,.2f}\n\n"
                  f"Realitzat acum: ${realized_cum:+,.2f}\n"
                  f"Flotant ara: ${floating:+,.2f}"),
        )
        m3.metric(
            "🏦 Balance actual",
            f"${cur_balance:,.2f}",
            delta=f"Equity ${cur_equity:,.2f}",
            delta_color="off",
        )
        m4.metric(
            "📊 Flotant",
            f"${floating:+,.2f}",
            delta=f"{(floating/cur_equity*100) if cur_equity else 0:+.2f}% eq",
            delta_color="normal",  # verd si +, vermell si -
        )

        st.divider()

        # Cicle actual
        st.markdown("### 🔄 Cicle actual")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Realitzat cicle", f"${m['raw'].get('realized_cycle', 0):+,.2f}")
        c2.metric("Net cicle", f"${m['raw'].get('net_cycle', 0):+,.2f}")
        c3.metric("Target", f"${m['raw'].get('target_usd', 0):,.2f}",
                  delta=f"{m['raw'].get('target_progress', 0):.1f}% complet")
        # Progress bar al target
        prog = max(0.0, min(1.0, float(m["raw"].get("target_progress", 0)) / 100.0))
        c4.progress(prog, text=f"{prog*100:.1f}% del cicle")

        # Posicions i grid
        st.markdown("### 📋 Posicions i Grid")
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Posicions obertes", f"{m['raw'].get('positions_total', 0)}",
                  delta=f"{m['raw'].get('buys_count', 0)} BUYs / {m['raw'].get('sells_count', 0)} SELLs")
        p2.metric("Pendents", f"{m['raw'].get('pending_total', 0)}")
        p3.metric("Spacing", f"${m['raw'].get('spacing', 0):.2f}",
                  delta=f"TP ${m['raw'].get('tp_usd', 0):.2f}")
        p4.metric("Preu actual", f"${m.get('price', 0):,.2f}",
                  delta=f"Anchor grid ${m['raw'].get('grid_anchor_price', 0):,.2f}")

        # Anchors
        st.markdown("### ⚓ Anchors protegits (no tanquen per TP)")
        a1, a2 = st.columns(2)
        anc_buy_e = m["raw"].get("anchor_buy_entry", 0)
        anc_buy_f = m["raw"].get("anchor_buy_floating", 0)
        anc_sell_e = m["raw"].get("anchor_sell_entry", 0)
        anc_sell_f = m["raw"].get("anchor_sell_floating", 0)
        if anc_buy_e > 0:
            a1.metric(f"Anchor BUY @ ${anc_buy_e:,.2f}",
                      f"${anc_buy_f:+,.2f}",
                      delta_color="off")
        else:
            a1.info("Sense Anchor BUY")
        if anc_sell_e > 0:
            a2.metric(f"Anchor SELL @ ${anc_sell_e:,.2f}",
                      f"${anc_sell_f:+,.2f}",
                      delta_color="off")
        else:
            a2.info("Sense Anchor SELL")

        # Estat tecnic
        st.divider()
        st.markdown("### 🛠️ Estat tecnic")
        cb = m["raw"].get("is_circuit_breaker", False)
        e1, e2, e3 = st.columns(3)
        e1.metric("Circuit Breaker", "🔴 ACTIVAT" if cb else "🟢 OK",
                  delta_color="inverse" if cb else "off")
        e2.metric("Global SL limit", f"-{m['raw'].get('global_sl_pct', 0):.1f}% equity")
        e3.metric("Heartbeat", f"fa {m.get('last_seen_age_sec', 0):.0f}s",
                  delta="actiu" if m.get('last_seen_age_sec', 999) < 30 else "stale",
                  delta_color="off" if m.get('last_seen_age_sec', 999) < 30 else "inverse")

        with st.expander("📦 Raw heartbeat JSON"):
            st.json(m["raw"])
        st.caption(f"Baseline guardat a: `{_baseline_path}` · "
                   f"Reset baseline: esborra el fitxer i recarrega.")


# ─── Auto-refresh DESACTIVAT ─────────────────────────────────────────
# La pàgina és estàtica: només es refresca quan l'usuari prem "🔄 Refresh ara"
# o un botó d'acció (Desa budget, Deposit, etc.). Així no apareix el "Running..."
# constantment ni es paguen 4 crides API cada 60s.
