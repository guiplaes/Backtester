"""
AUDIT LOGGER — Sistema centralitzat de seguiment del trading system.
Escriu a audit.jsonl (1 linia JSON per event, cronologic).

Categories:
  TG_MSG      - Missatge Telegram rebut (raw)
  TG_SIGNAL   - Senyal detectada del missatge (BUY/SELL/CLOSE/SL_ENTRY)
  SIGNAL_ON   - Senyal activada (signal_mgr.activate)
  SIGNAL_OFF  - Senyal desactivada (signal_mgr.deactivate)
  ORDER_SENT  - Ordre escrita al JSON per l'EA (MARKET, CLOSE_ALL, etc.)
  EA_EXEC     - EA ha processat l'ordre (PROCESSED)
  EA_AVG      - EA ha obert averaging (detectat via events)
  EA_STRUCT   - EA structural stop / break-even
  POS_CHANGE  - Canvi de posicions (obertura/tancament detectat)
  HEARTBEAT   - Heartbeat enviat (cada 30s nomes, no cada 5s)
  MANUAL      - Accio manual (BUY/SELL buttons)
  STATE       - Canvi d'estat (TANCANT, BE, etc.)
  ERROR       - Error/excepcio
  SYSTEM      - Inici/aturada/restart del sistema

Cada linia: {"ts": "2026-04-06 10:15:33.456", "cat": "TG_MSG", "msg": "...", ...}

Us: from audit_logger import audit
    audit("TG_MSG", "Missatge rebut", channel="TT", text="SELL XAUUSD...")
"""

import json
import os
import time
from datetime import datetime
import threading

_lock = threading.Lock()
_LOG_DIR = os.path.dirname(os.path.abspath(__file__))
_LOG_FILE = os.path.join(_LOG_DIR, 'audit.jsonl')

# Throttle heartbeat logs (max 1 every 30s)
_last_hb_log = 0

def audit(category, message, **kwargs):
    """Escriu una linia d'audit al fitxer JSONL."""
    global _last_hb_log

    # Throttle heartbeats
    if category == "HEARTBEAT":
        now = time.time()
        if now - _last_hb_log < 30:
            return
        _last_hb_log = now

    entry = {
        "ts": datetime.now().strftime('%Y-%m-%d %H:%M:%S.') + f"{datetime.now().microsecond // 1000:03d}",
        "cat": category,
        "msg": message,
    }
    entry.update(kwargs)

    try:
        with _lock:
            with open(_LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception:
        pass  # Never crash the system for logging


def audit_separator(label=""):
    """Escriu un separador visual al log (inici de sessio, etc.)."""
    entry = {
        "ts": datetime.now().strftime('%Y-%m-%d %H:%M:%S.') + f"{datetime.now().microsecond // 1000:03d}",
        "cat": "=====",
        "msg": f"===== {label} =====",
    }
    try:
        with _lock:
            with open(_LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception:
        pass
