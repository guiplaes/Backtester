#!/usr/bin/env python3
"""News calendar — weekly economic events for XAU/USD context.

Fetches the Forex Factory weekly JSON (free, public, no auth) and
persists a normalized calendar to `brain_news_calendar.json`.

Source:
    https://nfs.faireconomy.media/ff_calendar_thisweek.json

This module is REACTIVE-AGNOSTIC: it doesn't drive any trading decision
on its own. The data here is consumed by:
  · Dashboard widget (read-only display)
  · brain prompts (`upcoming_news` field, future Phase B)
  · Pre-news/post-news LLM cycles (future Phases C-D)

Usage as CLI:
    python news_calendar.py --fetch       # one-shot fetch + persist
    python news_calendar.py --today       # print today's events
    python news_calendar.py --week        # print 7-day events
    python news_calendar.py --daemon      # loop every 6h + Sunday-pinned

Programmatic use (from brain_flow.py / trader_brain.py):
    import news_calendar
    cal = news_calendar.load()                    # dict (cached on disk)
    today = news_calendar.events_today()           # list[dict]
    upcoming = news_calendar.events_upcoming(4)    # next 4 hours
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
COMMON = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files")
CALENDAR_FILE = COMMON / "brain_news_calendar.json"

# ── Source ───────────────────────────────────────────────────────────
FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FETCH_TIMEOUT_S = 12

# ── Currency relevance for XAU/USD trader ───────────────────────────
# USD: PRIMARY (XAU's macro driver — Fed, CPI, NFP, PMI, GDP, ISM)
# EUR/GBP: SECONDARY context (cross-impacts via DXY)
# Others: SKIP (not material to gold price action)
RELEVANT_CURRENCIES = {"USD", "EUR", "GBP"}

# Minimum impact level to keep. "Holiday" filtered out (not actionable).
KEEP_IMPACTS = {"High", "Medium"}  # "Low" optional — see config flag


# ── Logging ──────────────────────────────────────────────────────────
log = logging.getLogger("news_calendar")
if not log.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(asctime)s [news_calendar] %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)


# ── Forex Factory fetch + parse ─────────────────────────────────────
def _fetch_ff_raw() -> list[dict]:
    """Fetch the weekly JSON from Forex Factory. Returns parsed list of events.

    Raises on network/parse error. Caller decides retry policy.
    """
    req = urllib.request.Request(
        FF_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; ClaudeBrain/1.0)"},
    )
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
        data = resp.read().decode("utf-8", errors="replace")
    parsed = json.loads(data)
    if not isinstance(parsed, list):
        raise ValueError(f"FF JSON unexpected shape: {type(parsed).__name__}")
    return parsed


def _parse_ff_event(raw: dict) -> dict | None:
    """Normalize a Forex Factory event into our schema.

    Returns None if the event is filtered out (irrelevant currency/impact).
    """
    currency = (raw.get("country") or "").upper()
    impact = raw.get("impact") or ""
    title = (raw.get("title") or "").strip()
    if currency not in RELEVANT_CURRENCIES:
        return None
    if impact not in KEEP_IMPACTS:
        return None
    if not title:
        return None
    # FF date is ISO 8601 with timezone (usually -04:00 ET or -05:00 ET).
    iso = raw.get("date") or ""
    try:
        # fromisoformat handles `2026-04-30T18:00:00-04:00` directly in 3.11+
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None
    # Normalize to UTC
    dt_utc = dt.astimezone(timezone.utc)
    return {
        "ts": int(dt_utc.timestamp()),
        "iso": dt_utc.isoformat(),
        "currency": currency,
        "impact": impact.upper(),  # HIGH | MEDIUM
        "title": title,
        "forecast": (raw.get("forecast") or "").strip() or None,
        "previous": (raw.get("previous") or "").strip() or None,
        # `actual` is filled post-event by FF — keep it null at fetch time
        # if the source hasn't published it yet. For events in the past,
        # actual may be present.
        "actual": (raw.get("actual") or "").strip() or None,
    }


def fetch_and_persist() -> dict:
    """Fetch fresh calendar from Forex Factory and write to disk.

    Returns the persisted dict (with metadata + events). Does not raise:
    on any failure, returns {"ok": False, "error": ..., "events": []}.
    The disk file is only updated when fetch succeeds, so a transient
    network blip doesn't wipe the previous good calendar.
    """
    try:
        raw_events = _fetch_ff_raw()
    except Exception as e:
        log.warning(f"fetch failed: {e}")
        return {"ok": False, "error": str(e), "events": []}

    events = []
    skipped = 0
    for r in raw_events:
        ev = _parse_ff_event(r)
        if ev is None:
            skipped += 1
            continue
        events.append(ev)
    events.sort(key=lambda e: e["ts"])

    payload = {
        "ok": True,
        "fetched_ts": int(time.time()),
        "fetched_iso": datetime.now(timezone.utc).isoformat(),
        "source": "forexfactory",
        "source_url": FF_URL,
        "n_total_raw": len(raw_events),
        "n_kept": len(events),
        "n_filtered": skipped,
        "currencies_kept": sorted(RELEVANT_CURRENCIES),
        "impacts_kept": sorted(KEEP_IMPACTS),
        "events": events,
    }
    try:
        CALENDAR_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info(f"persisted {len(events)} events ({skipped} filtered) to {CALENDAR_FILE.name}")
    except Exception as e:
        log.warning(f"persist failed: {e}")
        return {"ok": False, "error": f"persist: {e}", "events": events}
    return payload


# ── Read-only access (cached on disk) ───────────────────────────────
def load() -> dict:
    """Return the full persisted calendar or empty stub if not present."""
    if not CALENDAR_FILE.exists():
        return {"ok": False, "fetched_ts": 0, "events": [], "error": "no calendar file"}
    try:
        return json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "fetched_ts": 0, "events": [], "error": str(e)}


def events_today(now_utc: datetime | None = None) -> list[dict]:
    """All events for today (UTC day window), sorted ascending."""
    now = now_utc or datetime.now(timezone.utc)
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    cal = load()
    return [
        e for e in (cal.get("events") or [])
        if day_start.timestamp() <= e["ts"] < day_end.timestamp()
    ]


def events_week(now_utc: datetime | None = None) -> list[dict]:
    """All events in the next 7 days from now, sorted ascending."""
    now = now_utc or datetime.now(timezone.utc)
    cutoff = now + timedelta(days=7)
    cal = load()
    return [
        e for e in (cal.get("events") or [])
        if e["ts"] >= int(now.timestamp()) and e["ts"] <= int(cutoff.timestamp())
    ]


def events_upcoming(hours: int = 4, now_utc: datetime | None = None) -> list[dict]:
    """Events occurring in the next N hours from now (used for brain context)."""
    now = now_utc or datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=hours)
    cal = load()
    return [
        e for e in (cal.get("events") or [])
        if int(now.timestamp()) <= e["ts"] <= int(cutoff.timestamp())
    ]


def freshness_age_s() -> float | None:
    """Seconds since last successful fetch. None if never fetched."""
    cal = load()
    fts = cal.get("fetched_ts") or 0
    if not fts:
        return None
    return max(0.0, time.time() - float(fts))


def needs_refresh(max_age_s: float = 6 * 3600) -> bool:
    """True if last fetch is older than max_age_s, or never fetched."""
    age = freshness_age_s()
    return age is None or age > max_age_s


# ── Daemon mode (background scheduler) ──────────────────────────────
def _seconds_until_next_sunday_2200_utc(now_utc: datetime | None = None) -> float:
    """Seconds until next Sunday 22:00 UTC (just before Asia opens Monday).

    If we're already past Sunday 22:00 UTC, returns the time to NEXT Sunday.
    """
    now = now_utc or datetime.now(timezone.utc)
    # weekday: Monday=0 ... Sunday=6
    days_ahead = (6 - now.weekday()) % 7
    target = (now + timedelta(days=days_ahead)).replace(hour=22, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=7)
    return (target - now).total_seconds()


def daemon_loop(refresh_interval_s: int = 6 * 3600) -> None:
    """Run forever: fetch on startup, then every `refresh_interval_s`, plus
    a pinned Sunday 22:00 UTC refetch (to align with new week).
    """
    log.info("daemon starting")
    fetch_and_persist()  # initial
    while True:
        # Whichever comes first: regular interval or Sunday pin
        sunday_in = _seconds_until_next_sunday_2200_utc()
        sleep_s = min(refresh_interval_s, sunday_in)
        log.info(f"next fetch in {int(sleep_s)}s "
                 f"(interval={refresh_interval_s}s, sunday_pin={int(sunday_in)}s)")
        time.sleep(sleep_s)
        try:
            fetch_and_persist()
        except Exception as e:
            log.error(f"daemon fetch error: {e}")


# ── CLI ──────────────────────────────────────────────────────────────
def _print_event(e: dict) -> None:
    impact_emoji = {"HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🟡"}.get(e.get("impact"), "·")
    iso = e.get("iso", "")
    when = iso[:16].replace("T", " ") + " UTC"
    cur = e.get("currency", "?")
    title = e.get("title", "?")
    fc = e.get("forecast") or "—"
    pv = e.get("previous") or "—"
    ac = e.get("actual") or "—"
    print(f"  {impact_emoji} {when}  {cur}  {title}")
    print(f"      forecast={fc}  previous={pv}  actual={ac}")


def _cli():
    ap = argparse.ArgumentParser(description="News calendar (Forex Factory)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--fetch", action="store_true", help="One-shot fetch + persist")
    g.add_argument("--today", action="store_true", help="Print today's events")
    g.add_argument("--week", action="store_true", help="Print next 7 days")
    g.add_argument("--upcoming", type=int, default=None,
                   help="Print events in next N hours (e.g. --upcoming 4)")
    g.add_argument("--daemon", action="store_true", help="Background loop")
    g.add_argument("--info", action="store_true", help="File freshness + counts")
    args = ap.parse_args()

    if args.fetch:
        r = fetch_and_persist()
        if r.get("ok"):
            print(f"OK fetched {r['n_kept']} events ({r['n_filtered']} filtered)")
        else:
            print(f"FAIL: {r.get('error')}", file=sys.stderr)
            sys.exit(1)
        return

    if args.today:
        evs = events_today()
        print(f"=== TODAY ({len(evs)} events) ===")
        for e in evs:
            _print_event(e)
        return

    if args.week:
        evs = events_week()
        print(f"=== NEXT 7 DAYS ({len(evs)} events) ===")
        for e in evs:
            _print_event(e)
        return

    if args.upcoming is not None:
        evs = events_upcoming(args.upcoming)
        print(f"=== NEXT {args.upcoming}H ({len(evs)} events) ===")
        for e in evs:
            _print_event(e)
        return

    if args.daemon:
        daemon_loop()
        return

    if args.info:
        cal = load()
        age = freshness_age_s()
        print(f"file: {CALENDAR_FILE}")
        print(f"exists: {CALENDAR_FILE.exists()}")
        print(f"fetched_ts: {cal.get('fetched_ts')} (age {age}s)")
        print(f"n_kept: {cal.get('n_kept')}")
        print(f"events: {len(cal.get('events') or [])}")
        return


if __name__ == "__main__":
    _cli()
