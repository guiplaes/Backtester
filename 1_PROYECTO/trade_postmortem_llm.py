#!/usr/bin/env python3
"""LLM-driven trade post-mortem.

For each closed trade:
  1. Assemble all material (decisions + rich snapshots + history events + broker truth via MT5)
  2. Send to DeepSeek reasoner for structured analysis
  3. Persist verdict to brain_postmortems.jsonl

Triggered automatically from signal_state.close_signal() via background thread,
and on-demand via CLI for backfills, weekly aggregation, or single-trade reviews.

Usage:
    python trade_postmortem_llm.py --last
    python trade_postmortem_llm.py --trade-id t_abc123
    python trade_postmortem_llm.py --weekly
    python trade_postmortem_llm.py --backfill --since 7d

Output (per closed trade):
    brain_postmortems.jsonl — append-only, one JSON per line

Output (weekly aggregator):
    logs/postmortem_weekly_<YYYYWww>.md  +  logs/postmortem_weekly_<YYYYWww>.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
COMMON = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files")
DECISIONS_LOG = COMMON / "brain_executor_decisions.jsonl"
SNAPSHOTS_LOG = COMMON / "brain_executor_snapshots.jsonl"
EVENTS_LOG = COMMON / "brain_events_log.jsonl"
HISTORY_LOG = COMMON / "brain_trade_history.json"
NARRATIVES_LOG = COMMON / "brain_trade_narratives.jsonl"
POSTMORTEMS_LOG = COMMON / "brain_postmortems.jsonl"
PROMPT_FILE = BASE_DIR / "prompts" / "postmortem.txt"
WEEKLY_REPORTS_DIR = BASE_DIR / "logs"

# ── I/O helpers ─────────────────────────────────────────────────────
def _read_jsonl(path: Path) -> list[dict]:
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


def _read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _append_jsonl(path: Path, row: dict) -> None:
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass


def _existing_postmortem_trade_ids() -> set[str]:
    return {p.get("trade_id") for p in _read_jsonl(POSTMORTEMS_LOG) if p.get("trade_id")}


# ── MT5 broker-truth fetch ──────────────────────────────────────────
def _broker_truth(trade_id_hint: str | None, started_ts: float | None,
                   ended_ts: float | None) -> dict:
    """Fetch real broker P&L for the trade window via MetaTrader5 API.

    Returns {realized_pnl, deals_count, commission, swap, ticket_ids} or {} on
    failure. The trade may span multiple position_ids (averaging), so we sum
    all deals whose position_ids appear in the trade window.
    """
    if not started_ts or not ended_ts:
        return {}
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            return {}
        utc_from = datetime.fromtimestamp(started_ts - 60, tz=timezone.utc)
        utc_to = datetime.fromtimestamp(ended_ts + 120, tz=timezone.utc)
        deals = mt5.history_deals_get(utc_from, utc_to)
        mt5.shutdown()
        if not deals:
            return {}
        # Filter deals to those overlapping our window — accept all in range.
        total_profit = sum(d.profit for d in deals)
        total_commission = sum(d.commission for d in deals)
        total_swap = sum(d.swap for d in deals)
        position_ids = sorted({d.position_id for d in deals if d.position_id})
        return {
            "realized_pnl_usd": round(total_profit, 2),
            "commission_usd": round(total_commission, 2),
            "swap_usd": round(total_swap, 2),
            "net_pnl_usd": round(total_profit + total_commission + total_swap, 2),
            "deals_count": len(deals),
            "position_ids": position_ids,
        }
    except Exception:
        return {}


# ── Per-trade material assembly ─────────────────────────────────────
def _assemble_trade_material(trade_id: str) -> dict | None:
    """Build the full per-trade material dict the LLM will analyze.

    Joins decisions ↔ snapshots by (trade_id, ts within ±5s window). The
    snapshot capture is best-effort, so a decision may exist without a
    matching snapshot — we include it anyway with snapshot=None.
    """
    decisions = [d for d in _read_jsonl(DECISIONS_LOG) if d.get("trade_id") == trade_id]
    if not decisions:
        return None

    snapshots = [s for s in _read_jsonl(SNAPSHOTS_LOG) if s.get("trade_id") == trade_id]
    snapshots_by_ts = {round(s.get("ts", 0), 0): s for s in snapshots}

    history = _read_json(HISTORY_LOG, {})
    events_in_history = history if isinstance(history, list) else (history.get("events") or [])
    trade_events = [e for e in events_in_history
                    if e.get("type") == "OPEN" or
                       (e.get("ticket") and e.get("ticket") in
                        _trade_tickets_for_trade(trade_events_log_path=EVENTS_LOG, trade_id=trade_id))]
    # Simpler: filter by trade_id when available, else leave as-is
    trade_events = [e for e in events_in_history if _event_in_trade(e, trade_id, decisions)]

    started_ts = min((d.get("ts", 0) for d in decisions), default=0)
    ended_ts = max((d.get("ts", 0) for d in decisions), default=0)
    # Try to find OPEN/FULL_CLOSE events for tighter timestamps
    for e in trade_events:
        ts = e.get("ts", 0)
        if e.get("type") == "OPEN" and ts:
            started_ts = min(started_ts, ts) if started_ts else ts
        if e.get("type") == "FULL_CLOSE" and ts:
            ended_ts = max(ended_ts, ts)

    broker = _broker_truth(trade_id, started_ts, ended_ts)

    # Build decisions+snapshot pairs (chronological).
    # Cost-reduction: full snapshots are 2-5 KB each. A 5-decision trade
    # repeats 5× the same payload (zones, market_context, external, htf...)
    # → 10-25 KB of duplicated context. We keep FULL snapshots only for the
    # first and last decisions; middle decisions get a compact "delta" view
    # (just call_meta + price + signal state). The LLM has zones+structure
    # from the first snapshot; intermediate snapshots only need to show
    # what's CHANGED for the analysis to be coherent.
    decisions_sorted = sorted(decisions, key=lambda d: d.get("ts", 0))
    n_decisions = len(decisions_sorted)

    def _compact_snapshot(snap_payload):
        """Keep only the non-redundant volatile fields for middle decisions."""
        if not snap_payload:
            return None
        # market_context contains price, RSI, ATR, last bars — useful even
        # for middle decisions to see how price evolved between calls.
        mc = (snap_payload.get("market_context") or {}).copy()
        # Drop bars history — first snapshot already has it; middle ones
        # would be redundant (LLM can interpolate from price + extremes).
        mc.pop("last_30_m5_candles", None)
        return {
            "_call_meta": snap_payload.get("_call_meta"),
            "market_context": mc,
            "signal": snap_payload.get("signal"),
            "risk": snap_payload.get("risk"),
            "trade_context": snap_payload.get("trade_context"),
            "trigger_events": snap_payload.get("trigger_events"),
        }

    paired = []
    for idx, d in enumerate(decisions_sorted):
        ts_round = round(d.get("ts", 0), 0)
        # Find closest snapshot within ±5s
        snap = snapshots_by_ts.get(ts_round)
        if not snap:
            for delta in (1, -1, 2, -2, 3, -3, 4, -4, 5, -5):
                snap = snapshots_by_ts.get(ts_round + delta)
                if snap:
                    break
        snap_payload = snap.get("payload") if snap else None
        is_endpoint = (idx == 0 or idx == n_decisions - 1)
        if is_endpoint:
            ctx_snap = snap_payload
        else:
            ctx_snap = _compact_snapshot(snap_payload)
        paired.append({
            "decision": {
                "ts": d.get("ts"),
                "iso": d.get("iso"),
                "action": d.get("action"),
                "confidence": d.get("confidence"),
                "mental_state": d.get("mental_state"),
                "thesis": d.get("thesis"),
                # Cap reasoning to 1000 chars (was 2000) — middle decisions
                # don't need full novel; key reasoning is in thesis + action.
                "reasoning_full": (d.get("reasoning_full") or "")[:(2000 if is_endpoint else 1000)],
                "next_plan": d.get("next_plan"),
                "invalidation_condition": d.get("invalidation_condition"),
                "trigger_events": d.get("trigger_events"),
                "order": d.get("order"),
                "close_pct": d.get("close_pct"),
            },
            "context_snapshot": ctx_snap,
            "snapshot_compacted": (not is_endpoint and snap_payload is not None),
        })

    # Compose summary
    open_event = next((e for e in trade_events if e.get("type") == "OPEN"), None)
    close_events = [e for e in trade_events if e.get("type") == "FULL_CLOSE"]
    avg_count = sum(1 for e in trade_events if e.get("type") == "AVERAGE")
    partial_count = sum(1 for e in trade_events if e.get("type") == "PARTIAL_CLOSE")
    # Safe direction extraction — `response_raw.order` may be missing, a list,
    # or an unexpected shape on legacy records.
    direction = (open_event or {}).get("direction")
    if not direction and decisions_sorted:
        try:
            _resp = decisions_sorted[0].get("response_raw") or {}
            _order = _resp.get("order") if isinstance(_resp, dict) else None
            if isinstance(_order, dict):
                direction = _order.get("type")
        except Exception:
            direction = None
    direction = direction or "?"
    entry = (open_event or {}).get("price") or 0
    duration_min = round((ended_ts - started_ts) / 60.0, 1) if ended_ts and started_ts else 0
    brain_estimated_pnl = round(sum(float(e.get("pnl_delta", 0) or 0)
                                    for e in trade_events
                                    if e.get("type") in ("PARTIAL_CLOSE", "FULL_CLOSE")), 2)

    return {
        "trade_id": trade_id,
        "trade_summary": {
            "direction": direction,
            "entry_price": entry,
            "duration_min": duration_min,
            "avg_count": avg_count,
            "partial_count": partial_count,
            "decisions_count": len(decisions_sorted),
            "brain_estimated_pnl_usd": brain_estimated_pnl,
        },
        "broker_truth": broker,
        "decisions_with_context": paired,
        "trade_events": trade_events,
        "started_ts": started_ts,
        "ended_ts": ended_ts,
    }


def _trade_tickets_for_trade(trade_events_log_path: Path, trade_id: str) -> set[int]:
    """Collect ticket numbers associated with a trade_id (best-effort)."""
    out = set()
    for e in _read_jsonl(trade_events_log_path):
        if e.get("trade_id") == trade_id and e.get("ticket"):
            out.add(e["ticket"])
    return out


def _event_in_trade(event: dict, trade_id: str, decisions: list[dict]) -> bool:
    """Heuristic: an event belongs to this trade if either:
       (a) ticket appears in decisions/events_log for the trade_id, or
       (b) ts is within decision-time window ±60s
    """
    if not decisions:
        return False
    ts = event.get("ts", 0)
    if not ts:
        return False
    first_ts = min(d.get("ts", 0) for d in decisions) - 60
    last_ts = max(d.get("ts", 0) for d in decisions) + 300
    return first_ts <= ts <= last_ts


# ── LLM call ─────────────────────────────────────────────────────────
def _call_llm(material: dict) -> dict | None:
    """Send material to DeepSeek reasoner and parse the structured response.

    Reuses _call_deepseek from trader_brain (so model selection, retries,
    fallback, and inflight-state tracking are consistent across the system).
    """
    try:
        sys.path.insert(0, str(BASE_DIR))
        from trader_brain import _call_deepseek
        prompt = _build_prompt(material)
        if not PROMPT_FILE.exists():
            return None
        system_prompt = PROMPT_FILE.read_text(encoding="utf-8")
        # Cost optimisation 2026-05-01: postmortem is offline analysis, no
        # latency pressure → V4-Flash (3× cheaper than promo Pro, 12× cheaper
        # post-promo) with reasoning=False (skip 8K reasoning tokens that
        # bloated cost). Quality drop is acceptable for retrospective audit.
        return _call_deepseek(prompt, system_prompt, label="POSTMORTEM",
                              reasoning=False, model="deepseek-v4-flash")
    except Exception as e:
        print(f"[postmortem] LLM call failed: {e}", file=sys.stderr)
        return None


def _build_prompt(material: dict) -> str:
    """Compact JSON-serialize the material for the LLM prompt body."""
    return (
        "Trade material per analitzar (JSON):\n\n"
        + json.dumps(material, ensure_ascii=False, indent=2, default=str)
    )


# ── Public entry points ─────────────────────────────────────────────
def run(trade_id: str, force: bool = False) -> dict | None:
    """Run post-mortem for a single trade. Returns the verdict dict, or None on failure.

    Idempotent: if a post-mortem for this trade_id already exists, returns the
    existing one unless force=True.
    """
    if not force and trade_id in _existing_postmortem_trade_ids():
        existing = [p for p in _read_jsonl(POSTMORTEMS_LOG) if p.get("trade_id") == trade_id]
        return existing[-1] if existing else None

    material = _assemble_trade_material(trade_id)
    if not material:
        return None
    verdict = _call_llm(material)
    if not verdict:
        return None

    row = {
        "ts": time.time(),
        "iso": datetime.now(timezone.utc).isoformat(),
        "trade_id": trade_id,
        "duration_min": material["trade_summary"]["duration_min"],
        "broker_pnl_usd": (material.get("broker_truth") or {}).get("net_pnl_usd"),
        "brain_estimated_pnl_usd": material["trade_summary"]["brain_estimated_pnl_usd"],
        "decisions_count": material["trade_summary"]["decisions_count"],
        "verdict": verdict.get("verdict"),
        "process_score": verdict.get("process_score"),
        "key_wins": verdict.get("key_wins") or [],
        "key_mistakes": verdict.get("key_mistakes") or [],
        "pattern_tags": verdict.get("pattern_tags") or [],
        "lesson": verdict.get("lesson"),
        "context_quality": verdict.get("context_quality"),
        "action_summary": verdict.get("action_summary"),
        "raw_llm_response": verdict,
    }
    _append_jsonl(POSTMORTEMS_LOG, row)
    return row


def run_async(trade_id: str) -> None:
    """Fire-and-forget background thread runner. Used from close_signal."""
    import threading
    threading.Thread(
        target=lambda: run(trade_id),
        daemon=True,
        name=f"postmortem_{trade_id[:8]}"
    ).start()


def weekly_aggregate(days: int = 7) -> dict:
    """Aggregate post-mortems over the last N days into a weekly summary dict.

    Output is a structured report ready for serialization to markdown / JSON.
    Produces:
      - trades by verdict
      - foreseeable_loss_rate (key health metric)
      - top pattern_tags (frequency)
      - top lessons (frequency or repetition signal)
      - mean process_score
      - sample size warning if N < 5
    """
    cutoff = time.time() - days * 86400
    pms = [p for p in _read_jsonl(POSTMORTEMS_LOG) if (p.get("ts") or 0) >= cutoff]
    n = len(pms)
    if not pms:
        return {
            "period_days": days,
            "n_trades": 0,
            "warning": "No post-mortems in window. Sample size 0.",
            "verdict_counts": {},
            "top_patterns": [],
            "top_lessons": [],
        }

    verdict_counts = Counter(p.get("verdict") for p in pms if p.get("verdict"))
    pattern_counter = Counter()
    for p in pms:
        for tag in (p.get("pattern_tags") or []):
            pattern_counter[tag] += 1
    top_patterns = pattern_counter.most_common(10)

    # Lessons: aggregate by simple text similarity (exact match for now).
    lesson_counter = Counter()
    lesson_examples = defaultdict(list)
    for p in pms:
        ls = (p.get("lesson") or "").strip()
        if ls:
            # Use first 60 chars as bucket key (rough deduplication)
            bucket = ls[:60].lower()
            lesson_counter[bucket] += 1
            lesson_examples[bucket].append({
                "trade_id": p.get("trade_id"),
                "verdict": p.get("verdict"),
                "lesson": ls,
            })
    top_lessons_keys = lesson_counter.most_common(10)
    top_lessons = [
        {
            "frequency": cnt,
            "lesson": lesson_examples[bk][0]["lesson"],
            "trade_ids": [ex["trade_id"] for ex in lesson_examples[bk][:3]],
        }
        for bk, cnt in top_lessons_keys
    ]

    process_scores = [float(p.get("process_score") or 0) for p in pms if p.get("process_score") is not None]
    mean_score = round(sum(process_scores) / len(process_scores), 2) if process_scores else 0

    foreseeable_losses = sum(1 for p in pms if p.get("verdict") == "LOSS_FORESEEABLE")
    foreseeable_loss_rate = round(foreseeable_losses / n * 100.0, 1) if n else 0

    win_lucky = sum(1 for p in pms if p.get("verdict") == "WIN_LUCKY")
    win_lucky_rate = round(win_lucky / n * 100.0, 1) if n else 0

    return {
        "period_days": days,
        "n_trades": n,
        "warning": f"Sample size {n} — pattern signals may be unreliable" if n < 5 else None,
        "verdict_counts": dict(verdict_counts),
        "mean_process_score": mean_score,
        "foreseeable_loss_rate_pct": foreseeable_loss_rate,
        "win_lucky_rate_pct": win_lucky_rate,
        "top_patterns": [{"tag": t, "frequency": c} for t, c in top_patterns],
        "top_lessons": top_lessons,
        "trades": [
            {
                "trade_id": p.get("trade_id"),
                "verdict": p.get("verdict"),
                "process_score": p.get("process_score"),
                "broker_pnl_usd": p.get("broker_pnl_usd"),
                "duration_min": p.get("duration_min"),
                "action_summary": (p.get("raw_llm_response") or {}).get("action_summary"),
            }
            for p in sorted(pms, key=lambda x: x.get("ts", 0))
        ],
    }


def write_weekly_report(agg: dict, out_dir: Path = None) -> tuple[Path, Path]:
    """Persist weekly aggregator output to markdown + JSON. Returns paths."""
    out_dir = out_dir or WEEKLY_REPORTS_DIR
    out_dir.mkdir(exist_ok=True)
    iso_year, iso_week, _ = datetime.now(timezone.utc).isocalendar()
    week_key = f"{iso_year}W{iso_week:02d}"
    md_path = out_dir / f"postmortem_weekly_{week_key}.md"
    json_path = out_dir / f"postmortem_weekly_{week_key}.json"

    # JSON dump (raw structured data)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(agg, f, ensure_ascii=False, indent=2)

    # Markdown rendered (human-readable)
    lines = []
    lines.append(f"# Weekly Post-Mortem — {week_key}")
    lines.append("")
    lines.append(f"**Period:** Last {agg['period_days']} days · **Trades reviewed:** {agg['n_trades']}")
    if agg.get("warning"):
        lines.append("")
        lines.append(f"> ⚠ {agg['warning']}")
    lines.append("")
    lines.append("## Headline Stats")
    lines.append("")
    lines.append(f"- **Mean process score:** {agg.get('mean_process_score')}/10")
    lines.append(f"- **Foreseeable loss rate:** {agg.get('foreseeable_loss_rate_pct')}%")
    lines.append(f"- **Win-lucky rate:** {agg.get('win_lucky_rate_pct')}%")
    lines.append("")
    lines.append("## Verdicts")
    lines.append("")
    for v, c in (agg.get("verdict_counts") or {}).items():
        lines.append(f"- `{v}`: {c}")
    lines.append("")
    lines.append("## Top patterns (recurring tags)")
    lines.append("")
    for p in agg.get("top_patterns") or []:
        lines.append(f"- **{p['tag']}** × {p['frequency']}")
    lines.append("")
    lines.append("## Top lessons (most actionable)")
    lines.append("")
    for i, le in enumerate((agg.get("top_lessons") or [])[:5], 1):
        lines.append(f"{i}. **(×{le['frequency']})** {le['lesson']}")
        lines.append(f"   _Trades: {', '.join(le['trade_ids'][:3])}_")
    lines.append("")
    lines.append("## Per-trade (chronological)")
    lines.append("")
    for t in agg.get("trades") or []:
        verdict = t.get("verdict") or "?"
        emoji = {
            "WIN_GOOD_PROCESS": "✅",
            "WIN_LUCKY": "🍀",
            "LOSS_UNAVOIDABLE": "🟡",
            "LOSS_FORESEEABLE": "❌",
            "SCRATCHED": "➖",
        }.get(verdict, "·")
        pnl = t.get("broker_pnl_usd")
        pnl_str = f"{'+' if (pnl or 0) >= 0 else ''}${pnl}" if pnl is not None else "?"
        lines.append(f"- {emoji} `{t.get('trade_id')[:14]}` · {verdict} · "
                     f"score {t.get('process_score')} · "
                     f"P&L {pnl_str} · {t.get('duration_min')}min")
        if t.get("action_summary"):
            lines.append(f"  - {t['action_summary']}")
    lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path, json_path


# ── CLI ──────────────────────────────────────────────────────────────
def _cli():
    ap = argparse.ArgumentParser(description="LLM-driven trade post-mortem")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--last", type=int, nargs="?", const=1,
                   help="Process the N most recent closed trades (default 1)")
    g.add_argument("--trade-id", type=str, help="Process a specific trade_id")
    g.add_argument("--weekly", action="store_true",
                   help="Aggregate post-mortems over the last 7 days into report+json")
    g.add_argument("--backfill", action="store_true",
                   help="Process all trades in --since window that lack a post-mortem")
    ap.add_argument("--since", type=str, default="7d",
                    help="Window for --backfill (e.g. 7d, 30d). Default 7d")
    ap.add_argument("--force", action="store_true",
                    help="Re-run post-mortem even if one already exists")
    args = ap.parse_args()

    if args.weekly:
        agg = weekly_aggregate(days=int(args.since.rstrip("d") or 7))
        md, js = write_weekly_report(agg)
        print(f"OK — weekly report:")
        print(f"  markdown: {md}")
        print(f"  json:     {js}")
        print(f"  trades:   {agg['n_trades']}")
        if agg.get("warning"):
            print(f"  ⚠ {agg['warning']}")
        return

    if args.trade_id:
        result = run(args.trade_id, force=args.force)
        if result:
            print(f"OK — trade {args.trade_id}: {result.get('verdict')} · score {result.get('process_score')}")
        else:
            print(f"FAIL — could not produce post-mortem for {args.trade_id}", file=sys.stderr)
            sys.exit(1)
        return

    if args.last:
        # Find the N most recent trade_ids that have decisions
        all_trade_ids_in_order = []
        seen = set()
        for d in reversed(_read_jsonl(DECISIONS_LOG)):
            tid = d.get("trade_id")
            if tid and tid not in seen:
                seen.add(tid)
                all_trade_ids_in_order.append(tid)
            if len(all_trade_ids_in_order) >= args.last:
                break
        for tid in all_trade_ids_in_order:
            r = run(tid, force=args.force)
            if r:
                print(f"  {tid}: {r.get('verdict')} · score {r.get('process_score')}")
            else:
                print(f"  {tid}: FAIL")
        return

    if args.backfill:
        days = int(args.since.rstrip("d") or 7)
        cutoff = time.time() - days * 86400
        candidates = set()
        for d in _read_jsonl(DECISIONS_LOG):
            if (d.get("ts") or 0) >= cutoff and d.get("trade_id"):
                candidates.add(d["trade_id"])
        existing = _existing_postmortem_trade_ids()
        todo = sorted(candidates - existing)
        print(f"Backfill: {len(todo)} trades pending in last {days}d")
        for tid in todo:
            r = run(tid, force=False)
            if r:
                print(f"  {tid}: {r.get('verdict')}")
            else:
                print(f"  {tid}: FAIL")


if __name__ == "__main__":
    _cli()
