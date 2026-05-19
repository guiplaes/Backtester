#!/usr/bin/env python3
"""
Trade History — append-only event log of trade lifecycle events.

Each event: {ts, utc, type, ticket, direction, lot, price, reason, pnl_delta, source}

Types:
  OPEN           — new ticket opened (signal entry)
  AVERAGE        — added to existing signal (averaging down)
  PARTIAL_CLOSE  — partial reduction of a ticket
  FULL_CLOSE     — ticket fully closed
  SIGNAL_CLOSE   — signal reset (all positions closed)
  DD_STOP        — EA auto-closed at DD limit

Sources:
  TG             — telegram signal
  HUNTER         — autonomous HUNTER entry
  FAST           — FAST engine averaging
  EXECUTOR       — Claude EXECUTOR decision
  MANUAL         — user/external (closed outside brain)
  DD_AUTO        — EA safety net at 3.5% DD
"""

import os
import json
import time
import threading
from datetime import datetime, timezone

# Optional mirror to the unified brain_journal. Imported lazily inside
# log_event so a circular import or missing module never breaks the legacy
# trade_history writer.
try:
    import brain_journal as _journal
except Exception:
    _journal = None

COMMON = r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files"
HISTORY_FILE = os.path.join(COMMON, 'brain_trade_history.json')

MAX_EVENTS = 2000  # keep last N events; older rolled to brain_trades_archive.json

_lock = threading.Lock()

# trade_history event type → journal event type
_JOURNAL_TYPE_MAP = {
    "OPEN":          "trade_opened",
    "AVERAGE":       "trade_averaged",
    "PARTIAL_CLOSE": "trade_partial",
    "FULL_CLOSE":    "trade_closed",
    "SIGNAL_CLOSE":  "trade_signal_closed",
    "DD_STOP":       "trade_dd_stopped",
}


def log_event(type, ticket=0, direction='', lot=0.0, price=0.0,
              reason='', pnl_delta=0.0, source='', meta=None,
              trade_id=None, snapshot=None):
    """Append a new event to the history. Thread-safe.

    Also mirrors to brain_journal (unified log) so review tooling has a
    single source. `trade_id` and `snapshot` are journal-only enrichments;
    leave None and the journal entry will inherit minimal context.
    """
    event = {
        'ts': time.time(),
        'utc': datetime.now(timezone.utc).isoformat(),
        'type': type,
        'ticket': int(ticket) if ticket else 0,
        'direction': direction or '',
        'lot': round(float(lot), 3),
        'price': round(float(price), 2) if price else 0.0,
        'reason': (reason or '')[:300],
        'pnl_delta': round(float(pnl_delta), 2),
        'source': source or '',
    }
    if meta:
        event['meta'] = meta

    with _lock:
        try:
            data = {'events': []}
            if os.path.exists(HISTORY_FILE):
                try:
                    with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                except Exception:
                    data = {'events': []}
            events = data.get('events', [])
            events.append(event)
            # Keep last MAX_EVENTS
            if len(events) > MAX_EVENTS:
                events = events[-MAX_EVENTS:]
            data['events'] = events
            with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # Mirror into unified journal (best-effort, never raises)
    if _journal is not None:
        try:
            jtype = _JOURNAL_TYPE_MAP.get(type, "note")
            jsource = source.split('_')[0].upper() if source else "BRAIN"
            if jsource not in ("TG", "EXECUTOR", "INDICATOR", "FAST", "HUNTER",
                               "VALIDATOR", "EA", "BRAIN", "USER", "SCHEDULER"):
                jsource = "BRAIN"
            payload = {
                'direction': direction,
                'lot': event['lot'],
                'price': event['price'],
                'reason': event['reason'],
                'pnl_delta': event['pnl_delta'],
                'source_raw': source,
            }
            if meta:
                payload['meta'] = meta
            links = {'ticket': event['ticket']} if event['ticket'] else None
            _journal.write(
                jtype, jsource, payload,
                trade_id=trade_id,
                snapshot=snapshot,
                links=links,
            )
        except Exception:
            pass

    return event


def load_recent(limit=30):
    """Return the last `limit` events."""
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        events = data.get('events', [])
        return events[-limit:]
    except Exception:
        return []
