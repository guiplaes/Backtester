"""Notificador Telegram pel Grid Manager.

Envia missatges al bot d'alertes definit a 1_PROYECTO/config.yaml (alert_bot).
Sense crash si TG no respon — mai bloqueja el monitor.

Anti-spam: dedup per `(category, key)` durant ANTI_SPAM_SECONDS.
   Ex: si el mateix bot triggereja 5 vegades en 10 min, només 1 alert.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

import yaml
import requests

# ── PATHS / CONFIG ─────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
CONFIG_YAML = BASE_DIR.parent / "1_PROYECTO" / "config.yaml"
SEEN_PATH = BASE_DIR / "logs" / ".notifier_seen.json"

# Anti-spam: mateix event amb mateixa key no es repeteix durant N segons
ANTI_SPAM_SECONDS = 600  # 10 min

log = logging.getLogger("notifier")

# Cache local del config (no rellegim cada call)
_CFG_CACHE: Optional[dict] = None


def _load_config() -> dict:
    global _CFG_CACHE
    if _CFG_CACHE is not None:
        return _CFG_CACHE
    try:
        with open(CONFIG_YAML, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        _CFG_CACHE = data.get("alert_bot", {})
        return _CFG_CACHE
    except Exception as e:
        log.warning(f"Cannot load alert_bot config: {e}")
        _CFG_CACHE = {}
        return _CFG_CACHE


def _load_seen() -> dict:
    if not SEEN_PATH.exists():
        return {}
    try:
        return json.loads(SEEN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_seen(seen: dict):
    try:
        SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        SEEN_PATH.write_text(json.dumps(seen), encoding="utf-8")
    except Exception:
        pass


def _should_send(category: str, key: str) -> bool:
    """Retorna True si encara no hem enviat aquesta alerta dins ANTI_SPAM_SECONDS."""
    seen = _load_seen()
    seen_key = f"{category}:{key}"
    now = time.time()
    last = seen.get(seen_key, 0)
    if now - last < ANTI_SPAM_SECONDS:
        return False
    seen[seen_key] = now
    # Prune entries massa velles
    seen = {k: v for k, v in seen.items() if now - v < ANTI_SPAM_SECONDS * 3}
    _save_seen(seen)
    return True


def notify(title: str, body: str = "", category: str = "general",
           key: str = "", urgent: bool = False) -> bool:
    """Envia un missatge al alert_bot de Telegram.

    Args:
        title: capçalera del missatge (apareix en negreta)
        body:  cos del missatge (opcional, sota el title)
        category: per agrupar tipus d'alerta (anti-spam)
        key:   identificador únic dins la categoria (ex: bot_name)
        urgent: si True, ignora anti-spam

    Returns True si s'ha enviat, False si error o blocat per spam.
    """
    if not urgent and key and not _should_send(category, key):
        log.debug(f"Notify suppressed (anti-spam): {category}:{key}")
        return False

    cfg = _load_config()
    token = cfg.get("token")
    chat_id = cfg.get("chat_id")
    if not token or not chat_id:
        log.warning("alert_bot not configured, skipping notify")
        return False

    text = f"*{title}*"
    if body:
        text += f"\n{body}"

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_notification": False,
            },
            timeout=8,
        )
        if r.status_code == 200:
            log.info(f"Notify OK: {title[:50]}")
            return True
        log.warning(f"Notify HTTP {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        log.warning(f"Notify exception: {e}")
        return False


# Convenience wrappers ──────────────────────────────────────────────
def notify_trigger(bot_name: str, reason: str, price: float,
                   new_bottom: float, new_top: float, ok: bool) -> bool:
    """Alerta quan un bot es recoloca."""
    emoji = "✅" if ok else "❌"
    title = f"{emoji} Grid recolocat: {bot_name}"
    body = (
        f"Motiu: `{reason}`\n"
        f"Preu actual: `{price:.4g}`\n"
        f"Nou rang: `{new_bottom:.4g}` — `{new_top:.4g}`\n"
        f"Resultat: {'OK' if ok else 'FAIL'}"
    )
    return notify(title, body, category="grid_trigger", key=bot_name)


def notify_rebalance(actions: list) -> bool:
    """Alerta quan el rebalancer executa accions."""
    if not actions:
        return False
    title = f"🔄 Rebalanceig: {len(actions)} accions"
    lines = []
    for a in actions:
        sources = ", ".join(f"{s['from']}:${s['amount']:.0f}" for s in a.get("sources", []))
        lines.append(
            f"• {a['target_bot']} +${a['amount_usdt']:.0f}  "
            f"(dev {a['deviation_pct']:+.1f}%)\n  from [{sources}]"
        )
    body = "\n".join(lines)
    # Anti-spam per signatura del conjunt
    sig = "|".join(sorted(f"{a['target_bot']}:{a['amount_usdt']:.0f}" for a in actions))
    return notify(title, body, category="rebalance", key=sig)


def notify_error(component: str, error: str) -> bool:
    """Alerta de fallida."""
    return notify(f"⚠️ Error {component}", f"```\n{error[:500]}\n```",
                  category="error", key=component[:30])
