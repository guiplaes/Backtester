"""TRADE MONITOR — Guardian tàctic del trade obert.

THREAD PARAL·LEL (2026-05-07): el monitor corre en el seu propi thread,
independent del main loop del brain. Tan aviat com arriba resposta de
DeepSeek, immediatament envia nova avaluació amb les dades noves.

Cycle real: només limitat per la latència de DeepSeek (3-5s típic).
Sense compartir temps amb FastEngine/INDICATOR/EXECUTOR.

Mode OBSERVATOR: registra les decisions però NO les aplica.
"""
import json
import os
import time
import threading
import logging

log = logging.getLogger("brain")

# Pausa MÍNIMA entre calls (després de rebre resposta).
# 0.5s deixa que arribin nous ticks/bars i evita saturar l'API.
INTER_CALL_PAUSE_S = 0.5

# Sleep quan no hi ha trade obert (poll cada N segons)
IDLE_POLL_S = 2.0

# Reset session quan acumulem N turns (evitar hallucination drift en models petits)
# DeepSeek-Flash comença a confabular després de ~15-20 turns. Reset proactiu.
MAX_TURNS = 20

# Cooldown per a no repetir mateixa acció dins finestra
ACTION_COOLDOWN_S = {
    'TIGHTEN_SL': 30,
    'PARTIAL_25': 60,
    'PARTIAL_50': 60,
    'MOVE_TP_CLOSER': 30,
    'MOVE_BE_NOW': 9999,  # un sol cop per trade
    'EXIT_NOW': 0,  # sense cooldown
}

# Confidence mínima per actuar (mode AUTHORITY)
# 0.90 = molt estricte. DeepSeek-Flash al·lucina amb facilitat fins a conf 0.80.
# Només actuar si CLARAMENT veu quelcom (>90% confidence).
MIN_CONFIDENCE = 0.90


class TradeMonitor:
    """Singleton manager del cycle Haiku quan hi ha trade obert."""

    def __init__(self):
        self.last_call_ts = 0.0
        self.turn_count = 0
        self.last_action = 'HOLD'
        self.last_action_ts = {}  # action → ts
        self.current_trade_id = None
        self.previous_response_pending = False
        # FASE 2: AUTHORITY (2026-05-07 amb payload focalitzat post-incident).
        # Payload sense float P&L (que va causar al·lucinació). Només
        # distàncies relatives + condicions vives. MIN_CONFIDENCE 0.90 per
        # extra protecció.
        self.observator_mode = False
        self.log_path = self._init_log_path()
        self.state_path = self._init_state_path()
        # Carrega state persistit al disc (sobreviu restart del brain)
        self._load_state()
        # Thread paral·lel — engegat per start_thread() des del trader_brain
        self._thread = None
        self._stop_event = None
        self._context_provider = None  # callable que retorna (sig_state, account, bars, flow, approach)
        self._call_claude_fn = None
        self._call_deepseek_fn = None
        self._system_prompt = ''

    def start_thread(self, context_provider, system_prompt: str,
                     call_claude_fn, call_deepseek_fn=None):
        """Llança el thread paral·lel del monitor. context_provider() ha de
        retornar (sig_state, account, bars_cache, flow_proxy_dict, approach_state)
        amb les dades més recents."""
        if self._thread is not None and self._thread.is_alive():
            return  # ja corrent
        self._context_provider = context_provider
        self._system_prompt = system_prompt
        self._call_claude_fn = call_claude_fn
        self._call_deepseek_fn = call_deepseek_fn
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name='TradeMonitor')
        self._thread.start()
        log.info("[MONITOR] Thread paral·lel engegat — vigilància continua")

    def _run_loop(self):
        """Loop principal del thread paral·lel."""
        log.info("[MONITOR] Loop start")
        _last_skip_log = 0
        while not self._stop_event.is_set():
            try:
                ctx = None
                try:
                    ctx = self._context_provider() if self._context_provider else None
                except Exception as _ce:
                    log.warning(f"[MONITOR] context_provider failed: {_ce}")
                    ctx = None

                if not ctx:
                    if time.time() - _last_skip_log > 30:
                        log.info("[MONITOR] no context yet, waiting")
                        _last_skip_log = time.time()
                    time.sleep(IDLE_POLL_S)
                    continue

                sig_state, account, bars_cache, flow_proxy_dict, approach_state = ctx

                if not sig_state or not sig_state.is_active():
                    if self.current_trade_id is not None:
                        self.reset_on_close()
                    if time.time() - _last_skip_log > 30:
                        log.info(f"[MONITOR] no active trade (sig_active={sig_state.is_active() if sig_state else 'None'})")
                        _last_skip_log = time.time()
                    time.sleep(IDLE_POLL_S)
                    continue

                if not account or not account.get('positions'):
                    if time.time() - _last_skip_log > 30:
                        log.info(f"[MONITOR] sig active but no positions yet (positions={account.get('positions') if account else 'None'})")
                        _last_skip_log = time.time()
                    time.sleep(IDLE_POLL_S)
                    continue

                if account.get('closing'):
                    time.sleep(IDLE_POLL_S)
                    continue

                # Detect new trade
                trade_id = sig_state._data.get('trade_id') or str(sig_state._data.get('opened_at', 0))
                if self.current_trade_id != trade_id:
                    self.reset_for_new_trade(trade_id)

                # 2026-05-07: STATELESS mode — no session continuity, no MAX_TURNS reset

                # Build payload + send call (síncron, retorna quan resposta arriba)
                self._do_one_call(sig_state, account, bars_cache, flow_proxy_dict, approach_state)

                # Petita pausa abans del proper call (deixa arribar nous ticks)
                time.sleep(INTER_CALL_PAUSE_S)

            except Exception as e:
                log.warning(f"[MONITOR] Loop iteration failed: {e}")
                time.sleep(IDLE_POLL_S)

    def _do_one_call(self, sig_state, account, bars_cache, flow_proxy_dict, approach_state):
        """Fa una crida STATELESS i loguja la decisió.

        2026-05-07: Eliminada acumulació de context. Cada call envia el
        payload COMPLET (estructura del trade + condicions vives) i el
        model decideix de zero. Sense session continuity = sense drift.
        """
        self.previous_response_pending = True
        self.last_call_ts = time.time()

        try:
            # SEMPRE el payload complet (no delta). Cada call és independent.
            payload = self.build_initial_payload(
                sig_state, account, bars_cache, flow_proxy_dict, approach_state
            )

            t0 = time.time()
            if self._call_deepseek_fn is not None:
                try:
                    # Sense conversation_role → SENSE session continuity
                    response = self._call_deepseek_fn(
                        payload, self._system_prompt, label='MONITOR',
                        reasoning=False,
                        model='deepseek-v4-flash',
                        conversation_role=None,  # STATELESS
                    )
                except Exception as _e:
                    log.warning(f"[MONITOR] DeepSeek failed ({_e}), fallback Haiku CLI")
                    response = self._call_claude_fn(
                        payload, self._system_prompt, label='MONITOR',
                        model='claude-haiku-4-5', effort=None, session_role=None,
                    )
            else:
                response = self._call_claude_fn(
                    payload, self._system_prompt, label='MONITOR',
                    model='claude-haiku-4-5', effort=None, session_role=None,
                )
            elapsed = time.time() - t0

            self.turn_count += 1
            self.previous_response_pending = False
            self._save_state()

            if not response:
                log.warning(f"[MONITOR] Empty response (elapsed {elapsed:.1f}s)")
                return

            decision = response if isinstance(response, dict) else {}
            action = decision.get('action', 'HOLD')
            confidence = float(decision.get('confidence', 0) or 0)
            reason = decision.get('reason', '')

            context = {
                'price': float(bars_cache[-1].get('close', 0)) if bars_cache else 0,
                'elapsed_s': int(time.time() - (sig_state._data.get('opened_at') or time.time())),
                'haiku_response_s': round(elapsed, 1),
            }
            self._log_decision(decision, context)

            log.info(
                f"[MONITOR] turn={self.turn_count} action={action} "
                f"conf={confidence:.2f} | {reason[:80]} ({elapsed:.1f}s)"
            )

            if action != 'HOLD' and confidence >= MIN_CONFIDENCE:
                if self.observator_mode:
                    log.info(
                        f"[MONITOR] [OBSERVATOR MODE] WOULD apply: {action} "
                        f"(params={decision.get('params', {})}) — NO aplicat"
                    )
                else:
                    self._apply_action(decision, sig_state, account)

            self.last_action = action

        except Exception as e:
            log.warning(f"[MONITOR] _do_one_call failed: {e}")
            self.previous_response_pending = False

    def _init_log_path(self):
        """Path del log dedicat."""
        try:
            base = os.path.dirname(os.path.abspath(__file__))
            log_dir = os.path.join(base, 'logs')
            os.makedirs(log_dir, exist_ok=True)
            return os.path.join(log_dir, 'trade_monitor.log')
        except Exception:
            return None

    def _init_state_path(self):
        """Path persistent state per sobreviure brain restarts."""
        try:
            base = os.path.dirname(os.path.abspath(__file__))
            return os.path.join(base, 'logs', 'trade_monitor_state.json')
        except Exception:
            return None

    def _save_state(self):
        """Persisteix state actual a disc."""
        if not self.state_path:
            return
        try:
            state = {
                'turn_count': self.turn_count,
                'current_trade_id': self.current_trade_id,
                'last_action': self.last_action,
                'last_action_ts': self.last_action_ts,
                'last_call_ts': self.last_call_ts,
                'updated_at': time.time(),
            }
            with open(self.state_path, 'w', encoding='utf-8') as f:
                json.dump(state, f)
        except Exception:
            pass

    def _load_state(self):
        """Carrega state des de disc (manté turn_count entre restarts)."""
        if not self.state_path or not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path, 'r', encoding='utf-8') as f:
                state = json.load(f)
            self.turn_count = int(state.get('turn_count', 0) or 0)
            self.current_trade_id = state.get('current_trade_id')
            self.last_action = state.get('last_action', 'HOLD')
            self.last_action_ts = state.get('last_action_ts') or {}
            self.last_call_ts = float(state.get('last_call_ts', 0) or 0)
            log.info(f"[MONITOR] State loaded: turn={self.turn_count} trade={self.current_trade_id}")
        except Exception as e:
            log.debug(f"[MONITOR] state load failed: {e}")

    def _log_decision(self, decision: dict, context: dict):
        """Escriu la decisió al log dedicat (mai falla)."""
        try:
            if not self.log_path:
                return
            entry = {
                'ts': time.time(),
                'iso': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()),
                'turn': self.turn_count,
                'mode': 'OBSERVATOR' if self.observator_mode else 'ACTIVE',
                'context': context,
                'decision': decision,
            }
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except Exception:
            pass

    def reset_for_new_trade(self, trade_id: str):
        """Crida quan es detecta un trade NOU. Si és el mateix trade
        post-restart, NO resetejar (preservem turn_count)."""
        if self.current_trade_id == trade_id:
            log.info(f"[MONITOR] Same trade {trade_id} after restart — preserving turn={self.turn_count}")
            self.previous_response_pending = False
            return
        try:
            import claude_session_manager as _csm
            _csm.reset('TRADE_MONITOR')
        except Exception:
            pass
        self.turn_count = 0
        self.last_action = 'HOLD'
        self.last_action_ts = {}
        self.current_trade_id = trade_id
        self.previous_response_pending = False
        self._save_state()
        log.info(f"[MONITOR] Reset for new trade {trade_id}")

    def reset_on_close(self):
        """Crida quan el trade es tanca."""
        try:
            import claude_session_manager as _csm
            _csm.reset('TRADE_MONITOR')
        except Exception:
            pass
        self.turn_count = 0
        self.current_trade_id = None
        self._save_state()
        log.info(f"[MONITOR] Session reset on trade close")

    def should_call(self, sig_state) -> bool:
        """Decideix si fem un call ARA."""
        if not sig_state.is_active():
            return False
        if self.previous_response_pending:
            return False
        if (time.time() - self.last_call_ts) < CYCLE_SECONDS:
            return False
        if self.turn_count >= MAX_TURNS:
            # Reset per evitar bloating
            log.info(f"[MONITOR] Hit MAX_TURNS={MAX_TURNS}, resetting session")
            try:
                import claude_session_manager as _csm
                _csm.reset('TRADE_MONITOR')
            except Exception:
                pass
            self.turn_count = 0
        return True

    def build_initial_payload(self, sig_state, account, bars_cache, flow_proxy_dict, approach_state) -> str:
        """Turn 1: context complet del trade."""
        plan = sig_state._data.get('executor_plan') or {}
        positions = account.get('positions') or []
        entry = sig_state._data.get('entry_price') or 0
        direction = sig_state._data.get('direction') or '?'
        lot = sum(float(p.get('volume', 0) or 0) for p in positions)
        opened_at = sig_state._data.get('opened_at', 0)
        elapsed = max(0, time.time() - opened_at) if opened_at else 0
        last_bar = bars_cache[-1] if bars_cache else {}
        price = float(last_bar.get('close', 0) or 0)

        # Plan resumit (manegant None per trades adoptats manualment)
        tp_target = float(plan.get('tp_target') or 0)
        wsl = float(plan.get('wick_dynamic_sl') or 0)
        be_trig = plan.get('breakeven_trigger') or {}
        be_price = float(be_trig.get('price') or 0) if isinstance(be_trig, dict) else 0
        pts = plan.get('profit_targets') or []
        ac_conds = plan.get('auto_close_conditions') or []
        invalidation_level = 0.0
        for c in ac_conds:
            if c.get('action') == 'FULL_CLOSE':
                try:
                    invalidation_level = float(c.get('level') or 0)
                except Exception:
                    invalidation_level = 0
                break

        # Flow proxy resumit
        spot_cmf = flow_proxy_dict.get('spot', {}).get('cmf_m15', {}).get('value', 0) or 0
        fut_cmf = flow_proxy_dict.get('futures', {}).get('cmf_m15', {}).get('value', 0) or 0
        spot_obv_4h = flow_proxy_dict.get('spot', {}).get('obv_h1', {}).get('change_4h', 0) or 0
        fut_obv_4h = flow_proxy_dict.get('futures', {}).get('obv_h1', {}).get('change_4h', 0) or 0

        # Approach
        delta_acc = approach_state.get('delta_acc', 0) if approach_state else 0
        signal_strength = approach_state.get('signal_strength', 0) if approach_state else 0

        # Detectar trade sense plan
        has_plan = (tp_target > 0 and wsl > 0)
        plan_warning = "" if has_plan else """
⚠️ TRADE SENSE PLAN COMPLET. Vigila només moviments extrems del preu/flux.
   Si el preu està estable, sempre HOLD.
"""

        favor = "DOWN" if direction == 'SELL' else "UP"

        payload = f"""TRADE OBERT — vigila'l.
{plan_warning}
PLAN del trade:
- Direction: {direction} (favor: preu {favor})
- Entry: ${entry:.2f}
- TP target: ${tp_target:.2f}
- BE trigger: ${be_price:.2f}
- SL virtual: ${wsl:.2f}
- Invalidation M5 close: ${invalidation_level:.2f}

MERCAT ACTUAL:
- Preu: ${price:.2f} (a {price-entry:+.2f}$ d'entry)
- M1: O={last_bar.get('open',0):.2f} H={last_bar.get('high',0):.2f} L={last_bar.get('low',0):.2f} C={last_bar.get('close',0):.2f} V={last_bar.get('volume',0)}
- Approach: delta_acc={delta_acc:+.0f}, signal_strength={signal_strength:+.2f}
- Flux: spot CMF M15 {spot_cmf:+.3f}, futures CMF M15 {fut_cmf:+.3f}
- OBV 4h: spot {spot_obv_4h:+.0f}, futures {fut_obv_4h:+.0f}

Vigila si les condicions afavoreixen arribar a TP o s'estan degradant cap a SL.
Aquesta és una avaluació INDEPENDENT — no necessites cap context d'avaluacions anteriors.
Decisió per aquest moment exacte?
"""
        return payload

    def build_delta_payload(self, sig_state, account, bars_cache, flow_proxy_dict, approach_state) -> str:
        """Turn N: payload focalitzat en POSICIÓ del preu vs PLAN + condicions vives.
        NO incloem float P&L (causa al·lucinació). Només estructura + moment.
        """
        plan = sig_state._data.get('executor_plan') or {}
        entry = sig_state._data.get('entry_price') or 0
        last_bar = bars_cache[-1] if bars_cache else {}
        price = float(last_bar.get('close', 0) or 0)
        direction = sig_state._data.get('direction') or '?'

        # Plan resumit
        tp_target = float(plan.get('tp_target') or 0)
        wsl = float(plan.get('wick_dynamic_sl') or 0)
        be_price = 0
        be_trig = plan.get('breakeven_trigger') or {}
        if isinstance(be_trig, dict):
            be_price = float(be_trig.get('price') or 0)
        be_set = sig_state._data.get('breakeven_set', False)

        # Distàncies clau (RELATIVES, no absolutes — el que importa)
        if price > 0 and entry > 0:
            dist_entry = price - entry
            dist_tp = (price - tp_target) if tp_target > 0 else None
            dist_sl = (price - wsl) if wsl > 0 else None
        else:
            dist_entry = dist_tp = dist_sl = None

        # Approach + flux (condicions vives)
        delta_acc = approach_state.get('delta_acc', 0) if approach_state else 0
        signal_strength = approach_state.get('signal_strength', 0) if approach_state else 0
        spot_cmf = flow_proxy_dict.get('spot', {}).get('cmf_m15', {}).get('value', 0) or 0
        fut_cmf = flow_proxy_dict.get('futures', {}).get('cmf_m15', {}).get('value', 0) or 0

        # Direcció favor — quina direcció és "bona" per al trade
        favor = "DOWN" if direction == 'SELL' else "UP"

        payload = f"""POSICIÓ del preu RESPECTE al plan:

Preu actual: ${price:.2f}
Entry: ${entry:.2f} (delta {dist_entry:+.2f}$ — favor {favor})
TP target: ${tp_target:.2f} (queden {abs(dist_tp):.2f}$ si arriba)
SL virtual: ${wsl:.2f} (a {abs(dist_sl):.2f}$ del preu)
BE: {'ACTIVAT' if be_set else 'no activat ($' + f'{be_price:.2f}' + ')'}

CONDICIONS DEL MOMENT:
M1 last: O={last_bar.get('open',0):.2f} H={last_bar.get('high',0):.2f} L={last_bar.get('low',0):.2f} C={last_bar.get('close',0):.2f} V={last_bar.get('volume',0)}
Approach: delta_acc={delta_acc:+.0f}, signal_strength={signal_strength:+.2f}
Flux: spot CMF {spot_cmf:+.3f}, futures CMF {fut_cmf:+.3f}

Última decisió: {self.last_action}

El moment afavoreix arribar a TP o s'està degradant cap a SL? Decisió?
"""
        return payload

    def call(self, sig_state, account, bars_cache, flow_proxy_dict, approach_state, system_prompt, call_claude_fn, call_deepseek_fn=None):
        """Fa una crida amb el delta data. Retorna dict decisió o None.

        Prefer DeepSeek API directa (2-5s típic) per mínima latència.
        Fallback a Claude CLI si DeepSeek no disponible.

        call_claude_fn: funció _call_claude del trader_brain (CLI subscripció)
        call_deepseek_fn: funció _call_deepseek (API directa, més ràpid)
        """
        if not self.should_call(sig_state):
            return None

        self.previous_response_pending = True
        self.last_call_ts = time.time()

        try:
            if self.turn_count == 0:
                payload = self.build_initial_payload(
                    sig_state, account, bars_cache, flow_proxy_dict, approach_state
                )
            else:
                payload = self.build_delta_payload(
                    sig_state, account, bars_cache, flow_proxy_dict, approach_state
                )

            t0 = time.time()
            # PRIORITAT DeepSeek API directa (latència 2-5s vs CLI 15-20s)
            if call_deepseek_fn is not None:
                try:
                    response = call_deepseek_fn(
                        payload, system_prompt, label='MONITOR',
                        reasoning=False,  # API call sense reasoning explícit
                        model='deepseek-v4-flash',  # V4 flash: ~5s amb judici intern, qualitat superior a V3-chat
                        conversation_role='TRADE_MONITOR',  # session continuity
                    )
                except Exception as _e:
                    log.warning(f"[MONITOR] DeepSeek failed ({_e}), fallback Haiku CLI")
                    response = call_claude_fn(
                        payload, system_prompt, label='MONITOR',
                        model='claude-haiku-4-5', effort=None, session_role='TRADE_MONITOR',
                    )
            else:
                response = call_claude_fn(
                    payload, system_prompt, label='MONITOR',
                    model='claude-haiku-4-5', effort=None, session_role='TRADE_MONITOR',
                )
            elapsed = time.time() - t0

            self.turn_count += 1
            self.previous_response_pending = False
            self._save_state()  # persisteix per sobreviure restarts

            if not response:
                log.warning(f"[MONITOR] Empty response (elapsed {elapsed:.1f}s)")
                return None

            # Resposta JSON
            decision = response if isinstance(response, dict) else {}
            action = decision.get('action', 'HOLD')
            confidence = float(decision.get('confidence', 0) or 0)
            reason = decision.get('reason', '')

            # Log structured
            context = {
                'price': float(bars_cache[-1].get('close', 0)) if bars_cache else 0,
                'elapsed_s': int(time.time() - (sig_state._data.get('opened_at') or time.time())),
                'haiku_response_s': round(elapsed, 1),
            }
            self._log_decision(decision, context)

            # Console log condensat
            log.info(
                f"[MONITOR] turn={self.turn_count} action={action} "
                f"conf={confidence:.2f} | {reason[:80]} ({elapsed:.1f}s)"
            )

            # Aplicar acció (mode actiu) — per ara mode OBSERVATOR
            if action != 'HOLD' and confidence >= MIN_CONFIDENCE:
                if self.observator_mode:
                    log.info(
                        f"[MONITOR] [OBSERVATOR MODE] WOULD apply: {action} "
                        f"(params={decision.get('params', {})}) — NO aplicat"
                    )
                else:
                    self._apply_action(decision, sig_state, account)

            self.last_action = action
            return decision

        except Exception as e:
            log.warning(f"[MONITOR] call failed: {e}")
            self.previous_response_pending = False
            return None

    def _apply_action(self, decision: dict, sig_state, account):
        """Aplica una acció (només si NO observator). Implementat però desactivat per FASE 1."""
        action = decision.get('action')
        params = decision.get('params') or {}
        reason = decision.get('reason', '')
        confidence = decision.get('confidence', 0)

        # Cooldown check
        cooldown = ACTION_COOLDOWN_S.get(action, 0)
        last = self.last_action_ts.get(action, 0)
        if (time.time() - last) < cooldown:
            log.info(f"[MONITOR] {action} cooldown active, skipping")
            return

        try:
            from trader_brain import (
                partial_close_pct, close_all_brain, move_sl_entry, notify
            )

            if action == 'TIGHTEN_SL':
                new_sl = float(params.get('price', 0) or 0)
                if new_sl <= 0:
                    return
                # SAFETY: només cap a entry, mai més enllà
                direction = sig_state._data.get('direction')
                entry = sig_state._data.get('entry_price', 0)
                old_sl = sig_state._data.get('executor_plan', {}).get('wick_dynamic_sl', 0)
                if direction == 'BUY' and not (old_sl < new_sl <= entry):
                    log.warning(f"[MONITOR] TIGHTEN_SL rejected — new {new_sl} no està entre {old_sl} i {entry}")
                    return
                if direction == 'SELL' and not (entry <= new_sl < old_sl):
                    log.warning(f"[MONITOR] TIGHTEN_SL rejected — new {new_sl} no està entre {entry} i {old_sl}")
                    return
                # Update plan
                sig_state._data['executor_plan']['wick_dynamic_sl'] = new_sl
                sig_state.save()
                notify('monitor', f"🎯 SL TIGHTENED a ${new_sl:.2f}\n📝 {reason}")
                self.last_action_ts[action] = time.time()

            elif action == 'PARTIAL_25' or action == 'PARTIAL_50':
                pct = 25 if action == 'PARTIAL_25' else 50
                positions = account.get('positions') or []
                for p in positions:
                    tk = int(p.get('ticket', 0) or 0)
                    if tk:
                        partial_close_pct(tk, pct)
                notify('monitor', f"💰 PARCIAL {pct}% defensiu\n📝 {reason}")
                self.last_action_ts[action] = time.time()

            elif action == 'MOVE_BE_NOW':
                if sig_state._data.get('breakeven_set'):
                    return
                ok = move_sl_entry()
                if ok:
                    sig_state.request_breakeven(sl_price=sig_state._data.get('entry_price'))
                    notify('monitor', f"🛡️ BE ACTIVAT (forçat)\n📝 {reason}")
                    self.last_action_ts[action] = time.time()

            elif action == 'EXIT_NOW':
                close_all_brain()
                sig_state._data['closing'] = True
                sig_state._data['status'] = 'CLOSING'
                sig_state.save()
                notify('monitor', f"🛑 EXIT FORÇAT\n📝 {reason}")
                self.last_action_ts[action] = time.time()

            elif action == 'MOVE_TP_CLOSER':
                # Refresh LADDER level
                tp_lvl = params.get('tp_level', 'TP1')
                new_price = float(params.get('price', 0) or 0)
                if new_price <= 0:
                    return
                # SAFETY: només més tight
                pts = sig_state._data.get('executor_plan', {}).get('profit_targets', [])
                if pts:
                    direction = sig_state._data.get('direction')
                    entry = sig_state._data.get('entry_price', 0)
                    idx = 0 if tp_lvl == 'TP1' else 1
                    if idx < len(pts):
                        old_price = pts[idx].get('price', 0)
                        if direction == 'BUY' and not (entry < new_price < old_price):
                            log.warning(f"[MONITOR] MOVE_TP_CLOSER rejected: {new_price} no entre {entry} i {old_price}")
                            return
                        if direction == 'SELL' and not (old_price < new_price < entry):
                            log.warning(f"[MONITOR] MOVE_TP_CLOSER rejected: {new_price} no entre {old_price} i {entry}")
                            return
                        pts[idx]['price'] = new_price
                        sig_state.save()
                        # Refresh ladder
                        try:
                            import executor_ladder as _el
                            _el.refresh_preserving_hits(pts, direction, entry_price=entry)
                        except Exception:
                            pass
                        notify('monitor', f"🎯 {tp_lvl} mogut a ${new_price:.2f}\n📝 {reason}")
                        self.last_action_ts[action] = time.time()

        except Exception as e:
            log.warning(f"[MONITOR] _apply_action {action} failed: {e}")


# Singleton
_monitor_instance = None


def get_monitor() -> TradeMonitor:
    """Retorna la instància singleton."""
    global _monitor_instance
    if _monitor_instance is None:
        _monitor_instance = TradeMonitor()
    return _monitor_instance


# Càrrega del prompt una vegada
_PROMPT_CACHE = None


def get_prompt() -> str:
    """Carrega el system prompt des de prompts/trade_monitor.txt."""
    global _PROMPT_CACHE
    if _PROMPT_CACHE is None:
        try:
            base = os.path.dirname(os.path.abspath(__file__))
            path = os.path.join(base, 'prompts', 'trade_monitor.txt')
            with open(path, 'r', encoding='utf-8') as f:
                _PROMPT_CACHE = f.read()
            log.info(f"[MONITOR] Prompt loaded: {len(_PROMPT_CACHE)} chars")
        except Exception as e:
            log.warning(f"[MONITOR] failed to load prompt: {e}")
            _PROMPT_CACHE = ""
    return _PROMPT_CACHE
