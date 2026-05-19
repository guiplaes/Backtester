#!/usr/bin/env python3
"""Daily + Weekly + Monthly Ledger — persistent P&L accounting.

Files written to Common\\Files:
  - brain_daily_ledger.json   → per-day history + rolled-up weeks/months + totals
  - brain_daily.json          → today-only snapshot (legacy dashboard reader)

Day record (keyed by 'YYYY-MM-DD'):
    start_balance : float  — balance at first tick of the UTC day (NEVER overwritten once set)
    end_balance   : float  — most recent balance seen
    start_ts / last_ts
    pnl_day_usd   : float  — end_balance - start_balance
    pnl_day_pct   : float  — pnl_day_usd / start_balance * 100
    trades_count / wins / losses / avgs / partials / trades_pnl_usd

Week record (keyed by 'YYYY-Www'):
    start_balance (of Monday) / end_balance (of Sunday) / pnl_week_usd / pnl_week_pct / days

Month record (keyed by 'YYYY-MM'):
    start_balance (of day 1) / end_balance (of last day) / pnl_month_usd / pnl_month_pct / weeks / days

Totals:
    project_start_balance / project_start_date
    accumulated_pnl_usd / accumulated_pnl_pct
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

COMMON = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files")
LEDGER_FILE = COMMON / "brain_daily_ledger.json"
TODAY_FILE = COMMON / "brain_daily.json"

_lock = threading.Lock()


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _iso_week(date_str: str) -> str:
    """Return ISO week key 'YYYY-Www' for a YYYY-MM-DD string."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _month_key(date_str: str) -> str:
    return date_str[:7]  # YYYY-MM


def _load() -> dict:
    if not LEDGER_FILE.exists():
        return {
            "project_start_balance": None,
            "project_start_date": None,
            "project_start_ts": None,
            "days": {},
            "weeks": {},
            "months": {},
            "accumulated_pnl_usd": 0.0,
            "accumulated_pnl_pct": 0.0,
        }
    try:
        data = json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
        data.setdefault("weeks", {})
        data.setdefault("months", {})
        return data
    except Exception:
        return {
            "project_start_balance": None, "project_start_date": None,
            "project_start_ts": None, "days": {}, "weeks": {}, "months": {},
            "accumulated_pnl_usd": 0.0, "accumulated_pnl_pct": 0.0,
        }


def _save(data: dict) -> None:
    try:
        LEDGER_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def _rollup_week(data: dict, week_key: str) -> None:
    """Recompute the weekly aggregate from its constituent days."""
    days = data.get("days", {})
    week_days = {d: v for d, v in days.items() if _iso_week(d) == week_key}
    if not week_days:
        return
    ordered = sorted(week_days.items())
    first_day = ordered[0][1]
    last_day = ordered[-1][1]
    start_bal = float(first_day.get("start_balance", 0) or 0)
    end_bal = float(last_day.get("end_balance", 0) or 0)
    pnl = round(end_bal - start_bal, 2)
    pct = round(pnl / start_bal * 100, 3) if start_bal > 0 else 0.0
    data.setdefault("weeks", {})[week_key] = {
        "start_date": ordered[0][0],
        "end_date": ordered[-1][0],
        "start_balance": start_bal,
        "end_balance": end_bal,
        "pnl_usd": pnl,
        "pnl_pct": pct,
        "days": [d for d, _ in ordered],
        "trades_count": sum(int(v.get("trades_count", 0) or 0) for _, v in ordered),
        "wins": sum(int(v.get("wins", 0) or 0) for _, v in ordered),
        "losses": sum(int(v.get("losses", 0) or 0) for _, v in ordered),
    }


def _rollup_month(data: dict, month_key: str) -> None:
    days = data.get("days", {})
    month_days = {d: v for d, v in days.items() if _month_key(d) == month_key}
    if not month_days:
        return
    ordered = sorted(month_days.items())
    first_day = ordered[0][1]
    last_day = ordered[-1][1]
    start_bal = float(first_day.get("start_balance", 0) or 0)
    end_bal = float(last_day.get("end_balance", 0) or 0)
    pnl = round(end_bal - start_bal, 2)
    pct = round(pnl / start_bal * 100, 3) if start_bal > 0 else 0.0
    weeks = sorted({_iso_week(d) for d, _ in ordered})
    data.setdefault("months", {})[month_key] = {
        "start_date": ordered[0][0],
        "end_date": ordered[-1][0],
        "start_balance": start_bal,
        "end_balance": end_bal,
        "pnl_usd": pnl,
        "pnl_pct": pct,
        "weeks": weeks,
        "days_count": len(ordered),
        "trades_count": sum(int(v.get("trades_count", 0) or 0) for _, v in ordered),
        "wins": sum(int(v.get("wins", 0) or 0) for _, v in ordered),
        "losses": sum(int(v.get("losses", 0) or 0) for _, v in ordered),
    }


def _recompute_totals(data: dict) -> None:
    psb = data.get("project_start_balance")
    days = data.get("days", {})
    if not days:
        data["accumulated_pnl_usd"] = 0.0
        data["accumulated_pnl_pct"] = 0.0
        return
    latest_date = max(days.keys())
    latest_end = float(days[latest_date].get("end_balance", 0) or 0)
    if psb and psb > 0:
        data["accumulated_pnl_usd"] = round(latest_end - float(psb), 2)
        data["accumulated_pnl_pct"] = round((latest_end - float(psb)) / float(psb) * 100, 3)
    else:
        data["accumulated_pnl_usd"] = 0.0
        data["accumulated_pnl_pct"] = 0.0


def update(balance: float) -> dict:
    """Record current balance against today's bucket.

    Key invariant: once a day's `start_balance` is set, it is NEVER overwritten
    on subsequent calls — only `end_balance` is updated. This means restarts,
    crashes, or ledger rewrites cannot erase the true day-start anchor.
    """
    balance = float(balance or 0)
    if balance <= 0:
        return {}
    with _lock:
        data = _load()
        today = _today_utc()
        now_ts = time.time()

        # Seed project start only ONCE (ever)
        if data.get("project_start_balance") is None:
            data["project_start_balance"] = balance
            data["project_start_date"] = today
            data["project_start_ts"] = now_ts

        days = data.setdefault("days", {})
        if today not in days:
            # Seed start_balance from previous day's end_balance, else current.
            prev = max((d for d in days if d < today), default=None)
            if prev and days[prev].get("end_balance"):
                start_bal = float(days[prev]["end_balance"])
            else:
                start_bal = balance
            days[today] = {
                "start_balance": start_bal,
                "start_ts": now_ts,
                "end_balance": balance,
                "last_ts": now_ts,
                "pnl_day_usd": round(balance - start_bal, 2),
                "pnl_day_pct": round((balance - start_bal) / start_bal * 100, 3) if start_bal > 0 else 0.0,
                "trades_count": 0, "wins": 0, "losses": 0,
                "avgs": 0, "partials": 0, "trades_pnl_usd": 0.0,
            }
        else:
            d = days[today]
            # NEVER overwrite start_balance — only end_balance.
            sb = float(d.get("start_balance") or balance)
            d["end_balance"] = balance
            d["last_ts"] = now_ts
            d["pnl_day_usd"] = round(balance - sb, 2)
            d["pnl_day_pct"] = round((balance - sb) / sb * 100, 3) if sb > 0 else 0.0

        # Enrich with trade stats (best-effort)
        try:
            import trade_library
            summary = trade_library.daily_summary(today)
            days[today]["trades_count"] = summary.get("total_trades", 0)
            days[today]["wins"] = summary.get("wins", 0)
            days[today]["losses"] = summary.get("losses", 0)
            days[today]["avgs"] = summary.get("total_avgs", 0)
            days[today]["partials"] = summary.get("total_partials", 0)
            days[today]["trades_pnl_usd"] = summary.get("total_pnl_usd", 0.0)
        except Exception:
            pass

        # Rollup week + month + totals
        _rollup_week(data, _iso_week(today))
        _rollup_month(data, _month_key(today))
        _recompute_totals(data)

        _save(data)

        # Legacy today-only file
        try:
            TODAY_FILE.write_text(
                json.dumps({
                    "date": today,
                    "start_balance": days[today]["start_balance"],
                    "start_ts": days[today]["start_ts"],
                    "end_balance": days[today]["end_balance"],
                    "pnl_day": days[today]["pnl_day_usd"],
                    "pnl_day_pct": days[today]["pnl_day_pct"],
                    "accumulated_pnl": data["accumulated_pnl_usd"],
                    "accumulated_pnl_pct": data["accumulated_pnl_pct"],
                }, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

        return dict(days[today])


def set_project_start(balance: float, date: str | None = None) -> None:
    """Manually seed project_start_balance. Used to recover from lost anchors."""
    with _lock:
        data = _load()
        data["project_start_balance"] = float(balance)
        data["project_start_date"] = date or _today_utc()
        data["project_start_ts"] = time.time()
        _recompute_totals(data)
        _save(data)


def set_day_start(date: str, balance: float) -> None:
    """Manually set start_balance for a specific day (recovery tool)."""
    with _lock:
        data = _load()
        days = data.setdefault("days", {})
        d = days.setdefault(date, {
            "start_balance": float(balance), "start_ts": time.time(),
            "end_balance": float(balance), "last_ts": time.time(),
            "pnl_day_usd": 0.0, "pnl_day_pct": 0.0,
            "trades_count": 0, "wins": 0, "losses": 0,
            "avgs": 0, "partials": 0, "trades_pnl_usd": 0.0,
        })
        d["start_balance"] = float(balance)
        eb = float(d.get("end_balance") or balance)
        d["pnl_day_usd"] = round(eb - float(balance), 2)
        d["pnl_day_pct"] = round((eb - float(balance)) / float(balance) * 100, 3) if balance > 0 else 0.0
        _rollup_week(data, _iso_week(date))
        _rollup_month(data, _month_key(date))
        _recompute_totals(data)
        _save(data)


def history(days_back: int = 30) -> list[dict]:
    data = _load()
    days = data.get("days", {})
    dates_sorted = sorted(days.keys(), reverse=True)[:days_back]
    return [{"date": d, **days[d]} for d in dates_sorted]


def weeks(weeks_back: int = 12) -> list[dict]:
    data = _load()
    ws = data.get("weeks", {})
    keys = sorted(ws.keys(), reverse=True)[:weeks_back]
    return [{"week": k, **ws[k]} for k in keys]


def months(months_back: int = 12) -> list[dict]:
    data = _load()
    ms = data.get("months", {})
    keys = sorted(ms.keys(), reverse=True)[:months_back]
    return [{"month": k, **ms[k]} for k in keys]


def accumulated() -> dict:
    """Project-wide P&L summary, adjusted for documented anomalies.

    Sums all `_phantom_cleanup` removed_pnl_delta across days and adds it
    back to accumulated_pnl_usd so the dashboard "Acumulat Total" matches
    the day-level view. Raw broker delta preserved as `*_raw` for auditing.
    """
    data = _load()
    raw_pnl_usd = float(data.get("accumulated_pnl_usd", 0.0) or 0.0)
    raw_pnl_pct = float(data.get("accumulated_pnl_pct", 0.0) or 0.0)
    psb = float(data.get("project_start_balance") or 0)
    # Sum cumulative anomaly adjustments across all days.
    total_adj = 0.0
    for _, day in (data.get("days") or {}).items():
        ph = day.get("_phantom_cleanup") if isinstance(day, dict) else None
        if isinstance(ph, dict) and "removed_pnl_delta" in ph:
            total_adj += -float(ph.get("removed_pnl_delta") or 0)
    adj_pnl_usd = round(raw_pnl_usd + total_adj, 2)
    adj_pnl_pct = round((adj_pnl_usd / psb * 100.0), 3) if psb else raw_pnl_pct
    return {
        "project_start_balance": data.get("project_start_balance"),
        "project_start_date": data.get("project_start_date"),
        "accumulated_pnl_usd": adj_pnl_usd,
        "accumulated_pnl_pct": adj_pnl_pct,
        "accumulated_pnl_usd_raw": round(raw_pnl_usd, 2),
        "accumulated_pnl_pct_raw": round(raw_pnl_pct, 3),
        "anomaly_adjustment_total_usd": round(total_adj, 2) if total_adj else 0,
        "total_days": len(data.get("days", {})),
        "total_weeks": len(data.get("weeks", {})),
        "total_months": len(data.get("months", {})),
    }


def reconcile(date: str | None = None) -> dict:
    """Today's P&L reconciliation, normalized for documented anomalies.

    `balance_delta_usd` and `pct` are returned WITH the phantom-cleanup
    adjustment applied (when the day has one), so the Trades view header
    matches the dashboard P&L. Raw broker-only values are exposed as
    `*_raw` for transparency. `discrepancy_usd` is computed against the
    adjusted balance — if non-zero, it points to genuine bookkeeping
    drift (events lost or duplicated) rather than the documented anomaly.
    """
    date = date or _today_utc()
    data = _load()
    day = data.get("days", {}).get(date, {})
    balance_delta_raw = float(day.get("pnl_day_usd", 0) or 0)
    balance_pct_raw = float(day.get("pnl_day_pct", 0) or 0)
    # Apply documented phantom-cleanup if present.
    phantom = day.get("_phantom_cleanup") or {}
    adj = 0.0
    if isinstance(phantom, dict) and "removed_pnl_delta" in phantom:
        adj = -float(phantom.get("removed_pnl_delta") or 0)
    balance_delta = round(balance_delta_raw + adj, 2)
    # Recompute % off the (possibly adjusted) delta against start_balance.
    start_bal = float(day.get("start_balance") or 0)
    balance_pct = round(balance_delta / start_bal * 100.0, 3) if start_bal else balance_pct_raw
    try:
        import trade_library
        trades_pnl = float(trade_library.daily_summary(date).get("total_pnl_usd", 0) or 0)
    except Exception:
        trades_pnl = 0.0
    return {
        "date": date,
        "balance_delta_usd": balance_delta,
        "balance_delta_pct": balance_pct,
        "balance_delta_usd_raw": balance_delta_raw,
        "balance_delta_pct_raw": balance_pct_raw,
        "anomaly_adjustment_usd": round(adj, 2) if adj else 0,
        "trades_pnl_usd": trades_pnl,
        "discrepancy_usd": round(balance_delta - trades_pnl, 2),
    }
