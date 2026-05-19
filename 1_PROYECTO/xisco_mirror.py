"""
xisco_mirror.py — Copy-trader del canal "Senales Xisco Analisis"

Arquitectura:
  TG canal (-1003711770973) → Telethon → parser regex → orders → MT5 EA

Comandes que escriu a xisco_orders.json (consumit per XiscoMirrorEA_v1_MT5):
  - MARKET (BUY/SELL)  -> sobre APERTURA
  - MODIFY_SL          -> sobre MODIFICACION amb SL
  - MODIFY_TP          -> sobre MODIFICACION amb TP
  - CLOSE_TICKET       -> sobre CIERRE

L'EA escriu xisco_positions.json amb totes les posicions (filtrades per magic 88888)
i nosaltres mapegem xisco_ticket -> local_ticket via comment "XISCO_<xisco_ticket>".

Sizing:  lot_nostre = lot_xisco * (balance_nostre / xisco_balance_ref)
         balance_nostre llegit de brain_ea_heartbeat.json (real-time)
         xisco_balance_ref es manté com a snapshot + P&L tancat acumulat

Cold start: ignorem qualsevol missatge previ al startup_ts. Nomes copiem els nous.
NO posem stop-loss propi. NO posem cap màxim de lots. Es còpia 100% pura.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Telethon
try:
    import yaml
    from telethon import TelegramClient, events
except ImportError as e:
    print(f"[xisco_mirror] missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

# ─── PATHS ─────────────────────────────────────────────────────────────
BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.yaml"
SESSION_NAME = "xisco_session"  # session NOVA dedicada — no compartim amb el brain
LOG_PATH = BASE / "logs" / "xisco_mirror.log"
TICKET_MAP_PATH = BASE / "xisco_ticket_map.json"
SEEN_PATH = BASE / "xisco_seen.json"

# MT5 Common Files (bridge amb l'EA)
MT5_COMMON = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files")
ORDERS_FILE = MT5_COMMON / "xisco_orders.json"
POSITIONS_FILE = MT5_COMMON / "xisco_positions.json"

# El nostre XiscoMirrorEA escriu balance/equity a xisco_positions.json — unica font

# ─── CONFIG ───────────────────────────────────────────────────────────
CHANNEL_ID = -1003711770973  # "Senales Xisco Analisis"
EA_MAGIC = 88888  # magic dedicat al mirror Xisco (Brain=99999, DualGrid=77777)
LOT_MIN = 0.01  # broker minimum
LOT_STEP = 0.01

# Sizing reference: balance Xisco aproximat actual (USC)
# S'actualitzarà dinàmicament sumant P&L tancat des d'aquest valor.
XISCO_BALANCE_REF_USC = 229_792.42  # snapshot 10/05 + closed P&L fins 13/05 07:17

# ─── LOGGING ──────────────────────────────────────────────────────────
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("xisco_mirror")

# ─── STATE ────────────────────────────────────────────────────────────
STARTUP_TS = time.time()  # cold start: ignorem tot el d'abans
log.info(f"Startup ts: {STARTUP_TS} ({datetime.fromtimestamp(STARTUP_TS, timezone.utc).isoformat()})")

# Ticket map: xisco_ticket (str) -> {local_ticket, side, lots, opened_ts, status}
ticket_map: dict[str, dict] = {}
# Pending opens: xisco_ticket esperant l'echo de l'EA per saber local_ticket
pending_opens: dict[str, dict] = {}
# Pending modifications/closes que han arribat ABANS que l'echo
deferred_actions: list[dict] = []
# Dedup d'IDs de missatge processats
seen_msgs: dict[str, float] = {}

# Comptadors per a logs/diagnòstic
counters = {
    "aperturas_received": 0,
    "aperturas_mirrored": 0,
    "aperturas_skipped_pre_startup": 0,
    "aperturas_skipped_dup": 0,
    "modificacions_received": 0,
    "modificacions_mirrored": 0,
    "modificacions_skipped_no_map": 0,
    "cierres_received": 0,
    "cierres_mirrored": 0,
    "cierres_skipped_no_map": 0,
    "parse_failures": 0,
}

# ─── PERSISTENCE ──────────────────────────────────────────────────────
def load_ticket_map():
    global ticket_map
    if TICKET_MAP_PATH.exists():
        try:
            ticket_map = json.loads(TICKET_MAP_PATH.read_text(encoding="utf-8"))
            log.info(f"Loaded ticket_map: {len(ticket_map)} entries")
        except Exception as e:
            log.warning(f"Failed to load ticket_map ({e}), starting empty")
            ticket_map = {}

def save_ticket_map():
    try:
        TICKET_MAP_PATH.write_text(json.dumps(ticket_map, indent=2), encoding="utf-8")
    except Exception as e:
        log.error(f"Failed to save ticket_map: {e}")

def load_seen():
    global seen_msgs
    if SEEN_PATH.exists():
        try:
            seen_msgs = json.loads(SEEN_PATH.read_text(encoding="utf-8"))
        except Exception:
            seen_msgs = {}

def save_seen():
    # Prune > 24h
    now = time.time()
    pruned = {k: v for k, v in seen_msgs.items() if now - v < 86400}
    try:
        SEEN_PATH.write_text(json.dumps(pruned), encoding="utf-8")
    except Exception:
        pass

# ─── BALANCE / SIZING ─────────────────────────────────────────────────
def read_our_balance_usc() -> float | None:
    """Llegeix el nostre balance del XiscoMirrorEA via xisco_positions.json.
    Si l'EA no esta atacat o el fitxer es stale, retorna None."""
    try:
        if not POSITIONS_FILE.exists():
            return None
        # Stale check: si l'EA fa massa que no escriu, balance no es fiable
        age = time.time() - POSITIONS_FILE.stat().st_mtime
        if age > 120:  # mes de 2 min sense actualitzar = EA aturat
            log.warning(f"xisco_positions.json stale ({int(age)}s) — EA no escriu")
            return None
        d = json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
        acc = d.get("account", {})
        bal = float(acc.get("balance", 0))
        return bal if bal > 0 else None
    except Exception as e:
        log.warning(f"Can't read our balance: {e}")
        return None

def compute_lot(lot_xisco: float) -> float | None:
    """lot_nostre = lot_xisco × (balance_nostre / xisco_balance_ref)
    arrodonim al pas del broker (0.01).
    Retorna None si no podem llegir balance (EA no atacat) — l'apertura
    es SKIPSEJA. Millor saltar un trade que obrir-lo mal escalat."""
    our_bal = read_our_balance_usc()
    if not our_bal:
        log.error("Can't read balance — XiscoMirrorEA no atacat al xart? SKIP apertura.")
        return None
    ratio = our_bal / XISCO_BALANCE_REF_USC
    raw = lot_xisco * ratio
    lot = max(LOT_MIN, round(raw / LOT_STEP) * LOT_STEP)
    lot = round(lot, 2)
    log.info(f"  Sizing: xisco={lot_xisco} x ({our_bal:.0f}/{XISCO_BALANCE_REF_USC:.0f}={ratio:.4f}) = {raw:.4f} -> {lot}")
    return lot

# ─── PARSER REGEX ─────────────────────────────────────────────────────
# Format dels missatges (vist al canal):
#   🟢 **APERTURA** | 🕐 2026.05.06 10:12:43 | 📊 XAUUSD-VIPc  |  SELL  |  0.20 lotes |
#       💲 Precio: `4661.05000` | 🎯 TP: `4655.00` | 🎫 Ticket: `382014623`
#   ✏️ **MODIFICACIÓN** | 🕐 ... | XAUUSD-VIPc | SELL | 🔻 SL: `4683.24` | 🎯 TP: sin límite | 🎫 Ticket: `382633305`
#   🔴 **CIERRE** | 🕐 ... | XAUUSD-VIPc | BUY | 0.20 lotes | Precio: `4694.31` | 💰 Resultado: `+21.00 USD` | 🎫 Ticket: `382319126`

RE_APERTURA = re.compile(
    r"APERTURA.*?(?P<symbol>XAUUSD-[A-Za-z]+).*?(?P<side>BUY|SELL).*?"
    r"(?P<lots>[\d.]+)\s*lotes.*?Precio:\s*`(?P<price>[\d.]+)`.*?"
    r"Ticket:\s*`(?P<ticket>\d+)`",
    re.DOTALL,
)
RE_APERTURA_TP = re.compile(r"TP:\s*`(?P<tp>[\d.]+)`")
RE_APERTURA_SL = re.compile(r"SL:\s*`(?P<sl>[\d.]+)`")

RE_MODIFICACION = re.compile(
    r"MODIFICACI[\w]*N.*?(?P<symbol>XAUUSD-[A-Za-z]+).*?(?P<side>BUY|SELL).*?"
    r"Ticket:\s*`(?P<ticket>\d+)`",
    re.DOTALL,
)
RE_MOD_SL = re.compile(r"SL:\s*`(?P<sl>[\d.]+)`")
RE_MOD_TP = re.compile(r"TP:\s*`(?P<tp>[\d.]+)`")
RE_MOD_SL_NONE = re.compile(r"SL:\s*sin\s*stop", re.IGNORECASE)
RE_MOD_TP_NONE = re.compile(r"TP:\s*sin\s*l", re.IGNORECASE)

RE_CIERRE = re.compile(
    r"CIERRE.*?(?P<symbol>XAUUSD-[A-Za-z]+).*?"
    r"Ticket:\s*`(?P<ticket>\d+)`",
    re.DOTALL,
)

# Detectem si és NO_POSITIONS / MT5 Arrancado per ignorar-los
def is_lifecycle_message(text: str) -> bool:
    return ("NO_POSITIONS" in text) or ("MT5 Arrancado" in text)

# ─── ORDER WRITER (cua FIFO a xisco_orders.json) ──────────────────────
def write_order(order: dict):
    """Append order a xisco_orders.json (l'EA polleja cada 500ms i el processa)."""
    payload = {
        "ts": int(time.time()),
        "orders": [order],
    }
    try:
        ORDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        ORDERS_FILE.write_text(json.dumps(payload), encoding="utf-8")
        log.info(f"  → ORDER written: {order}")
    except Exception as e:
        log.error(f"  → ORDER write FAILED: {e}")

# ─── ECHO READER (xisco_positions.json) ───────────────────────────────
def update_local_tickets_from_echo():
    """Llegeix posicions actuals i mapeja comment 'XISCO_xxxx' → local_ticket."""
    if not POSITIONS_FILE.exists():
        return
    try:
        data = json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
        positions = data.get("positions", [])
    except Exception as e:
        log.warning(f"Can't read positions echo: {e}")
        return

    found = 0
    for pos in positions:
        comment = pos.get("comment", "")
        # comments arriba amb prefix "MIRROR_" + tag passat al MARKET ordre
        # ex: comment = "MIRROR_XISCO_198650123"  (EA fa CommentTag + "_" + comment)
        m = re.search(r"XISCO_(\d+)", comment)
        if not m:
            continue
        xisco_ticket = m.group(1)
        local_ticket = pos.get("ticket")
        if xisco_ticket in ticket_map and ticket_map[xisco_ticket].get("local_ticket") is None:
            ticket_map[xisco_ticket]["local_ticket"] = local_ticket
            ticket_map[xisco_ticket]["status"] = "OPEN"
            log.info(f"  MAP UPDATED: xisco={xisco_ticket} → local={local_ticket}")
            found += 1
            # Si hi havia accions pendents per aquest ticket, processem-les
            for action in list(deferred_actions):
                if action["xisco_ticket"] == xisco_ticket:
                    apply_deferred_action(action)
                    deferred_actions.remove(action)
    if found:
        save_ticket_map()

def apply_deferred_action(action: dict):
    local = ticket_map[action["xisco_ticket"]].get("local_ticket")
    if not local:
        log.warning(f"Deferred action but no local_ticket yet: {action}")
        return
    if action["kind"] == "MODIFY_SL":
        write_order({"action": "MODIFY_SL", "ticket": local, "sl": action["sl"]})
    elif action["kind"] == "MODIFY_TP":
        write_order({"action": "MODIFY_TP", "ticket": local, "tp": action["tp"]})
    elif action["kind"] == "CLOSE":
        write_order({"action": "CLOSE_TICKET", "ticket": local})

# ─── HANDLERS PER TIPUS DE MISSATGE ───────────────────────────────────
def handle_apertura(text: str, msg_ts: float):
    counters["aperturas_received"] += 1
    m = RE_APERTURA.search(text)
    if not m:
        log.warning(f"APERTURA parse failed: {text[:200]}")
        counters["parse_failures"] += 1
        return
    side = m.group("side")
    lots = float(m.group("lots"))
    price = float(m.group("price"))
    ticket = m.group("ticket")
    sym = m.group("symbol")

    # COLD START: ignora aperturas anteriors al startup
    if msg_ts < STARTUP_TS:
        log.info(f"  APERTURA {ticket} skipped (pre-startup, ts={msg_ts})")
        counters["aperturas_skipped_pre_startup"] += 1
        return

    # Dedup per ticket
    if ticket in ticket_map:
        log.info(f"  APERTURA {ticket} skipped (already mapped)")
        counters["aperturas_skipped_dup"] += 1
        return

    log.info(f"APERTURA xisco_ticket={ticket} {side} {lots}@{price} ({sym})")
    our_lot = compute_lot(lots)
    if our_lot is None:
        log.error(f"  APERTURA {ticket} SKIPPED — sense balance EA. Ataca XiscoMirrorEA al xart.")
        return

    # Registrem al map (sense local_ticket fins que l'EA confirmi via echo)
    ticket_map[ticket] = {
        "local_ticket": None,
        "side": side,
        "xisco_lots": lots,
        "our_lots": our_lot,
        "xisco_price": price,
        "opened_ts": msg_ts,
        "status": "PENDING_OPEN",
    }
    save_ticket_map()
    write_order({
        "action": "MARKET",
        "type": side,
        "lot": our_lot,
        "sl": 0.0,
        "tp": 0.0,
        "comment": f"XISCO_{ticket}",
    })
    counters["aperturas_mirrored"] += 1

def handle_modificacion(text: str, msg_ts: float):
    counters["modificacions_received"] += 1
    m = RE_MODIFICACION.search(text)
    if not m:
        counters["parse_failures"] += 1
        return
    ticket = m.group("ticket")
    if ticket not in ticket_map:
        log.info(f"  MODIFICACION {ticket} skipped (no mapping — pre-startup ticket)")
        counters["modificacions_skipped_no_map"] += 1
        return

    sl_match = RE_MOD_SL.search(text)
    tp_match = RE_MOD_TP.search(text)
    sl_none = bool(RE_MOD_SL_NONE.search(text))
    tp_none = bool(RE_MOD_TP_NONE.search(text))

    local_ticket = ticket_map[ticket].get("local_ticket")

    # SL
    if sl_match:
        sl = float(sl_match.group("sl"))
        log.info(f"MODIFICACION xisco={ticket} SL={sl}")
        if local_ticket:
            write_order({"action": "MODIFY_SL", "ticket": local_ticket, "sl": sl})
            counters["modificacions_mirrored"] += 1
        else:
            deferred_actions.append({"kind": "MODIFY_SL", "xisco_ticket": ticket, "sl": sl})
            log.info(f"  deferred (no local yet)")
    elif sl_none:
        # SL eliminat — set SL=0 (broker entén 'no SL')
        log.info(f"MODIFICACION xisco={ticket} SL=NONE")
        if local_ticket:
            write_order({"action": "MODIFY_SL", "ticket": local_ticket, "sl": 0.0})
            counters["modificacions_mirrored"] += 1

    # TP
    if tp_match:
        tp = float(tp_match.group("tp"))
        log.info(f"MODIFICACION xisco={ticket} TP={tp}")
        if local_ticket:
            write_order({"action": "MODIFY_TP", "ticket": local_ticket, "tp": tp})
        else:
            deferred_actions.append({"kind": "MODIFY_TP", "xisco_ticket": ticket, "tp": tp})
    elif tp_none:
        log.info(f"MODIFICACION xisco={ticket} TP=NONE")
        if local_ticket:
            write_order({"action": "MODIFY_TP", "ticket": local_ticket, "tp": 0.0})

def handle_cierre(text: str, msg_ts: float):
    counters["cierres_received"] += 1
    m = RE_CIERRE.search(text)
    if not m:
        counters["parse_failures"] += 1
        return
    ticket = m.group("ticket")
    if ticket not in ticket_map:
        log.info(f"  CIERRE {ticket} skipped (no mapping — pre-startup ticket)")
        counters["cierres_skipped_no_map"] += 1
        return
    entry = ticket_map[ticket]
    if entry.get("status") == "CLOSED":
        log.info(f"  CIERRE {ticket} skipped (already closed)")
        return
    local_ticket = entry.get("local_ticket")
    log.info(f"CIERRE xisco={ticket} → local={local_ticket}")
    if local_ticket:
        write_order({"action": "CLOSE_TICKET", "ticket": local_ticket})
        entry["status"] = "CLOSED"
        entry["closed_ts"] = msg_ts
        save_ticket_map()
        counters["cierres_mirrored"] += 1
    else:
        # Encara no s'havia confirmat l'open
        deferred_actions.append({"kind": "CLOSE", "xisco_ticket": ticket})
        log.info(f"  deferred (no local yet)")

# ─── MAIN LOOP ────────────────────────────────────────────────────────
async def main():
    load_ticket_map()
    load_seen()

    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    tg = cfg.get("telegram", {})
    api_id = tg.get("api_id")
    api_hash = tg.get("api_hash")
    if not api_id or not api_hash:
        log.error("Missing telegram api_id/api_hash in config.yaml")
        return

    # Session dedicada — no compartim per evitar conflictes amb el brain listener
    # NOTA: Primer run cal autoritzar (codi via Telegram). Si ja existeix la session
    # del brain, copia-la a xisco_session.session per evitar second login.
    session_path = str(BASE / SESSION_NAME)
    if not Path(session_path + ".session").exists():
        # fallback a brain_session si existeix (només si no podem fer login interactiu)
        if (BASE / "brain_session.session").exists():
            log.warning("xisco_session not found, REUSING brain_session.session "
                       "(consider copying to xisco_session.session for isolation)")
            session_path = str(BASE / "brain_session")
        elif (BASE / "session.session").exists():
            log.warning("xisco_session not found, REUSING session.session")
            session_path = str(BASE / "session")

    client = TelegramClient(session_path, api_id, api_hash)
    await client.start()

    me = await client.get_me()
    log.info(f"Connected as: {me.first_name} ({me.id})")

    # Resol l'entitat
    entity = await client.get_entity(CHANNEL_ID)
    log.info(f"Channel resolved: {getattr(entity, 'title', '?')} (id={entity.id})")

    @client.on(events.NewMessage(chats=[entity]))
    async def on_msg(event):
        try:
            msg_id = str(event.message.id)
            seen_key = f"{event.chat_id}:{msg_id}"
            if seen_key in seen_msgs:
                return
            seen_msgs[seen_key] = time.time()

            text = event.message.text or ""
            if not text.strip():
                return

            # Timestamp del missatge (UTC)
            msg_ts = event.message.date.timestamp() if event.message.date else time.time()

            if is_lifecycle_message(text):
                log.debug(f"Lifecycle msg ignored: {text[:80]}")
                return

            if "APERTURA" in text:
                handle_apertura(text, msg_ts)
            elif "MODIFICACI" in text:  # acceptem MODIFICACION / MODIFICACIÓN
                handle_modificacion(text, msg_ts)
            elif "CIERRE" in text:
                handle_cierre(text, msg_ts)
            else:
                log.debug(f"Unknown msg type: {text[:100]}")
        except Exception as e:
            log.exception(f"on_msg error: {e}")

    # Loop de manteniment: cada 5s actualitzem mapping i guardem state
    async def maintenance():
        last_stats = time.time()
        while True:
            try:
                update_local_tickets_from_echo()
                save_seen()
                if time.time() - last_stats > 300:  # cada 5 min stats
                    log.info(f"STATS: {counters} | map_size={len(ticket_map)} | pending_defer={len(deferred_actions)}")
                    last_stats = time.time()
            except Exception as e:
                log.exception(f"maintenance error: {e}")
            await asyncio.sleep(5)

    asyncio.create_task(maintenance())

    log.info("xisco_mirror RUNNING — Ctrl+C per aturar")
    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Stopped by user")
    except Exception as e:
        log.exception(f"Fatal: {e}")
        sys.exit(1)
