#!/usr/bin/env python3
"""
journal_review — Render brain_journal events as a human-readable trade review.

Usage:
  python journal_review.py --last              # most recent trade
  python journal_review.py --last 5            # last 5 trades (table)
  python journal_review.py --trade-id t_xxx    # specific trade
  python journal_review.py --since 2d          # last 2 days, all events tagged to trades
  python journal_review.py --rejections        # only order_rejected events (last 24h)
  python journal_review.py --news              # only news_observed (last 7d)
  python journal_review.py --stats             # aggregate counters

Output: markdown to stdout. Pipe to a file or `less` if needed.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Make sibling modules importable when run directly
sys.path.insert(0, str(Path(__file__).resolve().parent))

import brain_journal as bj


# ── helpers ──────────────────────────────────────────────────────────

def _parse_since(s: str) -> float:
    """'2d', '6h', '30m' → seconds offset; 'YYYY-MM-DD' → epoch."""
    s = s.strip().lower()
    m = re.match(r"^(\d+)\s*([dhm])$", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        mult = {"d": 86400, "h": 3600, "m": 60}[unit]
        return time.time() - n * mult
    try:
        dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        raise SystemExit(f"can't parse --since '{s}'. use 2d / 6h / 30m / YYYY-MM-DD")


def _short_utc(iso: str | None) -> str:
    if not iso:
        return "?"
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%m-%d %H:%M:%S")
    except Exception:
        return iso[:19]


def _fmt_price(v) -> str:
    try:
        return f"{float(v):.2f}"
    except Exception:
        return "?"


def _fmt_pnl(v) -> str:
    try:
        f = float(v)
        return f"{f:+.2f}$"
    except Exception:
        return ""


# ── renderers ────────────────────────────────────────────────────────

def render_trade(trade_id: str) -> str:
    events = bj.read_trade(trade_id)
    if not events:
        return f"# {trade_id}\n\n_no events found_\n"

    out = []
    first = events[0]
    last = events[-1]
    direction = None
    entry_price = None
    total_pnl = 0.0
    n_avg = 0
    n_partial = 0
    n_decisions = 0
    n_rejections = 0
    closed = False

    for e in events:
        t = e.get("type")
        p = e.get("payload") or {}
        if t == "trade_opened":
            direction = direction or p.get("direction")
            entry_price = entry_price or p.get("price")
        elif t == "trade_averaged":
            n_avg += 1
        elif t in ("trade_partial", "trade_closed", "trade_signal_closed"):
            if t == "trade_partial":
                n_partial += 1
            if t in ("trade_closed", "trade_signal_closed"):
                closed = True
            try:
                total_pnl += float(p.get("pnl_delta") or 0)
            except Exception:
                pass
        elif t == "decision_executor":
            n_decisions += 1
        elif t == "order_rejected":
            n_rejections += 1

    # Header
    out.append(f"# {trade_id}")
    out.append("")
    out.append(
        f"**{direction or '?'}** · started `{_short_utc(first.get('utc'))}` "
        f"· {'CLOSED' if closed else 'OPEN'} "
        f"· last event `{_short_utc(last.get('utc'))}`"
    )
    if entry_price is not None:
        out.append(
            f"entry `{_fmt_price(entry_price)}` · {n_avg} avg(s) · "
            f"{n_partial} partial(s) · {n_decisions} decision(s) · "
            f"{n_rejections} rejection(s) · pnl **{_fmt_pnl(total_pnl)}**"
        )
    out.append("")

    # Timeline
    out.append("## Timeline")
    out.append("")
    for e in events:
        t = e.get("type", "?")
        when = _short_utc(e.get("utc"))
        src = e.get("source", "?")
        snap = e.get("snapshot") or {}
        p = e.get("payload") or {}

        ctx_bits = []
        if "price" in snap:
            ctx_bits.append(f"px={snap['price']}")
        if "dd_pct" in snap:
            ctx_bits.append(f"DD={snap['dd_pct']:.2f}%")
        if "session" in snap:
            ctx_bits.append(f"sess={snap['session']}")
        if isinstance(snap.get("news"), dict) and snap["news"].get("blocking"):
            ctx_bits.append("⛔NEWS")
        ctx = " · ".join(ctx_bits)

        if t == "signal_received":
            out.append(f"- `{when}` **SIGNAL_IN** ({src}/{p.get('channel','?')}) — {p.get('text','')[:120]}  · {ctx}")
        elif t == "signal_filter_blocked":
            out.append(f"- `{when}` ⛔ **BLOCKED** gate=`{p.get('gate')}` reason: {p.get('news_text','')[:80]}  · {ctx}")
        elif t == "news_observed":
            out.append(f"- `{when}` 📰 **NEWS** {p.get('importance')} @ {_short_utc(p.get('event_time'))} — {p.get('text','')[:80]}")
        elif t == "trade_opened":
            out.append(f"- `{when}` 🟢 **OPEN** {p.get('direction')} lot={p.get('lot')} @ {_fmt_price(p.get('price'))}  · {ctx}")
        elif t == "trade_averaged":
            out.append(f"- `{when}` ➕ **AVG** lot={p.get('lot')} @ {_fmt_price(p.get('price'))}  · {ctx}  · _{p.get('reason','')[:120]}_")
        elif t == "trade_partial":
            out.append(f"- `{when}` 💰 **PARTIAL** {_fmt_pnl(p.get('pnl_delta'))} @ {_fmt_price(p.get('price'))}  · _{p.get('reason','')[:120]}_")
        elif t in ("trade_closed", "trade_signal_closed"):
            out.append(f"- `{when}` 🔴 **CLOSE** {_fmt_pnl(p.get('pnl_delta'))} @ {_fmt_price(p.get('price'))} — {p.get('reason','')[:120]}")
        elif t == "decision_executor":
            action = p.get("action", "?")
            conf = p.get("confidence", 0)
            try:
                conf_pct = f"{float(conf)*100:.0f}%"
            except Exception:
                conf_pct = "?"
            mental = p.get("mental_state", "?")
            thesis = (p.get("thesis") or "")[:200]
            reasoning = (p.get("reasoning") or "")[:400]
            out.append(f"- `{when}` 🧠 **EXEC** `{action}` ({conf_pct}/{mental})  · {ctx}")
            if thesis:
                out.append(f"  - thesis: _{thesis}_")
            if reasoning:
                out.append(f"  - reasoning: {reasoning}")
            inv = p.get("invalidation_condition")
            if isinstance(inv, dict) and inv.get("text"):
                out.append(f"  - invalidation: {inv['text'][:160]}")
            if p.get("validator_rejection"):
                vr = p["validator_rejection"]
                out.append(f"  - ⚠️ validator: {vr.get('code')} — {vr.get('detail','')[:120]}")
        elif t == "order_rejected":
            out.append(f"- `{when}` 🚫 **REJECT** ({src}) {p.get('code')} — {p.get('detail','')[:120]}  (orig action: `{p.get('original_action')}`)")
        elif t == "decision_indicator":
            out.append(f"- `{when}` 🔭 IND bias={p.get('bias','?')} regime={p.get('regime','?')}")
        else:
            short = ", ".join(f"{k}={str(v)[:30]}" for k, v in list(p.items())[:3])
            out.append(f"- `{when}` {t} ({src}) — {short}")

    return "\n".join(out) + "\n"


def render_table(summaries: list[dict]) -> str:
    if not summaries:
        return "_no trades found_\n"
    out = []
    out.append("| trade_id | direction | started (UTC) | entry | avgs | partials | decisions | rejections | pnl | status |")
    out.append("|---|---|---|---|---|---|---|---|---|---|")
    for s in summaries:
        out.append(
            f"| `{s['trade_id']}` | {s.get('direction','?')} | "
            f"{_short_utc(s.get('started_utc'))} | "
            f"{_fmt_price(s.get('entry_price'))} | "
            f"{s['n_avgs']} | {s['n_partials']} | {s['n_decisions']} | "
            f"{s['n_rejections']} | {_fmt_pnl(s['total_pnl'])} | "
            f"{'CLOSED' if s.get('closed') else 'OPEN'} |"
        )
    return "\n".join(out) + "\n"


def render_rejections(since_ts: float) -> str:
    evs = bj.iter_events(since_ts=since_ts, types=["order_rejected"])
    if not evs:
        return "_no rejections in window_\n"
    out = ["# Rejections", ""]
    for e in evs:
        p = e.get("payload") or {}
        snap = e.get("snapshot") or {}
        out.append(
            f"- `{_short_utc(e.get('utc'))}` **{p.get('code')}** "
            f"(orig: {p.get('original_action')}) — {p.get('detail','')[:160]}"
        )
        if "price" in snap:
            out.append(f"  - context: px={snap['price']} DD={snap.get('dd_pct',0):.2f}% trade=`{e.get('trade_id') or '-'}`")
    return "\n".join(out) + "\n"


def render_news(since_ts: float) -> str:
    evs = bj.iter_events(since_ts=since_ts, types=["news_observed", "signal_filter_blocked"])
    if not evs:
        return "_no news in window_\n"
    out = ["# News", ""]
    for e in evs:
        p = e.get("payload") or {}
        if e.get("type") == "news_observed":
            out.append(f"- `{_short_utc(e.get('utc'))}` 📰 {p.get('importance')} @ {_short_utc(p.get('event_time'))} — {p.get('text','')[:120]}")
        else:
            out.append(f"- `{_short_utc(e.get('utc'))}` ⛔ blocked entry `{p.get('direction')}` from {p.get('channel')}: {p.get('news_text','')[:80]}")
    return "\n".join(out) + "\n"


def render_stats(since_ts: float) -> str:
    evs = bj.iter_events(since_ts=since_ts)
    if not evs:
        return "_no events in window_\n"
    counts: dict[str, int] = {}
    for e in evs:
        t = e.get("type", "?")
        counts[t] = counts.get(t, 0) + 1
    out = ["# Stats", "", f"window: since {_short_utc(datetime.fromtimestamp(since_ts, tz=timezone.utc).isoformat())}", ""]
    out.append("| event type | count |")
    out.append("|---|---|")
    for t, c in sorted(counts.items(), key=lambda x: -x[1]):
        out.append(f"| {t} | {c} |")
    return "\n".join(out) + "\n"


# ── main ─────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Brain journal review tool")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--last", nargs="?", const=1, type=int,
                   help="last N trades (default 1, full render). N>1 = table.")
    g.add_argument("--trade-id", type=str, help="render specific trade_id")
    g.add_argument("--rejections", action="store_true", help="list order rejections")
    g.add_argument("--news", action="store_true", help="list news observations + blocked entries")
    g.add_argument("--stats", action="store_true", help="aggregate counts")
    ap.add_argument("--since", type=str, default="7d",
                   help="time window for filters (default 7d). 2d/6h/30m or YYYY-MM-DD")
    args = ap.parse_args()

    since_ts = _parse_since(args.since)

    if args.trade_id:
        print(render_trade(args.trade_id))
    elif args.last is not None:
        summaries = bj.list_trades(since_days=30, limit=args.last)
        if args.last == 1 and summaries:
            print(render_trade(summaries[0]["trade_id"]))
        else:
            print(render_table(summaries))
    elif args.rejections:
        print(render_rejections(since_ts))
    elif args.news:
        print(render_news(since_ts))
    elif args.stats:
        print(render_stats(since_ts))


if __name__ == "__main__":
    main()
