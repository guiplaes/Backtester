#!/usr/bin/env python3
"""Staged Setups — pre-defined entry plans waiting for FastEngine to fire.

The Executor in IDLE mode pre-stages up to 2 potential entries. This module
persists them to disk so they survive brain restarts, and provides the helpers
to check/expire/invalidate them every tick.

Schema (per setup):
  id, direction (BUY|SELL), zone_price, tolerance_atr, confirmations_needed (list),
  confirmations_min (int), expiration_minutes, invalidation_price, confidence,
  thesis, staged_at (ts)

File: brain_staged_setups.json in Common Files
"""
import os, json, time, threading
from datetime import datetime, timezone

COMMON = r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files"
STAGED_FILE = os.path.join(COMMON, 'brain_staged_setups.json')
BLACKLIST_FILE = os.path.join(COMMON, 'brain_staged_blacklist.json')

# Drop entries (direction, zone±5USD) cannot be re-staged within this many seconds.
BLACKLIST_TTL_S = 30 * 60   # 30 minutes
BLACKLIST_RADIUS_USD = 5.0

_lock = threading.Lock()


def load():
    """Return list of active (non-expired, non-invalidated) setups."""
    try:
        if not os.path.exists(STAGED_FILE):
            return []
        with open(STAGED_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        setups = data.get('setups', [])
        # Filter out dead ones (expired/invalidated tagged on save)
        alive = [s for s in setups if not s.get('_dead')]
        return alive
    except Exception:
        return []


def save(setups):
    """Persist list of setups."""
    with _lock:
        try:
            data = {
                'updated_at': datetime.now(timezone.utc).isoformat(),
                'setups': setups,
            }
            with open(STAGED_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass


def _parse_price_scalar(raw, direction='SELL'):
    """Accept any shape the LLM might emit for a zone price and return a float.

    Handles:
      · int / float → returned as-is
      · list/tuple [lo, hi] → direction-aware endpoint (SELL=hi, BUY=lo)
      · str "4720"            → float
      · str "4720-4725"       → range, endpoint by direction
      · str "4720 to 4725"    → range, endpoint by direction
      · str "4720/4725"       → range
      · str "around 4720"     → first number
    Returns None if it can't extract a sensible number.
    """
    import re as _re
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, (list, tuple)):
        nums = []
        for v in raw:
            try:
                nums.append(float(v))
            except Exception:
                pass
        if len(nums) >= 2:
            nums.sort()
            return nums[-1] if direction == 'SELL' else nums[0]
        if len(nums) == 1:
            return nums[0]
        return None
    if isinstance(raw, str):
        # Extract all numbers from the string
        matches = _re.findall(r"\d+(?:\.\d+)?", raw)
        if not matches:
            return None
        if len(matches) == 1:
            return float(matches[0])
        # Range: take direction-aware endpoint
        nums = sorted(float(m) for m in matches[:2])
        return nums[-1] if direction == 'SELL' else nums[0]
    return None


def _quality_to_confidence(q):
    """Map LLM 'quality' grade letters to numeric confidence."""
    if isinstance(q, (int, float)):
        return float(q)
    if not isinstance(q, str):
        return None
    m = {
        'A+': 0.95, 'A': 0.88, 'A-': 0.82,
        'B+': 0.78, 'B': 0.72, 'B-': 0.67,
        'C+': 0.60, 'C': 0.55, 'C-': 0.50,
        'D': 0.40, 'F': 0.30,
    }
    return m.get(q.strip().upper())


def _normalize_setup(s):
    """LLMs may emit alternative field names. Normalize to canonical schema."""
    # Accept common variations
    # Sources of zone_price candidates, in priority order. The winner is run
    # through _parse_price_scalar which accepts numbers, ranges, strings etc.
    _zp_sources = ['zone_price', 'price', 'entry_price', 'entry_zone',
                   'entry_zones', 'preferred_entry_zone', 'trigger_zone',
                   'target_price', 'trigger_price', 'zone']
    _zp_sources_dict = ['key_levels']  # dict → extract by direction

    aliases = {
        'direction': ['direction', 'side', 'type'],
        'invalidation_price': ['invalidation_price', 'stop_price', 'invalid_price', 'sl'],
        'confidence': ['confidence', 'conf', 'probability', 'conviction',
                       'entry_quality', 'quality_grade', 'setup_quality',
                       'strength', 'grade'],
        'thesis': ['thesis', 'reason', 'reasoning', 'hypothesis', 'narrative', 'initial_thesis'],
        'expiration_minutes': ['expiration_minutes', 'expiration', 'expires_in_min', 'ttl_min'],
        'tolerance_atr': ['tolerance_atr', 'tolerance', 'tol_atr'],
        'confirmations_needed': ['confirmations_needed', 'confirmations', 'confirms', 'required'],
        'confirmations_min': ['confirmations_min', 'min_confirmations', 'min_confirms'],
        'id': ['id', 'setup_id', 'name'],
        # Entry sizing: LLM picks 1 (conservative, base_lot×1=0.04) or 2
        # (standard, base_lot×2=0.08). Optional — defaults to 2 if absent.
        'lot_multiplier': ['lot_multiplier', 'entry_multiplier', 'size_multiplier',
                           'multiplier', 'entry_size'],
    }
    out = dict(s)  # copy
    for canonical, variants in aliases.items():
        if canonical not in out:
            for v in variants:
                if v in out and v != canonical:
                    out[canonical] = out[v]
                    break

    # ── Extract zone_price robustly from ANY shape the LLM may emit ──
    direction = out.get('direction')
    zp = None

    # Special case: entry_zones as a list of DICTS (each with price_area, multiplier…)
    # This is the "multi-level averaging plan" format. Take the FIRST entry's price_area.
    if 'entry_zones' in s and isinstance(s['entry_zones'], list) and s['entry_zones']:
        first = s['entry_zones'][0]
        if isinstance(first, dict):
            pa = (first.get('price_area') or first.get('price') or
                  first.get('zone_price') or first.get('entry_price') or
                  first.get('zone'))
            if pa is not None:
                zp = _parse_price_scalar(pa, direction=direction or 'SELL')

    # Try scalar sources (numbers, strings "4720", ranges "4720-4725", lists [lo,hi])
    if zp is None:
        for key in _zp_sources:
            if key in s:
                zp = _parse_price_scalar(s[key], direction=direction or 'SELL')
                if zp is not None:
                    break
    # Fallback: dict of key_levels → pick nearest support/resistance
    if zp is None:
        for key in _zp_sources_dict:
            if key in s and isinstance(s[key], dict):
                kl = s[key]
                if direction == 'SELL':
                    cand = (kl.get('nearest_resistance') or kl.get('next_resistance')
                             or kl.get('resistance') or kl.get('entry'))
                elif direction == 'BUY':
                    cand = (kl.get('nearest_support') or kl.get('next_support')
                             or kl.get('support') or kl.get('entry'))
                else:
                    cand = None
                if cand is not None:
                    zp = _parse_price_scalar(cand, direction=direction or 'SELL')
                if zp is not None:
                    break
    out['zone_price'] = zp

    # ── Extract confidence from multiple shapes (float, enum, letter grade) ──
    if out.get('confidence') is None:
        # Try 'quality' letter grade (LLM may emit quality: "A", "B+" etc.)
        if 'quality' in s:
            q = _quality_to_confidence(s.get('quality'))
            if q is not None:
                out['confidence'] = q
    # Coerce `confidence` to float — LLMs often emit enum strings (HIGH/MODERATE/LOW)
    # instead of 0.0-1.0. Map them to sensible floats so the min-confidence filter works.
    _conf = out.get('confidence')
    if isinstance(_conf, str):
        _map = {
            'HIGH': 0.85, 'VERY_HIGH': 0.92, 'EXTREME': 0.95,
            'MEDIUM': 0.75, 'MODERATE': 0.75, 'MED': 0.75,
            'LOW': 0.60, 'WEAK': 0.55, 'VERY_LOW': 0.45,
        }
        out['confidence'] = _map.get(_conf.strip().upper(), 0.7)
    elif _conf is None:
        out['confidence'] = 0.7
    else:
        try:
            out['confidence'] = float(_conf)
        except Exception:
            out['confidence'] = 0.7
    # Defaults
    out.setdefault('tolerance_atr', 0.5)
    out.setdefault('expiration_minutes', 30)
    out.setdefault('confirmations_needed', ['vol_ratio_1.2'])
    out.setdefault('confirmations_min', 1)
    out.setdefault('thesis', '')
    # Tactical fields (Executor-oriented)
    # 2026-05-04: NO default play_type='range_fade' — això crea confusió amb el
    # nou Mode Recorregut Institucional. play_type es queda absent o l'omple
    # explícitament el LLM si vol. El dashboard detecta 'recorregut' per la
    # presència de auto_close_conditions, no pel play_type.
    out.setdefault('play_type', None)
    out.setdefault('averaging_zones', [])
    out.setdefault('profit_targets', [])
    out.setdefault('tactical_plan', '')
    # Hunter-specific fields (pass through if provided, defaults otherwise)
    out.setdefault('source', 'executor')             # 'executor' | 'hunter'
    out.setdefault('post_close', False)              # True = waits for signal close to become eligible
    out.setdefault('confluences', [])                # list of strings
    out.setdefault('regime_at_stage', None)
    # Hunter-style TP/SL explicit prices + distances (for broker order)
    if 'profit_target' in s or 'invalidation' in s:
        pt = s.get('profit_target') or s.get('tp_price')
        inv = s.get('invalidation') or s.get('invalidation_price')
        try:
            if pt is not None:
                out['profit_target'] = float(pt)
        except Exception:
            pass
        try:
            if inv is not None:
                out['invalidation_price'] = float(inv)
        except Exception:
            pass
    out.setdefault('target_distance_usd', None)
    out.setdefault('stop_distance_usd', None)
    # ── 2026-05-04: nous camps Mode Recorregut Institucional ──
    # Camps qualitatius (text lliure pel LLM)
    out.setdefault('trap_thesis', '')              # qui queda atrapat, qui s'aprofita
    out.setdefault('tp_thesis', '')                # per què aquell destí amb recorregut
    out.setdefault('invalidation_thesis', '')      # quan mor la tesi (text)
    # ── 2026-05-05: setup_type — reversion vs breakout ──
    # reversion: preu arriba a la zona des del costat oposat i rebutja amb candle+vol
    # breakout: preu trenca per la zona amb close beyond + vol elevat (continuation)
    _stype = (s.get('setup_type') or out.get('setup_type') or 'reversion')
    if _stype not in ('reversion', 'breakout'):
        _stype = 'reversion'
    out['setup_type'] = _stype
    # 2026-05-05: entry_mode — instant (default) vs confirmed.
    # Instant: el toc del trigger_zone és el trigger, sense esperar candela
    # ni vol. Filosofia de scalping pur a zones STRONG.
    # Confirmed: l'antic mode (espera rejection_candle + vol) — opcional
    # per a zones MODERATE o setups dubtosos.
    _emode = (s.get('entry_mode') or out.get('entry_mode') or 'instant').lower()
    if _emode not in ('instant', 'confirmed', 'wick'):
        _emode = 'instant'
    out['entry_mode'] = _emode
    # Camps específics de breakout (opcionals)
    if _stype == 'breakout':
        out.setdefault('breakout_tf', s.get('breakout_tf', 'M5'))
        try:
            out['breakout_vol_min'] = float(s.get('breakout_vol_min', 1.5))
        except (TypeError, ValueError):
            out['breakout_vol_min'] = 1.5
        try:
            out['breakout_buffer_usd'] = float(s.get('breakout_buffer_usd', 0.5))
        except (TypeError, ValueError):
            out['breakout_buffer_usd'] = 0.5
    # tp_target = alias enriquit del profit_target (single target en mode recorregut)
    if 'tp_target' in s and out.get('profit_target') is None:
        try:
            out['profit_target'] = float(s['tp_target'])
        except Exception:
            pass
    if 'tp_target' not in out and out.get('profit_target') is not None:
        out['tp_target'] = out['profit_target']
    # auto_close_conditions: llista de condicions pre-aprovades pel LLM. Cada
    # condició és validada — les invàlides es filtren amb log.
    raw_acc = s.get('auto_close_conditions') or out.get('auto_close_conditions') or []
    out['auto_close_conditions'] = _normalize_auto_close_list(raw_acc)
    if 'id' not in out or not out['id']:
        _zp_for_id = out.get('zone_price') or 0  # None-safe
        try:
            out['id'] = f"{(out.get('direction') or 'x').lower()}_{int(float(_zp_for_id))}"
        except Exception:
            out['id'] = f"{(out.get('direction') or 'x').lower()}_unknown"
    # Validate required
    if not out.get('direction') or not out.get('zone_price'):
        return None  # cannot stage without these
    return out


# ─────────────────────────────────────────────────────────────────
# Auto-close conditions DSL (Mode Recorregut Institucional 2026-05-04)
# ─────────────────────────────────────────────────────────────────

# Mètriques referenciables pel LLM al camp `metric` d'una condició kind=metric.
# Aquesta whitelist evita que el LLM inventi noms i el watcher no sàpiga
# resoldre'ls. Si el LLM vol expressar quelcom no llistat → ho posa al text
# qualitatiu invalidation_thesis i marca action=FORCE_REVIEW.
_VALID_METRICS = {
    'futures.cmf_value',          # CMF futures M15 actual (-1..+1)
    'futures.cmf_streak_signed',  # bars streak signats (positiu/negatiu)
    'futures.cvd_4h',             # CVD futures acumulat 4h
    'futures.cvd_last',           # CVD última barra futures
    'futures.vol_z',              # zscore vol futures vs 6h
    'spot.cmf_value',
    'spot.cmf_streak_signed',
    'spot.obv_h1_4h',
    'spot.cvd_4h',
    'spot.vol_z',
    'spread_usd',                 # GC1!−spot diferencial
    'approach.signal_strength',   # -1..+1 segons delta_acc tracker
    'approach.delta_acc',         # contractes signats acumulats
}

_VALID_KIND = {'bar_close', 'metric', 'tick'}
_VALID_TF = {'M5', 'M15', 'H1'}
_VALID_TEST_BAR = {'close_above', 'close_below', 'wick_above', 'wick_below'}
_VALID_TEST_METRIC = {'above', 'below', 'crosses_above', 'crosses_below'}
_VALID_ACTION = {'FULL_CLOSE', 'PARTIAL_50', 'FORCE_REVIEW'}


def _normalize_auto_close_list(raw):
    """Valida i normalitza la llista de auto_close_conditions del LLM.

    Cada condició invàlida es filtra silenciosament amb un log warning. Si tot
    falla, retorna llista buida (el watcher no farà res — DD safety net cobreix).
    """
    if not isinstance(raw, list):
        return []
    out = []
    import logging as _log
    log = _log.getLogger()
    for i, cond in enumerate(raw):
        nc = _normalize_one_auto_close(cond)
        if nc is None:
            try:
                log.warning(f"[AUTO_CLOSE] cond #{i} rebutjada: {cond}")
            except Exception:
                pass
            continue
        out.append(nc)
    return out


def _normalize_one_auto_close(cond):
    """Valida UNA condició. Retorna dict normalitzat o None si invàlida."""
    if not isinstance(cond, dict):
        return None
    kind = cond.get('kind')
    if kind not in _VALID_KIND:
        return None
    action = cond.get('action')
    if action not in _VALID_ACTION:
        return None
    out = {
        'id': str(cond.get('id', f'cond_{kind}'))[:60],
        'kind': kind,
        'action': action,
        'fired_at': None,  # marca quan dispara per no repetir
    }
    if kind == 'bar_close':
        tf = cond.get('tf')
        if tf not in _VALID_TF:
            return None
        test = cond.get('test')
        if test not in _VALID_TEST_BAR:
            return None
        try:
            level = float(cond.get('level'))
        except (TypeError, ValueError):
            return None
        out.update({'tf': tf, 'test': test, 'level': level})
        # vol opcional
        wvm = cond.get('with_vol_ratio_min')
        if wvm is not None:
            try:
                out['with_vol_ratio_min'] = float(wvm)
            except (TypeError, ValueError):
                pass
    elif kind == 'metric':
        metric = cond.get('metric')
        if metric not in _VALID_METRICS:
            return None
        test = cond.get('test')
        if test not in _VALID_TEST_METRIC:
            return None
        try:
            level = float(cond.get('level'))
        except (TypeError, ValueError):
            return None
        out.update({'metric': metric, 'test': test, 'level': level})
        # bars sostingudes opcional
        fbm = cond.get('for_bars_min')
        if fbm is not None:
            try:
                out['for_bars_min'] = int(fbm)
            except (TypeError, ValueError):
                pass
    elif kind == 'tick':
        test = cond.get('test')
        # tick reusa close_above/close_below com semàntica "preu actual"
        if test not in {'above', 'below', 'close_above', 'close_below'}:
            return None
        try:
            level = float(cond.get('level'))
        except (TypeError, ValueError):
            return None
        # normalitzem tick test a above/below
        out.update({
            'test': 'above' if 'above' in test else 'below',
            'level': level,
        })
    return out


def get_valid_metrics():
    """Public accessor — exposa la whitelist de mètriques per a documentació
    i per al watcher al trader_brain."""
    return set(_VALID_METRICS)


def validate_setup_geometry(setup: dict, current_price: float) -> tuple:
    """Valida que el setup té geometria coherent segons setup_type.

    REVERSION (default):
      - SELL: trigger_zone >= current_price (resistance, preu rebutja)
      - BUY:  trigger_zone <= current_price (support, preu rebutja)

    BREAKOUT:
      - SELL: trigger_zone <= current_price (preu trenca cap avall, continuation)
      - BUY:  trigger_zone >= current_price (preu trenca cap amunt, continuation)

    Geometries oposades segons setup_type. El FastEngine usa lògica diferent
    per a cada un (rejection candle vs close beyond).

    Tolerància: 1$ d'error.
    """
    direction = setup.get('direction')
    zp = setup.get('zone_price')
    setup_type = setup.get('setup_type', 'reversion')
    if not direction or zp is None or not current_price:
        return True, ''
    try:
        zp_f = float(zp)
        cp_f = float(current_price)
    except (TypeError, ValueError):
        return True, ''
    TOL = 1.0

    if setup_type == 'breakout':
        # Breakout: zone està al COSTAT OPOSAT a la direcció del trade
        # (preu ha de creuar la zona en favor de la direcció)
        if direction == 'SELL':
            # SELL breakout: zone és un suport per sota; preu ha de tancar per sota
            if zp_f > cp_f + TOL:
                return False, (
                    f"SELL BREAKOUT amb trigger_zone {zp_f:.2f} sobre preu "
                    f"{cp_f:.2f} (∆ +{zp_f-cp_f:.1f}$). Per breakout SELL, "
                    f"la zona ha d'estar PER SOTA del preu (preu trenca cap avall)."
                )
        elif direction == 'BUY':
            # BUY breakout: zone és resistència per sobre; preu ha de tancar per sobre
            if zp_f < cp_f - TOL:
                return False, (
                    f"BUY BREAKOUT amb trigger_zone {zp_f:.2f} sota preu "
                    f"{cp_f:.2f} (∆ -{cp_f-zp_f:.1f}$). Per breakout BUY, "
                    f"la zona ha d'estar PER SOBRE del preu (preu trenca cap amunt)."
                )
    else:
        # Reversion (default)
        if direction == 'SELL':
            if zp_f < cp_f - TOL:
                return False, (
                    f"SELL REVERSION amb trigger_zone {zp_f:.2f} sota preu "
                    f"{cp_f:.2f} (∆ -{cp_f-zp_f:.1f}$). Per reversion SELL, "
                    f"la zona (resistance) ha d'estar PER SOBRE del preu."
                )
        elif direction == 'BUY':
            if zp_f > cp_f + TOL:
                return False, (
                    f"BUY REVERSION amb trigger_zone {zp_f:.2f} sobre preu "
                    f"{cp_f:.2f} (∆ +{zp_f-cp_f:.1f}$). Per reversion BUY, "
                    f"la zona (support) ha d'estar PER SOTA del preu."
                )
    return True, ''


def _blacklist_load():
    try:
        if not os.path.exists(BLACKLIST_FILE):
            return []
        with open(BLACKLIST_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
        items = d.get('blacklist', [])
        now = time.time()
        return [b for b in items if (now - float(b.get('ts', 0))) < BLACKLIST_TTL_S]
    except Exception:
        return []


def get_active_blacklist():
    """Public accessor: retorna els setups recentment invalidats que NO es poden
    re-stagear ara mateix. Per al payload de l'EXECUTOR (perquè sàpiga què
    evitar) i per al dashboard (perquè es vegi).
    """
    try:
        items = _blacklist_load()
        now = time.time()
        out = []
        for b in items:
            ts = float(b.get('ts', 0) or 0)
            age_min = (now - ts) / 60.0
            remaining_min = max(0, (BLACKLIST_TTL_S - (now - ts)) / 60.0)
            out.append({
                "direction": b.get('direction'),
                "zone_price": b.get('zone_price'),
                "reason": b.get('reason'),
                "age_min": round(age_min, 1),
                "remaining_min": round(remaining_min, 1),
            })
        return out
    except Exception:
        return []


def _blacklist_save(items):
    try:
        with open(BLACKLIST_FILE, 'w', encoding='utf-8') as f:
            json.dump({'updated_at': datetime.now(timezone.utc).isoformat(),
                       'blacklist': items}, f, indent=2)
    except Exception:
        pass


def _blacklist_add(direction, zone_price, reason='breach_drop'):
    if direction not in ('BUY', 'SELL'):
        return
    try:
        zp = float(zone_price)
    except Exception:
        return
    items = _blacklist_load()  # already TTL-filtered
    items.append({'direction': direction, 'zone_price': zp,
                   'ts': time.time(), 'reason': reason})
    _blacklist_save(items)


def is_blacklisted(direction, zone_price, radius_usd=None):
    """Return True if a (direction, zone±radius) is blacklisted within TTL."""
    if direction not in ('BUY', 'SELL'):
        return False
    try:
        zp = float(zone_price)
    except Exception:
        return False
    r = float(radius_usd) if radius_usd is not None else BLACKLIST_RADIUS_USD
    for b in _blacklist_load():
        if b.get('direction') != direction:
            continue
        if abs(zp - float(b.get('zone_price', 0) or 0)) <= r:
            return True
    return False


def _rr_score(setup):
    """Risk/reward score for a setup. Falls back to confidence if distances missing."""
    td = setup.get('target_distance_usd')
    sd = setup.get('stop_distance_usd')
    try:
        if td is not None and sd is not None and float(sd) > 0:
            return float(td) / float(sd)
    except Exception:
        pass
    try:
        return float(setup.get('confidence') or 0.5)
    except Exception:
        return 0.5


def enforce_one_per_direction():
    """2026-05-07: ARA ÉS enforce_max_total(5). Permet múltiples setups
    paral·lels per direcció (cobertura escalonada de nivells). El nom
    es manté per back-compat, però la lògica ha canviat.

    L'EXECUTOR pot proposar fins a 5 setups simultanis. Quan un dispara,
    `try_fire_staged_setup` neteja els altres automàticament.

    Returns nº de setups dropped (només si > MAX_SETUPS).
    """
    MAX_SETUPS = 5
    setups = load()
    if len(setups) <= MAX_SETUPS:
        return 0
    # Massa setups: ordena per R/R i conserva els millors
    setups.sort(key=lambda s: _rr_score(s), reverse=True)
    kept = setups[:MAX_SETUPS]
    save(kept)
    return len(setups) - len(kept)


def evaluate_breach_m5(bar_close, bar_ts, grace_bars=4, m5_seconds=300):
    """Track M5-close zone breaches.

    NEW 2026-05-05 (qualitative philosophy):
    - Quan una M5 tanca beyond la zona en direcció contrària a l'esperada,
      es marca com a "newly_breached" perquè el caller pugui invocar
      l'EXECUTOR immediatament (review qualitatiu — el LLM decideix si
      invalida, manté, o modifica auto_close_conditions).
    - El grace de 4 barres (20 min) actua només com a SAFETY NET fallback
      si el LLM no respon. La filosofia és que el LLM decideixi qualitativament,
      no que el codi mati setups per regla pròpia.

    BUY:  close < zone is a breach.
    SELL: close > zone is a breach.

    Wicks (high/low) are ignored — only the M5 close.

    Returns: dict with two keys:
      - 'pruned': count of plans dropped via grace_expired (safety net)
      - 'newly_breached': list of {id, direction, zone, current_close} per
        setups que ara per primera vegada han estat breached — el caller
        ha d'invocar EXECUTOR review per a aquests.
    """
    try:
        bc = float(bar_close); ts = float(bar_ts)
    except Exception:
        return {'pruned': 0, 'newly_breached': []}
    if bc <= 0 or ts <= 0:
        return {'pruned': 0, 'newly_breached': []}
    setups = load()
    alive = []
    pruned = 0
    newly_breached = []
    changed = False
    for s in setups:
        zone = float(s.get('zone_price', 0) or 0)
        d = s.get('direction')
        if zone > 0 and d in ('BUY', 'SELL'):
            breached_now = (d == 'BUY' and bc < zone) or (d == 'SELL' and bc > zone)
            first_ts = s.get('_breach_first_ts')
            if breached_now and first_ts is None:
                # Primera vegada que es detecta la breach — marquem timestamp
                # i enregistrem per al caller perquè invoqui EXECUTOR review.
                s['_breach_first_ts'] = ts
                changed = True
                first_ts = ts
                newly_breached.append({
                    'id': s.get('id', '?'),
                    'direction': d,
                    'zone': zone,
                    'current_close': bc,
                })
            if first_ts is not None:
                bars_elapsed = int((ts - float(first_ts)) // m5_seconds)
                if bars_elapsed >= grace_bars:
                    # Safety net fallback: el LLM no ha actuat dins del grace,
                    # blacklist + drop. Cas excepcional, no la ruta normal.
                    _blacklist_add(d, zone, reason='breach_grace_expired')
                    pruned += 1
                    continue
        alive.append(s)
    if pruned > 0 or changed:
        save(alive)
    return {'pruned': pruned, 'newly_breached': newly_breached}


# Back-compat alias: old call sites can still invoke prune_zone_crossed but
# without bar_ts the M5 grace logic is skipped (immediate drop on close).
def prune_zone_crossed(bar_close):
    try:
        bc = float(bar_close)
    except Exception:
        return 0
    if bc <= 0:
        return 0
    setups = load()
    alive = [s for s in setups if not (
        float(s.get('zone_price', 0) or 0) > 0 and (
            (s.get('direction') == 'BUY' and bc < float(s.get('zone_price', 0)))
            or (s.get('direction') == 'SELL' and bc > float(s.get('zone_price', 0)))
        )
    )]
    pruned = len(setups) - len(alive)
    if pruned:
        save(alive)
    return pruned


def replace_all(new_setups):
    """Replace all staged setups with a fresh list. Called after Executor stages."""
    # Normalize and filter
    normalized = []
    for s in (new_setups or []):
        n = _normalize_setup(s)
        if n:
            normalized.append(n)
    new_setups = normalized
    # Stamp each with staged_at if not present
    now = time.time()
    for s in new_setups:
        if 'staged_at' not in s:
            s['staged_at'] = now
    save(new_setups)
    enforce_one_per_direction()


def replace_for_source(new_setups, source='executor', current_price=None):
    """Wipe setups belonging to `source` ONLY for the directions present in
    the new proposals, then add the new ones.

    Rationale: the Executor may re-plan a single direction (e.g. only BUY)
    while leaving its existing SELL plan untouched. Wiping every setup of
    this source on every call would erase the other direction's plan even
    when the LLM didn't intend to change it. So we replace per (source,
    direction) instead of per source.

    Setups from other sources (e.g. 'hunter') are preserved regardless.
    """
    src = (source or 'executor').lower()
    normalized = []
    re_proposed_blacklist = 0
    rejected_geometry = 0
    re_proposed_details = []
    rejected_geo_details = []
    for s in (new_setups or []):
        n = _normalize_setup(s)
        if not n:
            continue
        # 2026-05-07: BLACKLIST DESACTIVAT (decisió usuari).
        # Bloquejava massa rebots vàlids (segon toc del nivell amb rejection
        # clar es perdia). Ara no filtrem per blacklist; l'EXECUTOR decideix
        # qualitativament si re-proposar té sentit articulant motiu.
        # Es manté la lògica de _blacklist_add només per visibilitat al payload
        # (informació, no filtre).
        if False:  # blacklist hard filter desactivat
            re_proposed_blacklist += 1
            re_proposed_details.append(f"{n.get('direction')}@{n.get('zone_price')}")
            continue
        # 2026-05-04: validació de geometria per evitar SELL break-down i BUY break-up
        # que el FastEngine no sap executar (només fa REVERSION).
        if current_price:
            ok, reason = validate_setup_geometry(n, current_price)
            if not ok:
                rejected_geometry += 1
                rejected_geo_details.append(reason)
                continue
        n['source'] = src
        normalized.append(n)
    # 2026-05-06: log informatiu — re-staging blocked per blacklist (HARD FILTER)
    if re_proposed_blacklist > 0:
        try:
            import logging as _log
            _log.getLogger().warning(
                f"[STAGED] 🚫 BLOCKED {re_proposed_blacklist} setup(s) per blacklist "
                f"(zones invalidades fa <30min): {', '.join(re_proposed_details)} "
                f"(source={src})"
            )
        except Exception:
            pass
    if rejected_geometry > 0:
        try:
            import logging as _log
            for reason in rejected_geo_details:
                _log.getLogger().warning(f"[STAGED] rejected by geometry: {reason}")
            _log.getLogger().warning(
                f"[STAGED] {rejected_geometry} setup(s) rebutjats per geometria — "
                f"l'EXECUTOR proposa break-up/break-down que el FastEngine no executa. "
                f"Demana NOMÉS reversion setups (rebot a S/R, no continuació)."
            )
        except Exception:
            pass
    now = time.time()
    for s in normalized:
        s.setdefault('staged_at', now)
    # 2026-05-04: si new_setups és buit (LLM diu WAIT), wipe TOTS els setups
    # d'aquesta source. Comportament: cada cycle reavalua, els antics no
    # sobreviuen sense confirmació explícita.
    current = load()
    if not normalized:
        # WIPE: esborra tots els setups d'aquesta source
        kept = [s for s in current if (s.get('source') or 'executor').lower() != src]
        save(kept)
        return 0
    # Quan SÍ hi ha new_setups, comportament tradicional: substitueix per direcció
    dirs_in_new = {s.get('direction') for s in normalized if s.get('direction') in ('BUY', 'SELL')}
    kept = [
        s for s in current
        if not (
            (s.get('source') or 'executor').lower() == src
            and s.get('direction') in dirs_in_new
        )
    ]
    save(kept + normalized)
    enforce_one_per_direction()
    return len(normalized)


def add_setups_merge(new_setups, atr_m15=None):
    """Add new setups to the current list, merging with dedup.

    Rules:
      · Identical (id match) → newer overwrites older
      · Overlap (same direction, prices within 0.3 × atr_m15) → Hunter wins over Executor;
        within same source, newer wins
      · Otherwise, append
    """
    DEDUP_ATR_MULT = 0.3
    dedup_dist = (atr_m15 or 0) * DEDUP_ATR_MULT
    if dedup_dist <= 0:
        dedup_dist = 5.0  # sensible default if ATR unavailable

    normalized = []
    for s in (new_setups or []):
        n = _normalize_setup(s)
        if n:
            normalized.append(n)
    if not normalized:
        return 0

    current = load()
    now = time.time()
    for s in normalized:
        if 'staged_at' not in s:
            s['staged_at'] = now
        # Look for overlap
        new_src = (s.get('source') or 'executor').lower()
        new_dir = s.get('direction')
        new_price = float(s.get('zone_price', 0) or 0)
        replaced = False
        for i, old in enumerate(current):
            if old.get('id') == s.get('id'):
                current[i] = s
                replaced = True
                break
            old_dir = old.get('direction')
            old_price = float(old.get('zone_price', 0) or 0)
            if old_dir == new_dir and abs(old_price - new_price) <= dedup_dist:
                old_src = (old.get('source') or 'executor').lower()
                # Hunter beats Executor; within same source keep the better R/R
                if new_src == 'hunter' and old_src == 'executor':
                    current[i] = s
                    replaced = True
                    break
                if new_src == old_src:
                    if _rr_score(s) > _rr_score(old):
                        current[i] = s
                    replaced = True
                    break
                # new is executor, old is hunter → keep hunter, skip new
                replaced = True
                break
        if not replaced:
            current.append(s)
    save(current)
    enforce_one_per_direction()
    return len(normalized)


def unfreeze_post_close():
    """Flip post_close=true → false on all setups. Called when a signal closes so
    the alt_hypothesis setups become eligible for FastEngine to fire."""
    setups = load()
    changed = 0
    for s in setups:
        if s.get('post_close'):
            s['post_close'] = False
            changed += 1
    if changed:
        save(setups)
    return changed


def count_active_by_source(source):
    """Count current setups matching a given source ('hunter' | 'executor')."""
    setups = load()
    src = (source or '').lower()
    return sum(1 for s in setups if (s.get('source') or 'executor').lower() == src)


def clear():
    """Clear all staged setups (e.g. when trade opens from TG)."""
    save([])


def mark_fired(setup_id):
    """Mark a setup as fired (consumed). Effectively removes it."""
    setups = load()
    setups = [s for s in setups if s.get('id') != setup_id]
    save(setups)


def remove_near_zone(direction, zone_price, tolerance_pts=15.0):
    """Remove setups matching direction + zone near the just-closed trade's entry.

    Prevents automatic re-entry at the same level without fresh Executor reasoning.
    Alt-hypothesis setups at different zones/directions are preserved.
    Returns count of removed setups.
    """
    setups = load()
    before = len(setups)
    alive = [
        s for s in setups
        if not (
            s.get('direction') == direction and
            abs(float(s.get('zone_price', 0) or 0) - float(zone_price)) <= tolerance_pts
        )
    ]
    if len(alive) < before:
        save(alive)
    return before - len(alive)


def prune_expired_invalidated(current_price):
    """Remove setups that have expired or whose invalidation_price was crossed.
    Returns the number of pruned setups.
    """
    setups = load()
    now = time.time()
    alive = []
    pruned = 0
    for s in setups:
        # Expiration
        staged_at = float(s.get('staged_at', now))
        exp_min = float(s.get('expiration_minutes', 30))
        if (now - staged_at) > exp_min * 60:
            _blacklist_add(s.get('direction'), s.get('zone_price'), reason='ttl_expired')
            pruned += 1
            continue
        # Invalidation price
        inv_price = float(s.get('invalidation_price', 0) or 0)
        if inv_price > 0 and current_price > 0:
            direction = s.get('direction', '')
            # For BUY setup: invalidation usually BELOW zone (price breaks support)
            # For SELL setup: invalidation usually ABOVE zone (price breaks resistance)
            zone = float(s.get('zone_price', 0) or 0)
            if direction == 'BUY' and inv_price < zone and current_price <= inv_price:
                _blacklist_add(direction, zone, reason='invalidation_price')
                pruned += 1
                continue
            if direction == 'SELL' and inv_price > zone and current_price >= inv_price:
                _blacklist_add(direction, zone, reason='invalidation_price')
                pruned += 1
                continue
        alive.append(s)
    if pruned > 0:
        save(alive)
    return pruned


def check_confirmations(setup, bars, atr_value):
    """Check if the setup's confirmations are currently met.
    Returns (count_met, total_required_min, details) tuple.
    """
    if not bars or len(bars) < 21:
        return 0, int(setup.get('confirmations_min', 1)), {}

    last_closed = bars[-2] if len(bars) >= 2 else bars[-1]

    # Volume ratio
    vols = [float(b.get('volume', 0) or 0) for b in bars[-21:-1]]
    avg_v = (sum(vols) / len(vols)) if vols else 0
    last_v = float(last_closed.get('volume', 0) or 0)
    vol_r = (last_v / avg_v) if avg_v > 0 else 0

    # Candle type
    o = float(last_closed.get('open', 0) or 0)
    h = float(last_closed.get('high', 0) or 0)
    l = float(last_closed.get('low', 0) or 0)
    c = float(last_closed.get('close', 0) or 0)
    body = abs(c - o)
    rng = h - l
    uwick = h - max(c, o)
    lwick = min(c, o) - l
    is_pin_bar_bull = (lwick > body * 2 and uwick < body * 0.5 and c > o)
    is_pin_bar_bear = (uwick > body * 2 and lwick < body * 0.5 and c < o)
    is_strong_bull = (rng > 0 and body / rng > 0.7 and c > o)
    is_strong_bear = (rng > 0 and body / rng > 0.7 and c < o)
    is_hammer = is_pin_bar_bull
    is_engulfing_bull = (len(bars) >= 3 and c > float(bars[-3].get('open', 0) or 0)
                        and o < float(bars[-3].get('close', 0) or 0))
    is_engulfing_bear = (len(bars) >= 3 and c < float(bars[-3].get('open', 0) or 0)
                        and o > float(bars[-3].get('close', 0) or 0))

    # RSI
    closes = [float(b.get('close', 0) or 0) for b in bars[-15:]]
    rsi_val = 50
    if len(closes) >= 15:
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i-1]
            gains.append(max(0, d))
            losses.append(max(0, -d))
        avg_g = sum(gains[-14:]) / 14
        avg_l = sum(losses[-14:]) / 14
        if avg_l > 0:
            rs = avg_g / avg_l
            rsi_val = 100 - (100 / (1 + rs))
        elif avg_g > 0:
            rsi_val = 100

    direction = setup.get('direction', '')
    needed = setup.get('confirmations_needed', []) or []
    met_list = []
    details = {'vol_ratio': round(vol_r, 2), 'rsi': round(rsi_val, 1), 'candle': ''}

    for conf in needed:
        c_lower = (conf or '').lower()
        if c_lower == 'rejection_candle':
            if direction == 'BUY' and (is_pin_bar_bull or is_hammer):
                met_list.append(conf)
                details['candle'] = 'pin_bar_bull'
            elif direction == 'SELL' and is_pin_bar_bear:
                met_list.append(conf)
                details['candle'] = 'pin_bar_bear'
        elif c_lower == 'pin_bar':
            if (direction == 'BUY' and is_pin_bar_bull) or (direction == 'SELL' and is_pin_bar_bear):
                met_list.append(conf)
                details['candle'] = 'pin_bar'
        elif c_lower == 'engulfing':
            if (direction == 'BUY' and is_engulfing_bull) or (direction == 'SELL' and is_engulfing_bear):
                met_list.append(conf)
                details['candle'] = 'engulfing'
        elif c_lower == 'strong_bull_bar':
            if is_strong_bull and direction == 'BUY':
                met_list.append(conf)
                details['candle'] = 'strong_bull'
        elif c_lower == 'strong_bear_bar':
            if is_strong_bear and direction == 'SELL':
                met_list.append(conf)
                details['candle'] = 'strong_bear'
        elif c_lower == 'vol_ratio_1.2':
            if vol_r >= 1.2:
                met_list.append(conf)
        elif c_lower == 'vol_ratio_1.5':
            if vol_r >= 1.5:
                met_list.append(conf)
        elif c_lower == 'rsi_extreme':
            if direction == 'BUY' and rsi_val < 30:
                met_list.append(conf)
            elif direction == 'SELL' and rsi_val > 70:
                met_list.append(conf)

    min_req = int(setup.get('confirmations_min', 1))
    return len(met_list), min_req, {'met': met_list, **details}


def find_triggered(setups, current_price, atr_value, bars):
    """Return the first setup that is triggered (price in tolerance + confirmations met).
    Returns the setup dict, or None.

    Skips any setup with `post_close=True` — those are alt_hypothesis setups
    waiting for the current trade to close before becoming eligible. FastEngine
    already gates by IDLE, but this is a belt-and-suspenders check.
    """
    for s in setups:
        if s.get('post_close'):
            continue
        zone = float(s.get('zone_price', 0) or 0)
        if zone <= 0:
            continue
        tol_atr = float(s.get('tolerance_atr', 0.5))
        tol = atr_value * tol_atr
        if abs(current_price - zone) > tol:
            continue
        met, min_req, details = check_confirmations(s, bars, atr_value)
        if met >= min_req:
            s['_trigger_details'] = details
            return s
    return None
