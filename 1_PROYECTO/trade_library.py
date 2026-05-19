#!/usr/bin/env python3
"""Trade Library — groups event log into closed trades with summaries.

Reads brain_trade_history.json (append-only event log) and brain_trades_archive.json
(long-term archive). Groups events by SIGNAL_CLOSE boundaries into complete trade
lifecycles. Each trade gets a summary: open/close times, direction, entry, blend,
avgs, partials, final P&L, reason for close.

Exposed via:
  - trade_library.list_trades(since_days=1)  → list of trade summaries
  - trade_library.get_trade(index)           → full detail of one trade
  - trade_library.daily_summary(day='YYYY-MM-DD') → aggregated day stats
  - trade_library.roll_to_archive()          → move old events to archive (called periodically)
"""
import os
import json
import time
import threading
from datetime import datetime, timezone, timedelta
from collections import defaultdict

COMMON = r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files"
HISTORY_FILE = os.path.join(COMMON, 'brain_trade_history.json')
ARCHIVE_FILE = os.path.join(COMMON, 'brain_trades_archive.json')

_lock = threading.Lock()


def _load_events():
    """Load all events from both history (recent) and archive (old)."""
    events = []
    for path in (ARCHIVE_FILE, HISTORY_FILE):
        try:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    events.extend(data.get('events', []))
        except Exception:
            pass
    # Sort chronologically
    events.sort(key=lambda e: e.get('ts', 0))
    return events


def _dedupe_events(events):
    """Remove near-duplicate close events for the same ticket.

    When EXECUTOR sends a partial/full close and the P&L tracker DETECTS it
    seconds later, we get two rows for the same broker action — summing them
    doubles the P&L. Keep the EXECUTOR/HUNTER/FAST one (has reasoning) and
    drop the DETECTED twin within a 30s window.
    """
    DEDUP_WINDOW_S = 30
    CLOSE_TYPES = {'PARTIAL_CLOSE', 'FULL_CLOSE'}
    out = []
    for e in events:
        if e.get('type') not in CLOSE_TYPES:
            out.append(e)
            continue
        tk = e.get('ticket') or 0
        src = (e.get('source') or '').upper()
        ts = float(e.get('ts') or 0)
        dup_idx = None
        for i, prev in enumerate(out):
            if prev.get('type') != e.get('type'):
                continue
            if (prev.get('ticket') or 0) != tk:
                continue
            if abs(float(prev.get('ts') or 0) - ts) > DEDUP_WINDOW_S:
                continue
            dup_idx = i
            break
        if dup_idx is None:
            out.append(e)
            continue
        prev = out[dup_idx]
        prev_src = (prev.get('source') or '').upper()
        # Prefer the non-DETECTED one. If both non-DETECTED, keep the first.
        if prev_src == 'DETECTED' and src != 'DETECTED':
            out[dup_idx] = e
        # else: drop the current (duplicate)
    return out


def _group_into_trades(events):
    """Group events into logical trades (one per BUY/SELL cycle).

    A trade ends at SIGNAL_CLOSE. Everything after the previous SIGNAL_CLOSE
    (or the very first event) belongs to the next trade. Events are dedup'd
    first to avoid DETECTED+EXECUTOR double counting. Empty/orphan trades
    (only a SIGNAL_CLOSE with no prior content) are dropped as noise.
    """
    events = _dedupe_events(events)
    trades = []
    current = None

    def _start_trade(e, adopted=False):
        return {
            'open': True,
            'started_ts': e.get('ts'),
            'started_utc': e.get('utc'),
            'direction': e.get('direction'),
            'entry_price': e.get('price') or (e.get('meta') or {}).get('entry_price'),
            'source': e.get('source') if not adopted else 'ADOPTED',
            'adopted': adopted,
            'events': [e],
        }

    def _close_current(cur, e):
        cur['open'] = False
        cur['ended_ts'] = e.get('ts')
        cur['ended_utc'] = e.get('utc')
        cur['close_source'] = e.get('source')
        cur['close_reason'] = e.get('reason')
        cur['final_pnl'] = e.get('pnl_delta', 0)

    def _is_noise(cur):
        """A 'trade' with only a SIGNAL_CLOSE event and no real content."""
        evs = cur.get('events') or []
        if not evs:
            return True
        # Noise if no open/avg/close content beyond the terminator itself
        meaningful = [x for x in evs if x.get('type') in
                      ('OPEN', 'AVERAGE', 'PARTIAL_CLOSE', 'FULL_CLOSE')]
        return len(meaningful) == 0

    for e in events:
        t = e.get('type')
        if t == 'OPEN':
            if current:
                current['open'] = False
                if not _is_noise(current):
                    trades.append(current)
            current = _start_trade(e, adopted=False)
            continue

        if current is None:
            # First event isn't OPEN — synthesize an adopted trade. Drop it later
            # if nothing meaningful shows up before the next SIGNAL_CLOSE.
            current = _start_trade(e, adopted=True)
            if t == 'SIGNAL_CLOSE':
                _close_current(current, e)
                if not _is_noise(current):
                    trades.append(current)
                current = None
            continue

        current['events'].append(e)
        if t == 'SIGNAL_CLOSE':
            _close_current(current, e)
            if not _is_noise(current):
                trades.append(current)
            current = None

    if current and not _is_noise(current):
        trades.append(current)
    return trades


def _summarize_trade(trade):
    """Build a compact summary dict for a trade."""
    events = trade.get('events', [])
    avgs = [e for e in events if e.get('type') == 'AVERAGE']
    partials = [e for e in events if e.get('type') == 'PARTIAL_CLOSE']
    full_closes = [e for e in events if e.get('type') == 'FULL_CLOSE']
    be_sets = [e for e in events if e.get('type') == 'MOVE_SL_ENTRY']

    # Sum P&L from all profit-realizing events
    total_pnl = 0.0
    for e in events:
        if e.get('type') in ('PARTIAL_CLOSE', 'FULL_CLOSE', 'SIGNAL_CLOSE'):
            total_pnl += float(e.get('pnl_delta', 0) or 0)
    # If SIGNAL_CLOSE has a non-zero final pnl, prefer it — it's the authoritative
    # broker-reported realized figure. But ignore the 0 case ("cerramos" received
    # before any PARTIAL/FULL close event carries a value) so we don't wipe the sum.
    _fp = trade.get('final_pnl')
    if _fp is not None and _fp != 0:
        total_pnl = _fp

    # Duration
    if trade.get('ended_ts') and trade.get('started_ts'):
        duration_s = trade['ended_ts'] - trade['started_ts']
    else:
        duration_s = 0

    # Averaging prices
    avg_prices = [float(a.get('price', 0) or 0) for a in avgs if a.get('price')]
    avg_lots_total = sum(float(a.get('lot', 0) or 0) for a in avgs)
    initial_lot = 0.0
    if events and events[0].get('type') == 'OPEN':
        initial_lot = float(events[0].get('lot', 0) or 0)

    # Blend
    blend = trade.get('entry_price', 0)
    total_weighted = (trade.get('entry_price', 0) or 0) * initial_lot
    total_lots = initial_lot
    for a in avgs:
        lot = float(a.get('lot', 0) or 0)
        p = float(a.get('price', 0) or 0)
        total_weighted += p * lot
        total_lots += lot
    if total_lots > 0:
        blend = round(total_weighted / total_lots, 2)

    # Sources involved
    sources = set()
    for e in events:
        if e.get('source'):
            sources.add(e.get('source'))

    return {
        'started_utc': trade.get('started_utc'),
        'started_ts': trade.get('started_ts'),
        'ended_utc': trade.get('ended_utc'),
        'ended_ts': trade.get('ended_ts'),
        'open': trade.get('open', False),
        'direction': trade.get('direction'),
        'entry_price': trade.get('entry_price'),
        'blend_price': blend,
        'source_open': trade.get('source'),
        'source_close': trade.get('close_source'),
        'close_reason': (trade.get('close_reason') or '')[:160],
        'pnl_usd': round(total_pnl, 2),
        'duration_min': round(duration_s / 60, 1),
        'avgs_count': len(avgs),
        'avg_prices': avg_prices,
        'partials_count': len(partials),
        'partials_pnl': round(sum(float(p.get('pnl_delta', 0) or 0) for p in partials), 2),
        'full_closes_count': len(full_closes),
        'had_breakeven': len(be_sets) > 0,
        'initial_lot': initial_lot,
        'total_lot': round(total_lots, 3),
        'sources': sorted(sources),
        'event_count': len(events),
    }


def list_trades(since_days=None, limit=50):
    """Return list of trade summaries, newest first."""
    events = _load_events()
    if since_days:
        cutoff = time.time() - since_days * 86400
        events = [e for e in events if e.get('ts', 0) >= cutoff]
    trades = _group_into_trades(events)
    summaries = [_summarize_trade(t) for t in trades]
    summaries.sort(key=lambda s: s.get('started_ts', 0), reverse=True)
    return summaries[:limit]


def get_trade_detail(index):
    """Return full detail (all events) of trade at given index (0=most recent)."""
    events = _load_events()
    trades = _group_into_trades(events)
    trades.sort(key=lambda t: t.get('started_ts', 0), reverse=True)
    if index < 0 or index >= len(trades):
        return None
    t = trades[index]
    summary = _summarize_trade(t)
    summary['events'] = t.get('events', [])
    return summary


def daily_summary(day=None):
    """Aggregate day stats. `day` as 'YYYY-MM-DD' UTC; None = today UTC.

    If the day has a documented `_phantom_cleanup` in brain_daily_ledger.json,
    the anomaly's removed_pnl_delta is added back to total_pnl_usd so the
    summary reflects "trader's decisions" rather than broker accidents — same
    rule the dashboard header uses, keeping the two views coherent.
    """
    if not day:
        day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    events = _load_events()
    # Filter events for that day
    start = datetime.fromisoformat(day + 'T00:00:00+00:00').timestamp()
    end = start + 86400
    day_events = [e for e in events if start <= e.get('ts', 0) < end]
    trades = _group_into_trades(day_events)
    summaries = [_summarize_trade(t) for t in trades]

    total_trades = len(summaries)
    wins = sum(1 for s in summaries if s.get('pnl_usd', 0) > 0)
    losses = sum(1 for s in summaries if s.get('pnl_usd', 0) < 0)
    total_pnl = sum(s.get('pnl_usd', 0) for s in summaries)
    total_avgs = sum(s.get('avgs_count', 0) for s in summaries)
    total_partials = sum(s.get('partials_count', 0) for s in summaries)
    avg_duration = (sum(s.get('duration_min', 0) for s in summaries) / total_trades) if total_trades else 0
    buy_trades = sum(1 for s in summaries if s.get('direction') == 'BUY')
    sell_trades = sum(1 for s in summaries if s.get('direction') == 'SELL')

    # Override total_pnl with broker truth so the trades view header matches
    # the dashboard P&L (= balance delta + phantom adjustment). Per-trade
    # rows keep brain's estimates (they're roughly correct, slippage aside),
    # but the AGGREGATE comes from the same source as the header.
    # `total_pnl_usd_estimated` preserves the brain-events sum for transparency.
    total_pnl_estimated = total_pnl
    anomaly_meta = None
    try:
        import json as _j
        from pathlib import Path as _P
        _common = _P(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files")
        _led_path = _common / "brain_daily_ledger.json"
        _row = {}
        if _led_path.exists():
            with open(_led_path, 'r', encoding='utf-8') as _lf:
                _led = _j.load(_lf)
            _row = (_led.get('days') or {}).get(day) or {}
        _start_bal = float(_row.get('start_balance') or 0)
        _end_bal = float(_row.get('end_balance') or 0)
        _broker_delta = round(_end_bal - _start_bal, 2) if (_start_bal and _end_bal) else None
        _adj = 0.0
        _ph = _row.get('_phantom_cleanup')
        if isinstance(_ph, dict) and 'removed_pnl_delta' in _ph:
            _adj = -float(_ph.get('removed_pnl_delta') or 0)
            anomaly_meta = {
                "removed_pnl_delta": _ph.get('removed_pnl_delta'),
                "adjustment_applied": round(_adj, 2),
                "note": _ph.get('note'),
                "broker_verified": _ph.get('broker_verified', False),
            }
        if _broker_delta is not None:
            total_pnl = round(_broker_delta + _adj, 2)
    except Exception:
        pass

    return {
        'day': day,
        'total_trades': total_trades,
        'wins': wins,
        'losses': losses,
        'winrate': round(wins / total_trades * 100, 1) if total_trades else 0,
        'total_pnl_usd': round(total_pnl, 2),
        'total_pnl_usd_estimated': round(total_pnl_estimated, 2),
        'anomaly': anomaly_meta,
        'total_avgs': total_avgs,
        'total_partials': total_partials,
        'avg_duration_min': round(avg_duration, 1),
        'buy_trades': buy_trades,
        'sell_trades': sell_trades,
        'trades': summaries,
    }


def weekly_summary(end_date=None):
    """Aggregate stats for the 7 days ending on end_date (default today)."""
    if not end_date:
        end_dt = datetime.now(timezone.utc)
    else:
        end_dt = datetime.fromisoformat(end_date + 'T23:59:59+00:00')
    days = [(end_dt - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(7)]
    daily = [daily_summary(d) for d in days]
    total_pnl = sum(d.get('total_pnl_usd', 0) for d in daily)
    total_trades = sum(d.get('total_trades', 0) for d in daily)
    total_wins = sum(d.get('wins', 0) for d in daily)
    return {
        'end_date': end_dt.strftime('%Y-%m-%d'),
        'days': daily,
        'total_pnl_usd': round(total_pnl, 2),
        'total_trades': total_trades,
        'total_wins': total_wins,
        'winrate': round(total_wins / total_trades * 100, 1) if total_trades else 0,
    }


def roll_to_archive(keep_recent=50):
    """Move old events from HISTORY_FILE to ARCHIVE_FILE, keeping only the last N."""
    with _lock:
        try:
            if not os.path.exists(HISTORY_FILE):
                return 0
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            events = data.get('events', [])
            if len(events) <= keep_recent:
                return 0
            to_archive = events[:-keep_recent]
            keep = events[-keep_recent:]
            # Append to archive
            archive_data = {'events': []}
            if os.path.exists(ARCHIVE_FILE):
                try:
                    with open(ARCHIVE_FILE, 'r', encoding='utf-8') as f:
                        archive_data = json.load(f)
                except Exception:
                    pass
            archive_data.setdefault('events', []).extend(to_archive)
            with open(ARCHIVE_FILE, 'w', encoding='utf-8') as f:
                json.dump(archive_data, f, indent=2, ensure_ascii=False)
            # Save trimmed history
            data['events'] = keep
            with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return len(to_archive)
        except Exception:
            return 0


if __name__ == '__main__':
    # CLI usage
    import sys
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == 'today':
            s = daily_summary()
            print(json.dumps(s, indent=2, ensure_ascii=False))
        elif cmd == 'week':
            s = weekly_summary()
            print(json.dumps(s, indent=2, ensure_ascii=False))
        elif cmd == 'list':
            for t in list_trades(since_days=7):
                print(f"{t['started_utc']}  {t['direction']} @ {t['entry_price']}  "
                      f"pnl=${t['pnl_usd']:+.2f}  {t['avgs_count']} avgs  "
                      f"{t['duration_min']}min  {t['source_close']}")
        else:
            print("Usage: python trade_library.py [today|week|list]")
    else:
        s = daily_summary()
        print(f"Today ({s['day']}): {s['total_trades']} trades, "
              f"PnL ${s['total_pnl_usd']:+.2f}, winrate {s['winrate']}%")
