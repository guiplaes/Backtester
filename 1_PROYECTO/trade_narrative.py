"""Per-trade narrative builder.

Walks through brain_trade_history.json events in chronological order, groups
them into trades (OPEN → SIGNAL_CLOSE), and enriches each action (OPEN,
AVERAGE, PARTIAL_CLOSE, FULL_CLOSE) with the reasoning from the closest
matching Executor decision in brain_executor_decisions.jsonl.

Produces one structured JSON narrative per closed trade, persisted to
brain_trade_narratives.jsonl (append-only).

Consumed by:
  - trader_brain.py (auto-persists narrative when signal closes)
  - brain_flow.py (API /api/trade_narratives, dashboard UI)

Schema per narrative:
  {
    "trade_id": "t_abcd" | null,
    "direction": "BUY|SELL",
    "started_ts": 1700000000.0, "started_utc": "iso",
    "ended_ts": 1700000300.0,   "ended_utc": "iso",
    "duration_s": 300,
    "entry_price": 4700.0,
    "weighted_entry_price": 4698.5,
    "exit_price": 4705.2,       # last FULL_CLOSE price (may differ from entry)
    "total_pnl": 15.90,
    "avg_count": 1,
    "partial_count": 1,
    "close_reason": "cerramos via TG" | "TP" | null,
    "source_open": "EXECUTOR_AUTONOMOUS" | "BRAIN" | ...,
    "actions": [
      {
        "ts": 1700000000.0, "utc": "iso",
        "type": "OPEN|AVERAGE|PARTIAL_CLOSE|FULL_CLOSE",
        "price": 4700.0, "lot": 0.06,
        "pnl_delta": 0.0,   # only for partial/full closes
        "reason": "El preu a 4708 just a zona SELL MODERATE 4710, ...",
        "thesis": "Rang bearish 4692-4722, DXY UP, bias BEARISH"
      }
    ],
    "summary": "SELL 4min. Obert a 4708 amb tesi bearish, AVG a 4706 (cluster EQ_HIGHs swept), profit ladder partial a 4710, cerramos TG a 4712. +$15.90"
  }
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path


COMMON = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files")
HISTORY_FILE = COMMON / "brain_trade_history.json"
DECISIONS_FILE = COMMON / "brain_executor_decisions.jsonl"
NARRATIVES_FILE = COMMON / "brain_trade_narratives.jsonl"

_lock = threading.Lock()


# ── I/O helpers ────────────────────────────────────────────────────────

def _read_jsonl(path):
    if not path.exists():
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _read_history_events():
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d.get("events", [])
    except Exception:
        return []


def _iso(ts):
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except Exception:
        return None


# ── Decision matching ──────────────────────────────────────────────────

def _find_nearest_decision(decisions, event, window_before_s=120, window_after_s=10):
    """Return the closest preceding Executor decision whose action matches the event type.

    Matching logic per event.type:
      OPEN          → earliest decision with action=AVERAGE|OPEN with matching direction
      AVERAGE       → nearest decision with action=AVERAGE in time window
      PARTIAL_CLOSE → nearest decision with action=PARTIAL_CLOSE in time window
      FULL_CLOSE    → nearest decision with close_ticket set, OR PARTIAL_CLOSE>=100%, OR none
    """
    et = event.get("type")
    ets = float(event.get("ts") or 0)
    if ets <= 0:
        return None
    action_match = {
        "OPEN": ("AVERAGE", "OPEN"),       # legacy autonomous open was tagged as AVERAGE
        "AVERAGE": ("AVERAGE",),
        "PARTIAL_CLOSE": ("PARTIAL_CLOSE",),
        "FULL_CLOSE": ("PARTIAL_CLOSE",),  # full close often preceded by a partial
    }.get(et, ())
    if not action_match:
        return None
    best = None
    best_delta = 1e9
    for d in decisions:
        dts = float(d.get("ts") or 0)
        if dts <= 0:
            continue
        delta = ets - dts
        if delta < -window_after_s or delta > window_before_s:
            continue
        if d.get("action") not in action_match:
            continue
        if abs(delta) < best_delta:
            best_delta = abs(delta)
            best = d
    return best


def _reason_from_decision(d):
    """Extract a concise reasoning string from an Executor decision row."""
    if not d:
        return None
    # reasoning_full is the big one; thesis is a one-liner. Prefer thesis if short.
    rsn = (d.get("reasoning_full") or d.get("reasoning") or "").strip()
    return rsn[:500] if rsn else None


def _thesis_from_decision(d):
    if not d:
        return None
    return (d.get("thesis") or "").strip()[:240] or None


# ── Trade grouping ─────────────────────────────────────────────────────

def _group_trades(events):
    """Walk events in order. Emit (trade_events, close_event) pairs.

    A trade starts at the first OPEN after a prior SIGNAL_CLOSE (or at the head),
    and ends at the next SIGNAL_CLOSE. Events in between belong to that trade.
    """
    trades = []
    current = []
    for e in sorted(events, key=lambda x: x.get("ts", 0)):
        t = e.get("type")
        if t == "OPEN":
            if current:
                # Orphan OPEN without a prior close — flush whatever we had
                trades.append(current)
            current = [e]
        elif t == "SIGNAL_CLOSE":
            if current:
                current.append(e)
                trades.append(current)
                current = []
        else:
            if current:
                current.append(e)
    if current:
        trades.append(current)
    return trades


# ── Summary generator (deterministic, no LLM) ─────────────────────────

def _summarize(actions, direction, total_pnl, duration_s, close_reason):
    """Produce a 1-3 sentence human-readable summary of the trade."""
    if not actions:
        return ""
    entry = next((a for a in actions if a.get("type") == "OPEN"), None)
    avgs = [a for a in actions if a.get("type") == "AVERAGE"]
    partials = [a for a in actions if a.get("type") == "PARTIAL_CLOSE"]
    full = [a for a in actions if a.get("type") == "FULL_CLOSE"]

    dur_txt = f"{int(duration_s/60)}m" if duration_s >= 60 else f"{int(duration_s)}s"
    pnl_txt = f"{total_pnl:+.2f}$" if total_pnl is not None else "n/a"
    parts = [f"{direction} de {dur_txt}"]
    if entry:
        parts.append(f"entrada a {entry.get('price'):.2f}")
    if avgs:
        avg_prices = ", ".join(f"{a.get('price'):.1f}" for a in avgs)
        parts.append(f"{len(avgs)} averaging(s) a {avg_prices}")
    if partials:
        p_sum = sum(a.get("pnl_delta", 0) for a in partials)
        parts.append(f"{len(partials)} partial(s) capturant {p_sum:+.2f}$")
    if full:
        fp_sum = sum(a.get("pnl_delta", 0) for a in full)
        last_px = full[-1].get("price")
        px_txt = f"a {last_px:.2f}" if last_px else ""
        parts.append(f"tancament {px_txt} ({fp_sum:+.2f}$)")
    if close_reason:
        parts.append(f"motiu: {close_reason}")
    parts.append(f"total {pnl_txt}")
    return ". ".join(parts) + "."


# ── Build narrative ────────────────────────────────────────────────────

def build_trade_narrative(trade_events, decisions):
    """Compose a single narrative from a trade's events + all decisions log.

    `trade_events` is the list [OPEN, AVG, ..., SIGNAL_CLOSE] for ONE trade.
    """
    if not trade_events:
        return None
    opens = [e for e in trade_events if e.get("type") == "OPEN"]
    if not opens:
        return None
    first = trade_events[0]
    last = trade_events[-1]
    direction = first.get("direction") or ""
    entry_price = first.get("price")
    # Weighted entry (OPEN + AVGs)
    lots = [(e.get("price") or 0, e.get("lot") or 0)
            for e in trade_events if e.get("type") in ("OPEN", "AVERAGE")]
    tot_lot = sum(l for _, l in lots)
    w_entry = (sum(p * l for p, l in lots) / tot_lot) if tot_lot > 0 else entry_price
    # Sum pnl from partials + fulls + signal_close
    total_pnl = sum(float(e.get("pnl_delta") or 0)
                    for e in trade_events
                    if e.get("type") in ("PARTIAL_CLOSE", "FULL_CLOSE"))
    # SIGNAL_CLOSE often carries the authoritative final P&L
    sc = next((e for e in trade_events if e.get("type") == "SIGNAL_CLOSE"), None)
    if sc and sc.get("pnl_delta") not in (None, 0):
        total_pnl = float(sc.get("pnl_delta") or 0)

    started_ts = first.get("ts")
    ended_ts = last.get("ts")
    duration_s = (ended_ts - started_ts) if (started_ts and ended_ts) else 0

    # Trade_id inference: check decisions in window, grab the most common trade_id
    from collections import Counter
    tid_counts = Counter()
    for d in decisions:
        dts = float(d.get("ts") or 0)
        if started_ts and ended_ts and started_ts - 30 <= dts <= ended_ts + 30:
            if d.get("trade_id"):
                tid_counts[d["trade_id"]] += 1
    trade_id = tid_counts.most_common(1)[0][0] if tid_counts else None

    source_open = first.get("source") or first.get("source_open") or "?"
    close_reason = (sc or {}).get("reason") or (sc or {}).get("close_reason") or None
    last_full = next((e for e in reversed(trade_events) if e.get("type") == "FULL_CLOSE"), None)
    exit_price = (last_full or {}).get("price") or (sc or {}).get("price")

    # Enrich each action with the nearest matching decision
    actions = []
    for e in trade_events:
        t = e.get("type")
        if t not in ("OPEN", "AVERAGE", "PARTIAL_CLOSE", "FULL_CLOSE", "SIGNAL_CLOSE"):
            continue
        dec = _find_nearest_decision(decisions, e)
        reason = _reason_from_decision(dec)
        thesis = _thesis_from_decision(dec)
        # Fallback reason: use event.reason field if LLM wasn't involved
        if not reason:
            reason = (e.get("reason") or e.get("source") or "").strip() or None
        action = {
            "ts": e.get("ts"),
            "utc": _iso(e.get("ts")),
            "type": t,
            "price": e.get("price"),
            "lot": e.get("lot"),
        }
        if t in ("PARTIAL_CLOSE", "FULL_CLOSE"):
            action["pnl_delta"] = e.get("pnl_delta")
        if reason:
            action["reason"] = reason
        if thesis:
            action["thesis"] = thesis
        # Source if present (e.g. "BRAIN", "HUNTER", "EXECUTOR_AUTONOMOUS")
        if e.get("source"):
            action["source"] = e["source"]
        actions.append(action)

    narrative = {
        "trade_id": trade_id,
        "direction": direction,
        "started_ts": started_ts,
        "started_utc": _iso(started_ts),
        "ended_ts": ended_ts,
        "ended_utc": _iso(ended_ts),
        "duration_s": round(duration_s, 1),
        "entry_price": entry_price,
        "weighted_entry_price": round(w_entry, 3) if w_entry else None,
        "exit_price": exit_price,
        "total_pnl": round(total_pnl, 2),
        "avg_count": sum(1 for a in actions if a.get("type") == "AVERAGE"),
        "partial_count": sum(1 for a in actions if a.get("type") == "PARTIAL_CLOSE"),
        "full_close_count": sum(1 for a in actions if a.get("type") == "FULL_CLOSE"),
        "close_reason": close_reason,
        "source_open": source_open,
        "actions": actions,
    }
    narrative["summary"] = _summarize(actions, direction, total_pnl,
                                       duration_s, close_reason)
    return narrative


# ── Public API ─────────────────────────────────────────────────────────

def build_all_narratives():
    """Build narratives for every completed trade in the history.
    Returns list of narratives (newest first)."""
    events = _read_history_events()
    decisions = _read_jsonl(DECISIONS_FILE)
    trades = _group_trades(events)
    out = []
    for tr in trades:
        # Only include trades that ended (SIGNAL_CLOSE present)
        if not any(e.get("type") == "SIGNAL_CLOSE" for e in tr):
            continue
        n = build_trade_narrative(tr, decisions)
        if n:
            out.append(n)
    out.sort(key=lambda n: n.get("started_ts") or 0, reverse=True)
    return out


def persist_latest_narrative():
    """Build the narrative for the most recent closed trade and append it to
    brain_trade_narratives.jsonl. Idempotent per trade: won't duplicate if the
    same (started_ts, ended_ts) pair already exists.

    Returns the narrative dict, or None if no new trade to persist.
    """
    with _lock:
        events = _read_history_events()
        decisions = _read_jsonl(DECISIONS_FILE)
        trades = _group_trades(events)
        # Find the most recent closed trade
        last_closed = None
        for tr in reversed(trades):
            if any(e.get("type") == "SIGNAL_CLOSE" for e in tr):
                last_closed = tr
                break
        if not last_closed:
            return None
        narrative = build_trade_narrative(last_closed, decisions)
        if not narrative:
            return None
        # Dedup: scan existing narratives, skip if same (started_ts, ended_ts) exist
        key = (narrative.get("started_ts"), narrative.get("ended_ts"))
        existing = _read_jsonl(NARRATIVES_FILE)
        for n in existing:
            if (n.get("started_ts"), n.get("ended_ts")) == key:
                return None  # already persisted
        try:
            with open(NARRATIVES_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(narrative, ensure_ascii=False) + "\n")
        except OSError:
            return None
        return narrative


def read_all_narratives(limit=50):
    """Return the last N persisted narratives (newest first)."""
    out = _read_jsonl(NARRATIVES_FILE)
    out.sort(key=lambda n: n.get("started_ts") or 0, reverse=True)
    return out[:limit]


def backfill():
    """Rebuild brain_trade_narratives.jsonl from scratch using the full
    history + decisions log. Overwrites existing file."""
    narratives = build_all_narratives()
    with _lock:
        try:
            with open(NARRATIVES_FILE, "w", encoding="utf-8") as f:
                for n in narratives:
                    f.write(json.dumps(n, ensure_ascii=False) + "\n")
        except OSError:
            pass
    return len(narratives)


if __name__ == "__main__":
    # CLI usage: backfill or dump latest
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--backfill":
        n = backfill()
        print(f"backfilled {n} narratives to {NARRATIVES_FILE}")
    else:
        narratives = build_all_narratives()
        print(f"{len(narratives)} narratives")
        for n in narratives[:5]:
            print("—" * 60)
            print(n.get("started_utc"), n.get("direction"),
                  f"${n.get('total_pnl', 0):+.2f}")
            print(n.get("summary"))
