"""Validator — last-mile anti-bug firewall before executing an Executor response.

Role (v3.2)
───────────
Since the Executor now owns tactical judgment (via `dd_projection` + the 4-question
protocol), the validator is NO LONGER a judgment censor. It only catches:

  1. DIRECTION_MISMATCH    — LLM proposed an order opposite to the signal
  2. MULTIPLIER_INVALID    — order.multiplier malformed or out of range
  3. LOT_OUT_OF_RANGE      — computed lot outside broker physical bounds
  4. AVERAGE_AFTER_BREAKEVEN — BE received, no more averagings
  5. ILLEGAL_ACTION_WHILE_CLOSING — "cerramos" received, only partials/WAIT
  6. DD_SOFT_STOP          — defense-in-depth at 3.4% (hard is 3.5% in EA)

DD_SOFT_STOP is NOT a judgment layer — it's an anti-hallucination belt just
below the hard stop. The intent is: "if the Executor miscalculates and would
push us straight into 3.5% with reasonable adverse, refuse." It deliberately
sits at 3.4% (very tight margin) so the system stays able to operate up
against the limit with intelligent decisions — NOT a defensive 2% shutdown.

Fail-CLOSED on any exception (caller handles).
"""

from __future__ import annotations

from typing import Any

# ── Codis de rebuig ──
REJECT_DIRECTION_MISMATCH = "DIRECTION_MISMATCH"
REJECT_MULTIPLIER_INVALID = "MULTIPLIER_INVALID"
REJECT_LOT_OUT_OF_RANGE = "LOT_OUT_OF_RANGE"
REJECT_AVERAGE_AFTER_BREAKEVEN = "AVERAGE_AFTER_BREAKEVEN"
REJECT_ILLEGAL_ACTION_WHILE_CLOSING = "ILLEGAL_ACTION_WHILE_CLOSING"
REJECT_DD_SOFT_STOP = "DD_SOFT_STOP"

ALL_REJECT_CODES = (
    REJECT_DIRECTION_MISMATCH,
    REJECT_MULTIPLIER_INVALID,
    REJECT_LOT_OUT_OF_RANGE,
    REJECT_AVERAGE_AFTER_BREAKEVEN,
    REJECT_ILLEGAL_ACTION_WHILE_CLOSING,
    REJECT_DD_SOFT_STOP,
)

FORCED_FALLBACK_ACTION = "WAIT"

# XAUUSD: 1 lot = 100 oz → $1 price move = $100 PnL per lot.
XAUUSD_CONTRACT_VALUE_PER_USD = 100.0

# Defense-in-depth defaults (overridable via cfg).
DEFAULT_DD_SOFT_PCT = 3.4
DEFAULT_ADVERSE_ATR_FACTOR = 1.5


def _rejection(code: str, detail: str) -> dict:
    return {"code": code, "detail": detail, "action": FORCED_FALLBACK_ACTION}


def check(
    executor_response: dict,
    signal: dict,
    account: dict,
    cfg: dict,
) -> tuple[bool, dict | None]:
    """Last-mile sanity check.

    Expected cfg keys (merge of executor + sizing + risk_control):
        lot_min, lot_max                — physical broker bounds
        base_lot, max_multiplier        — sizing envelope (for multiplier validation)
        dd_soft_pct                     — soft DD ceiling (default 3.4)
        validator_adverse_atr_factor    — ATR multiplier for adverse estimate (default 1.5)

    Expected account key (new):
        atr_m5                          — current ATR(14) M5 in USD. If missing,
                                          falls back to a conservative $5 estimate.
    """
    if not isinstance(executor_response, dict):
        return True, None

    action = executor_response.get("action", "WAIT")
    if action in (None, "WAIT", "ALERT"):
        return True, None

    # "cerramos" dominates
    if signal and signal.get("flag_closing"):
        if action not in ("PARTIAL_CLOSE", "WAIT"):
            return False, _rejection(
                REJECT_ILLEGAL_ACTION_WHILE_CLOSING,
                f"Action {action!r} not allowed during closing sequence",
            )

    if action == "AVERAGE":
        order = executor_response.get("order") or {}
        order_type = order.get("type")
        multiplier = order.get("multiplier")

        # ── DIRECTION_MISMATCH ──
        sig_dir = (signal or {}).get("direction")
        if sig_dir and order_type and order_type != sig_dir:
            return False, _rejection(
                REJECT_DIRECTION_MISMATCH,
                f"AVERAGE order type {order_type!r} opposes signal direction {sig_dir!r}",
            )

        # ── MULTIPLIER_INVALID ──
        base_lot = float(cfg.get("base_lot", 0.03))
        max_mult = int(cfg.get("max_multiplier", 5))
        if isinstance(multiplier, bool) or not isinstance(multiplier, int):
            return False, _rejection(
                REJECT_MULTIPLIER_INVALID,
                f"multiplier must be int, got {type(multiplier).__name__}={multiplier!r}",
            )
        if multiplier < 1 or multiplier > max_mult:
            return False, _rejection(
                REJECT_MULTIPLIER_INVALID,
                f"multiplier {multiplier} outside [1, {max_mult}]",
            )
        lot_f = round(base_lot * multiplier, 2)

        # ── LOT_OUT_OF_RANGE ──
        lot_min = float(cfg.get("lot_min", 0.01))
        lot_max = float(cfg.get("lot_max", 0.5))
        if lot_f < lot_min or lot_f > lot_max:
            return False, _rejection(
                REJECT_LOT_OUT_OF_RANGE,
                f"computed lot {lot_f} (base×mult) outside [{lot_min}, {lot_max}]",
            )

        # ── AVERAGE_AFTER_BREAKEVEN ──
        if (signal or {}).get("breakeven_set"):
            return False, _rejection(
                REJECT_AVERAGE_AFTER_BREAKEVEN,
                "AVERAGE blocked — breakeven already set ('movemos SL' received)",
            )

        # ── DD_SOFT_STOP (defense-in-depth) ──
        balance = float((account or {}).get("balance", 0))
        dd_used_usd = float((account or {}).get("dd_used", 0))
        dd_soft_pct = float(cfg.get("dd_soft_pct", DEFAULT_DD_SOFT_PCT))
        adverse_factor = float(cfg.get("validator_adverse_atr_factor", DEFAULT_ADVERSE_ATR_FACTOR))
        atr_m5 = float((account or {}).get("atr_m5", 0) or 0)
        adverse_usd = atr_m5 * adverse_factor if atr_m5 > 0 else 5.0
        if balance > 0:
            extra_loss_usd = lot_f * adverse_usd * XAUUSD_CONTRACT_VALUE_PER_USD
            dd_post_usd = dd_used_usd + extra_loss_usd
            dd_post_pct = dd_post_usd / balance * 100.0
            if dd_post_pct > dd_soft_pct:
                return False, _rejection(
                    REJECT_DD_SOFT_STOP,
                    f"projected DD {dd_post_pct:.2f}% > soft limit {dd_soft_pct:.2f}% "
                    f"(lot={lot_f}, adverse={adverse_factor}×ATR=${adverse_usd:.2f})",
                )

    return True, None
