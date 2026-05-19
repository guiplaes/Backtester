"""
Rebalancer simulator — replica EXACTA de grid_manager/rebalancer.py adaptat al backtest.
"""
from __future__ import annotations

from dataclasses import dataclass

from fee_model import TRADING_FEE_RATE

# Mirror config.py
TARGET_WEIGHTS = {
    "PAXG_USDT": 0.40,
    "BTC_USDT":  0.30,
    "ETH_USDT":  0.20,
    "SOL_USDT":  0.10,
}
REBALANCE_THRESHOLD = 0.05
MIN_REBALANCE_USD = 10.0
REBALANCE_COOLDOWN_MIN = 30


@dataclass
class RebalanceEvent:
    ts_ms: int
    target_bot: str
    amount_usdt: float
    sources: list
    deviation_pct: float
    fee_total: float
    partial: bool


def compute_weights(bot_values: dict) -> dict:
    total = sum(bot_values.values())
    if total <= 0:
        return {}
    return {name: v / total for name, v in bot_values.items()}


def evaluate(bot_values: dict, reserve_available: float,
             last_action_ts_ms: dict, current_ts_ms: int,
             allow_external_deposit: bool = True) -> list[dict]:
    weights = compute_weights(bot_values)
    total = sum(bot_values.values())
    if total <= 0:
        return []

    deviations = {}
    for name, w in weights.items():
        target = TARGET_WEIGHTS.get(name, 0)
        deviations[name] = {
            "current_weight": w,
            "target_weight": target,
            "deviation": w - target,
            "deviation_usd": (w - target) * total,
        }

    over = []
    under = []
    for name, dev in deviations.items():
        if dev["deviation"] >= REBALANCE_THRESHOLD:
            over.append((name, dev["deviation_usd"]))
        elif dev["deviation"] <= -REBALANCE_THRESHOLD:
            under.append((name, -dev["deviation_usd"]))

    over.sort(key=lambda x: x[1], reverse=True)
    under.sort(key=lambda x: x[1], reverse=True)

    actions = []
    reserve_avail = reserve_available

    for under_name, under_amount in under:
        if under_amount < MIN_REBALANCE_USD:
            continue
        last_ts = last_action_ts_ms.get(under_name, 0)
        if (current_ts_ms - last_ts) / 60_000 < REBALANCE_COOLDOWN_MIN:
            continue

        remaining = under_amount
        sources = []

        if reserve_avail > 0:
            use = min(reserve_avail, remaining)
            if use >= MIN_REBALANCE_USD / 2:
                sources.append({"from": "RESERVE", "amount": use})
                remaining -= use
                reserve_avail -= use

        for i, (over_name, over_amount) in enumerate(over):
            if remaining < MIN_REBALANCE_USD / 2:
                break
            use = min(over_amount, remaining)
            if use >= MIN_REBALANCE_USD / 2:
                sources.append({"from": over_name, "amount": use})
                remaining -= use
                over[i] = (over_name, over_amount - use)

        # Si encara queda remaining després de reserves i overs → necessitem deposit extern
        deposit_needed = 0.0
        if remaining >= MIN_REBALANCE_USD / 2 and allow_external_deposit:
            deposit_needed = remaining
            sources.append({"from": "EXTERNAL_DEPOSIT", "amount": deposit_needed})
            remaining = 0

        if sources:
            total_moved = under_amount - remaining
            actions.append({
                "target_bot": under_name,
                "amount_usdt": total_moved,
                "sources": sources,
                "partial": remaining > 0,
                "deviation_pct": deviations[under_name]["deviation"] * 100,
                "external_deposit": deposit_needed,
            })

    return actions


def execute(action: dict, ts_ms: int) -> RebalanceEvent:
    """Calcula fees totals d'aplicar una acció.
    - Source != RESERVE: 0.05% del amount (reduce_bot fa una sell market)
    - Target bot: 0.05% del total (invest_in_bot fa una buy market)
    """
    target = action["target_bot"]
    amount = action["amount_usdt"]

    fee_total = 0.0
    for src in action["sources"]:
        if src["from"] != "RESERVE":
            fee_total += src["amount"] * TRADING_FEE_RATE
    fee_total += amount * TRADING_FEE_RATE

    return RebalanceEvent(
        ts_ms=ts_ms,
        target_bot=target,
        amount_usdt=amount,
        sources=action["sources"],
        deviation_pct=action["deviation_pct"],
        fee_total=fee_total,
        partial=action["partial"],
    )


if __name__ == "__main__":
    bot_values = {
        "PAXG_USDT": 380,
        "BTC_USDT":  380,
        "ETH_USDT":  140,
        "SOL_USDT":  100,
    }
    # Important: current_ts > cooldown_min × 60_000 perquè no estigui en cooldown
    actions = evaluate(bot_values, reserve_available=50,
                       last_action_ts_ms={}, current_ts_ms=31 * 60 * 1000)
    print(f"Accions proposades: {len(actions)}")
    for a in actions:
        print(f"  -> {a['target_bot']} +${a['amount_usdt']:.2f}  dev={a['deviation_pct']:+.1f}%")
        for s in a["sources"]:
            print(f"      from {s['from']}: ${s['amount']:.2f}")
        ev = execute(a, ts_ms=0)
        print(f"      fee_total: ${ev.fee_total:.4f}")
