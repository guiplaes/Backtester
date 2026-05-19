"""
Daily full analysis — runs once a day (22:00 UTC by default).
Pulls full context, invokes Claude for comprehensive review.
"""
import json
import logging
from datetime import datetime, timezone

from config import BOT_ID, LOG_DIR, SYMBOL
from pionex_client import get_bot_range, get_atr, get_klines, get_balance
from db import log_snapshot, log_decision, get_recent_snapshots
from claude_invoke import load_prompt, invoke_claude, parse_decision


LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=LOG_DIR / "daily.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("daily")


def gather_context():
    state = get_bot_range(BOT_ID)
    atr_7 = get_atr(SYMBOL, 7)
    atr_14 = get_atr(SYMBOL, 14)
    balance = get_balance()
    daily_klines = get_klines(SYMBOL, "1D", 14)
    recent = get_recent_snapshots(48)  # last 48 polls (~4h history)

    return {
        "now": datetime.now(timezone.utc).isoformat(),
        "bot_state": state,
        "atr_7d": atr_7,
        "atr_14d": atr_14,
        "balance": balance,
        "recent_daily_klines": [
            {"date": k["time"], "open": k["open"], "high": k["high"],
             "low": k["low"], "close": k["close"]}
            for k in daily_klines[-14:]
        ],
        "recent_snapshots_count": len(recent),
    }


def daily_analysis():
    ctx = gather_context()
    log.info(f"daily analysis | price={ctx['bot_state']['price']} ATR(7)={ctx['atr_7d']:.2f}")

    prompt = load_prompt("daily_review")
    filled = prompt.format(
        state_dump=json.dumps(ctx, indent=2, default=str),
        recent_snapshots=json.dumps(get_recent_snapshots(20), indent=2, default=str),
    )

    log.info("Invoking Claude for daily review...")
    r = invoke_claude(filled)
    if r["returncode"] != 0:
        log.error(f"Claude failed: {r.get('error')}")
        return None

    decision = parse_decision(r["output"])
    log.info(f"Daily decision: {decision.get('decision')} | risk={decision.get('risk')}")

    log_decision(
        trigger="daily_review",
        bot_id=BOT_ID,
        snapshot=ctx["bot_state"],
        action=decision.get("decision", "unknown"),
        reasoning=decision.get("reasoning", ""),
        new_range=decision.get("new_range"),
        cost=decision.get("cost_estimated", 0),
        executed=False,  # Claude executes via MCP if needed
    )

    return decision


if __name__ == "__main__":
    result = daily_analysis()
    if result:
        print(json.dumps(result, indent=2, default=str))
