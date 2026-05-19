"""
brain_journal — Single source of truth for trade lifecycle and reasoning.

Replaces the scattered combination of:
  · brain_trade_history.json     (OPEN/AVG/PARTIAL/CLOSE timeline)
  · brain_executor_decisions.jsonl  (LLM reasoning)
  · brain_events_log.jsonl       (internal triggers)
  · brain_trade_narratives.jsonl (post-hoc narratives)
  · audit.jsonl                  (TG/order audit)

…with a single append-only JSONL (`brain_journal.jsonl`) where every event
shares a common envelope and `trade_id`. Old files keep being written for
back-compat but the journal is the canonical log going forward.

Schema per line:
  {
    "ts":       float epoch,
    "utc":      ISO-8601 string,
    "trade_id": "t_DDMM_HHMM_DIR" | null,
    "type":     one of EVENT_TYPES,
    "source":   "TG" | "EXECUTOR" | "FAST" | "VALIDATOR" | "EA" | "BRAIN" | "INDICATOR" | "HUNTER" | ...,
    "snapshot": {price, balance, equity, dd_pct, session, news, ...} | null,
    "payload":  type-specific dict,
    "links":    {ticket, decision_id, ref_event} | null
  }

Public API:
  write(type, source, payload, *, trade_id=None, snapshot=None, links=None)
  build_snapshot(price, account, sig_state)
  read_trade(trade_id)
  list_trades(since_days=7, limit=50)
  iter_events(since_ts=None, types=None)

Files:
  brain_journal.jsonl          — current month
  brain_journal_YYYY-MM.jsonl  — archived months (auto-rotated)
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

# Try to import news_state for snapshot enrichment. Optional dependency to
# avoid forcing journal users to also load news_state at import time.
try:
    import news_state as _news_state
except Exception:
    _news_state = None

COMMON = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files")
JOURNAL_FILE = COMMON / "brain_journal.jsonl"
_SIGNAL_STATE_FILE = COMMON / "brain_signal_state.json"

_lock = threading.Lock()


def _resolve_trade_id_from_fsm() -> Optional[str]:
    """Read the active trade_id from the persisted FSM, if any. Returns None
    when no signal is active or the file is missing/corrupt. Cheap on Windows
    (file is ~2KB, read every event would be wasteful — callers should still
    pass trade_id explicitly when they have it)."""
    try:
        if not _SIGNAL_STATE_FILE.exists():
            return None
        with open(_SIGNAL_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        fsm = data.get("fsm") or {}
        tid = fsm.get("trade_id") if isinstance(fsm, dict) else None
        # Only return if signal is actually active — otherwise trade_id is stale
        if tid and data.get("active"):
            return tid
    except Exception:
        pass
    return None

# ── Closed taxonomy of event types ──
EVENT_TYPES = frozenset({
    # Signal lifecycle
    "signal_received",          # TG OPEN message arrived (pre-gate)
    "signal_filter_blocked",    # gate (news/session/spread) refused entry
    "signal_filter_passed",     # all gates passed, proceeding to open
    # Order proposal / execution
    "order_proposed",           # Executor/FAST/Hunter proposed an action
    "order_rejected",           # Validator or pre-flight rejected it
    "order_sent",               # MARKET/CLOSE/MODIFY written to brain_orders.json
    # Trade lifecycle (mirrors trade_history.log_event)
    "trade_opened",
    "trade_averaged",
    "trade_partial",
    "trade_closed",
    "trade_signal_closed",      # whole signal terminated
    "trade_dd_stopped",
    # LLM cycles
    "decision_executor",
    "decision_indicator",
    "decision_fast",
    "decision_hunter",
    # Context observations
    "news_observed",
    "session_transition",
    "thesis_updated",
    "invalidation_observed",
    # Catch-all for things we want logged but don't fit yet
    "note",
})

VALID_SOURCES = frozenset({
    "TG", "EXECUTOR", "INDICATOR", "FAST", "HUNTER",
    "VALIDATOR", "EA", "BRAIN", "USER", "SCHEDULER",
})


# ── Writer ────────────────────────────────────────────────────────────

def _ensure_dir():
    JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)


def write(
    type: str,
    source: str,
    payload: dict | None = None,
    *,
    trade_id: Optional[str] = None,
    snapshot: dict | None = None,
    links: dict | None = None,
) -> None:
    """Append an event to the journal. Never raises.

    Validation is intentionally lax in production (we'd rather log a
    malformed event than lose it), but `type` is checked against EVENT_TYPES
    to prevent typo proliferation. Unknown types fall back to "note" with
    the original type stashed in payload._unknown_type.
    """
    try:
        if type not in EVENT_TYPES:
            payload = dict(payload or {})
            payload["_unknown_type"] = type
            type = "note"

        # Auto-resolve trade_id from the FSM when caller didn't pass one and
        # the event is one that belongs to a trade lifecycle. This ensures
        # legacy trade_history.log_event call sites (which don't pass it)
        # still get correctly tagged.
        if trade_id is None and type in (
            "trade_opened", "trade_averaged", "trade_partial",
            "trade_closed", "trade_signal_closed", "trade_dd_stopped",
            "decision_executor", "decision_indicator", "decision_fast",
            "order_proposed", "order_rejected", "order_sent",
            "thesis_updated", "invalidation_observed",
            "signal_filter_passed",
        ):
            trade_id = _resolve_trade_id_from_fsm()

        now = time.time()
        row = {
            "ts": now,
            "utc": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            "trade_id": trade_id,
            "type": type,
            "source": source if source in VALID_SOURCES else "BRAIN",
            "snapshot": snapshot,
            "payload": payload or {},
            "links": links,
        }

        _ensure_dir()
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        with _lock:
            with open(JOURNAL_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        # Never crash the trader for journaling failures.
        pass


# ── Snapshot helper ───────────────────────────────────────────────────

def build_snapshot(price: float | None, account: dict | None,
                   sig_state=None, extras: dict | None = None) -> dict:
    """Construct the small context dict embedded in journal events.

    Captures *just enough* to reconstruct what the system saw at the moment
    of the event. Heavy data (zone maps, full bars) goes in payload only
    when the event type warrants it.
    """
    snap: dict = {}
    if price is not None:
        try:
            snap["price"] = round(float(price), 3)
        except Exception:
            pass
    if isinstance(account, dict):
        for k_dst, k_src in (
            ("balance", "balance"),
            ("equity", "equity"),
            ("dd_pct", "dd_pct"),
            ("dd_used", "dd_used"),
            ("has_signal", "has_signal"),
            ("direction", "direction"),
            ("entry_price", "entry_price"),
        ):
            v = account.get(k_src)
            if v is not None:
                snap[k_dst] = v
        # Position count is cheap and useful
        try:
            snap["positions_count"] = len(account.get("positions", []) or [])
        except Exception:
            pass

    # Session + news from news_state if available
    if _news_state is not None:
        try:
            snap["session"] = _news_state.session_label()
        except Exception:
            pass
        try:
            hi = _news_state.high_impact_within(30)
            if hi:
                snap["news"] = {
                    "importance": hi.get("importance"),
                    "event_time": hi["event_time"].isoformat() if hi.get("event_time") else None,
                    "blocking": True,
                }
            else:
                snap["news"] = {"blocking": False}
        except Exception:
            pass

    # Signal context
    if sig_state is not None:
        try:
            if hasattr(sig_state, "get_trade_id"):
                tid = sig_state.get_trade_id()
                if tid:
                    snap["trade_id"] = tid
            if hasattr(sig_state, "is_active") and sig_state.is_active():
                if hasattr(sig_state, "get"):
                    snap["avg_count"] = sig_state.get("avg_count") or 0
                    snap["breakeven_set"] = bool(sig_state.get("breakeven_set"))
        except Exception:
            pass

    if extras:
        try:
            snap.update(extras)
        except Exception:
            pass

    return snap


# ── Readers ───────────────────────────────────────────────────────────

def iter_events(since_ts: float | None = None,
                types: Iterable[str] | None = None,
                limit: int | None = None) -> list[dict]:
    """Yield events from the current journal file matching filters.

    Reads the entire file into memory — fine for journal sizes < 100 MB.
    For larger sets, switch to streaming or rotate per month.
    """
    if not JOURNAL_FILE.exists():
        return []
    types_set = frozenset(types) if types else None
    out: list[dict] = []
    try:
        with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if since_ts and row.get("ts", 0) < since_ts:
                    continue
                if types_set and row.get("type") not in types_set:
                    continue
                out.append(row)
    except OSError:
        return []
    if limit:
        out = out[-limit:]
    return out


def read_trade(trade_id: str) -> list[dict]:
    """All events tagged with `trade_id`, plus context events that occurred
    in the same time window (news_observed, session_transition) so the
    reviewer sees what was happening around the trade.
    """
    if not trade_id:
        return []
    direct = [e for e in iter_events() if e.get("trade_id") == trade_id]
    if not direct:
        return []
    t0 = min(e["ts"] for e in direct)
    t1 = max(e["ts"] for e in direct)
    context_types = {"news_observed", "session_transition"}
    context = [
        e for e in iter_events()
        if e.get("type") in context_types
        and t0 - 300 <= e.get("ts", 0) <= t1 + 60
        and e not in direct
    ]
    merged = direct + context
    merged.sort(key=lambda e: e.get("ts", 0))
    return merged


def list_trades(since_days: int = 7, limit: int = 50) -> list[dict]:
    """Return summaries of trades found in the journal, newest first.

    A "trade" here is identified by a unique non-null `trade_id`. Summary
    is built from the events tagged with that id.
    """
    cutoff = time.time() - since_days * 86400
    by_tid: dict[str, list[dict]] = {}
    for e in iter_events(since_ts=cutoff):
        tid = e.get("trade_id")
        if not tid:
            continue
        by_tid.setdefault(tid, []).append(e)

    summaries: list[dict] = []
    for tid, evs in by_tid.items():
        evs.sort(key=lambda x: x.get("ts", 0))
        first = evs[0]
        last = evs[-1]
        opens = [e for e in evs if e.get("type") == "trade_opened"]
        avgs = [e for e in evs if e.get("type") == "trade_averaged"]
        partials = [e for e in evs if e.get("type") == "trade_partial"]
        closes = [e for e in evs if e.get("type") in ("trade_closed", "trade_signal_closed")]
        decisions = [e for e in evs if e.get("type", "").startswith("decision_")]
        rejections = [e for e in evs if e.get("type") == "order_rejected"]

        direction = None
        entry_price = None
        if opens:
            direction = (opens[0].get("payload") or {}).get("direction")
            entry_price = (opens[0].get("payload") or {}).get("price")

        # Sum P&L from partials + closes
        total_pnl = 0.0
        for e in partials + closes:
            pnl = (e.get("payload") or {}).get("pnl_delta")
            if pnl is not None:
                try:
                    total_pnl += float(pnl)
                except Exception:
                    pass

        summaries.append({
            "trade_id": tid,
            "started_utc": first.get("utc"),
            "ended_utc": last.get("utc") if closes else None,
            "direction": direction,
            "entry_price": entry_price,
            "n_avgs": len(avgs),
            "n_partials": len(partials),
            "n_decisions": len(decisions),
            "n_rejections": len(rejections),
            "total_pnl": round(total_pnl, 2),
            "closed": bool(closes),
            "first_ts": first.get("ts"),
        })
    summaries.sort(key=lambda s: s.get("first_ts", 0), reverse=True)
    return summaries[:limit]
