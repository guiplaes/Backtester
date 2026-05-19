"""Event detector — codi pur, sense LLM.

Filtre pre-LLM que decideix quan cal invocar l'Executor. Executat a cada
tick del fast loop (~3s). Si no passa res estructuralment rellevant,
retorna None i l'Executor no es crida (estalvi massiu de cost LLM).

Tot event detectat s'escriu a brain_events_log.jsonl (append-only) per
telemetria, tant si invoca l'Executor com si es filtra.

MVP: 8 tipus d'events + HEARTBEAT com a fallback.

Regla de contradicció (CONTRADICTION_RULE)
──────────────────────────────────────────
Quan s'agrupen múltiples events dins del cooldown i es detecta contradicció
(típicament REJECTION_CANDLE_AT_ZONE + MOMENTUM_BREAK amb direccions
oposades), l'event **més recent** esdevé el principal a trigger_events[0];
la resta van al context com a trigger_events[1..]. L'Executor rep tots
dos per jutjar, però sap quin ha dominat l'últim tick.
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

# ── Tipus d'event (valor = string escrit al log) ──
EVENT_TRADE_OPENED = "TRADE_OPENED"
EVENT_PRICE_APPROACHING_ZONE = "PRICE_APPROACHING_ZONE"
EVENT_REJECTION_CANDLE_AT_ZONE = "REJECTION_CANDLE_AT_ZONE"
EVENT_MOMENTUM_BREAK = "MOMENTUM_BREAK"
EVENT_DD_THRESHOLD_CROSSED = "DD_THRESHOLD_CROSSED"
EVENT_PROFIT_CAPTURE_TRIGGER = "PROFIT_CAPTURE_TRIGGER"
EVENT_TG_MESSAGE = "TG_MESSAGE"
EVENT_SESSION_TRANSITION = "SESSION_TRANSITION"
EVENT_HEARTBEAT = "HEARTBEAT"

# v3.3: structural profit-capture events.
# Fire when the price reaches a logical exit level (contrary zone, structural
# break, liquidity sweep). The Executor treats these as "PARTIAL_CLOSE is the
# default action; needs strong reason to not act".
EVENT_REACHED_CONTRARY_ZONE = "REACHED_CONTRARY_ZONE"
EVENT_STRUCTURE_BROKEN_AGAINST = "STRUCTURE_BROKEN_AGAINST"
EVENT_LIQUIDITY_SWEPT = "LIQUIDITY_SWEPT"

# v3.3: hard trigger for "price has drifted adversely without any averaging".
# Prevents the degenerate case where Executor keeps WAITing past every zone
# because momentum looks strong — you'd end up riding adverse excursion without
# ever improving your average. Fires at N × 1.5 × ATR_M15 of cumulative adverse
# drift from the weighted entry price (N = 1, 2, 3...).
EVENT_ADVERSE_DRIFT_NO_AVG = "ADVERSE_DRIFT_NO_AVG"

ALL_EVENT_TYPES = (
    EVENT_TRADE_OPENED,
    EVENT_PRICE_APPROACHING_ZONE,
    EVENT_REJECTION_CANDLE_AT_ZONE,
    EVENT_MOMENTUM_BREAK,
    EVENT_DD_THRESHOLD_CROSSED,
    EVENT_PROFIT_CAPTURE_TRIGGER,
    EVENT_TG_MESSAGE,
    EVENT_SESSION_TRANSITION,
    EVENT_HEARTBEAT,
    EVENT_REACHED_CONTRARY_ZONE,
    EVENT_STRUCTURE_BROKEN_AGAINST,
    EVENT_LIQUIDITY_SWEPT,
    EVENT_ADVERSE_DRIFT_NO_AVG,
)

CONTRADICTION_RULE = "most_recent_event_is_principal_others_are_context"

CONTRADICTORY_EVENT_PAIRS = frozenset({
    (EVENT_REJECTION_CANDLE_AT_ZONE, EVENT_MOMENTUM_BREAK),
    (EVENT_MOMENTUM_BREAK, EVENT_REJECTION_CANDLE_AT_ZONE),
})

EVENTS_LOG_FILE = "brain_events_log.jsonl"
LOG_FIELD_INVOKED_EXECUTOR = "invoked_executor"

# Max events held in the pending queue before we start dropping the oldest.
# Drop-oldest (not drop-newest) keeps the most recent market state relevant.
# Overflow is logged to brain_events_log.jsonl with invoked_executor=False and a
# synthetic type so telemetry sees when the Executor fell behind.
EVENT_QUEUE_MAXLEN = 50
EVENT_QUEUE_OVERFLOW_TYPE = "QUEUE_OVERFLOW"


def _now_ts() -> float:
    return time.time()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_event(etype: str, details: dict | None = None, trade_id: str | None = None, ts: float | None = None) -> dict:
    return {
        "type": etype,
        "ts": ts if ts is not None else _now_ts(),
        "details": details or {},
        "trade_id": trade_id,
    }


def append_log(common_dir: str, event: dict, invoked_executor: bool) -> None:
    """Append-only write to brain_events_log.jsonl. Never raises."""
    try:
        path = os.path.join(common_dir, EVENTS_LOG_FILE)
        row = dict(event)
        row[LOG_FIELD_INVOKED_EXECUTOR] = bool(invoked_executor)
        row["iso"] = datetime.fromtimestamp(row.get("ts", _now_ts()), tz=timezone.utc).isoformat()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    except OSError:
        pass


# ─────────────────────────────────────────────────────────────────────────
# EventDetector class — stateful, one instance owned by trader_brain main loop.
# ─────────────────────────────────────────────────────────────────────────


class EventDetector:
    """Stateful event detector for the Executor's cooldown-based queue.

    Public API:
        - tick(context): scan current situation, enqueue new events
        - drain_pending(cooldown_seconds): return + clear queued events (respects cooldown)
        - next_event(): peek without consuming
        - mark_executor_called(ts): tell detector Executor was invoked (for heartbeat + cooldown)
        - reset_for_new_trade(trade_id): clear per-trade tracking state

    State held per trade_id:
        - approaching_zones: {zone_id: {"armed": True/False, "last_state": "near"|"far"}}
        - dd_thresholds_crossed: set of threshold values already emitted
        - profit_capture_fired: bool
        - momentum_break_fired_bar_ts: int | None (dedupe per bar)
        - last_bar_ts: int | None (to detect new M5 closes)
    """

    def __init__(self, cfg: dict, common_dir: str | None = None):
        self.cfg = cfg or {}
        self.common_dir = common_dir
        maxlen = int((cfg or {}).get("queue_maxlen", EVENT_QUEUE_MAXLEN))
        self.queue: deque[dict] = deque(maxlen=maxlen)
        self.last_executor_ts: float = 0.0
        self.last_heartbeat_emitted_ts: float = 0.0
        self.session_transitions_seen: set[str] = set()  # "YYYY-MM-DD|london_open"
        self._per_trade: dict[str | None, dict] = {}
        self._prev_trade_id: str | None = None

    # ── Config accessors ──

    def _c(self, key: str, default: Any) -> Any:
        return self.cfg.get(key, default)

    @property
    def cooldown_seconds(self) -> float:
        return float(self._c("cooldown_seconds", 60))

    @property
    def heartbeat_interval_seconds(self) -> float:
        return float(self._c("heartbeat_interval_seconds", 300))

    # ── Per-trade state ──

    @staticmethod
    def _avg_volume(bars: list[dict], n: int = 20) -> float:
        """Mean volume of last `n` bars excluding the most recent (current) one."""
        if len(bars) < 2:
            return 0.0
        window = bars[-(n + 1):-1] if len(bars) > n else bars[:-1]
        if not window:
            return 0.0
        return sum(b.get("volume", 0) for b in window) / len(window)

    def _trade_state(self, trade_id: str | None) -> dict:
        st = self._per_trade.get(trade_id)
        if st is None:
            st = {
                "approaching_zones": {},
                "dd_thresholds_crossed": set(),
                "profit_capture_fired": False,
                "momentum_break_fired_bar_ts": None,
                "last_bar_ts": None,
            }
            self._per_trade[trade_id] = st
        return st

    def reset_for_new_trade(self, trade_id: str | None) -> None:
        self._per_trade.pop(trade_id, None)

    # ── Enqueue ──

    def _enqueue(self, event: dict) -> None:
        # deque(maxlen=N).append() auto-drops the oldest when full. Detect that
        # case so telemetry records the overflow instead of swallowing it.
        at_capacity = self.queue.maxlen is not None and len(self.queue) == self.queue.maxlen
        dropped = self.queue[0] if at_capacity else None
        self.queue.append(event)
        if self.common_dir:
            if dropped is not None:
                overflow = _make_event(
                    EVENT_QUEUE_OVERFLOW_TYPE,
                    {"dropped_type": dropped.get("type"), "dropped_ts": dropped.get("ts"), "maxlen": self.queue.maxlen},
                    trade_id=event.get("trade_id"),
                )
                append_log(self.common_dir, overflow, invoked_executor=False)
            append_log(self.common_dir, event, invoked_executor=False)

    # ── Executor coordination ──

    def mark_executor_called(self, ts: float | None = None) -> None:
        """Called by trader_brain after dispatching to the Executor.
        Used to throttle HEARTBEAT fallback."""
        self.last_executor_ts = ts if ts is not None else _now_ts()

    # ── Public ──

    def tick(self, context: dict) -> None:
        """Scan `context` and enqueue any new events.

        Expected context keys:
          trade_id: str | None (same value while a single trade is open; changes on new trade)
          signal: dict | None with {direction, breakeven_set, flag_closing}
          price: float
          atr: float
          bars_m5: list[dict]  (each {open, high, low, close, volume, time})
          zones: list[dict]    (ACTIVE zones only, per zone_store shape)
          account: dict        ({balance, equity, dd_pct, floating_profit})
          tg_messages: list[dict] (new since last tick; filtered by caller to exclude cerramos/movemos SL)
          now: datetime (optional; defaults to utcnow)
        """
        trade_id = context.get("trade_id")
        if trade_id != self._prev_trade_id:
            # Signal TRADE_OPENED whenever a new non-None trade_id first appears.
            if trade_id is not None:
                self._enqueue(_make_event(EVENT_TRADE_OPENED, {"trade_id": trade_id}, trade_id))
            self._prev_trade_id = trade_id

        self._tick_session_transition(context)

        if trade_id is None:
            # No open trade → no point in tracking zone/DD/profit/momentum events.
            return

        self._tick_price_approaching(context, trade_id)
        self._tick_rejection_candle(context, trade_id)
        self._tick_momentum_break(context, trade_id)
        self._tick_dd_threshold(context, trade_id)
        self._tick_profit_capture(context, trade_id)
        self._tick_tg_messages(context, trade_id)
        # v3.3 structural profit-capture events
        self._tick_reached_contrary_zone(context, trade_id)
        self._tick_structure_broken_against(context, trade_id)
        self._tick_liquidity_swept(context, trade_id)
        self._tick_adverse_drift_no_avg(context, trade_id)

    # ── Individual detectors ──

    def _tick_session_transition(self, ctx: dict) -> None:
        now = ctx.get("now") or datetime.now(timezone.utc)
        day = now.strftime("%Y-%m-%d")
        checks = (
            ("london_open", self._c("session_london_open_utc", "07:00")),
            ("ny_open", self._c("session_ny_open_utc", "13:00")),
            ("ny_close", self._c("session_ny_close_utc", "21:00")),
        )
        for name, hhmm in checks:
            key = f"{day}|{name}"
            if key in self.session_transitions_seen:
                continue
            try:
                hh, mm = hhmm.split(":")
                transition_minutes = int(hh) * 60 + int(mm)
            except (ValueError, AttributeError):
                continue
            current_minutes = now.hour * 60 + now.minute
            # Fire once when we first observe that we've reached or passed the transition time.
            if current_minutes >= transition_minutes:
                self.session_transitions_seen.add(key)
                self._enqueue(_make_event(
                    EVENT_SESSION_TRANSITION,
                    {"transition": name, "at_utc": hhmm},
                    trade_id=ctx.get("trade_id"),
                ))

    def _tick_price_approaching(self, ctx: dict, trade_id: str) -> None:
        price = float(ctx.get("price", 0))
        atr = float(ctx.get("atr", 0))
        if atr <= 0 or price <= 0:
            return
        from zone_store import ZONE_STRENGTH_ORDER

        near_mul = float(self._c("approaching_zone_atr_multiplier", 0.5))
        reset_mul = float(self._c("approaching_zone_reset_atr_multiplier", 1.0))
        min_strength = self._c("approaching_zone_min_strength", "MODERATE")
        min_rank = ZONE_STRENGTH_ORDER.get(min_strength, 2)
        # 2026-04-26: per-zone cooldown — un cop disparat, no re-disparar fins
        # que hagin passat N segons, encara que el preu hagi anat far→near. El
        # mecanisme far/fired és per gestionar el cas de visites repetides
        # llargues; aquest cooldown evita el cas de price oscil·lant a la zona.
        per_zone_cooldown = float(self._c("approaching_zone_per_zone_cooldown_s", 300))
        now_ts = _now_ts()

        trade_state = self._trade_state(trade_id)
        armed = trade_state["approaching_zones"]

        for z in ctx.get("zones") or []:
            zid = z.get("id")
            if not zid:
                continue
            strength_rank = ZONE_STRENGTH_ORDER.get(z.get("strength", ""), 0)
            if strength_rank < min_rank:
                continue
            zprice = float(z.get("price", 0))
            dist = abs(price - zprice)
            state = armed.get(zid, {"fired": False, "fired_ts": 0})
            near = dist <= atr * near_mul
            far = dist >= atr * reset_mul
            fired_recently = (now_ts - float(state.get("fired_ts", 0))) < per_zone_cooldown
            if near and not state.get("fired") and not fired_recently:
                armed[zid] = {"fired": True, "fired_ts": now_ts}
                self._enqueue(_make_event(
                    EVENT_PRICE_APPROACHING_ZONE,
                    {
                        "zone_id": zid,
                        "zone_price": zprice,
                        "zone_strength": z.get("strength"),
                        "zone_type": z.get("type"),
                        "distance_usd": round(dist, 3),
                        "atr": atr,
                    },
                    trade_id=trade_id,
                ))
            elif far and state.get("fired"):
                # Reset "fired" but keep fired_ts — cooldown still applies
                # if zone re-approaches within the cooldown window.
                armed[zid] = {"fired": False, "fired_ts": state.get("fired_ts", 0)}

    def _tick_rejection_candle(self, ctx: dict, trade_id: str) -> None:
        bars = ctx.get("bars_m5") or []
        if len(bars) < 3:
            return
        # Use last CLOSED bar for pattern detection — avoids flip-flops from
        # live bar wicks that look like rejection but get invalidated at close.
        last = bars[-2]
        last_ts = last.get("time")
        if last_ts is None:
            return
        trade_state = self._trade_state(trade_id)
        if trade_state.get("last_bar_ts") == last_ts:
            # Same bar as last tick — already scanned.
            return

        from zone_lifecycle import is_engulfing, is_pin_bar

        vol_ratio_min = float(self._c("rejection_volume_min_ratio", 1.0))
        avg_vol = self._avg_volume(bars)
        vol = last.get("volume", 0)
        vol_ok = (avg_vol <= 0) or (vol / max(avg_vol, 1e-9) >= vol_ratio_min)
        if not vol_ok:
            trade_state["last_bar_ts"] = last_ts
            return

        touch_dist = float(ctx.get("touch_dist_usd", 0.5))
        fired_any = False
        for z in ctx.get("zones") or []:
            zprice = float(z.get("price", 0))
            ztype = z.get("type")
            bounce_dir = z.get("bounce_direction")
            at_zone = False
            if ztype == "SUPPORT":
                at_zone = last.get("low", zprice) <= zprice + touch_dist
            elif ztype == "RESISTANCE":
                at_zone = last.get("high", zprice) >= zprice - touch_dist
            if not at_zone:
                continue
            pattern = None
            if is_pin_bar(last, bounce_dir):
                pattern = "pin_bar"
            elif is_engulfing(bars[-3], last, bounce_dir):  # engulfing uses closed pair (-3, -2)
                pattern = "engulfing"
            if pattern:
                self._enqueue(_make_event(
                    EVENT_REJECTION_CANDLE_AT_ZONE,
                    {
                        "zone_id": z.get("id"),
                        "zone_price": zprice,
                        "pattern": pattern,
                        "bounce_direction": bounce_dir,
                        "volume_ratio": round(vol / max(avg_vol, 1e-9), 2) if avg_vol > 0 else None,
                    },
                    trade_id=trade_id,
                    ts=float(last_ts),
                ))
                fired_any = True
        trade_state["last_bar_ts"] = last_ts

    def _tick_momentum_break(self, ctx: dict, trade_id: str) -> None:
        signal = ctx.get("signal") or {}
        direction = signal.get("direction")
        if direction not in ("BUY", "SELL"):
            return
        bars = ctx.get("bars_m5") or []
        n = int(self._c("momentum_break_bars", 3))
        if len(bars) < n + 1:
            return
        last_n = bars[-n:]
        # Each bar must go AGAINST direction: BUY → bearish bars (close < open).
        def against(b):
            c, o = b.get("close", 0), b.get("open", 0)
            return c < o if direction == "BUY" else c > o
        if not all(against(b) for b in last_n):
            return
        # Volume rising across the sequence (strictly non-decreasing is enough).
        if bool(self._c("momentum_break_volume_rising", True)):
            vols = [b.get("volume", 0) for b in last_n]
            if not all(vols[i] <= vols[i + 1] for i in range(len(vols) - 1)):
                return
        last_ts = last_n[-1].get("time")
        trade_state = self._trade_state(trade_id)
        if trade_state.get("momentum_break_fired_bar_ts") == last_ts:
            return
        trade_state["momentum_break_fired_bar_ts"] = last_ts
        self._enqueue(_make_event(
            EVENT_MOMENTUM_BREAK,
            {
                "direction_against": direction,
                "bars": n,
                "last_bar_ts": last_ts,
            },
            trade_id=trade_id,
            ts=float(last_ts) if last_ts else _now_ts(),
        ))

    def _tick_dd_threshold(self, ctx: dict, trade_id: str) -> None:
        account = ctx.get("account") or {}
        dd_pct = float(account.get("dd_pct", 0))
        trade_state = self._trade_state(trade_id)
        crossed = trade_state["dd_thresholds_crossed"]
        for thr in self._c("dd_thresholds_pct", [1.5, 2.5]):
            try:
                thr_f = float(thr)
            except (TypeError, ValueError):
                continue
            if dd_pct >= thr_f and thr_f not in crossed:
                crossed.add(thr_f)
                self._enqueue(_make_event(
                    EVENT_DD_THRESHOLD_CROSSED,
                    {"threshold_pct": thr_f, "dd_pct_now": round(dd_pct, 2)},
                    trade_id=trade_id,
                ))

    def _tick_profit_capture(self, ctx: dict, trade_id: str) -> None:
        account = ctx.get("account") or {}
        balance = float(account.get("balance", 0))
        floating = float(account.get("floating_profit", 0))
        if balance <= 0 or floating <= 0:
            return
        pct_trigger = float(self._c("profit_capture_trigger_pct", 0.8))
        floating_pct = (floating / balance) * 100.0
        trade_state = self._trade_state(trade_id)
        if trade_state.get("profit_capture_fired"):
            return
        if floating_pct >= pct_trigger:
            trade_state["profit_capture_fired"] = True
            self._enqueue(_make_event(
                EVENT_PROFIT_CAPTURE_TRIGGER,
                {"floating_profit_usd": round(floating, 2), "floating_pct_of_balance": round(floating_pct, 3)},
                trade_id=trade_id,
            ))

    # ── v3.3 STRUCTURAL PROFIT-CAPTURE DETECTORS ──
    # Tots tres requereixen `min_profit_usd` (floor anti-soroll) i mantenen
    # cooldown per-trade perquè no es disparin repetidament al mateix moment.

    def _profit_floor_passed(self, ctx: dict) -> bool:
        """Filtre anti-soroll: el floating del trade ha de superar max($30, 0.05% balance)."""
        account = ctx.get("account") or {}
        balance = float(account.get("balance", 0) or 0)
        floating = float(account.get("floating_profit", 0) or 0)
        if balance <= 0:
            return False
        min_usd = max(30.0, balance * 0.0005)
        return floating >= min_usd

    def _tick_reached_contrary_zone(self, ctx: dict, trade_id: str) -> None:
        """PARTIAL signal: price reached a contrary (STRONG/MODERATE) zone + rejection.

        For BUY trade → contrary = nearest RESISTANCE above.
        For SELL trade → contrary = nearest SUPPORT below.
        """
        signal = ctx.get("signal") or {}
        direction = signal.get("direction")
        if direction not in ("BUY", "SELL"):
            return
        price = float(ctx.get("price", 0) or 0)
        atr = float(ctx.get("atr", 0) or 0)
        if price <= 0 or atr <= 0:
            return
        if not self._profit_floor_passed(ctx):
            return

        from zone_store import ZONE_STRENGTH_ORDER
        min_rank = ZONE_STRENGTH_ORDER.get("MODERATE", 2)

        # Find nearest contrary zone
        candidates = []
        for z in ctx.get("zones") or []:
            zp = z.get("price")
            if zp is None:
                continue
            zp = float(zp)
            zstrength = (z.get("strength") or "WEAK").upper()
            if ZONE_STRENGTH_ORDER.get(zstrength, 0) < min_rank:
                continue
            ztype = (z.get("type") or "").upper()
            if direction == "BUY" and ztype == "RESISTANCE" and zp > price:
                candidates.append((zp, z))
            elif direction == "SELL" and ztype == "SUPPORT" and zp < price:
                candidates.append((zp, z))

        if not candidates:
            return
        # Nearest contrary zone
        candidates.sort(key=lambda kv: abs(kv[0] - price))
        nearest_price, nearest_zone = candidates[0]
        distance = abs(price - nearest_price)
        tolerance = atr * 0.3
        if distance > tolerance:
            return

        # Rejection candle on last CLOSED bar (bars[-2]) — avoids firing on
        # live bar whose body/wick is still forming.
        bars = ctx.get("bars_m5") or []
        if len(bars) < 3:
            return
        last = bars[-2]
        o, h, l, c = (last.get(k, 0) for k in ("open", "high", "low", "close"))
        body = abs(c - o)
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l
        rejection = False
        if direction == "BUY" and upper_wick > body * 1.2 and c < o:
            rejection = True  # Long upper wick + bearish close = rejection at resistance
        if direction == "SELL" and lower_wick > body * 1.2 and c > o:
            rejection = True  # Long lower wick + bullish close = rejection at support

        if not rejection:
            return

        # Cooldown per (trade, zone_price) — don't re-fire at the same zone within 5 min
        trade_state = self._trade_state(trade_id)
        fired = trade_state.setdefault("contrary_zone_fired", {})
        zkey = round(nearest_price, 1)
        if (_now_ts() - fired.get(zkey, 0)) < 300:
            return
        fired[zkey] = _now_ts()

        self._enqueue(_make_event(
            EVENT_REACHED_CONTRARY_ZONE,
            {
                "zone_price": round(nearest_price, 2),
                "strength": nearest_zone.get("strength"),
                "type": nearest_zone.get("type"),
                "distance_usd": round(distance, 2),
                "trade_direction": direction,
            },
            trade_id=trade_id,
        ))

    def _tick_structure_broken_against(self, ctx: dict, trade_id: str) -> None:
        """PARTIAL signal: structural break against the trade direction with volume."""
        signal = ctx.get("signal") or {}
        direction = signal.get("direction")
        if direction not in ("BUY", "SELL"):
            return
        if not self._profit_floor_passed(ctx):
            return

        ms = ctx.get("market_state") or {}
        bos = (ms.get("structure") or {}).get("last_bos")
        if not bos or bos.get("age_bars") is None:
            return
        # Only fresh BOS (within last 3 bars)
        if int(bos.get("age_bars", 99)) > 3:
            return
        # BOS direction must be OPPOSITE to trade
        bos_type = (bos.get("type") or "").lower()
        adverse = (direction == "BUY" and bos_type == "bearish") or \
                  (direction == "SELL" and bos_type == "bullish")
        if not adverse:
            return

        # Volume check — volume of last CLOSED bar ≥ 1.5× avg 20 (exclude live bar)
        bars = ctx.get("bars_m5") or []
        if len(bars) < 21:
            return
        avg_vol = self._avg_volume(bars[:-1], n=20)  # avg excluding live bar
        last_vol = float(bars[-2].get("volume", 0) or 0)  # closed bar volume
        if avg_vol <= 0 or last_vol < avg_vol * 1.5:
            return

        # Per-BOS cooldown: don't refire for same BOS price
        trade_state = self._trade_state(trade_id)
        last_bos_price = trade_state.get("last_structural_bos_fired_price")
        if last_bos_price == bos.get("price"):
            return
        trade_state["last_structural_bos_fired_price"] = bos.get("price")

        self._enqueue(_make_event(
            EVENT_STRUCTURE_BROKEN_AGAINST,
            {
                "bos_type": bos.get("type"),
                "bos_price": bos.get("price"),
                "age_bars": bos.get("age_bars"),
                "volume_ratio": round(last_vol / avg_vol, 2) if avg_vol > 0 else None,
                "trade_direction": direction,
            },
            trade_id=trade_id,
        ))

    def _tick_liquidity_swept(self, ctx: dict, trade_id: str) -> None:
        """PARTIAL signal: price touched a contrary liquidity pool + closed back.

        For BUY trade → pool above was touched (high >= pool) but close returned below.
        For SELL trade → pool below was touched (low <= pool) but close returned above.
        """
        signal = ctx.get("signal") or {}
        direction = signal.get("direction")
        if direction not in ("BUY", "SELL"):
            return
        if not self._profit_floor_passed(ctx):
            return

        ms = ctx.get("market_state") or {}
        liq = ms.get("liquidity") or {}
        bars = ctx.get("bars_m5") or []
        if not bars:
            return

        # Scan last 5 bars for sweep + rejection pattern
        scan = bars[-5:] if len(bars) >= 5 else bars
        pools_to_check = liq.get("pools_above") if direction == "BUY" else liq.get("pools_below")
        if not pools_to_check:
            return

        trade_state = self._trade_state(trade_id)
        fired = trade_state.setdefault("liquidity_swept_fired", {})

        for pool_price in pools_to_check:
            pkey = round(float(pool_price), 1)
            if (_now_ts() - fired.get(pkey, 0)) < 600:
                continue  # 10 min cooldown per pool
            for b in scan:
                h = float(b.get("high", 0) or 0)
                l = float(b.get("low", 0) or 0)
                c = float(b.get("close", 0) or 0)
                swept = False
                if direction == "BUY":
                    # Pool above: high exceeded pool, close came back below
                    if h >= pool_price and c < pool_price:
                        swept = True
                else:
                    if l <= pool_price and c > pool_price:
                        swept = True
                if swept:
                    fired[pkey] = _now_ts()
                    self._enqueue(_make_event(
                        EVENT_LIQUIDITY_SWEPT,
                        {
                            "pool_price": round(float(pool_price), 2),
                            "side": "above" if direction == "BUY" else "below",
                            "trade_direction": direction,
                        },
                        trade_id=trade_id,
                    ))
                    break  # one event per pool per tick

    def _tick_adverse_drift_no_avg(self, ctx: dict, trade_id: str) -> None:
        """Fires once per 1.5×ATR_M15 step of cumulative adverse drift from entry.

        Rationale: prevents the degenerate case where Executor keeps WAITing past
        every zone because momentum is strong. At each adverse milestone (1.5×,
        3×, 4.5×... ATR_M15 from entry) we force a reassessment so the Executor
        has to decide: either promediar now (with structural context), PARTIAL,
        or accept it's riding the adverse without action.

        Guarded by avg_count — if the trade has already averaged, we don't
        spam this event; structural events drive decisions from there.
        Also suppressed if breakeven_set (no more averaging allowed anyway).
        """
        signal = ctx.get("signal") or {}
        direction = signal.get("direction")
        entry = float(signal.get("entry_price") or 0)
        avg_count = int(signal.get("avg_count") or 0)
        if direction not in ("BUY", "SELL") or entry <= 0:
            return
        if signal.get("breakeven_set") or signal.get("flag_closing"):
            return
        # Only fires pre-first-avg. Once there's an avg, structural events drive.
        if avg_count > 0:
            return

        atr_m15 = float(ctx.get("atr_m15") or 0)
        if atr_m15 <= 0:
            return
        price = float(ctx.get("price") or 0)
        if price <= 0:
            return

        adverse_usd = (entry - price) if direction == "BUY" else (price - entry)
        if adverse_usd <= 0:
            return  # trade is in profit or flat

        step = 1.5 * atr_m15
        n = int(adverse_usd // step)
        if n < 1:
            return

        trade_state = self._trade_state(trade_id)
        last_n = int(trade_state.get("adverse_drift_step", 0))
        if n <= last_n:
            return  # already fired for this step or beyond
        trade_state["adverse_drift_step"] = n

        self._enqueue(_make_event(
            EVENT_ADVERSE_DRIFT_NO_AVG,
            {
                "step_n": n,
                "adverse_usd": round(adverse_usd, 2),
                "atr_m15": round(atr_m15, 2),
                "entry_price": round(entry, 2),
                "current_price": round(price, 2),
                "trade_direction": direction,
            },
            trade_id=trade_id,
        ))

    def _tick_tg_messages(self, ctx: dict, trade_id: str) -> None:
        for msg in ctx.get("tg_messages") or []:
            self._enqueue(_make_event(
                EVENT_TG_MESSAGE,
                {"text": msg.get("text", ""), "channel": msg.get("channel"), "id": msg.get("id")},
                trade_id=trade_id,
            ))

    # ── Drain + heartbeat + contradiction rule ──

    def next_event(self) -> dict | None:
        return self.queue[0] if self.queue else None

    def drain_pending(self, current_ts: float | None = None) -> list[dict]:
        """Return all pending events, applying CONTRADICTION_RULE reordering,
        and clear the queue. Caller must pass these to the Executor and then
        call mark_executor_called(ts).

        If the queue is empty but the heartbeat interval has elapsed AND the
        Executor has not been called within that interval, emits and returns
        a single HEARTBEAT event. The heartbeat NEVER groups with other events.
        """
        current_ts = current_ts if current_ts is not None else _now_ts()

        if not self.queue:
            # Heartbeat fallback — only meaningful while a trade is open.
            # If no trade is being tracked, the Executor shouldn't be invoked anyway.
            if self._prev_trade_id is not None:
                if (current_ts - self.last_executor_ts) >= self.heartbeat_interval_seconds:
                    if (current_ts - self.last_heartbeat_emitted_ts) >= self.heartbeat_interval_seconds:
                        self.last_heartbeat_emitted_ts = current_ts
                        hb = _make_event(
                            EVENT_HEARTBEAT,
                            {"reason": "interval_elapsed"},
                            trade_id=self._prev_trade_id,
                            ts=current_ts,
                        )
                        return [hb]
            return []

        events = list(self.queue)
        self.queue.clear()
        events = _apply_contradiction_rule(events)
        return events


def _apply_contradiction_rule(events: list[dict]) -> list[dict]:
    """If any pair of contradictory event types coexist, place the most recent
    one at index 0; others follow in descending-time order.

    Otherwise return events in original order (time-ascending as enqueued)."""
    types = {e.get("type") for e in events}
    has_contradiction = any(
        (a in types and b in types) for (a, b) in CONTRADICTORY_EVENT_PAIRS
    )
    if not has_contradiction:
        return events
    return sorted(events, key=lambda e: e.get("ts", 0), reverse=True)


# Export helper for the main loop to log an event as "invoked_executor=True"
def log_invoked(common_dir: str, events: list[dict]) -> None:
    for e in events:
        append_log(common_dir, e, invoked_executor=True)
