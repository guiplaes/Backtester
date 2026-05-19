#!/usr/bin/env python3
"""
Signal State Manager for Brain v3.

Tracks the currently active signal (if any) and persists state to disk.
This is what the brain has decided is "currently being traded" —
not the raw Telegram heartbeat from the legacy app.

Persists to: brain_signal_state.json in Common Files
"""

import os, json, time, threading, logging
from datetime import datetime, timezone

# v3.3: FSM overlay for trade lifecycle. Adds `state` (IDLE/OPENING/OPEN/
# MANAGING/CLOSING/CLOSED) and persistent `trade_id` alongside legacy flags.
# Existing callers continue to work — FSM transitions are called defensively
# (wrapped in try/except) so invalid transitions log warnings instead of
# breaking the flow. Over time, hard-fail can be enabled once all call sites
# are known clean.
from trade_fsm import TradeFSM, TradeState, InvalidTransitionError

COMMON = r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files"
STATE_FILE = os.path.join(COMMON, 'brain_signal_state.json')

_lock = threading.Lock()
_log = logging.getLogger('brain')
_LEVEL_TOL = 0.15
_BREAKEVEN_CONFIRM_TIMEOUT_S = 20.0


def _f(v, default=0.0):
    try:
        return float(v or 0)
    except Exception:
        return float(default)


def _uniform_level(levels, tol=_LEVEL_TOL):
    vals = [_f(v) for v in levels if abs(_f(v)) > 1e-9]
    if not vals:
        return 0.0
    head = vals[0]
    if all(abs(v - head) <= tol for v in vals[1:]):
        return round(head, 2)
    return 0.0


def _sl_is_protective(sl_price: float, entry_price: float, direction: str, tol: float = _LEVEL_TOL) -> bool:
    if sl_price <= 0 or entry_price <= 0:
        return False
    if direction == 'BUY':
        return sl_price >= (entry_price - tol)
    if direction == 'SELL':
        return sl_price <= (entry_price + tol)
    return False


def summarize_broker_positions(positions):
    """Return a small broker-authoritative summary for the live managed book."""
    pos = [p for p in (positions or []) if p]
    if not pos:
        return {
            'direction': None,
            'entry_price': 0.0,
            'total_lots': 0.0,
            'uniform_sl': 0.0,
            'uniform_tp': 0.0,
            'protected': False,
        }

    buy_vol = sum(_f(p.get('volume', 0)) for p in pos if p.get('type') == 'BUY')
    sell_vol = sum(_f(p.get('volume', 0)) for p in pos if p.get('type') == 'SELL')
    direction = 'BUY' if buy_vol >= sell_vol else 'SELL'
    same_dir = [p for p in pos if p.get('type') == direction]
    if not same_dir:
        same_dir = pos

    total_lots = sum(_f(p.get('volume', 0)) for p in same_dir)
    weighted = sum(_f(p.get('volume', 0)) * _f(p.get('price_open', 0)) for p in same_dir)
    entry_price = round(weighted / total_lots, 2) if total_lots > 0 else 0.0

    sls = [_f(p.get('sl', 0)) for p in same_dir]
    tps = [_f(p.get('tp', 0)) for p in same_dir]
    live_sls = [sl for sl in sls if sl > 0]
    protected = bool(
        same_dir
        and len(live_sls) == len(same_dir)
        and all(_sl_is_protective(sl, entry_price, direction) for sl in live_sls)
    )

    return {
        'direction': direction,
        'entry_price': entry_price,
        'total_lots': round(total_lots, 2),
        'uniform_sl': _uniform_level(sls),
        'uniform_tp': _uniform_level(tps),
        'protected': protected,
    }


class SignalState:
    """Holds the active signal state. Persists to JSON."""

    EMPTY = {
        'active': False,
        'direction': None,        # 'BUY' or 'SELL'
        'entry_price': 0.0,
        'channel': None,
        'source': None,           # 'brain' | 'adopted' (was manually opened or legacy EA)
        'opened_at': 0,
        'opened_ts': 0,
        'breakeven_set': False,
        'breakeven_pending': False,
        'breakeven_pending_since': 0.0,
        'breakeven_target_price': 0.0,
        'sl_price': 0.0,
        'tp_price': 0.0,
        'zones_averaged': [],     # list of {'price': X, 'ts': Y, 'lot': Z}
        'avg_count': 0,
        'initial_lot': 0.0,
        'total_lots': 0.0,
        'closing': False,
        'last_msg_ts': 0,
        'status': 'NONE',         # NONE, WAITING_FILTER, OPEN, CLOSING
        # Realized P&L from partial/full ticket closes during this trade (USD).
        # Reset to 0 on open_signal / adopt_positions / close_signal.
        # NOTE (2026-04-24): this value is computed from broker POSITION_PROFIT
        # which EXCLUDES commission/spread/swap. For authoritative trade P&L
        # use `signal_end_balance - signal_start_balance` instead — that's the
        # ground-truth the broker credits to the account.
        'realized_profit': 0.0,
        # Ground-truth P&L accounting (2026-04-24) — snapshots of the MT5
        # account balance at the exact moment the signal opened and closed.
        # Difference = real P&L INCLUDING commission/spread/swap/slippage.
        # This also enables a true DD metric that doesn't reset when we
        # crystallize losses mid-trade.
        'signal_start_balance': 0.0,
        'signal_end_balance': 0.0,
        # v3.3 FSM overlay (persisted as nested dict)
        'fsm': None,
        # ── Executor tactical plan (carries the LLM's situational TP/AVG
        # reasoning past the fire boundary so apply_trade_plan can honor it).
        # Populated when a staged setup fires; cleared on close_signal.
        # Schema:
        #   profit_targets: list[float]   (closest→farthest, from staging)
        #   averaging_zones: list[float]  (informative, EA reads zones independently)
        #   tactical_plan:  str            (free-text plan from the LLM)
        #   play_type:      str            (quick_reaction|range_fade|structural_break)
        'executor_plan': {},
    }

    def __init__(self):
        self._data = dict(self.EMPTY)
        self.load()

    def load(self):
        """Load from disk. Tolerates UTF-8 BOM (e.g. files written by PowerShell)."""
        with _lock:
            try:
                if os.path.exists(STATE_FILE):
                    # utf-8-sig strips BOM if present, otherwise behaves like utf-8.
                    # Without this, a BOM-prefixed file silently fails to parse and
                    # the brain wrongly adopts positions as a new trade (incident
                    # 2026-04-30: PowerShell-patched state was unreadable).
                    with open(STATE_FILE, 'r', encoding='utf-8-sig') as f:
                        self._data = json.load(f)
                        # Ensure all keys exist
                        for k, v in self.EMPTY.items():
                            if k not in self._data:
                                self._data[k] = v
            except Exception:
                self._data = dict(self.EMPTY)

    def save(self):
        """Save to disk."""
        with _lock:
            try:
                self._data['updated'] = datetime.now(timezone.utc).isoformat()
                with open(STATE_FILE, 'w', encoding='utf-8') as f:
                    json.dump(self._data, f, indent=2, ensure_ascii=False)
            except Exception:
                pass

    def as_dict(self):
        return dict(self._data)

    def get(self, key, default=None):
        return self._data.get(key, default)

    # ── v3.3 FSM accessors ──
    def _fsm(self) -> TradeFSM:
        """Hydrate FSM from persisted data."""
        return TradeFSM.from_dict(self._data.get('fsm'))

    def _persist_fsm(self, fsm: TradeFSM):
        self._data['fsm'] = fsm.to_dict()

    def _fsm_try(self, fsm: TradeFSM, action_name: str, fn):
        """Run an FSM transition defensively. Logs if invalid rather than
        crashing — protects backward compat while we migrate.
        """
        try:
            fn()
            self._persist_fsm(fsm)
            return True
        except InvalidTransitionError as e:
            _log.warning(f"[FSM] {action_name} rejected from state {fsm.state.value}: {e}")
            return False

    def get_trade_id(self):
        """Return current trade_id (None if no active trade)."""
        return self._fsm().trade_id

    def get_fsm_state(self):
        """Return current FSM state string."""
        return self._fsm().state.value

    # ── High-level transitions ──

    def open_signal(self, direction, entry_price, channel, initial_lot, start_balance=0.0):
        """New signal opened by brain.

        start_balance: MT5 account balance at the instant of the order fill
        (from the EA heartbeat). Stored as the anchor for true P&L and signal DD.
        """
        # v3.3 FSM: IDLE → OPENING → OPEN (we assume sync broker fill for now)
        fsm = self._fsm()
        if fsm.state != TradeState.IDLE:
            # Defensive: if the previous cycle left FSM in non-IDLE (e.g. crash),
            # forcibly reset so we don't block legitimate new signals.
            _log.warning(f"[FSM] open_signal called while FSM={fsm.state.value}; force reset to IDLE")
            fsm = TradeFSM()
        tid = fsm.on_open_requested()
        fsm.on_open_filled()
        self._data.update({
            'active': True,
            'direction': direction,
            'entry_price': entry_price,
            'channel': channel,
            'source': 'brain',
            'opened_at': time.time(),
            'opened_ts': time.time(),
            'breakeven_set': False,
            'sl_price': 0.0,
            'tp_price': 0.0,
            'zones_averaged': [],
            'avg_count': 0,
            'initial_lot': initial_lot,
            'total_lots': initial_lot,
            'closing': False,
            'last_msg_ts': time.time(),
            'status': 'OPEN',
            'signal_start_balance': float(start_balance or 0),
            'signal_end_balance': 0.0,
            'fsm': fsm.to_dict(),
            'executor_plan': {},
        })
        self.save()
        _log.info(f"[FSM] Trade opened: id={tid} {direction} @ {entry_price} · start_bal=${float(start_balance or 0):.2f}")

    def adopt_positions(self, positions, start_balance=0.0):
        """Adopt existing positions (brain takes over manual/legacy trades).

        positions: list of position dicts from brain_positions.json
        start_balance: current MT5 account balance (anchor for true P&L).
        Derives direction + weighted entry from the positions.
        """
        if not positions:
            return False

        # All positions should be same direction (if not, we pick majority by volume)
        buy_vol = sum(p.get('volume', 0) for p in positions if p.get('type') == 'BUY')
        sell_vol = sum(p.get('volume', 0) for p in positions if p.get('type') == 'SELL')
        direction = 'BUY' if buy_vol > sell_vol else 'SELL'

        # Weighted entry
        total = 0
        weighted = 0
        for p in positions:
            if p.get('type') != direction: continue  # skip opposite (shouldn't exist normally)
            v = p.get('volume', 0)
            ep = p.get('price_open', 0)
            total += v
            weighted += v * ep
        entry = (weighted / total) if total > 0 else 0

        oldest_time = min((p.get('time', time.time()) for p in positions), default=time.time())

        # v3.3 FSM: adopted positions already exist → IDLE → OPENING → OPEN
        fsm = self._fsm()
        if fsm.state != TradeState.IDLE:
            _log.warning(f"[FSM] adopt_positions while FSM={fsm.state.value}; force reset")
            fsm = TradeFSM()
        tid = fsm.on_open_requested()
        fsm.on_open_filled()
        self._data.update({
            'active': True,
            'direction': direction,
            'entry_price': round(entry, 2),
            'channel': 'ADOPTED',
            'source': 'adopted',
            'opened_at': oldest_time,
            'opened_ts': time.time(),  # adoption time
            'breakeven_set': False,
            'sl_price': 0.0,
            'tp_price': 0.0,
            'zones_averaged': [],  # we don't know where past averagings happened
            'avg_count': max(0, len(positions) - 1),  # approximate
            'initial_lot': positions[0].get('volume', 0) if positions else 0,
            'total_lots': round(total, 2),
            'closing': False,
            'last_msg_ts': time.time(),
            'status': 'OPEN',
            'signal_start_balance': float(start_balance or 0),
            'signal_end_balance': 0.0,
            'fsm': fsm.to_dict(),
            'executor_plan': {},
        })
        self.save()
        _log.info(f"[FSM] Trade adopted: id={tid} {direction} @ {round(entry, 2)} · start_bal=${float(start_balance or 0):.2f}")
        return True

    def add_averaging(self, zone_price, lot):
        """Record an averaging. Transient MANAGING state around this."""
        fsm = self._fsm()
        if fsm.state == TradeState.OPEN:
            self._fsm_try(fsm, "on_manage_start", fsm.on_manage_start)
        self._data['zones_averaged'].append({
            'price': zone_price,
            'ts': time.time(),
            'lot': lot,
        })
        self._data['avg_count'] = len(self._data['zones_averaged'])
        # total_lots: sync with broker reality instead of accumulating.
        # Accumulating drifts over time when tickets close (partials, Executor
        # closures, zone captures) because add_averaging only sees opens, not
        # closes. Reading broker positions gives us the true live lot total.
        # Fall back to incremental if read fails.
        try:
            import os, json as _json
            _POS = os.path.join(
                r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files",
                "brain_positions.json"
            )
            with open(_POS, 'r', encoding='utf-8') as _f:
                _d = _json.load(_f)
            _live = sum(float(p.get('volume', 0) or 0) for p in (_d.get('positions') or []))
            # Broker file reflects state BEFORE the new averaging fills (EA
            # writes ~1s later). Add the just-queued lot for the effective
            # post-fill total.
            if _live > 0:
                self._data['total_lots'] = round(_live + lot, 2)
            else:
                # Positions file empty/stale — fall back to incremental
                self._data['total_lots'] = round(self._data['total_lots'] + lot, 2)
        except Exception:
            self._data['total_lots'] = round(self._data['total_lots'] + lot, 2)
        avg_count = self._data['avg_count']
        total_lots = self._data['total_lots']
        direction = self._data.get('direction', '?')
        if fsm.state == TradeState.MANAGING:
            self._fsm_try(fsm, "on_manage_done", fsm.on_manage_done)
        self.save()
        # TG notification
        try:
            from trader_brain import notify
            notify("averaging",
                   f"⚡ AVG #{avg_count} {direction} {lot} @ {zone_price:.2f}  "
                   f"·  total {total_lots:.2f} lots")
        except Exception:
            pass

    def mark_closing(self):
        """Mark signal as closing (TG 'cerramos' received)."""
        fsm = self._fsm()
        self._fsm_try(fsm, "on_closing_requested", fsm.on_closing_requested)
        self._data['closing'] = True
        self._data['status'] = 'CLOSING'
        self._data['last_msg_ts'] = time.time()
        tid = fsm.trade_id or "?"
        direction = self._data.get('direction', '?')
        self.save()
        try:
            from trader_brain import notify
            notify("closing_received",
                   f"⚠️ CLOSING received · TG 'cerramos' ({direction} trade `{tid[:10]}`)")
        except Exception:
            pass

    def set_breakeven(self, sl_price=None):
        """Mark signal as breakeven (no more averaging, no reentry)."""
        self._data['breakeven_set'] = True
        self._data['breakeven_pending'] = False
        self._data['breakeven_pending_since'] = 0.0
        self._data['breakeven_target_price'] = 0.0
        if sl_price:
            self._data['sl_price'] = sl_price
        self._data['last_msg_ts'] = time.time()
        direction = self._data.get('direction', '?')
        tid = self._fsm().trade_id or "?"
        self.save()
        try:
            from trader_brain import notify
            notify("breakeven_set",
                   f"🔒 BE set · {direction} trade `{tid[:10]}` · no more averagings")
        except Exception:
            pass

    def request_breakeven(self, sl_price=None):
        """Record a BE request; broker reconciliation confirms it later."""
        self._data['breakeven_pending'] = True
        self._data['breakeven_pending_since'] = time.time()
        self._data['breakeven_target_price'] = _f(sl_price, 0.0) if sl_price else 0.0
        self._data['last_msg_ts'] = time.time()
        self.save()

    def reconcile_with_broker(self, positions, balance=0.0):
        """Refresh signal state from live MT5 positions/account.

        This is the source-of-truth sync step: if the broker disagrees with our
        local memory, broker wins.
        """
        if not self.is_active():
            return False

        changed = False
        summary = summarize_broker_positions(positions)
        if positions:
            for key, value in (
                ('direction', summary['direction']),
                ('entry_price', summary['entry_price']),
                ('total_lots', summary['total_lots']),
            ):
                if value and self._data.get(key) != value:
                    self._data[key] = value
                    changed = True

            new_sl = summary['uniform_sl']
            new_tp = summary['uniform_tp']
            if round(_f(self._data.get('sl_price', 0)), 2) != round(new_sl, 2):
                self._data['sl_price'] = new_sl
                changed = True
            if round(_f(self._data.get('tp_price', 0)), 2) != round(new_tp, 2):
                self._data['tp_price'] = new_tp
                changed = True

            broker_be = bool(summary['protected'])
            if bool(self._data.get('breakeven_set')) != broker_be:
                self._data['breakeven_set'] = broker_be
                changed = True
            if broker_be and self._data.get('breakeven_pending'):
                self._data['breakeven_pending'] = False
                self._data['breakeven_pending_since'] = 0.0
                self._data['breakeven_target_price'] = 0.0
                changed = True
            elif self._data.get('breakeven_pending'):
                pending_since = _f(self._data.get('breakeven_pending_since', 0), 0.0)
                if pending_since and (time.time() - pending_since) > _BREAKEVEN_CONFIRM_TIMEOUT_S:
                    self._data['breakeven_pending'] = False
                    self._data['breakeven_pending_since'] = 0.0
                    self._data['breakeven_target_price'] = 0.0
                    changed = True
                    _log.warning("[BE] Pending breakeven expired without broker confirmation")

        start_bal = _f(self._data.get('signal_start_balance', 0), 0.0)
        live_bal = _f(balance, 0.0)
        if start_bal > 0 and live_bal > 0:
            realized = round(live_bal - start_bal, 2)
            if round(_f(self._data.get('realized_profit', 0), 0.0), 2) != realized:
                self._data['realized_profit'] = realized
                changed = True

        if changed:
            self.save()
        return changed

    def close_signal(self, end_balance=0.0):
        """Reset state — signal fully closed. CLOSING → CLOSED → IDLE.

        end_balance: MT5 account balance AFTER all tickets have been closed.
        If provided + signal_start_balance is set, the TG alert reports the
        authoritative net P&L (= end − start, includes commission/spread/swap).
        Falls back to the gross realized_profit if balance anchors unavailable.
        """
        fsm = self._fsm()
        tid = fsm.trade_id
        direction = self._data.get('direction', '?')
        realized_gross = float(self._data.get('realized_profit', 0) or 0)
        start_bal = float(self._data.get('signal_start_balance', 0) or 0)
        # Ground-truth net P&L from balance delta (only valid when tickets are
        # closed — caller must pass end_balance AFTER close_all_brain settled).
        if end_balance and start_bal:
            net_pnl = float(end_balance) - start_bal
            self._data['signal_end_balance'] = float(end_balance)
        else:
            net_pnl = realized_gross
        avg_count = self._data.get('avg_count', 0)
        opened_ts = self._data.get('opened_ts', 0)
        duration_s = (time.time() - opened_ts) if opened_ts else 0
        # Force through CLOSED then IDLE.
        if fsm.state == TradeState.CLOSING:
            self._fsm_try(fsm, "on_all_closed", fsm.on_all_closed)
        elif fsm.state in (TradeState.OPEN, TradeState.MANAGING):
            self._fsm_try(fsm, "on_closing_requested", fsm.on_closing_requested)
            self._fsm_try(fsm, "on_all_closed", fsm.on_all_closed)
        elif fsm.state == TradeState.OPENING:
            self._fsm_try(fsm, "on_aborted", fsm.on_aborted)
        if fsm.state == TradeState.CLOSED:
            self._fsm_try(fsm, "reset_to_idle", fsm.reset_to_idle)
        if tid:
            _log.info(f"[FSM] Trade closed: id={tid}")
        self._data = dict(self.EMPTY)
        self._data['fsm'] = fsm.to_dict()
        self.save()
        # Clear the executor ladder so a future signal starts with a clean
        # PARTIAL_CLOSE level map. Best-effort — never crash the close path.
        try:
            import executor_ladder as _el
            _el.clear()
        except Exception:
            pass
        # Fire LLM post-mortem in background — non-blocking, best-effort.
        # Uses DeepSeek reasoner; takes ~30s. If the LLM call fails, the
        # trade is still closed cleanly; the post-mortem can be re-run
        # later via `python trade_postmortem_llm.py --trade-id <tid>`.
        if tid:
            try:
                import trade_postmortem_llm as _pm
                _pm.run_async(tid)
            except Exception as _pme:
                _log.warning(f"[POSTMORTEM] failed to schedule for {tid}: {_pme}")
        # TG notification with summary — uses NET P&L from balance delta
        # when available (ground truth), gross realized otherwise.
        if tid:
            try:
                from trader_brain import notify
                mins = int(duration_s // 60)
                pnl_sign = "+" if net_pnl >= 0 else "−"
                # Show both figures when they diverge (commissions eaten)
                fees_str = ""
                if end_balance and start_bal and abs(net_pnl - realized_gross) >= 0.5:
                    fees = realized_gross - net_pnl
                    fees_str = f" (gross {realized_gross:+.2f} − fees ${fees:.2f})"
                notify("trade_closed",
                       f"🔒 CLOSED {direction} · P&L {pnl_sign}${abs(net_pnl):.2f}{fees_str} · "
                       f"{avg_count} avgs · {mins}min · `{tid[:10]}`")
            except Exception:
                pass

    def is_active(self):
        return self._data.get('active', False)

    def is_breakeven(self):
        return bool(self._data.get('breakeven_set', False) or self._data.get('breakeven_pending', False))

    def is_closing(self):
        return self._data.get('closing', False)


# Singleton pattern
_state = None

def get_state():
    global _state
    if _state is None:
        _state = SignalState()
    return _state
