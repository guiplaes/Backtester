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


# ── Vault DCA System: batch mode (1 TG per relocació) ──────────────
# Durant una relocació, en lloc d'enviar 5-6 TGs (cancel, add base, add usdt,
# fund p2, fund p4, create), els collectem en una "transacció" i enviem
# 1 resum final. Implementat amb contextvars (thread-safe, process-safe).
import contextlib
import contextvars
import time as _time

_vault_batch: contextvars.ContextVar = contextvars.ContextVar("vault_batch", default=None)


@contextlib.contextmanager
def vault_batch(title: str, key: str | None = None):
    """Context manager: collect vault events i emet UN sol TG al sortir.

    Ús:
        with vault_batch("Relocació PAXG_USDT", key="reloc:PAXG"):
            add_base(...)   # no envia TG
            add_usdt(...)   # no envia TG
            ...
        # ← Aquí s'envia el resum agregat
    """
    batch = {"title": title, "key": key or title[:30],
             "events": [], "started_at": _time.time(),
             "errors": [], "extra_notes": []}
    token = _vault_batch.set(batch)
    try:
        yield batch
    except Exception as e:
        batch["errors"].append(str(e)[:300])
        raise
    finally:
        _vault_batch.reset(token)
        _send_batch_summary(batch)


def _batch_active() -> dict | None:
    """Retorna el batch actiu si estem dins d'un with vault_batch(), o None."""
    return _vault_batch.get()


def _send_batch_summary(batch: dict) -> None:
    """Construeix i envia el TG resum d'un batch de vault events."""
    elapsed = _time.time() - batch["started_at"]
    n_events = len(batch["events"])
    has_errors = bool(batch["errors"])

    if n_events == 0 and not has_errors:
        return  # res a notificar

    def _esc(s):
        return str(s).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")

    emoji_overall = "❌" if has_errors else ("✅" if n_events > 0 else "ℹ️")
    title = f"{emoji_overall} {_esc(batch['title'])}"

    lines = []
    # Agrupar events per tipus per a ordre lògic al resum
    order = ["vault_add_base", "vault_add_usdt", "vault_remove_base", "vault_remove_usdt",
             "fund_p1_vault_profit", "fund_p2_usdt_reserve",
             "fund_p3_vault_loss", "fund_p4_own_asset", "profit_harvest"]
    sorted_events = sorted(batch["events"],
                           key=lambda e: order.index(e["event_type"]) if e["event_type"] in order else 99)

    for ev in sorted_events:
        emoji = {
            "vault_add_base": "📥", "vault_add_usdt": "💰",
            "vault_remove_base": "📤", "vault_remove_usdt": "💸",
            "fund_p1_vault_profit": "✅", "fund_p2_usdt_reserve": "🏦",
            "fund_p3_vault_loss": "⚠️", "fund_p4_own_asset": "🚨",
            "profit_harvest": "🌾",
        }.get(ev["event_type"], "•")
        short = ev["event_type"].replace("vault_", "").replace("fund_", "")
        direction = "+" if ev["qty_delta"] >= 0 else "−"
        asset = _esc(ev["asset"])
        if ev["asset"] == "USDT":
            lines.append(f"  {emoji} {short}: {direction}${abs(ev['qty_delta']):,.2f} → reserva *${ev['qty_after']:,.2f}*")
        else:
            delta_str = f"{direction}{abs(ev['qty_delta']):,.6f}"
            avg = (ev["cost_after"] / ev["qty_after"]) if ev["qty_after"] > 0 else None
            avg_str = f"avg ${avg:,.2f}" if avg else "buit"
            usd_delta = f"${abs(ev['cost_delta_usdt']):,.2f}"
            lines.append(f"  {emoji} {short} {asset}: {delta_str} ({usd_delta}) → {avg_str}")

    body_parts = []
    if batch["extra_notes"]:
        body_parts.append("\n".join(batch["extra_notes"]))
        body_parts.append("")  # spacer
    body_parts.append("*Moviments:*")
    body_parts.extend(lines)
    body_parts.append("")
    body_parts.append(f"⏱️ Completat en {elapsed:.1f}s")
    if has_errors:
        body_parts.append("")
        body_parts.append("*Errors:*")
        for err in batch["errors"][:3]:
            body_parts.append(f"  • {_esc(err[:200])}")

    notify(title, "\n".join(body_parts), category="vault_batch",
           key=batch["key"], urgent=True)


def add_batch_note(note: str) -> bool:
    """Afegeix una línia descriptiva al resum del batch actiu (si n'hi ha)."""
    batch = _batch_active()
    if batch is None:
        return False
    batch["extra_notes"].append(note)
    return True


# ── Vault DCA System ────────────────────────────────────────────────

# Decimals raonables per fer els missatges llegibles. USDT 2, BTC 6, ETH 4, etc.
_DECIMALS = {
    "USDT": 2, "BTC": 6, "ETH": 4, "PAXG": 4,
    "SOL": 3, "USOX": 3, "SPYX": 4,
}

def _fmt_qty(asset: str, qty: float) -> str:
    """Formata una quantitat amb decimals adequats per a l'asset."""
    d = _DECIMALS.get(asset, 4)
    return f"{qty:,.{d}f}"

def _fmt_usdt(amount: float) -> str:
    """Formata un import USDT amb símbol i 2 decimals (o 4 si és petit)."""
    if abs(amount) < 1 and amount != 0:
        return f"${amount:,.4f}"
    return f"${amount:,.2f}"

def _esc_md(s) -> str:
    """Escape de caràcters reservats de Markdown per evitar Telegram 400."""
    if s is None: return ""
    return (str(s).replace("\\", "\\\\")
                   .replace("_", "\\_").replace("*", "\\*")
                   .replace("[", "\\[").replace("`", "\\`"))


def notify_vault_event(*, event_type: str, asset: str,
                       qty_delta: float, cost_delta_usdt: float,
                       qty_after: float, cost_after: float,
                       source: str, notes: str | None = None,
                       extra_lines: list[str] | None = None) -> bool:
    """Notifica una mutació al vault amb llenguatge humà i format agradable.

    El format varia segons event_type per explicar QUÈ ha passat
    i QUÈ vol dir, no només els números bruts.

    Si estem dins d'un with vault_batch(...): NO envia TG individual,
    només acumula l'event al batch per al resum final.
    """
    # ── Batch mode check ───────────────────────────────────────
    batch = _batch_active()
    if batch is not None:
        batch["events"].append({
            "event_type": event_type, "asset": asset,
            "qty_delta": qty_delta, "cost_delta_usdt": cost_delta_usdt,
            "qty_after": qty_after, "cost_after": cost_after,
            "source": source, "notes": notes,
        })
        return True  # collected, not sent
    # Estat resultant comú a tots els missatges
    avg_after = (cost_after / qty_after) if qty_after > 0 else None
    qty_str = _fmt_qty(asset, abs(qty_delta))
    qty_after_str = _fmt_qty(asset, qty_after)

    # ─── Templates per tipus d'event ──────────────────────────────
    if event_type == "vault_add_usdt" and "injection" in (source or ""):
        # Aportació manual de l'usuari via ComptesLab
        title = f"💰 Aportació manual: +{_fmt_usdt(cost_delta_usdt)}"
        body = (
            f"Has injectat *{_fmt_usdt(cost_delta_usdt)}* a la reserva del sistema.\n\n"
            f"Reserva total ara: *{_fmt_usdt(qty_after)}*\n\n"
            f"_S'utilitzarà per finançar relocacions de grids "
            f"o per acumular durant caigudes._"
        )

    elif event_type == "vault_add_usdt" and "harvest" in (source or ""):
        # Profit diari recollit
        title = f"🌾 Profits recollits: +{_fmt_usdt(cost_delta_usdt)}"
        body = (
            f"S'han extret *{_fmt_usdt(cost_delta_usdt)}* dels grids actius "
            f"i afegit a la reserva.\n\n"
            f"Reserva total: *{_fmt_usdt(qty_after)}*"
        )

    elif event_type == "vault_add_usdt":
        # Genèric (ex: recuperat d'un bot tancat)
        title = f"💰 Reserva: +{_fmt_usdt(cost_delta_usdt)}"
        body = (
            f"S'han afegit *{_fmt_usdt(cost_delta_usdt)}* a la reserva del sistema.\n"
            f"Origen: _{_esc_md(source)}_\n\n"
            f"Reserva total ara: *{_fmt_usdt(qty_after)}*"
        )

    elif event_type == "vault_remove_usdt":
        title = f"💸 Reserva: −{_fmt_usdt(abs(cost_delta_usdt))}"
        body = (
            f"S'han retirat *{_fmt_usdt(abs(cost_delta_usdt))}* de la reserva.\n"
            f"Destí: _{_esc_md(source)}_\n\n"
            f"Reserva restant: *{_fmt_usdt(qty_after)}*"
        )

    elif event_type == "vault_add_base":
        # Bot tancat — base recuperat al vault
        avg_cost = cost_delta_usdt / qty_delta if qty_delta else 0
        title = f"📥 Vault {asset}: +{qty_str} (recuperat de bot tancat)"
        body = (
            f"S'ha tancat un grid de *{_esc_md(asset)}* i el base ha anat al vault.\n\n"
            f"Recuperat: *{qty_str} {_esc_md(asset)}* (cost mig {_fmt_usdt(avg_cost)})\n"
            f"Cost total afegit: *{_fmt_usdt(cost_delta_usdt)}*\n\n"
            f"Vault {_esc_md(asset)} ara: *{qty_after_str}* "
            f"(cost mig: {_fmt_usdt(avg_after) if avg_after else '—'})\n\n"
            f"_Esperarem que el preu recuperi per vendre, "
            f"o l'usarem per finançar relocacions si fa falta._"
        )

    elif event_type in ("vault_remove_base", "fund_p1_vault_profit"):
        # Venda de vault (sigui per finançar P1 o altra raó)
        sell_price = abs(cost_delta_usdt / qty_delta) if qty_delta else 0
        title = f"✅ Venda vault {asset}: −{qty_str} a {_fmt_usdt(sell_price)}"
        body = (
            f"S'han venut *{qty_str} {_esc_md(asset)}* del vault.\n"
            f"Preu de venda: *{_fmt_usdt(sell_price)}*\n"
            f"USDT obtingut: *{_fmt_usdt(abs(cost_delta_usdt))}*\n\n"
            f"Vault {_esc_md(asset)} restant: *{qty_after_str}*\n\n"
            f"Motiu: {_esc_md(source)}"
        )

    elif event_type == "fund_p2_usdt_reserve":
        title = f"🏦 Reserva USDT usada: −{_fmt_usdt(abs(cost_delta_usdt))}"
        body = (
            f"S'han utilitzat *{_fmt_usdt(abs(cost_delta_usdt))}* de la reserva USDT "
            f"per finançar una relocació.\n\n"
            f"Reserva restant: *{_fmt_usdt(qty_after)}*\n\n"
            f"Destí: _{_esc_md(source)}_"
        )

    elif event_type == "fund_p3_vault_loss":
        sell_price = abs(cost_delta_usdt / qty_delta) if qty_delta else 0
        avg_at_sale = abs(cost_delta_usdt / qty_delta)  # avg cost de la porció venuda
        title = f"⚠️ Venda en pèrdua: {asset} −{qty_str}"
        body = (
            f"No hi havia vault en profit suficient. Venem el de *menor pèrdua %*.\n\n"
            f"Asset venut: *{_esc_md(asset)}*\n"
            f"Quantitat: *{qty_str}* a {_fmt_usdt(sell_price)}\n"
            f"USDT obtingut: *{_fmt_usdt(abs(cost_delta_usdt))}*\n\n"
            f"Vault {_esc_md(asset)} restant: *{qty_after_str}*\n\n"
            f"_Aquesta venda realitza una pèrdua acotada per evitar congelar el sistema._"
        )

    elif event_type == "fund_p4_own_asset":
        sell_price = abs(cost_delta_usdt / qty_delta) if qty_delta else 0
        title = f"🚨 ÚLTIM RECURS: venda base propi {asset}"
        body = (
            f"*Atenció:* el sistema no ha trobat altra forma de finançar el nou grid de "
            f"*{_esc_md(asset)}* i ha venut part del mateix asset.\n\n"
            f"Venuts: *{qty_str} {_esc_md(asset)}* a {_fmt_usdt(sell_price)}\n"
            f"USDT obtingut: *{_fmt_usdt(abs(cost_delta_usdt))}*\n\n"
            f"Vault {_esc_md(asset)} restant: *{qty_after_str}*\n\n"
            f"_Aquesta situació hauria de ser excepcional. "
            f"Considera afegir USDT manualment si es repeteix._"
        )

    elif event_type == "profit_harvest":
        title = f"🌾 Profits dels grids: +{_fmt_usdt(cost_delta_usdt)}"
        body = (
            f"S'han recollit profits dels grids actius:\n"
            f"Total recaptat: *{_fmt_usdt(cost_delta_usdt)}*\n\n"
            f"Reserva del sistema: *{_fmt_usdt(qty_after)}*"
        )

    else:
        # Fallback genèric per qualsevol event nou
        direction = "+" if qty_delta >= 0 else "−"
        title = f"🔔 Vault {_esc_md(event_type)}: {asset} {direction}{qty_str}"
        body = (
            f"Asset: *{_esc_md(asset)}*\n"
            f"Quantitat: *{direction}{qty_str}*\n"
            f"Variació cost: *{_fmt_usdt(cost_delta_usdt)}*\n"
            f"Estat: {qty_after_str} {_esc_md(asset)} "
            f"(cost mig {_fmt_usdt(avg_after) if avg_after else '—'})\n"
            f"Origen: _{_esc_md(source)}_"
        )

    if extra_lines:
        body += "\n\n" + "\n".join(extra_lines)
    if notes and event_type not in ("vault_add_usdt",):
        # Saltem les notes per injections (que tenen un id intern, no és útil)
        body += f"\n\n_{_esc_md(notes[:200])}_"

    return notify(title, body, category="vault",
                  key=event_type + ":" + asset, urgent=True)
