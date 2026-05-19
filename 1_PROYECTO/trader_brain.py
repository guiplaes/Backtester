#!/usr/bin/env python3
"""
TRADER BRAIN v2 — Claude-powered XAUUSD trade manager.

Three-layer architecture:
  FAST (every 3s): reads TV price+volume, checks reversal zones, executes instantly
  INDICATOR (every 75s): Claude identifies reversal zones (always running)
  EXECUTOR (every 35s): Claude manages active trade (only when signal active)

Signals come from Telegram. Claude manages everything after entry.

Usage:
  python trader_brain.py --debug     (console output)
  pythonw trader_brain.py            (background)
"""

import subprocess, json, os, sys, time, logging, threading, hashlib
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, Future
import trade_history
import news_state
import brain_journal

# Async AI executor — AI calls run in threads so main loop never blocks.
_ai_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='AI')

# ═══════════════════════════════════════════════════════════════
# PATHS
# ═══════════════════════════════════════════════════════════════

TV_SCRIPT    = r"C:\Users\Administrator\tradingview-mcp-jackson\tv.js"
TV_ENV       = {**os.environ, "TV_CDP_PORT": "9223"}
NODE         = "node"
# Expected TV chart symbol for the main bars feed. Used as a guard: every
# ohlcv read passes this and verifies the response — prevents ever acting
# on bars from a wrong chart (e.g. DXY after a failed ohlcv-sym restore).
EXPECTED_SYMBOL = "OANDA:XAUUSD"

COMMON       = r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files"

# ── NEW Brain EA v1 protocol (ClaudeBrainEA_v1_MT5) — isolated from old app ──
ORDERS_FILE  = os.path.join(COMMON, 'brain_orders.json')         # brain → EA
POSITIONS    = os.path.join(COMMON, 'brain_positions.json')       # EA → brain
HEARTBEAT    = os.path.join(COMMON, 'brain_ea_heartbeat.json')    # EA → brain

# ── Brain internal state files ──
ZONES_FILE   = os.path.join(COMMON, 'brain_zones.json')
STATUS_FILE  = os.path.join(COMMON, 'brain_status.json')
STAGED_FILE  = os.path.join(COMMON, 'brain_staged_setups.json')
DAILY_FILE   = os.path.join(COMMON, 'brain_daily.json')
DAILY_HISTORY_FILE = os.path.join(COMMON, 'brain_daily_history.json')  # Multi-day H/L cache

# Daily performance goal
DAILY_GOAL_PCT = 1.0  # +1% daily target

# ── Brain config ──
BRAIN_MAGIC = 99999              # EXCLUSIVE brain magic (must match EA)
BRAIN_DD_LIMIT_PCT = 3.5         # auto-close threshold (must match EA)

CLI_PATH     = r"C:\nodejs\node-v22.14.0-win-x64\node_modules\@anthropic-ai\claude-code\cli.js"
SCREENSHOT_DIR = r"C:\Users\Administrator\tradingview-mcp-jackson\screenshots"

# ── LLM backend selection ──
# "deepseek" = DeepSeek API (cheap, ~10x cost reduction vs Sonnet). Requires DEEPSEEK_API_KEY.
# "claude"   = Claude CLI (Sonnet/Haiku, natively installed)
LLM_BACKEND = os.environ.get('LLM_BACKEND', 'deepseek')
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

def _load_env_file(path):
    """Load KEY=VALUE pairs from a .env file into os.environ (doesn't overwrite existing)."""
    try:
        if not os.path.exists(path):
            return
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and v and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass

# Load .env from project root
_load_env_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY', '')

LOG_DIR      = r"C:\Users\Administrator\Desktop\MT4 Claude\1_PROYECTO\logs"
PID_FILE     = os.path.join(LOG_DIR, 'trader_brain.pid')
os.makedirs(LOG_DIR, exist_ok=True)

DEBUG = '--debug' in sys.argv

# ── SAFETY FLAGS ──
# PAPER_MODE: log decisions but DO NOT execute orders (safe testing)
# Set to False ONLY when you want the brain to actually open/close trades
PAPER_MODE = False  # live mode — orders actually sent to EA

# Windows: prevent subprocess from opening console windows
_NOWIN = {}
if sys.platform == 'win32':
    _si = subprocess.STARTUPINFO()
    _si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _si.wShowWindow = 0  # SW_HIDE
    _NOWIN = {'startupinfo': _si, 'creationflags': subprocess.CREATE_NO_WINDOW}


def check_single_instance():
    """Ensure only one instance runs. Kill old if stale.

    2026-05-04: hardened against zombie-PID false positives.
    OpenProcess returns a handle even for terminated processes whose handle
    structure persists. We additionally verify via psutil that the process
    is alive AND running trader_brain.py specifically.
    """
    my_pid = os.getpid()
    if os.path.exists(PID_FILE):
        try:
            old_pid = int(open(PID_FILE).read().strip())
            if old_pid != my_pid:
                # Verify via psutil the old PID is REALLY a running brain
                old_is_real_brain = False
                try:
                    import psutil
                    p = psutil.Process(old_pid)
                    if p.is_running():
                        cmd = p.cmdline() or []
                        if cmd and "trader_brain.py" in " ".join(cmd).lower():
                            old_is_real_brain = True
                except Exception:
                    pass  # process gone, AccessDenied, etc → treat as stale
                if old_is_real_brain:
                    print(f"ERROR: Another instance already running (PID {old_pid}). Exiting.")
                    sys.exit(1)
                # Stale PID file — overwrite below
        except (ValueError, OSError):
            pass
    with open(PID_FILE, 'w') as f:
        f.write(str(my_pid))


def cleanup_pid():
    try: os.unlink(PID_FILE)
    except: pass

# Force UTF-8 on Windows console
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ═══════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════

log = logging.getLogger('brain')
log.setLevel(logging.DEBUG)
log.propagate = False
if not log.handlers:
    fh = logging.FileHandler(os.path.join(LOG_DIR, 'trader_brain.log'), encoding='utf-8')
    fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    log.addHandler(fh)
    if DEBUG:
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        log.addHandler(sh)

try:
    _exec_prompt_hash = hashlib.sha1(EXECUTOR_PROMPT.encode('utf-8')).hexdigest()[:12]
    log.info(f"[PROMPT] executor.txt loaded: {len(EXECUTOR_PROMPT)} chars sha1={_exec_prompt_hash}")
except Exception as _prompt_log_err:
    log.warning(f"[PROMPT] executor.txt hash log failed: {_prompt_log_err}")

# ═══════════════════════════════════════════════════════════════
# TV READER
# ═══════════════════════════════════════════════════════════════

_tv_lock = threading.Lock()  # serialize TV calls — prevents concurrent reads
                              # during ohlcv-sym swap from returning wrong symbol's bars

def tv(cmd, arg=None, arg2=None, arg3=None, timeout=8):
    """Call tv.js, return parsed JSON or None.

    Serialized via _tv_lock so concurrent threads (bars refresher + indicator
    + executor) can't race with ohlcv-sym swap (which transiently changes
    chart symbol to DXY/10Y). Without this lock, a concurrent `ohlcv` call
    during a DXY swap returns DXY bars as if they were XAU — silent corruption.

    Args:
        cmd: tv.js command (e.g. "ohlcv", "ohlcv-sym", "screenshot")
        arg, arg2, arg3: positional args passed to tv.js (e.g. SYMBOL, TF, COUNT)
        timeout: per-call timeout seconds
    """
    c = [NODE, TV_SCRIPT, cmd]
    if arg is not None: c.append(str(arg))
    if arg2 is not None: c.append(str(arg2))
    if arg3 is not None: c.append(str(arg3))
    with _tv_lock:
        try:
            r = subprocess.run(c, capture_output=True, text=True, timeout=timeout,
                               cwd=os.path.dirname(TV_SCRIPT), env=TV_ENV, **_NOWIN)
            if r.returncode == 0 and r.stdout.strip():
                return json.loads(r.stdout.strip())
        except subprocess.TimeoutExpired:
            log.warning(f"tv({cmd}) timeout after {timeout}s")
        except Exception as e:
            log.warning(f"tv({cmd}) error: {e}")
    return None

def tv_screenshot():
    """Take chart screenshot, return path."""
    r = tv("screenshot", "chart")
    return r.get('file_path') if r and r.get('success') else None

def tv_pine_update(source_code):
    """Inject Pine Script source, compile, and save layout for mobile sync.

    Retries pine-set up to 3 times (CDP can flake under load).
    Uses 25s timeout — pine-set with long script can take 10-15s.
    """
    r = None
    for attempt in range(3):
        r = tv("pine-set", source_code, timeout=25)
        if r and r.get('success'):
            break
        log.warning(f"pine-set attempt {attempt+1}/3 failed: {r}")
        time.sleep(1.5)
    if not r or not r.get('success'):
        log.warning(f"pine-set gave up after 3 attempts")
        return False
    r2 = None
    for attempt in range(3):
        r2 = tv("pine-compile", timeout=25)
        if r2 and r2.get('success'):
            break
        log.warning(f"pine-compile attempt {attempt+1}/3 failed: {r2}")
        time.sleep(1.5)
    if not r2 or not r2.get('success'):
        log.warning(f"pine-compile gave up after 3 attempts")
        return False
    log.info("Pine Script compiled OK")
    # Save chart layout so mobile picks up the changes
    tv_save_layout()
    return True

def tv_save_layout():
    """Save TradingView chart layout to cloud (syncs to mobile)."""
    try:
        c = [NODE, TV_SCRIPT, "save-layout"]
        subprocess.run(c, capture_output=True, text=True, timeout=5,
                       cwd=os.path.dirname(TV_SCRIPT), env=TV_ENV, **_NOWIN)
    except Exception:
        pass


def broker_position_pnl(pos):
    """Broker-authoritative ticket PnL, including swap and accrued fees when available."""
    try:
        gross = float(pos.get('profit_live_gross', pos.get('profit_gross', pos.get('profit', 0))) or 0)
        swap = float(pos.get('swap_live', pos.get('swap', 0)) or 0)
        commission = float(pos.get('commission_live', pos.get('commission', 0)) or 0)
        fee = float(pos.get('fee_live', pos.get('fee', 0)) or 0)
        return float(pos.get('profit_live_net', pos.get('profit_net', gross + swap + commission + fee)) or 0)
    except Exception:
        return 0.0

# ═══════════════════════════════════════════════════════════════
# LOCAL INDICATORS (instant, no Claude needed)
# ═══════════════════════════════════════════════════════════════

def rsi(closes, n=14):
    if len(closes) < n+1: return None
    deltas = [closes[i]-closes[i-1] for i in range(1, len(closes))]
    gains = [max(d,0) for d in deltas[-n:]]
    losses = [max(-d,0) for d in deltas[-n:]]
    ag = sum(gains)/n; al = sum(losses)/n
    return round(100 - 100/(1 + ag/al), 1) if al else 100.0

def ema(vals, n):
    if len(vals) < n: return None
    k = 2/(n+1); e = sum(vals[:n])/n
    for v in vals[n:]: e = v*k + e*(1-k)
    return round(e, 2)

def atr(bars, n=14):
    if len(bars) < n+1: return None
    trs = []
    for i in range(1, len(bars)):
        h,l,pc = bars[i]['high'], bars[i]['low'], bars[i-1]['close']
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return round(sum(trs[-n:])/n, 2)

def vol_ratio(bars, n=20):
    if len(bars) < n+1: return 1.0
    avg = sum(b['volume'] for b in bars[-n-1:-1]) / n
    return round(bars[-1]['volume'] / max(avg,1), 1)

def candle_type(b):
    body = abs(b['close']-b['open'])
    rng = b['high']-b['low']
    if rng == 0: return "DOJI"
    lwick = min(b['close'],b['open'])-b['low']
    uwick = b['high']-max(b['close'],b['open'])
    if lwick > body*2 and uwick < body*0.5:
        return "HAMMER" if b['close']>b['open'] else "INV_HAMMER"
    if uwick > body*2 and lwick < body*0.5:
        return "SHOOT_STAR"
    if body/rng > 0.7:
        return "STRONG_BULL" if b['close']>b['open'] else "STRONG_BEAR"
    return "BULL" if b['close']>b['open'] else "BEAR"

# ═══════════════════════════════════════════════════════════════
# MT5 STATE READER
# ═══════════════════════════════════════════════════════════════

def read_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except: return {}

def write_status(data):
    """Write brain status for dashboard to read."""
    try:
        data['ts'] = time.time()
        data['updated'] = datetime.now(timezone.utc).isoformat()
        with open(STATUS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def save_zones(zones_data):
    """DEPRECATED — kept for call sites that haven't migrated yet.

    The zone state is now persisted by run_indicator_pipeline() via
    zone_store.write_state() after the Reviewer merges the Indicator proposal
    into the live map. Direct writes here would bypass the cycle of life
    (touches, rejections, STALE/INVALIDATED status) maintained by zone_lifecycle.
    """
    try:
        zones_data['updated'] = datetime.now(timezone.utc).isoformat()
        with open(ZONES_FILE, 'w', encoding='utf-8') as f:
            json.dump(zones_data, f, indent=2)
    except Exception as e:
        log.warning(f"save_zones failed: {e}")

def load_zones():
    """Load zones from the new brain_zone_state.json via zone_store, in the
    legacy {reversal_zones, bias, context} shape for backwards compatibility.

    Falls back to brain_zones.json only if brain_zone_state.json doesn't yet
    exist (i.e. before the first INDICATOR run post-migration)."""
    try:
        from zone_store import read_state, legacy_compat_view
        state = read_state(COMMON)
        if state.get('zones'):
            return legacy_compat_view(state)
    except Exception as e:
        log.warning(f"load_zones via zone_store failed: {e}")
    data = read_json(ZONES_FILE)
    if not data:
        return {'reversal_zones': [], 'bias': 'NEUTRAL', 'context': ''}
    return data

def get_account_state():
    """Read brain_positions.json (from Brain EA v1) + signal_state for current signal info."""
    pos = read_json(POSITIONS)
    # Signal state from brain (the brain itself tracks signal, not EA)
    try:
        from signal_state import get_state, summarize_broker_positions
        sig = get_state()
    except Exception:
        sig = None
        summarize_broker_positions = None

    account_data = pos.get('account', {})
    balance = account_data.get('balance', 0)
    equity = account_data.get('equity', 0)
    positions = pos.get('positions', [])

    # DD limit is brain-controlled (3.5% configurable)
    # Signal state comes from brain's own tracker (not TG heartbeat)
    live_summary = summarize_broker_positions(positions) if summarize_broker_positions else None
    live_direction = (live_summary or {}).get('direction') or ''
    live_entry = (live_summary or {}).get('entry_price') or 0

    if sig and sig.is_active():
        direction = live_direction or sig.get('direction', '')
        entry_price = live_entry or sig.get('entry_price', 0)
        closing = sig.is_closing()
        sig_start_bal = float(sig.get('signal_start_balance', 0) or 0)
        trade_id = sig.get_trade_id() if hasattr(sig, 'get_trade_id') else None
    else:
        direction = live_direction
        entry_price = live_entry
        closing = False
        sig_start_bal = 0.0
        trade_id = None

    # ── TRUE DD (2026-04-24) ──
    # Legacy dd = balance − equity (floating-only). Problem: crystallizing
    # a losing partial resets dd to 0 because balance drops too — letting
    # the Executor "cheat at solitaire" by realizing losses to keep DD low.
    #
    # New dd_signal = signal_start_balance − equity. Includes realized
    # losses within this trade (every closed losing ticket keeps pushing
    # dd_signal up). Applied only when a signal is active; otherwise fall
    # back to floating-only against current balance.
    floating_dd = max(0, balance - equity)
    if sig_start_bal > 0:
        signal_dd = max(0, sig_start_bal - equity)
        dd_used = signal_dd  # authoritative for active trade
        dd_limit = sig_start_bal * (BRAIN_DD_LIMIT_PCT / 100.0)
    else:
        dd_used = floating_dd
        dd_limit = balance * (BRAIN_DD_LIMIT_PCT / 100.0)
    dd_remaining = dd_limit - dd_used
    dd_base = sig_start_bal if sig_start_bal > 0 else balance

    return {
        'balance': balance, 'equity': equity,
        'dd_limit': round(dd_limit, 2),
        'dd_used': round(dd_used, 2),
        'dd_remaining': round(dd_remaining, 2),
        'dd_pct': round(dd_used/dd_base*100, 2) if dd_base else 0,
        'dd_floating': round(floating_dd, 2),
        'signal_start_balance': round(sig_start_bal, 2),
        'positions': positions,
        'pos_count': len(positions),
        'has_signal': bool(direction and entry_price > 0),
        'direction': direction,
        'entry_price': entry_price,
        'trade_id': trade_id,
        'closing': closing,
    }

# ═══════════════════════════════════════════════════════════════
# ORDER WRITER
# ═══════════════════════════════════════════════════════════════

_order_lock = threading.Lock()

def has_pending_order():
    """True if brain_orders.json still has an unprocessed order from the EA's
    perspective. Callers should skip any new order attempts while this is True —
    waiting for the previous order to be processed (EA writes "PROCESSED" flag).
    Cheap file read; safe to call every tick.
    """
    try:
        if not os.path.exists(ORDERS_FILE):
            return False
        with open(ORDERS_FILE, 'r') as f:
            content = f.read().strip()
        if not content:
            return False
        # Same logic as write_order's guard: an order is pending if there's an
        # "action" field AND no "PROCESSED" marker yet.
        return '"action"' in content and '"PROCESSED"' not in content
    except Exception:
        return False


# Stale-order protection: if EA dies/restarts with a pending order in the file,
# or the targeted ticket closes before EA reads it, the file sits as a poison
# pill forever — blocking every subsequent MODIFY_TP/SL/CLOSE. After this many
# seconds we consider it abandoned and overwrite. 45s is long enough for the
# slowest EA read cycle (typical < 1s) and short enough that a live trade
# never stays unmanaged for more than one minute.
_STALE_ORDER_TIMEOUT = 45
_skip_window = {'start': 0.0, 'count': 0, 'alerted': False}

def write_order(order_dict, urgent=False):
    """Write order to Common Files. Returns True if written, False on skip/error.

    urgent=True bypasses the "previous order not processed" guard. Used for
    exits (CLOSE_ALL_BRAIN, CLOSE_TICKET) where latency is critical and the
    pending order (typically a MODIFY_TP/SL) is non-urgent. Without this,
    a MODIFY_TP written 0.5s before an opportunistic close would block the
    close and strand the trade (incident 2026-04-24 13:05).
    """
    with _order_lock:
        try:
            # Urgent orders (closes) always write, overwriting any pending
            # non-urgent order. The EA processes one order per tick anyway;
            # the previous MODIFY will be reissued next cycle if still relevant.
            if urgent and os.path.exists(ORDERS_FILE):
                existing = open(ORDERS_FILE, 'r').read().strip()
                pending = (existing and '"PROCESSED"' not in existing
                           and '"action"' in existing)
                if pending:
                    log.warning(f"Urgent order overrides pending non-urgent order: {order_dict.get('orders', [{}])[0].get('action')}")
                    # fall through to write immediately
                    with open(ORDERS_FILE, 'w', encoding='utf-8') as f:
                        json.dump(order_dict, f, separators=(',', ':'))
                    log.info(f"ORDER WRITTEN (urgent): {json.dumps(order_dict, separators=(',', ':'))}")
                    return True
            # Don't overwrite unprocessed orders — UNLESS they've gone stale.
            if os.path.exists(ORDERS_FILE):
                existing = open(ORDERS_FILE, 'r').read().strip()
                pending = (existing and '"PROCESSED"' not in existing
                           and '"action"' in existing)
                if pending:
                    # Parse ts from the pending order; if older than timeout,
                    # overwrite with a warning. This is the root-cause fix for
                    # the "orders blocked for hours" bug: EA restart / ticket
                    # closed between write-and-read no longer poisons the file.
                    age = None
                    try:
                        prev = json.loads(existing)
                        prev_ts = float(prev.get('ts', 0) or 0)
                        if prev_ts > 0:
                            age = time.time() - prev_ts
                    except Exception:
                        age = None
                    if age is None or age > _STALE_ORDER_TIMEOUT:
                        log.warning(f"Stale pending order (age={age}s > {_STALE_ORDER_TIMEOUT}s), overwriting")
                        # fall through to write
                    else:
                        # Still fresh — skip, and track for TG alert.
                        now = time.time()
                        if now - _skip_window['start'] > 300:
                            _skip_window['start'] = now
                            _skip_window['count'] = 0
                            _skip_window['alerted'] = False
                        _skip_window['count'] += 1
                        log.warning(f"Previous order not processed, skipping (age={age:.1f}s, window_skips={_skip_window['count']})")
                        if _skip_window['count'] >= 10 and not _skip_window['alerted']:
                            _skip_window['alerted'] = True
                            try:
                                notify("system_alert",
                                       f"⚠️ BRIDGE BLOCKED · {_skip_window['count']} ordres refusades en 5min · "
                                       f"auto-recuperació a {_STALE_ORDER_TIMEOUT}s")
                            except Exception:
                                pass
                        return False

            # Compact JSON (no spaces) — EA's ExtractJSONString requires "key":"value" without space after colon.
            with open(ORDERS_FILE, 'w', encoding='utf-8') as f:
                json.dump(order_dict, f, separators=(',', ':'))
            log.info(f"ORDER WRITTEN: {json.dumps(order_dict, separators=(',', ':'))}")
            return True
        except Exception as e:
            log.error(f"Write order failed: {e}")
            return False

def send_market(direction, lot, comment="BRAIN", sl=0, tp=0):
    """Send a MARKET order to Brain EA v1."""
    return write_order({
        "ts": int(time.time()),
        "orders": [{
            "action": "MARKET",
            "type": direction,
            "lot": lot,
            "sl": sl,
            "tp": tp,
            "comment": comment
        }]
    })

def close_ticket(ticket):
    """Close a specific brain position. Urgent — overrides any pending order."""
    return write_order({"ts": int(time.time()), "orders": [{"action": "CLOSE_TICKET", "ticket": ticket}]}, urgent=True)

def close_all_brain():
    """Close ALL brain positions (respects magic filter in EA). Urgent."""
    return write_order({"ts": int(time.time()), "orders": [{"action": "CLOSE_ALL_BRAIN"}]}, urgent=True)

def modify_tp(ticket, tp):
    """Set TP on a specific ticket."""
    return write_order({"ts": int(time.time()), "orders": [{"action": "MODIFY_TP", "ticket": ticket, "tp": tp}]})


def apply_trade_plan(positions, direction, bars_cache, reason=""):
    """Compute a TradePlan from current positions + zone map, and push MODIFY_TP
    orders to the EA for every ticket.

    Called from:
      - open_signal path right after the initial ticket is confirmed open
      - averaging path right after a new ticket is added
      - zone invalidation path (best-effort; periodic would also work)

    Honors `sig_state.executor_plan.profit_targets` when set: the LLM's
    situational TP plan beats geometric zone filtering. Falls back to zones
    automatically when no executor plan is attached (adopted/legacy trades).

    Silent no-op if zones unavailable or positions empty — we never crash the
    trading loop over a planning failure.

    2026-05-06 BUG FIX: skip COMPLETELY if trade is in 'institutional_recorregut'
    mode. El LLM ja ha decidit el TP correcte al fire (segons expected_bounce_usd
    + estructura). El zone-based replanning era de l'època d'averaging i
    sobreescrivia el TP del LLM amb zones genèriques (causant TPs aleatoris).
    """
    # Bloqueig per a mode recorregut — el LLM mana, no la lògica de zones
    try:
        from signal_state import get_state as _get_sig_state
        _ss_check = _get_sig_state()
        if _ss_check:
            _ep = _ss_check.get('executor_plan') or {}
            if (_ep.get('mode') == 'institutional_recorregut'
                or _ep.get('auto_close_conditions')):
                log.debug(f"[PLAN] {reason} — SKIP (mode recorregut, LLM mana el TP)")
                return None
    except Exception:
        pass

    try:
        import trade_plan as _tp
        from zone_store import read_state, active_zones
        # Build ticket list in the shape trade_plan expects
        tickets = []
        for p in positions or []:
            tickets.append({
                'ticket': int(p.get('ticket', 0) or 0),
                'entry_price': float(p.get('price_open', 0) or p.get('entry_price', 0) or 0),
                'volume': float(p.get('volume', 0) or 0),
            })
        tickets = [t for t in tickets if t['ticket'] and t['volume'] > 0]
        if not tickets:
            return None
        # Zones
        try:
            zstate = read_state(COMMON)
            zones = active_zones(zstate)
        except Exception:
            zones = []
        # ATR_M15
        try:
            bars_m15 = aggregate_bars(bars_cache, 3) if bars_cache else []
            atr_m15 = atr(bars_m15, 14) or (atr(bars_cache, 14) if bars_cache else 1.0) or 1.0
        except Exception:
            atr_m15 = 1.0
        # Executor's tactical TPs (preferred over geometric zones when set).
        # Accepts both legacy list[float] and new list[{price, close_pct, ...}].
        executor_targets = None
        try:
            from signal_state import get_state as _get_sig_state
            _ss = _get_sig_state()
            _exec_plan = _ss.get('executor_plan') if _ss else None
            if isinstance(_exec_plan, dict):
                _pt = _exec_plan.get('profit_targets') or []
                _prices = []
                for _t in _pt:
                    if isinstance(_t, dict):
                        _t = _t.get('price')
                    try:
                        if _t is not None:
                            _prices.append(float(_t))
                    except (TypeError, ValueError):
                        continue
                if _prices:
                    executor_targets = _prices
        except Exception:
            executor_targets = None
        plan = _tp.build_plan(tickets, zones, direction, atr_m15, reason=reason,
                              executor_targets=executor_targets)
        _tp.save_plan(plan)
        log.info(f"[PLAN] {reason} — {_tp.plan_summary(plan)}")
        # Push MODIFY_TP per ticket, but:
        #   - role=TP → send the zone price
        #   - role=RUNNER → send tp=0 (explicit no-TP, will be trailed)
        #   - role=KEEP_TP → DO NOT send anything; preserve any broker-level TP
        #     already in place (avoids the 1-ticket "wipe to 0" bug).
        for a in plan.assignments:
            if a.role == 'KEEP_TP' or a.tp_price is None:
                a.status = 'KEPT'
                continue
            try:
                modify_tp(a.ticket, a.tp_price)
                a.status = 'SENT_OK'
            except Exception as e:
                a.status = 'SEND_FAIL'
                log.warning(f"[PLAN] MODIFY_TP ticket={a.ticket} failed: {e}")
        _tp.save_plan(plan)  # persist send status
        return plan
    except Exception as e:
        log.warning(f"[PLAN] apply_trade_plan failed: {e}")
        return None

def modify_sl(ticket, sl):
    """Set SL on a specific ticket."""
    return write_order({"ts": int(time.time()), "orders": [{"action": "MODIFY_SL", "ticket": ticket, "sl": sl}]})

def modify_all_sl(sl, urgent=False):
    """Set SL on ALL brain tickets. urgent=True overrides pending non-urgent
    orders (use for protective SL moves that must not get queued behind a
    MODIFY_TP). The PEAK-LOCK trail uses urgent=True; LLM-driven SL moves
    typically don't need it."""
    return write_order({"ts": int(time.time()), "orders": [{"action": "MODIFY_ALL_SL", "sl": sl}]}, urgent=urgent)

def move_sl_entry():
    """Move SL of all brain positions to weighted entry (breakeven).

    Uses urgent=True: BE is a PROTECTION move and must override any
    pending non-urgent MODIFY_TP. Without urgent, a fresh MODIFY_TP
    written 1-2s before would block the BE — the brain marks
    breakeven_set=True but the broker still has SL=0, leaving the trade
    unprotected (incident 2026-04-29 17:15).
    """
    return write_order({"ts": int(time.time()), "orders": [{"action": "MOVE_SL_ENTRY"}]}, urgent=True)

def partial_close_pct(ticket, pct):
    """Close X% of a ticket's volume."""
    return write_order({"ts": int(time.time()), "orders": [{"action": "PARTIAL_CLOSE_PCT", "ticket": ticket, "pct": pct}]})


def partial_close_and_move_sl_entry(tickets_pcts):
    """2026-05-06: Combina PARTIAL_CLOSE_PCT + MOVE_SL_ENTRY en una sola
    escriptura d'ordre — així el EA executa les dues accions consecutivament
    al mateix tick i evitem el race condition entre BE i LADDER.

    tickets_pcts: list of (ticket, pct) tuples — un per cada ticket obert
    """
    actions = []
    for tk, pct in tickets_pcts:
        actions.append({"action": "PARTIAL_CLOSE_PCT", "ticket": int(tk), "pct": float(pct)})
    actions.append({"action": "MOVE_SL_ENTRY"})
    return write_order({"ts": int(time.time()), "orders": actions}, urgent=True)

def trail_sl(ticket, distance_usd):
    """Set trailing SL distance (in USD) for a ticket. EA will maintain it."""
    return write_order({"ts": int(time.time()), "orders": [{"action": "TRAIL_SL", "ticket": ticket, "distance": distance_usd}]})

# ═══════════════════════════════════════════════════════════════
# CLAUDE BRAIN PROMPTS — loaded from prompts/*.txt (single source of truth)
# ═══════════════════════════════════════════════════════════════

def _load_prompt_file(name):
    """Load a prompt text from 1_PROYECTO/prompts/<name>. Returns '' on failure
    so the caller can continue (LLM will then run with no system prompt, which
    is degraded but non-fatal). Logs the failure loudly."""
    try:
        path = os.path.join(os.path.dirname(__file__), 'prompts', name)
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        if not content.strip():
            log.error(f"[PROMPT] {name} exists but is empty — LLM will run with no system prompt")
        return content
    except FileNotFoundError:
        log.error(f"[PROMPT] {name} NOT FOUND in prompts/ — LLM will run with empty system prompt")
        return ""
    except Exception as e:
        log.error(f"[PROMPT] {name} load failed ({e}) — LLM will run with empty system prompt")
        return ""


# ── INDICATOR: lectura de mercat, mapa de zones ──
INDICATOR_PROMPT = _load_prompt_file('indicator.txt')

# ── ZONE REVIEWER: valida i consolida el mapa entre cicles ──
ZONE_REVIEWER_PROMPT = _load_prompt_file('zone_reviewer.txt')

# ── EXECUTOR: gestió del trade obert (MANAGE) + staging d'entrades (IDLE) ──
EXECUTOR_PROMPT = _load_prompt_file('executor.txt')
try:
    _exec_prompt_hash_after_load = hashlib.sha1(EXECUTOR_PROMPT.encode('utf-8')).hexdigest()[:12]
    log.info(f"[PROMPT] executor.txt active: {len(EXECUTOR_PROMPT)} chars sha1={_exec_prompt_hash_after_load}")
except Exception as _prompt_log_err_after_load:
    log.warning(f"[PROMPT] executor.txt active hash log failed: {_prompt_log_err_after_load}")

# ── HUNTER: scanner dedicat de reversions curtes ($8-12 price distance scalps) ──
HUNTER_PROMPT = ''  # Hunter eliminat 2026-05-04 — no carreguem hunter.txt


INTERPRETER_PROMPT = """Ets un parser expert de missatges de senyals de trading (XAUUSD scalping) provinents de Telegram (canals TrueTrading, Vikingo, FX Markets).

La teva tasca: classificar el missatge en un dels 5 tipus i extreure la informació clau.

TIPUS:
1. OPEN — missatge que obre una posició nova (ex: "SELL 4790", "compra 4800 con sl 4820", "BUY NOW")
   Extreure: direction (BUY|SELL), entry_price (float o null si diu "now"/"market")

2. MOVE_SL — mou el stop loss a breakeven o a un preu (ex: "movemos SL", "SL a BE", "sl 4800")
   Extreure: breakeven (bool), new_sl (float o null)

3. CLOSE — tanca posicions (ex: "cerramos", "close all", "tanquem")
   Extreure: close_all (bool)

4. NEWS — alerta de noticia (ex: "🚨 NFP en 15 min", "PIB alert", "CPI release 13:30")
   Extreure: news_importance (HIGH|MED|LOW), minutes_until (int o null), event_name (str — l'identificador de l'event: NFP, CPI, ECB, FOMC, PIB, PMI, ISM, etc. null si no és clar)

5. OTHER — cap de les anteriors (text informatiu, comentari, error)

CONTEXT PER DESAMBIGUAR (si arriba al payload):
- `current_trade`: si hi ha un trade obert, tens {direction, entry_price, blend, floating_usd}
  - Missatges ambigus com "4820" o "a BE" s'interpreten millor coneixent la direcció activa:
    · "4820" + current_trade=SELL → probablement MOVE_SL a 4820
    · "4820" + current_trade=null → probablement OTHER (soroll)
  - Si "close" o "cerramos" arriba i NO hi ha trade, retorna OTHER (no té efecte pràctic)

REGLES:
- Si no pots determinar clarament el tipus, retorna OTHER
- El camp "confidence" és la teva seguretat (0.0-1.0). Baixa si no està clar.
- Retorna NOMÉS JSON, sense comentaris addicionals

RESPOSTA (JSON ONLY):
{
  "type": "OPEN|MOVE_SL|CLOSE|NEWS|OTHER",
  "confidence": 0.0-1.0,
  "direction": "BUY|SELL|null",
  "entry_price": 4790.0,
  "breakeven": false,
  "new_sl": null,
  "close_all": false,
  "news_importance": "HIGH|MED|LOW|null",
  "minutes_until": null,
  "event_name": null,
  "raw_summary": "1 frase curta en català explicant què has entès"
}"""


# ── FILTER BRAIN — un trader veterà decideix si aquest senyal val la pena ARA ──
FILTER_PROMPT = """Ets un trader experimentat que ha guanyat molt i ha perdut molt. Acaba d'arribar un senyal de TrueTrading o Vikingo. Decideixes: entro o no entro?

La pregunta no és "aquest canal té raó?" (ja saps que normalment sí). La pregunta és: **aquest moment és BON per entrar?**

═══ COM PENSES ═══

Un trader veterà sap que hi ha moments on el mateix setup funciona i altres on no. Mires:

1. **On està el preu ara?** Està just a una zona on el mateix signal té sentit, o està enmig del no-res?
   - SELL a 5 USD per sota d'una resistència forta = idea decent
   - SELL enmig d'un impuls alcista fort sense retracement = mal moment, millor esperar
   - BUY just després d'un rebuig brutal a suport = excel·lent
   - BUY al mig de consolidació sense direcció = esperar

2. **Què està fent el mercat ara mateix?**
   - Tendència forta contra el senyal → perillós
   - Consolidació / rang → podria anar bé si el senyal és una operació de rang
   - Impuls a favor ja iniciat → segueix l'inèrcia si existeix

3. **Tens pressupost?**
   - DD actual < 50% límit → tens marge
   - DD > 50% → pensa si val la pena arriscar-te
   - DD > 70% → probablement skip, protegeix el que tens

4. **Algún altre senyal actiu?**
   - Si ja hi ha posicions obertes en direction oposat → NO (no fer hedging)
   - Si ja hi ha el mateix direction → depèn del context (normalment skip, ja estàs dins)

5. **Timing**
   - Acabes de perdre el darrer trade en aquesta direcció → prudent
   - A punt d'NFP/CPI/FOMC → DELAY fins després de news
   - Cap de setmana / baixa liquiditat → skip

═══ DECISIONS ═══

- **TAKE**: el setup és bo, el moment és bo, el preu està bé situat, hi ha pressupost. Endavant.
- **SKIP**: alguna cosa no encaixa. Potser el signal és bo però ARA no. No cal entrar tots.
- **DELAY**: hi ha una raó clara per esperar (news imminent, volatilitat extrema, etc.) i després reavaluar.

LOT_ADJUSTMENT (multiplicador del lot base):
- 1.0 = normal
- 0.7 = situació amb risc (entra però més petit)
- 1.3 = situació excepcional (podries ser més agressiu)
- Mai posis < 0.5 o > 1.5

═══ RESPOSTA (JSON ONLY) ═══
{
  "decision": "TAKE|SKIP|DELAY",
  "confidence": 0.0-1.0,
  "reasoning": "2-3 frases EN CATALÀ — per què aquesta decisió, basat en el que veus del mercat",
  "delay_minutes": null_or_number,
  "lot_adjustment": 1.0
}"""


def aggregate_bars(bars_m5, factor):
    """Aggregate M5 bars into higher TF (factor=3 for M15, factor=12 for H1)."""
    result = []
    for i in range(0, len(bars_m5) - factor + 1, factor):
        chunk = bars_m5[i:i+factor]
        result.append({
            'time': chunk[0]['time'],
            'open': chunk[0]['open'],
            'high': max(b['high'] for b in chunk),
            'low': min(b['low'] for b in chunk),
            'close': chunk[-1]['close'],
            'volume': sum(b['volume'] for b in chunk),
        })
    return result


# ════════════════════════════════════════════════════════════════════
# WICK REJECTION DETECTOR — entry_mode "wick"
# ════════════════════════════════════════════════════════════════════
# 2026-05-06: l'usuari va proposar mode estricte d'1 sola oportunitat:
#   El preu arriba a la zona (tolerància $1) → primer bar tancada que
#   reach la zona → check wick rejection → si yes FIRE, si no INVALIDATE.
#
# Aquesta lògica és per evitar stop hunts: en lloc d'entrar instant
# i quedar-nos exposats al sweep dels stops retail, esperem confirmació
# de la rejection (wick + close back inside).

def _detect_wick_rejection(bar, level, direction, atr_val, tolerance=1.0):
    """Detecta rejection en una sola barra. REGLA SIMPLE de l'usuari:

    "Si una vela té un MECHAZO al nivell (la mecha entra dins la zona ±tol
     i representa un rebuig clar), OBRIM."

    Patró de rebuig per SELL @ level:
      1. high >= level - tolerance  (la mecha amunt ha tocat o passat el nivell)
      2. wick_above = high - max(open, close) > 0  (mecha visible)
      3. wick_above > body O wick_above > 30% del range  (mecha dominant)
      4. close NO clarament bullish dins la barra (close <= open + 10% range)

    Per BUY: mirror (mecha avall al low).

    Returns:
      (detected: bool, sl_placement: float | None)
    """
    try:
        high = float(bar.get('high', 0))
        low = float(bar.get('low', 0))
        close = float(bar.get('close', 0))
        open_ = float(bar.get('open', 0))
    except Exception:
        return False, None

    range_ = high - low
    if range_ <= 0:
        return False, None
    body = abs(close - open_)

    # CLOSE_BUFFER: tolerància per al close vs level. Si el close ha recuperat
    # més enllà del level (BUY: close >= level-buffer, SELL: close <= level+buffer),
    # tenim rebuig vàlid. Si el close es queda més enllà del level en direcció
    # contrària, és un breakdown/breakout, no rebuig.
    CLOSE_BUFFER = 1.0

    # MIDA MÍNIMA de la mecha per ser "sweep" reconeixible (no mecha minúscula).
    # Doble criteri OR-relatiu: ≥30% del range OR ≥$1 absolut. Body color irrellevant.
    MIN_WICK_PCT_RANGE = 0.30
    MIN_WICK_USD = 1.0

    # MIN_SL_DISTANCE: SL mínim des d'entry. Si el wick top és més proper,
    # forcem un mínim raonable per a què R/R amb TP1 (típic +$4) tingui sentit.
    # $3 dóna marge contra noise i manté R/R ~1:1.3 amb TP1=$4.
    MIN_SL_DISTANCE = 3.0

    if direction == 'SELL':
        # 1. La mecha amunt ha de PIERÇAR REALMENT el nivell. No vale "estar a prop".
        #    Sense piercing real, no hi ha rebuig estructural — és nomes una vela
        #    que es va apropar.
        if high < level:  # estricte: cal sobrepassar el nivell
            return False, None
        # 2. Mecha visible cap amunt (independentment del color del body)
        wick_above = high - max(open_, close)
        if wick_above <= 0:
            return False, None
        # 3. Mida mínima del sweep — mecha ha de tenir forma reconeixible
        if not (wick_above >= MIN_WICK_PCT_RANGE * range_ or wick_above >= MIN_WICK_USD):
            return False, None
        # 4. Close no clarament rebentant el nivell amunt — si tanca molt
        #    per sobre del level (breakout net), no és rebuig vàlid.
        if close > level + CLOSE_BUFFER:
            return False, None
        # SL: max(level, wick_high) + 0.5. Així el SL queda fora del nivell
        # estructural — no enganxat a una mecha minúscula que travessa just per pèls.
        sl_placement = max(level, high) + 0.5
        return True, sl_placement

    elif direction == 'BUY':
        # Mirror: low ha de PIERÇAR el nivell
        if low > level:
            return False, None
        wick_below = min(open_, close) - low
        if wick_below <= 0:
            return False, None
        if not (wick_below >= MIN_WICK_PCT_RANGE * range_ or wick_below >= MIN_WICK_USD):
            return False, None
        if close < level - CLOSE_BUFFER:
            return False, None
        sl_placement = min(level, low) - 0.5
        return True, sl_placement

    return False, None


def _get_m1_bars():
    """Retorna les M1 bars construïdes des de ticks pel _price_tick_worker.

    Si el cache no està disponible (tick thread no encara) retorna [].
    """
    try:
        return list(globals().get('_M1_BARS_CACHE', []) or [])
    except Exception:
        return []


def _wick_setup_evaluator(setup, bars_cache, atr_val):
    """Evaluate setup amb entry_mode = "wick". Estricta 1-bar logic, ARA EN M1.

    2026-05-06: passat de M5 → M1 perquè detecti sweeps ràpids (l'usuari va
    proposar M1 originalment). M1 cache es construeix des de ticks al
    _price_tick_worker (60s de resolució real).

    Quan apareix una M1 closed que ha REACHED la zona (high/low dins de
    tolerance):
      - Si es detecta rejection → return ('fire', sl_placement)
      - Si NO es detecta rejection → return ('invalidate', None)
      - Si la M1 encara no ha reached la zona → return ('wait', None)

    Tolerància: $1 — la barra es considera "reached" si el high (SELL) o el
    low (BUY) ha arribat a level ± tolerance.

    Per al threshold ATR del wick proporcional, usem ATR_M5 / 5 com a proxy
    d'ATR_M1 (ATR M1 típic ≈ ATR M5 / 4-6). atr_val parameter és ATR M5.
    """
    # Try M1 cache first (preferred, real M1 resolution)
    m1_bars = _get_m1_bars()

    # Fallback: si encara no tenim 2 M1 bars (brain just started, < 2 min),
    # usar M5 cache com a workaround temporal (com abans)
    if len(m1_bars) < 2:
        if not bars_cache or len(bars_cache) < 2:
            return 'wait', None
        bars_to_use = bars_cache
        is_m1 = False
    else:
        bars_to_use = m1_bars
        is_m1 = True

    zone = setup.get('trigger_zone') or setup.get('zone_price', 0)
    direction = (setup.get('direction', '') or '').upper()
    if not zone or direction not in ('BUY', 'SELL'):
        return 'wait', None

    TOLERANCE = 1.0

    # 2026-05-06 BUG FIX: usar la ÚLTIMA bar closed (no penúltima).
    # M1 cache: només conté bars completades (l'append es fa al canvi de minut)
    # M5 cache de TV: també conté bars completades (TV exclou l'in-progress)
    # Usar bars[-2] perdia 1 bar de visibilitat, fent que el wick evaluator
    # mai veiés la bar que acabava de tancar reach la zona.
    closed_bar = bars_to_use[-1]
    closed_ts = closed_bar.get('time', 0)
    last_evaluated = setup.get('_wick_last_evaluated_ts', 0)

    if closed_ts <= last_evaluated:
        return 'wait', None

    high = closed_bar.get('high', 0)
    low = closed_bar.get('low', 0)

    # 2026-05-06: DEBUG log — cada nova barra evaluada, log què veu el wick evaluator
    # Throttled: only log once per bar (closed_ts) per setup
    _last_dbg = setup.get('_wick_last_dbg_ts', 0)
    if closed_ts != _last_dbg:
        setup['_wick_last_dbg_ts'] = closed_ts
        import datetime as _dt
        bar_t = _dt.datetime.fromtimestamp(closed_ts).strftime('%H:%M')
        log.info(
            f"[WICK-DBG] {direction}@{zone} | {'M1' if is_m1 else 'M5'} bar {bar_t}: "
            f"O={closed_bar.get('open',0):.2f} H={high:.2f} L={low:.2f} C={closed_bar.get('close',0):.2f}"
        )

    if direction == 'SELL':
        if high < zone - TOLERANCE:
            log.info(f"[WICK-DBG] not_reached: high {high:.2f} < threshold {zone-TOLERANCE:.2f}")
            return 'wait', None  # encara no ha reached
    elif direction == 'BUY':
        if low > zone + TOLERANCE:
            log.info(f"[WICK-DBG] not_reached: low {low:.2f} > threshold {zone+TOLERANCE:.2f}")
            return 'wait', None

    # Regla de l'usuari: el WICK ha de tocar la zona (no el cos). El que
    # importa és la mecha que rebutja. Si una vela té un mechazo cap a la
    # zona amb patró clar → fire, encara que open/close estiguin lluny.
    log.info(f"[WICK-DBG] REACHED zone — running rejection check")
    setup['_wick_last_evaluated_ts'] = closed_ts

    # ATR threshold ajustat segons TF de la barra
    # M1 ATR ≈ M5 ATR / 4-6 (volatilitat 5x més baixa per ser 5x menor TF)
    effective_atr = atr_val / 5.0 if is_m1 else atr_val

    detected, sl = _detect_wick_rejection(closed_bar, zone, direction, effective_atr, TOLERANCE)
    if detected:
        log.info(f"[WICK] Detection on {'M1' if is_m1 else 'M5'} bar @ ts={closed_ts}, "
                 f"high={high:.2f}, low={low:.2f}, level={zone}")
        return 'fire', sl
    else:
        log.info(f"[WICK] NO rejection on {'M1' if is_m1 else 'M5'} bar @ ts={closed_ts} "
                 f"(reach zone but pattern not met)")
        return 'invalidate', None


def _build_indicator_delta(bars, account):
    """Compact delta payload for INDICATOR follow-up turns (Claude session
    --resume). Only the latest few M5 bars + current price/indicators.

    The full multi-day, volume profile, EMAs, FVG context etc. is in the
    prior turn's payload (cached). LLM updates its zone analysis from
    just the recent bars + key signals.
    """
    if not bars:
        return "DELTA: no bars."

    last = bars[-1]
    closes = [b['close'] for b in bars]

    # Last 5 M5 bars with detail
    last_bars_text = ""
    for b in bars[-5:]:
        t = datetime.fromtimestamp(b['time'], tz=timezone.utc).strftime('%H:%M')
        last_bars_text += f"  {t} {candle_type(b):12s} O={b['open']:.1f} H={b['high']:.1f} L={b['low']:.1f} C={b['close']:.1f} V={b['volume']}\n"

    cur_rsi = rsi(closes, 14) or 0
    cur_atr = atr(bars, 14) or 0
    cur_vol = vol_ratio(bars)
    session = detect_session()

    # Account context (compact)
    acc_line = ""
    if account.get('has_signal'):
        acc_line = f"Senyal {account['direction']} actiu @ {account['entry_price']} ({account['pos_count']} posicions)"
    else:
        acc_line = "Cap senyal actiu"

    # GC1! institutional levels (H4/D1) — slow-changing context. Refresh
    # cada cycle al delta payload perquè la sessió de Claude tingui els
    # POCs actuals fins i tot quan el cache anterior no els tenia.
    # 2026-05-04: afegit per assegurar que el delta path porta sempre
    # info institucional H4/D1 (que abans només arribava al cold start).
    inst_block = ""
    try:
        gc_h4 = _fetch_gc_bars("gc_h4", "240", 180, cache_seconds=3600)
        gc_d1 = _fetch_gc_bars("gc_d1", "1D", 30, cache_seconds=14400)
        inst_lines = []
        # H4 últim POC i Naked POCs (importants com a imants)
        if gc_h4 and len(gc_h4) >= 30:
            try:
                from indicator_context import _poc_per_day, _naked_pocs
                weekly = _poc_per_day(gc_h4, n_days=20)
                if weekly:
                    naked_h4 = _naked_pocs(weekly, gc_h4)
                    if naked_h4:
                        gc_close = gc_h4[-1].get("close", 0) if gc_h4 else 0
                        # Top 3 Naked POCs H4 més propers al preu actual
                        sorted_naked = sorted(naked_h4, key=lambda p: abs(p["poc"] - gc_close))[:3]
                        inst_lines.append(
                            "Naked POCs H4 GC1!: " +
                            ", ".join(f"{p['poc']:.0f}" for p in sorted_naked)
                        )
            except Exception:
                pass
        # D1 POC agregat (commit institucional mensual)
        if gc_d1 and len(gc_d1) >= 5:
            try:
                from collections import defaultdict
                buckets = defaultdict(float)
                for b in gc_d1:
                    h, l, v = b.get("high", 0), b.get("low", 0), b.get("volume", 0)
                    if h <= l or v <= 0:
                        continue
                    span = max(h - l, 0.01)
                    bucket_count = max(1, int(span))
                    vol_per_bucket = v / bucket_count
                    p = l
                    while p < h:
                        buckets[round(p)] += vol_per_bucket
                        p += 1
                if buckets:
                    poc_30d = max(buckets.items(), key=lambda kv: kv[1])
                    gc_close = gc_d1[-1].get("close", 0)
                    dist = poc_30d[0] - gc_close
                    inst_lines.append(
                        f"POC 30d D1 GC1!: {poc_30d[0]} (dist {dist:+.0f}$ del preu)"
                    )
            except Exception:
                pass
        if inst_lines:
            inst_block = "\n" + "\n".join(inst_lines)
    except Exception:
        pass

    # Flow proxy compacte dual-feed (spot + futures GC1! + spread)
    flow_line = ""
    try:
        fp = _flow_proxy_dict()

        def _src_parts(src_dict):
            parts = []
            vb = src_dict.get("m5_volume_burst") or {}
            if vb:
                z = vb.get("zscore")
                z_txt = f" z={z:+.2f}σ" if z is not None else ""
                n = vb.get("contracts_last_bar", 0)
                parts.append(f"VolM5 {n:,} ({vb.get('pct_vs_6h_avg', 0):.0f}%/6h{z_txt})")
            cm = src_dict.get("m15_cmf") or {}
            if cm:
                parts.append(f"CMF_M15 {cm.get('value', 0):+.3f}({cm.get('streak_bars', 0)}b)")
            ob = src_dict.get("h1_obv") or {}
            if ob:
                div = ob.get("divergence_48h", "n/a")
                parts.append(f"OBV_H1 Δ4h={ob.get('change_4h', 0):+,.0f} div={div}")
            return parts

        spot_block = fp.get("spot") or {}
        fut_block = fp.get("futures") or {}
        spread = fp.get("spread_spot_futures") or {}

        lines = []
        if spot_block:
            sp = _src_parts(spot_block)
            if sp:
                lines.append("Flux SPOT: " + " · ".join(sp))
        if fut_block:
            fp_parts = _src_parts(fut_block)
            if fp_parts:
                lines.append("Flux GC1!: " + " · ".join(fp_parts))
        if spread:
            lines.append(
                f"Spread GC1!−spot: {spread.get('spread_usd', 0):+.2f}$"
            )
        if lines:
            flow_line = "\n" + "\n".join(lines)
    except Exception:
        pass

    return f"""═══ INDICATOR — DELTA ═══
Continua l'anàlisi del turn anterior. Tens TOT el context cachejat
(multi-TF complet, històric multi-dia, volume profile, etc).
Aquí només les dades NOVES.

Hora: {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC | Sessió: {session}
Preu: {last['close']:.2f} | RSI(14): {cur_rsi} | ATR: {cur_atr} | Vol: {cur_vol}x | Vela: {candle_type(last)}{inst_block}{flow_line}

═══ M5 últimes 5 veles ═══
{last_bars_text.rstrip()}

═══ COMPTE ═══
{acc_line}
DD: {account.get('dd_pct', 0):.1f}% | Equity: ${account.get('equity', 0):,.0f}

Refresc l'anàlisi de zones amb aquestes dades noves. JSON ONLY."""


def build_brain_prompt(bars, account, screenshot_path=None):
    """Build the data payload for Claude with multiframe analysis.

    Multi-turn cost optimisation: when INDICATOR is on Claude with sessions
    AND it's not the first turn, return a compact DELTA payload.
    """
    if not bars: return None

    # Same gating logic as EXECUTOR — delta only on Claude+session
    try:
        import claude_session_manager as _csm
        _is_first = _csm.is_first_turn('INDICATOR')
        _cfg = _get_llm_config('indicator') if '_get_llm_config' in globals() else {}
        _provider = (_cfg.get('provider') or '').lower()
        _use_delta = (_provider == 'claude') and (not _is_first)
    except Exception:
        _use_delta = False

    if _use_delta:
        return _build_indicator_delta(bars, account)

    closes = [b['close'] for b in bars]
    last = bars[-1]

    # Price context
    high_20 = max(b['high'] for b in bars[-20:])
    low_20 = min(b['low'] for b in bars[-20:])

    # Format last 20 M5 bars compactly
    bar_text = ""
    for b in bars[-20:]:
        t = datetime.fromtimestamp(b['time'], tz=timezone.utc).strftime('%H:%M')
        c = candle_type(b)
        bar_text += f"  {t} {c:12s} O={b['open']:.1f} H={b['high']:.1f} L={b['low']:.1f} C={b['close']:.1f} V={b['volume']}\n"

    # ── Multiframe: M15 and H1 from M5 bars ──
    bars_m15 = aggregate_bars(bars, 3)
    bars_h1 = aggregate_bars(bars, 12)

    m15_closes = [b['close'] for b in bars_m15]
    h1_closes = [b['close'] for b in bars_h1]

    m15_rsi = rsi(m15_closes, 14)
    m15_ema20 = ema(m15_closes, 20)
    m15_ema50 = ema(m15_closes, 50)
    m15_atr = atr(bars_m15, 14)

    h1_rsi = rsi(h1_closes, 14)
    h1_ema20 = ema(h1_closes, 20)
    h1_ema50 = ema(h1_closes, 50)
    h1_atr = atr(bars_h1, 14)

    # M15 last 3 bars
    m15_text = ""
    for b in bars_m15[-3:]:
        t = datetime.fromtimestamp(b['time'], tz=timezone.utc).strftime('%H:%M')
        m15_text += f"  {t} {candle_type(b):12s} H={b['high']:.1f} L={b['low']:.1f} C={b['close']:.1f}\n"

    # H1 last 3 bars
    h1_text = ""
    for b in bars_h1[-3:]:
        t = datetime.fromtimestamp(b['time'], tz=timezone.utc).strftime('%H:%M')
        h1_text += f"  {t} {candle_type(b):12s} H={b['high']:.1f} L={b['low']:.1f} C={b['close']:.1f}\n"

    # Weighted entry from positions
    w_entry = 0
    total_lots = 0
    if account['positions']:
        for p in account['positions']:
            lot = p.get('volume', p.get('lot', 0))
            price = p.get('price_open', p.get('open_price', 0))
            total_lots += lot
            w_entry += price * lot
        if total_lots > 0:
            w_entry = round(w_entry / total_lots, 2)

    high_50 = max(b['high'] for b in bars[-50:]) if len(bars) >= 50 else high_20
    low_50 = min(b['low'] for b in bars[-50:]) if len(bars) >= 50 else low_20
    range_50 = high_50 - low_50
    high_200 = max(b['high'] for b in bars[-200:]) if len(bars) >= 200 else high_50
    low_200 = min(b['low'] for b in bars[-200:]) if len(bars) >= 200 else low_50

    # Multi-day history + session + volume nodes
    daily_hist = update_daily_history(bars)
    session = detect_session()
    vol_nodes = compute_volume_nodes(bars)

    # Format multi-day lines (today + up to 5 prior)
    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    hist_lines = []
    for date in sorted(daily_hist.keys(), reverse=True)[:6]:
        hl = daily_hist[date]
        label = "Avui " if date == today_str else f"-{(datetime.now(timezone.utc).date() - datetime.strptime(date,'%Y-%m-%d').date()).days}d "
        hist_lines.append(f"  {label} {date}  High={hl['high']:.2f}  Low={hl['low']:.2f}  Rang={hl['high']-hl['low']:.1f}")
    hist_text = "\n".join(hist_lines) if hist_lines else "  (sense historial)"

    # Format volume nodes (skip section entirely if no volume data available)
    vol_section = ""
    if vol_nodes:
        vol_lines = []
        for n in vol_nodes:
            dist = last['close'] - n['price']
            vol_lines.append(f"  ${n['price']:.1f}  vol={n['vol']:,}  dist={dist:+.1f}")
        vol_section = "\n═══ ZONES D'ALT VOLUM (institucional, últimes 16h) ═══\n" + "\n".join(vol_lines) + "\n"

    # ── ORDER OPTIMISED FOR PROMPT-CACHE ──
    # Stable / semi-stable first (multi-day H/L, volume nodes, session block,
    # rich context which itself is semi-stable across consecutive ticks),
    # then volatile data (current price, M5/M15/H1 bars, account, positions),
    # then call-meta (timestamp + bar tail) at the very end.
    # Same content as before, just reordered — the LLM doesn't care about
    # section order within the prompt.
    data = f"""═══ HISTÒRIC MULTI-DIA (H/L per dia) ═══
{hist_text}
{vol_section}
{_indicator_context_block(bars, account, atr(bars, 14) or 0)}
{_indicator_rich_context(bars, account)}
{_flow_proxy_block()}
═══ M5 (operatiu) ═══
RSI(14): {rsi(closes, 14)} | EMA20: {ema(closes, 20)} | EMA50: {ema(closes, 50)}
ATR: {atr(bars, 14)} | Vol: {vol_ratio(bars)}x | Vela: {candle_type(last)}
{bar_text.rstrip()}

═══ M15 (context) ═══
RSI: {m15_rsi} | EMA20: {m15_ema20} | EMA50: {m15_ema50} | ATR: {m15_atr}
{m15_text.rstrip()}

═══ H1 (tendència) ═══
RSI: {h1_rsi} | EMA20: {h1_ema20} | EMA50: {h1_ema50} | ATR: {h1_atr}
{h1_text.rstrip()}

═══ COMPTE ═══
Balance: ${account['balance']:,.0f} | Equity: ${account['equity']:,.0f}
DD usat: ${account['dd_used']:.0f} ({account['dd_pct']:.1f}%) | DD limit: ${account['dd_limit']:.0f} (4%)
DD restant: ${account['dd_remaining']:.0f}

═══ POSICIONS ═══
Senyal actiu: {'SÍ — ' + account['direction'] + ' des de ' + str(account['entry_price']) if account['has_signal'] else 'NO'}
Posicions obertes: {account['pos_count']}
Weighted entry: {w_entry if w_entry else 'N/A'}
Total lots: {total_lots if total_lots else 0}
Tancant (cerramos): {'SÍ' if account.get('closing') else 'NO'}

═══ DADES TEMPS REAL ═══
Hora: {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC
Sessió: {session}
Preu: {last['close']:.2f} | High20: {high_20:.2f} | Low20: {low_20:.2f}
High50: {high_50:.2f} | Low50: {low_50:.2f} | Rang50: {range_50:.1f}
Day High: {high_200:.2f} | Day Low: {low_200:.2f} | Day Range: {high_200-low_200:.1f}

Analitza com un trader expert. Què fas? JSON ONLY."""

    return data


def _indicator_context_block(bars, account, atr_current):
    """Produce the CONTEXT EXTERN + HTF + SESSION block for Indicator prompt.

    Textual — the Indicator prompt is plain text, not JSON. Falls back to empty
    string on any failure (defensive, never blocks Indicator cycle).
    """
    try:
        import market_context as _mc
        cfg = _load_app_config()
        ctx = _mc.build_market_context(
            bars_m5=bars,
            account=account,
            tv_helper=tv,
            now_utc=datetime.now(timezone.utc),
            for_executor=False,      # Indicator version: HTF-heavy, no liquidity
            atr_m5=atr_current,
            config=cfg,
        )
    except Exception as e:
        log.warning(f"[INDICATOR_PROMPT] market_context failed: {e}")
        return ""

    lines = ["", "═══ CONTEXT EXTERN ═══"]
    dxy = ctx.get("external", {}).get("dxy")
    if dxy:
        brk = dxy.get('last_break', 'none')
        brk_txt = f" · trencament de {brk} recent" if brk != 'none' else ""
        lines.append(
            f"DXY: {dxy.get('price')} — M5 {dxy.get('trend_m5')} · H1 {dxy.get('trend_h1')}{brk_txt}"
        )
    y10 = ctx.get("external", {}).get("yield_10y")
    if y10:
        lines.append(f"10Y yield: {y10.get('price')} tendència {y10.get('trend_m15')}")

    htf = ctx.get("htf")
    if htf:
        d1h, d1l, d1c = htf.get('d1_high'), htf.get('d1_low'), htf.get('d1_close')
        if d1h and d1l and d1c:
            lines.append(f"D1 ahir: H={d1h} L={d1l} C={d1c}")
        if htf.get('weekly_open'):
            lines.append(f"Setmanal open: {htf['weekly_open']}")
        if htf.get('nearest_round'):
            lines.append(f"Round level més proper: {htf['nearest_round']}")

    ms = ctx.get("market_state", {})
    sess = ms.get("session") or {}
    if sess.get("name"):
        vwap_txt = ""
        if sess.get("vwap") is not None and sess.get("distance_from_vwap_usd") is not None:
            vwap_txt = f" · VWAP {sess['vwap']} (distància {sess['distance_from_vwap_usd']:+.2f})"
        lines.append(f"Sessió: {sess['name']} (minut {sess.get('minutes_since_open', 0)}){vwap_txt}")

    vol = ms.get("volatility") or {}
    if vol.get("atr_current") is not None and vol.get("percentile_20d") is not None:
        anom = " ⚠ anòmal" if vol.get("is_anomalous") else " (normal)"
        lines.append(f"ATR M5: {vol['atr_current']} — percentil {vol['percentile_20d']:.0f} vs 20d{anom}")

    bos = (ms.get("structure") or {}).get("last_bos")
    if bos:
        lines.append(f"Últim BOS M15: {bos.get('type')} @ {bos.get('price')} (fa {bos.get('age_bars')} bars)")

    return "\n".join(lines)


_MTF_BARS_CACHE = {
    "m5": {"ts": 0, "bars": []},
    "m15": {"ts": 0, "bars": []},
    "h1": {"ts": 0, "bars": []},
    "h4": {"ts": 0, "bars": []},
    "d1": {"ts": 0, "bars": []},
    # COMEX:GC1! futures bars — paral·lel al spot per al flux institucional.
    # Mateixos TTLs (60s/180s/600s/1800s/3600s). Símbol guard NO afectat: el
    # fetch passa per ohlcv-tf-sym que restaura el chart al final.
    "gc_m5":  {"ts": 0, "bars": []},
    "gc_m15": {"ts": 0, "bars": []},
    "gc_h1":  {"ts": 0, "bars": []},
    # H4/D1 GC1! — POCs institucionals multi-setmanal/mensuals (afegits
    # 2026-05-04). TTL alt → 1-2 swaps de chart per hora, impacte mínim.
    "gc_h4":  {"ts": 0, "bars": []},
    "gc_d1":  {"ts": 0, "bars": []},
    # M1 GC1! — usat per approach_tracker per delta granular (30s, només
    # quan hi ha zones prop del preu)
    "gc_m1":  {"ts": 0, "bars": []},
}


# ───────────── Approach Tracker singleton ─────────────
_APPROACH_TRACKER_INSTANCE = None


def _get_approach_tracker():
    """Lazy-init del singleton ApproachTracker. Retorna None si disabled o
    no es pot inicialitzar."""
    global _APPROACH_TRACKER_INSTANCE
    if _APPROACH_TRACKER_INSTANCE is not None:
        return _APPROACH_TRACKER_INSTANCE
    try:
        cfg = _load_app_config() or {}
        at_cfg = cfg.get("approach_tracker") or {}
        if not at_cfg.get("enabled", True):
            return None
        from approach_tracker import ApproachTracker
        _APPROACH_TRACKER_INSTANCE = ApproachTracker(at_cfg)
        log.info(
            f"[APPROACH_TRACKER] initialized — approach_dist={_APPROACH_TRACKER_INSTANCE.approach_dist} "
            f"zone_tol={_APPROACH_TRACKER_INSTANCE.zone_tol} "
            f"threshold_fut={_APPROACH_TRACKER_INSTANCE.delta_threshold_futures}"
        )
        return _APPROACH_TRACKER_INSTANCE
    except Exception as e:
        log.warning(f"[APPROACH_TRACKER] init failed: {e}")
        return None


def _fetch_mtf_bars(tf_label, tf_arg, count, cache_seconds):
    """Fetch bars at a specific TF via tv.js ohlcv-tf, with in-process cache.

    tf_label: dict key ("m5", "m15", "h1")
    tf_arg:   tv.js timeframe arg ("5", "15", "60")
    count:    number of bars
    cache_seconds: TTL for cache
    Returns list of bar dicts (possibly stale on transient failure).
    """
    now = time.time()
    cache = _MTF_BARS_CACHE.get(tf_label) or {"ts": 0, "bars": []}
    if cache["bars"] and (now - cache["ts"]) < cache_seconds:
        return cache["bars"]
    try:
        # Wider timeout — TF swap involves chart reload + bars fetch
        resp = tv("ohlcv-tf", tf_arg, count, timeout=20)
    except Exception as e:
        log.warning(f"[INDICATOR_PROMPT] ohlcv-tf {tf_label} failed: {e}")
        return cache["bars"]  # serve stale rather than nothing
    if resp and resp.get("success") and resp.get("bars"):
        bars = resp["bars"]
        _MTF_BARS_CACHE[tf_label] = {"ts": now, "bars": bars}
        return bars
    # Failure (timeout / restore_failed / etc.) — keep last good cache
    if resp and not resp.get("success"):
        log.warning(
            f"[INDICATOR_PROMPT] ohlcv-tf {tf_label} unsuccessful: "
            f"{resp.get('error', 'unknown')}"
        )
    return cache["bars"]


# Símbol secundari per al flux institucional (CME real-time, €7/mes).
# NO és el símbol d'execució — només per anàlisi (flow_proxy).
_GC_FUTURES_SYMBOL = "COMEX:GC1!"


def _fetch_gc_bars(tf_label, tf_arg, count, cache_seconds):
    """Germà de _fetch_mtf_bars per a COMEX:GC1! futures.

    Volum REAL en contractes (1 contracte = 100 oz). Usat només per
    flow_proxy — NO toca cap path d'execució. Si TV està penjat, mercat
    CME tancat, o subscripció caducada, retorna [] sense errors.

    2026-05-04 v2: RE-HABILITAT TOT. El bug que feia el chart stuck era el
    comand `chart-set-symbol` (no existia a tv.js). Ara amb `tv("symbol", X)`
    el restore funciona fiablement. Mantenim tots els TFs de GC1! per a no
    perdre context institucional.

    tf_label: dict key ("gc_m5", "gc_m15", "gc_h1", "gc_h4", "gc_d1", "gc_m1")
    """
    now = time.time()
    cache = _MTF_BARS_CACHE.get(tf_label) or {"ts": 0, "bars": []}
    if cache["bars"] and (now - cache["ts"]) < cache_seconds:
        return cache["bars"]
    try:
        # ohlcv-tf-sym: atòmic [swap a símbol+TF + read + restore].
        # No interfereix amb el chart actual del brain — es restaura.
        resp = tv("ohlcv-tf-sym", _GC_FUTURES_SYMBOL, tf_arg, count, timeout=20)
    except Exception as e:
        log.warning(f"[FLOW_PROXY] gc fetch {tf_label} failed: {e}")
        # Safety: assert chart back to XAUUSD M5 (atomic restore unreliable)
        try:
            tv("symbol", EXPECTED_SYMBOL, timeout=15)
            tv("timeframe", "5", timeout=10)
        except Exception:
            pass
        return cache["bars"]
    # Safety net 2026-05-04: explicit restore a XAUUSD M5 després de cada
    # gc fetch. ohlcv-tf-sym hauria de fer-ho atòmicament però falla
    # ocasionalment, deixant el chart visible a GC1!.
    try:
        tv("symbol", EXPECTED_SYMBOL, timeout=15)
        tv("timeframe", "5", timeout=10)
    except Exception:
        pass
    if resp and resp.get("success") and resp.get("bars"):
        bars = resp["bars"]
        _MTF_BARS_CACHE[tf_label] = {"ts": now, "bars": bars}
        return bars
    if resp and not resp.get("success"):
        log.warning(
            f"[FLOW_PROXY] gc fetch {tf_label} unsuccessful: "
            f"{resp.get('error', 'unknown')}"
        )
    return cache["bars"]


def _indicator_rich_context(bars, account):
    """Produce the MTF + volume profile + liquidity + correlations + technicals
    block for the Indicator prompt.

    Fetches dedicated TF data (M5×288=24h, M15×288=3d, H1×168=7d) so the
    Indicator can identify multi-day structural levels — the M1 chart only
    covers 5h, which was leaving big gaps below recent trading range. Cache
    per TF prevents thrashing tv.js: M5 60s, M15 180s, H1 600s.
    """
    try:
        # Multi-TF bars dedicated to the Indicator (NOT used by FAST engine).
        # H4 + D1 added 2026-04-29 to give the LLM enough structural context
        # to identify multi-week swing levels when price breaks to new lows
        # (the previous H1×7d window was blind to anything older).
        # TTLs estesos 2026-05-04 per minimitzar flicker del chart TV.
        # M5/M15 mantenen freshness tàctic; H1+ i tots els futures pugen
        # significativament perquè la seva info canvia lentament.
        bars_m5 = _fetch_mtf_bars("m5", "5", 288, cache_seconds=60)         # 24h, fresh
        bars_m15 = _fetch_mtf_bars("m15", "15", 288, cache_seconds=300)     # 72h (5min)
        bars_h1 = _fetch_mtf_bars("h1", "60", 168, cache_seconds=1200)      # 7d (20min)
        bars_h4 = _fetch_mtf_bars("h4", "240", 180, cache_seconds=3600)     # 30d (1h)
        bars_d1 = _fetch_mtf_bars("d1", "1D", 30, cache_seconds=7200)       # 30d (2h)
        # GC1! M5/H4/D1 per al volume profile FUTURES — TTLs molt alts
        # perquè estructura institucional canvia poc en horitzons llargs.
        gc_m5 = _fetch_gc_bars("gc_m5", "5", 288, cache_seconds=120)        # 24h (2min)
        gc_h4 = _fetch_gc_bars("gc_h4", "240", 180, cache_seconds=3600)     # 30d (1h)
        gc_d1 = _fetch_gc_bars("gc_d1", "1D", 30, cache_seconds=14400)      # 30d (4h)
        log.info(
            f"[INDICATOR_PROMPT] MTF bars: M5={len(bars_m5)} M15={len(bars_m15)} "
            f"H1={len(bars_h1)} H4={len(bars_h4)} D1={len(bars_d1)} "
            f"GC_M5={len(gc_m5)} GC_H4={len(gc_h4)} GC_D1={len(gc_d1)}"
        )
        import indicator_context as _ic
        return _ic.build_all(
            bars_m5=bars_m5 or bars,   # M5 dedicated fetch (fallback to bars if fetch failed)
            account=account,
            tv_helper=tv,
            bars_m15=bars_m15,
            bars_h1=bars_h1,
            bars_h4=bars_h4,
            bars_d1=bars_d1,
            gc_m5=gc_m5,
            gc_h4=gc_h4,
            gc_d1=gc_d1,
        )
    except TypeError:
        # build_all signature might not accept new kwargs yet — fallback
        try:
            import indicator_context as _ic
            return _ic.build_all(bars_m5=bars, account=account, tv_helper=tv)
        except Exception as e:
            log.warning(f"[INDICATOR_PROMPT] rich_context fallback failed: {e}")
            return ""
    except Exception as e:
        log.warning(f"[INDICATOR_PROMPT] rich_context failed: {e}")
        return ""


def _flow_proxy_block():
    """Calcula el flow proxy dual-feed (spot + GC1! futures) i retorna el
    bloc de text per al prompt. Buit si no hi ha cap font de dades.

    Reutilitza el cache MTF (_fetch_mtf_bars per spot, _fetch_gc_bars per
    futures). Cache hits gairebé sempre — _indicator_rich_context ha
    demanat les bars spot fa segons; les futures es renoven al primer
    cycle i després cada 60-600s segons TF.
    """
    try:
        import flow_proxy as _fp
        # TTLs alineats amb _indicator_rich_context perquè comparteixen
        # el cache global → fetch un sol cop, reusat per ambdós.
        bars_m5 = _fetch_mtf_bars("m5", "5", 288, cache_seconds=60)
        bars_m15 = _fetch_mtf_bars("m15", "15", 288, cache_seconds=300)
        bars_h1 = _fetch_mtf_bars("h1", "60", 168, cache_seconds=1200)
        # Futures CME — fallback graceful si fetch falla.
        gc_m5  = _fetch_gc_bars("gc_m5",  "5",  288, cache_seconds=120)
        gc_m15 = _fetch_gc_bars("gc_m15", "15", 288, cache_seconds=600)
        gc_h1  = _fetch_gc_bars("gc_h1",  "60", 168, cache_seconds=1800)
        spot_price = bars_m5[-1].get("close") if bars_m5 else None
        gc_price   = gc_m5[-1].get("close")  if gc_m5 else None
        fp = _fp.build_flow_proxy(
            bars_m5, bars_m15, bars_h1,
            gc_m5=gc_m5, gc_m15=gc_m15, gc_h1=gc_h1,
            spot_price=spot_price, gc_price=gc_price,
        )
        return _fp.render_flow_proxy(fp)
    except Exception as e:
        log.warning(f"[INDICATOR_PROMPT] flow_proxy failed: {e}")
        return ""


def _flow_proxy_dict():
    """Versió dict-only del flow proxy per a delta payloads (compacta).

    Inclou les claus top-level (alies de `spot`) per backward compat amb
    consumidors antics, més els blocs `spot`, `futures` i
    `spread_spot_futures` quan estan disponibles.
    """
    try:
        import flow_proxy as _fp
        # TTLs alineats amb _indicator_rich_context (cache compartit).
        bars_m5 = _fetch_mtf_bars("m5", "5", 288, cache_seconds=60)
        bars_m15 = _fetch_mtf_bars("m15", "15", 288, cache_seconds=300)
        bars_h1 = _fetch_mtf_bars("h1", "60", 168, cache_seconds=1200)
        gc_m5  = _fetch_gc_bars("gc_m5",  "5",  288, cache_seconds=120)
        gc_m15 = _fetch_gc_bars("gc_m15", "15", 288, cache_seconds=600)
        gc_h1  = _fetch_gc_bars("gc_h1",  "60", 168, cache_seconds=1800)
        spot_price = bars_m5[-1].get("close") if bars_m5 else None
        gc_price   = gc_m5[-1].get("close")  if gc_m5 else None
        return _fp.build_flow_proxy(
            bars_m5, bars_m15, bars_h1,
            gc_m5=gc_m5, gc_m15=gc_m15, gc_h1=gc_h1,
            spot_price=spot_price, gc_price=gc_price,
        )
    except Exception:
        return {}


DECISIONS_LOG_FILE_NAME = 'brain_executor_decisions.jsonl'


def _decisions_log_path():
    return os.path.join(COMMON, DECISIONS_LOG_FILE_NAME)


def read_last_executor_decision(trade_id):
    """Read the most recent Executor decision row for `trade_id` from
    brain_executor_decisions.jsonl, or None if none exists."""
    if not trade_id:
        return None
    path = _decisions_log_path()
    if not os.path.exists(path):
        return None
    try:
        last = None
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get('trade_id') == trade_id:
                    last = row
        return last
    except OSError:
        return None


def append_executor_decision(row):
    """Append one JSON row to brain_executor_decisions.jsonl. Never raises."""
    try:
        with open(_decisions_log_path(), 'a', encoding='utf-8') as f:
            f.write(json.dumps(row, separators=(',', ':')) + '\n')
    except OSError:
        pass


_SNAPSHOTS_LOG = os.path.join(COMMON, 'brain_executor_snapshots.jsonl')


def append_executor_snapshot(row):
    """Append one rich-context snapshot row to brain_executor_snapshots.jsonl.

    Captured at prompt-build time — preserves the EXACT market context the
    LLM saw when it made the decision. Joined back with the corresponding
    executor_decision via (trade_id, ts) so the post-mortem can evaluate
    each decision against its own context, not against later hindsight.

    Best-effort writer: never raises, never blocks the trading loop.
    """
    try:
        with open(_SNAPSHOTS_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(row, separators=(',', ':'), ensure_ascii=False, default=str) + '\n')
            f.flush()
        log.info(f"[SNAPSHOT] wrote (size now: {os.path.getsize(_SNAPSHOTS_LOG)})")
    except Exception as e:
        log.warning(f"[SNAPSHOT] append failed: {type(e).__name__}: {e}")


def parse_invalidation_condition(raw):
    """Parse an Executor `invalidation_condition` field into {text, structured}.

    Accepts either a string (legacy/untyped prompt output) or an object with
    a `text` field plus optional `structured` object. Attempts to extract a
    simple structured form if only text is given:
      "si M5 tanca sota 4782 amb volum" →
        {direction: below, trigger: close, price: 4782.0, require_volume: true}
    Returns {"text": str, "structured": dict|None}.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, dict):
        text = raw.get('text') or ''
        structured = raw.get('structured')
        if not isinstance(structured, dict):
            structured = None
        return {"text": text, "structured": structured}

    text = str(raw)
    structured = _try_parse_invalidation_text(text)
    return {"text": text, "structured": structured}


def _try_parse_invalidation_text(text):
    """Best-effort extraction of structured fields from a Catalan/Spanish invalidation text."""
    if not text:
        return None
    import re
    lower = text.lower()
    direction = None
    if any(w in lower for w in (' sota ', ' per sota', ' bajo', ' under ', ' below ')):
        direction = 'below'
    elif any(w in lower for w in (' sobre ', ' per sobre', ' encima', ' over ', ' above ')):
        direction = 'above'
    trigger = 'close' if 'tanca' in lower or 'close' in lower or 'tancament' in lower else ('break' if 'trenca' in lower or 'break' in lower else None)
    require_volume = ('amb volum' in lower) or ('with volume' in lower) or ('con volum' in lower)
    m = re.search(r'(\d{3,5}(?:[.,]\d+)?)', text)
    price = None
    if m:
        try:
            price = float(m.group(1).replace(',', '.'))
        except ValueError:
            price = None
    if direction is None or price is None or trigger is None:
        return None
    return {
        "direction": direction,
        "trigger": trigger,
        "price": price,
        "require_volume": require_volume,
    }


def _force_executor_review(reason: str):
    """Triggera una re-execució de l'EXECUTOR quan algun esdeveniment important
    ha passat amb un staged_setup (fire, invalidate, expire). L'EXECUTOR
    rebrà el payload nou amb l'estat actualitzat i decidirà què fer.

    User reported: "sempre que un plan s'executa o s'invalida o es borra s ha
    de cridar a executor". Aquest helper ho fa des d'un sol lloc.
    """
    try:
        ctrl_path = os.path.join(COMMON, 'brain_controls.json')
        ctrl = {}
        if os.path.exists(ctrl_path):
            try:
                with open(ctrl_path, 'r', encoding='utf-8') as f:
                    ctrl = json.load(f)
            except Exception:
                ctrl = {}
        ctrl['force_executor'] = True
        ctrl['force_executor_reason'] = reason[:200]
        ctrl['force_executor_ts'] = time.time()
        with open(ctrl_path, 'w', encoding='utf-8') as f:
            json.dump(ctrl, f, indent=2)
        try:
            log.info(f"[FORCE_EXECUTOR] triggered: {reason}")
        except Exception:
            pass
    except Exception as _e:
        try:
            log.warning(f"[FORCE_EXECUTOR] failed: {_e}")
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# AUTO-CLOSE WATCHER — Mode Recorregut Institucional (2026-05-04)
# ═══════════════════════════════════════════════════════════════════════════
#
# Aquest mòdul avalua les `auto_close_conditions` que l'EXECUTOR LLM ha
# pre-aprovat per a cada trade. NO és una regla determinista del codi: cada
# condició la va posar el LLM raonant qualitativament la tesi del trade.
# El FastEngine només vigila el que el LLM li ha encarregat per a aquell trade.
#
# Filosofia: "si una condició clara que el LLM ha posat es compleix, tanquem.
# Si no, encara podria anar més en contra."

# Mapping de noms de mètriques a resolvers contra dades vives. Ha de ser
# coherent amb _VALID_METRICS de staged_setups.py.
def _resolve_metric(metric_name, flow_proxy_now, approach_state_now):
    """Retorna el valor actual d'una mètrica per a avaluar condicions metric.

    Si la mètrica no es pot resoldre (dades absents), retorna None i la
    condició no dispara aquest cycle (es re-avaluarà al pròxim).

    Estructura real del flow_proxy (vegi flow_proxy.build_flow_proxy):
        fp['spot'] / fp['futures'] = {
            'm5_volume_burst': {'pct_vs_6h_avg', 'zscore', 'contracts_last_bar'?},
            'm15_cmf':         {'value', 'streak_bars'},
            'h1_obv':          {'change_4h', 'divergence_48h'},
            'm5_cvd_proxy':    {'cvd_4h', 'cvd_last_bar', 'bullish_bar_ratio_1h'},
            ...
        }
        fp['spread_spot_futures'] = {'spread_usd', 'spot_price', 'gc_price'}
    """
    try:
        fp = flow_proxy_now or {}
        spot = fp.get('spot') or {}
        fut = fp.get('futures') or {}
        # CMF value/streak
        if metric_name == 'futures.cmf_value':
            return (fut.get('m15_cmf') or {}).get('value')
        if metric_name == 'futures.cmf_streak_signed':
            blk = fut.get('m15_cmf') or {}
            v = blk.get('value')
            s = blk.get('streak_bars')
            if v is None or s is None:
                return None
            return float(s) if v >= 0 else -float(s)
        if metric_name == 'spot.cmf_value':
            return (spot.get('m15_cmf') or {}).get('value')
        if metric_name == 'spot.cmf_streak_signed':
            blk = spot.get('m15_cmf') or {}
            v = blk.get('value')
            s = blk.get('streak_bars')
            if v is None or s is None:
                return None
            return float(s) if v >= 0 else -float(s)
        # CVD proxy
        if metric_name == 'futures.cvd_4h':
            return (fut.get('m5_cvd_proxy') or {}).get('cvd_4h')
        if metric_name == 'futures.cvd_last':
            return (fut.get('m5_cvd_proxy') or {}).get('cvd_last_bar')
        if metric_name == 'spot.cvd_4h':
            return (spot.get('m5_cvd_proxy') or {}).get('cvd_4h')
        # Volume burst zscore
        if metric_name == 'futures.vol_z':
            return (fut.get('m5_volume_burst') or {}).get('zscore')
        if metric_name == 'spot.vol_z':
            return (spot.get('m5_volume_burst') or {}).get('zscore')
        # OBV H1
        if metric_name == 'spot.obv_h1_4h':
            return (spot.get('h1_obv') or {}).get('change_4h')
        # Spread spot↔futures
        if metric_name == 'spread_usd':
            blk = fp.get('spread_spot_futures') or {}
            return blk.get('spread_usd')
        # Approach state (per zona)
        if metric_name == 'approach.signal_strength':
            asn = approach_state_now or {}
            return asn.get('signal_strength')
        if metric_name == 'approach.delta_acc':
            asn = approach_state_now or {}
            return asn.get('delta_acc')
    except Exception:
        return None
    return None


def _eval_one_auto_close(cond, bars_m5, bars_m15, last_m5_close_ts, last_m15_close_ts,
                          flow_proxy_now, approach_state_now, current_price):
    """Avalua UNA condició. Retorna True si dispara, False altrament.

    Per a kind=bar_close: dues vies de fire:
    A) TICK-BASED SAFETY (per FULL_CLOSE): preu > level + buffer durant ≥30s → fire
       Aquest és el SL real. NO espera M5 close, NO mira vol.
    B) BAR_CLOSE (per FULL_CLOSE i altres): M5/M15 tanca beyond level → fire
       Per FULL_CLOSE: ignora with_vol_ratio_min (vol no és filtre per a SL).
       Per FORCE_REVIEW/PARTIAL_50: respecta with_vol_ratio_min (és judici LLM).
    """
    try:
        kind = cond.get('kind')
        # Ja disparada → no repetim
        if cond.get('fired_at'):
            return False

        action = (cond.get('action') or '').upper()
        is_full_close = (action == 'FULL_CLOSE')

        if kind == 'bar_close':
            tf = cond.get('tf')
            test = cond.get('test')
            level = cond.get('level')
            min_vol = cond.get('with_vol_ratio_min')

            # ── TICK-BASED SAFETY NET (només per FULL_CLOSE) ──
            # 2026-05-06: si l'acció és tancament total (= SL virtual), apliquem
            # un check tick: si el preu actual ha superat el level per un buffer
            # i s'ha sostingut, disparem SENSE esperar bar close ni filtre vol.
            # Aquesta és la salvaguarda contra "M5 tanca above level però vol baix
            # i no fires → preu va $5-10 enllà sense protecció".
            if is_full_close and current_price is not None and level is not None:
                try:
                    BUFFER_USD = 0.5      # filtra wicks instantanis
                    SUSTAINED_S = 30.0    # 30 segons sostinguts
                    breach_now = False
                    if test == 'close_above' or test == 'wick_above':
                        breach_now = float(current_price) > (float(level) + BUFFER_USD)
                    elif test == 'close_below' or test == 'wick_below':
                        breach_now = float(current_price) < (float(level) - BUFFER_USD)
                    # Tracking del primer ts de breach sostingut (al cond mateix)
                    if breach_now:
                        first_ts = cond.get('_tick_breach_first_ts')
                        now_ts = time.time()
                        if first_ts is None:
                            cond['_tick_breach_first_ts'] = now_ts
                        else:
                            elapsed = now_ts - float(first_ts)
                            if elapsed >= SUSTAINED_S:
                                # FIRE — preu sostingut beyond level + buffer
                                return True
                    else:
                        # Reset si torna a estar dins
                        if cond.get('_tick_breach_first_ts') is not None:
                            cond['_tick_breach_first_ts'] = None
                except Exception:
                    pass

            # ── BAR-CLOSE (logic original, però sense vol filter per FULL_CLOSE) ──
            if tf == 'M5':
                bars = bars_m5 or []
                last_close_ts_attr = last_m5_close_ts
            elif tf == 'M15':
                bars = bars_m15 or []
                last_close_ts_attr = last_m15_close_ts
            else:
                return False
            if not bars:
                return False
            last = bars[-1]
            ts = float(last.get('time') or 0)
            # Només avaluem en NOVA barra tancada (no la que s'està formant)
            if ts <= last_close_ts_attr:
                return False
            close_p = float(last.get('close') or 0)
            high_p = float(last.get('high') or 0)
            low_p = float(last.get('low') or 0)
            ok = False
            if test == 'close_above':
                ok = close_p > level
            elif test == 'close_below':
                ok = close_p < level
            elif test == 'wick_above':
                ok = high_p > level
            elif test == 'wick_below':
                ok = low_p < level
            if not ok:
                return False
            # 2026-05-06: el filtre de vol_ratio_min NOMÉS s'aplica a accions
            # NO-FULL_CLOSE (FORCE_REVIEW, PARTIAL_50). Per a SL (FULL_CLOSE),
            # vol filter és contraproduent — la regla "vol baix però preu beyond"
            # és exactament un setup que ja ha fallat i hem de sortir.
            if min_vol is not None and not is_full_close:
                v_ratio = vol_ratio(bars) if 'vol_ratio' in globals() else None
                if v_ratio is None or v_ratio < min_vol:
                    return False
            return True

        elif kind == 'metric':
            metric = cond.get('metric')
            test = cond.get('test')
            level = cond.get('level')
            val = _resolve_metric(metric, flow_proxy_now, approach_state_now)
            if val is None:
                return False
            try:
                val_f = float(val)
            except (TypeError, ValueError):
                return False
            if test == 'above':
                return val_f > level
            if test == 'below':
                return val_f < level
            # crosses_above/below requeriria estat anterior — simplifiquem
            # tractant-los com above/below per ara. Si cal estat, afegim cache.
            if test == 'crosses_above':
                return val_f > level
            if test == 'crosses_below':
                return val_f < level
            return False

        elif kind == 'tick':
            test = cond.get('test')
            level = cond.get('level')
            if current_price is None:
                return False
            if test == 'above':
                return current_price > level
            if test == 'below':
                return current_price < level
            return False
    except Exception:
        return False
    return False


def _evaluate_auto_close_conditions(conds, bars_cache, account, sig_state,
                                      flow_proxy_now, approach_state_now,
                                      last_m5_close_ts, last_m15_close_ts):
    """Avalua totes les condicions actives del trade i retorna les disparades.

    Cada condició disparada inclourà la seva action ('FULL_CLOSE' / 'PARTIAL_50'
    / 'FORCE_REVIEW'). El caller s'encarrega d'executar l'action i marcar
    `fired_at` (per evitar re-disparos).
    """
    if not conds:
        return []
    if not isinstance(conds, list):
        return []
    # Preparem M5/M15 si calen
    bars_m5 = None
    bars_m15 = None
    needs_m5 = any(c.get('kind') == 'bar_close' and c.get('tf') == 'M5' for c in conds)
    needs_m15 = any(c.get('kind') == 'bar_close' and c.get('tf') == 'M15' for c in conds)
    if needs_m5 or needs_m15:
        try:
            if needs_m5:
                bars_m5 = aggregate_bars(bars_cache, 5) if bars_cache else []
            if needs_m15:
                bars_m15 = aggregate_bars(bars_cache, 15) if bars_cache else []
        except Exception:
            bars_m5 = bars_m5 or []
            bars_m15 = bars_m15 or []

    cur_price = None
    try:
        if bars_cache:
            cur_price = float(bars_cache[-1].get('close') or 0)
    except Exception:
        cur_price = None

    fired_list = []
    for cond in conds:
        if _eval_one_auto_close(cond, bars_m5, bars_m15,
                                  last_m5_close_ts, last_m15_close_ts,
                                  flow_proxy_now, approach_state_now,
                                  cur_price):
            fired_list.append(cond)
    return fired_list


def _recent_closes_by_zone(lookback_min: int = 240, max_items: int = 6):
    """Llegeix el brain_journal.jsonl i retorna trades tancats recents agrupats
    per nivell aproximat. Dades crues per a l'EXECUTOR — no scoring, no regles.
    L'LLM decideix qualitativament si l'energia del nivell s'ha descarregat.

    Retorna: [
       {"zone_price": 4570.0, "direction": "SELL", "min_ago": 12,
        "pnl_usd": +89.0, "outcome": "win"|"loss", "exit_price": 4561.0,
        "entry_price": 4569.5, "movement_usd": 8.5},
       ...
    ]  ordenat per recencia (més recent primer)
    """
    try:
        path = os.path.join(COMMON, "brain_journal.jsonl")
        if not os.path.exists(path):
            return []
        cutoff = time.time() - (lookback_min * 60)
        # Mantenim mapping trade_id → entry info per fer-lo "join" amb close events
        opens = {}
        closes = []
        with open(str(path), "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                ts = float(d.get("ts") or 0)
                if ts < cutoff:
                    continue
                t = d.get("type") or ""
                tid = d.get("trade_id")
                p = d.get("payload") or {}
                if t == "trade_opened" and tid:
                    opens[tid] = {
                        "ts_open": ts,
                        "entry_price": p.get("price"),
                        "direction": p.get("direction"),
                    }
                elif t in ("trade_closed", "trade_signal_closed") and tid:
                    closes.append({
                        "ts_close": ts,
                        "trade_id": tid,
                        "exit_price": p.get("price"),
                        "pnl": float(p.get("pnl_delta") or 0),
                        "direction": p.get("direction"),
                    })
        # Build report: per close, pair with open (if available)
        items = []
        now = time.time()
        for c in sorted(closes, key=lambda x: x["ts_close"], reverse=True)[:max_items*2]:
            o = opens.get(c["trade_id"]) or {}
            entry = o.get("entry_price")
            exit_ = c["exit_price"]
            direction = c.get("direction") or o.get("direction") or "?"
            # "zone_price" = entry rounded to nearest 0.5 (heurística suau)
            zone = round(float(entry), 1) if entry else None
            mov = None
            if entry and exit_:
                mov = round(abs(float(exit_) - float(entry)), 2)
            items.append({
                "zone_price": zone,
                "direction": direction,
                "min_ago": int((now - c["ts_close"]) / 60),
                "pnl_usd": round(c["pnl"], 2),
                "outcome": "win" if c["pnl"] > 0 else ("loss" if c["pnl"] < 0 else "even"),
                "entry_price": round(float(entry), 2) if entry else None,
                "exit_price": round(float(exit_), 2) if exit_ else None,
                "movement_usd": mov,
            })
            if len(items) >= max_items:
                break
        return items
    except Exception as _e:
        try:
            log.warning(f"[RECENT_CLOSES] failed: {_e}")
        except Exception:
            pass
        return []


def _build_executor_delta(bars, account, trigger_events, sig_state):
    """Compact delta payload for EXECUTOR follow-up turns (Claude session
    --resume). The first turn already provided the full snapshot; this
    delivers only what's CHANGED + the new triggers, ~1-2 KB total.

    The LLM has full state from prior turns cached. We just need:
      - Latest 3-5 M5 bars (NEW since last call)
      - Current price, RSI, ATR, vol_ratio
      - Risk state (DD, lots, equity)
      - Position changes since last call (best-effort summary)
      - New trigger events
      - Active snipers / staged setups (current state — they may have
        fired/expired between calls)
    """
    if not bars:
        return "DELTA: no bars."
    last = bars[-1]
    price = last['close']
    closes = [b['close'] for b in bars]
    cur_rsi = rsi(closes, 14) or 0
    cur_atr = atr(bars, 14) or 0
    cur_vol = vol_ratio(bars)

    # Last 5 M5 bars only (new ones since previous turn)
    last_bars = []
    for b in bars[-5:]:
        last_bars.append({
            'time_utc': datetime.fromtimestamp(b['time'], tz=timezone.utc).strftime('%H:%M'),
            'candle': candle_type(b),
            'o': b['open'], 'h': b['high'], 'l': b['low'], 'c': b['close'], 'v': b['volume'],
        })

    # Position summary (compact)
    positions_compact = []
    total_lots = 0.0
    total_pnl = 0.0
    for p in account.get('positions', []):
        lot = p.get('volume', p.get('lot', 0))
        op = p.get('price_open', p.get('open_price', 0))
        pnl = broker_position_pnl(p)
        total_lots += lot
        total_pnl += pnl
        positions_compact.append({
            "tk": p.get('ticket', 0), "lot": lot, "entry": op, "pnl": round(pnl, 2),
        })

    # Active snipers / staged compact view
    try:
        import snipers as _snp
        snipers_now = [
            {"price": s.get("price"), "direction": s.get("direction"),
             "multiplier": s.get("multiplier"),
             "mins_ago": int((time.time() - float(s.get("placed_at", 0) or 0)) / 60)
                          if s.get("placed_at") else None}
            for s in (_snp.load() or [])
        ]
    except Exception:
        snipers_now = []
    try:
        import staged_setups as _ss
        staged_now = [
            {"id": s.get("id"), "direction": s.get("direction"),
             "zone_price": s.get("zone_price"), "confidence": s.get("confidence")}
            for s in (_ss.load() or [])
        ]
    except Exception:
        staged_now = []

    # Flow proxy compacte per al delta — núm. crus, no text llarg
    flow_compact = None
    try:
        fp = _flow_proxy_dict()
        if fp:
            flow_compact = {}
            spot_block = fp.get("spot") or {}
            fut_block = fp.get("futures") or {}
            if spot_block:
                vb = spot_block.get("m5_volume_burst") or {}
                cm = spot_block.get("m15_cmf") or {}
                ob = spot_block.get("h1_obv") or {}
                cv = spot_block.get("m5_cvd_proxy") or {}
                flow_compact["spot"] = {
                    "vol_pct_6h": vb.get("pct_vs_6h_avg"),
                    "vol_z": vb.get("zscore"),
                    "cmf_m15": cm.get("value"),
                    "cmf_streak": cm.get("streak_bars"),
                    "obv_h1_4h": ob.get("change_4h"),
                    "obv_div_48h": ob.get("divergence_48h"),
                    "cvd_4h": cv.get("cvd_4h"),
                    "cvd_last": cv.get("cvd_last_bar"),
                    "bull_bars_1h_pct": cv.get("bullish_bar_ratio_1h"),
                }
            if fut_block:
                vb = fut_block.get("m5_volume_burst") or {}
                cm = fut_block.get("m15_cmf") or {}
                ob = fut_block.get("h1_obv") or {}
                cv = fut_block.get("m5_cvd_proxy") or {}
                flow_compact["futures"] = {
                    "contracts_last": vb.get("contracts_last_bar"),
                    "vol_pct_6h": vb.get("pct_vs_6h_avg"),
                    "vol_z": vb.get("zscore"),
                    "cmf_m15": cm.get("value"),
                    "cmf_streak": cm.get("streak_bars"),
                    "obv_h1_4h": ob.get("change_4h"),
                    "obv_div_48h": ob.get("divergence_48h"),
                    "cvd_4h": cv.get("cvd_4h"),
                    "cvd_last": cv.get("cvd_last_bar"),
                    "bull_bars_1h_pct": cv.get("bullish_bar_ratio_1h"),
                }
            sp = fp.get("spread_spot_futures") or {}
            if sp:
                flow_compact["spread_usd"] = sp.get("spread_usd")
    except Exception:
        flow_compact = None

    delta = {
        "turn_type": "delta",
        "note": ("Continuació de la conversa. Tens el context complet del trade "
                 "des dels turns anteriors (zones, multi-TF, market_context, etc). "
                 "Aquí només arriba el que ha canviat des de l'última crida."),
        "elapsed_since_open": None,
        "timestamp_utc": datetime.now(timezone.utc).strftime('%H:%M:%S'),
        "trigger_events_new": trigger_events or [],
        "market_context_now": {
            "price": price,
            "rsi_14": round(cur_rsi, 2),
            "atr_14": round(cur_atr, 2),
            "volume_ratio": cur_vol,
            "last_candle_type": candle_type(last),
            "last_5_m5_bars": last_bars,
        },
        # Flux dual-feed snapshot per a la decisió: spot + futures + spread.
        # Compact (núm. crus, no text). Buit si flow_proxy ha fallat.
        "flow_proxy_now": flow_compact,
        "signal": {
            "direction": account.get('direction'),
            "total_lots": round(total_lots, 3),
            "total_pnl_unrealized": round(total_pnl, 2),
            "breakeven_set": bool(sig_state.get('breakeven_set')) if sig_state and hasattr(sig_state, 'get') else False,
            "breakeven_pending": bool(sig_state.get('breakeven_pending')) if sig_state and hasattr(sig_state, 'get') else False,
            "flag_closing": bool(account.get('closing')),
        },
        "positions": positions_compact,
        "risk": {
            "balance": float(account.get('balance', 0) or 0),
            "equity": float(account.get('equity', 0) or 0),
            "dd_pct": account.get('dd_pct'),
            "dd_remaining_usd": None,  # filled below if available
        },
        "active_snipers": snipers_now,
        "staged_setups_armed": staged_now,
        # 2026-05-04: trades tancats recents per a "energia descarregada per nivell"
        "recent_closes_by_zone": _recent_closes_by_zone(),
        # 2026-05-04: zones blacklistades temporalment (no es poden re-stagear).
        # L'EXECUTOR ha de RAONAR amb això: si proposa una zona blacklistada,
        # serà rebutjada silenciosament. Mira el camp i evita-les o espera el TTL.
        "staged_blacklist": (lambda: __import__('staged_setups').get_active_blacklist())(),
    }

    # Explosion state — al delta també (estava només al full payload).
    # Crucial perquè quan el detector marca explosion, l'EXECUTOR ha de saber-ho.
    try:
        import explosion_detector
        _expl = explosion_detector.last_state()
        delta["explosion_state"] = _expl if _expl else {"active": False}
    except Exception:
        delta["explosion_state"] = {"active": False}

    # Add elapsed since trade open if known
    try:
        if sig_state and hasattr(sig_state, 'get'):
            opened_ts = float(sig_state.get('opened_ts', 0) or 0)
            if opened_ts > 0:
                delta["elapsed_since_open"] = f"{int((time.time() - opened_ts) / 60)}min"
    except Exception:
        pass

    # Approach states (NEW): inclou per cada zona en APPROACH/AT_ZONE el seu
    # state amb delta institucional acumulat + signal_strength.
    try:
        _at = _get_approach_tracker()
        if _at is not None:
            _approach_data = _at.get_payload_dict()
            if _approach_data:
                delta["approach_states"] = _approach_data
    except Exception:
        pass

    # NEW 2026-05-04 v2: incloem asymmetric_risk + directional_commitment al
    # delta payload perquè el cache de Claude no els tenia (s'han afegit
    # avui). Si el cache és vell, així el LLM els rep igualment.
    try:
        from zone_store import read_state as _rs_d
        _zd = _rs_d(COMMON)
        _ar_d = _zd.get('asymmetric_risk') if isinstance(_zd.get('asymmetric_risk'), dict) else None
        _dc_d = _zd.get('directional_commitment') if isinstance(_zd.get('directional_commitment'), dict) else None
        if _ar_d:
            delta["asymmetric_risk"] = _ar_d
        if _dc_d:
            delta["directional_commitment_now"] = _dc_d
    except Exception:
        pass

    header = (
        "═══ CICLE EXECUTOR — DELTA ═══\n"
        "Continua el raonament del trade des del turn anterior. Tens TOT el "
        "context (zones, history bars, market_context, sizing) cachejat. "
        "Aquí només són les dades NOVES del moment actual.\n\n"
    )
    # Snapshot per al delta path (no només full path) — necessari per a
    # post-mortem i debug. El payload aquí és el `delta`, no el `payload` ple.
    try:
        _trade_id_snap_d = None
        if sig_state is not None and hasattr(sig_state, 'get_trade_id'):
            _trade_id_snap_d = sig_state.get_trade_id()
        if not _trade_id_snap_d and account.get('has_signal'):
            _trade_id_snap_d = f"{account.get('direction','?')}_{account.get('entry_price',0)}"
        append_executor_snapshot({
            "ts": time.time(),
            "iso": datetime.now(timezone.utc).isoformat(),
            "trade_id": _trade_id_snap_d,
            "trigger_events_types": [e.get('type') for e in (trigger_events or [])],
            "delta_mode": True,
            "payload": delta,
        })
    except Exception:
        pass
    return header + json.dumps(delta, ensure_ascii=False, indent=2, default=str)


def build_executor_prompt(bars, account, trigger_events=None, sig_state=None):
    """Build data payload for EXECUTOR brain.

    Output is a JSON-structured payload preceded by a short Catalan header
    so the current prompt (which expects a descriptive context) can still
    parse it. A later PR will adapt the EXECUTOR prompt to consume the
    JSON schema directly.

    Multi-turn cost optimisation: when EXECUTOR is on Claude with sessions
    AND it's not the first turn of the trade, return a compact DELTA
    payload instead of the full snapshot. Saves ~30× quota per call.
    """
    if not bars:
        return None

    # Decide full vs delta based on session state. Only Claude-with-session
    # benefits — DeepSeek path always sends full (its prefix-cache works
    # better with stable user-msg leading bytes than with multi-turn).
    try:
        import claude_session_manager as _csm
        # Only use delta if this Claude session has had a successful first turn
        _is_first = _csm.is_first_turn('EXECUTOR')
        # Also check that the role is actually configured for Claude — if
        # it's on DeepSeek the session won't be used and delta would lose
        # information. Read config.
        _cfg = _get_llm_config('executor') if '_get_llm_config' in globals() else {}
        _provider = (_cfg.get('provider') or '').lower()
        _use_delta = (_provider == 'claude') and (not _is_first)
    except Exception:
        _use_delta = False

    if _use_delta:
        return _build_executor_delta(bars, account, trigger_events, sig_state)
    # else fall through to full snapshot below

    closes = [b['close'] for b in bars]
    last = bars[-1]
    price = last['close']

    # Load zones via zone_store (legacy view); also grab structured zones + regime/coverage
    regime = None
    coverage = None
    coverage_gap = None
    # New fields from Indicator (2026-05-02): working_range + directional_commitment
    working_range = None
    directional_commitment = None
    asymmetric_risk = None  # 2026-05-04 v2
    try:
        from zone_store import read_state, active_zones
        _zone_state = read_state(COMMON)
        zones_structured = active_zones(_zone_state)
        bias = _zone_state.get('bias', 'NEUTRAL')
        context = _zone_state.get('context', '')
        regime = _zone_state.get('regime')
        coverage = _zone_state.get('coverage')
        coverage_gap = _zone_state.get('coverage_gap')
        working_range = _zone_state.get('working_range') if isinstance(_zone_state.get('working_range'), dict) else None
        directional_commitment = _zone_state.get('directional_commitment') if isinstance(_zone_state.get('directional_commitment'), dict) else None
        asymmetric_risk = _zone_state.get('asymmetric_risk') if isinstance(_zone_state.get('asymmetric_risk'), dict) else None
    except Exception:
        zones_structured = []
        zones_data = load_zones()
        zones_structured = [
            {
                "price": z.get('price'),
                "type": z.get('type'),
                "strength": z.get('strength'),
                "bounce_direction": z.get('bounce_direction', ''),
                "condition": z.get('condition', ''),
            }
            for z in zones_data.get('reversal_zones', [])
        ]
        bias = zones_data.get('bias', 'NEUTRAL')
        context = zones_data.get('context', '')

    total_lots = 0.0
    total_pnl = 0.0
    w_entry_num = 0.0
    positions = []
    for p in account.get('positions', []):
        lot = p.get('volume', p.get('lot', 0))
        op = p.get('price_open', p.get('open_price', 0))
        pnl = broker_position_pnl(p)
        ticket = p.get('ticket', 0)
        total_lots += lot
        total_pnl += pnl
        w_entry_num += op * lot
        positions.append({"ticket": ticket, "lot": lot, "entry": op, "pnl": pnl})
    w_entry = round(w_entry_num / total_lots, 2) if total_lots > 0 else None

    # Nearest zones above / below for quick reference
    supports = sorted(
        [z for z in zones_structured if z.get('price', 0) < price],
        key=lambda z: z['price'], reverse=True,
    )
    resistances = sorted(
        [z for z in zones_structured if z.get('price', 0) >= price],
        key=lambda z: z['price'],
    )

    cur_rsi = rsi(closes, 14) or 0
    cur_atr = atr(bars, 14) or 0
    cur_vol = vol_ratio(bars)

    # ── Sizing + risk config for dd_projection and trade_context ──
    _cfg = _load_app_config()
    sizing_cfg = _cfg.get('sizing', {}) or {}
    risk_cfg = _cfg.get('risk_control', {}) or {}
    exits_cfg = _cfg.get('exits', {}) or {}
    instr_cfg = _cfg.get('instrument', {}) or {}
    base_lot = float(sizing_cfg.get('base_lot', 0.03))
    max_mult = int(sizing_cfg.get('max_multiplier', 5))
    dd_hard_pct = float(risk_cfg.get('dd_hard_pct', BRAIN_DD_LIMIT_PCT))
    cvu = float(instr_cfg.get('contract_value_per_usd', 100.0))
    hint_1r = float(exits_cfg.get('hint_first_partial_r', 1.0))
    hint_2r = float(exits_cfg.get('hint_second_partial_r', 2.0))
    rr_hints_on = bool(exits_cfg.get('enable_rr_hints', True))

    # Determine trade_id for previous_state lookup
    trade_id = None
    if sig_state is not None:
        try:
            # v3.3: prefer FSM-persisted UUID over legacy derived id
            if hasattr(sig_state, 'get_trade_id'):
                trade_id = sig_state.get_trade_id()
            else:
                trade_id = sig_state.get('id') if hasattr(sig_state, 'get') else None
        except Exception:
            trade_id = None
    if not trade_id and account.get('has_signal'):
        # Fallback only if FSM lost its id (e.g. pre-v3.3 state file)
        trade_id = f"{account.get('direction','?')}_{account.get('entry_price',0)}"

    previous_row = read_last_executor_decision(trade_id)
    previous_state = None
    if previous_row:
        previous_state = {
            "last_action": previous_row.get('action'),
            "last_thesis": previous_row.get('thesis'),
            "last_invalidation_condition": previous_row.get('invalidation_condition'),
            "last_decision_ts": previous_row.get('ts'),
            "last_mental_state": previous_row.get('mental_state'),
        }

    # M5 last 30 bars (2.5h of context) — enough to see where price has travelled
    # since the trade opened, including any zones already touched/averaged.
    m5_last = [
        {
            "time_utc": datetime.fromtimestamp(b['time'], tz=timezone.utc).strftime('%H:%M'),
            "candle": candle_type(b),
            "o": b['open'], "h": b['high'], "l": b['low'], "c": b['close'], "v": b['volume'],
        }
        for b in bars[-30:]
    ]

    # Price extremes since trade opened — the LLM needs to know "price ALREADY
    # touched 4826 30 min ago and we averaged there", otherwise it treats a
    # retest like a first-visit.
    price_extremes = None
    try:
        if sig_state and hasattr(sig_state, 'get'):
            opened_ts = sig_state.get('opened_ts', 0) or 0
        else:
            opened_ts = 0
        if opened_ts > 0:
            bars_since_open = [b for b in bars if b.get('time', 0) >= opened_ts]
            if bars_since_open:
                highs = [b['high'] for b in bars_since_open]
                lows = [b['low'] for b in bars_since_open]
                price_extremes = {
                    "high_since_open": round(max(highs), 2),
                    "low_since_open": round(min(lows), 2),
                    "bars_since_open": len(bars_since_open),
                    "high_age_bars": len(bars_since_open) - 1 - highs.index(max(highs)),
                    "low_age_bars": len(bars_since_open) - 1 - lows.index(min(lows)),
                }
    except Exception:
        pass

    # Averaging history — explicit list of WHERE and WHEN each AVG was done,
    # so the LLM doesn't have to reverse-engineer it from the positions array.
    averaging_history = []
    try:
        if sig_state and hasattr(sig_state, 'get'):
            zs_avg = sig_state.get('zones_averaged') or []
            now = time.time()
            for z in zs_avg:
                averaging_history.append({
                    "price": round(float(z.get('price', 0) or 0), 2),
                    "lot": float(z.get('lot', 0) or 0),
                    "mins_ago": int((now - (z.get('ts', 0) or 0)) / 60),
                })
    except Exception:
        pass

    # ── dd_projection: for each multiplier 1..max, the price where the
    # 3.5% safety net would trigger if that averaging were opened now. ──
    dd_projection = []
    next_adverse = None
    trade_context = None
    direction = account.get('direction')
    balance = float(account.get('balance', 0) or 0)
    dd_used_usd = float(account.get('dd_used', 0) or 0)
    dd_limit_usd = balance * (dd_hard_pct / 100.0) if balance > 0 else 0.0
    if account.get('has_signal') and direction in ('BUY', 'SELL') and balance > 0:
        try:
            from risk import build_dd_projection, find_next_adverse_zone
            dd_projection = build_dd_projection(
                current_price=price,
                direction=direction,
                current_total_lots=total_lots,
                base_lot=base_lot,
                max_multiplier=max_mult,
                dd_usd_used=dd_used_usd,
                dd_limit_usd=dd_limit_usd,
                contract_value_per_usd=cvu,
            )
            next_adverse = find_next_adverse_zone(
                current_price=price,
                direction=direction,
                zones=zones_structured,
                min_strength_rank=1,  # MODERATE or better
            )
        except Exception as _e:
            log.warning(f"[EXECUTOR_PROMPT] dd_projection failed: {_e}")

        # ── trade_context: R-multiple and partial hints for profit capture ──
        # R-unit is scaled by session_factor — Asia compresses targets, OVERLAP
        # stretches them, dead hours (Tokyo lunch / NY late) damp them further.
        # See news_state.SESSION_FACTORS for current calibration based on the
        # 2026-04-25 session_anatomy study.
        if rr_hints_on and w_entry and cur_atr > 0:
            try:
                _sess_factor = float(news_state.session_factor())
            except Exception:
                _sess_factor = 1.0
            r_unit_base = cur_atr * 2.0  # baseline: 1R = 2 ATR adverse
            r_unit = r_unit_base * _sess_factor
            if direction == 'BUY':
                profit_price = price - w_entry
                at_1r = w_entry + hint_1r * r_unit
                at_2r = w_entry + hint_2r * r_unit
            else:
                profit_price = w_entry - price
                at_1r = w_entry - hint_1r * r_unit
                at_2r = w_entry - hint_2r * r_unit
            r_mult = round(profit_price / r_unit, 2) if r_unit > 0 else 0
            try:
                _sess_ctx = news_state.session_context()
            except Exception:
                _sess_ctx = {"label": "?", "r_factor": 1.0}
            trade_context = {
                "weighted_entry": w_entry,
                "current_price": price,
                "atr_m5": round(cur_atr, 2),
                "r_unit_usd": round(r_unit, 2),
                "r_unit_base_usd": round(r_unit_base, 2),
                "session_r_factor": round(_sess_factor, 2),
                "session": _sess_ctx,
                "current_r_multiple": r_mult,
                "partial_hints": {
                    "at_1r": round(at_1r, 2),
                    "at_2r": round(at_2r, 2),
                },
            }

    # Read explosion state (last evaluated by main loop). Cheap file read.
    try:
        import explosion_detector
        _explosion = explosion_detector.last_state()
    except Exception:
        _explosion = {}

    # ── KEY ORDER FOR PROMPT CACHE EFFICIENCY ──
    # Anthropic + DeepSeek both cache by leading-token hash. Anything that
    # changes call-to-call breaks the cache from that point on. Therefore:
    #   1. STABLE keys first  (sizing, instrument constants — never change)
    #   2. SEMI-STABLE next   (zones — change every few minutes)
    #   3. VOLATILE last      (market data, signal, positions — change every tick)
    # JSON key order is preserved by Python dicts; the LLM doesn't care.
    payload = {
        # ── STABLE (cached for hours) ──
        "sizing": {
            "base_lot": base_lot,
            "max_multiplier": max_mult,
        },
        # ── SEMI-STABLE (cached for minutes; zones change when Indicator runs) ──
        "zones": {
            "bias": bias,
            "regime": regime,
            "context": context,
            "coverage": coverage,
            "coverage_gap": coverage_gap,
            "nearest_support": supports[0] if supports else None,
            "nearest_resistance": resistances[0] if resistances else None,
            "all_active": zones_structured,
            # New 2026-05-02: Indicator's institutional-thinking outputs.
            # working_range = the structural box where price operates now.
            # directional_commitment = today's risk-asymmetric bias.
            # Both are LLM-judged narratives — read them and reason with them.
            "working_range": working_range,
            "directional_commitment": directional_commitment,
            # NEW 2026-05-04 v2: asymmetric_risk separates BULL_SQUEEZE risk
            # from BEAR_CONTINUATION risk independently. Use to calibrate
            # setup sizing/caution beyond just directional bias.
            "asymmetric_risk": asymmetric_risk,
        },
        # ── VOLATILE (per-tick) ──
        "market_context": {
            "timestamp_utc": datetime.now(timezone.utc).strftime('%H:%M:%S'),
            "price": price,
            "rsi_14": round(cur_rsi, 2),
            "atr_14": round(cur_atr, 2),
            "volume_ratio": cur_vol,
            "last_candle_type": candle_type(last),
            "last_30_m5_candles": m5_last,
            "price_extremes_since_open": price_extremes,
        },
        "explosion_state": _explosion if _explosion else {"active": False},
        "signal": {
            "direction": direction,
            "channel": (sig_state.get('channel') if sig_state and hasattr(sig_state, 'get') else None),
            "entry_price": account.get('entry_price'),
            "weighted_entry": w_entry,
            "total_lots": round(total_lots, 3),
            "breakeven_set": bool(sig_state.get('breakeven_set')) if sig_state and hasattr(sig_state, 'get') else False,
            "breakeven_pending": bool(sig_state.get('breakeven_pending')) if sig_state and hasattr(sig_state, 'get') else False,
            "flag_closing": bool(account.get('closing')),
            "trade_id": trade_id,
            # Explicit averaging history so the LLM doesn't have to reverse-engineer
            # from positions where/when it already averaged. Crucial for "has the
            # price already touched this zone?" reasoning.
            "averaging_history": averaging_history,
            "avg_count": len(averaging_history),
        },
        "positions": positions,
        "risk": {
            "balance": balance,
            "equity": account.get('equity'),
            "dd_usd": round(dd_used_usd, 2),
            "dd_pct": account.get('dd_pct'),
            "dd_hard_pct": dd_hard_pct,
            "dd_remaining_usd": round(dd_limit_usd - dd_used_usd, 2) if dd_limit_usd else None,
            "dd_projection": dd_projection,
            "next_adverse_zone": next_adverse,
        },
        "trade_context": trade_context,
        "previous_state": previous_state,
        "trigger_events": trigger_events or [],
        # ── 2026-05-04: Trades tancats recents (per zona) ──
        # Dades crues — l'EXECUTOR ha de raonar qualitativament si l'energia del
        # nivell s'ha descarregat o no, i si és viable un nou trade. Veure prompt.
        "recent_closes_by_zone": _recent_closes_by_zone(),
        # 2026-05-04: zones blacklistades. Si proposes una d'aquestes, es
        # rebutjarà silenciosament — has d'esperar TTL (30 min) o triar-ne una altra.
        "staged_blacklist": (lambda: __import__('staged_setups').get_active_blacklist())(),
    }

    # ── Active snipers (pre-placed avg triggers from a previous Executor cycle) ──
    # The Executor must know what's already armed so it doesn't re-propose the
    # same level or argue against one of its own standing orders. Each cycle the
    # Executor REPLACES the sniper list with whatever it outputs this round —
    # so effectively this field is a "last cycle's pre-commitments" view.
    try:
        import snipers as _snp
        _snipes = [
            {
                "price": s.get("price"),
                "direction": s.get("direction"),
                "multiplier": s.get("multiplier"),
                "mins_ago": int((time.time() - float(s.get("placed_at", 0) or 0)) / 60)
                            if s.get("placed_at") else None,
                "reason": (s.get("reason") or "")[:200],
            }
            for s in (_snp.load() or [])
        ]
        payload["active_snipers"] = _snipes
    except Exception:
        payload["active_snipers"] = []

    # ── Staged setups armed (full plans waiting for trigger) ──
    # Setups proposed by Executor or Hunter — full thesis + profit_targets +
    # invalidation. They fire reflexively (FastEngine) when price + candle +
    # vol confirm. Without this in the payload, the Executor forgets its own
    # armed plans and reasons inconsistently with what's pending.
    try:
        import staged_setups as _ss
        _ss_now = _ss.load() or []
        # Compact view — keep the fields the LLM needs to reason, drop verbose ones.
        _ss_compact = []
        for s in _ss_now:
            _staged_at = s.get("staged_at") or 0
            _ss_compact.append({
                "id": s.get("id"),
                "direction": s.get("direction"),
                "zone_price": s.get("zone_price"),
                "confidence": s.get("confidence"),
                "lot_multiplier": s.get("lot_multiplier", 1),
                "play_type": s.get("play_type"),
                "source": s.get("source"),
                "thesis": (s.get("thesis") or "")[:180],
                "invalidation_price": s.get("invalidation_price"),
                "expiration_minutes": s.get("expiration_minutes"),
                "mins_armed_ago": int((time.time() - float(_staged_at)) / 60)
                                  if _staged_at else None,
                "averaging_zones": s.get("averaging_zones") or [],
                "profit_targets_count": len(s.get("profit_targets") or []),
            })
        payload["staged_setups_armed"] = _ss_compact
    except Exception:
        payload["staged_setups_armed"] = []

    # ── Market context enrichment (external DXY/10Y + session + structure) ──
    # Non-blocking: if anything fails we just omit the section. LLM handles missing keys.
    try:
        import market_context as _mc
        _mc_ctx = _mc.build_market_context(
            bars_m5=bars,
            account=account,
            tv_helper=tv,
            now_utc=datetime.now(timezone.utc),
            for_executor=True,
            atr_m5=cur_atr,
            config=_cfg,
        )
        payload["external"] = _mc_ctx.get("external")
        payload["htf"] = _mc_ctx.get("htf")
        payload["market_state"] = _mc_ctx.get("market_state")
    except Exception as _mc_err:
        log.warning(f"[EXECUTOR_PROMPT] market_context failed: {_mc_err}")
        payload["external"] = None
        payload["market_state"] = None

    # ── Rich context (volume profile spot + futures + liquidity + technical) ──
    # Same source as Indicator, subset relevant for tactical trade management.
    # NEW: passem gc_m5 perquè l'Executor també vegi el Volume Profile FUTURES
    # — Naked POCs institucionals són imants per a targets i decisions d'avg.
    try:
        import indicator_context as _ic
        gc_m5_exec = _fetch_gc_bars("gc_m5", "5", 288, cache_seconds=120)
        payload["rich_context"] = _ic.build_for_executor(bars, tv_helper=tv, gc_m5=gc_m5_exec)
    except Exception as _rc_err:
        log.warning(f"[EXECUTOR_PROMPT] rich_context failed: {_rc_err}")
        payload["rich_context"] = ""

    # ── Flow proxy dual-feed: spot tick-volume + futures contracts + spread + CVD ──
    # NEW: l'Executor també rep el flux raw per decidir averaging/partial/BE
    # amb context institucional. Filosofia: dades crues, l'LLM les interpreta
    # qualitativament al pensament d'execució.
    try:
        flow_data = _flow_proxy_dict()
        if flow_data:
            payload["flow_proxy"] = flow_data
    except Exception as _fp_err:
        log.warning(f"[EXECUTOR_PROMPT] flow_proxy failed: {_fp_err}")

    # ── Approach states per zona (NEW 2026-05-03) ──
    # Per cada zona en APPROACH/AT_ZONE, l'estat acumulat de delta + vol
    # institucional. L'EXECUTOR pot raonar amb això per gestió tàctica
    # (partial, BE, snipers) i validar la coherència del flux amb la tesi.
    try:
        _at = _get_approach_tracker()
        if _at is not None:
            _approach_data = _at.get_payload_dict()
            if _approach_data:
                payload["approach_states"] = _approach_data
    except Exception as _at_err:
        log.warning(f"[EXECUTOR_PROMPT] approach_states failed: {_at_err}")

    # Stable preamble at the TOP — same on every call so prompt cache hits
    # the maximum prefix. The volatile timestamp/events summary is appended
    # at the END as `_call_meta` (LLM still sees it; just last so it doesn't
    # poison the cache for the rest).
    header = (
        "═══ CICLE EXECUTOR — dades estructurades ═══\n"
        "Context complet com a JSON (claus ordenades de més estable a més volàtil "
        "per maximitzar prompt-cache):\n"
    )
    payload["_call_meta"] = {
        "timestamp_utc": datetime.now(timezone.utc).strftime('%H:%M:%S'),
        "price": price,
        "dd_pct": account.get('dd_pct', 0),
        "total_lots": round(total_lots, 2),
        "events": [e.get('type') for e in (trigger_events or [])] or ['(cap — tick)'],
    }

    # Phase A — Rich context snapshot for post-mortem.
    # Captured AT PROMPT-BUILD TIME so we preserve exactly what the LLM saw
    # when deciding. Joined with the matching executor_decision later by
    # (trade_id, ts proximity). Best-effort: silent on any failure so the
    # trading path never blocks.
    try:
        _trade_id_snap = None
        if sig_state is not None and hasattr(sig_state, 'get_trade_id'):
            _trade_id_snap = sig_state.get_trade_id()
        if not _trade_id_snap and account.get('has_signal'):
            _trade_id_snap = f"{account.get('direction','?')}_{account.get('entry_price',0)}"
        append_executor_snapshot({
            "ts": time.time(),
            "iso": datetime.now(timezone.utc).isoformat(),
            "trade_id": _trade_id_snap,
            "trigger_events_types": [e.get('type') for e in (trigger_events or [])],
            "payload": payload,
        })
    except Exception:
        pass

    return header + json.dumps(payload, ensure_ascii=False, indent=2)


_LLM_INFLIGHT_FILE = os.path.join(COMMON, 'brain_llm_inflight.json')

def _llm_inflight_set(label: str, calling: bool, provider: str = '', model: str = ''):
    """Track which LLM roles are currently calling. The dashboard reads this
    to backlight the corresponding tab while the request is in flight.

    `label` is uppercased ('INDICATOR', 'EXECUTOR', 'HUNTER', 'ZONE_REVIEWER',
    'INTERPRETER', etc.). State persists across calls so the dashboard sees a
    coherent in-flight map. Best-effort — never crash the LLM call."""
    try:
        try:
            with open(_LLM_INFLIGHT_FILE, 'r', encoding='utf-8') as _f:
                _state = json.load(_f) or {}
        except Exception:
            _state = {}
        key = (label or '').lower()
        if calling:
            _state[key] = {
                'calling': True,
                'started_ts': time.time(),
                'provider': provider,
                'model': model,
            }
        else:
            # Mark ended; keep last duration so dashboard can show "X·last 90s".
            prev = _state.get(key) or {}
            started = float(prev.get('started_ts') or 0)
            duration = (time.time() - started) if started else 0
            _state[key] = {
                'calling': False,
                'last_ended_ts': time.time(),
                'last_duration_s': round(duration, 1),
                'provider': prev.get('provider', provider),
                'model': prev.get('model', model),
            }
        with open(_LLM_INFLIGHT_FILE, 'w', encoding='utf-8') as _f:
            json.dump(_state, _f, indent=2)
    except Exception:
        pass


def _call_deepseek(prompt_data, system_prompt, label="brain", reasoning=False, model=None,
                   conversation_role=None):
    """Call DeepSeek API via HTTP. Returns parsed JSON dict or None.

    reasoning=True uses deepseek-reasoner (chain-of-thought, slower, better for complex decisions).
    reasoning=False uses deepseek-chat (faster, cheaper, structured tasks).

    `model` overrides the default selection. Use for cost-optimised roles:
        - "deepseek-v4-pro"   (default — best quality, promo $0.435/$0.87 per MTok until 2026-05-31)
        - "deepseek-v4-flash" (3× cheaper than promo Pro, 12× cheaper than post-promo Pro)
                              Use for non-critical analytical roles (POSTMORTEM, INTERPRETER).

    Prompt caching: DeepSeek fa prefix caching automàtic sobre el primer missatge
    del cos (incloent el `system`). Mantenim el system_prompt com a constant per
    garantir hit; les dades variables van al `user`. El response retorna
    `usage.prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` per verificar.
    No cal injectar cap camp `cache_control` — no està al schema de l'API.
    """
    if not DEEPSEEK_API_KEY:
        log.warning(f"[{label}] DEEPSEEK_API_KEY not set — falling back to Claude")
        return _call_claude(prompt_data, system_prompt, label, model="sonnet")

    import urllib.request, urllib.error
    # V4 (active since 2026-04-24): default policy is "always V4-Pro when not
    # using Claude" — quality first. V4-Pro has 75% promo discount until
    # 2026-05-31. Per-call override via `model=` lets cost-sensitive roles
    # (postmortem analysis, simple parsing) use V4-Flash for ~3× savings.
    # Old IDs (deepseek-reasoner / deepseek-chat) deprecated 2026-07-24.
    #
    # Pro returns `reasoning_content` separate from `content`; the parser below
    # already handles both. The body schema differs between reasoning/non-
    # reasoning callers: reasoning needs huge max_tokens (CoT shares budget),
    # non-reasoning still wants response_format=json_object (Pro honors it).
    model_id = model or "deepseek-v4-pro"

    # ── Build messages array — single-turn or multi-turn (cache-optimised) ──
    # Multi-turn mode (conversation_role set): prepend the rolling history
    # so DeepSeek's prefix-cache can re-read prior turns at ~120× discount.
    # The caller is expected to pass `prompt_data` as a SMALL DELTA when
    # there's prior history; sending full state each turn would defeat the
    # cache benefit (would COST more than single-turn).
    _conv_history = []
    if conversation_role:
        try:
            import llm_conversation as _lc
            _conv_history = _lc.load(conversation_role)
        except Exception as _ce:
            log.warning(f"[{label}] conversation load failed: {_ce}")
            _conv_history = []

    messages = [{"role": "system", "content": system_prompt}]
    for _m in _conv_history:
        if _m.get("role") in ("user", "assistant") and _m.get("content"):
            messages.append({"role": _m["role"], "content": _m["content"]})
    messages.append({"role": "user", "content": prompt_data})

    if reasoning:
        body_dict = {
            "model": model_id,
            "messages": messages,
            "max_tokens": 16000,  # CoT can use 1.5-5k; leave room for full answer
        }
    else:
        body_dict = {
            "model": model_id,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 4000,  # bumped — Pro thinks even for json_object tasks
            "response_format": {"type": "json_object"},
        }
    body = json.dumps(body_dict).encode('utf-8')

    req = urllib.request.Request(
        DEEPSEEK_URL, data=body,
        headers={
            'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
            'Content-Type': 'application/json',
        }
    )
    try:
        log.info(f"[{label}] Calling DeepSeek ({model_id}, prompt={len(prompt_data)} chars)...")
        _llm_inflight_set(label, True, provider='deepseek', model=model_id)
        t0 = time.time()
        timeout_secs = 180 if reasoning else 90
        with urllib.request.urlopen(req, timeout=timeout_secs) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        elapsed = time.time() - t0
        msg = data.get('choices', [{}])[0].get('message', {})
        finish_reason = data.get('choices', [{}])[0].get('finish_reason', '')
        content = msg.get('content', '') or ''
        reasoning_content = msg.get('reasoning_content', '') or ''
        usage = data.get('usage', {})
        reasoning_tokens = usage.get('completion_tokens_details', {}).get('reasoning_tokens', 0) if isinstance(usage.get('completion_tokens_details'), dict) else 0
        cache_hit = usage.get('prompt_cache_hit_tokens', 0)
        cache_miss = usage.get('prompt_cache_miss_tokens', 0)
        log.info(f"[{label}] DeepSeek responded in {elapsed:.1f}s (in={usage.get('prompt_tokens',0)}, out={usage.get('completion_tokens',0)}, reasoning={reasoning_tokens}, cache_hit={cache_hit}, cache_miss={cache_miss}, finish={finish_reason})")

        # Try primary content
        # Helper to record the (user, assistant) turn pair on success.
        # Stores the FULL content the LLM emitted (not just the parsed JSON
        # slice) so future turns see the same context the model produced.
        def _save_turn(asst_text):
            if conversation_role and asst_text:
                try:
                    import llm_conversation as _lc
                    _lc.append_pair(conversation_role, prompt_data, asst_text)
                except Exception as _se:
                    log.warning(f"[{label}] conversation save failed: {_se}")

        if content:
            j0 = content.find('{')
            j1 = content.rfind('}') + 1
            if j0 >= 0 and j1 > j0:
                try:
                    parsed = json.loads(content[j0:j1])
                    _save_turn(content)
                    return parsed
                except json.JSONDecodeError:
                    pass

        # Reasoner sometimes puts JSON in reasoning_content if it ran out of tokens
        if reasoning_content:
            # Search for JSON block in reasoning (last one is usually the intended answer)
            j1 = reasoning_content.rfind('}')
            if j1 > 0:
                j0 = reasoning_content.rfind('{', 0, j1)
                if j0 >= 0:
                    try:
                        parsed = json.loads(reasoning_content[j0:j1+1])
                        # Save the JSON-only as assistant turn (compact, useful as
                        # context anchor; reasoning_content can be huge).
                        _save_turn(reasoning_content[j0:j1+1])
                        return parsed
                    except json.JSONDecodeError:
                        pass

        log.warning(f"[{label}] Empty/invalid response (content_len={len(content)}, reasoning_len={len(reasoning_content)}, finish={finish_reason})")
    except urllib.error.HTTPError as e:
        body_text = ''
        try: body_text = e.read().decode('utf-8', errors='replace')[:300]
        except: pass
        log.warning(f"[{label}] DeepSeek HTTP {e.code}: {body_text}")
    except Exception as e:
        log.error(f"[{label}] DeepSeek error: {e}")
    finally:
        _llm_inflight_set(label, False)
    # Fallback to Claude Sonnet — but ONLY if this role isn't already in an
    # active Claude→DeepSeek fallback. Otherwise we'd bounce back to Claude,
    # it would time out / 401, and trigger yet another fallback → TG spam loop.
    try:
        import llm_fallback as _lf
        _role = _lf._role_key(label) if hasattr(_lf, '_role_key') else (label or '').lower()
        if _role in (_lf.status() or {}):
            log.info(f"[{label}] DeepSeek empty, skipping Claude fallback (role already fallbacked)")
            return None
    except Exception:
        pass
    log.info(f"[{label}] Falling back to Claude Sonnet")
    return _call_claude(prompt_data, system_prompt, label, model="sonnet")


def _call_claude(prompt_data, system_prompt, label="brain", model="sonnet", effort=None,
                 session_role=None):
    """Call Claude CLI with given system prompt, return parsed JSON.

    model: "sonnet" (default, reasoning) or "haiku" (faster + cheaper, structured tasks).
    effort: "low" | "medium" | "high" | "max" | None.
        When set, passes --effort to the CLI. Higher effort = more thinking
        tokens / deeper reasoning. Consumes more subscription quota per call.
        Mapped per role in callers (EXECUTOR/INDICATOR="high", others=None).
    session_role: when set, uses Claude CLI session continuity. First call
        creates a session via `--session-id <uuid>`; subsequent calls add
        `--resume <uuid>` so the conversation history is reused. Anthropic
        prompt-cache then keeps cache_read at ~14K+ across calls and only
        the new user message (typically a delta payload) is billed at full
        rate. Verified empirically 2026-05-02. Caller manages reset via
        `claude_session_manager.reset(role)` (e.g. on trade close).

    The user `prompt_data` is piped via STDIN (not CLI arg) to avoid Windows'
    32,767-char command-line limit (WinError 206). The `system_prompt` stays
    as a --system-prompt arg (it fits: max ~24KB for executor.txt).
    """
    # Windows lpCommandLine TOTAL limit is 32,767 chars across ALL arguments.
    # executor.txt grew to 37KB in 2026-04, breaking --system-prompt with
    # WinError 206. Splitting across --append-system-prompt didn't help:
    # the total cmd line was still 37KB. Real fix: cap what goes via cmd
    # args to ~28KB and prepend any overflow to STDIN (unbounded).
    SYS_CMD_LIMIT = 28000  # leaves margin for other args + escaping overhead
    cmd = [NODE, CLI_PATH,
           "-p",  # print mode, prompt will come from stdin
           "--output-format", "json",  # JSON gives us cache_read/cache_creation stats
           "--max-turns", "1",
           "--model", model,
           "--tools", ""]

    # ── Session continuity (multi-turn cache, ~30× quota reduction) ──
    # First call creates session; subsequent calls --resume the same UUID.
    # session_id + is_first computed via claude_session_manager.
    _is_first_turn = True
    if session_role:
        try:
            import claude_session_manager as _csm
            _sid, _is_first_turn = _csm.get_or_create(session_role)
            if _is_first_turn:
                cmd += ["--session-id", _sid]
            else:
                cmd += ["--resume", _sid]
        except Exception as _se:
            log.warning(f"[{label}] session manager failed: {_se}; falling back to single-turn")
            session_role = None  # disable for this call
    final_stdin = prompt_data
    if len(system_prompt) <= SYS_CMD_LIMIT:
        cmd += ["--system-prompt", system_prompt]
    else:
        # Pass the first chunk via --system-prompt (kept as system role).
        # Push the overflow into stdin as additional context the user supplies
        # to the assistant — Claude will read it as user input but the system
        # role text still anchors behavior. Functionally equivalent for our
        # one-shot, no-tools usage.
        cmd += ["--system-prompt", system_prompt[:SYS_CMD_LIMIT]]
        overflow = system_prompt[SYS_CMD_LIMIT:]
        final_stdin = (
            "## ADDITIONAL SYSTEM INSTRUCTIONS (continued):\n"
            f"{overflow}\n\n"
            "## TASK INPUT:\n"
            f"{prompt_data}"
        )
    if effort in ("low", "medium", "high", "max"):
        cmd += ["--effort", effort]

    try:
        # Timeouts molt generosos — l'usuari vol que Claude prengui el temps
        # que necessiti. Opus amb pensament alt pot trigar 3-8min en prompts
        # grans. Heartbeat cada 30s confirma que segueix viu (no penjat).
        # Cobrim aliases (sonnet/opus) i IDs (claude-opus-*, claude-sonnet-*).
        _is_heavy = (model in ('opus', 'sonnet')
                     or model.startswith('claude-opus-')
                     or model.startswith('claude-sonnet-'))
        _timeout = 600 if _is_heavy else 120
        # Critical env hygiene: if ANTHROPIC_API_KEY is set to empty string,
        # Claude CLI tries API-key auth with an empty key → 401, ignoring the
        # OAuth subscription token. Strip empty/blank ANTHROPIC_API_KEY so the
        # CLI falls back to CLAUDE_CODE_OAUTH_TOKEN (the subscription auth).
        cli_env = {k: v for k, v in os.environ.items()
                   if not (k == "ANTHROPIC_API_KEY" and (not v or not v.strip()))}
        log.info(f"[{label}] Calling Claude {model} (prompt={len(prompt_data)} chars via stdin, sys={len(system_prompt)} chars, timeout={_timeout}s)...")
        _llm_inflight_set(label, True, provider='claude', model=model)
        t0 = time.time()
        # Heartbeat thread — logs progress every 30s while waiting, so the
        # user knows Claude is still working (not silently stuck).
        _alive = {"flag": True}
        def _heartbeat():
            elapsed = 0
            while _alive["flag"] and elapsed < _timeout:
                _w = 30
                for _ in range(_w):
                    if not _alive["flag"]:
                        return
                    time.sleep(1)
                elapsed += _w
                if _alive["flag"]:
                    log.info(f"[{label}] Claude {model} still working... {elapsed}s/{_timeout}s")
        _hb_thread = threading.Thread(target=_heartbeat, daemon=True)
        _hb_thread.start()
        try:
            r = subprocess.run(cmd, input=final_stdin,
                               capture_output=True, timeout=_timeout,
                               encoding='utf-8', errors='replace',
                               env=cli_env, **_NOWIN)
        finally:
            _alive["flag"] = False
        elapsed = time.time() - t0
        log.info(f"[{label}] Claude responded in {elapsed:.1f}s (rc={r.returncode})")

        stdout = (r.stdout or '').strip()
        stderr = (r.stderr or '').strip()

        if r.returncode == 0 and stdout:
            # Output is the CLI wrapper JSON: {"type":"result","result":"...",
            # "usage":{...}, ...}. Parse wrapper, log token/cache stats, then
            # parse the inner JSON from .result (the model's actual response).
            try:
                wrapper = json.loads(stdout)
            except json.JSONDecodeError:
                wrapper = None
            if isinstance(wrapper, dict) and wrapper.get("result") is not None:
                # Log cache + token usage so we see real savings.
                usage = wrapper.get("usage") or {}
                in_tok = usage.get("input_tokens", 0)
                cache_create = usage.get("cache_creation_input_tokens", 0)
                cache_read = usage.get("cache_read_input_tokens", 0)
                out_tok = usage.get("output_tokens", 0)
                cost = wrapper.get("total_cost_usd", 0.0)
                cache_pct = (100.0 * cache_read / max(1, cache_read + cache_create + in_tok))
                log.info(f"[{label}] tokens: in={in_tok} cache_write={cache_create} cache_read={cache_read} out={out_tok} (cache_hit={cache_pct:.0f}%, ${cost:.4f})")
                # Mark session as used IMMEDIATELY when CLI returns success.
                # The CLI has created the session on its side regardless of
                # whether our JSON parse later succeeds. If we wait until
                # after parse, a parse failure leaves used=False and the
                # next call retries --session-id same UUID → "already in use"
                # error. Mark now to use --resume next time. (incident
                # 2026-05-03 10:30: forced INDICATOR rejected because of this)
                if session_role:
                    try:
                        import claude_session_manager as _csm
                        _csm.mark_used(session_role)
                    except Exception:
                        pass
                # Quota tracking — sum billed tokens against the 5h window
                # so the dispatcher can fall back to DeepSeek before we hit
                # subscription rate-limit. Use sum of all components (most
                # defensive metric).
                try:
                    import llm_quota as _quota
                    _quota.record(label, in_tok + cache_create + cache_read + out_tok, model=model)
                except Exception:
                    pass
                inner = wrapper["result"] or ""
                # Inner result is the model's text. The model is instructed
                # to emit JSON; extract first {...} block.
                j0 = inner.find('{')
                j1 = inner.rfind('}') + 1
                if j0 >= 0 and j1 > j0:
                    try:
                        parsed = json.loads(inner[j0:j1])
                        # session was already marked used above (right after
                        # CLI success — see comment there about parse-fail bug)
                        # Reset soft-failure counter — this call succeeded.
                        try:
                            import llm_fallback
                            llm_fallback.mark_success(label)
                        except Exception:
                            pass
                        return parsed
                    except json.JSONDecodeError:
                        pass
                log.warning(f"[{label}] No JSON in result: {inner[:300]}")
            else:
                log.warning(f"[{label}] CLI wrapper not parseable: {stdout[:300]}")
            try:
                import llm_fallback
                llm_fallback.detect_and_switch(label, stderr, stdout, r.returncode,
                                                empty_response=True)
            except Exception:
                pass
        else:
            log.warning(f"[{label}] CLI failed rc={r.returncode}")
            if stderr: log.warning(f"[{label}] stderr: {stderr[:300]}")
            if stdout: log.warning(f"[{label}] stdout: {stdout[:300]}")
            # Auto-recovery: "Session ID ... is already in use" means the CLI
            # has the session but our manager thinks used=False (ex: parse
            # failure on a previous call left it half-marked). Mark used and
            # let the caller retry — next attempt will use --resume correctly.
            if session_role and stderr and 'already in use' in stderr.lower():
                try:
                    import claude_session_manager as _csm
                    _csm.mark_used(session_role)
                    log.info(f"[{label}] Session was orphaned — marked used, next call will --resume")
                except Exception:
                    pass
            # Auto-fallback: ANY Claude failure (rc!=0, rate limit, empty) → switch to DeepSeek
            try:
                import llm_fallback
                llm_fallback.detect_and_switch(label, stderr, stdout, r.returncode)
            except Exception:
                pass
    except subprocess.TimeoutExpired:
        log.warning(f"[{label}] Claude timeout ({_timeout}s)")
        # Timeout → also fallback to DeepSeek (Claude infrastructure slow/blocked)
        try:
            import llm_fallback
            llm_fallback.detect_and_switch(label, "", "", -1, timeout=True)
        except Exception:
            pass
    except json.JSONDecodeError as e:
        log.warning(f"[{label}] JSON parse error: {e}")
        try:
            import llm_fallback
            llm_fallback.detect_and_switch(label, str(e), "", 0, empty_response=True)
        except Exception:
            pass
    except Exception as e:
        log.error(f"[{label}] Error: {e}")
        try:
            import llm_fallback
            llm_fallback.detect_and_switch(label, str(e), "", -1)
        except Exception:
            pass
    finally:
        _llm_inflight_set(label, False)
    return None


LLM_CONFIG_FILE = os.path.join(COMMON, 'brain_llm_config.json')

def _get_llm_config(role):
    """Read LLM provider+model for a given role from brain_llm_config.json.
    Falls back to defaults if file missing or role missing.
    Returns dict {"provider": "deepseek"|"claude", "model": "chat"|"reasoner"|"sonnet"|"opus"|"haiku"}.

    Override layer (2026-04-29): if `brain_llm_fallback.json` has an active,
    non-expired override for this role, return the fallback dict instead.
    The user's config is NEVER mutated by fallback — overrides live in their
    own file and auto-expire. When the override expires (or the user clears
    it), the original config is in effect again automatically.
    """
    defaults = {
        'indicator': {'provider': 'deepseek', 'model': 'chat'},
        'reviewer':  {'provider': 'deepseek', 'model': 'chat'},
        'executor':  {'provider': 'deepseek', 'model': 'reasoner'},
        'interpreter': {'provider': 'deepseek', 'model': 'chat'},
        'hunter':    {'provider': 'deepseek', 'model': 'chat'},
    }
    base = defaults.get(role, {'provider': 'deepseek', 'model': 'chat'})
    try:
        if os.path.exists(LLM_CONFIG_FILE):
            with open(LLM_CONFIG_FILE, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            base = cfg.get(role, base)
    except Exception:
        pass
    # Apply transient override if active.
    try:
        ov_path = os.path.join(COMMON, 'brain_llm_fallback.json')
        if os.path.exists(ov_path):
            with open(ov_path, 'r', encoding='utf-8') as f:
                ov_state = json.load(f)
            ov = (ov_state.get('overrides') or {}).get(role)
            if ov and time.time() < float(ov.get('restore_after_ts', 0)):
                fb = ov.get('fallback') or {}
                if fb.get('provider') and fb.get('model'):
                    return fb
    except Exception:
        pass
    return base


def _call_llm(prompt_data, system_prompt, label, role, default_reasoning=False,
              conversation_role=None, session_role=None):
    """Dispatch to the right provider based on runtime config.

    Args:
        conversation_role: DeepSeek-side multi-turn history (HUNTER, etc).
        session_role: Claude CLI --session-id/--resume role name. When set,
            calls 2+ within the role's session reuse the conversation via
            --resume, hitting prompt-cache for ~30× quota reduction.

    Quota safety: before routing to Claude, check the 5h subscription
    quota tracker. If usage > SAFE_THRESHOLD, route to DeepSeek instead
    so we don't blow the rate limit mid-trade.
    """
    # ── Quota fallback: if Claude subscription is near limit, route to DS ──
    _quota_pct = 0.0
    try:
        import llm_quota as _quota
        _quota_pct = _quota.usage_pct()
    except Exception:
        pass
    cfg = _get_llm_config(role)
    provider = (cfg.get('provider') or 'deepseek').lower()
    model = (cfg.get('model') or '').lower()
    # Claude effort policy per role (2026-04-29, Max subscription):
    # · EXECUTOR → "medium" (baixat de high per rate-limit del Max). Manté
    #   raonament estructurat però consumeix ~50% menys quota per call.
    # · INDICATOR → "high" (anàlisi estructural multi-TF, decisió rara — cada
    #   M15 tick — el cost extra val la pena per zones acurades)
    # · REVIEWER/FILTER/HUNTER → default (lleugeres)
    _CLAUDE_EFFORT = {
        'executor':  'medium',
        'indicator': 'high',
        'reviewer':  None,
        'filter':    None,
        'hunter':    None,
    }
    if provider == 'claude':
        # Quota safety: if subscription window is near saturation, route
        # to DeepSeek for this call to preserve Claude availability for
        # critical roles. We bypass Claude only when quota is REALLY tight
        # — the 0.80 threshold leaves headroom for 5h drift.
        if _quota_pct >= 0.80:
            log.warning(
                f"[{label}] Claude quota at {_quota_pct*100:.0f}% — routing to "
                f"DeepSeek (graceful downgrade, returns to Claude when quota recovers)"
            )
            return _call_deepseek(prompt_data, system_prompt, label,
                                  reasoning=default_reasoning,
                                  conversation_role=conversation_role)
        # Accept aliases (sonnet/opus/haiku) and explicit IDs (claude-opus-4-6,
        # claude-sonnet-4-6, etc.). Anything else → sonnet fallback.
        if model in ('sonnet', 'opus', 'haiku') or model.startswith('claude-'):
            claude_model = model
        else:
            claude_model = 'sonnet'
        # Effort gating: only valid for the alias models. Explicit IDs that
        # already include reasoning level shouldn't pass --effort.
        eff = _CLAUDE_EFFORT.get(role) if model in ('sonnet', 'opus', 'haiku') else None
        # Override: if user explicitly set claude-opus-4-* / claude-sonnet-4-*
        # we still want high effort for the heavy-thinking roles.
        if eff is None and role in ('executor', 'indicator', 'hunter') and model.startswith('claude-'):
            eff = 'high'
        return _call_claude(prompt_data, system_prompt, label, model=claude_model,
                             effort=eff, session_role=session_role)
    # Default / fallback: DeepSeek (with optional multi-turn conversation)
    if model == 'reasoner':
        return _call_deepseek(prompt_data, system_prompt, label, reasoning=True,
                              conversation_role=conversation_role)
    if model == 'chat':
        return _call_deepseek(prompt_data, system_prompt, label, reasoning=False,
                              conversation_role=conversation_role)
    return _call_deepseek(prompt_data, system_prompt, label, reasoning=default_reasoning,
                          conversation_role=conversation_role)


def call_indicator(prompt_data):
    """INDICATOR — identifies reversal zones. Runtime-configurable LLM.

    Session-aware on Claude path. Reset daily at 00:00 UTC and on regime
    changes (handled by reset hooks).
    """
    return _call_llm(prompt_data, INDICATOR_PROMPT, "INDICATOR", role='indicator',
                     default_reasoning=False, session_role='INDICATOR')


def call_zone_reviewer(prompt_data):
    """ZONE REVIEWER — stabilises the zone map. Runtime-configurable LLM.

    NOTE: multi-turn was tested 2026-05-01 and reverted. DeepSeek's prefix
    cache did NOT catch the conversation history reliably (cache_hit barely
    moved from system-only level despite 5-turn history). Net effect was
    MORE expensive than single-turn (~$0.014 vs ~$0.007 per call) because
    the bigger prompt offset cache savings that never materialised.
    Reverted to single-turn. Each zone review is independent anyway —
    decisions don't strictly need prior-decision context.
    """
    return _call_llm(prompt_data, ZONE_REVIEWER_PROMPT, "ZONE_REVIEWER",
                     role='reviewer', default_reasoning=False)


def run_indicator_pipeline(prompt_data, bars, price, atr_val, bias_prev, account=None, atr_m15=None):
    """Run INDICATOR → ZONE REVIEWER pipeline as a single task for the thread pool.

    Args:
        account: dict with {has_signal, direction, positions, ...} or None.
                 Used to build `trade_open` context for the Reviewer so it can
                 reason about adverse-side coverage.
        atr_m15: pre-computed ATR(14) M15 in USD. The Reviewer uses it to decide
                 zone fusion distance. If None, the M5 ATR is used as fallback.

    Returns dict with {reversal_zones, bias, context} formatted as the legacy
    contract (so the main loop doesn't need to change its response-handling shape).
    The persisted `brain_zone_state.json` is updated as a side-effect via zone_store.
    On Reviewer failure, falls back to writing the Indicator proposal directly.
    """
    from zone_store import (
        BIAS_NEUTRAL,
        apply_reviewer_decisions,
        build_zone,
        legacy_compat_view,
        read_state,
        write_state,
        ZONE_SOURCE_INDICATOR,
        ZONE_STATUS_ACTIVE,
    )

    # Step 1: INDICATOR
    ind_resp = call_indicator(prompt_data)
    if not ind_resp:
        return None
    proposed = ind_resp.get('reversal_zones', []) or []
    bias = ind_resp.get('bias', BIAS_NEUTRAL)
    ctx = ind_resp.get('context', '') or ''
    regime = ind_resp.get('regime')
    coverage = ind_resp.get('coverage') if isinstance(ind_resp.get('coverage'), dict) else None
    ind_notes = ind_resp.get('notes', '') or ''
    # New fields (2026-05-02): Indicator now produces working_range and
    # directional_commitment alongside zones. Persist them in zone_state so
    # the Executor can read them later (next phase) and the dashboard / TV
    # chart can display them.
    working_range = ind_resp.get('working_range') if isinstance(ind_resp.get('working_range'), dict) else None
    directional_commitment = ind_resp.get('directional_commitment') if isinstance(ind_resp.get('directional_commitment'), dict) else None
    # NEW 2026-05-04 v2: asymmetric_risk extret del INDICATOR per a passar
    # a l'EXECUTOR via zone_store. Camp opcional (LLM pot ometre).
    asymmetric_risk = ind_resp.get('asymmetric_risk') if isinstance(ind_resp.get('asymmetric_risk'), dict) else None

    # AUDIT: persist the raw Indicator response BEFORE the Reviewer touches it.
    # This lets us see what the Indicator actually proposed (strength distribution,
    # condition justifications) independently of the Reviewer's filter.
    try:
        _audit_path = os.path.join(COMMON, 'brain_indicator_last.json')
        with open(_audit_path, 'w', encoding='utf-8') as _af:
            json.dump({
                'ts': time.time(),
                'iso': datetime.now(timezone.utc).isoformat(),
                'response': ind_resp,
            }, _af, indent=2, ensure_ascii=False)
    except Exception as _ae:
        log.warning(f"[INDICATOR] audit write failed: {_ae}")

    # ── Build trade_open block from account ──
    trade_open = None
    has_trade_open = False
    if account and account.get('has_signal'):
        has_trade_open = True
        total_lots = 0.0
        w_entry_num = 0.0
        for p in account.get('positions', []) or []:
            lot = float(p.get('volume', p.get('lot', 0)) or 0)
            pr = float(p.get('price_open', p.get('open_price', 0)) or 0)
            total_lots += lot
            w_entry_num += pr * lot
        w_entry = (w_entry_num / total_lots) if total_lots > 0 else account.get('entry_price')
        trade_open = {
            "direction": account.get('direction'),
            "weighted_entry": round(float(w_entry), 2) if w_entry else None,
            "total_lots": round(total_lots, 3),
        }

    # ── Detect regime change vs persisted state ──
    current_state = read_state(COMMON)
    prev_regime = current_state.get('regime')
    regime_change_recent = bool(prev_regime and regime and prev_regime != regime)

    # Step 2: REVIEWER
    # atr_m15 is crucial for zone fusion distance (0.5 × atr_m15). If it's not
    # available (insufficient M15 bars) we fall back to atr_m5 and log a warning
    # — the prompt is aware of this fallback and compensates.
    _atr_m15_used = atr_m15 if atr_m15 is not None else atr_val
    if atr_m15 is None:
        log.warning(f"[REVIEWER] atr_m15 unavailable — falling back to atr_m5 ({atr_val:.2f}). Zone fusion may be tighter than ideal.")
    reviewer_prompt = build_zone_reviewer_prompt(
        proposed_zones=proposed,
        current_state=current_state,
        market_context={
            "price": price,
            "atr_m5": atr_val,
            "atr_m15": _atr_m15_used,
            "atr_m15_available": atr_m15 is not None,
            "bias_prev": bias_prev,
            "bias_proposed": bias,
            "regime": regime,
            "regime_prev": prev_regime,
            "regime_change_recent": regime_change_recent,
            "coverage": coverage,
            "trade_open": trade_open,
            "indicator_context": ctx,
            "indicator_notes": ind_notes,
        },
    )
    rev_resp = call_zone_reviewer(reviewer_prompt)

    # Step 3: merge. If Reviewer failed, fall back to seeding state from proposal.
    if rev_resp and isinstance(rev_resp.get('decisions'), list):
        decisions = rev_resp['decisions']
        new_bias = rev_resp.get('overall_bias', bias)
        rev_notes = rev_resp.get('notes', '') or ''
        coverage_gap = rev_resp.get('coverage_gap')
        if coverage_gap not in (None, 'close', 'mid_adverse', 'far_adverse'):
            log.warning(f"[ZONE_REVIEWER] unexpected coverage_gap={coverage_gap!r}, ignoring")
            coverage_gap = None
        merged_notes = " | ".join(n for n in (ind_notes, rev_notes) if n)
        if regime_change_recent:
            log.info(f"[ZONE_REVIEWER] regime change detected: {prev_regime} → {regime}")
        if coverage_gap:
            log.warning(f"[ZONE_REVIEWER] coverage_gap={coverage_gap} (Executor will operate without safety net)")
        new_state = apply_reviewer_decisions(
            current_state=current_state,
            proposed_zones=proposed,
            decisions=decisions,
            new_bias=new_bias,
            new_context=ctx,
            new_regime=regime,
            new_coverage=coverage,
            new_notes=merged_notes,
            new_coverage_gap=coverage_gap,
            has_trade_open=has_trade_open,
            new_working_range=working_range,
            new_directional_commitment=directional_commitment,
            new_asymmetric_risk=asymmetric_risk,
        )
    else:
        log.warning("[ZONE_REVIEWER] no valid response — falling back to proposal-only state")
        new_zones = [
            build_zone(
                price=float(p.get('price', 0)),
                ztype=p.get('type', 'SUPPORT'),
                strength=p.get('strength', 'MODERATE'),
                bounce_direction=p.get('bounce_direction', p.get('bounce', 'BUY')),
                condition=p.get('condition', ''),
                source=ZONE_SOURCE_INDICATOR,
                region=p.get('region'),
                # Dual-feed metadata propagada del LLM si l'ha proporcionat
                confidence_numeric=p.get('confidence_numeric'),
                naked_poc_futures=p.get('naked_poc_futures', False),
                data_sources_count=p.get('data_sources_count', 1),
                expected_bounce_usd=p.get('expected_bounce_usd'),
            )
            for p in proposed
        ]
        new_state = {
            "bias": bias,
            "regime": regime,
            "context": ctx,
            "notes": ind_notes,
            "coverage": coverage or {"close": False, "mid": False, "far": False},
            "coverage_gap": None,
            "zones": new_zones,
        }

    # ── STRONG cluster suppression (2026-04-27) ──
    # If 2+ STRONG zones cluster within ~1.5×ATR_M5 they're operationally the
    # same structural block. Treating them as independent strong levels causes:
    # (1) FAST engine fires N AVGs in cascade (1 confirmation each is too easy)
    # (2) Staircase TPs stack tightly with no proper escalation
    # (3) Proportional capture closes 50%×N → premature full close
    # Keep the zone with highest confluence (most touches as proxy) as STRONG
    # and demote the others to MODERATE. Information preserved, behavior fixed.
    try:
        _zones_list = new_state.get("zones") or []
        _cluster_dist = max(2.5, 1.5 * (atr_val or 2.0))
        _strong_zones = [z for z in _zones_list
                         if (z.get("strength") or "").upper() == "STRONG"
                         and (z.get("status") or "ACTIVE").upper() == "ACTIVE"]
        # Sort STRONG by descending confluence-proxy (touches), tie-break on
        # condition length (longer = more confluences described).
        def _confluence_score(z):
            return (int(z.get("touches", 0) or 0), len(z.get("condition") or ""))
        _strong_zones.sort(key=_confluence_score, reverse=True)
        _kept_anchors = []     # list of (price, type) we've accepted as STRONG
        _demoted = 0
        for z in _strong_zones:
            zp = float(z.get("price", 0) or 0)
            ztype = (z.get("type") or "").upper()
            if zp <= 0:
                continue
            # If a same-type STRONG anchor already exists within cluster_dist,
            # demote this one to MODERATE.
            in_cluster = any(
                a_type == ztype and abs(a_price - zp) <= _cluster_dist
                for a_price, a_type in _kept_anchors
            )
            if in_cluster:
                z["strength"] = "MODERATE"
                _demoted += 1
                # Annotate condition so the operator sees why
                _orig_cond = z.get("condition") or ""
                z["condition"] = (
                    f"[demoted from STRONG: clustered with another STRONG "
                    f"within {_cluster_dist:.1f}$] " + _orig_cond
                )
            else:
                _kept_anchors.append((zp, ztype))
        if _demoted:
            log.info(
                f"[CLUSTER-SUPPRESS] {_demoted} STRONG zone(s) demoted to MODERATE "
                f"(cluster_dist={_cluster_dist:.2f}$ at ATR_M5={atr_val:.2f})"
            )
    except Exception as _cse:
        log.warning(f"[CLUSTER-SUPPRESS] failed: {_cse}")

    # Inject working_range + directional_commitment + asymmetric_risk into
    # state before persisting. apply_reviewer_decisions doesn't know about
    # these — they're Indicator-only outputs that pass through unchanged.
    if working_range is not None:
        new_state['working_range'] = working_range
    if directional_commitment is not None:
        new_state['directional_commitment'] = directional_commitment
    if asymmetric_risk is not None:
        new_state['asymmetric_risk'] = asymmetric_risk
    # Log to console — this is what the operator sees
    if directional_commitment:
        _dc_side = directional_commitment.get('side', '?')
        _dc_strength = directional_commitment.get('strength', '?')
        _dc_conf = directional_commitment.get('confidence', 0)
        _dc_expl = (directional_commitment.get('explanation') or '')[:200]
        log.info(
            f"[INDICATOR] BIAS: {_dc_side} ({_dc_strength}, conf={_dc_conf:.2f}) — {_dc_expl}"
        )
    if working_range:
        _wr_high = working_range.get('high')
        _wr_low = working_range.get('low')
        _wr_type = working_range.get('type', '?')
        _wr_basis = (working_range.get('basis') or '')[:120]
        log.info(
            f"[INDICATOR] RANG: {_wr_low} - {_wr_high} ({_wr_type}) — {_wr_basis}"
        )

    try:
        write_state(COMMON, new_state)
    except Exception as e:
        log.warning(f"[ZONE_STORE] write_state failed: {e}")

    # Return legacy-compat view so the main loop's response handler stays unchanged.
    return legacy_compat_view(new_state)


def build_zone_reviewer_prompt(proposed_zones, current_state, market_context):
    """Build the JSON payload for the Zone Reviewer brain.

    proposed_zones: list of zones from Indicator (no id yet)
    current_state: dict read from zone_store.read_state()
    market_context: dict {price, atr, rsi, bias_prev, session, notes}
    """
    # Recently invalidated/removed zones (last 60 min) — useful for the Reviewer
    # to REACTIVATE zones that were killed but now look valid again, or to
    # remember WHY they were killed so it doesn't re-propose the same trap.
    _INVALID_WINDOW_S = 3600
    _now_ts = time.time()
    def _iso_to_ts(iso_str):
        try:
            from datetime import datetime as _dt
            return _dt.fromisoformat(iso_str.replace('Z', '+00:00')).timestamp()
        except Exception:
            return 0.0
    recently_invalidated = []
    for z in (current_state or {}).get("zones", []):
        if z.get("status") in ("INVALIDATED", "REMOVED", "STALE"):
            inv_iso = z.get("invalidated_at") or z.get("last_validated_at")
            inv_ts = _iso_to_ts(inv_iso) if inv_iso else 0.0
            if inv_ts > 0 and (_now_ts - inv_ts) <= _INVALID_WINDOW_S:
                recently_invalidated.append({
                    "id": z.get("id"),
                    "price": z.get("price"),
                    "type": z.get("type"),
                    "strength": z.get("strength"),
                    "bounce_direction": z.get("bounce_direction"),
                    "invalidated_at": inv_iso,
                    "invalidated_reason": z.get("invalidated_reason"),
                })
    # ── KEY ORDER FOR DEEPSEEK PREFIX CACHE ──
    # Stable / slow-changing first, volatile last.
    payload = {
        "recently_invalidated_zones": recently_invalidated,
        "current_zones": [
            {
                "id": z.get("id"),
                "price": z.get("price"),
                "type": z.get("type"),
                "strength": z.get("strength"),
                "bounce_direction": z.get("bounce_direction"),
                "region": z.get("region"),
                "status": z.get("status"),
                "touches": z.get("touches", 0),
                "rejections": z.get("rejections", 0),
                "created_at": z.get("created_at"),
                "last_validated_at": z.get("last_validated_at"),
            }
            for z in (current_state or {}).get("zones", [])
            if z.get("status") == "ACTIVE"
        ],
        "market_context": market_context or {},
        "proposed_zones": proposed_zones or [],   # ← input to review goes last
    }
    return (
        "Revisa el mapa de zones. Retorna una decisió per cada zona actual "
        "i per cada zona proposada. JSON ONLY.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def call_executor(prompt_data):
    """EXECUTOR — manages the active trade. Runtime-configurable LLM (default DeepSeek reasoner).

    Session-aware on Claude path: --session-id on first call, --resume on
    subsequent. Reset on trade close (handled in main loop). Cuts quota
    consumption by ~30× on follow-up calls vs single-turn.
    """
    return _call_llm(prompt_data, EXECUTOR_PROMPT, "EXECUTOR", role='executor',
                     default_reasoning=True, session_role='EXECUTOR')


def call_hunter(prompt_data):
    """HUNTER — reversion scalping scanner. Pattern-recognition on zones + confluences.
    Default DeepSeek chat (fast + cheap, no deep reasoning needed).
    Multi-turn conversation enabled — reduces input cost by ~50% on follow-up
    calls within the inactivity window."""
    return _call_llm(prompt_data, HUNTER_PROMPT, "HUNTER", role='hunter',
                     default_reasoning=False, conversation_role='HUNTER')


def _build_hunter_delta(bars_cache, account, sig_state):
    """Compact delta payload for HUNTER follow-up turns.

    The LLM has the FULL context from the first turn already cached. We
    only send what could have CHANGED meaningfully:
      - current price + timestamp
      - latest 5 M5 bars (the new ones since last call)
      - current ATR/RSI snapshot (cheap, helps re-anchor)
      - account state (DD, lots — usually small drift)
      - any active trade context
      - regime/bias if changed (from zone_store; cheap to send each time
        as a single line)

    Total target: ≤ 1.5 KB. Compared to full payload (≈ 16 KB) that's a
    >10× input reduction; actual cost saving with cache lands around -50%.
    """
    if not bars_cache:
        return "DELTA: no bars available."

    last = bars_cache[-1]
    price = float(last.get('close', 0) or 0)

    # Last 5 M5 bars — compact
    last_bars = []
    for b in bars_cache[-5:]:
        last_bars.append({
            'o': round(float(b.get('open', 0) or 0), 2),
            'h': round(float(b.get('high', 0) or 0), 2),
            'l': round(float(b.get('low', 0) or 0), 2),
            'c': round(float(b.get('close', 0) or 0), 2),
            'v': int(float(b.get('volume', 0) or 0)),
        })

    # Cheap technicals
    try:
        atr_m5 = atr(bars_cache, 14) or 0.0
    except Exception:
        atr_m5 = 0.0
    try:
        closes = [float(b.get('close', 0) or 0) for b in bars_cache]
        rsi_m5 = rsi(closes, 14) if len(closes) >= 15 else None
    except Exception:
        rsi_m5 = None

    # Active trade context (if any)
    active_trade = None
    try:
        if sig_state and sig_state.is_active():
            opened_ts = float(sig_state.get('opened_ts', 0) or 0)
            age_min = round((time.time() - opened_ts) / 60, 1) if opened_ts else None
            active_trade = {
                'direction': sig_state.get('direction'),
                'entry_price': sig_state.get('entry_price'),
                'age_minutes': age_min,
            }
    except Exception:
        pass

    acc_ctx = {
        'balance': round(float(account.get('balance', 0) or 0), 2),
        'equity': round(float(account.get('equity', 0) or 0), 2),
        'dd_pct': round(float(account.get('dd_pct', 0) or 0), 3),
    }

    # Pull regime/bias on the cheap (in case it shifted since last turn)
    regime, bias = None, None
    try:
        from zone_store import read_state
        zstate = read_state(COMMON)
        regime = (zstate or {}).get('regime')
        bias = (zstate or {}).get('bias')
    except Exception:
        pass

    delta = {
        'turn_type': 'delta',
        'note': "Full context provided in turn 1. This is the per-call update.",
        'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        'price': round(price, 2),
        'atr_m5': round(atr_m5, 2),
        'rsi_m5': round(rsi_m5, 1) if rsi_m5 is not None else None,
        'last_5_m5_bars': last_bars,
        'account': acc_ctx,
        'active_trade': active_trade,
        'regime': regime,
        'bias': bias,
    }
    return (
        "DELTA — analitza com a continuació de la conversa anterior. "
        "El context complet (zones, multi-TF, swings, etc.) ja està a la "
        "història. Aquí només arriben les dades noves del moment. "
        "Si no veus res operable, retorna setups buits.\n\n"
        "JSON ONLY.\n\n"
        + json.dumps(delta, ensure_ascii=False, indent=2, default=str)
    )


def build_hunter_prompt(bars_cache, account, sig_state):
    """Build a payload for the Hunter role.

    Sends a FULL or DELTA payload depending on whether there's prior
    conversation context. Cost-optimised:
      - Turn 1 (no prior context): full snapshot — zones, multi-TF bars,
        market context, swings, exhaustion, etc.
      - Turn 2+ (with prior context): compact delta — current price, last
        few new M5 bars, account state, new triggers. The LLM keeps the
        prior context from the conversation history; only the new info is
        billed at full input price.

    Multi-turn savings (DeepSeek v4-pro promo): ~50% per call after first.
    Falls back to full payload if conversation_role is empty.
    """
    # Detect whether there's prior conversation history. If yes, send a
    # compact delta. The conversation manager (`llm_conversation`) handles
    # window/TTL; we just ask "is there anything cached?".
    _is_first = True
    try:
        import llm_conversation as _lc
        _is_first = _lc.is_first_turn('HUNTER')
    except Exception:
        _is_first = True

    if not _is_first:
        return _build_hunter_delta(bars_cache, account, sig_state)
    # else fall through to full snapshot below
    # ── Zones with full context (touches, rejections, age) ──
    try:
        from zone_store import read_state, active_zones
        zstate = read_state(COMMON)
        zones = active_zones(zstate)
    except Exception:
        zstate, zones = {}, []
    regime = (zstate or {}).get('regime')
    bias = (zstate or {}).get('bias')
    zone_ctx = (zstate or {}).get('context', '')

    price = bars_cache[-1]['close'] if bars_cache else 0.0

    # Split zones by direction relative to price (above = resistance, below = support)
    zones_above, zones_below = [], []
    for z in zones or []:
        try:
            zp = float(z.get('price', 0) or 0)
        except Exception:
            continue
        if zp <= 0:
            continue
        dist = zp - price
        z_entry = {
            'price': zp,
            'distance_usd': round(dist, 2),
            'type': z.get('type'),
            'strength': z.get('strength'),
            'bounce_direction': z.get('bounce_direction'),
            'touches': int(z.get('touches', 0) or 0),
            'rejections': int(z.get('rejections', 0) or 0),
            'created_at': z.get('created_at'),
            'last_validated_at': z.get('last_validated_at'),
            'condition': (z.get('condition') or '')[:160],
        }
        if dist > 0:
            zones_above.append(z_entry)
        else:
            zones_below.append(z_entry)
    # Sort: closest first. Keep up to 8 per side (trader doesn't need more than that)
    zones_above.sort(key=lambda z: z['distance_usd'])
    zones_below.sort(key=lambda z: -z['distance_usd'])
    zones_above = zones_above[:8]
    zones_below = zones_below[:8]

    # ── Multi-timeframe bars (raw so the trader can "see" structure) ──
    try:
        bars_m15 = aggregate_bars(bars_cache, 3)
    except Exception:
        bars_m15 = []
    try:
        bars_m1 = aggregate_bars(bars_cache, 1) if bars_cache else []  # approximation; M5 is finest we have
    except Exception:
        bars_m1 = []

    def _compact_bars(bars_list, n):
        out = []
        for b in (bars_list or [])[-n:]:
            out.append({
                'o': round(float(b.get('open', 0) or 0), 2),
                'h': round(float(b.get('high', 0) or 0), 2),
                'l': round(float(b.get('low', 0) or 0), 2),
                'c': round(float(b.get('close', 0) or 0), 2),
                'v': int(float(b.get('volume', 0) or 0)),
            })
        return out

    # ── Technical indicators — ATR, RSI, EMAs across timeframes ──
    try:
        _atr_m5 = atr(bars_cache, 14) or 0.0
    except Exception:
        _atr_m5 = 0.0
    try:
        _atr_m15 = atr(bars_m15, 14) or _atr_m5
    except Exception:
        _atr_m15 = _atr_m5

    try:
        closes_m5 = [float(b.get('close', 0) or 0) for b in (bars_cache or [])]
        _rsi_m5 = rsi(closes_m5, 14) if len(closes_m5) >= 15 else None
    except Exception:
        closes_m5, _rsi_m5 = [], None
    try:
        closes_m15 = [float(b.get('close', 0) or 0) for b in (bars_m15 or [])]
        _rsi_m15 = rsi(closes_m15, 14) if len(closes_m15) >= 15 else None
    except Exception:
        closes_m15, _rsi_m15 = [], None

    def _safe_ema(vals, n):
        try:
            return round(ema(vals, n), 2) if len(vals) >= n else None
        except Exception:
            return None
    emas = {
        'ema20_m5': _safe_ema(closes_m5, 20),
        'ema50_m5': _safe_ema(closes_m5, 50),
        'ema20_m15': _safe_ema(closes_m15, 20),
        'ema50_m15': _safe_ema(closes_m15, 50),
        'ema200_m15': _safe_ema(closes_m15, 200),
    }

    # ── Recent swing highs/lows (trader reads structure from pivots) ──
    swings = {'recent_highs': [], 'recent_lows': []}
    try:
        pw = 3  # pivot window
        N = len(bars_cache or [])
        for i in range(pw, N - pw):
            hi = float(bars_cache[i].get('high', 0) or 0)
            lo = float(bars_cache[i].get('low', 0) or 0)
            is_swing_high = all(
                hi >= float(bars_cache[j].get('high', 0) or 0)
                for j in list(range(i - pw, i)) + list(range(i + 1, i + pw + 1))
            )
            is_swing_low = all(
                lo <= float(bars_cache[j].get('low', 0) or 0)
                for j in list(range(i - pw, i)) + list(range(i + 1, i + pw + 1))
            )
            bars_ago = N - 1 - i
            if is_swing_high:
                swings['recent_highs'].append({'price': round(hi, 2), 'bars_ago': bars_ago})
            if is_swing_low:
                swings['recent_lows'].append({'price': round(lo, 2), 'bars_ago': bars_ago})
        swings['recent_highs'] = swings['recent_highs'][-5:]
        swings['recent_lows'] = swings['recent_lows'][-5:]
    except Exception:
        pass

    # ── Exhaustion pre-detection (hints for the trader, not rules) ──
    exhaustion = {}
    try:
        # Volume pattern last 5 bars (rising / falling / flat)
        last5 = [float(b.get('volume', 0) or 0) for b in (bars_cache or [])[-6:-1]]  # excluir live bar
        if len(last5) >= 3:
            diffs = [last5[i] - last5[i-1] for i in range(1, len(last5))]
            rising = sum(1 for d in diffs if d > 0)
            falling = sum(1 for d in diffs if d < 0)
            if falling >= 3:
                exhaustion['volume_pattern'] = 'decreasing'
            elif rising >= 3:
                exhaustion['volume_pattern'] = 'increasing'
            else:
                exhaustion['volume_pattern'] = 'mixed'
            exhaustion['volume_last_5'] = [int(v) for v in last5]
        # Last closed bar volume vs 20-bar average
        recent_vols = [float(b.get('volume', 0) or 0) for b in (bars_cache or [])[-21:-1]]
        if len(recent_vols) >= 10:
            avg_v = sum(recent_vols) / len(recent_vols)
            last_v = float(bars_cache[-2].get('volume', 0) or 0) if len(bars_cache) >= 2 else 0
            exhaustion['volume_last_vs_avg_ratio'] = round(last_v / avg_v, 2) if avg_v > 0 else None
        # Pin bar detection (last 3 closed bars, wick ≥ 2× body)
        pin_bar_detected = False
        for b in (bars_cache or [])[-4:-1]:
            o = float(b.get('open', 0) or 0); c = float(b.get('close', 0) or 0)
            h = float(b.get('high', 0) or 0); l = float(b.get('low', 0) or 0)
            body = abs(c - o)
            upper_wick = h - max(c, o)
            lower_wick = min(c, o) - l
            if body > 0 and (upper_wick >= 2 * body or lower_wick >= 2 * body):
                pin_bar_detected = True
                break
        exhaustion['pin_bar_last_3'] = pin_bar_detected
        # Doji last 3 (body < 10% of range)
        doji_detected = False
        for b in (bars_cache or [])[-4:-1]:
            o = float(b.get('open', 0) or 0); c = float(b.get('close', 0) or 0)
            h = float(b.get('high', 0) or 0); l = float(b.get('low', 0) or 0)
            rng = h - l
            if rng > 0 and abs(c - o) / rng < 0.1:
                doji_detected = True
                break
        exhaustion['doji_last_3'] = doji_detected
        # Simple RSI divergence hint: price higher high, RSI lower high (or inverse)
        if len(closes_m5) >= 20 and _rsi_m5 is not None:
            recent_prices = closes_m5[-10:]
            # Very rough: compare last 3 vs prior 3
            p_now = max(recent_prices[-3:])
            p_prev = max(recent_prices[-8:-3]) if len(recent_prices) >= 8 else p_now
            try:
                rsi_now = rsi(closes_m5[-15:], 14)
                rsi_prev = rsi(closes_m5[-20:-3], 14)
                if p_now > p_prev and rsi_now < rsi_prev:
                    exhaustion['bearish_divergence'] = True
                elif p_now < p_prev and rsi_now > rsi_prev:
                    exhaustion['bullish_divergence'] = True
            except Exception:
                pass
    except Exception:
        pass

    # ── Market context (build_market_context returns session + structure + liquidity + macro) ──
    try:
        from market_context import build_market_context
        mc = build_market_context(bars_cache, account, tv_helper=None,
                                   now_utc=datetime.now(timezone.utc), for_executor=True)
    except Exception:
        mc = None

    # ── Active trade context (for alt_hypothesis mode) ──
    active_trade = None
    try:
        if sig_state and sig_state.is_active():
            opened_ts = sig_state.get('opened_ts', 0) or 0
            age_min = round((time.time() - float(opened_ts)) / 60, 1) if opened_ts else None
            active_trade = {
                'direction': sig_state.get('direction'),
                'entry_price': sig_state.get('entry_price'),
                'blend': sig_state.get('entry_price'),
                'age_minutes': age_min,
            }
    except Exception:
        pass

    # ── News imminence + safety flags ──
    news_imminent = False
    try:
        import telegram_listener
        if hasattr(telegram_listener, 'is_news_imminent'):
            news_imminent = bool(telegram_listener.is_news_imminent(minutes=15))
    except Exception:
        pass

    losing_streak_hit = False
    try:
        import hunter_stats as hs
        losing_streak_hit = hs.is_losing_streak_hit(threshold=3)
    except Exception:
        pass

    acc_ctx = {
        'balance': round(float(account.get('balance', 0) or 0), 2),
        'equity': round(float(account.get('equity', 0) or 0), 2),
        'dd_pct': round(float(account.get('dd_pct', 0) or 0), 3),
        'dd_limit_pct': 3.5,
    }

    # ── Previous Hunter setups summary (last 5, so Hunter learns from history) ──
    recent_hunter = []
    try:
        import hunter_stats as _hs
        s = _hs.summary(days=7)
        recent_hunter = s.get('daily', {})
    except Exception:
        pass

    # ── KEY ORDER FOR DEEPSEEK PREFIX CACHE ──
    # Stable (changes once/day or less) → semi-stable (every few minutes) →
    # volatile (per call). DeepSeek caches by prefix; the more stable the
    # leading bytes, the higher the hit rate.
    payload = {
        # ── STABLE (≈ once per day) ──
        'recent_hunter_daily_summary': recent_hunter,
        # ── SEMI-STABLE (change occasionally) ──
        'news_imminent': news_imminent,
        'losing_streak_hit': losing_streak_hit,
        # ── SEMI-STABLE (change every few minutes) ──
        'regime': regime,
        'bias': bias,
        'indicator_context': zone_ctx[:300] if zone_ctx else '',
        'zones_above_price': zones_above,   # resistance zones (≤ 8 nearest)
        'zones_below_price': zones_below,   # support zones (≤ 8 nearest)
        'market_state': (mc or {}).get('market_state') if isinstance(mc, dict) else None,
        'external': (mc or {}).get('external') if isinstance(mc, dict) else None,
        'htf': (mc or {}).get('htf') if isinstance(mc, dict) else None,
        # ── ACCOUNT (slow drift) ──
        'account': acc_ctx,
        # ── SLOW-CHANGING TECHNICALS (M15+ updates) ──
        'emas': emas,
        'swings': swings,
        'atr_m15': round(_atr_m15, 2),
        'rsi_m15': round(_rsi_m15, 1) if _rsi_m15 is not None else None,
        'bars_m15_last_20': _compact_bars(bars_m15, 20),
        # ── VOLATILE (per call) ──
        'atr_m5': round(_atr_m5, 2),
        'rsi_m5': round(_rsi_m5, 1) if _rsi_m5 is not None else None,
        'exhaustion': exhaustion,
        'bars_m5_last_30': _compact_bars(bars_cache, 30),
        'active_trade': active_trade,
        'price': price,
        'timestamp_utc': datetime.now(timezone.utc).isoformat(),
    }
    # ── Rich context (MTF structure + volume profile + liquidity + technical) ──
    # Hunter benefits most from POC/HVN/naked POCs + liquidity + divergences.
    try:
        import indicator_context as _ic
        payload['rich_context'] = _ic.build_for_hunter(bars_cache, tv_helper=tv)
    except Exception as _rc_err:
        log.warning(f"[HUNTER_PROMPT] rich_context failed: {_rc_err}")
        payload['rich_context'] = ''
    return (
        "Analitza el mercat com un trader expert. Identifica setups de reversió "
        "amb edge real. Si no veus res operable, retorna setups buits amb explicació.\n\n"
        "JSON ONLY.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    )


def call_interpreter(tg_message_text, channel_name, current_trade=None):
    """INTERPRETER — TG message classification fallback. DeepSeek chat (simple + cheap).

    `current_trade` (optional): dict with {direction, entry_price, blend, floating_usd}
    if there's an active trade. Helps the Interpreter disambiguate messages like
    "4820" (could be MOVE_SL if there's a SELL open; likely noise otherwise).
    """
    ctx_block = ""
    if current_trade:
        ctx_block = (
            "\n\nCONTEXT — trade obert ara:\n"
            + json.dumps({
                "direction": current_trade.get("direction"),
                "entry_price": current_trade.get("entry_price"),
                "blend": current_trade.get("blend"),
                "floating_usd": current_trade.get("floating_usd"),
            }, ensure_ascii=False)
        )
    else:
        ctx_block = "\n\nCONTEXT — no hi ha trade obert. Missatges de 'close' o preus aïllats són probablement OTHER."
    prompt = f"Canal: {channel_name}\nMissatge:\n{tg_message_text}{ctx_block}\n\nClassifica aquest missatge. JSON ONLY."
    return _call_deepseek(prompt, INTERPRETER_PROMPT, "INTERPRETER", reasoning=False)


def call_filter(signal_info, context_data):
    """Call Claude FILTER to decide TAKE/SKIP/DELAY on an OPEN signal.
    signal_info: dict with direction, entry_price, channel
    context_data: dict with price, zones, bias, account, positions
    """
    prompt = f"""═══ SENYAL REBUT ═══
Canal: {signal_info.get('channel')}
Direction: {signal_info.get('direction')}
Entry price: {signal_info.get('entry_price', 'MARKET')}

═══ MERCAT ARA ═══
Preu actual: {context_data.get('price', 0):.2f}
RSI M5: {context_data.get('rsi', 0):.1f} | ATR: {context_data.get('atr', 0):.2f}
High20: {context_data.get('high20', 0):.1f} | Low20: {context_data.get('low20', 0):.1f}
Sessió UTC: {datetime.now(timezone.utc).strftime('%H:%M')}

═══ BIAS + ZONES ═══
Bias: {context_data.get('bias', 'NEUTRAL')}
Zones properes:
{context_data.get('zones_text', '(cap)')}

═══ COMPTE ═══
Balance: ${context_data.get('balance', 0):,.0f}
DD usat: ${context_data.get('dd_used', 0):.0f} ({context_data.get('dd_pct', 0):.1f}%)
Posicions obertes: {context_data.get('pos_count', 0)}
Senyal anterior actiu: {context_data.get('has_signal', False)}

Decideix: TAKE, SKIP o DELAY? JSON ONLY."""
    return _call_claude(prompt, FILTER_PROMPT, "FILTER")


# ═══════════════════════════════════════════════════════════════
# TG PROCESSING — handles Telegram messages (Brain v3 signal flow)
# ═══════════════════════════════════════════════════════════════

def build_filter_context(bars_cache, account, price, engine):
    """Build context data for the FILTER Claude call."""
    zones_data = load_zones()
    zones = engine.reversal_zones or zones_data.get('reversal_zones', [])
    bias = zones_data.get('bias', 'NEUTRAL')

    # Zone distances
    zone_lines = []
    for z in sorted(zones, key=lambda z: z.get('price', 0)):
        zp = z.get('price', 0)
        dist = price - zp
        zone_lines.append(f"  {z.get('type','?')} {zp:.1f} [{z.get('strength','?')}] dist={dist:+.1f} — {z.get('condition','')}")
    zones_text = "\n".join(zone_lines) if zone_lines else "(cap zona encara identificada)"

    closes = [b['close'] for b in bars_cache]
    return {
        'price': price,
        'rsi': rsi(closes, 14) or 0,
        'atr': atr(bars_cache, 14) or 0,
        'high20': max(b['high'] for b in bars_cache[-20:]),
        'low20': min(b['low'] for b in bars_cache[-20:]),
        'bias': bias,
        'zones_text': zones_text,
        'balance': account.get('balance', 0),
        'dd_used': account.get('dd_used', 0),
        'dd_pct': account.get('dd_pct', 0),
        'pos_count': account.get('pos_count', 0),
        'has_signal': account.get('has_signal', False),
    }


import re as _re

# Regex patterns for standard signal messages (avoids Claude CLI latency)
_RE_OPEN_DIR = _re.compile(r'\b(buy|sell|compra|venta|vende|long|short|corto|largo)\b', _re.I)
_RE_PRICE    = _re.compile(r'(\d{4}(?:[.,]\d{1,3})?)')
_RE_MARKET   = _re.compile(r'\b(now|market|mercado|ya|ahora)\b', _re.I)
_RE_CLOSE    = _re.compile(r'\b(cerramos|cerrar|close\s*all|tanquem|tanca[rm]?|cierr[ae]|close\s+it|close\s+trade)\b', _re.I)
_RE_MOVE_SL  = _re.compile(r'\b(movemos\s*sl|sl\s*(?:a|at|al|to)\s*be|sl\s*be|breakeven|break\s*even|be\s*stop|subir?\s*sl|entry\s*sl|sl\s*(?:a|al|at|to)?\s*entry|sl\s*to\s*entry)\b', _re.I)
_RE_SL_PRICE = _re.compile(r'\bsl\s*(?:@|a|at|en)?\s*(\d{4}(?:[.,]\d{1,3})?)', _re.I)

def interpret_tg_local(text, channel):
    """Regex-based TG message interpreter. Returns INTERPRETER-compatible dict or None (fallback to CLI).

    Handles the standard vocabulary of TrueTrading/Vikingo with high confidence.
    Returns None for anything ambiguous — caller will fall through to Claude CLI.

    Priority order: CLOSE → OPEN (direction wins even if SL also mentioned) → MOVE_SL.
    """
    if not text:
        return None
    t = text.strip()

    # 1) CLOSE — "cerramos", "tanquem", "close all"
    if _RE_CLOSE.search(t):
        return {
            'type': 'CLOSE',
            'confidence': 0.98,
            'close_all': True,
            'raw_summary': 'tancar posicions (regex)',
        }

    # 2) OPEN — direcció present guanya (p.ex. "compra 4800 con sl 4820" és OPEN, no MOVE_SL)
    m = _RE_OPEN_DIR.search(t)
    if m:
        tok = m.group(1).lower()
        if tok in ('buy', 'compra', 'long', 'largo'):
            direction = 'BUY'
        else:
            direction = 'SELL'
        entry_price = None
        if not _RE_MARKET.search(t):
            # Preu d'entrada: el PRIMER número vàlid que NO estigui precedit per "sl"
            # Treu primer els "sl 4820" per no capturar-los com a entry
            t_nosl = _RE_SL_PRICE.sub('', t)
            pm = _RE_PRICE.search(t_nosl)
            if pm:
                try:
                    p = float(pm.group(1).replace(',', '.'))
                    # Sanity bound: XAUUSD in ~3000-6000 range
                    if 1000 <= p <= 10000:
                        entry_price = p
                except Exception:
                    pass
        return {
            'type': 'OPEN',
            'confidence': 0.95,
            'direction': direction,
            'entry_price': entry_price,
            'raw_summary': f"{direction} @ {entry_price or 'MARKET'} (regex)",
        }

    # 3) MOVE_SL — "movemos SL", "SL a BE", "breakeven", "sl 4820", "sl al entry"...
    sl_price_match = _RE_SL_PRICE.search(t)
    if _RE_MOVE_SL.search(t) or sl_price_match:
        new_sl = None
        breakeven = True
        if sl_price_match and not _RE_MOVE_SL.search(t):
            try:
                new_sl = float(sl_price_match.group(1).replace(',', '.'))
                breakeven = False
            except Exception:
                pass
        return {
            'type': 'MOVE_SL',
            'confidence': 0.97,
            'breakeven': breakeven,
            'new_sl': new_sl,
            'raw_summary': f"moure SL a {'BE' if breakeven else new_sl} (regex)",
        }

    # No clear match — let the caller fallback to Claude CLI
    return None


def process_tg_messages(messages, sig_state, engine, bars_cache, account, price):
    """Process incoming Telegram messages. Tries local regex first, falls back to INTERPRETER CLI."""
    for msg in messages:
        text = msg.get('text', '').strip()
        channel = msg.get('channel', '?')
        ch_type = msg.get('type', 'signal')
        if not text:
            continue

        log.info(f"[TG] From {channel}: {text[:100]}")

        # Journal raw TG message receipt (every type — we'll classify after).
        try:
            brain_journal.write(
                "signal_received" if ch_type == 'signal' else "note",
                "TG",
                {"channel": channel, "ch_type": ch_type, "text": text[:500]},
                trade_id=sig_state.get_trade_id() if hasattr(sig_state, 'get_trade_id') else None,
                snapshot=brain_journal.build_snapshot(price, account, sig_state),
            )
        except Exception:
            pass

        # Data channels carry FX Markets 🚨 news warnings. Parse locally and
        # store in news_state so handle_open_signal can gate new entries.
        # Existing trades keep being managed normally — this only blocks NEW.
        if ch_type == 'data':
            try:
                _ts = msg.get('utc') or msg.get('ts')
                if isinstance(_ts, (int, float)):
                    _msg_dt = datetime.fromtimestamp(float(_ts), tz=timezone.utc)
                elif isinstance(_ts, str):
                    _msg_dt = datetime.fromisoformat(_ts)
                    if _msg_dt.tzinfo is None:
                        _msg_dt = _msg_dt.replace(tzinfo=timezone.utc)
                else:
                    _msg_dt = datetime.now(timezone.utc)
                _ev = news_state.parse_fx_message(text, _msg_dt)
                if _ev:
                    if news_state.add_event(_ev):
                        log.info(f"[NEWS] {_ev['importance']} event @ {_ev['event_time'].isoformat()} — {_ev['text'][:60]}")
                        try:
                            brain_journal.write(
                                "news_observed", "TG",
                                {
                                    "importance": _ev['importance'],
                                    "event_time": _ev['event_time'].isoformat(),
                                    "received_at": _ev['received_at'].isoformat(),
                                    "text": _ev['text'][:200],
                                },
                                snapshot=brain_journal.build_snapshot(price, account, sig_state),
                            )
                        except Exception:
                            pass
                        if _ev['importance'] == 'HIGH':
                            try:
                                notify('news_alert', f"📰 HIGH news in {int((_ev['event_time']-datetime.now(timezone.utc)).total_seconds()/60)}min · {_ev['text'][:80]} — new entries blocked")
                            except Exception:
                                pass
                else:
                    log.info(f"[TG] Data channel msg (non-news) — ignored")
            except Exception as _ne:
                log.warning(f"[TG] news parse error: {_ne}")
            continue

        # LOCAL REGEX first (instant, no CLI)
        interp = interpret_tg_local(text, channel)
        if interp:
            log.info(f"[TG] Local regex match: {interp['type']} conf={interp['confidence']}")
        else:
            # Fallback: Claude CLI INTERPRETER for ambiguous messages.
            # Include current trade context so the LLM can disambiguate things
            # like bare numbers ("4820" means MOVE_SL only if a trade is open).
            current_trade_ctx = None
            try:
                if sig_state and sig_state.is_active():
                    current_trade_ctx = {
                        "direction": sig_state.get("direction"),
                        "entry_price": sig_state.get("entry_price"),
                        "blend": sig_state.get("entry_price"),  # best available proxy
                        "floating_usd": None,  # not easily accessible here
                    }
            except Exception:
                pass
            log.info(f"[TG] No regex match, calling Claude INTERPRETER...")
            interp = call_interpreter(text, channel, current_trade=current_trade_ctx)
        if not interp:
            log.warning(f"[TG] Interpreter returned nothing, skipping")
            continue

        msg_type = interp.get('type', 'OTHER')
        conf = interp.get('confidence', 0)
        log.info(f"[TG] Interpreted as {msg_type} (conf={conf}) — {interp.get('raw_summary', '')}")

        if conf < 0.6:
            log.info(f"[TG] Low confidence ({conf}), skipping")
            continue

        # ── Handle by type ──
        # tg_follow_enabled is the master gate. When OFF, the brain operates
        # COMPLETELY autonomously and TG channels are TREATED AS LOGS ONLY:
        # no OPEN, no CLOSE, no MOVE_SL trigger any action. Earlier design
        # bypassed this for CLOSE/MOVE_SL ("user needs to manage TG-opened
        # trades") but that broke isolation when running autonomous: a TG
        # cerramos closed an autonomous BUY that wasn't even ours to close
        # (incident 2026-04-27 09:54).
        _tg_follow = True
        try:
            _ctrl_path = os.path.join(COMMON, 'brain_controls.json')
            if os.path.exists(_ctrl_path):
                with open(_ctrl_path, 'r', encoding='utf-8') as _cf:
                    _tg_follow = bool(json.load(_cf).get('tg_follow_enabled', True))
        except Exception:
            _tg_follow = True

        if msg_type == 'OPEN':
            if not _tg_follow:
                log.info(f"[TG] OPEN {interp.get('direction','?')} @ {interp.get('entry_price','?')} ignored — tg_follow_enabled=OFF")
                continue
            handle_open_signal(interp, channel, sig_state, engine, bars_cache, account, price)

        elif msg_type == 'MOVE_SL':
            if not _tg_follow:
                log.info(f"[TG] MOVE_SL from {channel} ignored — tg_follow_enabled=OFF")
                continue
            handle_move_sl(interp, sig_state, tg_channel=channel)

        elif msg_type == 'CLOSE':
            if not _tg_follow:
                log.info(f"[TG] CLOSE 'cerramos' from {channel} ignored — tg_follow_enabled=OFF (autonomous mode)")
                try:
                    notify('dd_alert',
                           f"🛡 TG cerramos de `{channel}` ignorat — tg_follow=OFF, operativa autònoma intacta.")
                except Exception:
                    pass
                continue
            handle_close(interp, sig_state, tg_channel=channel)

        elif msg_type == 'NEWS':
            imp = interp.get('news_importance', 'LOW')
            mins = interp.get('minutes_until')
            log.info(f"[TG] NEWS {imp} in {mins} min — just logged, no action yet")


def handle_open_signal(interp, channel, sig_state, engine, bars_cache, account, price):
    """Handle an OPEN signal from TG. Calls FILTER, then opens trade if TAKE."""
    direction = interp.get('direction')
    entry = interp.get('entry_price')

    if direction not in ('BUY', 'SELL'):
        log.warning(f"[OPEN] Invalid direction: {direction}")
        return

    # Session gate — block NEW entries if current session is disabled in config.
    # Existing trades keep being managed normally.
    try:
        if not news_state.is_session_enabled():
            _sess = news_state.session_label()
            log.warning(f"[OPEN] BLOCKED — session {_sess} disabled in config.sessions_enabled")
            try:
                brain_journal.write(
                    "signal_filter_blocked", "BRAIN",
                    {
                        "gate": "session_disabled",
                        "session": _sess,
                        "direction": direction,
                        "channel": channel,
                        "entry_signal_price": entry,
                    },
                    snapshot=brain_journal.build_snapshot(price, account, sig_state),
                )
            except Exception:
                pass
            try:
                notify('signal_received',
                       f"⛔ Signal {direction} from {channel} ignored — session {_sess} disabled")
            except Exception:
                pass
            return
    except Exception as _se:
        log.warning(f"[OPEN] session gate check failed ({_se}) — proceeding")

    # News gate — block NEW entries when a HIGH-impact event is within 30min
    # (or has just fired and we're in its post-window). Existing trades are
    # NOT affected — Executor/FAST keep managing averaging, trailing, exits.
    try:
        _hi = news_state.high_impact_within(30)
        if _hi:
            _now = datetime.now(timezone.utc)
            _delta_min = (_hi['event_time'] - _now).total_seconds() / 60.0
            if _delta_min >= 0:
                _when = f"in {int(_delta_min)}min"
            else:
                _when = f"{int(-_delta_min)}min ago (live)"
            log.warning(f"[OPEN] BLOCKED — HIGH news {_when}: {_hi['text'][:80]}")
            try:
                brain_journal.write(
                    "signal_filter_blocked", "BRAIN",
                    {
                        "gate": "news_high_impact",
                        "direction": direction,
                        "channel": channel,
                        "entry_signal_price": entry,
                        "news_event_time": _hi['event_time'].isoformat(),
                        "news_importance": _hi.get('importance'),
                        "news_text": _hi.get('text', '')[:200],
                        "delta_min": round(_delta_min, 1),
                    },
                    snapshot=brain_journal.build_snapshot(price, account, sig_state),
                )
            except Exception:
                pass
            try:
                notify('signal_received',
                       f"⛔ Signal {direction} from {channel} ignored — HIGH news {_when}\n"
                       f"{_hi['text'][:120]}")
            except Exception:
                pass
            return
    except Exception as _ge:
        log.warning(f"[OPEN] news gate check failed ({_ge}) — proceeding")

    # Conflict check: already have an active signal
    if sig_state.is_active():
        existing_dir = sig_state.get('direction')
        if existing_dir == direction:
            log.info(f"[OPEN] Already active same-direction signal, ignoring duplicate")
        else:
            log.warning(f"[OPEN] Conflicting direction (current={existing_dir}, new={direction}), ignoring")
        return

    # Direct entry — no FILTER CLI. Trust the signal from the channel.
    # NO INDIVIDUAL SL — only safety net is EA's 3.5% DD auto-close.
    # v3.2: initial lot = base_lot × initial_multiplier (default 2x). Single
    # risk lever in config.yaml. `calculate_initial_lot()` kept for reference
    # but no longer used for TG entries.
    atr_m1 = atr(bars_cache, 14) or 5.0
    try:
        _sz = (_load_app_config().get('sizing') or {})
        _base_lot_open = float(_sz.get('base_lot', 0.03))
        _init_mult = int(_sz.get('initial_multiplier', 2))
        lot = max(0.01, min(0.50, round(_base_lot_open * _init_mult, 2)))
    except Exception as _e:
        log.warning(f"[OPEN] sizing config load failed ({_e}), falling back to ATR formula")
        lot = max(0.01, min(0.50, round(calculate_initial_lot(account.get('balance', 0), atr_m1), 2)))
    entry_price = entry or price  # tracked as reference for sig_state

    log.info(f"[OPEN] DIRECT entry: {direction} {lot} @ market={price:.2f} (signal_entry={entry_price:.2f}) ATR={atr_m1:.2f} — NO SL (3.5% DD is hard stop)")

    if PAPER_MODE:
        log.info(f"[OPEN][PAPER] WOULD execute {direction} {lot} — blocked by PAPER_MODE")
        sig_state.open_signal(direction, entry_price, channel, lot, start_balance=account.get('balance', 0))
    else:
        sent = send_market(direction, lot, f"OPEN_{channel}", sl=0, tp=0)
        if sent:
            sig_state.open_signal(direction, entry_price, channel, lot, start_balance=account.get('balance', 0))
            log.info(f"[OPEN] EXECUTED {direction} {lot} @ ~{price:.2f} (channel={channel})")
            trade_history.log_event(
                type='OPEN', direction=direction, lot=lot, price=price,
                source='TG', reason=f'Signal from {channel} @ {entry_price:.2f}'
            )
        else:
            log.error(f"[OPEN] send_market FAILED")


def load_staged_setup():
    """Backward-compat: retorna el primer staged_setup viu (si n'hi ha cap).
    Per a multi-setup parallel, vegi `load_all_staged_setups()`.
    """
    setups = load_all_staged_setups()
    return setups[0] if setups else None


def load_all_staged_setups():
    """2026-05-05: Carrega TOTS els staged_setups vius (lista).

    El fitxer brain_staged_setups.json conté `setups: [...]` (plural) gestionat
    per staged_setups.py. Aquesta funció és el punt d'entrada del FastEngine
    per iterar i comprovar cada setup independentment — permet múltiples
    setups paral·lels (e.g., reversion + breakout al mateix nivell, o setups
    a diferents nivells).
    """
    try:
        with open(STAGED_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # Format actual (multi-setup): {"setups": [...], "updated_at": ...}
        setups = data.get('setups') or []
        # Format legacy (single-setup): {"setup": {...}}
        if not setups and data.get('setup'):
            setups = [data.get('setup')]
        # Filter expired (per `expires_at` legacy o `staged_at + expiration_minutes`)
        now = time.time()
        alive = []
        for s in setups:
            if not isinstance(s, dict):
                continue
            # Expiry check: si tenim expires_at, comprovem-lo. Si tenim
            # staged_at + expiration_minutes, calculem.
            expired = False
            if s.get('expires_at'):
                expired = float(s.get('expires_at', 0)) < now
            elif s.get('staged_at') and s.get('expiration_minutes'):
                age_s = now - float(s.get('staged_at', now))
                expired = age_s > (float(s.get('expiration_minutes', 30)) * 60)
            if expired:
                continue
            alive.append(s)
        return alive
    except Exception:
        return []


def save_staged_setup(setup):
    try:
        with open(STAGED_FILE, 'w', encoding='utf-8') as f:
            json.dump({'setup': setup, 'updated': datetime.now(timezone.utc).isoformat()}, f, indent=2)
    except Exception as e:
        log.warning(f"save_staged_setup failed: {e}")


def clear_staged_setup(setup_id=None):
    """2026-05-05: Esborra UN setup específic (per id) o TOTS si setup_id=None.

    En multi-setup mode, una invalidació individual NO ha de tombar la
    resta de setups paral·lels. Per tant cal sempre passar setup_id quan
    es marca una zona com a invalida; només neteja-ho TOT quan el sistema
    acaba de fire (i el cleanup parallel del try_fire_staged_setup ho farà).
    """
    try:
        import staged_setups as _ss
        if setup_id:
            current = _ss.load() or []
            kept = [s for s in current if s.get('id') != setup_id]
            _ss.save(kept)
        else:
            # Legacy/explicit "clear all"
            _ss.save([])
    except Exception:
        try:
            # Fallback de last resort
            with open(STAGED_FILE, 'w', encoding='utf-8') as f:
                json.dump({'setups': [], 'updated_at': datetime.now(timezone.utc).isoformat()}, f, indent=2)
        except Exception:
            pass


# Cooldown between SIGNAL_CLOSE and the next autonomous staged-setup fire.
# Prevents the "spam" pattern of closing + immediately re-opening (often in the
# opposite direction), which turned a profitable scalping day into a risk
# nightmare on 2026-04-23.
STAGING_POST_CLOSE_COOLDOWN_S = 60   # 2026-05-05: reduït de 300 → 60s. 5min era
                                      # massa per scalping. 60s = settlement time
                                      # de l'EA + un breve respir per evitar wash trades.
                                      # Si el LLM proposa nou setup just després del
                                      # close i el FastEngine confirma, dispara.
                                      # staged setup can fire
_LAST_SIGNAL_CLOSE_TS = 0.0
_LAST_COOLDOWN_LOG_TS = 0.0


def _set_last_signal_close_ts():
    global _LAST_SIGNAL_CLOSE_TS
    _LAST_SIGNAL_CLOSE_TS = time.time()


def _try_fire_breakout(setup, sig_state, bars, account, price, zp, direction, cur_atr):
    """Lògica fire per a setups de tipus 'breakout'.

    Confirmation = bar M5/M15 close BEYOND zone amb vol elevat.
    Direction:
      BUY  breakout: bar close > zp + buffer (preu ha tancat sobre la resistència)
      SELL breakout: bar close < zp - buffer (preu ha tancat sota el suport)

    Camps del setup:
      breakout_tf:        'M5' | 'M15' (default M5 — més en línia amb el ritme real de l'or)
      breakout_vol_min:   ratio mínim vs avg vol (default 1.5)
      breakout_buffer_usd: marge sobre la zona perquè wicks no comptin (default 0.5)
    """
    tf = setup.get('breakout_tf', 'M5')
    vol_min = float(setup.get('breakout_vol_min', 1.5))
    buffer_usd = float(setup.get('breakout_buffer_usd', 0.5))

    # ── 2026-05-05: INVALIDACIÓ DETERMINISTA ELIMINADA (breakout) ──
    # Mateix tractament que reversion: la invalidació qualitativa la fa el LLM
    # via auto_close_conditions + breach detection (M5 close → review).
    # Safety net ultra-ampli només per evitar setups zombi de break que ja no
    # tenen sentit (preu allunyat 3× ATR sense confirmar break).
    _setup_age_s = max(0, time.time() - float(setup.get('staged_at', 0) or 0))
    if _setup_age_s > 300:  # >5 min armat
        _safety_dist = max(15.0, cur_atr * 3.0)
        if direction == 'BUY' and price < zp - _safety_dist:
            log.warning(f"[STAGED-BREAKOUT] SAFETY-NET DROP — preu {price:.2f} extremadament lluny del BUY breakout zone {zp:.2f} sense confirmar break (∆ -{zp-price:.1f}$ > {_safety_dist:.1f}$). LLM no ha invalidat en {_setup_age_s:.0f}s.")
            clear_staged_setup(setup_id=setup.get('id'))
            return False
        if direction == 'SELL' and price > zp + _safety_dist:
            log.warning(f"[STAGED-BREAKOUT] SAFETY-NET DROP — preu {price:.2f} extremadament lluny del SELL breakout zone {zp:.2f} sense confirmar break (∆ +{price-zp:.1f}$ > {_safety_dist:.1f}$). LLM no ha invalidat en {_setup_age_s:.0f}s.")
            clear_staged_setup(setup_id=setup.get('id'))
            return False

    # ── INVALIDATION: zone no longer in indicator zones ──
    try:
        zones_data = load_zones()
        current_zones = zones_data.get('reversal_zones', [])
        still_exists = False
        for z in current_zones:
            z_price = z.get('price', 0)
            if abs(z_price - zp) <= 3.0:
                still_exists = True
                break
        if current_zones and not still_exists:
            log.info(f"[STAGED-BREAKOUT] INVALIDATED — zone {zp:.2f} no longer in indicator zones")
            clear_staged_setup()
            return False
    except Exception:
        pass

    # ── Avaluem la barra tancada del TF requerit ──
    if tf == 'M5':
        bars_tf = aggregate_bars(bars, 5)
    elif tf == 'M15':
        bars_tf = aggregate_bars(bars, 15)
    else:
        bars_tf = bars  # M1 fallback (raríssim)
    if not bars_tf or len(bars_tf) < 2:
        return False

    # Última barra TANCADA (bars_tf[-1] pot ser la que s'està formant — agafem [-2] si M5/M15)
    # Per simplicitat, usem [-1] que és la més recent; si vol confirmació estricte, [-2]
    last_closed = bars_tf[-1] if len(bars_tf) >= 1 else None
    if not last_closed:
        return False

    bar_close = float(last_closed.get('close', 0) or 0)
    bar_vol = float(last_closed.get('volume', 0) or 0)
    if bar_close <= 0:
        return False

    # ── Confirmation breakout: close beyond zone amb buffer ──
    if direction == 'BUY':
        # BUY breakout: close per sobre zone + buffer
        ok_close = bar_close >= zp + buffer_usd
    else:
        # SELL breakout: close per sota zone - buffer
        ok_close = bar_close <= zp - buffer_usd

    # ── Volum check ──
    avg_vol = 0
    if len(bars_tf) >= 21:
        vols = [float(b.get('volume', 0) or 0) for b in bars_tf[-21:-1]]
        if vols:
            avg_vol = sum(vols) / len(vols)
    vol_ratio_val = (bar_vol / avg_vol) if avg_vol > 0 else 0
    ok_vol = vol_ratio_val >= vol_min

    # ── Log monitor cada 5s quan preu prop de zona ──
    dist_to_zone = price - zp
    if abs(dist_to_zone) <= 5.0:
        _now = time.time()
        if (_now - getattr(_try_fire_breakout, '_last_monitor_log', 0)) > 5:
            _try_fire_breakout._last_monitor_log = _now
            _verdict = "🔥 BREAKOUT FIRE!" if (ok_close and ok_vol) else "WAIT"
            log.info(
                f"[LEVEL-BREAKOUT] {direction}@{zp:.1f} ({tf}) | preu={price:.2f} | "
                f"bar_close={bar_close:.2f} (vs {zp+buffer_usd if direction=='BUY' else zp-buffer_usd:.2f}) | "
                f"vol={vol_ratio_val:.1f}x (need {vol_min:.1f}x) | "
                f"close={'✓' if ok_close else '✗'} vol={'✓' if ok_vol else '✗'} → {_verdict}"
            )

    if not (ok_close and ok_vol):
        return False

    # ── FIRE ──
    # 2026-05-07: Lot fix 0.25 (decisió usuari — dimensionat al capital).
    lot = 0.25
    target = (setup.get('tp_target') or setup.get('target_price')
              or setup.get('profit_target') or 0)
    try:
        target = float(target or 0)
    except Exception:
        target = 0
    z_strength = setup.get('zone_strength', '?')
    comment = f"BREAKOUT_{z_strength}_{int(zp)}_{tf}_v{vol_ratio_val:.1f}"

    if has_pending_order():
        return False

    # News + session gates (igual que reversion)
    try:
        _hi = news_state.high_impact_within(30)
        if _hi:
            log.info(f"[STAGED-BREAKOUT] BLOCKED — HIGH news pending: {_hi['text'][:80]}")
            return False
    except Exception:
        pass
    try:
        if not news_state.is_session_enabled():
            log.info(f"[STAGED-BREAKOUT] BLOCKED — session disabled")
            return False
    except Exception:
        pass

    log.info(
        f"[STAGED-BREAKOUT] 🔥 FIRING {direction} {lot} @ {price:.2f} "
        f"(zone {zp} {tf}, close {bar_close:.2f}, vol {vol_ratio_val:.1f}x)"
    )

    if PAPER_MODE:
        log.info(f"[STAGED-BREAKOUT][PAPER] WOULD execute")
        clear_staged_setup()
        return True

    # 2026-05-06: TP=0 al broker. LADDER gestiona tots els tancaments parcials.
    sent = send_market(direction, lot, comment, sl=0, tp=0)
    if not sent:
        log.error(f"[STAGED-BREAKOUT] send_market FAILED")
        return False

    # Persistir state com a una entrada normal
    sig_state.open_signal(direction, price, 'AUTONOMOUS', lot,
                          start_balance=account.get('balance', 0))
    try:
        sig_state._data['tp_price'] = float(target) if target else 0.0
        _auto_close = setup.get('auto_close_conditions') or []
        # 2026-05-06 BUG FIX: profit_targets són dicts complets, no floats
        _pt_normalized_b = []
        for p in (setup.get('profit_targets') or []):
            if not p: continue
            if isinstance(p, dict):
                _pt_normalized_b.append(p)
            else:
                try:
                    _pt_normalized_b.append({'price': float(p), 'close_pct': 0, 'reasoning': ''})
                except Exception:
                    pass
        _exec_plan = {
            'profit_targets':       _pt_normalized_b,
            'averaging_zones':      [],
            'tactical_plan':        '',
            'play_type':            'breakout',
            'staged_at':            time.time(),
            'auto_close_conditions': _auto_close,
            'trap_thesis':           str(setup.get('trap_thesis') or ''),
            'tp_thesis':             str(setup.get('tp_thesis') or ''),
            'invalidation_thesis':   str(setup.get('invalidation_thesis') or ''),
            'tp_target':             float(target) if target else 0.0,
            'entry_price':           float(price),
            'mode':                  'institutional_recorregut' if _auto_close else 'legacy',
            'setup_type':            'breakout',
        }
        sig_state._data['executor_plan'] = _exec_plan
        sig_state.save()
        log.info(f"[STAGED-BREAKOUT] 🎯 RECORREGUT-BREAKOUT armed: tp={target}, {len(_auto_close)} auto_close_conditions")
        # Multi-TP ladder si profit_targets present
        if _exec_plan['profit_targets']:
            try:
                import executor_ladder as _el
                _el.init_from_signal(setup.get('profit_targets'), direction, entry_price=price)
                log.info(f"[STAGED-BREAKOUT] 🪜 Multi-TP ladder armat: {_exec_plan['profit_targets']}")
            except Exception as _le:
                log.warning(f"[STAGED-BREAKOUT] ladder init failed: {_le}")
    except Exception as _e:
        log.warning(f"[STAGED-BREAKOUT] Failed to persist executor_plan: {_e}")

    clear_staged_setup()
    log.info(f"[STAGED-BREAKOUT] EXECUTED {direction} {lot} — setup consumed")
    trade_history.log_event(
        type='OPEN', direction=direction, lot=lot, price=price,
        source='AUTONOMOUS_BREAKOUT',
        reason=f'Breakout fired @ zone {zp} ({tf} close {bar_close:.2f}, vol {vol_ratio_val:.1f}x)'
    )
    _force_executor_review(f'breakout_fired {direction}@{zp:.1f} (lot {lot})')
    return True


def try_fire_staged_setup(sig_state, bars, account, price):
    """Called by FAST engine path: if staged setup exists and market confirms, execute MARKET order.

    Conditions to fire:
    - Setup exists and not expired/invalidated
    - No active signal (we don't overlap)
    - Price is within tolerance of zone
    - Candle shows rejection (HAMMER/STRONG_BEAR/etc.) OR strong wick against direction
    - Volume ≥ 1.3x

    INVALIDATION checks (clear setup):
    - Price broke zone decisively in wrong direction
    - Zone no longer exists in current INDICATOR zones (structure changed)
    - Time expired (safety cap)
    """
    if sig_state.is_active():
        return False

    # Post-close cooldown: after a signal close, wait STAGING_POST_CLOSE_COOLDOWN_S
    # before firing any staged setup. Prevents rapid open→close→open chains.
    now = time.time()
    if _LAST_SIGNAL_CLOSE_TS > 0 and (now - _LAST_SIGNAL_CLOSE_TS) < STAGING_POST_CLOSE_COOLDOWN_S:
        global _LAST_COOLDOWN_LOG_TS
        remaining = int(STAGING_POST_CLOSE_COOLDOWN_S - (now - _LAST_SIGNAL_CLOSE_TS))
        if (now - _LAST_COOLDOWN_LOG_TS) > 60:  # log at most once per minute
            log.info(f"[STAGED] post-close cooldown active, {remaining}s remaining")
            _LAST_COOLDOWN_LOG_TS = now
        return False

    # 2026-05-05: MULTI-SETUP. Carrega TOTS els setups vius i avalua cada un.
    # Si un dispara, els altres es netegen (només una posició al broker).
    all_setups = load_all_staged_setups()
    if not all_setups:
        return False

    # Itera per cada setup. El primer que confirmi → fire. La resta es netegen.
    for setup in all_setups:
        direction = setup.get('direction')
        zp = setup.get('zone_price', 0)
        if not direction or not zp:
            continue

        setup_type = setup.get('setup_type', 'reversion')
        sid = setup.get('id', f'{direction}@{zp}')

        # Cada setup té la seva pròpia avaluació segons setup_type
        fired = _evaluate_and_fire_single(setup, sig_state, bars, account, price)
        if fired:
            # Setup ha disparat → netejem la resta
            try:
                import staged_setups as _ss
                remaining = [s for s in (_ss.load() or []) if s.get('id') != sid]
                _ss.save(remaining)
                if remaining:
                    log.info(f"[STAGED] {sid} ha disparat — eliminem {len(remaining)} setup(s) alternatiu(s)")
            except Exception as _ce:
                log.warning(f"[STAGED] cleanup post-fire failed: {_ce}")
            return True
    return False


def _evaluate_and_fire_single(setup, sig_state, bars, account, price):
    """Avalua i dispara UN setup individual. Helper de try_fire_staged_setup."""
    direction = setup.get('direction')
    zp = setup.get('zone_price', 0)
    if not direction or not zp:
        return False

    setup_type = setup.get('setup_type', 'reversion')
    entry_mode = (setup.get('entry_mode') or 'instant').lower()

    cur_atr = atr(bars, 14) or 5.0
    # 2026-05-05: tolerància depèn del mode d'entrada.
    # INSTANT: el toc del nivell és el trigger. Tolerància TIGHT ($0.5)
    #   per evitar entrar abans d'arribar al nivell. Amb SL $3-5, una
    #   tolerància de $4 mataria el R/R abans de començar.
    # CONFIRMED: esperem candela + vol. Tolerància més ampla per acceptar
    #   wicks que han tocat la zona i tornen (rebot violent ja consumat).
    if entry_mode == 'instant':
        tolerance = 0.5  # tight — disparem al toc real
        wick_margin = 0.3
    else:
        tolerance = max(3.5, cur_atr * 0.6)  # ampla per a confirmed
        wick_margin = 1.5

    # ══════════════════════════════════════════════════════════════════════
    # BREAKOUT setup — lògica separada
    # ══════════════════════════════════════════════════════════════════════
    if setup_type == 'breakout':
        return _try_fire_breakout(setup, sig_state, bars, account, price, zp, direction, cur_atr)

    # ══════════════════════════════════════════════════════════════════════
    # REVERSION setup (default) — lògica clàssica
    # ══════════════════════════════════════════════════════════════════════

    # ── 2026-05-05: INVALIDACIÓ DETERMINISTA ELIMINADA ──
    # Abans aquí havia 3 capes de regles per distància (zone breached,
    # target already reached, train missed). Eren regles deterministes que
    # decidien per pròpia compta. Ara la invalidació és qualitativa via:
    #   1. auto_close_conditions de l'EXECUTOR (LLM-pre-aprovat per al setup)
    #   2. evaluate_breach_m5 al M5 close detector → invoca EXECUTOR review
    #      immediatament en breach, deixant que el LLM decideixi
    #   3. Safety net: 4 M5 bars de grace si el LLM no respon
    #   4. expiration_minutes definit per l'EXECUTOR per cada setup
    # SAFETY NET FINAL ULTRA-AMPLI (només per evitar setups zombi extrems):
    # Si el preu s'ha allunyat MÉS DE 3× ATR de la zona en direcció contrària
    # I el setup està armat fa més de 5 minuts, dropejem com a últim recurs.
    _setup_age_s = max(0, time.time() - float(setup.get('staged_at', 0) or 0))
    if _setup_age_s > 300:  # >5 min armat
        _safety_dist = max(15.0, cur_atr * 3.0)
        if direction == 'BUY' and price < zp - _safety_dist:
            log.warning(f"[STAGED] SAFETY-NET DROP — price {price:.2f} extremely far below BUY zone {zp:.2f} ({zp-price:.1f}$ > {_safety_dist:.1f}$ × {_setup_age_s:.0f}s armat). LLM no ha invalidat.")
            clear_staged_setup(setup_id=setup.get('id'))
            return False
        if direction == 'SELL' and price > zp + _safety_dist:
            log.warning(f"[STAGED] SAFETY-NET DROP — price {price:.2f} extremely far above SELL zone {zp:.2f} ({price-zp:.1f}$ > {_safety_dist:.1f}$ × {_setup_age_s:.0f}s armat). LLM no ha invalidat.")
            clear_staged_setup(setup_id=setup.get('id'))
            return False

    # ── INVALIDATION 2: Zone no longer in current indicator zones (structure change) ──
    # 2026-05-05 FIX: 3 problemes resolts:
    #   1) clear_staged_setup() sense setup_id esborrava TOTS els setups paral·lels
    #   2) z_bounce == direction era massa estricte: l'EXECUTOR pot armar BUY a una
    #      zona que l'indicator marca RESISTANCE/SELL (failed-break / sweep+reverse).
    #      Un nivell estructural no perd validesa pel canvi de polaritat assignada.
    #   3) Per a entry_mode='wick', l'invalidació pròpia (TICK breach + 30s sostingut)
    #      té prioritat: no podem pre-emptar el wick evaluator amb un check estructural.
    if entry_mode != 'wick':
        try:
            zones_data = load_zones()
            current_zones = zones_data.get('reversal_zones', [])
            # Look for a zone near zp (within $3.5) — proximity only, ignorem polaritat.
            still_exists = False
            for z in current_zones:
                z_price = z.get('price', 0)
                if abs(z_price - zp) <= 3.5:
                    still_exists = True
                    break
            if current_zones and not still_exists:
                log.info(f"[STAGED] INVALIDATED — zone {zp:.2f} {direction} no longer near any current indicator zone (structure changed)")
                clear_staged_setup(setup_id=setup.get('id'))
                return False
        except Exception:
            pass

    # NEW 2026-05-04: LEVEL MONITOR — log micro-status cada cycle quan
    # preu prop de la zona staged. Throttle 5s perquè no inundi.
    dist_to_zone = price - zp  # signed: + significa preu sobre zone
    last = bars[-1]
    c = candle_type(last)
    cur_vol = vol_ratio(bars)
    closes = [b['close'] for b in bars]
    cur_rsi = rsi(closes, 14) or 50

    ok_candle = False
    if direction == 'SELL':
        ok_candle = c in ('STRONG_BEAR', 'SHOOT_STAR', 'INV_HAMMER') or (c == 'BEAR' and cur_rsi > 65)
    else:
        ok_candle = c in ('STRONG_BULL', 'HAMMER') or (c == 'BULL' and cur_rsi < 35)
    ok_vol = cur_vol >= 1.3

    # Tolerància doble (2026-05-04) — l'usuari va detectar la incoherència:
    #  - "current_in_tolerance" = preu ARA dins ±tolerance de la zona (cas tranquil: preu hi és)
    #  - "wick_touched_zone"    = la M1 tancada té wick que ha tocat la zona (cas rebuig violent:
    #    el preu ja ha fugit, però el rebuig amb volum demostra que era vàlid)
    # Si CAP de les dues, encara no estem en mode "valoració" — WAIT.
    current_in_tolerance = abs(price - zp) <= tolerance
    wick_touched_zone = False
    if direction == 'SELL':
        # Per SELL: la M1 hauria d'haver tocat la zona per dalt amb wick_margin de marge
        wick_touched_zone = float(last.get('high', 0)) >= (zp - wick_margin)
    else:
        # Per BUY: la M1 hauria d'haver tocat la zona per baix amb wick_margin de marge
        wick_touched_zone = float(last.get('low', 1e9)) <= (zp + wick_margin)
    in_tolerance = current_in_tolerance or wick_touched_zone

    # Log monitoring cada 5s quan preu < 5$ de la zona
    if abs(dist_to_zone) <= 5.0:
        _now = time.time()
        if (_now - getattr(try_fire_staged_setup, '_last_monitor_log', 0)) > 5:
            try_fire_staged_setup._last_monitor_log = _now
            _verdict = "🔥 FIRE!" if (in_tolerance and ok_candle and ok_vol) else (
                "CLOSE" if in_tolerance else f"WAIT (dist {dist_to_zone:+.2f}$)"
            )
            _tol_mark = '✓' if in_tolerance else '✗'
            if in_tolerance and not current_in_tolerance and wick_touched_zone:
                _tol_mark = '✓(wick)'  # senyal: cas rebuig consumat, preu ja ha fugit
            log.info(
                f"[LEVEL] {direction}@{zp:.1f} | preu={price:.2f} ({dist_to_zone:+.2f}$) | "
                f"candle={c} RSI={cur_rsi:.0f} | vol={cur_vol:.1f}x | "
                f"rejection={'✓' if ok_candle else '✗'} "
                f"vol={'✓' if ok_vol else '✗'} "
                f"tol={_tol_mark} "
                f"→ {_verdict}"
            )

    # 2026-05-06: ENTRY MODE = "wick" — abans del check de tolerància normal,
    # el mode wick té la seva pròpia lògica de monitorització + 1 sola
    # oportunitat (estricta, anti-stop-hunt).
    _entry_mode = (setup.get('entry_mode') or 'instant').lower()
    if _entry_mode == 'wick':
        # ── 2026-05-06: TICK-BASED INVALIDATION per a wick setups ──
        # Si el preu actual passa la zona significativament I es manté allà,
        # invalidem AL MOMENT (sense esperar bar close). Això és l'invariant
        # de la teva idea: "si entra a zona i no rebot, fora".
        TICK_INVAL_BUFFER = 1.5   # $1.5 més enllà del nivell
        TICK_INVAL_SUSTAIN_S = 30.0
        try:
            tick_breach = False
            if direction == 'SELL':
                # Per SELL: preu passa per sobre del nivell + buffer
                tick_breach = price > (zp + TICK_INVAL_BUFFER)
            elif direction == 'BUY':
                # Per BUY: preu passa per sota del nivell - buffer
                tick_breach = price < (zp - TICK_INVAL_BUFFER)
            now_ts = time.time()
            if tick_breach:
                first_ts = setup.get('_wick_tick_breach_first_ts')
                if first_ts is None:
                    setup['_wick_tick_breach_first_ts'] = now_ts
                    # Save state
                    try:
                        import staged_setups as _ss_save
                        current = _ss_save.load() or []
                        for i, s in enumerate(current):
                            if s.get('id') == setup.get('id'):
                                current[i] = setup
                                _ss_save.save(current)
                                break
                    except Exception:
                        pass
                else:
                    elapsed = now_ts - float(first_ts)
                    if elapsed >= TICK_INVAL_SUSTAIN_S:
                        log.warning(
                            f"[STAGED][WICK] 🗑 INVALIDAT TICK-BASED — preu {price:.2f} "
                            f"travessa {direction} zone {zp:.2f} per {abs(price-zp):.2f}$ "
                            f"sostingut {elapsed:.0f}s sense wick rejection. "
                            f"Setup eliminat (1 oportunitat consumida)."
                        )
                        try:
                            notify('staged_executor',
                                   f"❌ SETUP INVALIDAT @ ${zp:.2f}\n"
                                   f"📍 Preu va creuar el nivell sense rebot {direction}")
                        except Exception:
                            pass
                        # 2026-05-07: Blacklist desactivat (decisió usuari).
                        # No bloquegem re-staging; l'EXECUTOR decideix.
                        clear_staged_setup(setup_id=setup.get('id'))
                        return False
            else:
                # Reset si torna a estar dins (preu entra al wick zone)
                if setup.get('_wick_tick_breach_first_ts') is not None:
                    setup['_wick_tick_breach_first_ts'] = None
        except Exception as _ti_e:
            log.debug(f"[WICK] tick invalidation check failed: {_ti_e}")

        verdict, wick_sl = _wick_setup_evaluator(setup, bars, cur_atr)
        if verdict == 'wait':
            # Continua monitoritzant, no fa res aquest cycle
            # Salvem el setup amb el _wick_last_evaluated_ts actualitzat
            try:
                import staged_setups as _ss_save
                current = _ss_save.load() or []
                for i, s in enumerate(current):
                    if s.get('id') == setup.get('id'):
                        current[i] = setup
                        _ss_save.save(current)
                        break
            except Exception:
                pass
            return False
        elif verdict == 'invalidate':
            # Una sola oportunitat — la barra va arribar i NO va mostrar wick → fora.
            log.warning(
                f"[STAGED][WICK] 🗑 INVALIDAT — primer toc de zona {zp:.2f} "
                f"sense rejection clara. Trade fora (1 sola oportunitat per disseny)."
            )
            try:
                notify('staged_executor',
                       f"❌ SETUP INVALIDAT @ ${zp:.2f}\n"
                       f"📍 La vela va arribar però sense patró de rebot ({direction})")
            except Exception:
                pass
            # 2026-05-07: Blacklist desactivat (decisió usuari).
            clear_staged_setup(setup_id=setup.get('id'))
            return False
        elif verdict == 'fire':
            # Wick rejection detectada → store el SL dinàmic per quan firem
            setup['_wick_dynamic_sl'] = wick_sl
            log.info(
                f"[STAGED][WICK] ✅ Wick rejection detectada @ {zp:.2f} "
                f"(SL dinàmic a {wick_sl:.2f}) — fire MARKET"
            )
            # Continuem cap al fire path (saltem el check ok_candle/ok_vol clàssic)
        else:
            return False

    # Distance check — preu actual O wick de la M1 tancada han d'haver tocat la zona
    # (només per a modes instant/confirmed; wick ja té el seu check)
    if _entry_mode != 'wick':
        if not in_tolerance:
            return False

    # 2026-05-05: ENTRY MODE — instant vs confirmed.
    # Filosofia nova de scalping: a zones STRONG, l'edge està al LEVEL no a
    # la candela. Esperar rejection_candle + vol fa que la meitat del
    # moviment ja s'hagi fet i el SL tight no aguanta el confirmation lag.
    # Per defecte: instant entry al toc. Confirmed només si l'LLM ho demana
    # explícitament (zones MODERATE o setups dubtosos).
    if _entry_mode == 'confirmed':
        if not (ok_candle and ok_vol):
            return False
    elif _entry_mode == 'wick':
        # Already handled above — saltem (la verificació ja està feta)
        pass
    elif _entry_mode == 'instant':
        # No esperem candela ni vol — el toc del nivell és el trigger.
        # El SL virtual via auto_close_conditions gestiona el risc.
        pass
    else:
        # Mode desconegut → fallback a confirmed per seguretat
        log.warning(f"[STAGED] entry_mode desconegut '{_entry_mode}' → fallback a confirmed")
        if not (ok_candle and ok_vol):
            return False

    # Fire!
    # 2026-05-07: Lot fix 0.25 (decisió usuari — dimensionat al capital del compte).
    lot = 0.25
    # Mode Recorregut Institucional: tp_target té prioritat sobre target_price.
    # Si l'EXECUTOR proposa un destí estructural (tp_target), l'usem com TP al broker.
    target = (setup.get('tp_target') or setup.get('target_price')
              or setup.get('profit_target') or 0)
    try:
        target = float(target or 0)
    except Exception:
        target = 0
    z_strength = setup.get('zone_strength', '?')
    comment = f"STAGED_{z_strength}_{int(zp)}_{c[:4]}_v{cur_vol:.1f}"

    # Gate: don't fire if a previous order is still pending in the EA's queue.
    # Retry next tick when the EA has processed it.
    if has_pending_order():
        return False

    # News gate — same as TG OPEN. Existing trades unaffected; only NEW blocked.
    # 2026-05-06: SKIP per a wick mode. Si el patró de rebuig s'ha donat al
    # nivell, l'usuari ha estat explícit: "obrim". El wick rejection ja és
    # la confirmació; no anul·lem un patró comprovat per news pendent.
    if entry_mode != 'wick':
        try:
            _hi = news_state.high_impact_within(30)
            if _hi:
                log.info(f"[STAGED] BLOCKED — HIGH news pending: {_hi['text'][:80]}")
                return False
        except Exception:
            pass

    # Session gate — same logic as TG OPEN.
    try:
        if not news_state.is_session_enabled():
            log.info(f"[STAGED] BLOCKED — session {news_state.session_label()} disabled in config")
            return False
    except Exception:
        pass

    log.info(f"[STAGED] 🔥 FIRING {direction} {lot} @ {price:.2f} (zone {zp} {z_strength}, candle {c}, vol {cur_vol}x, rsi {cur_rsi:.0f})")

    if PAPER_MODE:
        log.info(f"[STAGED][PAPER] WOULD execute — blocked by PAPER_MODE")
        clear_staged_setup()
        return True

    # 2026-05-06: Broker TP = TP2 (últim nivell del LADDER) com a safety net
    # per moviments bruscos (gap, spike). El LADDER manté la prioritat:
    # quan el preu toca TP1, dispara PARTIAL_CLOSE_PCT 50% (TG: LADDER L1)
    # + MOVE_SL_ENTRY (TG: BE auto-set). Quan el preu arriba a TP2, el LADDER
    # dispara el segon 50% (TG: LADDER L2) i el broker TP tanca redundantment.
    # Si LADDER falla (race al EA), el broker TP captura el moviment brusc.
    # 2026-05-06 BUG fix anterior: tp=tp_target tancava 100% en un sol toc i
    # el LADDER no podia gestionar parcials separats. La solució ÉS aquesta:
    # el LADDER ha d'haver disparat ABANS que arribi a TP2.
    # 2026-05-07 BUG FIX: aplicar el shift (entry_real - zona_planificada)
    # al broker TP també. Sense això, broker TP es quedava al valor zona-relatiu
    # original i tancava el 100% abans que el LADDER L2 (post-shift) pogués
    # disparar — el LADDER ladrant a 4743 mentre broker TP tancava a 4741.
    _broker_tp = 0
    try:
        _pts = setup.get('profit_targets') or []
        if _pts:
            _broker_tp = float(_pts[-1].get('price', 0) or 0)
        if _broker_tp <= 0:
            _broker_tp = float(target or 0)
        # Aplicar el shift d'entry per mantenir el broker TP coherent amb el LADDER
        _zone_planned = float(setup.get('zone_price') or zp or 0)
        _entry_real = float(price or 0)
        _shift_tp = _entry_real - _zone_planned if (_zone_planned > 0 and _entry_real > 0) else 0
        if abs(_shift_tp) > 0.05 and _broker_tp > 0:
            _broker_tp = round(_broker_tp + _shift_tp, 2)
            log.info(f"[STAGED] 🔧 Broker TP shift {_shift_tp:+.2f}$ aplicat → ${_broker_tp:.2f}")
    except Exception:
        _broker_tp = float(target or 0)
    sent = send_market(direction, lot, comment, sl=0, tp=_broker_tp if _broker_tp > 0 else 0)
    if sent:
        sig_state.open_signal(direction, price, 'AUTONOMOUS', lot,
                              start_balance=account.get('balance', 0))
        try:
            sig_state._data['tp_price'] = float(target) if target else 0.0
            # Persist the Executor's tactical plan onto the active signal so
            # apply_trade_plan can honor profit_targets after the fire boundary
            # (the staged_setup is cleared right after this branch).
            _profit_targets = setup.get('profit_targets') or []
            _avg_zones      = setup.get('averaging_zones') or []
            _tactical       = setup.get('tactical_plan') or ''
            _play_type      = setup.get('play_type') or ''
            # ── 2026-05-04: nous camps Mode Recorregut Institucional ──
            _auto_close = setup.get('auto_close_conditions') or []
            _trap = setup.get('trap_thesis') or ''
            _tp_th = setup.get('tp_thesis') or ''
            _inv_th = setup.get('invalidation_thesis') or ''
            _tp_target = setup.get('tp_target') or target or 0
            # 2026-05-06 BUG FIX: profit_targets són dicts {price, close_pct, reasoning},
            # NO floats. Antic float(p) fallava amb TypeError matant tota la persistència
            # (auto_close, BE trigger, etc.) → trades obrien sense protecció.
            # averaging_zones — preservem si són floats, ignorem si són dicts.
            _pt_normalized = []
            for p in _profit_targets:
                if p is None: continue
                if isinstance(p, dict):
                    _pt_normalized.append(p)
                else:
                    try:
                        _pt_normalized.append({'price': float(p), 'close_pct': 0, 'reasoning': ''})
                    except Exception:
                        pass
            _avg_normalized = []
            for p in _avg_zones:
                if p is None: continue
                if isinstance(p, (int, float)):
                    _avg_normalized.append(float(p))
                # dicts de averaging zones legacy: ignored (no averaging in recorregut)
            # 2026-05-06: persistim el breakeven_trigger del LLM (era el que
            # faltava). El LLM proposa al setup "breakeven_trigger: {price, reasoning}"
            # i ara el guardem perquè el watcher/EXECUTOR el respectin.
            _be_trigger = setup.get('breakeven_trigger')
            # Si el setup és wick i tenim wick_dynamic_sl, l'incorporem al plan
            _wick_dynamic_sl = setup.get('_wick_dynamic_sl')
            # 2026-05-07: SL mínim $4 d'entry (1R fix).
            # Math estratègic consistent:
            #   - SL: $4 (1R risk)
            #   - TP1: +$4 (1R) → 50% close
            #   - TP2: +$8 (2R) → 50% close
            #   - Win avg: 0.5×$4 + 0.5×$8 = $6
            #   - Break-even: 40% win rate (loss $4 / (loss $4 + win $6))
            MIN_SL_DIST_FROM_ENTRY = 4.0
            if _wick_dynamic_sl and direction == 'SELL':
                _min_sl_sell = float(price) + MIN_SL_DIST_FROM_ENTRY
                if float(_wick_dynamic_sl) < _min_sl_sell:
                    log.info(f"[STAGED] SL ampliat: ${_wick_dynamic_sl:.2f} → ${_min_sl_sell:.2f} (mínim $4 d'entry, 1R)")
                    _wick_dynamic_sl = _min_sl_sell
            elif _wick_dynamic_sl and direction == 'BUY':
                _min_sl_buy = float(price) - MIN_SL_DIST_FROM_ENTRY
                if float(_wick_dynamic_sl) > _min_sl_buy:
                    log.info(f"[STAGED] SL ampliat: ${_wick_dynamic_sl:.2f} → ${_min_sl_buy:.2f} (mínim $4 d'entry, 1R)")
                    _wick_dynamic_sl = _min_sl_buy
            _entry_mode_used = (setup.get('entry_mode') or 'instant').lower()
            _exec_plan = {
                'profit_targets':  _pt_normalized,
                'averaging_zones': _avg_normalized,
                'tactical_plan':   str(_tactical),
                'play_type':       str(_play_type),
                'staged_at':       time.time(),
                'entry_mode_used': _entry_mode_used,  # 'wick' / 'instant' / 'confirmed'
                # Mode Recorregut Institucional ─────────────────────────────
                'auto_close_conditions': _auto_close,
                'trap_thesis':           str(_trap),
                'tp_thesis':             str(_tp_th),
                'invalidation_thesis':   str(_inv_th),
                'tp_target':             float(_tp_target) if _tp_target else 0.0,
                'entry_price':           float(price),
                'mode':                  'institutional_recorregut' if _auto_close else 'legacy',
                # 2026-05-06: BE trigger del LLM
                'breakeven_trigger':     _be_trigger if isinstance(_be_trigger, dict) else None,
                # 2026-05-06: SL dinàmic calculat pel mode wick (sobre el wick top)
                'wick_dynamic_sl':       float(_wick_dynamic_sl) if _wick_dynamic_sl else None,
            }
            sig_state._data['executor_plan'] = _exec_plan
            sig_state.save()
            log.info(
                f"[STAGED] ✅ executor_plan persistit: mode={_entry_mode_used} "
                f"auto_close={len(_auto_close)} cond, profit_targets={len(_pt_normalized)}, "
                f"tp_target={_tp_target}, BE={_be_trigger.get('price') if isinstance(_be_trigger, dict) else None}, "
                f"wick_sl={_wick_dynamic_sl}"
            )
            if _auto_close:
                # Validacions de salut del setup
                _tp_dist = abs(float(_tp_target) - float(price)) if _tp_target else 0
                if _tp_dist < 15.0:
                    log.warning(
                        f"[STAGED] ⚠ tp_target a només ${_tp_dist:.1f}$ — sota recomanat $15 "
                        f"(recorregut potser massa curt per R/R 1:3). Continuem igualment."
                    )
                # Distancia de la condició FULL_CLOSE més propera vs entry
                _full_close_dist = None
                for c_ in _auto_close:
                    if c_.get('action') == 'FULL_CLOSE' and c_.get('kind') == 'bar_close':
                        try:
                            _d = abs(float(c_.get('level')) - float(price))
                            if _full_close_dist is None or _d < _full_close_dist:
                                _full_close_dist = _d
                        except Exception:
                            pass
                if _full_close_dist is not None:
                    _rr = _tp_dist / _full_close_dist if _full_close_dist > 0 else 0
                    log.info(
                        f"[STAGED] 🎯 RECORREGUT mode armed: tp={_tp_target} (${_tp_dist:.1f}$ enllà), "
                        f"SL virtual a ${_full_close_dist:.1f}$ → R/R efectiu 1:{_rr:.2f}"
                    )
                    if _rr < 1.5:
                        log.warning(
                            f"[STAGED] ⚠ R/R efectiu 1:{_rr:.2f} sota recomanat 1:1.5 — "
                            f"el sistema continua però el setup té expectància baixa"
                        )
                else:
                    log.info(
                        f"[STAGED] 🎯 RECORREGUT mode armed: tp={_tp_target}, "
                        f"sense FULL_CLOSE conditions (només FORCE_REVIEW/PARTIAL) — "
                        f"DD 3.5% és l'única protecció dura"
                    )
                for i, c_ in enumerate(_auto_close):
                    log.info(f"[STAGED]   cond #{i}: {c_.get('id')} [{c_.get('kind')}] → {c_.get('action')}")
                # 2026-05-04: Mode Recorregut + multi-TP via profit_targets (l'usuari vol mantenir-ho).
                # Si el LLM proposa profit_targets [{price, close_pct}], inicialitzem el ladder
                # perquè dispari PARTIAL_CLOSE_PCT a cada nivell. tp_target segueix sent el
                # destí final al broker (l'última partial pot ser 100% al tp_target).
                if _exec_plan['profit_targets']:
                    # 2026-05-06: SHIFT LADDER perquè les distàncies +1R/+2R
                    # siguin RELATIVES a l'entry real, no a la zona planificada.
                    # L'EXECUTOR proposa absoluts pensant en zona; si entry difereix,
                    # els targets s'han d'ajustar per mantenir el R/R original.
                    _zone_planned = float(setup.get('zone_price') or zp or 0)
                    _entry_real = float(price or 0)
                    _shift = _entry_real - _zone_planned if (_zone_planned > 0 and _entry_real > 0) else 0
                    if abs(_shift) > 0.05:
                        for _t in _exec_plan['profit_targets']:
                            try:
                                _t['price'] = round(float(_t.get('price', 0)) + _shift, 2)
                            except Exception:
                                pass
                        # També BE trigger
                        if isinstance(_exec_plan.get('breakeven_trigger'), dict):
                            try:
                                _exec_plan['breakeven_trigger']['price'] = round(
                                    float(_exec_plan['breakeven_trigger'].get('price', 0)) + _shift, 2
                                )
                            except Exception:
                                pass
                        log.info(
                            f"[STAGED] 🔧 LADDER shift {_shift:+.2f}$ aplicat (zona {_zone_planned} "
                            f"vs entry {_entry_real}) — TP/BE ajustats per mantenir R/R"
                        )
                    log.info(
                        f"[STAGED] 🪜 Multi-TP ladder armat: {_exec_plan['profit_targets']} "
                        f"(Mode Recorregut + LLM-aprovat, post-shift)"
                    )
                    try:
                        import executor_ladder as _el
                        _el.init_from_signal(
                            _exec_plan['profit_targets'], direction,
                            entry_price=price,
                        )
                    except Exception as _le:
                        log.warning(f"[STAGED] ladder init failed: {_le}")
            elif _exec_plan['profit_targets']:
                # Sense auto_close però amb profit_targets — ladder igualment
                # També apliquem shift per consistència
                _zone_planned = float(setup.get('zone_price') or zp or 0)
                _entry_real = float(price or 0)
                _shift = _entry_real - _zone_planned if (_zone_planned > 0 and _entry_real > 0) else 0
                if abs(_shift) > 0.05:
                    for _t in _exec_plan['profit_targets']:
                        try:
                            _t['price'] = round(float(_t.get('price', 0)) + _shift, 2)
                        except Exception:
                            pass
                    if isinstance(_exec_plan.get('breakeven_trigger'), dict):
                        try:
                            _exec_plan['breakeven_trigger']['price'] = round(
                                float(_exec_plan['breakeven_trigger'].get('price', 0)) + _shift, 2
                            )
                        except Exception:
                            pass
                log.info(
                    f"[STAGED] Multi-TP ladder armat: targets={_exec_plan['profit_targets']}"
                )
                try:
                    import executor_ladder as _el
                    _el.init_from_signal(
                        _exec_plan['profit_targets'], direction,
                        entry_price=price,
                    )
                except Exception as _le:
                    log.warning(f"[STAGED] ladder init failed: {_le}")
            else:
                # Sense auto_close i sense profit_targets — protecció zero
                log.warning(
                    f"[STAGED] ⚠ Trade obert SENSE auto_close_conditions ni profit_targets. "
                    f"Només protecció: TP broker (${target if target > 0 else 'cap'}) i DD 3.5%. "
                    f"L'EXECUTOR hauria de proposar conditions al pròxim cycle."
                )
        except Exception as _e:
            log.warning(f"[STAGED] Failed to persist executor_plan: {_e}")
        clear_staged_setup()
        log.info(f"[STAGED] EXECUTED {direction} {lot} — setup consumed")
        trade_history.log_event(
            type='OPEN', direction=direction, lot=lot, price=price,
            source='AUTONOMOUS',
            reason=f'Staged setup fired @ zone {zp} [{z_strength}] — candle {c}, vol {cur_vol:.1f}x, target {target}'
        )
        # 2026-05-04: trigger EXECUTOR review immediately — un setup acaba de
        # disparar i tenim trade obert; l'EXECUTOR ha de plantejar gestió.
        _force_executor_review(f'staged_setup_fired {direction}@{zp:.1f} (lot {lot})')
        return True
    else:
        log.error(f"[STAGED] send_market FAILED")
        return False


def handle_move_sl(interp, sig_state, tg_channel=None):
    """Handle MOVE_SL message — set breakeven via Brain EA v1, but ONLY if the
    active signal was opened by the same channel that now requests the SL move.

    Ownership rule (same as handle_close): a "movemos SL" from channel X should
    only affect a trade whose `channel == X`. An AUTONOMOUS/HUNTER trade is the
    system's own operation; external TG signals have no authority over it.
    Its BE logic is handled by the Executor and the profit-ladder, not by
    external TG commands.
    """
    if not sig_state.is_active():
        log.warning("[MOVE_SL] No active signal, ignoring")
        return

    cur_channel = sig_state.get('channel') if hasattr(sig_state, 'get') else None
    # Strict ownership: TG move-SL only honored when the trade was opened by
    # the EXACT same TG channel. ADOPTED no longer whitelisted (see handle_close
    # for rationale).
    if tg_channel and cur_channel and cur_channel != tg_channel:
        log.info(
            f"[MOVE_SL] Ignored TG 'movemos SL' from {tg_channel}: current "
            f"trade channel={cur_channel} (autonomous, hunter, adopted, or "
            f"different TG owner). External signal has no authority here."
        )
        try:
            notify('dd_alert',
                   f"🛡 Movemos SL de `{tg_channel}` ignorat — trade actiu "
                   f"és `{cur_channel}`. BE no aplicat.")
        except Exception:
            pass
        return

    new_sl = interp.get('new_sl')
    sig_state.request_breakeven(sl_price=new_sl)

    if PAPER_MODE:
        log.info("[MOVE_SL][PAPER] WOULD send MOVE_SL_ENTRY to EA — blocked by PAPER_MODE")
    else:
        move_sl_entry()
        log.info("[MOVE_SL] Breakeven set via EA.")


def handle_close(interp, sig_state, tg_channel=None):
    """Handle CLOSE — close brain positions via EA, but ONLY if the current
    signal was OPENED BY THE EXACT SAME TG channel that now sends "cerramos".

    Ownership rule (strict): an active signal's `channel` field records who
    opened it (e.g. 'TrueTrading', 'Vikingo', 'AUTONOMOUS', 'ADOPTED', 'HUNTER').
    A TG cerramos from channel X may close ONLY trades opened from channel X.

    AUTONOMOUS / HUNTER / ADOPTED trades are NOT closed by TG cerramos. Their
    exit is owned by the brain's own logic (profit ladder, trailing, Executor,
    DD safety net) or by the operator manually via dashboard. ADOPTED used to
    be whitelisted on the assumption "the operator wants TG to manage adopted
    positions" — that broke isolation for autonomous trades (incident 2026-04-
    27 09:54: a TrueTrading cerramos closed an autonomous BUY because the
    re-adoption code had set channel='ADOPTED'). Removed.
    """
    cur_channel = sig_state.get('channel') if hasattr(sig_state, 'get') else None
    if tg_channel and cur_channel and cur_channel != tg_channel:
        # Trade belongs to a different owner than the TG channel that sent
        # cerramos. Do nothing — let the trade's own exit logic decide.
        log.info(
            f"[CLOSE] Ignored TG 'cerramos' from {tg_channel}: current trade "
            f"channel={cur_channel} (autonomous, hunter, adopted, or different "
            f"TG owner). External signal has no authority here."
        )
        try:
            notify('dd_alert',
                   f"🛡 Cerramos de `{tg_channel}` ignorat — trade actiu "
                   f"és `{cur_channel}`. La nostra operativa segueix.")
        except Exception:
            pass
        return
    sig_state.mark_closing()

    # Snapshot final floating P&L from open tickets and credit it to realized.
    try:
        open_pos = read_json(POSITIONS).get('positions', []) or []
        floating_pnl = sum(broker_position_pnl(p) for p in open_pos)
        if sig_state.is_active() and floating_pnl != 0:
            prev = float(sig_state._data.get('realized_profit', 0) or 0)
            sig_state._data['realized_profit'] = prev + floating_pnl
            sig_state.save()
            log.info(f"[P&L] Snapshot at close: floating ${floating_pnl:+.2f} added to realized (was ${prev:+.2f})")
    except Exception as e:
        log.warning(f"[P&L] snapshot at close failed: {e}")

    realized = sig_state.get('realized_profit', 0.0) if sig_state.is_active() else 0.0
    if PAPER_MODE:
        log.info("[CLOSE][PAPER] WOULD close_all_brain — blocked by PAPER_MODE")
    else:
        close_all_brain()
        log.info("[CLOSE] close_all_brain sent to EA.")
    # NOTE (2026-04-24): do NOT call close_signal() here. Positions may not
    # have settled yet at the broker — balance still reflects pre-close state.
    # The MANUAL_CLOSE detector (grace-period loop) sees pos_count==0 after
    # EA finishes and calls close_signal(end_balance=...) with the real
    # post-settlement balance, so TG reports net P&L (fees included) instead
    # of gross price-delta. `closing=True` via mark_closing() is enough to
    # suppress new actions until then.
    _set_last_signal_close_ts()  # start 5-min cooldown before next staged fire
    # Cancel any live snipers — they belong to the trade that just closed.
    try:
        import snipers as _snp
        _n_cancel = _snp.cancel_all(reason="signal_close_tg")
        if _n_cancel:
            log.info(f"[SNIPER] Cancelled {_n_cancel} sniper(s) on TG cerramos")
    except Exception:
        pass
    # Release any Hunter alt_hypothesis setups that were waiting for trade close,
    # then purge same-zone setups so the Executor must re-analyze before re-entry.
    try:
        import staged_setups as _ss
        released = _ss.unfreeze_post_close()
        if released:
            log.info(f"[HUNTER] Released {released} post_close setups (eligible now that trade closed)")
        _closed_dir = sig_state.get('direction')
        _closed_entry = float(sig_state.get('entry_price') or 0)
        if _closed_dir and _closed_entry:
            _removed = _ss.remove_near_zone(_closed_dir, _closed_entry)
            if _removed:
                log.info(f"[STAGED] Purged {_removed} same-zone setup(s) after close "
                         f"({_closed_dir}@{_closed_entry:.2f}) — Executor must re-analyze before re-entry")
    except Exception:
        pass
    # Persist a narrative of this trade (actions + reasoning) for the dashboard.
    try:
        import trade_narrative as _tn
        n = _tn.persist_latest_narrative()
        if n:
            log.info(f"[NARRATIVE] saved for trade {n.get('trade_id')} "
                     f"({n.get('direction')} pnl={n.get('total_pnl'):+.2f} "
                     f"actions={len(n.get('actions') or [])})")
    except Exception as _tne:
        log.debug(f"[NARRATIVE] persist failed: {_tne}")


# ═══════════════════════════════════════════════════════════════
# SMART LOT SIZING — calculates initial lot based on DD budget + ATR
# ═══════════════════════════════════════════════════════════════

def calculate_initial_lot(balance, atr_m1, risk_pct_of_dd_budget=0.30):
    """Calculate initial lot size.

    Strategy: reserve 30% of DD budget for initial entry.
    Remaining 70% is budget for averaging (no count limit; only DD-bounded).

    Formula:
      dd_budget = balance * (BRAIN_DD_LIMIT_PCT / 100)
      initial_budget = dd_budget * risk_pct_of_dd_budget
      per_lot_risk_at_atr = atr_m1 * 100  # USD loss if price moves 1 ATR against us
      lot = initial_budget / (atr_m1 * 100 * 2)  # allow 2 ATR move

    Floor: 0.01, Cap: 1.0
    """
    if balance <= 0 or not atr_m1 or atr_m1 < 0.5:
        return 0.03  # safe default
    dd_budget = balance * (BRAIN_DD_LIMIT_PCT / 100.0)
    initial_budget = dd_budget * risk_pct_of_dd_budget
    # Allow 2 ATR move against us before SL
    lot = initial_budget / (atr_m1 * 100 * 2)
    lot = round(max(0.01, min(1.0, lot)), 2)
    return lot


# ═══════════════════════════════════════════════════════════════
# CHART — Pine Script indicator (syncs to mobile)
# ═══════════════════════════════════════════════════════════════

def generate_pine_script(brain_response, account):
    """Generate Pine Script v6 from Claude's reversal zones."""
    plan = brain_response.get('plan', {})
    reversal_zones = brain_response.get('reversal_zones', [])
    bias = plan.get('bias', 'NEUTRAL')
    notes = (plan.get('notes', '') or '')[:40].replace('"', "'")
    action = brain_response.get('action', 'WAIT')
    confidence = brain_response.get('confidence', 0)

    # hline() for each zone
    hlines = []
    for i, zone in enumerate(reversal_zones):
        zp = zone.get('price', 0)
        ztype = zone.get('type', 'SUPPORT').upper()
        strength = zone.get('strength', 'MODERATE').upper()
        if not zp:
            continue
        clr = 'color.green' if ztype == 'SUPPORT' else 'color.red'
        if strength == 'WEAK':
            clr = f'color.new({"color.green" if ztype == "SUPPORT" else "color.red"}, 50)'
        lw = {'STRONG': 3, 'MODERATE': 2, 'WEAK': 1}.get(strength, 2)
        ls = 'hline.style_solid' if strength == 'STRONG' else 'hline.style_dashed'
        label = f"{ztype[0]}{i+1} {zp:.1f}"
        hlines.append(f'hline({zp}, "{label}", color={clr}, linewidth={lw}, linestyle={ls})')

    # Entry line
    entry_line = ""
    if account.get('positions'):
        tl, we = 0, 0
        for p in account['positions']:
            lot = p.get('volume', p.get('lot', 0))
            pr = p.get('price_open', p.get('open_price', 0))
            tl += lot; we += pr * lot
        if tl > 0:
            we = we / tl
            entry_line = f'hline({we:.2f}, "ENTRY {we:.2f} ({tl:.2f}L)", color=color.white, linewidth=2, linestyle=hline.style_solid)'

    bias_clr = 'color.lime' if bias == 'BULLISH' else 'color.red' if bias == 'BEARISH' else 'color.gray'

    dd_pct = account.get('dd_pct', 0)
    dd_row = ""
    table_rows = 3
    if account.get('has_signal'):
        dd_clr = 'color.red' if dd_pct > 2.5 else 'color.yellow' if dd_pct > 1.5 else 'color.white'
        dd_row = f"""
    table.cell(t, 0, 3, "DD", text_color=color.white, text_size=size.small)
    table.cell(t, 1, 3, "{dd_pct:.1f}%/4%", text_color={dd_clr}, text_size=size.small)"""
        table_rows = 4

    return f"""//@version=6
indicator("Claude Brain", overlay=true)
{chr(10).join(hlines)}
{entry_line}
var table t = table.new(position.top_left, 2, {table_rows}, bgcolor=color.new(color.black, 70), border_width=1)
if barstate.islast
    table.cell(t, 0, 0, "BIAS", text_color=color.white, text_size=size.small)
    table.cell(t, 1, 0, "{bias}", text_color={bias_clr}, text_size=size.small)
    table.cell(t, 0, 1, "Action", text_color=color.white, text_size=size.small)
    table.cell(t, 1, 1, "{action} {confidence:.0%}", text_color=color.gray, text_size=size.small)
    table.cell(t, 0, 2, "Context", text_color=color.white, text_size=size.small)
    table.cell(t, 1, 2, "{notes}", text_color=color.gray, text_size=size.small){dd_row}
"""


# Persistence of drawn zone IDs (to remove them next update)
_DRAWN_IDS_FILE = os.path.join(LOG_DIR, 'drawn_zone_ids.json')


def _load_drawn_ids():
    try:
        with open(_DRAWN_IDS_FILE, 'r') as f:
            return json.load(f).get('ids', [])
    except Exception:
        return []


def _save_drawn_ids(ids):
    try:
        with open(_DRAWN_IDS_FILE, 'w') as f:
            json.dump({'ids': ids}, f)
    except Exception:
        pass


def _should_notify_setups(setups, category='staged', cooldown_s=900):
    """Deduper for TG notifications — returns False if the same set of setups
    (same directions+prices) was already notified for this category within the
    cooldown window. Prevents spam when stagers re-fire at every event tick.

    `category` lets us track 'staged' (Executor) and 'hunter' independently.
    `cooldown_s` default 15 min — after that, notify again as a health ping.
    """
    fp_parts = sorted(
        f"{s.get('direction','?')}@{round(float(s.get('zone_price', s.get('price', 0)) or 0), 1)}"
        for s in (setups or [])
    )
    fp = '|'.join(fp_parts) or 'empty'
    now = time.time()
    cache = getattr(_should_notify_setups, '_cache', None)
    if cache is None:
        cache = {}
        _should_notify_setups._cache = cache
    last = cache.get(category)
    if last and last.get('fp') == fp and (now - last.get('ts', 0)) < cooldown_s:
        return False
    cache[category] = {'fp': fp, 'ts': now}
    return True


def redraw_tv(bars, account, signal_state=None):
    """Redraw TradingView with current live state. Reads zones from zone_store
    directly (no need for an Indicator response) and calls draw_reasoning.
    Lightweight wrapper callable from any state-change event."""
    try:
        from zone_store import read_state, active_zones, legacy_compat_view
        zstate = read_state(COMMON)
        # legacy_compat_view returns {reversal_zones, bias, context, ...}
        response_like = legacy_compat_view(zstate)
        return draw_reasoning(response_like, bars, account, signal_state)
    except Exception as _re:
        log.debug(f"[TV] redraw failed: {_re}")
        return 0


def draw_reasoning(brain_response, bars, account, signal_state=None):
    """Draw the REAL current strategy on TradingView — a faithful mirror of
    what the system will actually do, not speculative "planned AVG" lines.

    Layers (in draw priority):
      · ZONES del mapa (informatives, per context) — fines, translúcides
      · ENTRY ponderat de les posicions obertes — blanc
      · TPs REALS dels tickets oberts (del broker) — cyan
      · SLs REALS dels tickets — taronja
      · HUNTER staged setups — violeta, amb TP/SL petits
      · EXECUTOR staged setups — groc, amb fletxa direcció

    El que desapareix: les línies especulatives "AVG→SELL 0.06 @ X" que
    podien no executar-se mai. El TV reflecteix NOMÉS el que el sistema
    farà o està fent ara.
    """
    if not brain_response or not bars:
        return 0
    zones = brain_response.get('reversal_zones', []) or []

    now_ts = int(time.time())
    new_ids = []

    # Note: state table cleanup happens inline at draw time (see _state_table_ids
    # stack at LAYER 0). Old single-id cleanup obsolete.

    # Two-phase draw: collect everything to draw FIRST, fingerprint it,
    # and only touch TradingView if the picture actually changed. This kills
    # the visual "flicker" of clearing+repainting identical shapes every
    # time draw_reasoning is invoked (5 callers during an active trade).
    _pending = []  # list of (price_rounded, color, lw, ls, label, textcolor)

    def _draw_line(price, color, linewidth, linestyle, label, textcolor=None):
        if not price or price <= 0:
            return
        _pending.append((round(float(price), 2), color, linewidth, linestyle, label, textcolor or color))

    # ── LAYER 0: BIAS LABEL compacte (single line) ──
    # 2026-05-04: una sola línia near current price amb tota la info clau:
    # BIAS · CONFIANÇA · RANG · TARGET BREAK. Sense apilar línies.
    _wr = brain_response.get('working_range') if isinstance(brain_response.get('working_range'), dict) else None
    _dc = brain_response.get('directional_commitment') if isinstance(brain_response.get('directional_commitment'), dict) else None
    _bias_colors = {
        'BUY_ONLY':  ('#22cc55', '⬆ BUY'),
        'SELL_ONLY': ('#cc2255', '⬇ SELL'),
        'NEUTRAL':   ('#cccccc', '◦ NEUTRAL'),
        'WAIT_NO_TRADE': ('#ffaa00', '⏸ NO-TRADE'),
    }
    _state_label_parts = []
    if _dc:
        _dc_side = (_dc.get('side') or '').upper()
        _dc_conf = _dc.get('confidence', 0)
        _dc_color, _dc_emoji = _bias_colors.get(_dc_side, ('#bbbbbb', _dc_side))
        _state_label_parts.append(f"{_dc_emoji} ({_dc_conf:.0%})")
    else:
        _dc_color = '#888888'
    if _wr:
        _wr_hi = _wr.get('high'); _wr_lo = _wr.get('low')
        _wr_type = (_wr.get('type', 'RANGE') or 'RANGE')[:6]
        if _wr_hi and _wr_lo:
            _state_label_parts.append(f"RANG {_wr_lo:.0f}-{_wr_hi:.0f} [{_wr_type}]")
            _up = _wr.get('expansion_targets_if_breaks_up') or []
            _dn = _wr.get('expansion_targets_if_breaks_down') or []
            if _up:
                _state_label_parts.append(f"↑{_up[0]:.0f}")
            if _dn:
                _state_label_parts.append(f"↓{_dn[0]:.0f}")
    # NEW 2026-05-04 v2: include asymmetric_risk in chart label
    _ar = brain_response.get('asymmetric_risk') if isinstance(brain_response.get('asymmetric_risk'), dict) else None
    if _ar:
        _ar_short = {'HIGH': 'H', 'MEDIUM': 'M', 'LOW': 'L'}
        _bsq = _ar_short.get((_ar.get('bull_squeeze_risk') or '').upper(), '?')
        _bcn = _ar_short.get((_ar.get('bear_continuation_risk') or '').upper(), '?')
        _pc = (_ar.get('primary_concern') or '').replace('_', ' ').upper()[:13]
        _state_label_parts.append(f"BULL_SQ:{_bsq} · BEAR_CT:{_bcn}")
        if _pc:
            _state_label_parts.append(f"⚠ {_pc}")
    _state_label = " · ".join(_state_label_parts)
    # Guardem el label per dibuixar-lo després del draw-clear
    draw_reasoning._state_label = _state_label
    draw_reasoning._state_color = _dc_color

    # ── LAYER 1: Zones del mapa (context, no estratègia) ──
    # Diferenciació visual segons confidence + data sources + naked POC futures:
    #   · STRONG amb 2 fonts confirmades → solid color rich
    #   · STRONG amb 1 font només → solid color faded (mercat CME tancat)
    #   · MODERATE/WEAK → dashed line
    #   · naked_poc_futures=True → marca distintiva ⭐ + label amb "(NPOC GC1!)"
    #   · NEW: si approach_tracker té estat APPROACH/AT_ZONE per la zona,
    #         override color amb gradient segons signal_strength del flux.
    _at_for_draw = None
    try:
        _at_for_draw = _get_approach_tracker()
    except Exception:
        pass

    for zone in zones:
        zp = zone.get('price', 0)
        if not zp:
            continue
        ztype = (zone.get('type', 'SUPPORT') or 'SUPPORT').upper()
        strength = (zone.get('strength', 'MODERATE') or 'MODERATE').upper()
        is_support = ztype == 'SUPPORT'
        # Dual-feed metadata
        is_naked_gc = bool(zone.get('naked_poc_futures'))
        sources_count = int(zone.get('data_sources_count') or 1)
        conf_numeric = zone.get('confidence_numeric')
        zone_id = zone.get('id') or ''

        # Approach state per aquesta zona (si tracker disponible)
        approach_st = None
        approach_strength = 0.0
        approach_state_label = "IDLE"
        if _at_for_draw is not None and zone_id:
            try:
                approach_st = _at_for_draw.get_state(zone_id)
                if approach_st is not None:
                    approach_state_label = approach_st.state
                    approach_strength = approach_st.signal_strength(
                        _at_for_draw.delta_threshold_futures
                    )
            except Exception:
                approach_st = None

        # Color base segons tipus + força
        if is_naked_gc:
            color = '#d4af37'  # daurat NPOC futures
        elif is_support:
            color = '#2d7a3a' if sources_count >= 2 else '#557555'
        else:
            color = '#a02020' if sources_count >= 2 else '#7a4a4a'

        # Override color amb approach gradient si la zona està APPROACH/AT_ZONE
        # APPROACH: gris → indica seguiment actiu però decisió no compromesa
        # AT_ZONE: gradient verd/vermell segons direcció del flux institucional
        #          - Verd intens (+strength alt): compradors agressius dominant
        #          - Vermell intens (-strength alt): venedors agressius dominant
        #          - Groc: flux neutre/contradictori
        if approach_st is not None and approach_state_label != 'IDLE':
            if approach_state_label == 'APPROACH':
                color = '#888888'  # gris translúcid (encara expectant)
            elif approach_state_label == 'AT_ZONE':
                # Color depèn del signal_strength
                if approach_strength > 0.1:
                    # Verd, intensitat segons strength (0.1 = clar, 1.0 = max viu)
                    intensity = min(1.0, abs(approach_strength))
                    # interpolar entre verd suau (#558855) i verd intens (#00ff00)
                    g = int(0x88 + (0xff - 0x88) * intensity)
                    r = int(0x55 - 0x55 * intensity)
                    b = int(0x55 - 0x55 * intensity)
                    color = f'#{r:02x}{g:02x}{b:02x}'
                elif approach_strength < -0.1:
                    intensity = min(1.0, abs(approach_strength))
                    r = int(0x88 + (0xff - 0x88) * intensity)
                    g = int(0x55 - 0x55 * intensity)
                    b = int(0x55 - 0x55 * intensity)
                    color = f'#{r:02x}{g:02x}{b:02x}'
                else:
                    color = '#ffff00'  # groc neutre

        linewidth = {'STRONG': 2, 'MODERATE': 1, 'WEAK': 1}.get(strength, 1)
        if is_naked_gc:
            linewidth = max(linewidth, 2)  # Naked GC1! sempre prou gruixut
        if approach_state_label == 'AT_ZONE':
            linewidth = max(linewidth, 3)  # AT_ZONE més visible

        # Style: solid si STRONG + dual-confirmed o AT_ZONE
        if approach_state_label == 'AT_ZONE':
            linestyle = 0  # solid
        else:
            linestyle = 0 if (strength == 'STRONG' and sources_count >= 2) else 2

        # Label enriquit
        label_parts = [f"{ztype[0]}{zp:.1f} [{strength[0]}]"]
        if is_naked_gc:
            label_parts.append("⭐NPOC_GC")
        if conf_numeric is not None:
            try:
                label_parts.append(f"c={float(conf_numeric):.2f}")
            except (TypeError, ValueError):
                pass
        if sources_count >= 2:
            label_parts.append("·dual")
        # Etiqueta amb approach metrics si state != IDLE
        if approach_st is not None and approach_state_label != 'IDLE':
            if approach_state_label == 'AT_ZONE':
                label_parts.append(
                    f"⚡{approach_state_label} {approach_st.bars_acc}b "
                    f"Δ{approach_st.delta_acc:+.0f} s={approach_strength:+.2f}"
                )
            else:
                label_parts.append(
                    f"~{approach_state_label} {approach_st.bars_acc}b"
                )
        label = " ".join(label_parts)
        _draw_line(zp, color, linewidth, linestyle, label)

    # ── LAYER 2: Entry ponderat de posicions obertes ──
    # Re-read positions FRESH from disk: the `account` snapshot may be stale
    # (the Indicator pipeline runs async — the account passed to this function
    # was captured ~75-180s ago). If positions changed in between, we'd skip
    # drawing the ENTRY/TP layer. Re-reading guarantees we always reflect the
    # CURRENT state.
    try:
        _fresh_pos = read_json(POSITIONS).get('positions', []) or []
    except Exception:
        _fresh_pos = account.get('positions') or []
    positions = _fresh_pos if _fresh_pos else (account.get('positions') or [])
    if positions:
        tl, we = 0.0, 0.0
        for p in positions:
            lot = float(p.get('volume', p.get('lot', 0)) or 0)
            pr = float(p.get('price_open', p.get('open_price', 0)) or 0)
            tl += lot; we += pr * lot
        if tl > 0:
            we = we / tl
            _draw_line(we, '#ffffff', 3, 0, f'ENTRY {we:.2f} ({tl:.2f}L)')

    # ── LAYER 3: TPs i SLs REALS del broker (per ticket) ──
    # These are the authoritative "what will happen" values — what the broker
    # has registered and will execute on touch.
    for p in positions:
        tk = p.get('ticket') or p.get('ticket_id') or 0
        tp = float(p.get('tp', 0) or 0)
        sl = float(p.get('sl', 0) or 0)
        vol = float(p.get('volume', p.get('lot', 0)) or 0)
        _comment = (p.get('comment') or '')
        tag = 'H' if _comment.startswith('HUNTER_') else ('B' if _comment.startswith('BRAIN') else 'A')
        if tp and tp > 0:
            _draw_line(tp, '#00d0d0', 2, 0, f'TP [{tag}] tk{tk} {vol:.2f}L @ {tp:.2f}')
        if sl and sl > 0:
            _draw_line(sl, '#ff9020', 2, 0, f'SL [{tag}] tk{tk} @ {sl:.2f}')

    # ── LAYER 4: Staged setups (Hunter + Executor) ──
    try:
        import staged_setups as _ss
        setups = _ss.load() or []
    except Exception:
        setups = []

    for s in setups:
        if s.get('post_close'):
            # Alt_hypothesis setups — waiting for current trade to close.
            # Show dimmed so user knows they're pending.
            pass
        src = (s.get('source') or 'executor').lower()
        direction = s.get('direction')
        zp = float(s.get('zone_price', 0) or 0)
        if not zp or direction not in ('BUY', 'SELL'):
            continue
        dir_arrow = '↑' if direction == 'BUY' else '↓'
        conf = float(s.get('confidence', 0) or 0)
        conf_pct = int(conf * 100)
        pc_tag = ' [🔒post-close]' if s.get('post_close') else ''
        sid = (s.get('id') or '')[:10]
        if src == 'hunter':
            # Violet for Hunter, with TP/SL small lines
            _draw_line(zp, '#a78bfa', 2, 2, f'🏹{dir_arrow} {sid} @ {zp:.2f} ({conf_pct}%){pc_tag}')
            tp = float(s.get('profit_target', 0) or 0)
            sl_price = float(s.get('invalidation_price', 0) or 0)
            if tp > 0:
                _draw_line(tp, '#a78bfa', 1, 2, f'  ↳TP {tp:.2f}')
            if sl_price > 0:
                _draw_line(sl_price, '#a78bfa', 1, 2, f'  ↳SL {sl_price:.2f}')
        else:
            # Yellow for Executor staged entry — afegim entry_mode al label
            _entry_m = (s.get('entry_mode') or 'instant').lower()
            _mode_tag = f' [{_entry_m.upper()}]' if _entry_m != 'instant' else ''
            _draw_line(zp, '#f0c040', 2, 2,
                       f'🎯{dir_arrow} {sid} @ {zp:.2f} ({conf_pct}%){pc_tag}{_mode_tag}')

            # 2026-05-06: dibuixar TOTS els nivells del pla del trade:
            # · profit_targets (multi-TP ladder amb close_pct)
            # · breakeven_trigger (BE point)
            # · auto_close FULL_CLOSE (SL virtual)
            # · auto_close PARTIAL_50 (defensive partials)

            _tp_t = float(s.get('tp_target') or s.get('profit_target') or 0)
            _acc = s.get('auto_close_conditions') or []
            _profit_targets = s.get('profit_targets') or []
            _be_trigger = s.get('breakeven_trigger') or {}

            # Multi-TP ladder (profit_targets) — verd amb close_pct visible
            for i, _pt in enumerate(_profit_targets):
                if not isinstance(_pt, dict):
                    continue
                _pt_price = _pt.get('price')
                _pt_pct = _pt.get('close_pct', 0)
                if _pt_price:
                    _pt_dist = abs(float(_pt_price) - zp)
                    _label = f'  ↳🎯 TP{i+1} {_pt_price:.2f} ({_pt_pct}%) (+${_pt_dist:.1f})'
                    _draw_line(float(_pt_price), '#3fd47e', 2, 0, _label)

            # Si NO hi ha multi-TP però sí tp_target → dibuixa el TP únic
            if not _profit_targets and _tp_t > 0:
                _tp_dist = abs(_tp_t - zp)
                _draw_line(_tp_t, '#3fd47e', 2, 2,
                           f'  ↳🎯 TP {_tp_t:.2f} (+${_tp_dist:.1f})')

            # Breakeven trigger — blau (preu on BE s'activa)
            _be_price = _be_trigger.get('price') if isinstance(_be_trigger, dict) else None
            if _be_price:
                _be_dist = abs(float(_be_price) - zp)
                _draw_line(float(_be_price), '#4a9eff', 1, 2,
                           f'  ↳🛡 BE trigger {float(_be_price):.2f} (+${_be_dist:.1f})')

            if _acc:
                # SL virtual = el FULL_CLOSE més proper a la zona
                _sl_v = None
                for _c in _acc:
                    if _c.get('action') == 'FULL_CLOSE' and _c.get('level'):
                        _l = float(_c.get('level'))
                        if _sl_v is None or abs(_l - zp) < abs(_sl_v - zp):
                            _sl_v = _l
                if _sl_v is not None:
                    _sl_dist = abs(_sl_v - zp)
                    _draw_line(_sl_v, '#ff5a5f', 2, 2,
                               f'  ↳💀 SL virtual {_sl_v:.2f} (-${_sl_dist:.1f})')
                # Partials defensius pre-aprovats (auto_close PARTIAL_50)
                for _c in _acc:
                    if _c.get('action') == 'PARTIAL_50' and _c.get('level'):
                        _pl = float(_c.get('level'))
                        _draw_line(_pl, '#f0b429', 1, 2,
                                   f'  ↳⚠ PARTIAL 50% def @ {_pl:.2f}')

    # ── LAYER 4.5: Executor-proposed SNIPERS (pre-placed averagings) ──
    # Draw each live sniper as an orange solid line with a target emoji label.
    # These are price levels where the Executor has decided it wants an
    # immediate MARKET AVG if the price touches — no wait, no confirmation.
    try:
        import snipers as _snp
        for s in (_snp.load() or []):
            sp = s.get('price')
            if not sp:
                continue
            direction = s.get('direction', '?')
            arrow = '↓' if direction == 'SELL' else '↑'
            mult = s.get('multiplier', 1)
            _draw_line(float(sp), '#ff9010', 3, 0,
                       f'🎯 SNIPER {direction}{arrow} ×{mult} @ {sp:.2f}')
    except Exception as _snpe:
        log.debug(f"[DRAW] snipers failed: {_snpe}")

    # ── LAYER 4.7: EXECUTOR LADDER + BE TRIGGER ──
    # The LLM's tactical exit plan (profit_targets with close_pct) and the
    # planned breakeven trigger. These are what the dashboard shows in the
    # "🪜 Executor Ladder" section — drawing them on TV ensures the chart
    # matches the console plan instead of just showing broker-side TPs.
    try:
        from signal_state import get_state as _gs
        _ss_now = _gs()
        _ep_chart = _ss_now.get('executor_plan') if _ss_now else None
        _be_set_now = bool(_ss_now.get('breakeven_set')) if _ss_now else False
        if isinstance(_ep_chart, dict):
            # Profit targets ladder — green levels with close_pct + reasoning
            _pts = _ep_chart.get('profit_targets') or []
            _direction_now = _ss_now.get('direction') if _ss_now else None
            _arrow = '↓' if _direction_now == 'SELL' else '↑'
            for _idx, _t in enumerate(_pts, 1):
                if isinstance(_t, dict):
                    _p = _t.get('price')
                    _pct = _t.get('close_pct')
                    _reason = (_t.get('reasoning') or '')[:40]
                else:
                    _p, _pct, _reason = _t, None, ''
                try:
                    _p_f = float(_p)
                except (TypeError, ValueError):
                    continue
                if _p_f <= 0:
                    continue
                _label = f'🪜 LADDER L{_idx} {_arrow} {_p_f:.2f}'
                if _pct is not None:
                    _label += f' [{int(_pct)}%]'
                if _reason:
                    _label += f' · {_reason}'
                _draw_line(_p_f, '#3fd47e', 2, 0, _label)
            # Breakeven trigger — blue dashed line, only if not yet set
            _be = _ep_chart.get('breakeven_trigger')
            if isinstance(_be, dict) and not _be_set_now:
                _bep = _be.get('price')
                try:
                    _bep_f = float(_bep) if _bep is not None else None
                except (TypeError, ValueError):
                    _bep_f = None
                if _bep_f and _bep_f > 0:
                    _br = (_be.get('reasoning') or '')[:50]
                    _label_be = f'🔒 BE TRIGGER @ {_bep_f:.2f}'
                    if _br:
                        _label_be += f' · {_br}'
                    _draw_line(_bep_f, '#4a9eff', 2, 2, _label_be)
            # 2026-05-04: Mode Recorregut — pintar TP, SL virtual i partials
            # del trade obert (a partir de auto_close_conditions persistides).
            _tp_open = _ep_chart.get('tp_target')
            _acc_open = _ep_chart.get('auto_close_conditions') or []
            try:
                _tp_open_f = float(_tp_open) if _tp_open else 0
            except (TypeError, ValueError):
                _tp_open_f = 0
            if _tp_open_f > 0:
                _draw_line(_tp_open_f, '#3fd47e', 3, 0, f'🎯 TP {_tp_open_f:.2f}')
            if _acc_open:
                _entry_open = _ep_chart.get('entry_price') or _ss_now.get('entry_price') if _ss_now else None
                try:
                    _entry_open_f = float(_entry_open) if _entry_open else None
                except (TypeError, ValueError):
                    _entry_open_f = None
                # SL virtual = FULL_CLOSE més proper a entry
                _sl_v_open = None
                for _c in _acc_open:
                    if _c.get('action') == 'FULL_CLOSE' and _c.get('level'):
                        _l = float(_c.get('level'))
                        if _entry_open_f is None:
                            _sl_v_open = _l
                            break
                        if _sl_v_open is None or abs(_l - _entry_open_f) < abs(_sl_v_open - _entry_open_f):
                            _sl_v_open = _l
                if _sl_v_open is not None:
                    _fired_tag = ''
                    for _c in _acc_open:
                        if _c.get('action') == 'FULL_CLOSE' and _c.get('fired_at'):
                            _fired_tag = ' ⚠FIRED'
                    _draw_line(_sl_v_open, '#ff5a5f', 3, 2,
                               f'💀 SL virtual {_sl_v_open:.2f}{_fired_tag}')
                # Partials pre-aprovats
                for _c in _acc_open:
                    if _c.get('action') == 'PARTIAL_50' and _c.get('level'):
                        _pl = float(_c.get('level'))
                        _f = ' ⚠FIRED' if _c.get('fired_at') else ''
                        _draw_line(_pl, '#f0b429', 2, 2,
                                   f'📉 PARTIAL 50% @ {_pl:.2f}{_f}')
    except Exception as _ldre:
        log.debug(f"[DRAW] ladder/BE failed: {_ldre}")

    # ── LAYER 5: Current Executor invalidation line ──
    # The "if this breaks, my plan is dead" threshold. Drawn as a dashed red
    # line with clear label so the visual matches a trader's stop-kill mindset.
    try:
        trade_id = signal_state.get('id') if signal_state and hasattr(signal_state, 'get') else None
        if trade_id:
            last = read_last_executor_decision(trade_id)
            if last:
                parsed = parse_invalidation_condition(last.get('invalidation_condition'))
                s = (parsed or {}).get('structured') or {}
                inv_price = s.get('price')
                inv_dir = s.get('direction')
                inv_trigger = s.get('trigger') or 'cross'
                if inv_price and inv_dir in ('above', 'below'):
                    arrow = '⬇' if inv_dir == 'below' else '⬆'
                    vol_tag = ' +vol' if s.get('require_volume') else ''
                    _draw_line(inv_price, '#ff3030', 1, 2,
                               f'⚠ INVAL {arrow} {inv_trigger}{vol_tag} @ {inv_price:.2f}')
    except Exception as _ie:
        log.debug(f"[DRAW] invalidation line failed: {_ie}")

    # ── LAYER 6: Naked POCs (high-value magnets from volume profile) ──
    # Price → naked POCs = powerful magnets. Drawn as thin dotted orange lines.
    try:
        import indicator_context as _ic
        naked = _ic._naked_pocs(_ic._poc_per_day(bars, n_days=5), bars)
        current_price = bars[-1].get('close', 0) if bars else 0
        for p in (naked or [])[:3]:
            poc_price = p.get('poc', 0)
            if not poc_price:
                continue
            dist = poc_price - current_price
            _draw_line(poc_price, '#ffa040', 1, 2,
                       f'🧲 NAKED POC {poc_price:.1f} ({p.get("label","?")}, {dist:+.1f}$)')
    except Exception as _npe:
        log.debug(f"[DRAW] naked POCs failed: {_npe}")

    # ── LAYER 7: Critical HVN levels from Pine TV ──
    # Only the 3 nearest on each side, to avoid clutter. Color: faint purple.
    try:
        import indicator_context as _ic
        tvp = _ic._fetch_tv_session_volume_profile(tv)
        pine = (tvp or {}).get('levels') or []
        current_price = bars[-1].get('close', 0) if bars else 0
        if pine and current_price:
            above = sorted([l for l in pine if l > current_price])[:3]
            below = sorted([l for l in pine if l < current_price], reverse=True)[:3]
            for level in above + below:
                _draw_line(level, '#7050a0', 1, 2, f'HVN {level:.1f}')
    except Exception as _hvne:
        log.debug(f"[DRAW] HVN levels failed: {_hvne}")

    # ── Differential update: only add NEW shapes and remove OBSOLETE ones ──
    # Each shape has a stable key (its visual content). We track the map
    # {key -> entity_id} between calls. On each redraw:
    #   · Keys present in both: no TV call (shape stays)
    #   · Keys only in new: tv("draw") → store entity_id
    #   · Keys only in old: tv("draw-remove", entity_id) → drop from map
    # This kills the "flicker" entirely — TV only sees the deltas.
    new_shapes = {}  # key -> (price, color, lw, ls, label, textcolor)
    for tup in _pending:
        price, color, lw, ls, lbl, tc = tup
        key = f"{price}|{color}|{lw}|{ls}|{lbl}|{tc}"
        new_shapes[key] = tup  # de-dups identical lines (rare but harmless)

    shape_map = getattr(draw_reasoning, '_shape_map', None) or {}
    new_keys = set(new_shapes.keys())
    old_keys = set(shape_map.keys())

    # First call after process start: shape_map is empty but TV may have
    # orphaned shapes from a previous brain instance (e.g. ENTRY line from
    # the previous trade). The diff would only ADD new ones, leaving the
    # orphans visible. Force a full draw-clear on the very first call to
    # ensure TV state matches our in-memory map.
    if not shape_map and not getattr(draw_reasoning, '_first_call_done', False):
        tv("draw-clear", timeout=8)
        draw_reasoning._first_call_done = True
        log.info("[DRAW] First call after start — TV cleared to flush orphans")

    # Short-circuit: nothing changed
    if new_keys == old_keys and shape_map:
        log.debug(f"[DRAW] skipped — chart unchanged ({len(new_keys)} shapes)")
        return len(shape_map)

    to_add = new_keys - old_keys
    to_remove = old_keys - new_keys
    kept = new_keys & old_keys

    # Remove obsolete shapes
    next_map = {k: shape_map[k] for k in kept}
    for k in to_remove:
        eid = shape_map.get(k)
        if eid:
            tv("draw-remove", str(eid), timeout=8)

    # Add new shapes
    for k in to_add:
        price, color, lw, ls, lbl, tc = new_shapes[k]
        params = {
            'shape': 'horizontal_line',
            'point': {'time': now_ts, 'price': price},
            'overrides': {
                'linecolor': color,
                'linewidth': lw,
                'linestyle': ls,
                'showLabel': True,
                'textcolor': tc,
            },
            'text': lbl,
        }
        r = tv("draw", json.dumps(params), timeout=8)
        if r and r.get('entity_id'):
            next_map[k] = r['entity_id']
            new_ids.append(r['entity_id'])

    # Carry forward kept entity_ids so listing/cleanup still tracks them
    new_ids.extend(eid for k, eid in next_map.items() if k in kept)

    # Cleanup any leftover state table entity_ids from previous brain
    # instances (l'antiga taula que vam revertir el 2026-05-04).
    for _eid in (getattr(draw_reasoning, '_state_table_ids', None) or []):
        try:
            tv("draw-remove", str(_eid), timeout=3)
        except Exception:
            pass
    draw_reasoning._state_table_ids = []

    # ── STATE LABEL drawn LAST — al TOP del rang visible ──
    # Anchor al màxim entre: high recent, totes les zones, working_range.high.
    # Així queda SEMPRE per sobre del price action visible (top del chart).
    _state_label_text = getattr(draw_reasoning, '_state_label', '')
    _state_label_color = getattr(draw_reasoning, '_state_color', '#ffffff')
    if _state_label_text and bars:
        try:
            # Cleanup previous state label
            _prev_state_label_id = getattr(draw_reasoning, '_state_label_id', None)
            if _prev_state_label_id:
                try:
                    tv("draw-remove", str(_prev_state_label_id), timeout=3)
                except Exception:
                    pass
            _last_close = float(bars[-1].get('close', 0))
            # Anchor al màxim entre: high recent, zones, WR.high
            _highs = [b.get('high', 0) for b in bars[-50:] if b.get('high')]
            _max_high = max(_highs) if _highs else _last_close
            _all_max = _max_high
            for _z in zones:
                _zp = _z.get('price', 0)
                if _zp:
                    _all_max = max(_all_max, _zp)
            if _wr and _wr.get('high'):
                _all_max = max(_all_max, _wr.get('high'))
            # +3$ sobre tot — clarament al top
            _label_price = _all_max + 3.0
            # Time: bar més recent (canto dret) + horzLabelsAlign=right
            # → text extends LEFT des del bar més recent → top-right canto
            _label_time = now_ts
            _label_params = {
                'shape': 'horizontal_line',
                'point': {'time': _label_time, 'price': _label_price},
                'overrides': {
                    'linecolor': 'rgba(0,0,0,0)',
                    'linewidth': 0,
                    'showLabel': True,
                    'textcolor': _state_label_color,
                    'fontsize': 14,
                    'bold': True,
                    'horzLabelsAlign': 'right',
                },
                'text': _state_label_text,
            }
            _r = tv("draw", json.dumps(_label_params), timeout=5)
            if _r and _r.get('entity_id'):
                draw_reasoning._state_label_id = _r['entity_id']
                new_ids.append(_r['entity_id'])
        except Exception as _sl_err:
            log.debug(f"[DRAW] state label failed: {_sl_err}")

    _save_drawn_ids(new_ids)
    draw_reasoning._shape_map = next_map
    delta = f"+{len(to_add)} -{len(to_remove)} ={len(kept)}"
    log.info(f"Chart delta: {delta} (total {len(next_map)} shapes)")
    return len(next_map)


# ═══════════════════════════════════════════════════════════════
# PLAN INVALIDATION WATCHER — proactive check between Executor calls
# ═══════════════════════════════════════════════════════════════


_PLAN_INVAL_STATE = {"last_trade_id": None, "last_fire_ts": 0.0}


# ── LLM health tracking: detect calls stuck >3 min ──
_llm_inflight_ts = {'indicator': 0.0, 'executor': 0.0, 'hunter': 0.0,
                    'staging': 0.0, 'reviewer': 0.0}
_llm_inflight_alerted = {k: False for k in _llm_inflight_ts}

# Per-role thresholds. Only triggered on TRUE hangs — normal DeepSeek reasoner
# chain (indicator + reviewer) can eat 6-8 min legitimately.
LLM_WATCHDOG_THRESHOLDS = {
    'indicator': 600,   # 10 min: indicator + reviewer chain
    'executor':  360,   # 6 min: single reasoner call
    'hunter':    180,   # 3 min: chat model, fast
    'staging':   600,   # 10 min: same pipeline as indicator
    'reviewer':  420,   # 7 min: reasoner-only
}
# TG alert enabled per role. Defaulting OFF to avoid spam; enable only when
# debugging a specific issue.
LLM_WATCHDOG_TG_ENABLED = False


def _llm_mark_submit(role):
    _llm_inflight_ts[role] = time.time()
    _llm_inflight_alerted[role] = False


def _llm_mark_done(role):
    _llm_inflight_ts[role] = 0.0
    _llm_inflight_alerted[role] = False


def _llm_watchdog_tick():
    """Emit a one-shot TG alert if any tracked LLM call has been in-flight too long."""
    now = time.time()
    for role, ts in list(_llm_inflight_ts.items()):
        threshold = LLM_WATCHDOG_THRESHOLDS.get(role, 300)
        if ts > 0 and (now - ts) > threshold and not _llm_inflight_alerted[role]:
            _llm_inflight_alerted[role] = True
            elapsed = int(now - ts)
            log.warning(f"[LLM-WATCHDOG] {role} stuck {elapsed}s — still in flight "
                        f"(threshold {threshold}s)")
            if LLM_WATCHDOG_TG_ENABLED:
                try:
                    notify('dd_alert',
                           f'⚠️ LLM `{role}` no respon des de fa {elapsed}s '
                           f'(llindar {threshold}s). Revisa logs.')
                except Exception:
                    pass


def _check_plan_invalidation(bars, account, sig_state):
    """Between Executor calls, watch the last decision's `invalidation_condition`.
    If the structured condition triggers on the current bar, force an Executor
    re-evaluation by setting `force_executor` in brain_controls.json.

    Runs on every FastEngine tick (~3s). Cheap: a file read + a numeric compare.

    Safety:
      · Never cancels trades or sends orders directly. Only triggers Executor re-think.
      · Cooldown: at most one force per trade per 60s to avoid thrashing.
      · Silent degradation: any parse/IO error is swallowed (invalidation check never
        blocks the fast loop).

    Returns True if invalidation fired this tick; False otherwise.
    """
    try:
        if not bars or not account.get('has_signal') or account.get('closing'):
            return False
        trade_id = sig_state.get('id') if hasattr(sig_state, 'get') else None
        if not trade_id:
            return False
        # Cooldown — once per trade per 60s
        now = time.time()
        if (_PLAN_INVAL_STATE.get("last_trade_id") == trade_id
                and (now - _PLAN_INVAL_STATE.get("last_fire_ts", 0)) < 60):
            return False

        last = read_last_executor_decision(trade_id)
        if not last:
            return False
        # Only re-fire if the decision is older than 20s (avoid firing right after
        # the Executor just set a fresh invalidation).
        dec_ts = last.get('ts') or 0
        if (now - float(dec_ts)) < 20:
            return False
        inv_raw = last.get('invalidation_condition')
        parsed = parse_invalidation_condition(inv_raw)
        if not parsed or not parsed.get('structured'):
            return False
        s = parsed['structured']
        inv_dir = s.get('direction')            # 'above' | 'below'
        inv_trigger = s.get('trigger')           # 'close' | 'break'
        inv_price = s.get('price')
        need_vol = bool(s.get('require_volume'))
        if inv_price is None or inv_dir not in ('above', 'below'):
            return False

        last_bar = bars[-1]
        prev_bars = bars[-20:] if len(bars) >= 20 else bars
        # "close" trigger: use last CLOSED bar's close (bars[-2]); "break" uses live high/low
        if inv_trigger == 'close' and len(bars) >= 2:
            check_bar = bars[-2]
            value = check_bar.get('close', 0)
            crossed = (inv_dir == 'below' and value < inv_price) or \
                      (inv_dir == 'above' and value > inv_price)
        else:  # break or unspecified — use live bar high/low
            value = last_bar.get('low', 0) if inv_dir == 'below' else last_bar.get('high', 0)
            crossed = (inv_dir == 'below' and value < inv_price) or \
                      (inv_dir == 'above' and value > inv_price)
        if not crossed:
            return False
        # Volume filter: ratio of last bar vs avg 20 excluding live
        if need_vol:
            if len(prev_bars) < 6:
                return False
            avg_v = sum(b.get('volume', 0) for b in prev_bars[-21:-1]) / max(
                1, min(20, len(prev_bars) - 1))
            cur_v = last_bar.get('volume', 0)
            if avg_v <= 0 or cur_v < avg_v * 1.0:
                return False

        # All conditions met — fire force_executor
        ctrl_path = os.path.join(COMMON, 'brain_controls.json')
        ctrl = {}
        try:
            if os.path.exists(ctrl_path):
                with open(ctrl_path, 'r', encoding='utf-8') as f:
                    ctrl = json.load(f)
        except Exception:
            ctrl = {}
        ctrl['force_executor'] = True
        ctrl['force_executor_reason'] = f'plan_invalidated @ {inv_price} ({inv_dir} {inv_trigger})'
        ctrl['force_executor_ts'] = now
        try:
            with open(ctrl_path, 'w', encoding='utf-8') as f:
                json.dump(ctrl, f, indent=2)
        except Exception:
            pass
        _PLAN_INVAL_STATE["last_trade_id"] = trade_id
        _PLAN_INVAL_STATE["last_fire_ts"] = now
        log.warning(
            f"[PLAN-INVAL] Invalidation condition triggered: "
            f"{inv_dir} {inv_trigger} {inv_price} (value={value:.2f}) — "
            f"forcing Executor reassessment"
        )
        return True
    except Exception as e:
        log.debug(f"[PLAN-INVAL] check error: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════
# TRADE MANAGEMENT — unified rule map (2026-04-24 refactor Fase 1)
# ═══════════════════════════════════════════════════════════════════════
# There are THREE independent layers acting on an open trade. Each has
# one purpose. Do not add new layers without updating this block.
#
# LAYER A — TP ASSIGNMENT (broker-side exit)
#   • assign_staircase_tps() — STRONG contrary zones only, min 5 pts from
#     price, buffer 0.2×ATR. Recalc every 15 s.
#   • Leaves TP untouched if no valid zone. Never regresses.
#
# LAYER B — SL PROGRESSION (peak-lock, 3 stages)
#   • Stage 1 (BE): path ≥ 50% to STRONG TP zone OR peak ≥ $50 floating
#     → SL at blend. MIN 10 pts blend→zone or path trigger is disabled.
#   • Stage 2: peak ≥ $100 → SL at blend + $5/lot favor.
#   • Stage 3: peak ≥ $150 → SL at blend + $10/lot favor.
#   • Each stage fires ONCE per signal (persisted in sig_state).
#   • Also set manually via TG "movemos SL" or Executor MOVE_SL_BE.
#
# LAYER C — AUTOMATIC PARTIAL/FULL EXITS (reflex closes)
#   Priority order (first match wins, mutex via _partial_fired):
#     1. opportunistic_close — FULL close at STRONG target + momentum.
#        Target reached = thesis played out. NOT gated by BE.
#     2. profit_ladder — partial every $40 of floating crossed.
#     3. fast_momentum_partial — partial on 3-bar favorable burst ≥ 2×ATR.
#     4. trailing_from_peak — partial on 30% drawdown from peak ≥ $40.
#     5. zone_touch_partial — partial on contrary zone touch.
#
#   BE GATE: once breakeven_set is true, layers C.2–C.5 are SKIPPED
#   (only Executor CLI can close after BE — deliberate human-in-loop).
#   Layer C.1 (full close at target) still fires — it's a thesis-done
#   exit, not a reflex.
#
#   SINGLE-FIRE MUTEX: at most one partial fires per 3 s tick. Prevents
#   the double-close bug where e.g. ladder + zone_touch agreed at the
#   same moment and closed two tickets.
#
# LAYER D — EXECUTOR (slow, Claude-CLI every 35 s)
#   Bypasses FAST caps. Can PARTIAL_CLOSE, AVERAGE, MOVE_SL_BE, or
#   REDUCE_RISK (= 50% partial + MOVE_SL_BE). Intended for deliberate
#   decisions after analysis.
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# PEAK-LOCK — progressive SL protection (single source of truth)
# ═══════════════════════════════════════════════════════════════
# Triggered from the main loop after FastEngine partial checks.
# Stage 1 fires ONCE per signal at the FIRST of these conditions:
#   · price has covered ≥ S1_PATH_PCT of the distance blend→STRONG contrary zone
#   · peak floating P&L reaches S1_USD (fallback for lot-independent trades)
# Stage 2 and 3 are USD-only (assume lot is meaningful once peak ≥ $100).
# Keeping both triggers for Stage 1 means small-lot trades still get BE from
# the path rule; big-lot trades get BE quickly from the USD rule.
PEAK_LOCK_S1_MIN_ADVANCE = 8.0          # price units — blend must have advanced this far in favor
PEAK_LOCK_S1_MIN_TP_DISTANCE = 10.0     # pts minimum blend→zone for the contrary-STRONG-broken check
# Continuous trailing after Stage 1 (replaces fixed Stages 2/3).
# Distance-based, NOT USD-based: tracks how far price has advanced from blend
# in the favor direction (price units), locks TRAIL_PCT of that peak advance.
# Lot-size independent — works the same with 0.04 or 4 lots.
# Only push toward profit, never retreat. Verify broker actually applied SL.
PEAK_LOCK_TRAIL_MIN_ADVANCE = 5.0       # don't trail until advance ≥ this many price units
PEAK_LOCK_TRAIL_PCT = 0.50              # lock this fraction of peak advance
PEAK_LOCK_TRAIL_MIN_STEP = 0.5          # min SL movement per write (price units)
PEAK_LOCK_VERIFY_TIMEOUT_S = 15.0       # if broker SL ≠ target after this, retry urgent


# ═══════════════════════════════════════════════════════════════
# FAST ENGINE — checks plan conditions every 3s
# ═══════════════════════════════════════════════════════════════

class FastEngine:
    """Local fast checker. Monitors Claude's reversal zones for averaging.

    ANTI-SPAM protections:
    1. Per-zone cooldown: once averaged at a zone, don't touch it for PER_ZONE_COOLDOWN
    2. Max averagings per signal: MAX_AVG_PER_SIGNAL caps total averages
    3. Price deviation requirement: price must move MIN_DEVIATION $ away before re-entering
    4. Projected-DD ceiling: skip if `dd + lot×ATR×100 > DD_PROJ_CEILING × limit`
    5. Execution lock: don't trigger while previous order is being processed
    """

    # ── Tunables ──
    # AGGRESSIVE profile (2026-04-20): trust the auto-invalidation safety net.
    # If a zone fails with volume, zone_lifecycle marks it INVALIDATED and the
    # DFMO fallback takes over. Better to average and let the market correct
    # mistakes than stay out and never profit from ranges.
    MIN_AVG_COOLDOWN = 15      # global cooldown between ANY averagings (sec)
    PER_ZONE_COOLDOWN = 300    # cooldown per specific zone (5 min — fast range retest allowed)
    # 2026-04-30: NO numeric averaging cap. User policy: only the 3.5% DD limit
    # (EA hard stop) and projected-DD ceiling gate averaging. Sentinel value 999
    # keeps the variable for legacy logging but effectively disables count-based
    # blocking. The probabilistic edge is supposed to apply equally from cent 1
    # to cent 3.5%; arbitrary count cuts disrupt the strategy.
    MAX_AVG_PER_SIGNAL = 999
    MIN_DEVIATION = 3.0        # $ price must deviate from last averaged zone (tight XAU ranges)
    # Projected-DD ceiling (see inline check in check()): if adding this AVG
    # would, after 1×ATR adverse move, push DD past this fraction of the limit,
    # skip. PESSIMISTIC by design (uses ATR projection, not current price).
    # This replaces the old upfront "don't even try above X% current DD" gate —
    # a static threshold contradicts the system's philosophy:
    #   · LLM Indicator plans zones with full context (DXY, HTF, structure, DD)
    #   · Re-plans on ZONE_CHANGED / PRICE_MOVED / regime changes
    #   · Auto-invalidates zones broken by volume
    # If the plan survives all that, FastEngine must honor it. Only the projected
    # DD — the honest "could this specific AVG blow us up" question — should gate.
    DD_PROJ_CEILING = 0.85
    ZONE_MATCH_TOLERANCE = 3.0 # zones within $3 are considered "same"
    EXEC_LOCK_TIMEOUT = 10     # wait X sec for order to be processed by EA
    # Minimum zone strength required for averaging.
    # MODERATE = accept both MODERATE and STRONG zones; STRONG gets relaxed confirmation.
    MIN_AVG_STRENGTH = "MODERATE"   # STRONG | MODERATE | WEAK
    # Confirmations required (vol / candle / rsi): 1 of 3 for STRONG zones,
    # 2 of 3 for MODERATE. STRONG = zone IS the confirmation; MODERATE needs help.
    CONFIRMATIONS_STRONG = 1
    CONFIRMATIONS_MODERATE = 2
    _STRENGTH_RANK = {"WEAK": 1, "MODERATE": 2, "STRONG": 3}

    # ── Profit ladder — progressive profit locking ──
    # Every N USD of floating profit reached, close the most profitable ticket.
    PROFIT_LADDER_STEP_USD = 40.0       # close 1 ticket for each $40 floating crossed
    PROFIT_LADDER_MAX_STEPS = 5         # safety cap

    # Fast momentum partial — instant capture on 3-bar favorable burst
    FAST_MOM_MIN_FLOATING_USD = 40.0    # minimum floating to qualify

    # ── Trailing-from-peak — captures reversals ──
    TRAIL_MIN_PEAK_USD = 40.0           # only arm trailing once peak ≥ this
    TRAIL_DRAWDOWN_PCT = 0.30           # fire when floating < peak × (1 − 0.30)
    TRAIL_MAX_FIRES = 3                 # cap spam

    # ── Single-ticket exit thresholds ──
    LADDER_SINGLE_TICKET_USD = 30.0      # partial-close (50%) solo ticket if profit ≥ $30
    LADDER_SINGLE_TICKET_PCT = 50        # take HALF off, let the rest run to TP/trail
    TRAIL_SINGLE_TICKET_PEAK_USD = 60.0  # trail only after peak ≥ $60
    TRAIL_SINGLE_TICKET_DRAWDOWN_PCT = 0.40  # stricter drawdown for full close

    # ── Opportunistic full close — autonomous exit at strong target zone ──
    # When the price reaches (or approaches with momentum) a STRONG contrary
    # zone AND we're in meaningful profit, close EVERYTHING. This is the
    # "the trade made its move, don't get greedy" rule. Distinct from ladder
    # (progressive) and trailing (reversal-based): this is structural closure
    # when the target zone is reached — the thesis has played out.
    OPP_CLOSE_TOLERANCE_ATR = 1.0        # distance in ATR to count as "at zone"
    OPP_CLOSE_MOMENTUM_ATR = 0.4         # min momentum toward zone (3-bar delta)
    # Floating threshold as R-multiple (not $): with a single 0.03 lot the move
    # to the target is worth much less than with 4 tickets averaged. Using R
    # (profit normalized by ATR) makes this size-independent.
    # R = price_move / ATR_M5 per total_lot. We require floating ≥ MIN_R × size-weight.
    OPP_CLOSE_MIN_R = 1.5                # floating equivalent ≥ 1.5 R (for any lot size)
    OPP_CLOSE_MIN_FLOATING_USD = 20.0    # absolute minimum (anti-noise), lowered

    # ── Partial-close tunables (reflex exit at contrary zones) ──
    # When price reaches a zone OPPOSITE to trade direction with floating profit,
    # FastEngine books a partial automatically. Executor is slow (60-150s) and
    # by the time it decides, the price has moved — we miss the peak. This is
    # the fast-lane for profit capture at structural reversal levels.
    MIN_PARTIAL_STRENGTH = "MODERATE"   # STRONG or MODERATE zones only
    PARTIAL_PCT = 100                   # close ENTIRE most-profitable ticket (not a fraction).
                                         # Humans don't close "30% of a ticket"; they close the ticket
                                         # that has the most profit entirely, and keep the rest running.
                                         # This also removes the "per-ticket × 30% < floor" blocker.
    MAX_PARTIALS_PER_SIGNAL = 3         # cap spammy partials
    PARTIAL_PER_ZONE_COOLDOWN = 900     # 15 min per zone (don't repeat same zone)
    # Floor lowered: a 0.03 lot ticket at ~$40 profit now passes the floor.
    # Also switched to "full ticket" mode so smaller tickets still get captured.
    PARTIAL_MIN_PROFIT_USD = 15.0
    PARTIAL_MIN_PROFIT_BALANCE_PCT = 0.025  # 0.025% of balance

    # Persistence file for per-zone cooldown state (survives brain restarts).
    # Without this, the zones_averaged dict is lost on every restart and the
    # engine re-fires the same zone within seconds — observed 2026-04-23.
    STATE_FILE = os.path.join(COMMON, 'brain_fastengine_state.json')

    def __init__(self):
        self.reversal_zones = []
        self.last_avg_time = 0
        self.zones_averaged = {}   # {zone_price: ts_of_last_avg}
        self._load_state()
        self.avg_count = 0          # total averagings in current signal
        self.last_avg_price = None  # price where last avg happened
        self.last_max_deviation = 0 # max deviation since last avg
        self.exec_lock_until = 0    # prevent triggering while EA processing
        self.current_signal_key = None  # (direction, entry_price) to detect signal change
        # Partial-close reflex state
        self.zones_partialed = {}   # {zone_price: ts_of_last_partial}
        self.partial_count = 0      # partials done in current signal
        # Profit-ladder state: highest $X step already locked for current signal
        self.profit_ladder_step = 0
        # Blend ladder — highest $10 step of blend advance already captured
        self.blend_ladder_step = 0
        # Initial lot per ticket — first lot we ever see for a ticket. Used so
        # 25% MODERATE / 50% STRONG capture is computed against the ORIGINAL
        # entry lot, not the dwindling current lot. Lets 4 MODERATE captures
        # = full close (4 × 25% of 0.04 = 0.04) and 2 STRONG = full close.
        self._initial_lot_per_ticket = {}
        # Trailing-from-peak state
        self.peak_floating = 0
        self.trail_fires = 0
        # PEAK-LOCK trail state (Stage 2/3 replacement)
        self.peak_lock_sl_target = 0.0
        self.peak_lock_target_set_ts = 0.0
        self.peak_lock_target_verified = False
        self.peak_advance = 0.0
        # Sizing config: base_lot × fast_engine_multipliers[strength]. Single source
        # of truth lives in config.yaml — change there to retune the whole system.
        _cfg = _load_app_config()
        _sz = _cfg.get('sizing', {}) or {}
        self._base_lot = float(_sz.get('base_lot', 0.03))
        self._fast_multipliers = dict(_sz.get('fast_engine_multipliers', {
            'STRONG': 1, 'MODERATE': 1, 'WEAK': 0,
        }))
        # DFMO fallback config — triggers averaging when zones are invalidated
        # by volume breakout and DFMO shows exhaustion with non-breakout volume.
        self._dfmo_cfg = dict(_cfg.get('dfmo', {}) or {})
        self._last_dfmo_avg_ts = 0

    def update_zones(self, reversal_zones):
        if reversal_zones:
            self.reversal_zones = reversal_zones
            log.info(f"Zones updated: {len(reversal_zones)} reversal zones")

    def _reset_for_new_signal(self, signal_key):
        """Reset per-signal state when signal changes.

        Bug 2026-04-30: at brain startup, current_signal_key=None and the first
        detected signal_key triggered a reset, clearing zones_averaged that had
        been loaded from disk. Result: FastEngine forgot already-averaged zones
        and re-fired automatically. Fix: only reset when transitioning between
        TWO ACTIVE keys, not when seeing a key for the first time after boot.
        """
        if self.current_signal_key == signal_key:
            return  # no change
        # Skip reset on initial detection (current=None means just booted).
        # Just adopt the signal_key without clearing in-memory state, so any
        # zones_averaged loaded from brain_fastengine_state.json are preserved.
        if self.current_signal_key is None:
            log.info(f"Signal first seen: {signal_key}. Preserving disk-restored state (no reset).")
            self.current_signal_key = signal_key
            return
        log.info(f"Signal changed: {self.current_signal_key} -> {signal_key}. Resetting anti-spam state.")
        self.zones_averaged = {}
        self.avg_count = 0
        self.last_avg_price = None
        self.last_max_deviation = 0
        self.zones_partialed = {}
        self.partial_count = 0
        self.profit_ladder_step = 0
        self.blend_ladder_step = 0
        self.peak_floating = 0
        self.trail_fires = 0
        self.peak_lock_sl_target = 0.0
        self.peak_lock_target_set_ts = 0.0
        self.peak_lock_target_verified = False
        self._initial_lot_per_ticket = {}
        self.current_signal_key = signal_key

    def _find_zone_key(self, zp):
        """Find existing zone entry within tolerance, or return zp as new key."""
        for existing_price in self.zones_averaged.keys():
            if abs(existing_price - zp) <= self.ZONE_MATCH_TOLERANCE:
                return existing_price
        return zp

    def _record_initial_lots(self, positions):
        """Record the first-seen lot for every open ticket.

        Called at the start of capture functions so we have a stable reference
        for "X% of original" calculations. New tickets (e.g. fresh AVGs) are
        added the first cycle they appear.
        """
        for p in positions or []:
            try:
                tk = int(p.get('ticket') or p.get('ticket_id') or 0)
                lot = float(p.get('volume', 0) or 0)
            except Exception:
                continue
            if tk and lot > 0 and tk not in self._initial_lot_per_ticket:
                self._initial_lot_per_ticket[tk] = lot

    def _capture_lot_and_pct(self, ticket, current_lot, capture_pct):
        """Compute (lot_to_close, pct_of_current) for a fixed-fraction-of-initial
        capture. Returns (None, None) if can't capture (e.g. would leave <0.01).

        capture_pct is the fraction OF THE INITIAL lot to close. The EA closes
        a percentage of CURRENT, so we convert: lot_target / current_lot × 100.
        """
        try:
            tk = int(ticket)
        except Exception:
            return None, None
        initial = self._initial_lot_per_ticket.get(tk)
        if not initial or initial <= 0:
            # Fallback: never recorded (shouldn't happen if _record_initial_lots
            # was called). Treat current as initial — same effect first call.
            initial = current_lot
        lot_target = round(initial * capture_pct / 100.0, 2)
        if lot_target < 0.01:
            return None, None
        # Cap at current lot — can't close more than what's there.
        if lot_target >= current_lot:
            # Close everything that's left (full close of this ticket).
            lot_target = current_lot
            pct_of_current = 100.0
        else:
            pct_of_current = round(lot_target / current_lot * 100.0, 1)
            # Ensure leaves ≥0.01 broker-side
            if round(current_lot - lot_target, 2) < 0.01:
                lot_target = current_lot
                pct_of_current = 100.0
        return lot_target, pct_of_current

    def mark_order_sent(self, zp, price):
        """Called after an order is successfully sent. Records state."""
        zone_key = self._find_zone_key(zp)
        self.zones_averaged[zone_key] = time.time()
        self.avg_count += 1
        self.last_avg_price = price
        self.last_max_deviation = 0
        self.last_avg_time = time.time()
        self.exec_lock_until = time.time() + self.EXEC_LOCK_TIMEOUT
        self._save_state()
        log.info(f"FAST: Marked zone {zp:.1f} averaged. Count={self.avg_count}")

    def _load_state(self):
        """Restore per-zone averaged timestamps from disk so restarts don't
        reset the cooldown map. Keeps only entries younger than 1h."""
        try:
            if not os.path.exists(self.STATE_FILE):
                return
            with open(self.STATE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            now = time.time()
            # Per-zone cooldown: discard entries > 1h old (cold)
            keep = {}
            for k, v in (data.get('zones_averaged') or {}).items():
                try:
                    ts = float(v)
                    if now - ts < 3600:
                        keep[float(k)] = ts
                except (TypeError, ValueError):
                    continue
            self.zones_averaged = keep
            # Last avg timestamps also restored
            self.last_avg_time = float(data.get('last_avg_time') or 0)
            self.last_avg_price = data.get('last_avg_price')
            if keep:
                log.info(f"FastEngine state restored: {len(keep)} zone cooldown(s) active")
        except Exception as e:
            log.debug(f"FastEngine load_state failed: {e}")

    def _save_state(self):
        """Persist per-zone averaged + partialed maps to disk."""
        try:
            data = {
                'zones_averaged': {str(k): v for k, v in self.zones_averaged.items()},
                'zones_partialed': {str(k): v for k, v in self.zones_partialed.items()},
                'last_avg_time': self.last_avg_time,
                'last_avg_price': self.last_avg_price,
            }
            with open(self.STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.debug(f"FastEngine save_state failed: {e}")

    def _refresh_zones_from_store(self):
        """Re-read ACTIVE zones from disk. zone_lifecycle may have marked some
        INVALIDATED between indicator runs — without this we'd still average at
        a broken zone. Cheap JSON read, idempotent."""
        try:
            from zone_store import read_state, active_zones
            state = read_state(COMMON)
            active = active_zones(state)
            # Transform to the shape the engine expects (price, bounce, strength).
            # bounce_direction → bounce, keep strength as-is. Filter to ones we
            # can average at (have price & strength).
            out = []
            for z in active:
                p = z.get('price')
                b = z.get('bounce_direction')
                s = z.get('strength') or 'MODERATE'
                if p and b:
                    # Keep both 'bounce' (legacy) AND 'bounce_direction' (zone_store canonical
                    # name) so assign_staircase_tps and other consumers work regardless.
                    # Previously only 'bounce' was stored → TP assigner filter by
                    # 'bounce_direction' always missed → "no contrary zones" fallback every tick.
                    out.append({'price': float(p), 'bounce': b, 'bounce_direction': b,
                                'strength': s, 'type': z.get('type')})
            if out:
                self.reversal_zones = out
        except Exception as e:
            log.debug(f"FAST: zone refresh failed ({e}); using cached zones")

    def check(self, bars, account):
        """Check if price is at a reversal zone with confirmation. Returns order or None."""
        if not bars or not account.get('has_signal'):
            return None

        if account.get('closing'):
            return None

        # Trading hours gate: no new averagings during rest hours (Asia session)
        if not is_within_trading_hours():
            return None

        # Refresh zones so INVALIDATED ones are automatically dropped
        self._refresh_zones_from_store()
        if not self.reversal_zones:
            # No ACTIVE zones available → try DFMO fallback directly
            dfmo_order = self._dfmo_fallback(bars, account)
            return dfmo_order

        # Reset state if signal changed
        # Use trade_id (stable per-trade) NOT entry_price/blend (changes on every avg).
        # Bug 2026-04-30: blend changed → signal_key changed → zones_averaged reset
        # → same zone fired 9× in 3 minutes (incident at zone 4556).
        signal_key = (account.get('direction', ''), account.get('trade_id'))
        self._reset_for_new_signal(signal_key)

        # Execution lock (wait for EA to process previous order)
        if time.time() < self.exec_lock_until:
            return None

        # Global cooldown
        if time.time() - self.last_avg_time < self.MIN_AVG_COOLDOWN:
            return None

        # Max averagings per signal
        if self.avg_count >= self.MAX_AVG_PER_SIGNAL:
            return None

        # No upfront DD gate — the projected-DD check inside the zone loop
        # (`dd_if_avg > dd_limit × DD_PROJ_CEILING`) is the honest safety.
        # Trust the LLM-planned zones; let invalidation + projection do the work.

        price = bars[-1]['close']

        # Track max deviation from last avg price
        if self.last_avg_price is not None:
            dev = abs(price - self.last_avg_price)
            if dev > self.last_max_deviation:
                self.last_max_deviation = dev

        direction = account.get('direction', '')
        current_atr = atr(bars, 14) or 5.0
        tolerance = current_atr * 0.3

        # ── Entry-zone settling guard (2026-04-27) ──
        # When the brain enters at zone X, FAST should NOT immediately AVG on
        # the same zone. Bug seen: BUY entered @ 4711 (Executor staged from
        # POC), then 13s later FAST averaged @ 4711 because price was still in
        # the zone and showed a STRONG_BEAR candle (treated as "rejection").
        # That doubled exposure on the entry zone with zero new evidence —
        # operator had to close manually.
        # Skip any AVG zone within `entry_settle_dist` of the entry price until
        # price has moved `entry_settle_move` away from entry. This forces the
        # zone to be "consumed" until the trade has had time to develop.
        entry_price_ref = float(account.get('entry_price') or 0)
        entry_settle_dist = max(tolerance, current_atr * 0.5)  # how close counts as "same zone"
        entry_settle_move = current_atr * 1.5                   # price must move this far
        entry_zone_consumed = (
            entry_price_ref > 0
            and abs(price - entry_price_ref) < entry_settle_move
        )

        closes = [b['close'] for b in bars]
        cur_vol = vol_ratio(bars)
        cur_rsi = rsi(closes, 14) or 50
        # Candle pattern from last CLOSED bar (bars[-2]). Using live bar would
        # produce rejection signals that later flip when the bar finishes.
        cur_candle = candle_type(bars[-2]) if len(bars) >= 2 else candle_type(bars[-1])

        for zone in self.reversal_zones:
            zp = zone.get('price', 0)
            bounce = zone.get('bounce_direction', '')
            strength = zone.get('strength', 'MODERATE')

            if bounce != direction:
                continue

            if not zp or abs(price - zp) > tolerance:
                continue

            # Settling guard: skip the entry zone until price has moved away
            if entry_zone_consumed and abs(zp - entry_price_ref) < entry_settle_dist:
                log.debug(
                    f"FAST AVG skip: zone {zp:.1f} too close to entry {entry_price_ref:.1f} "
                    f"(price has only moved {abs(price - entry_price_ref):.2f}pts < {entry_settle_move:.2f}pts settling distance)"
                )
                continue

            # Strength filter: skip zones below minimum required strength
            zone_rank = self._STRENGTH_RANK.get(strength.upper(), 1)
            min_rank = self._STRENGTH_RANK.get(self.MIN_AVG_STRENGTH.upper(), 2)
            if zone_rank < min_rank:
                continue  # zone too weak for averaging

            # ── Per-zone: ONE SHOT PER SIGNAL (2026-04-24) ──
            # Si ja s'ha fet una averaging en aquesta zona dins del signal
            # actual, no la tornem a tocar. Es reseteja només al canvi de
            # senyal via _reset_for_new_signal. Abans hi havia cooldown de
            # 5 min + deviation, però el user vol que un nivell consumit
            # quedi fora del pla per aquest trade.
            zone_key = self._find_zone_key(zp)
            if zone_key in self.zones_averaged:
                continue  # ja usada aquesta senyal — skip permanent

            # ── Cluster mutex (2026-04-27) ──
            # Treat zones near a previously-averaged zone as the SAME structural
            # block. Prevents the 3-AVGs-in-30s cascade when STRONG levels
            # cluster within an ATR (e.g. 4711/4709/4705 cluster of HTF pivots).
            # cluster_radius is generous: max($5, 1.5×ATR_M1).
            cluster_radius = max(5.0, 1.5 * current_atr)
            in_consumed_cluster = any(
                abs(prev_zp - zp) <= cluster_radius
                for prev_zp in self.zones_averaged.keys()
            )
            if in_consumed_cluster:
                log.debug(
                    f"FAST AVG skip: zone {zp:.1f} within cluster_radius "
                    f"{cluster_radius:.1f}$ of an already-averaged zone"
                )
                continue

            # ── Check confirmations ──
            confirmed = 0
            reasons = []

            if cur_vol >= 1.5:
                confirmed += 1
                reasons.append(f"vol={cur_vol}x")

            if cur_candle in ('HAMMER', 'INV_HAMMER', 'STRONG_BULL', 'STRONG_BEAR'):
                confirmed += 1
                reasons.append(f"candle={cur_candle}")

            if direction == 'BUY' and cur_rsi < 35:
                confirmed += 1
                reasons.append(f"rsi={cur_rsi}")
            elif direction == 'SELL' and cur_rsi > 65:
                confirmed += 1
                reasons.append(f"rsi={cur_rsi}")

            # Required confirmations depend on zone strength.
            # STRONG zone = the zone itself is the primary signal (1 confirmation enough).
            # MODERATE zone = needs more evidence (2 confirmations).
            min_confirmations = (self.CONFIRMATIONS_STRONG if strength.upper() == "STRONG"
                                  else self.CONFIRMATIONS_MODERATE)
            if confirmed >= min_confirmations:
                # Reflex lot via config (base_lot × fast_multipliers[strength]).
                # If multiplier is 0 for this strength, FastEngine stays hands-off
                # and lets the Executor handle it with full context.
                from sizing import fast_engine_lot
                lot = fast_engine_lot(self._base_lot, strength.upper(), self._fast_multipliers)
                if lot is None:
                    continue  # strength disabled for FastEngine — Executor's call
                # NO confluence bonus, NO strength scaling beyond the base
                # multiplier. FastEngine reflexa amb unitat mínima; carregar
                # fort és decisió de l'Executor amb context complet.
                # DD safety check (softer - already have hard stop above)
                dd_if_avg = account['dd_used'] + (lot * current_atr * 100)
                if dd_if_avg > account['dd_limit'] * self.DD_PROJ_CEILING:
                    log.warning(f"Zone {zp:.1f} skipped: projected DD too high ({dd_if_avg:.0f} > {account['dd_limit']*self.DD_PROJ_CEILING:.0f})")
                    return None

                reason_str = " + ".join(reasons)
                log.info(f"FAST TRIGGER at {zp:.1f} [{strength}] (avg#{self.avg_count+1}): {reason_str}")
                # NOTE: caller MUST call engine.mark_order_sent(zp, price) after write_order succeeds
                return {
                    'type': direction,
                    'lot': lot,
                    'zone_price': zp,  # added so main loop can pass to mark_order_sent
                    'comment': f"RZ_{zp:.0f}_{strength[0]}_{reason_str}"
                }

        # Zone path didn't fire — try DFMO fallback (for cases where the nearest
        # zone has been invalidated by breakout and price continues adverse).
        return self._dfmo_fallback(bars, account)

    def _dfmo_fallback(self, bars, account):
        """DESACTIVAT 2026-05-04 — Mode Recorregut Institucional NO usa
        DFMO triggers (era per a averaging legacy). Sempre retorna None.
        La resta del cos del mètode queda com a documentació històrica."""
        return None
        """DFMO-based averaging trigger — ONLY activates AFTER a zone has been
        invalidated by volume breakout in the adverse direction. Concept:

          1. Zone was a valid level, price breaks it with high volume → INVALIDATED
          2. Price is now past that dead zone, extending adversely
          3. DFMO tells us when the extension is exhausting (K exit OB/OS)
          4. We re-enter at this structurally-confirmed exhaustion point

        Without a recent invalidation, DFMO should NOT fire — we don't want it
        as a primary always-on trigger. That would average at any exhaustion,
        even in clean rangebound moves that zones already handle.

        Additional requirements:
          • Volume is NORMAL (1.0-1.5×) — breakout invalidates the DFMO signal
          • Trade is in adverse excursion (not already profitable)
          • Per-signal avg cap + DD guard + cooldowns respected
        """
        if not self._dfmo_cfg.get('enabled', True):
            return None
        if not bars or len(bars) < 40:
            return None
        if not account.get('has_signal'):
            return None
        if account.get('closing'):
            return None
        if not is_within_trading_hours():
            return None

        # Respect global avg cap + exec lock (shared with zone path)
        if self.avg_count >= self.MAX_AVG_PER_SIGNAL:
            return None
        if time.time() < self.exec_lock_until:
            return None
        if time.time() - self.last_avg_time < self.MIN_AVG_COOLDOWN:
            return None
        cooldown = float(self._dfmo_cfg.get('cooldown_seconds', 300))
        if time.time() - self._last_dfmo_avg_ts < cooldown:
            return None

        direction = account.get('direction', '')
        entry_price = float(account.get('entry_price') or 0)
        price = bars[-1]['close']
        if entry_price <= 0:
            return None

        # ── GATE: require a recent zone invalidation in the adverse direction ──
        # For SELL trade: we need a SELL-bounce zone (resistance) above us to have
        # been invalidated by breakout in the last N seconds, AND price to now be
        # past it (still extending adverse). For BUY: support below invalidated.
        invalidation_window_s = float(self._dfmo_cfg.get('invalidation_window_seconds', 900))
        try:
            from zone_store import read_state
            from datetime import datetime as _dt
            st = read_state(COMMON)
            now_ts = time.time()
            recently_invalidated = False
            for z in st.get('zones', []):
                if z.get('status') != 'INVALIDATED':
                    continue
                inv_at = z.get('invalidated_at')
                if not inv_at:
                    continue
                try:
                    inv_ts = _dt.fromisoformat(inv_at.replace('Z', '+00:00')).timestamp()
                except Exception:
                    continue
                if (now_ts - inv_ts) > invalidation_window_s:
                    continue
                # Must be a zone whose bounce matches our direction (adverse = zone
                # that would have reversed us but instead broke)
                zone_bounce = z.get('bounce_direction')
                if zone_bounce != direction:
                    continue
                zone_price = float(z.get('price', 0) or 0)
                # For SELL: invalidated resistance should be BELOW current price (price broke through it upward)
                # For BUY: invalidated support should be ABOVE current price (price broke through it downward)
                if direction == 'SELL' and zone_price < price:
                    recently_invalidated = True
                    break
                if direction == 'BUY' and zone_price > price:
                    recently_invalidated = True
                    break
            if not recently_invalidated:
                return None  # no invalidation context — DFMO stays silent
        except Exception as e:
            log.debug(f"DFMO invalidation check failed: {e}")
            return None

        # AGGRESSIVE profile: allow DFMO to fire in either direction (approach
        # or adverse). If the zone has been invalidated by breakout and price
        # is still extending in the range, DFMO is our best structural signal
        # for when momentum exhausts — whether we're in profit or DD.
        # (Previous behavior required adverse > 0; that was too restrictive.)

        # Use closed bars (drop live bar) for DFMO computation.
        closed = bars[:-1] if len(bars) >= 2 else bars

        # Volume check on the signaling bar (the one that triggered zone END).
        last_closed = closed[-1] if closed else None
        if not last_closed:
            return None
        avg_vol = 0
        if len(closed) >= 21:
            vols = [float(b.get('volume', 0) or 0) for b in closed[-21:-1]]
            if vols:
                avg_vol = sum(vols) / len(vols)
        last_vol = float(last_closed.get('volume', 0) or 0)
        vol_ratio_val = (last_vol / avg_vol) if avg_vol > 0 else 0

        v_min = float(self._dfmo_cfg.get('vol_ratio_min', 1.0))
        v_max = float(self._dfmo_cfg.get('vol_ratio_max', 1.5))
        if vol_ratio_val < v_min or vol_ratio_val >= v_max:
            return None  # either no-force (too low) or breakout (too high)

        # Compute DFMO zone END
        try:
            from dfmo import dfmo_zone_end
        except Exception as e:
            log.warning(f"DFMO import failed: {e}")
            return None
        sig = dfmo_zone_end(
            closed,
            direction=direction,
            ob=float(self._dfmo_cfg.get('ob', 80.0)),
            os_=float(self._dfmo_cfg.get('os', 20.0)),
            stoch_period=int(self._dfmo_cfg.get('stoch_period', 25)),
            k_smooth=int(self._dfmo_cfg.get('k_smooth', 4)),
            d_smooth=int(self._dfmo_cfg.get('d_smooth', 4)),
            rsi_period=int(self._dfmo_cfg.get('rsi_period', 3)),
        )
        if not sig:
            return None

        # All checks passed → fire 1x averaging.
        from sizing import fast_engine_lot
        lot = fast_engine_lot(self._base_lot, 'STRONG', self._fast_multipliers)
        if lot is None:
            return None
        current_atr = atr(bars, 14) or 5.0
        dd_if_avg = account['dd_used'] + (lot * current_atr * 100)
        if dd_if_avg > account['dd_limit'] * self.DD_PROJ_CEILING:
            log.warning(f"DFMO skipped: projected DD too high ({dd_if_avg:.0f})")
            return None

        adverse = (entry_price - price) if direction == 'BUY' else (price - entry_price)
        log.info(
            f"FAST DFMO TRIGGER: {sig['zone']} zone-END "
            f"(K prev={sig['k_prev']} curr={sig['k_curr']}, RSI prev={sig['rsi_prev']} curr={sig['rsi_curr']}) "
            f"vol_ratio={vol_ratio_val:.2f} delta_vs_entry={adverse:+.1f}$ → AVG {direction} {lot}"
        )
        self._last_dfmo_avg_ts = time.time()
        return {
            'type': direction,
            'lot': lot,
            'zone_price': price,  # use current price as "zone" for tracking
            'comment': f"DFMO_{sig['zone']}_K{sig['k_curr']:.0f}_V{vol_ratio_val:.1f}",
        }

    # TP buffer: distance from the actual zone price where TP is placed, so
    # the broker executes BEFORE the zone reversal-level is even touched.
    # For SELL: TP = support + buffer (above support, hit on approach).
    # For BUY:  TP = resistance - buffer (below resistance, hit on approach).
    # This removes dependency on our 3-second scan catching the exact touch.
    TP_BUFFER_ATR = 0.2   # 0.2 × ATR ≈ 1.0-1.5$ ahead of the zone

    def check_staged_entry(self, bars, account):
        """DESACTIVAT 2026-05-06.

        Aquesta era la via VELLA de fire (find_triggered amb confirmacions
        clàssiques: rejection_candle + vol_ratio). Ara TOTA la lògica de fire
        passa per `try_fire_staged_setup` → `_evaluate_and_fire_single` que
        suporta entry_mode "instant" / "confirmed" / "wick" amb les fixes
        d'avui (apply_trade_plan skip recorregut, vol filter eliminat per
        FULL_CLOSE, tick-based safety net, etc.).

        Mantenir aquesta via paral·lela causava bugs (firejava setups en mode
        wick com si fossin confirmed, ignorant la nova lògica). Retorna
        sempre None — la única ruta de fire activa és try_fire_staged_setup.
        """
        return None

    def mark_staged_fired(self, setup_id):
        try:
            import staged_setups
            staged_setups.mark_fired(setup_id)
        except Exception:
            pass
        self.exec_lock_until = time.time() + self.EXEC_LOCK_TIMEOUT

    def assign_staircase_tps(self, bars, account):
        """Assign TP on each open ticket to a target zone, escalat + buffer.

        Each ticket gets assigned to a different target zone. For multi-ticket
        averaging scenarios, tickets are matched to zones such that the
        resulting TP is ALWAYS on the profit side of THAT ticket's entry —
        never on the loss side. The worst-entry ticket (farthest from profit)
        goes to the farthest zone; the best-entry ticket to the nearest.

        Four hard guards make this safe even when the book is deep in DD:

          1. Profit-side-of-ENTRY: TP must improve P&L vs the ticket's own
             entry price (not just vs current market). This prevents writing
             a TP on the wrong side of our cost basis when price has rallied
             past the entry (the bug that killed the TT SELL on 2026-04-21).

          2. Profit-side-of-PRICE: TP must be in the profit direction from
             current market (SELL: below price; BUY: above price). Otherwise
             the broker fires it instantly.

          3. Minimum distance from price: TP must be ≥ 0.3 × ATR away from
             current price. Prevents immediate execution on volatility spike.

          4. Monotonic toward profit: TP can only MOVE toward profit, never
             backward. SELL: new_tp ≤ current_tp. BUY: new_tp ≥ current_tp.
             Once a good TP is set, the broker exit is locked in; it can
             improve if better zones appear, but it can never retreat.

        If no valid zone exists for a ticket, its TP is left untouched — we
        do NOT write a losing TP as a fallback. FAST PARTIAL + executor
        remain responsible for such tickets.

        Returns list of (ticket, new_tp, zone_price) assignments to send.
        """
        positions = account.get('positions') or []
        if not positions:
            return []
        direction = account.get('direction', '')
        if direction not in ('BUY', 'SELL'):
            return []
        if not bars:
            return []

        current_atr = atr(bars, 14) or 5.0
        buffer = current_atr * self.TP_BUFFER_ATR
        # Guard #3 — minimum distance from price. Original 0.3×ATR was too
        # permissive on XAUUSD (typical M1 ATR 1-2$ → min_dist 0.3-0.6pts),
        # which let a MODERATE zone 1-2pts away become the TP target
        # (incident 2026-04-24 09:36 — TP at 4682.9 with price at 4684.4,
        # ~1.5pts away = scalp noise). Floor at 5pts absolute and 1×ATR:
        # whichever is larger. This makes the broker-side TP a real target,
        # not noise.
        min_dist = max(5.0, current_atr * 1.0)

        # Candidate TP zones: contrary bounce, STRONG ONLY (MODERATE zones
        # are too ephemeral — they appear/disappear between indicator cycles
        # and often sit 1-3pts from price, dragging TP into noise). If no
        # STRONG zone is available on the profit side, the fallback below
        # synthesizes a 3×ATR M15 target instead.
        contrary_bounce = 'BUY' if direction == 'SELL' else 'SELL'
        try:
            price = float(positions[0].get('price_current', 0) or 0)
        except Exception:
            price = bars[-1]['close']
        if price <= 0:
            return []
        # ── EXECUTOR-DRIVEN TP CANDIDATES (preferred) ──
        # When the trade was opened from a staged setup, the LLM's profit_targets
        # are persisted on sig_state. They reflect the situational reasoning
        # (session, ATR, structure, news) for THIS specific trade. Use them
        # ahead of the generic STRONG-zone scan so the broker-side TP matches
        # the tactical plan instead of the geometric "any STRONG zone" pick.
        executor_targets_live: list[float] = []
        try:
            from signal_state import get_state as _gs
            _ss = _gs()
            _ep = _ss.get('executor_plan') if _ss else None
            if isinstance(_ep, dict):
                for _t in (_ep.get('profit_targets') or []):
                    # Accept both legacy floats and new dict schema.
                    if isinstance(_t, dict):
                        _t = _t.get('price')
                    try:
                        _tp = float(_t) if _t is not None else None
                    except (TypeError, ValueError):
                        continue
                    if _tp is None:
                        continue
                    # Profit side of price + min distance gate.
                    if direction == 'SELL' and _tp >= price:
                        continue
                    if direction == 'BUY' and _tp <= price:
                        continue
                    if abs(_tp - price) < min_dist:
                        continue
                    executor_targets_live.append(_tp)
        except Exception:
            executor_targets_live = []

        candidates = []
        if executor_targets_live:
            # Use the LLM's targets directly — already on the profit side and
            # past min_dist. Sort closest-first (matches FAST's existing
            # nearest-first contract; per-ticket guards below stay intact).
            candidates = sorted(executor_targets_live, key=lambda t: abs(t - price))
        else:
            for z in self.reversal_zones:
                if (z.get('bounce_direction') or '').upper() != contrary_bounce:
                    continue
                if (z.get('strength') or '').upper() != 'STRONG':
                    continue
                zp = float(z.get('price', 0) or 0)
                if zp <= 0:
                    continue
                # guard #2: profit side of current price
                if direction == 'SELL' and zp >= price:
                    continue
                if direction == 'BUY' and zp <= price:
                    continue
                candidates.append(zp)
        # Skip ATR fallback when executor targets exist — if all are
        # invalidated by per-ticket guards below, leaving the TP untouched
        # is the correct behavior (the LLM's plan is the source of truth).
        if not candidates and not executor_targets_live:
            # Fallback: zones-map biased or incomplete (e.g. all resistances on a
            # BEARISH bias map, leaving a SELL trade with no support below).
            # Synthesize a conservative TP at ~3× ATR M15 away from the nearest
            # ticket's entry so the trade has SOME broker-side exit. If any
            # genuine zone later emerges in the profit direction, the monotonic
            # guard + next recalc will replace this fallback TP with the real one.
            try:
                bars_m15 = aggregate_bars(bars, 3)
                atr_m15 = atr(bars_m15, 14) or current_atr * 3
            except Exception:
                atr_m15 = current_atr * 3
            fallback_dist = max(atr_m15 * 3.0, min_dist * 2)
            if direction == 'SELL':
                fallback_tp = price - fallback_dist
            else:
                fallback_tp = price + fallback_dist
            candidates = [round(fallback_tp, 2)]
            log.info(f"[FAST] TP fallback — no contrary zones available for {direction}; "
                     f"using ATR-based target {fallback_tp:.2f} ({fallback_dist:.1f}$ from price)")

        # Sort candidates by proximity to current price (nearest first).
        # SELL: nearest support below = highest below (desc).
        # BUY:  nearest resistance above = lowest above (asc).
        candidates = sorted(candidates, reverse=(direction == 'SELL'))

        # Sort tickets so the WORST entry (smallest profit cushion) takes the
        # NEAREST TP zone — locks the most-exposed ticket first. The BEST entry
        # rides toward the farthest zone with the largest cushion to absorb a
        # reversal. Closing best-first would strand the worst ticket without
        # cover; closing worst-first leaves a better-blended remainder.
        # For SELL: lowest entry = worst (sold cheapest); for BUY: highest = worst.
        if direction == 'SELL':
            tickets_sorted = sorted(positions, key=lambda p: float(p.get('price_open', 0) or 0))
        else:
            tickets_sorted = sorted(positions, key=lambda p: float(p.get('price_open', 0) or 0), reverse=True)

        assignments = []
        used_zones = set()
        for p in tickets_sorted:
            tk = p.get('ticket') or p.get('ticket_id')
            if not tk:
                continue
            entry = float(p.get('price_open', 0) or 0)
            if entry <= 0:
                continue
            current_tp = float(p.get('tp', 0) or 0)

            # Pick the nearest unused zone that satisfies ALL guards for THIS ticket.
            chosen = None
            chosen_tp = None
            for zp in candidates:
                if zp in used_zones:
                    continue
                # Buffer: TP slightly ahead of zone, toward current price
                if direction == 'SELL':
                    tp_target = round(zp + buffer, 2)
                else:
                    tp_target = round(zp - buffer, 2)
                # guard #1: profit side of ENTRY
                if direction == 'SELL' and tp_target >= entry:
                    continue
                if direction == 'BUY' and tp_target <= entry:
                    continue
                # guard #2 (re-check after buffer): profit side of PRICE
                if direction == 'SELL' and tp_target >= price:
                    continue
                if direction == 'BUY' and tp_target <= price:
                    continue
                # guard #3: minimum distance from current price
                if abs(tp_target - price) < min_dist:
                    continue
                # guard #4: monotonic toward profit vs existing TP.
                # Exception: allow overwriting ONLY if current TP is clearly a
                # stale fallback — specifically, more than 1×ATR away from any
                # real zone AND pointing to unreachable price. This prevents the
                # "TP ping-pong" observed 2026-04-23 (41 MODIFY_TP in 20 min).
                zone_prices = [float(zz.get('price', 0) or 0) for zz in self.reversal_zones]
                tol_zone = max(2.0, current_atr * 1.0)
                current_tp_near_zone = any(
                    abs(current_tp - zp_) <= tol_zone for zp_ in zone_prices
                ) if current_tp > 0 else False
                if current_tp > 0 and current_tp_near_zone:
                    # Current TP is a real zone-based target → enforce monotonic
                    if direction == 'SELL' and tp_target >= current_tp:
                        continue  # would retreat vs real zone TP
                    if direction == 'BUY' and tp_target <= current_tp:
                        continue
                chosen = zp
                chosen_tp = tp_target
                break

            if chosen is None:
                continue  # no valid zone for this ticket — leave TP untouched

            # Skip write if change is trivially small (already set close enough)
            if current_tp > 0 and abs(current_tp - chosen_tp) < 0.3:
                used_zones.add(chosen)
                continue

            assignments.append((tk, chosen_tp, chosen))
            used_zones.add(chosen)
        return assignments

    def check_opportunistic_close(self, bars, account):
        """Autonomous full close when price reaches a STRONG target zone with
        substantial profit. This is the most aggressive exit — close ALL
        tickets at once. Fires when:

        1. Price is within OPP_CLOSE_TOLERANCE_ATR of a STRONG contrary zone
        2. Momentum last 3 bars is toward the zone (price is going in our favor)
        3. Total floating ≥ OPP_CLOSE_MIN_FLOATING_USD

        Returns a dict with {full_close: True} or None.
        """
        if not self.reversal_zones or not bars or not account.get('has_signal'):
            return None
        if account.get('closing'):
            return None
        if not is_within_trading_hours():
            return None
        if time.time() < self.exec_lock_until:
            return None

        positions = account.get('positions') or []
        if not positions:
            return None

        def ticket_profit(p):
            try: return broker_position_pnl(p)
            except Exception: return 0.0

        floating = sum(ticket_profit(p) for p in positions)
        if floating < self.OPP_CLOSE_MIN_FLOATING_USD:
            return None

        direction = account.get('direction', '')
        price = bars[-1]['close']
        # For touch detection use wick extreme of the live bar (and last closed)
        # so wick-touches of the target zone count. Price spikes that touch and
        # retrace must still trigger the close — this was the user's complaint.
        last = bars[-1]
        prev = bars[-2] if len(bars) >= 2 else last
        if direction == 'SELL':
            touch_price = min(float(last.get('low', price) or price),
                              float(prev.get('low', price) or price))
        else:
            touch_price = max(float(last.get('high', price) or price),
                              float(prev.get('high', price) or price))
        current_atr = atr(bars, 14) or 5.0
        tolerance = current_atr * self.OPP_CLOSE_TOLERANCE_ATR

        # R-multiple check (size-independent):
        # Gross price move in our favor = abs(price - weighted_entry).
        # R = move / ATR. We require R ≥ OPP_CLOSE_MIN_R (default 1.5)
        total_lots = sum(float(p.get('volume', 0) or 0) for p in positions)
        w_sum = sum(float(p.get('volume', 0) or 0) * float(p.get('price_open', 0) or 0) for p in positions)
        w_entry = (w_sum / total_lots) if total_lots > 0 else 0
        if w_entry > 0 and current_atr > 0:
            if direction == 'SELL':
                move_usd = w_entry - price
            else:
                move_usd = price - w_entry
            r_mult = move_usd / current_atr
            if r_mult < self.OPP_CLOSE_MIN_R:
                return None

        # Momentum toward profit over last 3 closed bars
        momentum_toward_profit = 0
        if len(bars) >= 4:
            delta = bars[-1]['close'] - bars[-4]['close']
            momentum_toward_profit = (-delta) if direction == 'SELL' else delta

        if momentum_toward_profit < current_atr * self.OPP_CLOSE_MOMENTUM_ATR:
            return None

        # Find nearest STRONG contrary zone (where bounce == direction for profit side)
        # Use touch_price (wick extreme) for zone distance — a wick-touch still counts.
        contrary_bounce = 'BUY' if direction == 'SELL' else 'SELL'
        best_zone = None
        best_dist = float('inf')
        for zone in self.reversal_zones:
            if (zone.get('bounce_direction') or '').upper() != contrary_bounce:
                continue
            if (zone.get('strength') or '').upper() != 'STRONG':
                continue
            zp = float(zone.get('price', 0) or 0)
            if zp <= 0:
                continue
            if direction == 'SELL' and zp >= touch_price:
                continue
            if direction == 'BUY' and zp <= touch_price:
                continue
            d = abs(touch_price - zp)
            if d < best_dist:
                best_dist = d
                best_zone = zone

        if not best_zone:
            return None
        if best_dist > tolerance:
            return None

        log.info(
            f"FAST OPPORTUNISTIC CLOSE: touch {touch_price:.2f} (close {price:.2f}) "
            f"at STRONG target zone {best_zone.get('price'):.1f} (dist {best_dist:.2f}$), "
            f"floating=${floating:.2f}, momentum=+${momentum_toward_profit:.2f}/3bars → CLOSE ALL"
        )
        return {
            'full_close': True,
            'zone_price': float(best_zone.get('price', 0)),
            'floating': floating,
        }

    def mark_opportunistic_close(self):
        self.exec_lock_until = time.time() + self.EXEC_LOCK_TIMEOUT

    def check_profit_ladder(self, bars, account):
        """Progressive profit locking by milestones — independent of zones.

        Every PROFIT_LADDER_STEP_USD of floating profit crossed, close the
        most-profitable ticket. Acts like a "staircase" of profit capture:
          · Step 1: floating hits $40 → close best ticket → realized
          · Step 2: floating hits $80 (or any new cumulative from what's left) → close next
          · etc.

        Requires >= 2 tickets. Floating is computed from remaining positions
        (what's still open) — so after a step closes, floating resets and
        we wait for the NEXT milestone from the new baseline.

        Returns partial dict or None.
        """
        if not account.get('has_signal'):
            return None
        if account.get('closing'):
            return None
        if self.partial_count >= self.MAX_PARTIALS_PER_SIGNAL:
            return None
        if self.profit_ladder_step >= self.PROFIT_LADDER_MAX_STEPS:
            return None
        if time.time() < self.exec_lock_until:
            return None

        positions = account.get('positions') or []
        if not positions:
            return None

        def ticket_profit(p):
            try: return broker_position_pnl(p)
            except Exception: return 0.0

        floating = sum(ticket_profit(p) for p in positions)

        # Different thresholds for single vs multi-ticket:
        #  · Multi-ticket: every PROFIT_LADDER_STEP_USD = close 1 ticket (full)
        #  · Single-ticket: at LADDER_SINGLE_TICKET_USD = partial-close 50% (half off)
        single_ticket_mode = (len(positions) == 1)
        if single_ticket_mode:
            if floating < self.LADDER_SINGLE_TICKET_USD:
                return None
            # Single-ticket ladder fires once — it reduces the solo ticket by
            # LADDER_SINGLE_TICKET_PCT, the rest rides to TP/trailing.
            if self.profit_ladder_step >= 1:
                return None
        else:
            if floating < self.PROFIT_LADDER_STEP_USD:
                return None

        # Winning tickets only
        winning = [p for p in positions if ticket_profit(p) > 0]
        if not winning:
            return None

        target = max(winning, key=ticket_profit)
        tk_profit = ticket_profit(target)
        tk = target.get('ticket') or target.get('ticket_id')
        if not tk:
            return None

        # Ensure this step's realized would actually be ≥ floor (anti-noise)
        balance = account.get('balance', 0) or 0
        floor_usd = max(self.PARTIAL_MIN_PROFIT_USD, balance * self.PARTIAL_MIN_PROFIT_BALANCE_PCT / 100.0)
        if tk_profit < floor_usd:
            return None

        # Single-ticket: partial (half off). Multi-ticket: full close of best ticket.
        close_pct = self.LADDER_SINGLE_TICKET_PCT if single_ticket_mode else 100
        expected = tk_profit * (close_pct / 100.0)
        log.info(
            f"FAST PROFIT-LADDER STEP #{self.profit_ladder_step+1} "
            f"(floating ${floating:.2f} mode={'single-half' if single_ticket_mode else 'multi-full'}) "
            f"→ ticket {tk} close {close_pct}% ≈${expected:.2f}"
        )
        return {
            'ticket': int(tk),
            'pct': close_pct,
            'zone_price': 0,   # not zone-based
            'strength': 'LADDER',
            'expected_realized': round(expected, 2),
            'is_ladder': True,
        }

    def mark_ladder_fired(self):
        self.profit_ladder_step += 1
        self.partial_count += 1
        self.exec_lock_until = time.time() + self.EXEC_LOCK_TIMEOUT

    def check_fast_momentum_partial(self, bars, account):
        """Instant partial capture on strong favorable momentum.

        Fires when:
          · Momentum over last 3 M5 bars ≥ 2 × ATR in profit direction
          · Current floating ≥ FAST_MOM_MIN_FLOATING_USD
          · At least 1 winning ticket (profit > floor)
          · Not in exec_lock, below partial cap

        Idea: a big sudden move in our favor is a gift; book some before the
        whipsaw reverses. Independent of zones (so captures spikes mid-range).
        Cap-protected via MAX_PARTIALS_PER_SIGNAL.
        """
        if not account.get('has_signal') or account.get('closing'):
            return None
        if self.partial_count >= self.MAX_PARTIALS_PER_SIGNAL:
            return None
        if time.time() < self.exec_lock_until:
            return None
        positions = account.get('positions') or []
        if not positions or not bars or len(bars) < 4:
            return None

        direction = account.get('direction', '')
        if direction not in ('BUY', 'SELL'):
            return None

        # Momentum over last 3 closed bars
        delta = bars[-1]['close'] - bars[-4]['close']
        favorable = (direction == 'BUY' and delta > 0) or (direction == 'SELL' and delta < 0)
        if not favorable:
            return None

        cur_atr = atr(bars, 14) or 5.0
        # Require momentum ≥ 2 × ATR in profit direction (strong 3-bar burst)
        if abs(delta) < 2.0 * cur_atr:
            return None

        def ticket_profit(p):
            try: return broker_position_pnl(p)
            except Exception: return 0.0

        floating = sum(ticket_profit(p) for p in positions)
        if floating < self.FAST_MOM_MIN_FLOATING_USD:
            return None

        winning = [p for p in positions if ticket_profit(p) > 0]
        if not winning:
            return None
        target = max(winning, key=ticket_profit)
        tk = target.get('ticket') or target.get('ticket_id')
        tk_profit = ticket_profit(target)
        if not tk:
            return None

        balance = account.get('balance', 0) or 0
        floor_usd = max(self.PARTIAL_MIN_PROFIT_USD, balance * self.PARTIAL_MIN_PROFIT_BALANCE_PCT / 100.0)
        if tk_profit < floor_usd:
            return None

        log.info(
            f"FAST MOMENTUM PARTIAL — delta 3bars={delta:+.2f}$ (≥ 2×ATR {2*cur_atr:.2f}) "
            f"floating ${floating:.2f} → close ticket {tk} profit=${tk_profit:.2f}"
        )
        return {
            'ticket': int(tk),
            'pct': 100,
            'zone_price': 0,
            'strength': 'MOMENTUM',
            'expected_realized': round(tk_profit, 2),
            'is_momentum': True,
        }

    def check_trailing(self, bars, account):
        """Trailing-from-peak partial. Tracks max floating profit seen; if the
        floating drops by TRAIL_DRAWDOWN_PCT from the peak AND peak was ≥
        TRAIL_MIN_PEAK_USD, close 1 ticket. This captures reversals without
        depending on zones — it's a pure "don't give back what you had" rule.
        """
        if not account.get('has_signal'):
            return None
        if account.get('closing'):
            return None
        if self.trail_fires >= self.TRAIL_MAX_FIRES:
            return None
        if time.time() < self.exec_lock_until:
            return None

        positions = account.get('positions') or []
        if not positions:
            return None

        def ticket_profit(p):
            try: return broker_position_pnl(p)
            except Exception: return 0.0

        floating = sum(ticket_profit(p) for p in positions)

        # Update peak
        if floating > self.peak_floating:
            self.peak_floating = floating

        # Different arm thresholds for single vs multi-ticket
        if len(positions) == 1:
            min_peak = self.TRAIL_SINGLE_TICKET_PEAK_USD
            drawdown_pct = self.TRAIL_SINGLE_TICKET_DRAWDOWN_PCT
        else:
            min_peak = self.TRAIL_MIN_PEAK_USD
            drawdown_pct = self.TRAIL_DRAWDOWN_PCT

        if self.peak_floating < min_peak:
            return None

        drawdown_trigger = self.peak_floating * (1 - drawdown_pct)
        if floating >= drawdown_trigger:
            return None

        # Pick most-profitable ticket still in profit
        winning = [p for p in positions if ticket_profit(p) > 0]
        if not winning:
            return None
        target = max(winning, key=ticket_profit)
        tk_profit = ticket_profit(target)
        tk = target.get('ticket') or target.get('ticket_id')
        if not tk:
            return None

        # Need at least the floor
        balance = account.get('balance', 0) or 0
        floor_usd = max(self.PARTIAL_MIN_PROFIT_USD, balance * self.PARTIAL_MIN_PROFIT_BALANCE_PCT / 100.0)
        if tk_profit < floor_usd:
            return None

        log.info(
            f"FAST TRAILING TRIGGER #{self.trail_fires+1} — peak ${self.peak_floating:.2f} "
            f"→ now ${floating:.2f} (drop {((self.peak_floating - floating)/self.peak_floating*100):.0f}%). "
            f"Close ticket {tk} profit=${tk_profit:.2f}"
        )
        return {
            'ticket': int(tk),
            'pct': 100,
            'zone_price': 0,
            'strength': 'TRAIL',
            'expected_realized': round(tk_profit, 2),
            'is_trail': True,
        }

    def mark_trail_fired(self):
        self.trail_fires += 1
        self.partial_count += 1
        # Reset peak to current floating so next trail needs a fresh peak
        self.peak_floating = 0
        self.exec_lock_until = time.time() + self.EXEC_LOCK_TIMEOUT

    def check_partial(self, bars, account):
        """Reflex partial-close when price reaches a contrary zone with profit.

        Mirror of `check()` but for EXIT instead of entry. When price touches a
        zone with bounce OPPOSITE to the trade direction (i.e. a zone that would
        push price our way), and we're already in significant profit, we book
        a partial without waiting for the Executor (which takes 60-150s and
        would miss the peak at fast rangebound reversals).

        Returns: {'ticket': int, 'pct': int, 'zone_price': float} or None.
        """
        if not self.reversal_zones or not bars or not account.get('has_signal'):
            return None
        if account.get('closing'):
            return None
        if not is_within_trading_hours():
            return None

        # Reset state if signal changed
        # Use trade_id (stable per-trade) NOT entry_price/blend (changes on every avg).
        # Bug 2026-04-30: blend changed → signal_key changed → zones_averaged reset
        # → same zone fired 9× in 3 minutes (incident at zone 4556).
        signal_key = (account.get('direction', ''), account.get('trade_id'))
        self._reset_for_new_signal(signal_key)

        # Max partials per signal cap (anti-spam)
        if self.partial_count >= self.MAX_PARTIALS_PER_SIGNAL:
            return None

        # Execution lock (shared with averaging path)
        if time.time() < self.exec_lock_until:
            return None

        positions = account.get('positions') or []
        if not positions:
            return None

        # ── Single-ticket guard ──
        # With only 1 open ticket, "close 100% of most profitable ticket" = full
        # close. That's premature when the trade just opened and hasn't averaged:
        # a fresh single-ticket trade should be allowed to develop. Partials are
        # a multi-ticket capture strategy — first average, THEN partial as price
        # reverses. If the only ticket needs to exit, it'll be via TG cerramos,
        # Executor structural decision, or BE/DD safety nets. Not reflex.
        if len(positions) < 2 and self.avg_count < 1:
            return None

        direction = account.get('direction', '')
        price = bars[-1]['close']
        current_atr = atr(bars, 14) or 5.0
        # Zone-partial tolerance: TIGHT (0.3 × ATR). The zone path only fires
        # on exact touch + breakout filter below. The "catch fast moves" role
        # has moved to profit_ladder and trailing — zone-partial is now a minor
        # third path, only meaningful when the zone really is being tested.
        tolerance = current_atr * 0.3

        # Profit floor: max(absolute, balance%). Partial must book ≥ floor.
        balance = account.get('balance', 0) or 0
        floor_usd = max(self.PARTIAL_MIN_PROFIT_USD, balance * self.PARTIAL_MIN_PROFIT_BALANCE_PCT / 100.0)

        # For a SELL trade: bounce='BUY' means zone pushes price UP (good for our SELL).
        # For a BUY trade: bounce='SELL' means zone pushes price DOWN (good for our BUY).
        # So contrary zone (profit-taking zone) has bounce == direction.
        # Wait — re-think: bounce is the direction price will go AFTER the zone.
        # If we're SELL and price drops to a zone with bounce=BUY, that zone pushes
        # price back up → cuts our profit. We want to partial BEFORE that rebound.
        # If we're BUY and price rises to a zone with bounce=SELL, same logic.
        # So the partial trigger is when price touches a zone where bounce is OPPOSITE
        # to our direction — that zone is going to reverse us.
        contrary_bounce = 'BUY' if direction == 'SELL' else 'SELL'

        # Momentum estimate: delta over last 3 closed bars in the profit direction.
        # For SELL, profit-direction movement is NEGATIVE price delta; for BUY, positive.
        momentum_toward_profit = 0
        if len(bars) >= 4:
            delta = bars[-1]['close'] - bars[-4]['close']
            if direction == 'SELL':
                momentum_toward_profit = -delta  # positive if price dropping (good)
            else:
                momentum_toward_profit = delta

        for zone in self.reversal_zones:
            zp = zone.get('price', 0)
            bounce = zone.get('bounce_direction', '')
            strength = zone.get('strength', 'MODERATE')

            if bounce != contrary_bounce:
                continue
            if not zp:
                continue

            dist_to_zone = abs(price - zp)
            # Zone-partial fires ONLY on exact touch. "Approach" trigger removed
            # — it was too eager on fast moves. Profit capture on fast reversals
            # is handled by profit_ladder + trailing-from-peak instead, which
            # don't depend on price being near a specific zone.
            is_touch = dist_to_zone <= tolerance
            if not is_touch:
                continue

            # Strength filter
            zone_rank = self._STRENGTH_RANK.get(strength.upper(), 1)
            min_rank = self._STRENGTH_RANK.get(self.MIN_PARTIAL_STRENGTH.upper(), 2)
            if zone_rank < min_rank:
                continue

            # Per-zone: one-shot per signal (legacy path — also respects
            # the consumed-zone rule).
            zone_key = self._find_zone_key_dict(zp, self.zones_partialed)
            if zone_key in self.zones_partialed:
                continue

            # ── BREAKOUT FILTER ──
            # If the last CLOSED bar has punched clearly past the zone with high
            # volume, it's a breakout — price will likely continue further, so
            # skip the partial and let the trade run. For SELL profit side the
            # contrary zone is a SUPPORT below us: breakout = close < zone − dist
            # with high volume. For BUY profit side (resistance above): breakout
            # = close > zone + dist with high volume.
            if len(bars) >= 21:
                closed_bars_for_vol = bars[:-1] if len(bars) >= 2 else bars
                last_closed = closed_bars_for_vol[-1] if closed_bars_for_vol else None
                if last_closed:
                    last_close = float(last_closed.get('close', 0) or 0)
                    last_vol = float(last_closed.get('volume', 0) or 0)
                    vols = [float(b.get('volume', 0) or 0) for b in closed_bars_for_vol[-21:-1]]
                    avg_v = (sum(vols) / len(vols)) if vols else 0
                    vol_r = (last_vol / avg_v) if avg_v > 0 else 0
                    breakout_dist = max(1.0, current_atr * 0.5)  # need real punch, not noise
                    is_breakout = False
                    if direction == 'SELL':
                        # contrary zone is support BELOW; breakout = close below
                        if last_close < (zp - breakout_dist) and vol_r > 1.5:
                            is_breakout = True
                    else:  # BUY
                        # contrary zone is resistance ABOVE; breakout = close above
                        if last_close > (zp + breakout_dist) and vol_r > 1.5:
                            is_breakout = True
                    if is_breakout:
                        log.info(f"PARTIAL SKIP: breakout past {zp:.1f} detected "
                                 f"(close={last_close:.1f}, vol={vol_r:.1f}×) — let trade run")
                        continue

            # Pick the ticket with highest per-unit profit (most profit from reversal).
            # For a trade entered high (SELL) that averaged higher, the FIRST ticket
            # (lowest entry for SELL, highest entry for BUY) has the most unit-profit
            # when price reverses toward it. MT5 reports `profit` per ticket — use it.
            # We close PARTIAL_PCT of that ticket; its realized PnL must exceed floor.
            def ticket_profit(p):
                try: return broker_position_pnl(p)
                except Exception: return 0.0
            # Only tickets currently in profit are candidates
            winning = [p for p in positions if ticket_profit(p) > 0]
            if not winning:
                continue
            target = max(winning, key=ticket_profit)
            tk_profit = ticket_profit(target)
            expected_realized = tk_profit * (self.PARTIAL_PCT / 100.0)
            if expected_realized < floor_usd:
                continue  # noise — not worth it

            tk = target.get('ticket') or target.get('ticket_id')
            if not tk:
                continue

            # ── Blend-guard: skip if closing leaves the remaining book upside-down ──
            # We pick winners by per-ticket profit, but that can strand the worst
            # entry (farthest from price). Compute the weighted blend of what would
            # REMAIN after this partial; if that blend ends up adverse to price
            # (blend > price for BUY, blend < price for SELL), closing books a
            # small win but leaves an exposed losing book — let it run instead.
            try:
                remaining = [p for p in positions if (p.get('ticket') or p.get('ticket_id')) != tk]
                rem_vol = sum(float(p.get('volume', 0) or 0) for p in remaining)
                if rem_vol > 0:
                    rem_blend = sum(float(p.get('volume', 0) or 0) * float(p.get('price_open', 0) or 0)
                                    for p in remaining) / rem_vol
                    adverse = (direction == 'BUY' and rem_blend > price) or \
                              (direction == 'SELL' and rem_blend < price)
                    if adverse:
                        log.info(
                            f"PARTIAL SKIP [blend-guard]: closing ticket={tk} would leave "
                            f"{len(remaining)} tickets at blend {rem_blend:.2f} vs price {price:.2f} "
                            f"({direction} upside-down) — preserving hedge, let it run"
                        )
                        continue
            except Exception as _bg_err:
                log.debug(f"[blend-guard] skip check failed: {_bg_err}")

            log.info(
                f"FAST PARTIAL TRIGGER [touch] at {zp:.1f} [{strength} contrary] "
                f"dist={dist_to_zone:.2f}$ ticket={tk} profit=${tk_profit:.2f} → "
                f"close {self.PARTIAL_PCT}%, partial#{self.partial_count+1}/{self.MAX_PARTIALS_PER_SIGNAL}"
            )
            return {
                'ticket': int(tk),
                'pct': self.PARTIAL_PCT,
                'zone_price': zp,
                'strength': strength,
                'expected_realized': round(expected_realized, 2),
            }

        return None

    # ═══════════════════════════════════════════════════════════
    # PROPORTIONAL ZONE CAPTURE (2026-04-24)
    # ═══════════════════════════════════════════════════════════
    # Replaces the old "close 100% of most-profitable ticket" rule.
    # When price reaches a planned TP zone, capture a % of the TOTAL
    # position distributed proportionally across ALL tickets, so the
    # remaining book keeps the same blend ratio (no stranded ticket).
    #
    # Capture by strength:
    #   MODERATE → 25% of each ticket
    #   STRONG   → 50% of each ticket
    #   WEAK     → 0 (skip)
    #
    # NOT gated by breakeven_set — this is target-reached profit
    # capture, not reflex panic. Per-zone cooldown prevents re-firing
    # on the same level.
    CAPTURE_PCT_MODERATE = 25
    CAPTURE_PCT_STRONG = 50

    # ── Blend ladder — proportional capture every $X of weighted price advance ──
    # Independent of zones: when the WEIGHTED BLEND of the open book has moved
    # ≥ N×step in profit direction, fire a 25% proportional capture across all
    # tickets (same mechanic as a MODERATE contrary zone touch). Designed to
    # opportunistically lock partial profits in trends that don't conveniently
    # hit a planned zone.
    BLEND_LADDER_STEP_USD = 10.0   # every $10 of blend advance
    BLEND_LADDER_PCT = 25          # capture 25% of each ticket per step
    # Minimum movement from blend to zone before capturing: prevents firing
    # at zones right next to the blend ("super a prop") which would give
    # scraps of profit while sacrificing position integrity. Earlier bug
    # (2026-04-24 11:22): closed at zone 4690 with blend 4693.22 → only 3pt
    # of move captured, stranded the worst entry. Now enforce ≥5 pts OR
    # ≥1.5×ATR M5 (whichever is larger).
    MIN_CAPTURE_MOVE_PTS = 5.0

    def check_zone_proportional_capture(self, bars, account):
        """Proportional partial close when price reaches a contrary zone.

        Returns dict {zone_price, strength, orders: [{ticket, pct, ...}]} or None.
        Caller is expected to issue one PARTIAL_CLOSE_PCT per order.
        """
        if not self.reversal_zones or not bars or not account.get('has_signal'):
            return None
        if account.get('closing'):
            return None
        if not is_within_trading_hours():
            return None

        # Use trade_id (stable per-trade) NOT entry_price/blend (changes on every avg).
        # Bug 2026-04-30: blend changed → signal_key changed → zones_averaged reset
        # → same zone fired 9× in 3 minutes (incident at zone 4556).
        signal_key = (account.get('direction', ''), account.get('trade_id'))
        self._reset_for_new_signal(signal_key)

        if self.partial_count >= self.MAX_PARTIALS_PER_SIGNAL:
            return None
        if time.time() < self.exec_lock_until:
            return None

        positions = account.get('positions') or []
        if not positions:
            return None

        direction = account.get('direction', '')
        price = bars[-1]['close']
        current_atr = atr(bars, 14) or 5.0
        tolerance = current_atr * 0.3

        contrary_bounce = 'BUY' if direction == 'SELL' else 'SELL'

        # Find the triggered zone (tightest touch; strength-filtered)
        triggered_zone = None
        for zone in self.reversal_zones:
            zp = zone.get('price', 0)
            bounce = zone.get('bounce_direction', '')
            strength = (zone.get('strength', 'MODERATE') or 'MODERATE').upper()
            if bounce != contrary_bounce or not zp:
                continue
            if strength not in ('MODERATE', 'STRONG'):
                continue
            if abs(price - zp) > tolerance:
                continue
            # Per-zone cooldown
            zone_key = self._find_zone_key_dict(zp, self.zones_partialed)
            if zone_key in self.zones_partialed:
                # One-shot per signal: una zona ja capturada queda FORA del
                # pla per aquest trade. Es reseteja al canvi de senyal.
                continue
            triggered_zone = zone
            break

        if triggered_zone is None:
            return None

        zp = triggered_zone.get('price', 0)
        strength = (triggered_zone.get('strength', 'MODERATE') or 'MODERATE').upper()

        # ── Minimum move guard ──
        # Compute weighted blend; require zone to be ≥ MIN_CAPTURE_MOVE_PTS
        # away from blend IN THE PROFIT DIRECTION. Absolute only — previous
        # ATR multiplier caused over-restriction in high-volatility sessions
        # (2026-04-24: ATR M5 11pts → guard demanava 16+ pts, bloquejava
        # captures raonables a 3-5 pts).
        try:
            tot_vol = sum(float(p.get('volume', 0) or 0) for p in positions)
            w_sum = sum(float(p.get('volume', 0) or 0) * float(p.get('price_open', 0) or 0) for p in positions)
            blend = (w_sum / tot_vol) if tot_vol > 0 else 0
        except Exception:
            blend = 0
        min_move = self.MIN_CAPTURE_MOVE_PTS
        if blend > 0:
            if direction == 'SELL':
                move_in_favor = blend - zp  # zone below blend for SELL = profit
            else:
                move_in_favor = zp - blend  # zone above blend for BUY
            if move_in_favor < min_move:
                log.debug(
                    f"ZONE CAPTURE skip: zone {zp:.2f} only {move_in_favor:.2f}pts "
                    f"from blend {blend:.2f} (need {min_move:.1f}+); too close for capture"
                )
                return None

        # Breakout filter (reused from check_partial): skip if price punched
        # clearly past the zone with high volume — trade is likely to continue.
        if len(bars) >= 21:
            closed_bars = bars[:-1] if len(bars) >= 2 else bars
            last_closed = closed_bars[-1] if closed_bars else None
            if last_closed:
                last_close = float(last_closed.get('close', 0) or 0)
                last_vol = float(last_closed.get('volume', 0) or 0)
                vols = [float(b.get('volume', 0) or 0) for b in closed_bars[-21:-1]]
                avg_v = (sum(vols) / len(vols)) if vols else 0
                vol_r = (last_vol / avg_v) if avg_v > 0 else 0
                breakout_dist = max(1.0, current_atr * 0.5)
                is_breakout = False
                if direction == 'SELL' and last_close < (zp - breakout_dist) and vol_r > 1.5:
                    is_breakout = True
                if direction == 'BUY' and last_close > (zp + breakout_dist) and vol_r > 1.5:
                    is_breakout = True
                if is_breakout:
                    log.info(f"ZONE CAPTURE SKIP: breakout past {zp:.1f} detected "
                             f"(close={last_close:.1f}, vol={vol_r:.1f}×) — let trade run")
                    return None

        # Capture pct from zone strength
        capture_pct = self.CAPTURE_PCT_STRONG if strength == 'STRONG' else self.CAPTURE_PCT_MODERATE

        # Record initial lots BEFORE building orders — needed so
        # _capture_lot_and_pct uses the original entry size.
        self._record_initial_lots(positions)

        # Build proportional orders: close capture_pct% of EACH ticket's
        # ORIGINAL lot (not current). 4 MODERATE captures (4×25%) = full close.
        # 2 STRONG captures (2×50%) = full close. Last cut may close 100% of
        # remaining (when the original-fraction would leave <0.01 on broker).
        orders = []
        total_realized = 0.0
        total_lot_before = sum(float(p.get('volume', 0) or 0) for p in positions)
        total_lot_closed = 0.0
        for p in positions:
            try:
                prof = broker_position_pnl(p)
                lot = float(p.get('volume', 0) or 0)
                tk = int(p.get('ticket') or p.get('ticket_id') or 0)
            except Exception:
                continue
            if lot <= 0 or tk == 0:
                continue
            lot_close, pct_of_cur = self._capture_lot_and_pct(tk, lot, capture_pct)
            if lot_close is None:
                continue
            # Profit portion proportional to lot fraction of CURRENT
            # (matches what gets realized when EA partials at pct_of_cur%).
            realized_portion = prof * (lot_close / lot) if lot > 0 else 0.0
            orders.append({
                'ticket': tk,
                'pct': pct_of_cur,
                'lot_close': lot_close,
                'profit_portion': round(realized_portion, 2),
            })
            total_realized += realized_portion
            total_lot_closed += lot_close

        if not orders:
            return None

        # Floor check against TOTAL realized
        balance = account.get('balance', 0) or 0
        floor_usd = max(self.PARTIAL_MIN_PROFIT_USD,
                        balance * self.PARTIAL_MIN_PROFIT_BALANCE_PCT / 100.0)
        if total_realized < floor_usd:
            log.debug(f"ZONE CAPTURE skip: total realized ${total_realized:.2f} < floor ${floor_usd:.2f}")
            return None

        log.info(
            f"FAST ZONE CAPTURE at {zp:.1f} [{strength}] "
            f"capture={capture_pct}% of {total_lot_before:.2f}L → "
            f"closing {total_lot_closed:.2f}L across {len(orders)} ticket(s) "
            f"≈ ${total_realized:+.2f} booked (proportional)"
        )
        return {
            'zone_price': zp,
            'strength': strength,
            'capture_pct': capture_pct,
            'total_realized': round(total_realized, 2),
            'orders': orders,
        }

    def check_blend_ladder_capture(self, bars, account):
        """Proportional capture every $X of WEIGHTED-BLEND price advance.

        Triggered independently of zones. Acts as a fixed-distance profit
        ladder anchored to the book's weighted entry price (blend), not to
        floating P&L. Each step that the blend advances toward profit fires
        a 25% proportional partial across all tickets — same shape as a
        MODERATE contrary zone touch.

        Step size: BLEND_LADDER_STEP_USD ($10 default).
        Capture %: BLEND_LADDER_PCT (25% default).

        Resets on signal change. Step counter is monotonic — once level N is
        captured we never re-fire for level ≤ N within the same signal.
        """
        if not bars or not account.get('has_signal'):
            return None
        if account.get('closing'):
            return None
        if not is_within_trading_hours():
            return None

        # Use trade_id (stable per-trade) NOT entry_price/blend (changes on every avg).
        # Bug 2026-04-30: blend changed → signal_key changed → zones_averaged reset
        # → same zone fired 9× in 3 minutes (incident at zone 4556).
        signal_key = (account.get('direction', ''), account.get('trade_id'))
        self._reset_for_new_signal(signal_key)

        if self.partial_count >= self.MAX_PARTIALS_PER_SIGNAL:
            return None
        if time.time() < self.exec_lock_until:
            return None

        positions = account.get('positions') or []
        if not positions:
            return None
        direction = account.get('direction', '')
        if direction not in ('BUY', 'SELL'):
            return None

        # Compute weighted blend
        try:
            tot_vol = sum(float(p.get('volume', 0) or 0) for p in positions)
            if tot_vol <= 0:
                return None
            blend = sum(float(p.get('volume', 0) or 0) * float(p.get('price_open', 0) or 0)
                        for p in positions) / tot_vol
        except Exception:
            return None

        price = bars[-1]['close']
        # Distance the blend has advanced in profit direction
        if direction == 'BUY':
            blend_advance = price - blend
        else:
            blend_advance = blend - price

        if blend_advance <= 0:
            return None  # blend not in profit yet

        step = self.BLEND_LADDER_STEP_USD
        if step <= 0:
            return None
        # Highest step crossed (e.g. advance=23 → level=2 with step=10)
        target_level = int(blend_advance // step)
        if target_level <= self.blend_ladder_step:
            return None  # already captured this level

        # Record initial lots (needed for fixed-fraction-of-original capture).
        self._record_initial_lots(positions)

        # Build proportional orders: 25% of EACH ticket's ORIGINAL lot per step.
        # 4 levels × 25% = full close. Same accounting as zone capture path.
        capture_pct = self.BLEND_LADDER_PCT
        orders = []
        total_realized = 0.0
        total_lot_before = tot_vol
        total_lot_closed = 0.0
        for p in positions:
            try:
                prof = broker_position_pnl(p)
                lot = float(p.get('volume', 0) or 0)
                tk = int(p.get('ticket') or p.get('ticket_id') or 0)
            except Exception:
                continue
            if lot <= 0 or tk == 0:
                continue
            lot_close, pct_of_cur = self._capture_lot_and_pct(tk, lot, capture_pct)
            if lot_close is None:
                continue
            realized_portion = prof * (lot_close / lot) if lot > 0 else 0.0
            orders.append({
                'ticket': tk,
                'pct': pct_of_cur,
                'lot_close': lot_close,
                'profit_portion': round(realized_portion, 2),
            })
            total_realized += realized_portion
            total_lot_closed += lot_close

        if not orders:
            return None

        # Floor check vs USD floor (avoids firing on tiny realized when only
        # one ticket can split). Same floor as zone-capture path.
        balance = account.get('balance', 0) or 0
        floor_usd = max(self.PARTIAL_MIN_PROFIT_USD,
                        balance * self.PARTIAL_MIN_PROFIT_BALANCE_PCT / 100.0)
        if total_realized < floor_usd:
            log.debug(
                f"BLEND LADDER skip: level={target_level} realized=${total_realized:.2f} "
                f"< floor ${floor_usd:.2f}"
            )
            return None

        log.info(
            f"FAST BLEND LADDER level={target_level} (advance={blend_advance:.2f}$ "
            f"from blend {blend:.2f}) capture={capture_pct}% of {total_lot_before:.2f}L → "
            f"closing {total_lot_closed:.2f}L across {len(orders)} ticket(s) "
            f"≈ ${total_realized:+.2f} booked"
        )
        return {
            'level': target_level,
            'blend': round(blend, 2),
            'blend_advance': round(blend_advance, 2),
            'capture_pct': capture_pct,
            'total_realized': round(total_realized, 2),
            'orders': orders,
        }

    def mark_blend_ladder_fired(self, level: int) -> None:
        """Called after a blend-ladder partial is successfully sent."""
        self.blend_ladder_step = max(self.blend_ladder_step, int(level))
        self.partial_count += 1
        self.exec_lock_until = time.time() + self.EXEC_LOCK_TIMEOUT
        self._save_state()
        log.info(
            f"FAST: Marked blend ladder level={self.blend_ladder_step}. "
            f"Partials count={self.partial_count}/{self.MAX_PARTIALS_PER_SIGNAL}"
        )

    def _find_zone_key_dict(self, zp, d):
        """Find matching zone key in given dict within ZONE_MATCH_TOLERANCE."""
        for existing_price in d.keys():
            if abs(existing_price - zp) <= self.ZONE_MATCH_TOLERANCE:
                return existing_price
        return zp

    def mark_partial_sent(self, zp):
        """Called after a partial order is successfully sent."""
        zone_key = self._find_zone_key_dict(zp, self.zones_partialed)
        self.zones_partialed[zone_key] = time.time()
        self.partial_count += 1
        self.exec_lock_until = time.time() + self.EXEC_LOCK_TIMEOUT
        self._save_state()  # persist so dashboard + restart see consumed zones
        log.info(f"FAST: Marked zone {zp:.1f} partialed. Count={self.partial_count}/{self.MAX_PARTIALS_PER_SIGNAL}")


# ═══════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════

FAST_INTERVAL = 2         # seconds — 2s compromise between reactivity and load.
                           # 1s was too aggressive (contributed to rapid open/close
                           # chains on 2026-04-23). 2s still captures wicks via
                           # intrabar high/low check in snipers.
INDICATOR_INTERVAL = 75   # seconds — always runs
EXECUTOR_INTERVAL = 35    # seconds — only when signal active
SCREENSHOT_INTERVAL = 120 # seconds

# Cooldown after an autonomous trade closes before opening a new one


def read_autonomous_mode():
    """Read AUTONOMOUS_MODE from control file. Default: OFF (safe)."""
    ctrl_path = os.path.join(COMMON, 'brain_control.json')
    try:
        with open(ctrl_path, 'r', encoding='utf-8') as f:
            return bool(json.load(f).get('autonomous_mode', False))
    except Exception:
        return False


def update_daily_history(bars):
    """Maintain multi-day H/L history in brain_daily_history.json.

    Each UTC day, records the high/low of that day. Keeps last 7 days.
    Called every cycle — only writes on day rollover or when current day extends H/L.
    """
    if not bars or len(bars) < 10:
        return {}
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    # Compute today's H/L from bars of today
    today_start_ts = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    today_bars = [b for b in bars if b.get('time', 0) >= today_start_ts]
    if not today_bars:
        today_bars = bars  # fallback
    today_high = max(b['high'] for b in today_bars)
    today_low = min(b['low'] for b in today_bars)

    data = {}
    try:
        if os.path.exists(DAILY_HISTORY_FILE):
            with open(DAILY_HISTORY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
    except Exception:
        data = {}

    days = data.get('days', {})
    # Update today
    prev_today = days.get(today, {})
    updated = False
    new_high = max(today_high, prev_today.get('high', 0)) if prev_today else today_high
    new_low = min(today_low, prev_today.get('low', 999999)) if prev_today else today_low
    if prev_today.get('high') != new_high or prev_today.get('low') != new_low:
        days[today] = {'high': round(new_high, 2), 'low': round(new_low, 2)}
        updated = True

    # On day rollover, we also snapshot the previous bars window to extract prior day H/L
    # (best-effort: if we have bars from yesterday still, use them)
    yesterday = (datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() - 86400)
    yesterday_date = datetime.fromtimestamp(yesterday, tz=timezone.utc).strftime('%Y-%m-%d')
    if yesterday_date not in days:
        ybars = [b for b in bars if yesterday <= b.get('time', 0) < today_start_ts]
        if ybars:
            days[yesterday_date] = {'high': round(max(b['high'] for b in ybars), 2),
                                      'low': round(min(b['low'] for b in ybars), 2)}
            updated = True

    # Keep last 7 days only
    sorted_dates = sorted(days.keys(), reverse=True)[:7]
    days = {d: days[d] for d in sorted_dates}

    if updated or not data:
        data = {'days': days, 'updated': datetime.now(timezone.utc).isoformat()}
        try:
            with open(DAILY_HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass
    return days


def detect_session():
    """Detect current trading session based on UTC hour."""
    h = datetime.now(timezone.utc).hour
    m = datetime.now(timezone.utc).minute
    if 0 <= h < 7: return "Asia (soft, low volume)"
    if 7 <= h < 12: return "London (active, moderate volatility)"
    if 12 <= h < 13: return "London pre-NY (building)"
    if 13 <= h < 17: return "London-NY overlap (MAX volatility)"
    if 17 <= h < 21: return "NY afternoon (fading)"
    return "After-hours (thin, erratic)"


def compute_volume_nodes(bars, n_levels=5):
    """Identify price levels with concentrated volume (basic volume profile).

    Groups M5 bars by price buckets and sums volume. Returns top N high-volume levels.
    Returns [] if the feed has no volume data (e.g., PYTH feed).
    """
    if not bars or len(bars) < 20:
        return []
    # Skip entirely if no volume data (some feeds don't report it)
    total_vol = sum(b.get('volume', 0) for b in bars[-200:])
    if total_vol < 100:
        return []
    price_mid = bars[-1]['close']
    bucket_size = max(1.5, price_mid * 0.0005)
    buckets = {}
    for b in bars[-200:]:
        mid = (b['high'] + b['low']) / 2
        key = round(mid / bucket_size) * bucket_size
        buckets[key] = buckets.get(key, 0) + b.get('volume', 0)
    top = sorted(buckets.items(), key=lambda x: x[1], reverse=True)[:n_levels]
    return [{'price': round(p, 1), 'vol': int(v)} for p, v in sorted(top)]


def log_status_snapshot(price, bars, account, sig_state, engine, _ind_bias='?', _ind_regime='?', _exec_action='?', _exec_conf=0):
    """Print a compact human-readable snapshot to the log every 60s.

    Shows: price, momentum, active zones map (relative to price), signal state,
    FastEngine budget, last LLM decisions. Intended for "what's going on right now?"
    at a glance without digging through logs.
    """
    try:
        bias = _ind_bias
        regime = _ind_regime
        # Load zones and sort by distance from price
        try:
            from zone_store import read_state, active_zones
            zs = active_zones(read_state(COMMON))
        except Exception:
            zs = []
        def zline(z):
            p = float(z.get('price', 0))
            s = z.get('strength', '?')[0]
            b = z.get('bounce_direction') or '?'
            arrow = '▲' if p > price else '▼'
            return f"{p:.1f}{arrow}{p-price:+.1f}[{s}/{b[0]}]"
        zs_sorted = sorted(zs, key=lambda z: abs(float(z.get('price', 0)) - price))
        zones_str = ' '.join(zline(z) for z in zs_sorted[:6]) if zs_sorted else 'NONE'

        # Momentum from last 5 M5 bars
        if len(bars) >= 6:
            delta = bars[-1]['close'] - bars[-6]['close']
            mom = f"{delta:+.1f}$/5bars"
        else:
            mom = '?'

        # Signal block
        if account.get('has_signal'):
            direction = account.get('direction', '')
            entry = account.get('entry_price', 0)
            avg = sig_state.get('avg_count') if hasattr(sig_state, 'get') else 0
            pl = account.get('equity', 0) - account.get('balance', 0)
            if hasattr(sig_state, 'get') and sig_state.get('breakeven_set'):
                be = '🔒BE'
            elif hasattr(sig_state, 'get') and sig_state.get('breakeven_pending'):
                be = '🕒BE'
            else:
                be = ''
            sig_str = f"{direction}@{entry:.2f} avg={avg} float={pl:+.1f}$ {be}".strip()
            fast_budget = f"avgs={engine.avg_count} partials={engine.partial_count}/{engine.MAX_PARTIALS_PER_SIGNAL}"
        else:
            sig_str = 'IDLE'
            fast_budget = '(no signal)'

        # DD
        dd_used = account.get('dd_used', 0)
        dd_limit = account.get('dd_limit', 0)
        dd_ratio = (dd_used / dd_limit * 100) if dd_limit else 0

        # DD TG alerts at 2% and 3% absolute (auto-close is at BRAIN_DD_LIMIT_PCT=3.5%).
        # Fire once per upward crossing; 0.5% hysteresis before re-arming to avoid spam.
        dd_abs_pct = float(account.get('dd_pct', 0) or 0)
        if not hasattr(log_status_snapshot, '_dd_alert_level'):
            log_status_snapshot._dd_alert_level = 0
        _lvl = log_status_snapshot._dd_alert_level
        _eq = float(account.get('equity', 0) or 0)
        _float = _eq - float(account.get('balance', 0) or 0)
        if dd_abs_pct >= 3.0 and _lvl < 3:
            notify("dd_alert",
                   f"🚨 DD {dd_abs_pct:.2f}% · equity ${_eq:,.0f} · float ${_float:+,.2f} · "
                   f"queda {max(0.0, BRAIN_DD_LIMIT_PCT - dd_abs_pct):.2f}% fins auto-close ({BRAIN_DD_LIMIT_PCT}%)")
            log_status_snapshot._dd_alert_level = 3
        elif dd_abs_pct >= 2.0 and _lvl < 2:
            notify("dd_alert",
                   f"⚠️ DD {dd_abs_pct:.2f}% · equity ${_eq:,.0f} · float ${_float:+,.2f}")
            log_status_snapshot._dd_alert_level = 2
        if dd_abs_pct < 2.5 and _lvl >= 3:
            log_status_snapshot._dd_alert_level = 2
        if dd_abs_pct < 1.5 and _lvl >= 2:
            log_status_snapshot._dd_alert_level = 0

        # Daily P&L — balance_now vs start-of-day anchor
        daily_str = ""
        try:
            if os.path.exists(DAILY_FILE):
                with open(DAILY_FILE, 'r', encoding='utf-8') as _df:
                    _da = json.load(_df)
                # Try multiple keys (start_balance is the one currently written)
                _anchor = float(_da.get('start_balance') or _da.get('balance_anchor') or 0)
                _bal_now = float(account.get('balance', 0) or 0)
                if _anchor > 0:
                    _daily = _bal_now - _anchor
                    _sign = '+' if _daily >= 0 else ''
                    daily_str = f"  ·  day P&L: {_sign}${_daily:.2f}"
        except Exception:
            pass

        # Scout recommendation when IDLE — quick heuristic view of best setup now
        scout_line = ""
        if not account.get('has_signal'):
            try:
                from scout import recommend, format_recommendation
                ms = None
                try:
                    from market_context import build_market_context
                    from datetime import datetime, timezone as _tz
                    ms = build_market_context(bars, account, tv_helper=None,
                                               now_utc=datetime.now(_tz.utc), for_executor=True)
                except Exception:
                    ms = None
                rec = recommend(price, bars, zs, market_state=ms, indicator_bias=bias)
                scout_line = f"\n│  scout: {format_recommendation(rec)}"
            except Exception as _se:
                scout_line = ""

        # Session + news header — what context are we trading in right now?
        try:
            _sess = news_state.session_label()
            _factor = news_state.session_factor()
            _sess_enabled = news_state.is_session_enabled()
            _sess_summary = news_state.sessions_enabled_summary()
            _news_line = news_state.pending_summary()
            _hi_block = news_state.high_impact_within(30)
            _sess_disabled_tag = ' · SESSION DISABLED 🚫' if not _sess_enabled else ''
            _gate_tag = ' · NEWS BLOCK ⛔' if _hi_block else ''
        except Exception:
            _sess = '?'
            _factor = 1.0
            _sess_summary = '?'
            _news_line = 'NEWS: ?'
            _sess_disabled_tag = ''
            _gate_tag = ''

        # Explosion state (cheap file read, last evaluated by main loop)
        _explosion_tag = ''
        try:
            import explosion_detector
            _expl = explosion_detector.last_state()
            if _expl.get('active'):
                _crit1 = _expl.get('criteria', {}).get('recent_adverse_strong_break', {})
                n_breaks = _crit1.get('adverse_count_30min', 0)
                _explosion_tag = f' · 🔥 EXPLOSION ({n_breaks} STRONG broken 30m, snipers FROZEN)'
        except Exception:
            pass

        # NEW 2026-05-04: live approach states (LLM not needed — pure tracker)
        # Mostra cada zona en APPROACH/AT_ZONE amb delta acumulat institucional.
        _approach_line = ""
        try:
            _at = _get_approach_tracker()
            if _at is not None:
                _ap_data = _at.get_payload_dict()
                if _ap_data:
                    _ap_lines = []
                    for _zid, _st in _ap_data.items():
                        _ap_lines.append(
                            f"{_st.get('state','?')} {_st.get('zone_price','?')} {_st.get('zone_type','?')[:1]} "
                            f"· {_st.get('bars_acc',0)}b vol={_st.get('vol_acc',0):.0f} "
                            f"Δ={_st.get('delta_acc',0):+.0f} σ={_st.get('signal_strength',0):+.2f}"
                        )
                    if _ap_lines:
                        _approach_line = "\n│  🎯 APPROACH: " + "  |  ".join(_ap_lines)
        except Exception:
            pass

        # Indicator's directional commitment + working_range (the daily bias map)
        _commit_line = ""
        _range_line = ""
        _risk_line = ""
        try:
            from zone_store import read_state
            _z = read_state(COMMON)
            _dc = _z.get('directional_commitment') if isinstance(_z.get('directional_commitment'), dict) else None
            _wr = _z.get('working_range') if isinstance(_z.get('working_range'), dict) else None
            _ar = _z.get('asymmetric_risk') if isinstance(_z.get('asymmetric_risk'), dict) else None
            if _dc:
                _side = _dc.get('side', '?')
                _strength = _dc.get('strength', '?')
                _conf = _dc.get('confidence', 0)
                _commit_line = f"\n│  📍 BIAS: {_side} ({_strength}, conf={_conf:.2f})"
            if _wr:
                _hi = _wr.get('high'); _lo = _wr.get('low'); _t = _wr.get('type', '?')
                if _hi and _lo:
                    _range_line = f"\n│  📐 RANG: {_lo:.1f}—{_hi:.1f} ({_t})"
            if _ar:
                _bsq = _ar.get('bull_squeeze_risk', '?')
                _bcn = _ar.get('bear_continuation_risk', '?')
                _pc = _ar.get('primary_concern', '?')
                _risk_line = f"\n│  ⚠ RISC: bull_sq={_bsq} bear_ct={_bcn} → {_pc}"
        except Exception:
            pass

        # 2026-05-04: linia auto_close conditions actives (Mode Recorregut)
        _auto_close_line = ""
        try:
            if sig_state and sig_state.is_active():
                _ep = sig_state._data.get('executor_plan') or {}
                _ac = _ep.get('auto_close_conditions') or []
                _tpt = _ep.get('tp_target')
                if _ac:
                    _conds_str = []
                    for _c in _ac:
                        _mark = '🔥' if _c.get('fired_at') else '👁'
                        if _c.get('kind') == 'bar_close':
                            _d = f"{_c.get('tf')} {_c.get('test')} {_c.get('level')}"
                        elif _c.get('kind') == 'metric':
                            _d = f"{_c.get('metric')} {_c.get('test')} {_c.get('level')}"
                        else:
                            _d = f"tick {_c.get('test')} {_c.get('level')}"
                        _conds_str.append(f"{_mark}{_c.get('id', '?')[:20]}→{_c.get('action')}({_d})")
                    _auto_close_line = (f"\n│  💀 INVALIDATION (LLM-pre-aprovades): "
                                        + "  ·  ".join(_conds_str))
                    if _tpt:
                        _auto_close_line += f"  ·  🎯 TP={_tpt}"
                elif _ep:
                    # Trade obert sense conditions — protecció zero
                    _auto_close_line = "\n│  ⚠ Sense auto_close_conditions actives — només DD 3.5% protegeix"
        except Exception:
            pass

        log.info(
            f"┌─ STATUS ─ price={price:.2f} mom={mom} regime={regime} bias={bias}\n"
            f"│  session: {_sess} (R×{_factor:.2f}, on: {_sess_summary}){_sess_disabled_tag}  ·  {_news_line}{_gate_tag}{_explosion_tag}\n"
            f"│  signal: {sig_str}\n"
            f"│  fast: {fast_budget}  ·  DD {dd_used:.1f}/{dd_limit:.1f} ({dd_ratio:.1f}%){daily_str}\n"
            f"│  zones: {zones_str}{_range_line}{_commit_line}{_risk_line}{_approach_line}{_auto_close_line}\n"
            f"│  last_exec: {_exec_action} (conf={int(_exec_conf*100) if _exec_conf <= 1 else int(_exec_conf)}%){scout_line}"
        )
    except Exception as e:
        log.warning(f"snapshot log failed: {e}")


def update_daily_anchor(balance):
    """Maintain per-day + cumulative ledger. Rolls over at UTC midnight.

    Persists full history to brain_daily_ledger.json (new) and keeps
    brain_daily.json in sync for legacy dashboard readers.
    """
    try:
        import daily_ledger
        return daily_ledger.update(float(balance or 0))
    except Exception as e:
        # Defensive fallback: preserve old single-day behaviour so brain
        # keeps running even if the ledger module blows up.
        log.warning(f"[DAILY] ledger update failed: {e}")
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        data = {}
        try:
            if os.path.exists(DAILY_FILE):
                with open(DAILY_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
        except Exception:
            data = {}
        if data.get('date') != today or not data.get('start_balance'):
            data = {'date': today, 'start_balance': balance, 'start_ts': time.time()}
            try:
                with open(DAILY_FILE, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2)
                log.info(f"[DAILY] New day {today}: anchor balance = ${balance:,.2f}")
            except Exception:
                pass
        return data

def notify(event_type: str, message: str):
    """Send a Telegram notification if the event type is enabled in config.

    event_type: one of the keys under notifications.events in config.yaml
    message: the full formatted text (with emojis etc.) to send.

    Checks config.notifications.enabled (global) and config.notifications.events.<event_type>.
    Never raises. Idempotent if config missing (falls back to defaults: enabled+all true).
    """
    try:
        cfg = _load_app_config()
        notif_cfg = cfg.get('notifications', {}) or {}
        if not notif_cfg.get('enabled', True):
            return
        events_cfg = notif_cfg.get('events', {}) or {}
        # Default: unknown event type → ON (fail-loud)
        if not events_cfg.get(event_type, True):
            return
        _brain_send_alert(message)
    except Exception as e:
        log.warning(f"[NOTIFY] failed for {event_type}: {e}")


def _brain_send_alert(message, return_id=False):
    """Send a brief alert to the Telegram alert_bot configured in config.yaml.
    Mirrors watchdog.send_alert but scoped to this process. Never raises.
    If return_id=True, returns the Telegram message_id (or None on failure).
    """
    try:
        import ssl, urllib.parse, urllib.request, json as _json
        cfg = _load_app_config()
        alert_cfg = (cfg.get('alert_bot') or {})
        token = alert_cfg.get('token')
        chat_id = alert_cfg.get('chat_id')
        if not token or chat_id is None:
            log.warning(f"[ALERT] no alert_bot config — message dropped: {message}")
            return None if return_id else None
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            'chat_id': str(chat_id),
            'text': f"🧠 BRAIN: {message}",
        }).encode('utf-8')
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, data=data, method='POST')
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            body = resp.read().decode('utf-8', errors='replace')
        log.info(f"[ALERT] TG sent: {message[:80]}")
        if return_id:
            try:
                j = _json.loads(body)
                return j.get('result', {}).get('message_id')
            except Exception:
                return None
        return None
    except Exception as e:
        log.warning(f"[ALERT] send failed: {e}")
        return None


# ── Pinned/editable status messages ──
# Some categories (staged setups, scout status, etc.) update very frequently.
# Sending a new TG message for each update spams the chat. Instead we send
# ONCE per category and EDIT the same message on every update — the operator
# sees the latest state in a single persistent message that scrolls up as new
# REAL events (trade opens, AVGs, partials, BE, closes) push it down.
#
# Stored in COMMON so brain restarts can resume editing the same message.
_PINNED_FILE = os.path.join(COMMON, 'brain_pinned_msgs.json')

def _load_pinned():
    try:
        if os.path.exists(_PINNED_FILE):
            with open(_PINNED_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_pinned(data):
    try:
        with open(_PINNED_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.debug(f"[ALERT] pinned save failed: {e}")

def _brain_edit_alert(msg_id, message):
    """Edit an existing Telegram message. Returns True on success."""
    try:
        import ssl, urllib.parse, urllib.request
        cfg = _load_app_config()
        alert_cfg = (cfg.get('alert_bot') or {})
        token = alert_cfg.get('token')
        chat_id = alert_cfg.get('chat_id')
        if not token or chat_id is None or not msg_id:
            return False
        url = f"https://api.telegram.org/bot{token}/editMessageText"
        data = urllib.parse.urlencode({
            'chat_id': str(chat_id),
            'message_id': str(msg_id),
            'text': f"🧠 BRAIN: {message}",
        }).encode('utf-8')
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, data=data, method='POST')
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            resp.read()
        return True
    except Exception:
        return False


def notify_update_clear(category: str = None):
    """Clear pinned message tracking for a category (or all if None).
    Next notify_update call will send a fresh message instead of editing.
    Useful when context resets — e.g. trade opens, signal closes, etc.
    """
    try:
        pinned = _load_pinned()
        if category:
            pinned.pop(category, None)
        else:
            pinned.clear()
        _save_pinned(pinned)
    except Exception:
        pass


def notify_update(category: str, message: str, max_age_h: int = 24):
    """Send-or-edit a pinned status message for a given category.

    First call for `category`: sends a new message and stores its message_id.
    Subsequent calls: EDITS the existing message in place — the operator sees
    a single persistent "latest status" message instead of a flood.

    Categories typical: 'staged_executor', 'staged_hunter', 'tg_signal_seen',
    'scout_status'. Real events (trade_opened, averaging, partial_close, BE,
    closing, dd_threshold) keep using `notify()` for fresh notifications.

    If the stored message is too old (>max_age_h) or edit fails, sends new.
    """
    try:
        cfg = _load_app_config()
        notif_cfg = cfg.get('notifications', {}) or {}
        if not notif_cfg.get('enabled', True):
            return
        # Honor per-category mute via events.<category>=false (same as notify())
        events_cfg = notif_cfg.get('events', {}) or {}
        if events_cfg.get(category) is False:
            return
        pinned = _load_pinned()
        state = pinned.get(category) or {}
        now = time.time()
        msg_id = state.get('msg_id')
        last_update = float(state.get('last_update') or 0)
        # 2026-05-04: si el missatge és IDÈNTIC al darrer enviat, NO fer res.
        # Evita el spam "STAGED SELL 4574.5 (82%)" repetit cada cycle de l'EXECUTOR.
        last_msg_stored = state.get('last_message') or ''
        if msg_id and last_msg_stored and message[:200] == last_msg_stored:
            # Mateix text → silenciar, només actualitzem el timestamp lleugerament
            state['last_update'] = now
            pinned[category] = state
            _save_pinned(pinned)
            return
        if msg_id and (now - last_update) < max_age_h * 3600:
            # Try to edit existing message
            if _brain_edit_alert(msg_id, message):
                state['last_update'] = now
                state['last_message'] = message[:200]
                pinned[category] = state
                _save_pinned(pinned)
                return
            # Edit failed (deleted by user, too old, etc.) — fall through to send new
        new_id = _brain_send_alert(message, return_id=True)
        if new_id:
            pinned[category] = {
                'msg_id': new_id,
                'created_at': now,
                'last_update': now,
                'last_message': message[:200],
            }
            _save_pinned(pinned)
    except Exception as e:
        log.warning(f"[NOTIFY_UPDATE] failed for {category}: {e}")


def _load_app_config():
    """Load config.yaml (optional) for the event-driven architecture sections.
    Returns an empty dict on any failure so callers can fall back to defaults."""
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}
    path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        log.warning(f"[CONFIG] could not load config.yaml: {e}")
        return {}


# ═══════════════════════════════════════════════════════════════════════════
# INDICATOR cadence gate
# ═══════════════════════════════════════════════════════════════════════════
# Fires the INDICATOR+REVIEWER pipeline only when something structurally
# meaningful has changed since the last run. Saves ~40-60% of LLM cost in
# quiet markets / weekends / Asia session, and keeps the zone map stable
# (the Reviewer's "estabilitat" bias works better when it's not re-invoked
# pointlessly).
#
# Triggers that DO fire:
#   - INITIAL: first run after start, or no zones in store yet
#   - MAX_IDLE: more than 60 min since last run (safety net — zones can stale)
#   - PRICE_MOVED: price moved > 0.6 * ATR_M15 since last run (structural move)
#   - ZONE_APPROACH: price now within 0.5 * ATR_M15 of any active zone
#   - VOLUME_SPIKE: current M5 bar vol > 2.5× avg of last 20 bars
#   - ZONE_CHANGED: zone_state.updated_at differs (lifecycle invalidated or
#                   touched something, reviewer needs to refresh)
#   - NEW_SIGNAL: TG signal_id changed since last run
#   - NO_SIGNAL_YET: no signal active AND no zones — need a map
#
# Min interval between calls: 3 min (throttles all reasons).
# ═══════════════════════════════════════════════════════════════════════════
INDICATOR_GATE_MIN_INTERVAL_S = 180
INDICATOR_GATE_MAX_INTERVAL_S = 3600
INDICATOR_GATE_PRICE_MOVE_ATR = 0.6
INDICATOR_GATE_ZONE_APPROACH_ATR = 0.5
INDICATOR_GATE_VOLUME_SPIKE_RATIO = 2.5


def is_within_trading_hours(now_utc=None, cfg=None):
    """Return True if current UTC time falls within an operable window.

    SOURCE OF TRUTH: `sessions_enabled` (the dashboard toggle). If the current
    UTC session is enabled there, we ALWAYS return True — period. The legacy
    `trading_hours` block is consulted only as an additional negative filter
    when `sessions_enabled` says the session is OFF (so a user can narrow
    further but never override the toggle).

    Background: until 2026-04-29 these were two independent gates. The
    `trading_hours` window (e.g. 05:00-20:00 UTC) silently blocked Asia even
    when sessions_enabled.ASIA=true — because Asia (00-05 UTC) fell outside
    the trading_hours window. This function now makes sessions_enabled
    authoritative so the dashboard reflects reality.

    TG-driven actions (cerramos, movemos SL) should bypass this gate entirely.
    """
    from datetime import datetime, timezone as _tz
    if now_utc is None:
        now_utc = datetime.now(_tz.utc)

    # Sessions toggle is the source of truth.
    try:
        import news_state as _ns
        if _ns.is_session_enabled(now_utc):
            return True
    except Exception:
        # If sessions module fails to load, fall through to legacy gate so
        # we don't silently lock everything.
        pass

    # Legacy trading_hours gate — consulted only when sessions_enabled said NO.
    if cfg is None:
        cfg = _load_app_config()
    th = (cfg.get('trading_hours') or {})
    if not th.get('enabled', True):
        return False  # sessions said NO and trading_hours not used → blocked
    try:
        start = th.get('start_utc', '00:00')
        end = th.get('end_utc', '23:59')
        sh, sm = [int(x) for x in start.split(':')]
        eh, em = [int(x) for x in end.split(':')]
    except (ValueError, AttributeError):
        return False  # malformed → blocked (sessions said NO, no fallback)
    cur_min = now_utc.hour * 60 + now_utc.minute
    start_min = sh * 60 + sm
    end_min = eh * 60 + em
    if start_min <= end_min:
        return start_min <= cur_min < end_min
    # Wrap-around (e.g. 22:00-06:00)
    return cur_min >= start_min or cur_min < end_min


def _should_run_indicator(
    now_ts: float,
    bars_cache,
    account,
    sig_state,
    last_run_ts: float,
    last_price: float | None,
    last_zones_updated: str | None,
    last_signal_id,
):
    """Decide whether the INDICATOR+REVIEWER pipeline should run this cycle.

    Returns (bool, reason_str). The reason goes to the log and can be surfaced
    by the dashboard.
    """
    # Trading hours gate: skip entirely during rest hours (Asia session)
    # unless there is an OPEN trade — then we still want zone awareness.
    if not is_within_trading_hours():
        has_open = account and account.get('has_signal')
        if not has_open:
            return False, "OFF_HOURS (Asia session rest, no open trade)"

    # Always allow first run after startup
    if last_run_ts == 0.0:
        return True, "INITIAL (first cycle)"

    elapsed = now_ts - last_run_ts

    # ── EMERGENCY BYPASS (vol spike ≥ force_refresh_on_vol_spike) ──
    # A news/FOMC candle can invalidate the entire zone map in seconds. We let
    # these bypass the 3-min throttle so the Indicator+Reviewer get a fresh
    # reading on the new structural regime before FastEngine keeps operating
    # on stale zones.
    try:
        _zcfg = (_load_app_config().get('zones') or {})
        _force_ratio = float(_zcfg.get('force_refresh_on_vol_spike', 3.0))
    except Exception:
        _force_ratio = 3.0
    if bars_cache and len(bars_cache) >= 21:
        _cur_v = bars_cache[-1].get('volume', 0)
        _prev_v = [b.get('volume', 0) for b in bars_cache[-21:-1]]
        _avg_v = sum(_prev_v) / len(_prev_v) if _prev_v else 0
        if _avg_v > 0 and _cur_v >= _force_ratio * _avg_v:
            return True, f"FORCE_VOL_SPIKE (vol {_cur_v} >= {_force_ratio}× avg {_avg_v:.0f}) — bypass throttle"

    # Hard floor — never call more than once every 3 min regardless
    if elapsed < INDICATOR_GATE_MIN_INTERVAL_S:
        return False, f"throttle ({int(elapsed)}s < {INDICATOR_GATE_MIN_INTERVAL_S}s min)"

    # Safety net — always call at least once an hour
    if elapsed >= INDICATOR_GATE_MAX_INTERVAL_S:
        return True, f"MAX_IDLE ({int(elapsed/60)}m since last run)"

    if not bars_cache:
        return False, "no bars"

    cur_price = bars_cache[-1].get('close', 0)
    bars_m15 = aggregate_bars(bars_cache, 3)
    atr_m15 = atr(bars_m15, 14) or (atr(bars_cache, 14) or 1.0)

    # New signal came in since last run
    cur_sig_id = sig_state.get('id') if hasattr(sig_state, 'get') else None
    if cur_sig_id and cur_sig_id != last_signal_id:
        return True, f"NEW_SIGNAL ({cur_sig_id})"

    # Price moved significantly
    if last_price is not None and atr_m15 > 0:
        price_move = abs(cur_price - last_price)
        if price_move >= INDICATOR_GATE_PRICE_MOVE_ATR * atr_m15:
            return True, f"PRICE_MOVED ({price_move:.2f} USD >= {INDICATOR_GATE_PRICE_MOVE_ATR} * ATR_M15 {atr_m15:.2f})"

    # Volume spike on current M5 bar
    if len(bars_cache) >= 21:
        cur_vol = bars_cache[-1].get('volume', 0)
        prev_vols = [b.get('volume', 0) for b in bars_cache[-21:-1]]
        avg_vol = sum(prev_vols) / len(prev_vols) if prev_vols else 0
        if avg_vol > 0 and cur_vol >= INDICATOR_GATE_VOLUME_SPIKE_RATIO * avg_vol:
            return True, f"VOLUME_SPIKE (vol {cur_vol} >= {INDICATOR_GATE_VOLUME_SPIKE_RATIO}× avg {avg_vol:.0f})"

    # Zone state changed (lifecycle invalidated/touched → Reviewer should re-see)
    try:
        from zone_store import read_state as _rs, active_zones as _az
        _st = _rs(COMMON)
        zs_updated = _st.get('updated_at')
        if last_zones_updated and zs_updated and zs_updated != last_zones_updated:
            return True, "ZONE_CHANGED (lifecycle touched/invalidated)"

        # No zones at all → need a map
        zones = _az(_st)
        if not zones:
            return True, "NO_ZONES (empty map — need fresh proposal)"

        # Price approaching any active zone
        if atr_m15 > 0:
            for z in zones:
                zp = z.get('price')
                if zp is None:
                    continue
                dist = abs(cur_price - float(zp))
                if dist <= INDICATOR_GATE_ZONE_APPROACH_ATR * atr_m15:
                    return True, f"ZONE_APPROACH ({z.get('type')} {zp} at {dist:.1f} USD)"
    except Exception as e:
        log.warning(f"[INDICATOR GATE] zone check error: {e}")

    return False, f"quiet ({int(elapsed/60)}m since last · no triggers)"


def should_run_executor_staging(now_ts, last_stage_ts, bars_cache, sig_state,
                                 last_price_staged, account,
                                 staging_ctx=None):
    """Event-driven gate for Executor in IDLE staging mode.

    Only fires on MEANINGFUL triggers — NOT on a time interval.
    Returns (bool, reason).

    Triggers (any of):
      · INITIAL — first call (last_stage_ts == 0)
      · PRICE_AT_ZONE — price is within 0.5 × ATR_M15 of an ACTIVE zone
      · VOLUME_SPIKE — last bar volume ≥ 2× avg(last 20)
      · BAR_BREAKOUT — last closed bar range ≥ 1.5 × ATR_M5 (strong move)
      · REGIME_CHANGED — zone_state bias/regime flipped since last fire
      · FALLBACK — 30 min elapsed with no other trigger (safety net)

    Blocks:
      · autonomous_staging disabled
      · OFF_HOURS
      · trade already active (not IDLE)
      · bars cache empty
    Also enforces a hard minimum interval (60s) between fires to avoid spam
    when events cluster (a single big move can touch multiple detectors).

    `staging_ctx` is a mutable dict the caller keeps alive across ticks so we
    can remember the last regime/bias/zone set and detect changes.
    """
    try:
        _cfg = _load_app_config().get('executor', {}) or {}
    except Exception:
        _cfg = {}
    if not _cfg.get('autonomous_staging_enabled', False):
        return False, "autonomous_staging disabled"
    if not is_within_trading_hours():
        return False, "OFF_HOURS"
    if account and account.get('has_signal'):
        return False, "trade active (not IDLE)"
    if not bars_cache or len(bars_cache) < 20:
        return False, "no bars"

    # Minimum gap between fires — keeps event clusters from triggering twice
    MIN_GAP_S = 60
    FALLBACK_S = float(_cfg.get('staging_fallback_interval_s', 1800))

    if last_stage_ts == 0:
        return True, "INITIAL"

    elapsed = now_ts - last_stage_ts
    if elapsed < MIN_GAP_S:
        return False, f"cooling ({int(elapsed)}s < {MIN_GAP_S}s)"

    cur_price = bars_cache[-1].get('close', 0)
    bars_m15 = aggregate_bars(bars_cache, 3)
    atr_m15 = atr(bars_m15, 14) or (atr(bars_cache, 14) or 1.0)
    atr_m5 = atr(bars_cache, 14) or 1.0

    ctx = staging_ctx if staging_ctx is not None else {}

    # ── Trigger: PRICE_AT_ZONE ──
    try:
        from zone_store import read_state, active_zones
        zstate = read_state(COMMON)
        zones = active_zones(zstate)
    except Exception:
        zstate, zones = {}, []
    near_threshold = 0.5 * atr_m15
    nearest = None
    nearest_dist = 1e9
    for z in zones:
        try:
            zp = float(z.get('price', 0) or 0)
            d = abs(cur_price - zp)
            if d < nearest_dist:
                nearest_dist = d
                nearest = z
        except Exception:
            continue
    # Only fire on ENTER — remember if we were already near this zone
    last_near_zone = ctx.get('last_near_zone')
    if nearest and nearest_dist <= near_threshold:
        zp = float(nearest.get('price', 0) or 0)
        zkey = f"{zp:.1f}:{nearest.get('strength','?')}"
        if last_near_zone != zkey:
            ctx['last_near_zone'] = zkey
            return True, f"PRICE_AT_ZONE ({nearest.get('strength','?')} {zp:.1f}, {nearest_dist:.2f}$)"
    else:
        ctx['last_near_zone'] = None

    # ── Trigger: VOLUME_SPIKE on last closed M5 bar ──
    try:
        recent = bars_cache[-21:-1]  # last 20 closed, skip live bar
        vols = [float(b.get('volume', 0) or 0) for b in recent if b.get('volume')]
        if vols and len(vols) >= 10:
            avg_vol = sum(vols) / len(vols)
            last_vol = float(bars_cache[-2].get('volume', 0) or 0)
            if avg_vol > 0 and last_vol >= 2.0 * avg_vol:
                last_spike_ts = ctx.get('last_spike_ts', 0)
                if now_ts - last_spike_ts >= 300:  # 5-min debounce per spike
                    ctx['last_spike_ts'] = now_ts
                    return True, f"VOLUME_SPIKE ({last_vol:.0f} vs avg {avg_vol:.0f})"
    except Exception:
        pass

    # ── Trigger: BAR_BREAKOUT (strong move on last closed bar) ──
    try:
        last = bars_cache[-2]
        rng = float(last.get('high', 0) or 0) - float(last.get('low', 0) or 0)
        if atr_m5 > 0 and rng >= 1.5 * atr_m5:
            last_bo_ts = ctx.get('last_bo_ts', 0)
            if now_ts - last_bo_ts >= 300:
                ctx['last_bo_ts'] = now_ts
                return True, f"BAR_BREAKOUT (range {rng:.2f} vs ATR_M5 {atr_m5:.2f})"
    except Exception:
        pass

    # ── Trigger: REGIME_CHANGED (from zone_state) ──
    try:
        cur_key = (zstate.get('bias'), zstate.get('regime'))
        prev_key = ctx.get('last_regime_key')
        if prev_key is not None and cur_key != prev_key:
            ctx['last_regime_key'] = cur_key
            return True, f"REGIME_CHANGED ({prev_key} → {cur_key})"
        ctx['last_regime_key'] = cur_key
    except Exception:
        pass

    # ── Fallback: long quiet period (safety net) ──
    if elapsed >= FALLBACK_S:
        return True, f"FALLBACK ({int(elapsed/60)}min quiet)"

    return False, f"quiet ({int(elapsed/60)}m, no trigger)"


def should_run_hunter(now_ts, last_hunter_ts, bars_cache, sig_state, account,
                      hunter_ctx=None):
    """Event-driven gate for the Reversion Hunter scanner.

    Hunter operates in TWO modes:
      · IDLE: no trade active → emit reversion setups, normal eligibility
      · ACTIVE (alt_hypothesis): trade active → emit setups OPPOSITE to current
        direction, tagged post_close=true (wait for trade close to become eligible)

    Returns (bool, reason).
    """
    try:
        _cfg = _load_app_config().get('hunter', {}) or {}
    except Exception:
        _cfg = {}
    if not _cfg.get('enabled', False):
        return False, "hunter disabled"
    if not is_within_trading_hours():
        return False, "OFF_HOURS"
    if not bars_cache or len(bars_cache) < 20:
        return False, "no bars"

    # Daily cap
    try:
        import hunter_stats as hs
        setups_today = hs.setup_count_today()
        max_per_day = int(_cfg.get('max_setups_per_day', 8))
        if setups_today >= max_per_day:
            return False, f"daily cap reached ({setups_today}/{max_per_day})"
        if hs.is_losing_streak_hit(threshold=int(_cfg.get('losing_streak_pause', 3))):
            return False, "losing streak pause (3+ losses today)"
        # Cooldown after last close
        last_close = hs.last_close_ts()
        cooldown = float(_cfg.get('cooldown_after_setup_close_s', 900))
        if last_close and (now_ts - last_close) < cooldown:
            return False, f"cooldown ({int((now_ts - last_close))}s < {int(cooldown)}s)"
    except Exception:
        pass  # hunter_stats unavailable, allow through

    # Per-mode minimum interval
    has_signal = bool(account and account.get('has_signal'))
    if has_signal:
        if not _cfg.get('alt_hypothesis_enabled', True):
            return False, "trade active, alt_hypothesis disabled"
        min_gap = float(_cfg.get('active_min_interval_s', 600))
    else:
        min_gap = float(_cfg.get('idle_min_interval_s', 300))

    if last_hunter_ts and (now_ts - last_hunter_ts) < min_gap:
        return False, f"throttle ({int(now_ts - last_hunter_ts)}s < {int(min_gap)}s)"

    # Storage concurrent cap
    try:
        import staged_setups as _ss
        concurrent = _ss.count_active_by_source('hunter')
        max_concurrent = int(_cfg.get('max_concurrent_setups', 3))
        if concurrent >= max_concurrent:
            return False, f"concurrent cap ({concurrent}/{max_concurrent})"
    except Exception:
        pass

    # First call always fires
    if last_hunter_ts == 0:
        return True, "INITIAL"

    # Reuse the same trigger events as staging (PRICE_AT_ZONE, VOLUME_SPIKE, etc.)
    # This keeps Hunter responsive to structural events without duplicating logic.
    cur_price = bars_cache[-1].get('close', 0)
    bars_m15 = aggregate_bars(bars_cache, 3)
    atr_m15 = atr(bars_m15, 14) or (atr(bars_cache, 14) or 1.0)
    atr_m5 = atr(bars_cache, 14) or 1.0
    ctx = hunter_ctx if hunter_ctx is not None else {}

    # PRICE_AT_ZONE
    try:
        from zone_store import read_state, active_zones
        zstate = read_state(COMMON)
        zones = active_zones(zstate)
    except Exception:
        zstate, zones = {}, []
    nearest_dist = 1e9
    nearest = None
    for z in zones:
        try:
            zp = float(z.get('price', 0) or 0)
            d = abs(cur_price - zp)
            if d < nearest_dist:
                nearest_dist = d
                nearest = z
        except Exception:
            continue
    last_near_zone = ctx.get('last_near_zone')
    if nearest and nearest_dist <= 0.5 * atr_m15:
        zp = float(nearest.get('price', 0) or 0)
        zkey = f"{zp:.1f}:{nearest.get('strength','?')}"
        if last_near_zone != zkey:
            ctx['last_near_zone'] = zkey
            return True, f"PRICE_AT_ZONE ({nearest.get('strength','?')} {zp:.1f})"
    else:
        ctx['last_near_zone'] = None

    # VOLUME_SPIKE
    try:
        recent = bars_cache[-21:-1]
        vols = [float(b.get('volume', 0) or 0) for b in recent if b.get('volume')]
        if vols and len(vols) >= 10:
            avg_vol = sum(vols) / len(vols)
            last_vol = float(bars_cache[-2].get('volume', 0) or 0)
            if avg_vol > 0 and last_vol >= 2.0 * avg_vol:
                last_spike_ts = ctx.get('last_spike_ts', 0)
                if now_ts - last_spike_ts >= 300:
                    ctx['last_spike_ts'] = now_ts
                    return True, f"VOLUME_SPIKE ({last_vol:.0f} vs avg {avg_vol:.0f})"
    except Exception:
        pass

    # BAR_BREAKOUT
    try:
        last = bars_cache[-2]
        rng = float(last.get('high', 0) or 0) - float(last.get('low', 0) or 0)
        if atr_m5 > 0 and rng >= 1.5 * atr_m5:
            last_bo_ts = ctx.get('last_bo_ts', 0)
            if now_ts - last_bo_ts >= 300:
                ctx['last_bo_ts'] = now_ts
                return True, f"BAR_BREAKOUT (range {rng:.2f} vs ATR_M5 {atr_m5:.2f})"
    except Exception:
        pass

    # REGIME_CHANGED
    try:
        cur_key = (zstate.get('bias'), zstate.get('regime'))
        prev_key = ctx.get('last_regime_key')
        if prev_key is not None and cur_key != prev_key:
            ctx['last_regime_key'] = cur_key
            return True, f"REGIME_CHANGED ({prev_key} → {cur_key})"
        ctx['last_regime_key'] = cur_key
    except Exception:
        pass

    # FALLBACK — if nothing has fired in 30 min, scan anyway
    FALLBACK_S = float(_cfg.get('fallback_interval_s', 1800))
    elapsed = now_ts - last_hunter_ts
    if elapsed >= FALLBACK_S:
        return True, f"FALLBACK ({int(elapsed/60)}min quiet)"

    return False, f"quiet ({int(elapsed/60)}m, no trigger)"


def main():
    check_single_instance()
    log.info("=" * 60)
    log.info("TRADER BRAIN v3 — Telegram-integrated architecture")
    log.info("=" * 60)

    # ── One-shot migration: archive legacy brain_zones.json to .legacy.json ──
    # Runs only if brain_zone_state.json doesn't exist yet. Idempotent.
    try:
        from zone_store import archive_legacy_zones
        if archive_legacy_zones(COMMON):
            log.info("[MIGRATE] brain_zones.json → brain_zones.legacy.json (first run with new zone store)")
    except Exception as e:
        log.warning(f"[MIGRATE] archive_legacy_zones failed: {e}")

    # Test TV connection
    h = tv("health")
    if not h or not h.get('success'):
        print("ERROR: TradingView not connected. Launch with CDP port 9223 first.")
        sys.exit(1)

    log.info(f"Connected: {h['symbol']} TF={h['timeframe']}")
    print(f"Connected: {h['symbol']} TF={h['timeframe']} — running {'debug' if DEBUG else 'headless'}")

    # 2026-05-04 v2: assert XAUUSD M5 ONCE at startup. After this, brain
    # avoids chart swap-fetch (uses ohlcv-current-chart for primary feed).
    # Chart stays stable at XAUUSD M5 for the operator's view.
    try:
        tv("symbol", EXPECTED_SYMBOL, timeout=15)
        tv("timeframe", "5", timeout=10)
        log.info(f"[CHART] asserted to {EXPECTED_SYMBOL} M5 (one-time at startup)")
    except Exception as e:
        log.warning(f"[CHART] startup assert failed: {e}")

    # ── Start Telegram Listener (thread) ──
    try:
        import telegram_listener
        telegram_listener.start_listener()
        log.info("Telegram listener started")
    except Exception as e:
        log.warning(f"Telegram listener failed to start: {e}")

    # ── Start Reduce-Risk daemon (thread) ──
    # Runs independently of the main loop. Polls brain_controls.json every 1s
    # for manual reduce_risk button presses and dispatches PARTIAL_CLOSE_PCT
    # immediately via write_order (which has its own lock). Decoupled from the
    # main loop so a click is honored within 1-2s even if the loop is blocked
    # on tv() calls or Indicator MTF fetches (incident 2026-04-27 19:14: a
    # second click waited 76s while the loop was processing ohlcv-sym
    # timeouts and Indicator pipeline).
    def _reduce_risk_worker():
        ctrl_path = os.path.join(COMMON, 'brain_controls.json')
        positions_path = POSITIONS
        while True:
            try:
                if not os.path.exists(ctrl_path):
                    time.sleep(1.0); continue
                with open(ctrl_path, 'r', encoding='utf-8') as _cf:
                    _ctrl = json.load(_cf)
                _rr_pct = _ctrl.get('reduce_risk_pct')
                _rr_ts = float(_ctrl.get('reduce_risk_ts') or 0)
                if not _rr_pct or not _rr_ts or (time.time() - _rr_ts) > 60:
                    time.sleep(1.0); continue
                # Read fresh positions
                with open(positions_path, 'r', encoding='utf-8') as _pf:
                    _pos_data = json.load(_pf)
                _positions = _pos_data.get('positions', []) or []
                if not _positions:
                    log.info(f"[REDUCE_RISK] {_rr_pct}% requested but no positions — skipped")
                    _ctrl['reduce_risk_pct'] = None
                    _ctrl['reduce_risk_ts'] = 0
                    with open(ctrl_path, 'w', encoding='utf-8') as _cf:
                        json.dump(_ctrl, _cf, indent=2)
                    time.sleep(1.0); continue
                _orders = []
                _full_closed = 0
                _partials = 0
                for _p in _positions:
                    try:
                        _tk = int(_p.get('ticket') or _p.get('ticket_id') or 0)
                        _lot = float(_p.get('volume', 0) or 0)
                    except Exception:
                        continue
                    if not _tk or _lot < 0.01:
                        continue
                    _lc = round(_lot * float(_rr_pct) / 100.0, 2)
                    _remaining = round(_lot - _lc, 2)
                    # Promote-to-full-close rule: if either the close amount
                    # or the remainder falls under the broker minimum (0.01
                    # XAUUSD), close the whole ticket instead of skipping or
                    # leaving an unmanageable scrap. This matches the user
                    # intent: "if 25% would leave only 25% of the original
                    # behind, just close it all" — applied at the broker-min
                    # boundary so small tickets always get reduced fully.
                    if _lc < 0.01 or _remaining < 0.01:
                        _orders.append({"action": "CLOSE_TICKET", "ticket": _tk})
                        _full_closed += 1
                    else:
                        _orders.append({
                            "action": "PARTIAL_CLOSE_PCT",
                            "ticket": _tk,
                            "pct": int(_rr_pct),
                        })
                        _partials += 1
                if _orders:
                    write_order({"ts": int(time.time()), "orders": _orders}, urgent=True)
                    log.warning(
                        f"[REDUCE_RISK] Manual {_rr_pct}% reduction sent to EA "
                        f"on {len(_orders)} ticket(s) [worker thread]"
                    )
                    try:
                        notify("partial_close",
                               f"🛡 REDUCE RISK manual {_rr_pct}% — {len(_orders)} ticket(s)")
                    except Exception:
                        pass
                # Consume the flag (clear)
                _ctrl['reduce_risk_pct'] = None
                _ctrl['reduce_risk_ts'] = 0
                with open(ctrl_path, 'w', encoding='utf-8') as _cf:
                    json.dump(_ctrl, _cf, indent=2)
            except Exception as _e:
                log.debug(f"[REDUCE_RISK worker] error: {_e}")
            time.sleep(1.0)

    try:
        _rr_thread = threading.Thread(target=_reduce_risk_worker, daemon=True, name="reduce_risk")
        _rr_thread.start()
        log.info("Reduce-Risk worker thread started")
    except Exception as e:
        log.warning(f"Reduce-Risk worker failed to start: {e}")

    # ── Fast price tick (independent of main loop, via MT5 Python API) ──
    # Writes brain_tick.json every 200ms with current bid. Uses MT5 module
    # (~0.04ms per tick read) instead of tv.js subprocess (~2s overhead).
    # Decouples dashboard live price from main loop — works even while the
    # brain is waiting on Claude opus 200s+.
    #
    # 2026-05-06: També construeix M1 bars des de ticks (per detecció wick
    # rejection a M1, no M5). Resolució real per detectar sweeps ràpids.
    TICK_FILE = os.path.join(COMMON, 'brain_tick.json')
    MARKET_TICK_FILE = os.path.join(COMMON, 'brain_market_tick.json')  # written by EA
    BROKER_SYMBOL = 'XAUUSD-VIPc'  # current broker symbol (was XAUUSD.crp - wrong)
    def _price_tick_worker():
        try:
            import MetaTrader5 as _mt5
        except ImportError:
            log.warning("[TICK] MetaTrader5 module not installed — usant brain_market_tick.json")
            _mt5 = None
        if _mt5 and not _mt5.initialize():
            log.warning(f"[TICK] mt5.initialize failed: {_mt5.last_error()} — usant brain_market_tick.json")
            _mt5 = None
        last_price = None
        last_write_ts = 0
        # ── M1 builder state ──
        # Buffer ticks dins del minut actual; quan canvia el minut, build M1
        # bar i append-lo a _M1_BARS_CACHE (rotating, ~120 bars).
        global _M1_BARS_CACHE
        if '_M1_BARS_CACHE' not in globals():
            _M1_BARS_CACHE = []
        _current_min_ts = None
        _current_min_ticks = []  # [{ts, price}]
        # Reading from brain_market_tick.json (reliable, written by EA every tick)
        # Avoids broker-symbol mismatch issues (was failing with XAUUSD.crp)
        while True:
            try:
                px = None
                # Primary source: MT5 direct (if available)
                if _mt5:
                    try:
                        info = _mt5.symbol_info_tick(BROKER_SYMBOL)
                        if info and info.bid > 0:
                            px = float(info.bid)
                    except Exception:
                        px = None
                # Fallback 1: brain_market_tick.json (reliable, written by EA)
                if px is None and os.path.exists(MARKET_TICK_FILE):
                    try:
                        with open(MARKET_TICK_FILE, 'r', encoding='utf-8') as _mtf:
                            _md = json.load(_mtf)
                        _bid = _md.get('bid')
                        if _bid and _bid > 0:
                            px = float(_bid)
                    except Exception:
                        px = None
                # Fallback 2: tv.js
                if px is None:
                    q = tv("quote", timeout=8)
                    if q and q.get('success') and q.get('last') is not None:
                        px = float(q['last'])
                if px is not None:
                    now = time.time()
                    # ── M1 bar builder ──
                    minute_ts = int(now) - (int(now) % 60)  # bar open ts (minute boundary)
                    if _current_min_ts is None:
                        _current_min_ts = minute_ts
                    if minute_ts != _current_min_ts:
                        # Minut canviat → build M1 bar from accumulated ticks
                        if _current_min_ticks:
                            prices = [t['price'] for t in _current_min_ticks]
                            m1_bar = {
                                'time': _current_min_ts,
                                'open': prices[0],
                                'high': max(prices),
                                'low':  min(prices),
                                'close': prices[-1],
                                'volume': len(_current_min_ticks),  # tick count proxy
                            }
                            _M1_BARS_CACHE.append(m1_bar)
                            # Rotating buffer: keep last 120 M1 bars (~2h)
                            if len(_M1_BARS_CACHE) > 120:
                                _M1_BARS_CACHE = _M1_BARS_CACHE[-120:]
                        # Reset for new minute
                        _current_min_ts = minute_ts
                        _current_min_ticks = []
                    _current_min_ticks.append({'ts': now, 'price': px})

                    # Write tick (existing logic)
                    if (last_price is None
                            or abs(px - last_price) >= 0.01
                            or (now - last_write_ts) >= 5.0):
                        tmp = TICK_FILE + '.tmp'
                        with open(tmp, 'w', encoding='utf-8') as f:
                            json.dump({'ts': now, 'price': px, 'symbol': BROKER_SYMBOL}, f)
                        try:
                            os.replace(tmp, TICK_FILE)
                        except Exception:
                            pass
                        last_price = px
                        last_write_ts = now
            except Exception:
                pass
            time.sleep(0.2)  # 200ms — effectively real-time
    try:
        _tick_thread = threading.Thread(target=_price_tick_worker, daemon=True, name="price_tick")
        _tick_thread.start()
        log.info("Price tick thread started (MT5 direct, 200ms cadence)")
    except Exception as e:
        log.warning(f"Price tick thread failed to start: {e}")

    # ── Signal state manager ──
    from signal_state import get_state
    sig_state = get_state()
    initial_pos = read_json(POSITIONS)
    existing = initial_pos.get('positions', [])
    if sig_state.is_active():
        if not existing:
            # Ghost signal: active flag set but EA reports 0 positions. Happens when
            # brain died mid-trade and EA closed everything (DD auto-close, manual,
            # etc.) without Python present to tidy up. Reset before starting so the
            # dashboard stops showing a phantom trade.
            log.warning(f"[BOOTSTRAP] Ghost signal detected: active={sig_state.get('direction')} but 0 positions — clearing")
            # Bootstrap: no reliable end_balance (positions already gone before
            # we woke up). Fall back to realized_profit for the summary.
            sig_state.close_signal()
        else:
            log.info(f"Loaded active signal from disk: {sig_state.get('direction')} @ {sig_state.get('entry_price')} (source={sig_state.get('source')})")
    elif existing:
        # ── ADOPTION: positions open without active brain signal ──
        log.info(f"[ADOPT] Found {len(existing)} open positions with no active brain signal — adopting as brain-managed")
        _adopt_bal = 0.0
        try:
            _adopt_bal = float(read_json(POSITIONS).get('account', {}).get('balance', 0) or 0)
        except Exception:
            pass
        if sig_state.adopt_positions(existing, start_balance=_adopt_bal):
            log.info(f"[ADOPT] SUCCESS: {sig_state.get('direction')} @ {sig_state.get('entry_price')}, {sig_state.get('total_lots')} lots, channel=ADOPTED")

    # If we've loaded or adopted a live signal with positions but no TradePlan
    # exists, defer the plan generation until bars_cache is populated — we flag
    # it here so the first main-loop tick with bars triggers apply_trade_plan.
    _plan_bootstrap_pending = False
    try:
        _plan_file = os.path.join(COMMON, 'brain_trade_plan.json')
        if sig_state.is_active() and not os.path.exists(_plan_file):
            _plan_bootstrap_pending = True
            log.info("[PLAN] Bootstrap pending: active signal without trade_plan.json — will generate once bars are ready")
    except Exception:
        pass

    engine = FastEngine()

    # ── Load config sections for the new event-driven architecture ──
    app_cfg = _load_app_config()
    zone_lifecycle_cfg = app_cfg.get('zone_lifecycle', {}) or {}
    event_detector_cfg = app_cfg.get('event_detector', {}) or {}
    executor_cfg = app_cfg.get('executor', {}) or {}

    # ── Event detector instance ──
    from event_detector import EventDetector, log_invoked
    event_detector = EventDetector(event_detector_cfg, common_dir=COMMON)

    bars_cache = []
    last_indicator = 0
    last_m15_slot = None    # tracks (Y,M,D,H,M15slot) so INDICATOR fires once per M15 close
    last_executor = 0
    # Autonomous staging state
    last_staging_ts = 0
    last_staging_price = None
    # Context for event-driven staging gate (remembers last zone/regime seen
    # so we only fire on CHANGES, not on every tick the condition holds).
    _staging_gate_ctx = {}
    staging_trades_today = 0
    staging_day = None
    _staging_future = None  # for async LLM call
    # Hunter state (parallel to staging)
    last_hunter_ts = 0
    _hunter_gate_ctx = {}
    _hunter_future = None

    # Control file handling (dashboard buttons)
    _CTRL_FILE = os.path.join(COMMON, 'brain_controls.json')
    _last_ctrl_read = 0
    def _consume_control(key):
        """Read brain_controls.json, if `key` is set True consume it (reset) and return True."""
        try:
            if not os.path.exists(_CTRL_FILE):
                return False
            with open(_CTRL_FILE, 'r', encoding='utf-8') as _cf:
                data = json.load(_cf)
            if data.get(key):
                data[key] = False
                with open(_CTRL_FILE, 'w', encoding='utf-8') as _cf:
                    json.dump(data, _cf, indent=2)
                return True
        except Exception:
            pass
        return False
    prev_had_signal = False
    prev_autonomous_mode = False
    prev_positions_map = {}

    # ── Indicator gate state: remembers what the map looked like last time
    # the INDICATOR+REVIEWER pipeline ran, so we can skip cycles where
    # nothing structurally meaningful has changed (saves ~40-60% of cost
    # during quiet markets / weekends / Asia session).
    _ind_last_run_ts = 0.0
    _ind_last_price = None
    _ind_last_zones_updated = None   # ISO string from zone_state
    _ind_last_signal_id = None

    # Async AI futures — main loop never blocks waiting for Claude/DeepSeek
    _indicator_future = None
    _executor_future = None
    _last_trigger_events = []  # events passed to the Executor; used when persisting the response
    last_screenshot = 0
    last_tg_check = 0
    screenshot_path = None
    scan = 0

    # State vars for status file (read by brain_dashboard.py)
    _ind_status = 'WAITING'
    _exec_status = 'IDLE'
    _exec_action = '...'
    _exec_conf = 0
    _exec_mental = '...'
    _exec_reasoning = ''
    _exec_plan = ''
    _exec_action = '?'
    _last_snapshot_ts = 0
    _last_periodic_redraw_ts = 0
    _tv_last_ok_ts = time.time()
    _tv_fail_streak = 0
    _tv_degraded_logged = False

    try:
        # Flag for human-driven research sessions (e.g. evaluating GC1!
        # futures vs spot). When this file exists in COMMON, the brain
        # idles the main loop — no TV reads, no symbol guard, no LLM
        # calls. The operator can switch the chart freely. Remove the
        # file and the brain resumes within FAST_INTERVAL seconds.
        from pathlib import Path as _Path
        _RESEARCH_FLAG = _Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files\research_mode.flag")
        _research_logged = False

        while True:
            t0 = time.time()
            scan += 1

            # ── 0. Research mode pause ──
            if _RESEARCH_FLAG.exists():
                if not _research_logged:
                    log.warning("[RESEARCH] research_mode.flag present — brain idling. Symbol guard disabled. Remove flag to resume.")
                    _research_logged = True
                time.sleep(FAST_INTERVAL)
                continue
            elif _research_logged:
                log.warning("[RESEARCH] flag removed — resuming normal operation")
                _research_logged = False

            # ── 1. Read TV bars ──
            # ohlcv-tf-sym: atomic [symbol-guard + swap to M1 + read + restore TF].
            # Decouples brain's M1 analysis from visible TF — the operator can
            # browse M5/M15/H1 while the brain keeps reading M1 internally.
            # The symbol guard is critical: without it a stray DXY/USDJPY
            # chart would feed bogus prices (~$103) to the brain.
            # 2026-05-04 v3: CHART ASSERTION + READ pattern.
            # Abans de cada read, assertim XAUUSD M5 (idempotent — no-op
            # si ja és correcte). Llavors llegim del chart actual (sense
            # swap). Si algun fetch anterior va deixar el chart a GC1!/etc,
            # això el restaura ABANS del read → bars correctes.
            try:
                tv("symbol", EXPECTED_SYMBOL, timeout=15)
                tv("timeframe", "5", timeout=10)
            except Exception:
                pass
            ohlcv = tv("ohlcv", "300", timeout=15)
            if ohlcv and ohlcv.get('bars'):
                got_sym = ohlcv.get('symbol')
                if got_sym and got_sym != EXPECTED_SYMBOL:
                    _tv_fail_streak += 1
                    log.error(f"[GUARD] TV returned bars for WRONG symbol: expected={EXPECTED_SYMBOL} got={got_sym} — DISCARDING")
                else:
                    bars_cache = ohlcv['bars']
                    if _tv_fail_streak >= 3:
                        log.info(f"[TV] Feed recovered after {_tv_fail_streak} failed reads")
                    _tv_last_ok_ts = time.time()
                    _tv_fail_streak = 0
                    _tv_degraded_logged = False
            else:
                _tv_fail_streak += 1

            _tv_last_ok_age = max(0.0, time.time() - _tv_last_ok_ts) if _tv_last_ok_ts else None
            _tv_feed_ok = _tv_last_ok_age is not None and _tv_last_ok_age <= 30
            if not _tv_feed_ok and not _tv_degraded_logged:
                age_txt = f"{_tv_last_ok_age:.0f}s" if _tv_last_ok_age is not None else "unknown"
                log.error(f"[TV] Feed degraded — no successful OHLCV read for {age_txt} (fail_streak={_tv_fail_streak})")
                _tv_degraded_logged = True

            if not bars_cache:
                log.warning("No bars, retrying...")
                time.sleep(FAST_INTERVAL)
                continue

            price = bars_cache[-1]['close']

            # ── Approach Phase Tracker update (cada cycle 2s) ──
            # Track aproximació del preu a cada zona del mapa amb delta
            # institucional acumulat. Consumit pel FastEngine (gate),
            # LLM payloads, i visualització al chart.
            try:
                _at = _get_approach_tracker()
                if _at is not None and _at.enabled:
                    try:
                        from zone_store import read_state, active_zones
                        _zs = read_state(COMMON)
                        _zones_now = active_zones(_zs) or []
                    except Exception:
                        _zones_now = []
                    if _zones_now:
                        # Optimització flicker: només fetchem gc_m1 si hi ha
                        # alguna zona dins approach_dist + 3$ buffer. Si totes
                        # les zones estan lluny, no necessitem el delta i evitem
                        # el swap del chart cap a GC1!.
                        try:
                            _at_cfg = config.get("approach_tracker", {}) or {}
                            _max_dist = float(_at_cfg.get("approach_dist", 5.0)) + 3.0
                        except Exception:
                            _max_dist = 8.0
                        _close_zones = [z for z in _zones_now
                                        if abs(float(z.get("price", 0)) - float(price)) <= _max_dist]
                        if _close_zones:
                            # M1 GC1! cache 30s — captem cada bar M1 ràpid
                            _gc_m1 = _fetch_gc_bars("gc_m1", "1", 60, cache_seconds=30)
                        else:
                            _gc_m1 = []  # cap zona prop → no fetch, no flicker
                        _at.update(zones=_zones_now,
                                   price_now=float(price),
                                   gc_m1_bars=_gc_m1,
                                   spot_m5_bars=bars_cache)
            except Exception as _at_err:
                log.warning(f"[APPROACH_TRACKER] update failed: {_at_err}")

            # ── Bootstrap TradePlan now that bars are available ──
            if _plan_bootstrap_pending and sig_state.is_active():
                try:
                    _bs_positions = (read_json(POSITIONS) or {}).get('positions', []) or []
                    if _bs_positions:
                        _bs_dir = sig_state.get('direction')
                        if _bs_dir in ('BUY', 'SELL'):
                            apply_trade_plan(_bs_positions, _bs_dir, bars_cache,
                                             reason='adopt_bootstrap')
                            log.info("[PLAN] Bootstrap plan generated for adopted signal")
                            _plan_bootstrap_pending = False
                except Exception as _bpe:
                    log.warning(f"[PLAN] bootstrap apply failed: {_bpe}")
                    _plan_bootstrap_pending = False  # don't spin; retry on next position change

            # Periodic status snapshot (every 60s) — gives the operator a quick
            # "what's going on right now" view without tailing the whole log.
            if time.time() - _last_snapshot_ts > 60:
                try:
                    _account_snap = get_account_state()
                    log_status_snapshot(price, bars_cache, _account_snap, sig_state, engine,
                                        _ind_bias=_ind_bias if '_ind_bias' in dir() else '?',
                                        _ind_regime=_ind_regime if '_ind_regime' in dir() else '?',
                                        _exec_action=_exec_action, _exec_conf=_exec_conf)
                except Exception as _se:
                    log.warning(f"snapshot failed: {_se}")
                _last_snapshot_ts = time.time()

            # ── 2. Read account state ──
            account = get_account_state()

            # ── 2.0. Periodic TV redraw — guarantees the operator always sees
            # current state. Triggers:
            #   · Position count changed (trade opened/closed, partial fills)
            #   · Signal state changed (active flip, side flip)
            #   · Staged setups changed (armed/invalidated/expired/fired) —
            #     critical perquè quan no hi ha trade obert l'únic indicador
            #     visual del que el sistema està fent és la línia groga del
            #     setup. Si s'invalida i no repintem, queda stale al chart.
            #   · Every 30s while there's an active trade or signal
            # The differential update inside draw_reasoning means a no-op redraw
            # is cheap (skipped when fingerprint matches), so we can be liberal.
            _now_pos = len(account.get('positions') or [])
            _now_sig_active = bool(sig_state.get('active') if hasattr(sig_state, 'get') else False)
            _now_sig_side = sig_state.get('side') if hasattr(sig_state, 'get') else None
            # Fingerprint dels staged setups vius (id+direction+zone+target).
            # Qualsevol canvi (afegir, esborrar, modificar) → repintat.
            try:
                _staged_now = load_all_staged_setups() or []
                _staged_fp = tuple(sorted(
                    f"{s.get('id','?')}|{s.get('direction','?')}|"
                    f"{s.get('trigger_zone') or s.get('zone_price') or s.get('price') or 0:.2f}|"
                    f"{s.get('tp_target') or s.get('profit_target') or 0:.2f}"
                    for s in _staged_now
                ))
            except Exception:
                _staged_fp = tuple()
            _staged_changed = (_staged_fp != getattr(main, '_last_staged_fp', None))
            _state_changed = (
                _now_pos != getattr(main, '_last_pos_count', -1)
                or _now_sig_active != getattr(main, '_last_sig_active', None)
                or _now_sig_side != getattr(main, '_last_sig_side', None)
                or _staged_changed
            )
            _periodic_due = (
                (_now_pos > 0 or _now_sig_active or len(_staged_now) > 0)
                and (time.time() - _last_periodic_redraw_ts > 30)
            )
            if _state_changed or _periodic_due:
                try:
                    redraw_tv(bars_cache, account, sig_state)
                except Exception as _rde:
                    log.debug(f"[TV] periodic redraw failed: {_rde}")
                _last_periodic_redraw_ts = time.time()
                main._last_pos_count = _now_pos
                main._last_sig_active = _now_sig_active
                main._last_sig_side = _now_sig_side
                main._last_staged_fp = _staged_fp

            # ── 2.1. Live re-adoption — handle manual position changes ──
            # If the operator manually closes the brain's trade and opens a new
            # one (e.g. flips BUY→SELL on the broker), the brain must update
            # sig_state to track the new positions. Without this, the brain
            # keeps managing the OLD direction, FAST/Executor decisions go to
            # the wrong side, and the dashboard shows phantom state.
            #
            # Cases handled per tick:
            #   A) sig_active + 0 positions  → ghost signal → close_signal
            #   B) sig_active + opposite dir → manual flip → close + re-adopt
            #   C) sig_inactive + positions  → manual open → adopt
            #   D) all other → no-op
            #
            # Skip if `closing` is set (we're mid-CLOSE_ALL handshake; allow
            # the EA to settle before we react to the empty book).
            try:
                _positions_now = account.get('positions') or []
                _sig_active = sig_state.is_active()
                _sig_closing = bool(sig_state.is_closing())
                if not _sig_closing:
                    # Determine actual direction from positions (majority by volume)
                    if _positions_now:
                        _buy_vol = sum(float(p.get('volume', 0) or 0)
                                       for p in _positions_now if p.get('type') == 'BUY')
                        _sell_vol = sum(float(p.get('volume', 0) or 0)
                                        for p in _positions_now if p.get('type') == 'SELL')
                        _actual_dir = 'BUY' if _buy_vol >= _sell_vol else 'SELL'
                    else:
                        _actual_dir = None

                    _sig_dir = sig_state.get('direction') if _sig_active else None

                    if _sig_active and not _positions_now:
                        # Case A: ghost signal — clear it.
                        # Pass end_balance so close_signal computes ground-truth
                        # net P&L (= balance_now − signal_start_balance), which
                        # the TG alert reports. Without this, falls back to
                        # gross realized_profit which can still be 0 if the
                        # heartbeat hasn't replayed the closed-ticket profits
                        # yet, and we mis-report P&L=$0.00 on a real loss/win.
                        _gh_end_bal = float(account.get('balance', 0) or 0)
                        _gh_start_bal = float(sig_state.get('signal_start_balance', 0) or 0)
                        _gh_net_pnl = ((_gh_end_bal - _gh_start_bal)
                                       if (_gh_end_bal and _gh_start_bal)
                                       else float(sig_state.get('realized_profit', 0.0) or 0))
                        _gh_dir = sig_state.get('direction', '')
                        _gh_entry = float(sig_state.get('entry_price') or 0)
                        log.warning(
                            f"[ADOPT-LIVE] Ghost signal: sig={_sig_dir} but EA reports 0 positions "
                            f"— clearing (end_balance=${_gh_end_bal:.2f}, net_pnl=${_gh_net_pnl:+.2f})"
                        )
                        # Write SIGNAL_CLOSE to journal BEFORE close_signal()
                        # clears state — l'EXECUTOR llegeix recent_closes_by_zone
                        # per saber que aquest nivell ja ha mort un trade i no
                        # repetir el mateix sense estructura nova. Sense aquest
                        # log, l'expert qualitatiu pren decisions a cegues.
                        try:
                            trade_history.log_event(
                                type='SIGNAL_CLOSE',
                                direction=_gh_dir,
                                source='GHOST',
                                price=_gh_entry,
                                reason=(f'Ghost signal cleanup (auto_close, EA DD, manual broker close — '
                                        f'no positions for >0s). net=${_gh_net_pnl:+.2f}'),
                                pnl_delta=_gh_net_pnl,
                                trade_id=sig_state.get('trade_id'),
                            )
                        except Exception as _glog_e:
                            log.debug(f"[ADOPT-LIVE] journal log failed: {_glog_e}")
                        sig_state.close_signal(end_balance=_gh_end_bal)
                        # Reset FastEngine state so any cached zones/partials don't leak
                        try:
                            engine._reset_for_new_signal((None, 0))
                        except Exception:
                            pass
                        try:
                            import llm_conversation as _lcv
                            _lcv.reset('HUNTER')
                        except Exception:
                            pass
                        # NOTE 2026-05-05: NO resetegem EXECUTOR session a trade
                        # close. La conversa persistent és la memòria narrativa
                        # que necessita per no contradir-se entre trades. Si la
                        # sessió té 4h de TTL i acumulem decisions, el LLM pot
                        # raonar amb continuïtat: "fa 1h vaig SELL@4552 perquè X,
                        # ara vols BUY@4552 — què ha canviat?". Sense això, cada
                        # trade comença a cegues. El prompt explícita que els
                        # trades anteriors poden estar tancats; el LLM ho gestiona.
                        # try:
                        #     import claude_session_manager as _csm
                        #     _csm.reset('EXECUTOR')
                        # except Exception:
                        #     pass
                    elif _sig_active and _actual_dir and _actual_dir != _sig_dir:
                        # Case B: direction mismatch — close current sig, adopt new.
                        # Pass end_balance for accurate net P&L on the TG close alert.
                        _flip_end_bal = float(account.get('balance', 0) or 0)
                        _flip_start_bal = float(sig_state.get('signal_start_balance', 0) or 0)
                        _flip_net = ((_flip_end_bal - _flip_start_bal)
                                     if (_flip_end_bal and _flip_start_bal)
                                     else float(sig_state.get('realized_profit', 0.0) or 0))
                        _flip_dir = sig_state.get('direction', '')
                        _flip_entry = float(sig_state.get('entry_price') or 0)
                        log.warning(
                            f"[ADOPT-LIVE] Direction flip: sig={_sig_dir} but positions={_actual_dir} "
                            f"— closing sig + re-adopting {_actual_dir} ({len(_positions_now)} ticket(s))"
                        )
                        # Journal log so EXECUTOR sees this trade in recent_closes
                        try:
                            trade_history.log_event(
                                type='SIGNAL_CLOSE',
                                direction=_flip_dir,
                                source='FLIP',
                                price=_flip_entry,
                                reason=f'Direction flip: {_flip_dir}→{_actual_dir} '
                                       f'({len(_positions_now)} ticket(s)). net=${_flip_net:+.2f}',
                                pnl_delta=_flip_net,
                                trade_id=sig_state.get('trade_id'),
                            )
                        except Exception as _flog_e:
                            log.debug(f"[ADOPT-LIVE] journal log failed: {_flog_e}")
                        sig_state.close_signal(end_balance=_flip_end_bal)
                        if sig_state.adopt_positions(_positions_now, start_balance=account.get('balance', 0)):
                            log.info(
                                f"[ADOPT-LIVE] Re-adopted {sig_state.get('direction')} @ "
                                f"{sig_state.get('entry_price')} "
                                f"({sig_state.get('total_lots')} lots)"
                            )
                            try:
                                notify("trade_opened",
                                       f"🔄 ADOPTED {sig_state.get('direction')} {sig_state.get('total_lots')} @ "
                                       f"{sig_state.get('entry_price')} (manual flip detected)")
                            except Exception:
                                pass
                            # Refresh account so downstream code uses new direction
                            account = get_account_state()
                    elif (not _sig_active) and _positions_now:
                        # Case C: positions appeared without an active signal
                        log.warning(
                            f"[ADOPT-LIVE] Manual open detected: {len(_positions_now)} position(s) "
                            f"without active sig — adopting"
                        )
                        if sig_state.adopt_positions(_positions_now, start_balance=account.get('balance', 0)):
                            log.info(
                                f"[ADOPT-LIVE] Adopted {sig_state.get('direction')} @ "
                                f"{sig_state.get('entry_price')} "
                                f"({sig_state.get('total_lots')} lots)"
                            )
                            try:
                                notify("trade_opened",
                                       f"🔄 ADOPTED {sig_state.get('direction')} {sig_state.get('total_lots')} @ "
                                       f"{sig_state.get('entry_price')} (manual open)")
                            except Exception:
                                pass
                            account = get_account_state()
            except Exception as _ae:
                log.warning(f"[ADOPT-LIVE] check failed: {_ae}")

            # Broker-authoritative reconciliation: whenever MT5 has live positions
            # or balance changes, broker state overwrites local assumptions.
            try:
                if sig_state.is_active():
                    if sig_state.reconcile_with_broker(account.get('positions') or [],
                                                       balance=account.get('balance', 0)):
                        account = get_account_state()
            except Exception as _re:
                log.warning(f"[BROKER_SYNC] reconcile failed: {_re}")

            # ── 2.5. Daily anchor (start-of-day balance) ──
            daily_anchor = update_daily_anchor(account.get('balance', 0))

            # ── 3. Write status for GUI dashboard ──
            zones_data = load_zones()
            # Market context for dashboard (cached 60s DXY / 120s yield internally)
            _mc_status = None
            try:
                import market_context as _mc_mod
                _mc_status = _mc_mod.build_market_context(
                    bars_m5=bars_cache,
                    account=account,
                    tv_helper=tv,
                    now_utc=datetime.now(timezone.utc),
                    for_executor=True,
                    atr_m5=atr(bars_cache, 14) or 0,
                    config=app_cfg,
                )
            except Exception as _mce:
                log.debug(f"[STATUS] market_context skip: {_mce}")
            write_status({
                'price': price,
                'rsi': rsi([b['close'] for b in bars_cache], 14) or 0,
                'atr': atr(bars_cache, 14) or 0,
                'vol': vol_ratio(bars_cache),
                'candle': candle_type(bars_cache[-1]),
                'high20': max(b['high'] for b in bars_cache[-20:]),
                'low20': min(b['low'] for b in bars_cache[-20:]),
                'scan': scan,
                'indicator': {
                    'status': _ind_status,
                    'bias': zones_data.get('bias', '?'),
                    'context': zones_data.get('context', ''),
                    'zones': len(engine.reversal_zones),
                    'last': last_indicator,
                    'interval': INDICATOR_INTERVAL,
                },
                'executor': {
                    'status': _exec_status,
                    'action': _exec_action,
                    'confidence': _exec_conf,
                    'mental': _exec_mental,
                    'reasoning': _exec_reasoning,
                    'plan': _exec_plan,
                    'last': last_executor,
                    'interval': EXECUTOR_INTERVAL,
                },
                'fast': {
                    'zones_count': len(engine.reversal_zones),
                    'cooldown': max(0, engine.MIN_AVG_COOLDOWN - (time.time() - engine.last_avg_time)),
                },
                'staged_setup': load_staged_setup(),
                'daily': {
                    'date': daily_anchor.get('date', ''),
                    'start_balance': daily_anchor.get('start_balance', 0),
                    'start_ts': daily_anchor.get('start_ts', 0),
                    'goal_pct': DAILY_GOAL_PCT,
                },
                'feed': {
                    'source': 'tradingview_cdp',
                    'cdp_port': 9223,
                    'expected_symbol': EXPECTED_SYMBOL,
                    'connected': _tv_feed_ok,
                    'status': 'OK' if _tv_feed_ok else 'DEGRADED',
                    'consecutive_failures': _tv_fail_streak,
                    'last_ok_age_s': round(_tv_last_ok_age, 1) if _tv_last_ok_age is not None else None,
                    'bars_cached': len(bars_cache),
                    'last_bar_time': bars_cache[-1].get('time') if bars_cache else None,
                },
                'zones': engine.reversal_zones,
                'external': (_mc_status or {}).get('external'),
                'htf': (_mc_status or {}).get('htf'),
                'market_state': (_mc_status or {}).get('market_state'),
            })

            # ── 3.4. REALIZED P&L TRACKING — sum profit of tickets that closed/reduced ──
            if sig_state.is_active():
                current_positions = read_json(POSITIONS).get('positions', [])
                current_map = {p.get('ticket', 0): {'volume': p.get('volume', p.get('lot', 0)),
                                                      'profit': broker_position_pnl(p),
                                                      'comment': p.get('comment', ''),
                                                      'type': p.get('type', ''),
                                                      'price_open': float(p.get('price_open', 0) or p.get('open_price', 0) or 0)}
                               for p in current_positions if p.get('ticket', 0)}
                # Detect position-count change (new ticket appeared OR ticket closed).
                # On ANY change, force an immediate Executor re-evaluation so the LLM
                # can react to the new state (supervise opens/closes/modifies fast).
                _position_changed = False
                if prev_positions_map:
                    _new_tks = set(current_map.keys()) - set(prev_positions_map.keys())
                    _gone_tks = set(prev_positions_map.keys()) - set(current_map.keys())
                    if _new_tks or _gone_tks:
                        _position_changed = True
                    else:
                        for tk, prev in prev_positions_map.items():
                            cur = current_map.get(tk)
                            if cur and abs(cur['volume'] - prev['volume']) > 0.001:
                                _position_changed = True
                                break
                if _position_changed:
                    try:
                        _ctrl_path = os.path.join(COMMON, 'brain_controls.json')
                        _ctrl = {}
                        if os.path.exists(_ctrl_path):
                            with open(_ctrl_path, 'r', encoding='utf-8') as _cf:
                                _ctrl = json.load(_cf)
                        _ctrl['force_executor'] = True
                        _ctrl['force_executor_reason'] = 'position_changed'
                        _ctrl['force_executor_ts'] = time.time()
                        with open(_ctrl_path, 'w', encoding='utf-8') as _cf:
                            json.dump(_ctrl, _cf, indent=2)
                        log.info(f"[SUPERVISE] Position change detected — forcing Executor invocation")
                    except Exception as _pce:
                        log.debug(f"[SUPERVISE] could not set force_executor: {_pce}")

                    # ── TG notification: NEW ticket added (averaging executed) ──
                    # Fires regardless of who placed the order: FAST engine, Executor,
                    # Hunter sniper, manual. The user wants to know any time exposure
                    # grows. Tickets that GO are notified separately by the close path.
                    try:
                        if _new_tks:
                            _total_lots_now = sum(c.get('volume', 0) for c in current_map.values())
                            _avg_count = sig_state.get('avg_count', 0) if sig_state.is_active() else len(_new_tks)
                            for _new_tk in _new_tks:
                                _ndata = current_map.get(_new_tk) or {}
                                _ndir = _ndata.get('type', sig_state.get('direction', '?') if sig_state.is_active() else '?')
                                _nlot = float(_ndata.get('volume', 0) or 0)
                                _nprice = float(_ndata.get('price_open', 0) or 0)
                                notify("averaging",
                                       f"⚡ AVG #{_avg_count} {_ndir} {_nlot:.2f} @ {_nprice:.2f}  ·  "
                                       f"total {_total_lots_now:.2f} lots")
                    except Exception as _avg_e:
                        log.debug(f"[NOTIFY] averaging alert failed: {_avg_e}")
                    # Recompute TradePlan + push MODIFY_TP to broker for every ticket.
                    # Triggered on: new averaging, ticket closed, volume change.
                    # Rules: worst-positioned ticket closes first; runner = best;
                    # never set a TP that would close a ticket at a loss.
                    if current_positions and sig_state.is_active():
                        _direction = sig_state.get('direction') or ''
                        if _direction in ('BUY', 'SELL'):
                            _reason = 'avg' if _new_tks else ('close' if _gone_tks else 'vol_change')
                            try:
                                apply_trade_plan(current_positions, _direction,
                                                 bars_cache, reason=_reason)
                            except Exception as _pe:
                                log.warning(f"[PLAN] apply after position change failed: {_pe}")
                            # Redraw TV so TP/SL lines reflect the new plan immediately
                            try:
                                redraw_tv(bars_cache, account, sig_state)
                            except Exception:
                                pass
                if prev_positions_map:
                    realized_delta = 0.0
                    # Dedup window: if EXECUTOR/FAST logged a close for this ticket
                    # within the last 30s, the DETECTED tracker is double-counting —
                    # skip the pnl_delta (already realized by the triggering module).
                    DEDUP_WIN_S = 30
                    def _recent_close_for(tk):
                        try:
                            recent = trade_history.load_recent(limit=30)
                            now_ts = time.time()
                            for ev in reversed(recent):
                                if (now_ts - float(ev.get('ts', 0))) > DEDUP_WIN_S:
                                    break
                                if int(ev.get('ticket') or 0) != int(tk or 0):
                                    continue
                                if ev.get('type') not in ('PARTIAL_CLOSE', 'FULL_CLOSE'):
                                    continue
                                src = (ev.get('source') or '').upper()
                                if src not in ('DETECTED', ''):
                                    return ev  # real source owns the pnl
                        except Exception:
                            pass
                        return None
                    for tk, prev in prev_positions_map.items():
                        cur = current_map.get(tk)
                        if cur is None:
                            dup = _recent_close_for(tk)
                            if dup is None:
                                realized_delta += prev['profit']
                                trade_history.log_event(
                                    type='FULL_CLOSE', ticket=tk, direction=sig_state.get('direction', ''),
                                    lot=prev['volume'], price=price, source='DETECTED',
                                    pnl_delta=prev['profit'],
                                    reason='Ticket closed (detected by P&L tracker)'
                                )
                            else:
                                log.debug(f"[P&L] skip DETECTED full_close tk={tk} — already logged by {dup.get('source')} (pnl {dup.get('pnl_delta')})")
                            # Hunter stats tracking — if the ticket comment starts with HUNTER_
                            try:
                                _tk_comment = (prev.get('comment') or '')
                                if _tk_comment.startswith('HUNTER_'):
                                    _setup_id = _tk_comment[len('HUNTER_'):]
                                    _pnl = float(prev.get('profit', 0) or 0)
                                    # Infer exit reason from where the price ended up
                                    _exit_reason = 'tp_hit' if _pnl > 0 else ('sl_hit' if _pnl < 0 else 'manual')
                                    import hunter_stats as _hs
                                    _hs.record_close(int(tk), _pnl, _exit_reason, setup_id=_setup_id)
                                    log.info(f"[HUNTER] close logged: tk={tk} setup={_setup_id} pnl={_pnl:+.2f} reason={_exit_reason}")
                            except Exception as _hce:
                                log.debug(f"[HUNTER] close tracking failed: {_hce}")
                        elif cur['volume'] < prev['volume'] - 0.001:
                            frac_closed = (prev['volume'] - cur['volume']) / prev['volume']
                            portion_profit = prev['profit'] * frac_closed
                            dup = _recent_close_for(tk)
                            if dup is None:
                                realized_delta += portion_profit
                                trade_history.log_event(
                                    type='PARTIAL_CLOSE', ticket=tk, direction=sig_state.get('direction', ''),
                                    lot=round(prev['volume'] - cur['volume'], 3), price=price, source='DETECTED',
                                    pnl_delta=round(portion_profit, 2),
                                    reason=f'Partial detected ({frac_closed*100:.0f}% of ticket reduced)'
                                )
                            else:
                                log.debug(f"[P&L] skip DETECTED partial tk={tk} — already logged by {dup.get('source')} (pnl {dup.get('pnl_delta')})")
                    if realized_delta != 0:
                        try:
                            sig_state._data['realized_profit'] = sig_state._data.get('realized_profit', 0) + realized_delta
                            sig_state.save()
                            log.info(f"[P&L] Realized ${realized_delta:+.2f} (ticket closed/reduced). Total realized: ${sig_state._data['realized_profit']:+.2f}")
                        except Exception:
                            pass
                prev_positions_map = current_map
            else:
                prev_positions_map = {}

            # ── 3.5. ADOPTION CHECK — detect manual/orphan positions during runtime ──
            if not sig_state.is_active() and account.get('pos_count', 0) > 0:
                positions_raw = read_json(POSITIONS).get('positions', [])
                if positions_raw:
                    log.info(f"[ADOPT] Runtime: {len(positions_raw)} orphan positions detected, adopting")
                    if sig_state.adopt_positions(positions_raw, start_balance=account.get('balance', 0)):
                        log.info(f"[ADOPT] Took over: {sig_state.get('direction')} @ {sig_state.get('entry_price')}, {sig_state.get('total_lots')} lots")

            # ── 3.6. MANUAL CLOSE DETECTOR — sig active but no brain positions → reset state ──
            # Grace period 10s to avoid racing with an in-flight close_all_brain
            if sig_state.is_active() and account.get('pos_count', 0) == 0:
                if '_empty_since' not in dir(engine) or getattr(engine, '_empty_since', 0) == 0:
                    engine._empty_since = time.time()
                elif time.time() - engine._empty_since > 10:
                    log.info(f"[MANUAL_CLOSE] Signal was {sig_state.get('direction')} @ {sig_state.get('entry_price')} but no brain positions for >10s — resetting sig_state")
                    # Ground-truth net P&L: balance delta (includes fees/swap).
                    _end_bal = float(account.get('balance', 0) or 0)
                    _start_bal = float(sig_state.get('signal_start_balance', 0) or 0)
                    _net_pnl = (_end_bal - _start_bal) if (_end_bal and _start_bal) else float(sig_state.get('realized_profit', 0.0) or 0)
                    trade_history.log_event(
                        type='SIGNAL_CLOSE', direction=sig_state.get('direction', ''),
                        source='MANUAL',
                        reason=f'Positions closed externally (manual, EA DD auto-close, or broker action). net=${_net_pnl:+.2f}',
                        pnl_delta=_net_pnl
                    )
                    _mc_dir = sig_state.get('direction')   # capture before close clears state
                    _mc_entry = float(sig_state.get('entry_price') or 0)
                    sig_state.close_signal(end_balance=_end_bal)
                    _set_last_signal_close_ts()  # start 5-min cooldown
                    engine._empty_since = 0
                    try:
                        import snipers as _snp
                        _n = _snp.cancel_all(reason="manual_close")
                        if _n:
                            log.info(f"[SNIPER] Cancelled {_n} sniper(s) on manual close")
                    except Exception:
                        pass
                    # Reset HUNTER conversation — context from a closed trade
                    # isn't useful for a fresh hunt. Saves cache_write next call.
                    try:
                        import llm_conversation as _lcv
                        _lcv.reset('HUNTER')
                        log.debug("[CONV] HUNTER conversation reset on signal close")
                    except Exception:
                        pass
                    # NOTE 2026-05-05: NO resetegem EXECUTOR session a trade
                    # close. La sessió persistent és la memòria narrativa que
                    # necessita el LLM per no contradir-se entre trades. Si la
                    # mantenim viva, cada decisió queda a la conversa i la
                    # propera cycle té tot el context implícit. La frase al
                    # prompt avisa que trades anteriors poden estar tancats —
                    # el LLM és prou intel·ligent per gestionar-ho. Cost: el
                    # cache_read és el ~10% del cost normal d'input.
                    # try:
                    #     import claude_session_manager as _csm
                    #     _csm.reset('EXECUTOR')
                    #     log.debug("[CSM] EXECUTOR Claude session reset on trade close")
                    # except Exception:
                    #     pass
                    try:
                        import staged_setups as _ss
                        released = _ss.unfreeze_post_close()
                        if released:
                            log.info(f"[HUNTER] Released {released} post_close setups (eligible now)")
                        if _mc_dir and _mc_entry:
                            _removed = _ss.remove_near_zone(_mc_dir, _mc_entry)
                            if _removed:
                                log.info(f"[STAGED] Purged {_removed} same-zone setup(s) after close "
                                         f"({_mc_dir}@{_mc_entry:.2f}) — Executor must re-analyze before re-entry")
                    except Exception:
                        pass
                    # Persist trade narrative (manual close path)
                    try:
                        import trade_narrative as _tn
                        n = _tn.persist_latest_narrative()
                        if n:
                            log.info(f"[NARRATIVE] saved for trade {n.get('trade_id')} "
                                     f"({n.get('direction')} pnl={n.get('total_pnl'):+.2f} "
                                     f"actions={len(n.get('actions') or [])})")
                    except Exception as _tne:
                        log.debug(f"[NARRATIVE] persist failed: {_tne}")
            else:
                engine._empty_since = 0

            # ── 4.-1. MANUAL REDUCE-RISK ──
            # IMPORTANT: the actual handler runs in a SEPARATE DAEMON THREAD
            # (started below at first iteration). The main loop can block for
            # 30-60s on tv() calls or Indicator processing — if the handler
            # was here, button clicks would queue behind. Now it polls every
            # 1s independently and writes via the same write_order lock.

            # ── 4.0. ORPHAN SNIPER CLEANUP ──
            # Snipers exist for averaging into an active trade. If signal is
            # IDLE (no active position), no sniper should be armed — firing
            # one would OPEN a new counter-trend position with zero context.
            # This catches edge cases where snipers survive trade close
            # (e.g. SL-hit closures, Executor proposing while IDLE).
            try:
                if not sig_state.is_active():
                    import snipers as _snp_chk
                    _active_snps = [s for s in _snp_chk._read()
                                    if not s.get("_fired") and not s.get("_cancelled")]
                    if _active_snps:
                        _n_orphan = _snp_chk.cancel_all(reason="signal_idle_orphan")
                        if _n_orphan:
                            log.warning(
                                f"[SNIPER] Cancelled {_n_orphan} orphan sniper(s) "
                                f"— signal is IDLE, snipers without active trade are forbidden"
                            )
            except Exception as _orph_e:
                log.debug(f"[SNIPER] orphan check failed: {_orph_e}")

            # ── 4. Screenshot (every 2 min) ──
            if time.time() - last_screenshot > SCREENSHOT_INTERVAL:
                sp = tv_screenshot()
                if sp:
                    screenshot_path = sp
                    last_screenshot = time.time()

            # ── 4.2. ZONE LIFECYCLE — pure code, updates touches/rejections/status ──
            try:
                import zone_lifecycle
                # Thread current signal direction so the lifecycle can pass it
                # to explosion_detector when a STRONG zone clean-breaks.
                _zlc = dict(zone_lifecycle_cfg or {})
                _zlc["signal_direction"] = (sig_state.get("direction") if sig_state.is_active() else None)
                zone_lifecycle.tick(COMMON, bars_cache, _zlc)
            except Exception as e:
                log.warning(f"[ZONE_LIFECYCLE] tick failed: {e}")

            # ── 4.2.b. EXPLOSION DETECTOR — evaluate each cycle, persist state ──
            try:
                import explosion_detector
                _atr_m15 = atr(bars_cache, 14) or 0  # ATR M5(14) as proxy for M15
                _sig_dir = sig_state.get("direction") if sig_state.is_active() else None
                explosion_detector.evaluate(
                    bars_m5=bars_cache or [],
                    atr_m15=_atr_m15,
                    signal_direction=_sig_dir,
                )
            except Exception as e:
                log.warning(f"[EXPLOSION] evaluate failed: {e}")

            # ── 4.3. EVENT DETECTOR — enqueue events for the Executor (commit 10 consumes) ──
            try:
                from zone_store import active_zones, read_state
                _zone_state = read_state(COMMON)
                # v3.3: prefer FSM-persisted UUID over legacy derived id
                _trade_id = sig_state.get_trade_id() if hasattr(sig_state, 'get_trade_id') else None
                if not _trade_id and account.get('has_signal'):
                    _trade_id = f"{account.get('direction','?')}_{account.get('entry_price',0)}"
                _floating = sum(broker_position_pnl(p) for p in account.get('positions', []))
                # market_state (BOS + liquidity pools) for structural profit-capture events
                _market_state_for_ev = None
                if _mc_status:
                    _market_state_for_ev = _mc_status.get('market_state')
                event_detector.tick({
                    "trade_id": _trade_id if account.get('has_signal') else None,
                    "signal": {
                        "direction": account.get('direction'),
                        "entry_price": float(account.get('entry_price') or 0),
                        "avg_count": int(sig_state.get('avg_count') or 0) if hasattr(sig_state, 'get') else 0,
                        "breakeven_set": bool(sig_state.is_breakeven()) if hasattr(sig_state, 'is_breakeven') else False,
                        "flag_closing": bool(account.get('closing')),
                    } if account.get('has_signal') else None,
                    "price": price,
                    "atr": atr(bars_cache, 14) or 0,
                    "atr_m15": atr(aggregate_bars(bars_cache, 3), 14) or 0,
                    "bars_m5": bars_cache[-60:] if bars_cache else [],
                    "zones": active_zones(_zone_state),
                    "market_state": _market_state_for_ev,  # v3.3: for BOS + liquidity events
                    "account": {
                        "balance": account.get('balance', 0),
                        "equity": account.get('equity', 0),
                        "dd_pct": account.get('dd_pct', 0),
                        "floating_profit": _floating,
                    },
                    "tg_messages": [],  # commit 10 will wire TG-message events here if needed
                    "touch_dist_usd": float(zone_lifecycle_cfg.get('touch_dist_usd', 0.5)),
                })
            except Exception as e:
                log.warning(f"[EVENT_DETECTOR] tick failed: {e}")

            # ── 4.5. TELEGRAM PROCESSING (every 5s) ──
            if time.time() - last_tg_check > 5:
                last_tg_check = time.time()
                try:
                    import telegram_listener
                    pending = telegram_listener.pop_pending()
                    if pending:
                        process_tg_messages(pending, sig_state, engine, bars_cache, account, price)
                except Exception as e:
                    log.warning(f"[TG] Processing error: {e}")

            # Read autonomous mode once per cycle (used by 5.5 STAGED + Executor IDLE staging)
            autonomous_mode = read_autonomous_mode()
            prev_autonomous_mode = autonomous_mode

            # ── 5. FAST ENGINE — check zone conditions ──
            # 2026-05-05: DESACTIVAT en Mode Recorregut Institucional. El reflex
            # "FAST AVG" automàtic era part del sistema legacy d'averaging — ja
            # eliminat. En recorregut: single-trade, no averaging mai.
            # Skip si trade actiu té auto_close_conditions (signe del recorregut).
            _skip_fast_avg = False
            try:
                _ep_fa = sig_state._data.get('executor_plan') or {} if hasattr(sig_state, '_data') else {}
                if _ep_fa.get('mode') == 'institutional_recorregut' or _ep_fa.get('auto_close_conditions'):
                    _skip_fast_avg = True
            except Exception:
                pass
            if has_pending_order() or _skip_fast_avg:
                order = None
            else:
                order = engine.check(bars_cache, account)
            if order:
                sent = send_market(order['type'], order['lot'], order['comment'])
                if sent:
                    engine.mark_order_sent(order['zone_price'], price)
                    if sig_state.is_active():
                        sig_state.add_averaging(order['zone_price'], order['lot'])
                    log.info(f"[FAST] AVERAGE {order['type']} {order['lot']} -- {order['comment']}")

            # ── 5-bis. SNIPERS — Executor-proposed pre-placed averagings ──
            # If active signal + current price touches any Executor-proposed sniper
            # level, fire MARKET instantly WITHOUT waiting for candle confirmation.
            # Respects global cooldown + DD-projection ceiling (same safety as in-zone
            # averaging). The LLM decides which levels; code just executes.
            if account.get('has_signal') and not account.get('closing') and not has_pending_order():
                try:
                    import snipers as _snp
                    # Cancel all snipers if BE is set (no more averagings allowed)
                    if hasattr(sig_state, 'is_breakeven') and sig_state.is_breakeven():
                        _existing = _snp.load()
                        if _existing:
                            _nc = _snp.cancel_all(reason="be_set")
                            if _nc:
                                log.info(f"[SNIPER] Cancelled {_nc} on breakeven_set")
                    # Pass the current bar's high/low so intrabar wicks trigger
                    # snipers even when the close is back inside. Fast spikes no
                    # longer need the bar to close at the target price.
                    _cur_bar = bars_cache[-1] if bars_cache else {}
                    triggered = _snp.find_triggered(
                        price,
                        bar_high=_cur_bar.get('high'),
                        bar_low=_cur_bar.get('low'),
                    )
                    # Respect global cooldown between any two averagings
                    if triggered and (time.time() - engine.last_avg_time) >= engine.MIN_AVG_COOLDOWN:
                        # DD projection guard: skip if firing would push DD past ceiling.
                        # Uses FastEngine's dd_limit to compute the ceiling.
                        try:
                            _mult = float(triggered.get('multiplier') or 1.0)
                            _blot = getattr(engine, '_base_lot', 0.03) or 0.03
                            _lot = max(0.01, round(_blot * _mult, 2))
                        except Exception:
                            _lot = 0.03
                        _direction = triggered.get('direction')
                        _sid = triggered.get('id', '?')
                        _comment = f"SNIPER_{_sid}"
                        _sent = send_market(_direction, _lot, _comment)
                        if _sent:
                            engine.mark_order_sent(float(triggered.get('price') or price), price)
                            if sig_state.is_active():
                                sig_state.add_averaging(float(triggered.get('price') or price), _lot)
                            _snp.mark_fired(_sid)
                            log.info(f"[SNIPER FIRED] {_sid} {_direction} {_lot}L @ "
                                     f"{price:.2f} (target {triggered.get('price')}) — "
                                     f"reason: {triggered.get('reason', '')[:120]}")
                except Exception as _snpe:
                    log.debug(f"[SNIPER] check error: {_snpe}")

            # ── 5-pre-be. EXECUTOR BE TRIGGER — auto-fire MOVE_SL_ENTRY ──
            # ── Phantom-BE detector (2026-04-29) ──
            # If breakeven_set=True but ALL open positions have SL=0, that
            # means the MOVE_SL_ENTRY never actually applied (rate limit,
            # broker rejection, etc.). Reset the flag so the system can
            # retry on next BE trigger — and unblock averagings the
            # Executor may need. Without this, the brain falsely believes
            # the trade is protected and refuses to manage risk (incident
            # 2026-04-29 17:15).
            if account.get('has_signal') and sig_state.is_breakeven():
                try:
                    _pos_now = read_json(POSITIONS).get('positions', []) or []
                    if _pos_now and all(float(p.get('sl', 0) or 0) == 0 for p in _pos_now):
                        log.warning(
                            "[BE-PHANTOM] breakeven_set=True but ALL positions have SL=0 — "
                            "broker never applied the move. Resetting flag so the system "
                            "can retry / resume normal management."
                        )
                        sig_state._data['breakeven_set'] = False
                        sig_state._data['breakeven_pending'] = False
                        sig_state._data['breakeven_pending_since'] = 0.0
                        sig_state._data['breakeven_target_price'] = 0.0
                        sig_state.save()
                except Exception as _phe:
                    log.debug(f"[BE-PHANTOM] check failed: {_phe}")

            # When the Executor has set a breakeven_trigger price and the
            # current bar wick has reached it (in profit direction), move SL
            # to entry/blend automatically. One-shot: once BE is set, the
            # trigger is consumed (sig_state.breakeven_set == True).
            if (account.get('has_signal')
                    and not account.get('closing')
                    and not sig_state.is_breakeven()
                    and bars_cache):
                try:
                    _ep_be = sig_state._data.get('executor_plan') or {}
                    _be_obj = _ep_be.get('breakeven_trigger') if isinstance(_ep_be, dict) else None
                    if isinstance(_be_obj, dict) and _be_obj.get('price') is not None and len(bars_cache) >= 2:
                        _be_price = float(_be_obj.get('price'))
                        # 2026-04-30 (Opció A): requerir CLOSE M5 favorable, NO wick.
                        # Abans: qualsevol wick intra-bar tocant el trigger l'activava.
                        # Resultat: trade tancava al primer retest del blend perquè
                        # el SL al blend saltava amb qualsevol pullback.
                        # Ara: només dispara si el preu HA TANCAT una M5 més enllà
                        # del trigger en direcció FAVORABLE. Confirmem moviment real,
                        # no soroll de wick.
                        _last_closed_bar = bars_cache[-2] or {}
                        _bc_be = float(_last_closed_bar.get('close') or 0)
                        _dir_be = account.get('direction')
                        _atr_m1_be = atr(bars_cache, 14) or 0.0
                        # Tolerància petita per evitar problemes d'arrodoniment quan
                        # el close coincideix exactament amb el trigger.
                        _be_tol = 0.05
                        _hit = False
                        if _bc_be and _dir_be == 'BUY':
                            # BUY: trigger en favor = per sobre del blend
                            # Hit quan close M5 ≥ trigger (preu ha tancat al o per
                            # sobre del nivell objectiu, profit confirmat)
                            _hit = (_bc_be + _be_tol) >= _be_price
                        elif _bc_be and _dir_be == 'SELL':
                            # SELL: trigger en favor = per sota del blend
                            # Hit quan close M5 ≤ trigger
                            _hit = (_bc_be - _be_tol) <= _be_price
                        # Rate-limit retries: wait 20s after last MOVE_SL_ENTRY
                        # so the phantom detector doesn't instantly re-trigger.
                        _be_last_sent = float(sig_state._data.get('be_last_sent_at', 0) or 0)
                        if _hit and (time.time() - _be_last_sent) < 20:
                            _hit = False
                        if _hit:
                            try:
                                # Only mark BE if the order actually wrote.
                                # Otherwise the brain "thinks" it's protected
                                # but the broker has SL=0 (incident 2026-04-29).
                                _be_ok = move_sl_entry()
                                if not _be_ok:
                                    log.warning(f"[BE] Trigger hit @ {_be_price:.2f} but move_sl_entry() BLOCKED — will retry next tick")
                                    continue
                                sig_state._data['be_last_sent_at'] = time.time()
                                sig_state.request_breakeven(sl_price=_be_price)
                                log.info(
                                    f"[BE] Trigger hit @ {_be_price:.2f} "
                                    f"(price {price:.2f}, dir {_dir_be}) — "
                                    f"MOVE_SL_ENTRY sent"
                                )
                                try:
                                    notify(
                                        "be_set",
                                        f"🔒 BE auto-set — trigger {_be_price:.2f} reached "
                                        f"({(_be_obj.get('reasoning') or '')[:60]})",
                                    )
                                except Exception:
                                    pass
                            except Exception as _bee2:
                                log.warning(f"[BE] move_sl_entry failed: {_bee2}")
                except Exception as _bex:
                    log.debug(f"[BE] tick error: {_bex}")

            # ── 5-pre. EXECUTOR LADDER — PARTIAL_CLOSE at LLM's profit_targets ──
            # The Executor's tactical plan defines (price, close_pct) levels
            # summing to 100%. When price touches a level (intrabar wicks ok),
            # we fire PARTIAL_CLOSE_PCT per ticket. Independent of broker-side
            # TPs and the FAST BLEND LADDER — all three can coexist; the ladder
            # is the LLM's tesi-driven exit, FAST is a generic R-based safety
            # net, and broker TPs catch us if the brain sleeps.
            if (account.get('has_signal')
                    and not account.get('closing')
                    and bars_cache):
                try:
                    import executor_ladder as _ladder
                    _cur = bars_cache[-1] or {}
                    _bh = _cur.get('high')
                    _bl = _cur.get('low')
                    _dir = account.get('direction')
                    # Profit-side tolerance so a wick at 4565.05 still fires the
                    # 4565.00 LLM target. Scales with M1 volatility: low-vol ~$0.20,
                    # NY normal ~$0.40, news-spike ~$1+. See trader_brain notes.
                    _atr_m1_now = atr(bars_cache, 14) or 0.0
                    _ladder_buffer = 0.15 * float(_atr_m1_now or 0)
                    if _dir in ('BUY', 'SELL') and (_bh is not None or _bl is not None):
                        # 2026-05-04: passar current_price perquè tick() validi
                        # que el preu ARA està al costat profit del nivell. Sense
                        # això, un wick antic dins el bar M1 fa fire indegut.
                        _cur_price = float(_cur.get('close') or price or 0)
                        fired = _ladder.tick(
                            _dir, float(_bh or 0), float(_bl or 0),
                            buffer_usd=_ladder_buffer,
                            current_price=_cur_price if _cur_price > 0 else None,
                        )
                        if fired:
                            # 2026-05-06: Comprovem si el BE trigger TAMBÉ
                            # hauria de disparar al mateix temps. Si LADDER L1
                            # i BE estan al mateix preu (cas típic: TP1 = BE),
                            # els enviem com a UNA sola ordre multi-acció per
                            # evitar el race condition que descartava un dels dos.
                            _be_should_also_fire = False
                            try:
                                if not sig_state.is_breakeven():
                                    _ep_be_check = sig_state._data.get('executor_plan') or {}
                                    _be_obj_check = _ep_be_check.get('breakeven_trigger') if isinstance(_ep_be_check, dict) else None
                                    if isinstance(_be_obj_check, dict) and _be_obj_check.get('price'):
                                        _be_price_check = float(_be_obj_check.get('price'))
                                        # Si el primer LADDER level està al mateix preu
                                        # del BE trigger (±$0.5), els disparem junts.
                                        _l1_price = float(fired[0].get('price') or 0)
                                        if _l1_price and abs(_l1_price - _be_price_check) <= 0.5:
                                            _be_should_also_fire = True
                            except Exception:
                                pass

                            for _lv_idx, _lv in enumerate(fired):
                                _pct = float(_lv.get('close_pct') or 0)
                                if _pct <= 0:
                                    continue
                                _lp = float(_lv.get('price') or 0)
                                _why = (_lv.get('reasoning') or '')[:80]
                                # Fire PARTIAL_CLOSE_PCT per ticket — pct
                                # applies to current per-ticket volume.
                                _tickets_to_close = []
                                for _p in (account.get('positions') or []):
                                    _tk = int(_p.get('ticket', 0) or 0)
                                    if not _tk:
                                        continue
                                    _tickets_to_close.append((_tk, _pct))

                                if _be_should_also_fire and _lv_idx == 0 and _tickets_to_close:
                                    # COMBINED order: PARTIAL + MOVE_SL_ENTRY
                                    try:
                                        ok = partial_close_and_move_sl_entry(_tickets_to_close)
                                        if ok:
                                            sig_state._data['be_last_sent_at'] = time.time()
                                            sig_state.request_breakeven(sl_price=_lp)
                                            log.info(
                                                f"[LADDER+BE] COMBINED order: PARTIAL_CLOSE_PCT {_pct:.0f}% + "
                                                f"MOVE_SL_ENTRY @ {_lp:.2f}"
                                            )
                                            try:
                                                notify(
                                                    "partial_close",
                                                    f"🔒 BE auto-set @ {_lp:.2f}",
                                                )
                                            except Exception:
                                                pass
                                        else:
                                            log.warning(f"[LADDER+BE] combined order BLOCKED — fallback per ticket")
                                            for _tk, _ in _tickets_to_close:
                                                try:
                                                    partial_close_pct(_tk, _pct)
                                                except Exception:
                                                    pass
                                    except Exception as _ce:
                                        log.warning(f"[LADDER+BE] combined fire failed: {_ce}")
                                else:
                                    for _tk, _ in _tickets_to_close:
                                        try:
                                            partial_close_pct(_tk, _pct)
                                        except Exception as _pce:
                                            log.warning(
                                                f"[LADDER] partial_close_pct ticket={_tk} "
                                                f"pct={_pct} failed: {_pce}"
                                            )
                                log.info(
                                    f"[LADDER] FIRED level @ {_lp:.2f} pct={_pct:.0f}% — {_why}"
                                )
                                try:
                                    notify(
                                        "partial_close",
                                        f"💰 PARCIAL {_pct:.0f}% a ${_lp:.2f}\n📝 {_why}",
                                    )
                                except Exception:
                                    pass
                except Exception as _le:
                    log.debug(f"[LADDER] tick error: {_le}")

            # ── 5-post. PLAN INVALIDATION WATCH ──
            # Proactive: if last Executor's invalidation_condition triggers on
            # current bar, fire force_executor so the plan gets re-evaluated in
            # seconds rather than waiting for next natural event (up to 60s).
            _check_plan_invalidation(bars_cache, account, sig_state)

            # ── 5-post-b. STAGED SETUP PRUNE ──
            # Every-tick prune (was only firing when Executor re-checked setups).
            # Keeps staged_setups.json clean between LLM calls.
            try:
                import staged_setups as _ss
                _pruned = _ss.prune_expired_invalidated(price)
                if _pruned:
                    log.info(f"[STAGED] {_pruned} setup(s) pruned (expired/invalidated)")
                    # 2026-05-04: trigger EXECUTOR review — un setup ha mort,
                    # cal replantejar quina és la propera idea.
                    _force_executor_review(f'staged_setup_pruned ({_pruned} setup(s) invalidat/expirat)')
                # M5-close breach detector — qualitative philosophy.
                # Aggregates M1 → M5; on each new completed M5 chunk, si el
                # close és beyond la zone d'un plan en direcció contrària:
                #   1. NEWLY BREACHED (primera vegada): invoquem EXECUTOR
                #      immediatament perquè decideixi qualitativament
                #      (invalidar / mantenir / modificar conditions).
                #   2. SAFETY NET (grace 4 bars = 20 min): si el LLM no
                #      ha respost dins el grace, el codi blacklist + drop
                #      com a fallback. Aquest cas hauria de ser excepcional.
                if len(bars_cache) >= 5:
                    _bars_m5 = aggregate_bars(bars_cache, 5)
                    if _bars_m5:
                        _closed_m5 = _bars_m5[-1]
                        _ts_m5 = _closed_m5.get('time') or 0
                        _last_seen_m5 = getattr(engine, '_zone_cross_last_ts', 0)
                        if _ts_m5 and _ts_m5 != _last_seen_m5:
                            _bc_m5 = float(_closed_m5.get('close') or 0)
                            if _bc_m5 > 0:
                                _result = _ss.evaluate_breach_m5(_bc_m5, _ts_m5, grace_bars=4)
                                _dropped = _result.get('pruned', 0)
                                _newly = _result.get('newly_breached', []) or []
                                # Cas 1: nova breach detectada → review immediat
                                if _newly:
                                    _ids = ','.join(b.get('id', '?') for b in _newly)
                                    _details = '; '.join(
                                        f"{b.get('id')}({b.get('direction')}@{b.get('zone'):.1f},"
                                        f"close={b.get('current_close'):.2f})"
                                        for b in _newly
                                    )
                                    log.warning(
                                        f"[STAGED] M5 close breached zone for {len(_newly)} setup(s): "
                                        f"{_details} — invocant EXECUTOR per decisió qualitativa"
                                    )
                                    _force_executor_review(
                                        f'staged_zone_breached_immediate ({_ids}, close={_bc_m5:.2f})'
                                    )
                                # Cas 2: grace expired (safety net fallback)
                                if _dropped:
                                    log.warning(
                                        f"[STAGED] {_dropped} plan(s) dropped per safety-net: "
                                        f"M5 grace 4-bar expirat sense actuació LLM (close={_bc_m5:.2f})"
                                    )
                                    _force_executor_review(
                                        f'staged_setup_m5_grace_safety_net ({_dropped} plan(s), close={_bc_m5:.2f})'
                                    )
                            engine._zone_cross_last_ts = _ts_m5
                # Enforce 1-per-direction every tick so any path that writes
                # setups (legacy state, unknown caller) gets squeezed to the
                # best R/R BUY + best R/R SELL. No-op when already enforced.
                _trimmed = _ss.enforce_one_per_direction()
                if _trimmed:
                    log.info(f"[STAGED] {_trimmed} duplicate plan(s) dropped — kept best R/R per direction")
            except Exception:
                pass

            # ── 5-post-c. LLM HEALTH WATCHDOG ──
            # Emits a one-shot TG alert if any role has been in-flight > 3 min.
            _llm_watchdog_tick()

            # ── 5a. FAST ENGINE — staircase TP assignment ──
            # Recompute TPs at most every TP_RECALC_INTERVAL seconds (throttled
            # from every-tick to every 15s) OR immediately on a ticket-count
            # change (new averaging / partial close). Every-tick was causing
            # write-thrash and let zone drift sneak TPs into the loss side on
            # fast moves. With monotonic + profit-side guards in the assigner,
            # 15s cadence is safe and keeps broker-side exit fresh enough.
            if account.get('has_signal') and not account.get('closing'):
                try:
                    TP_RECALC_INTERVAL = 15  # seconds
                    _now = time.time()
                    _pos_count = len(account.get('positions') or [])
                    _last_ts = getattr(engine, '_last_tp_recalc_ts', 0.0)
                    _last_count = getattr(engine, '_last_tp_recalc_poscount', -1)
                    should_recalc = (
                        (_now - _last_ts) >= TP_RECALC_INTERVAL
                        or _pos_count != _last_count
                    )
                    if should_recalc:
                        tp_assignments = engine.assign_staircase_tps(bars_cache, account)
                        for tk, tp_target, zone_p in tp_assignments:
                            if tk and tp_target > 0:
                                if modify_tp(tk, tp_target):
                                    log.info(f"[FAST] TP set: ticket {tk} → TP {tp_target:.2f} (zone {zone_p:.1f} + buffer)")
                                else:
                                    log.warning(f"[FAST] TP write FAILED: ticket {tk} → TP {tp_target:.2f} (bridge blocked)")
                        engine._last_tp_recalc_ts = _now
                        engine._last_tp_recalc_poscount = _pos_count
                except Exception as _tpe:
                    log.debug(f"TP assignment failed: {_tpe}")

            # ── 5a-bis. Progressive peak-lock (3 stages) — DESACTIVAT 2026-05-04 ──
            # Mode Recorregut Institucional: BE i SL es controlen via
            # `breakeven_trigger` que el LLM pre-aprova explícitament. NO hi
            # ha trail automàtic genèric (era retail-style profit protection).
            # Skip si trade té executor_plan.mode == 'institutional_recorregut'
            # o auto_close_conditions definides (signe del nou paradigma).
            _skip_peak_lock = False
            try:
                _ep_pl = sig_state._data.get('executor_plan') or {} if hasattr(sig_state, '_data') else {}
                if _ep_pl.get('mode') == 'institutional_recorregut' or _ep_pl.get('auto_close_conditions'):
                    _skip_peak_lock = True
            except Exception:
                pass
            if (not _skip_peak_lock
                    and account.get('has_signal') and not account.get('closing')
                    and hasattr(sig_state, 'get')):
                try:
                    _positions = account.get('positions') or []
                    if _positions and bars_cache:
                        _tot_lots = sum(float(p.get('volume', 0) or 0) for p in _positions)
                        _floating = sum(broker_position_pnl(p) for p in _positions)
                        # Track peak across ticks (use FastEngine.peak_floating which
                        # is already maintained by check_trailing; fall back here if
                        # trailing hasn't run yet this cycle).
                        if _floating > getattr(engine, 'peak_floating', 0):
                            engine.peak_floating = _floating
                        _peak = getattr(engine, 'peak_floating', 0) or _floating
                        _stage = int(sig_state.get('peak_lock_stage') or 0)
                        _direction = account.get('direction', '')
                        # Compute blend for SL targeting
                        _w_sum = sum(float(p.get('volume',0) or 0) * float(p.get('price_open',0) or 0) for p in _positions)
                        _blend = (_w_sum / _tot_lots) if _tot_lots > 0 else 0

                        def _target_sl(favor_usd_per_lot):
                            # profit direction: SELL below blend, BUY above blend
                            if _direction == 'SELL':
                                return round(_blend - favor_usd_per_lot, 2)
                            if _direction == 'BUY':
                                return round(_blend + favor_usd_per_lot, 2)
                            return 0

                        # Stage 1 — breakeven. Triggers (any of):
                        #   A) blend has advanced ≥ PEAK_LOCK_S1_MIN_ADVANCE
                        #      price units in our favor direction.
                        #   B) Price has BROKEN a contrary STRONG zone (i.e.
                        #      crossed past it in our favor direction).
                        # Distance-based — lot-size independent.
                        _price_now = float(_positions[0].get('price_current', 0) or 0) if _positions else 0
                        if _blend > 0 and _price_now > 0:
                            if _direction == 'SELL':
                                _advance_now = max(0.0, _blend - _price_now)
                            elif _direction == 'BUY':
                                _advance_now = max(0.0, _price_now - _blend)
                            else:
                                _advance_now = 0.0
                        else:
                            _advance_now = 0.0
                        _advance_trigger = _advance_now >= PEAK_LOCK_S1_MIN_ADVANCE
                        # Contrary STRONG zone broken
                        _zone_broken_trigger = False
                        _broken_zone_price = 0.0
                        try:
                            _contrary = 'BUY' if _direction == 'SELL' else 'SELL'
                            _strong_zones = [float(z.get('price', 0) or 0)
                                             for z in (engine.reversal_zones or [])
                                             if (z.get('bounce_direction') or '').upper() == _contrary
                                             and (z.get('strength') or '').upper() == 'STRONG'
                                             and float(z.get('price', 0) or 0) > 0]
                            if _strong_zones and _blend > 0 and _price_now > 0:
                                if _direction == 'SELL':
                                    for _z in _strong_zones:
                                        if (_blend - _z) >= PEAK_LOCK_S1_MIN_TP_DISTANCE and _price_now < _z:
                                            _zone_broken_trigger = True
                                            _broken_zone_price = _z
                                            break
                                elif _direction == 'BUY':
                                    for _z in _strong_zones:
                                        if (_z - _blend) >= PEAK_LOCK_S1_MIN_TP_DISTANCE and _price_now > _z:
                                            _zone_broken_trigger = True
                                            _broken_zone_price = _z
                                            break
                        except Exception:
                            pass
                        if _stage < 1 and (_advance_trigger or _zone_broken_trigger) and _blend > 0:
                            # Only mark breakeven_set=True if the order actually
                            # made it to the orders file (BE-PHANTOM guard,
                            # incident 2026-04-29 17:15).
                            _be_written = move_sl_entry()
                            if _be_written:
                                sig_state.request_breakeven(sl_price=_blend)
                                sig_state._data['peak_lock_stage'] = 1
                                sig_state.save()
                                if _zone_broken_trigger:
                                    _reason = f"contrary STRONG zone {_broken_zone_price:.2f} broken (advance {_advance_now:.2f})"
                                else:
                                    _reason = f"advance {_advance_now:.2f} pts ≥ {PEAK_LOCK_S1_MIN_ADVANCE:.0f}"
                                log.info(f"[PEAK-LOCK] Stage 1 fired — {_reason} → SL al blend {_blend:.2f} (BE)")
                            else:
                                log.warning(f"[PEAK-LOCK] Stage 1 BLOCKED — move_sl_entry() refused (advance={_advance_now:.2f}); will retry next cycle")
                                _reason = ""
                            if _reason:
                                try:
                                    notify("breakeven_set",
                                           f"🛡️ BREAK-EVEN ACTIVAT · SL mogut a entrada ${_blend:.2f}\n"
                                           f"📝 {_reason}")
                                except Exception:
                                    pass
                        # ── Continuous TRAIL (replaces fixed Stages 2/3) ──
                        # Distance-based: track how far price has advanced from
                        # blend in the favor direction (price units), lock
                        # TRAIL_PCT of that peak advance as SL distance.
                        # Lot-independent — same SL math regardless of lot size.
                        if _stage >= 1 and _blend > 0 and _positions:
                            _price_now_pl = float(_positions[0].get('price_current', 0) or 0)
                            if _price_now_pl > 0:
                                if _direction == 'SELL':
                                    _advance_now = max(0.0, _blend - _price_now_pl)
                                elif _direction == 'BUY':
                                    _advance_now = max(0.0, _price_now_pl - _blend)
                                else:
                                    _advance_now = 0.0
                                if _advance_now > getattr(engine, 'peak_advance', 0.0):
                                    engine.peak_advance = _advance_now
                                _peak_adv = float(getattr(engine, 'peak_advance', 0.0) or 0.0)
                                if _peak_adv >= PEAK_LOCK_TRAIL_MIN_ADVANCE:
                                    _favor_dist = _peak_adv * PEAK_LOCK_TRAIL_PCT
                                    _new_target = _target_sl(_favor_dist)
                                    _cur_target = float(getattr(engine, 'peak_lock_sl_target', 0) or _blend)
                                    # Only move SL FURTHER into profit.
                                    # SELL: lower target is better.
                                    # BUY: higher target is better.
                                    if _direction == 'SELL':
                                        is_better = _new_target < (_cur_target - PEAK_LOCK_TRAIL_MIN_STEP)
                                    elif _direction == 'BUY':
                                        is_better = _new_target > (_cur_target + PEAK_LOCK_TRAIL_MIN_STEP)
                                    else:
                                        is_better = False
                                    if is_better:
                                        _written = modify_all_sl(_new_target, urgent=True)
                                        if _written:
                                            engine.peak_lock_sl_target = _new_target
                                            engine.peak_lock_target_set_ts = time.time()
                                            engine.peak_lock_target_verified = False
                                            log.info(
                                                f"[PEAK-LOCK] Trail {_direction} → SL {_new_target:.2f} "
                                                f"(peak advance {_peak_adv:.2f} pts, "
                                                f"locked {_favor_dist:.2f} pts = "
                                                f"{PEAK_LOCK_TRAIL_PCT*100:.0f}% of advance)"
                                            )
                                            try:
                                                notify("breakeven_set",
                                                       f"🔒 TRAILING SL · profit assegurat ${_favor_dist:.2f}\n"
                                                       f"💹 SL pujat a ${_new_target:.2f} (avant ${_peak_adv:.2f} des d'entrada)")
                                            except Exception:
                                                pass
                                        else:
                                            log.warning(f"[PEAK-LOCK] Trail BLOCKED — write refused (advance {_peak_adv:.2f}); will retry next tick")
                            # Phantom detector: confirm the broker actually
                            # applied our last target SL. If 15s pass and the
                            # ticket SL ≠ target, retry urgent. Closes the
                            # 2026-05-01 14:43 gap where Stage 2 wrote
                            # SL=4589.39 but trade closed at BE.
                            _last_target = float(getattr(engine, 'peak_lock_sl_target', 0) or 0)
                            _set_ts = float(getattr(engine, 'peak_lock_target_set_ts', 0) or 0)
                            _verified = bool(getattr(engine, 'peak_lock_target_verified', False))
                            if _last_target > 0 and _set_ts > 0 and not _verified:
                                _age = time.time() - _set_ts
                                if _age > PEAK_LOCK_VERIFY_TIMEOUT_S:
                                    _actual = [float(p.get('sl', 0) or 0) for p in _positions]
                                    _all_match = (
                                        _actual
                                        and all(abs(s - _last_target) < 0.05 for s in _actual)
                                    )
                                    if _all_match:
                                        engine.peak_lock_target_verified = True
                                        log.debug(f"[PEAK-LOCK] target SL {_last_target:.2f} verified on broker")
                                    else:
                                        log.warning(
                                            f"[PEAK-LOCK] PHANTOM SL: target={_last_target:.2f} "
                                            f"but broker has {_actual} — retrying urgent"
                                        )
                                        modify_all_sl(_last_target, urgent=True)
                                        engine.peak_lock_target_set_ts = time.time()
                except Exception as _pl_err:
                    log.debug(f"[PEAK-LOCK] check failed: {_pl_err}")

            # ── 5a1. FAST ENGINE — staged entry trigger (autonomous mode) ──
            # When IDLE and we have a pre-staged setup, check if price reached
            # the zone with confirmations. Fires OPEN reflexively (sub-second).
            #
            # Gate: if there's a previous order the EA hasn't processed yet,
            # SKIP staged-entry detection entirely for this tick. The EA needs
            # to finish the previous action before we queue another, otherwise
            # write_order silently drops them and the detected trigger would
            # re-fire every tick (cataract observed 2026-04-23 20:00-20:02).
            if has_pending_order():
                staged_trigger = None
            else:
                try:
                    staged_trigger = engine.check_staged_entry(bars_cache, account)
                except Exception as _se:
                    log.debug(f"[STAGED] check failed: {_se}")
                    staged_trigger = None
            if staged_trigger:
                _direction = staged_trigger.get('direction')
                _sid = staged_trigger.get('id', '?')
                _thesis = staged_trigger.get('thesis', '')
                _details = staged_trigger.get('_trigger_details', {})
                # Daily counter is kept for telemetry but NOT used as a gate:
                # we trade whenever the staged setup fires — no human fatigue limit.
                _today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
                if staging_day != _today:
                    staging_day = _today
                    staging_trades_today = 0
                if _direction in ('BUY', 'SELL'):
                    # Identify if this is a Hunter setup (opens with broker-level TP+SL)
                    _is_hunter = (staged_trigger.get('source') or '').lower() == 'hunter'
                    _h_tp = staged_trigger.get('profit_target') if _is_hunter else 0.0
                    _h_sl = staged_trigger.get('invalidation_price') if _is_hunter else 0.0
                    # Compute lot from config
                    try:
                        _sz = _load_app_config().get('sizing', {}) or {}
                        _blot = float(_sz.get('base_lot', 0.03))
                        _imult = int(_sz.get('initial_multiplier', 2))
                        _max_mult = int(_sz.get('max_multiplier', 5))
                    except Exception:
                        _blot, _imult, _max_mult = 0.03, 2, 5
                    # Hunter uses conservative lot (always multiplier=1)
                    if _is_hunter:
                        _lot = round(_blot * 1, 2)
                        _src_tag = 'HUNTER'
                        _comment = f"HUNTER_{_sid}"
                    else:
                        # LLM-driven sizing: Executor can specify `lot_multiplier`
                        # (1..max_multiplier) per setup, reasoning about risk,
                        # distance to levels, conviction, session, etc. If absent
                        # or invalid, fall back to default initial_multiplier (2).
                        _llm_mult = staged_trigger.get('lot_multiplier')
                        try:
                            _llm_mult = int(_llm_mult) if _llm_mult is not None else None
                        except (TypeError, ValueError):
                            _llm_mult = None
                        if _llm_mult is None or _llm_mult < 1 or _llm_mult > _max_mult:
                            _entry_mult = _imult
                            _mult_src = 'config_default'
                        else:
                            _entry_mult = _llm_mult
                            _mult_src = 'llm'
                        _lot = round(_blot * _entry_mult, 2)
                        _src_tag = 'EXECUTOR_AUTONOMOUS'
                        _comment = f"STAGED_{_sid}"
                        log.info(f"[STAGED FIRED] sizing: base={_blot} × mult={_entry_mult} ({_mult_src}) → lot={_lot}")
                    log.info(f"[STAGED FIRED] {_sid} {_direction} @ {price:.2f} "
                             f"src={_src_tag} tp={_h_tp or 0} sl={_h_sl or 0} — thesis: {_thesis[:80]}")
                    log.info(f"[STAGED FIRED] confirmations: {_details}")

                    # ── APPROACH TRACKER GATE ──
                    # Soft veto: bloqueja el fire si el flux institucional
                    # acumulat durant l'aproximació és FORTAMENT contrari a
                    # la direcció. Default thresholds conservadors al
                    # config.yaml. Si tracker disabled o no té state per
                    # aquesta zona, passa sense bloquejar.
                    _at_gate_block = False
                    try:
                        _at = _get_approach_tracker()
                        if _at is not None:
                            _zone_id = staged_trigger.get('zone_id') or _sid
                            _block, _reason = _at.should_block_fire(_zone_id, _direction)
                            if _block:
                                log.warning(f"[APPROACH GATE] BLOCKED fire of {_sid} ({_direction}): {_reason}")
                                _at_gate_block = True
                    except Exception as _at_gerr:
                        log.warning(f"[APPROACH GATE] check failed: {_at_gerr}")

                    if _at_gate_block:
                        # Salta aquest setup — pot reintentar al següent
                        # cycle quan flux es modifiqui (o expira si no).
                        continue

                    # The detection gate above (has_pending_order()) means we only
                    # reach here when the EA has processed the previous order. So
                    # send_market should succeed. If it doesn't for a different
                    # reason, we just log and continue — no re-fire loop possible
                    # because next tick's detection will be gated again.
                    #
                    # 2026-05-04: detectar Mode Recorregut Institucional. Si el
                    # setup té auto_close_conditions, és recorregut: TP al broker =
                    # tp_target, SL = 0 (el watcher gestiona invalidació qualitativa).
                    _auto_close_set = staged_trigger.get('auto_close_conditions') or []
                    _is_recorregut = bool(_auto_close_set)
                    _tp_target_set = staged_trigger.get('tp_target')
                    if _tp_target_set is None:
                        # Fallback: profit_target single (Hunter) o primer profit_targets
                        _tp_target_set = staged_trigger.get('profit_target')
                        if _tp_target_set is None:
                            _pts = staged_trigger.get('profit_targets') or []
                            if _pts:
                                _first = _pts[0]
                                if isinstance(_first, dict):
                                    _tp_target_set = _first.get('price')
                                else:
                                    _tp_target_set = _first
                    try:
                        _tp_target_set = float(_tp_target_set) if _tp_target_set else 0.0
                    except Exception:
                        _tp_target_set = 0.0

                    try:
                        if _is_recorregut:
                            # Mode Recorregut: TP real al broker, SL=0 (watcher gestiona)
                            sent = send_market(_direction, _lot, _comment,
                                                sl=0, tp=_tp_target_set if _tp_target_set > 0 else 0)
                        elif _is_hunter and _h_tp and _h_sl:
                            sent = send_market(_direction, _lot, _comment,
                                                sl=float(_h_sl), tp=float(_h_tp))
                        else:
                            # Legacy: TP del profit_target/tp_target si existeix, sl=0
                            sent = send_market(_direction, _lot, _comment,
                                                sl=0, tp=_tp_target_set if _tp_target_set > 0 else 0)
                    except Exception as _soe:
                        log.warning(f"[STAGED FIRED] send_market failed: {_soe}")
                        sent = False
                    if sent:
                        if sig_state and hasattr(sig_state, 'open_signal'):
                            sig_state.open_signal(_direction, price, 'AUTONOMOUS', _lot,
                                                  start_balance=account.get('balance', 0))
                            # Persist Executor's tactical plan onto the active
                            # signal so apply_trade_plan can honor profit_targets
                            # past the fire boundary. Hunter setups carry a
                            # single profit_target; Executor setups carry a list.
                            try:
                                _pt_raw = (staged_trigger.get('profit_targets')
                                           or ([staged_trigger.get('profit_target')]
                                               if staged_trigger.get('profit_target') else []))
                                _avg_raw = staged_trigger.get('averaging_zones') or []
                                # ── 2026-05-04: camps Mode Recorregut Institucional ──
                                _trap = staged_trigger.get('trap_thesis') or ''
                                _tpth = staged_trigger.get('tp_thesis') or ''
                                _invth = staged_trigger.get('invalidation_thesis') or ''
                                # 2026-05-06 BUG FIX: profit_targets són dicts complets
                                _pt_norm = []
                                for p in _pt_raw:
                                    if not p: continue
                                    if isinstance(p, dict):
                                        _pt_norm.append(p)
                                    else:
                                        try:
                                            _pt_norm.append({'price': float(p), 'close_pct': 0, 'reasoning': ''})
                                        except Exception:
                                            pass
                                _avg_norm = []
                                for p in _avg_raw:
                                    if p is None: continue
                                    if isinstance(p, (int, float)):
                                        _avg_norm.append(float(p))
                                _exec_plan = {
                                    'profit_targets':  _pt_norm,
                                    'averaging_zones': _avg_norm,
                                    'tactical_plan':   str(staged_trigger.get('tactical_plan') or ''),
                                    'play_type':       str(staged_trigger.get('play_type') or ''),
                                    'staged_at':       time.time(),
                                    'source':          _src_tag,
                                    # Mode Recorregut Institucional ─────────────────
                                    'auto_close_conditions': _auto_close_set,
                                    'trap_thesis':           str(_trap),
                                    'tp_thesis':             str(_tpth),
                                    'invalidation_thesis':   str(_invth),
                                    'tp_target':             float(_tp_target_set) if _tp_target_set else 0.0,
                                    'entry_price':           float(price),
                                    'mode': 'institutional_recorregut' if _is_recorregut else 'legacy',
                                }
                                sig_state._data['executor_plan'] = _exec_plan
                                sig_state.save()
                                if _is_recorregut:
                                    # Logging detallat amb R/R efectiu
                                    _tp_dist = abs(float(_tp_target_set) - float(price)) if _tp_target_set else 0
                                    _full_close_dist = None
                                    for _c in _auto_close_set:
                                        if _c.get('action') == 'FULL_CLOSE' and _c.get('kind') == 'bar_close':
                                            try:
                                                _d = abs(float(_c.get('level')) - float(price))
                                                if _full_close_dist is None or _d < _full_close_dist:
                                                    _full_close_dist = _d
                                            except Exception:
                                                pass
                                    if _full_close_dist:
                                        _rr = _tp_dist / _full_close_dist if _full_close_dist > 0 else 0
                                        log.info(
                                            f"[STAGED FIRED] 🎯 RECORREGUT mode armed: "
                                            f"tp={_tp_target_set} (${_tp_dist:.1f}$ enllà), "
                                            f"SL virtual a ${_full_close_dist:.1f}$ → R/R 1:{_rr:.2f}"
                                        )
                                    else:
                                        log.info(
                                            f"[STAGED FIRED] 🎯 RECORREGUT armed: tp={_tp_target_set}, "
                                            f"sense FULL_CLOSE conditions (només FORCE_REVIEW/PARTIAL)"
                                        )
                                    for _i, _c in enumerate(_auto_close_set):
                                        log.info(f"[STAGED FIRED]   cond #{_i}: {_c.get('id')} "
                                                 f"[{_c.get('kind')}] → {_c.get('action')}")
                                elif _exec_plan['profit_targets']:
                                    log.info(
                                        f"[STAGED FIRED] Persisted executor_plan (legacy): "
                                        f"targets={_exec_plan['profit_targets']} "
                                        f"play={_exec_plan['play_type']} "
                                        f"avg={_exec_plan['averaging_zones']}"
                                    )
                                    # Init the ladder NOMÉS en mode legacy (recorregut té TP único broker)
                                    try:
                                        import executor_ladder as _el2
                                        # 2026-05-04 fix: era `_t.get(...)` (undef); ara `staged_trigger`
                                        _entry_for_ladder = float(
                                            staged_trigger.get('price')
                                            or staged_trigger.get('entry_price')
                                            or price or 0
                                        )
                                        _el2.init_from_signal(
                                            _exec_plan['profit_targets'], _direction,
                                            entry_price=_entry_for_ladder if _entry_for_ladder > 0 else None,
                                        )
                                    except Exception as _le2:
                                        log.warning(f"[STAGED FIRED] ladder init failed: {_le2}")
                                else:
                                    log.warning(
                                        f"[STAGED FIRED] ⚠ Trade obert sense conditions ni profit_targets. "
                                        f"Només DD 3.5% protegeix. EXECUTOR hauria de proposar conditions al pròxim cycle."
                                    )
                            except Exception as _epe:
                                log.warning(f"[STAGED FIRED] Failed to persist executor_plan: {_epe}")
                        staging_trades_today += 1
                        engine.mark_staged_fired(_sid)
                        # Hunter: record fill + DON'T clear others; leave any other post_close setups alone
                        if _is_hunter:
                            try:
                                import hunter_stats as hs
                                hs.record_fill(_sid, ticket_id=None, fill_price=price)
                            except Exception:
                                pass
                        else:
                            try:
                                staged_setups_module = __import__('staged_setups')
                                staged_setups_module.clear()
                            except Exception:
                                pass
                        try:
                            # 2026-05-07: missatge TG simplificat
                            _emoji = "🎯" if _is_hunter else "🚀"
                            _label = "HUNTER" if _is_hunter else "OBERT"
                            _tp_part = f" · TP {_h_tp} · SL {_h_sl}" if _is_hunter else ""
                            notify("trade_opened",
                                   f"{_emoji} {_label} {_direction} {_lot} @ ${price:.2f}{_tp_part}\n"
                                   f"💡 {_thesis[:120]}")
                        except Exception:
                            pass
                        try:
                            trade_history.log_event(
                                type='OPEN', direction=_direction, lot=_lot, price=price,
                                reason=f"{'Hunter' if _is_hunter else 'Autonomous staged'} entry: {_thesis[:160]}",
                                source=_src_tag,
                            )
                        except Exception:
                            pass

            # ═══════════════════════════════════════════════════════════
            # AUTOMATIC EXIT PATHS — priority-ordered, single-fire per tick
            # ═══════════════════════════════════════════════════════════
            # Five independent systems that can close part/all of the trade:
            #   1. opportunistic_close (FULL close — target zone reached)
            #   2. trailing (partial — drawdown from peak)
            #   3. momentum (partial — 3-bar favorable burst)
            #   4. ladder (partial — $N floating milestone)
            #   5. zone-touch (partial — price at contrary zone)
            #
            # Rules (unified 2026-04-24 to fix rule cross-firing):
            #   · breakeven_set (BE applied) → ALL paths skipped. Once we've
            #     secured the trade, only Executor CLI can partial-close on
            #     explicit decision. FAST reflex partials risk over-exiting
            #     a protected runner.
            #   · Single-fire mutex: the FIRST matching path fires; the rest
            #     skip for this tick. Avoids double-close when two reflexes
            #     agree (e.g. trailing + zone-touch in the same moment).
            # ═══════════════════════════════════════════════════════════
            _be_active = bool(hasattr(sig_state, 'is_breakeven') and sig_state.is_breakeven())
            _partial_fired = False

            # 2026-05-04: Mode Recorregut Institucional — desactivar reflex
            # automàtics. La gestió va via auto_close_conditions (LLM pre-aprova)
            # i executor_ladder (multi-TP del LLM). NO automatismes genèrics.
            _skip_reflex = False
            try:
                _ep_rfx = sig_state._data.get('executor_plan') or {} if hasattr(sig_state, '_data') else {}
                if _ep_rfx.get('mode') == 'institutional_recorregut' or _ep_rfx.get('auto_close_conditions'):
                    _skip_reflex = True
            except Exception:
                pass

            # ── 5b0. FAST ENGINE — opportunistic full close ──
            # HIGHEST priority: if price reaches a STRONG target zone with
            # substantial floating and momentum, close EVERYTHING.
            # NOT gated by BE — this is a TARGET REACHED signal, different
            # from reflex partials. Reaching the target = thesis played out.
            opp = None if _skip_reflex else engine.check_opportunistic_close(bars_cache, account)
            if opp and opp.get('full_close'):
                # Only mark as fired IF the order actually went to the EA.
                # Fix 2 (2026-04-24): incident at 13:05:29 where close_all
                # was blocked by a pending MODIFY_TP written 0.5s earlier,
                # but mark_opportunistic_close fired anyway → no retry →
                # user had to close manually 3min later. Now we verify.
                _sent_close = close_all_brain()
                if not _sent_close:
                    log.warning(f"[FAST] OPP CLOSE WRITE FAILED — will retry next tick. Zone {opp['zone_price']:.1f}")
                    try:
                        notify("system_alert",
                               f"⚠️ Opportunistic close @ {opp['zone_price']:.1f} NO ha sortit al EA. Reintentant...")
                    except Exception:
                        pass
                    # Don't mark fired — let next tick try again
                    continue_to_next_exit = True  # sentinel, no-op, just documentation
                else:
                    engine.mark_opportunistic_close()
                    if sig_state.is_active():
                        sig_state.mark_closing()
                    try:
                        notify("trade_closed",
                               f"🎯 TANCAT 100% al destí ${opp['zone_price']:.1f} · "
                               f"profit capturat ${opp['floating']:.2f}")
                    except Exception:
                        pass
                    try:
                        trade_history.log_event(
                            type='FULL_CLOSE', direction=sig_state.get('direction', ''),
                            price=price,
                            reasoning=f"Opportunistic full close at STRONG target zone {opp['zone_price']:.1f} with momentum",
                            source='fast_engine_opp',
                            pnl_delta=opp['floating'],
                        )
                    except Exception:
                        pass
                    log.info(f"[FAST] OPPORTUNISTIC CLOSE ALL at zone {opp['zone_price']:.1f}")

            # ── 5b1. FAST ENGINE — profit ladder (DESACTIVAT 2026-04-24) ──
            # Tancava el 100% del millor ticket a cada $40 de floating,
            # trencant la distribució de lots. Delegat a peak-lock (SL
            # progressiu sense tancar tickets) + zone capture proporcional.
            # Set to None to skip; kept in code for possible re-enable via flag.
            ladder = None  # disabled — see above
            # ORIGINAL: ladder = None if (_be_active or _partial_fired) else engine.check_profit_ladder(bars_cache, account)
            if ladder:
                sent = partial_close_pct(ladder['ticket'], ladder['pct'])
                if sent:
                    _partial_fired = True
                    engine.mark_ladder_fired()
                    try:
                        notify("partial_close",
                               f"🪜 PROFIT LADDER step #{engine.profit_ladder_step} · "
                               f"ticket {ladder['ticket']} · ≈${ladder['expected_realized']:.2f} booked")
                    except Exception:
                        pass
                    try:
                        trade_history.log_event(
                            type='PARTIAL_CLOSE', ticket=ladder['ticket'],
                            direction=sig_state.get('direction', ''),
                            lot=None, price=price,
                            reasoning=f"Profit ladder step #{engine.profit_ladder_step}",
                            source='fast_engine_ladder',
                        )
                    except Exception:
                        pass
                    log.info(f"[FAST] LADDER ticket={ladder['ticket']} step={engine.profit_ladder_step}")

            # ── 5b1-bis. FAST ENGINE — momentum burst partial ──
            # Captures instant profits on a 3-bar favorable burst (≥ 2×ATR).
            # Runs BEFORE trailing so we lock gains while still near the peak,
            # not after the mandatory drawdown retrace.
            # MOMENTUM partial DESACTIVAT 2026-04-24 — tancava 100% del millor
            # ticket en burst 3-bar. Substituit per zone capture proporcional.
            mom = None
            # ORIGINAL: mom = None if (_be_active or _partial_fired) else engine.check_fast_momentum_partial(bars_cache, account)
            if mom:
                sent = partial_close_pct(mom['ticket'], mom['pct'])
                if sent:
                    _partial_fired = True
                    engine.partial_count += 1
                    engine.exec_lock_until = time.time() + engine.EXEC_LOCK_TIMEOUT
                    try:
                        notify("partial_close",
                               f"⚡ MOMENTUM PARTIAL · ticket {mom['ticket']} · ≈${mom['expected_realized']:.2f} booked (3-bar burst)")
                    except Exception:
                        pass
                    try:
                        trade_history.log_event(
                            type='PARTIAL_CLOSE', ticket=mom['ticket'],
                            direction=sig_state.get('direction', ''),
                            lot=None, price=price,
                            reasoning="Fast momentum burst ≥ 2×ATR",
                            source='fast_engine_momentum',
                        )
                    except Exception:
                        pass
                    log.info(f"[FAST] MOMENTUM partial ticket={mom['ticket']}")

            # ── 5b2. FAST ENGINE — trailing from peak ──
            # Captures reversals: if floating profit drops 30% from its peak
            # after reaching ≥ $40, close 1 ticket. Zone-agnostic.
            # TRAILING DESACTIVAT 2026-04-24 — tancava 100% del millor ticket
            # en drawdown ≥30% del peak. La protecció de profit està delegada
            # a peak-lock (mou SL progressivament): Stage1 BE a peak $50,
            # Stage2 blend+$5 a peak $100, Stage3 blend+$10 a peak $150.
            # Això protegeix el profit SENSE trencar la distribució de tickets.
            trail = None
            # ORIGINAL: trail = None if (_be_active or _partial_fired) else engine.check_trailing(bars_cache, account)
            if trail:
                sent = partial_close_pct(trail['ticket'], trail['pct'])
                if sent:
                    _partial_fired = True
                    engine.mark_trail_fired()
                    try:
                        notify("partial_close",
                               f"📉 TRAILING FIRE #{engine.trail_fires} · "
                               f"ticket {trail['ticket']} · ≈${trail['expected_realized']:.2f} booked (drop from peak)")
                    except Exception:
                        pass
                    try:
                        trade_history.log_event(
                            type='PARTIAL_CLOSE', ticket=trail['ticket'],
                            direction=sig_state.get('direction', ''),
                            lot=None, price=price,
                            reasoning=f"Trailing from peak #{engine.trail_fires}",
                            source='fast_engine_trail',
                        )
                    except Exception:
                        pass

            # ── 5b3. FAST ENGINE — proportional zone capture ──
            # When price reaches a PLANNED TP zone, capture a % of total
            # position distributed across ALL tickets proportionally. This
            # replaces the old "close 100% of best ticket" rule that was
            # stranding worst-positioned tickets.
            #
            # NOT gated by BE: this is target-reached profit capture, not
            # reflex. Still respects the single-fire mutex so it doesn't
            # compound with ladder/momentum/trailing in the same tick.
            zcap = None if (_partial_fired or _skip_reflex) else engine.check_zone_proportional_capture(bars_cache, account)
            if zcap:
                any_sent = False
                for o in zcap['orders']:
                    sent = partial_close_pct(o['ticket'], o['pct'])
                    if sent:
                        any_sent = True
                        try:
                            trade_history.log_event(
                                type='PARTIAL_CLOSE', ticket=o['ticket'],
                                direction=sig_state.get('direction', ''),
                                lot=o['lot_close'], price=price,
                                pnl_delta=o['profit_portion'],
                                reasoning=f"Proportional capture {zcap['capture_pct']}% at {zcap['strength']} zone {zcap['zone_price']:.1f}",
                                source='fast_zone_capture',
                            )
                        except Exception:
                            pass
                if any_sent:
                    _partial_fired = True
                    engine.mark_partial_sent(zcap['zone_price'])
                    try:
                        notify("partial_close",
                               f"🎯 ZONE CAPTURE {zcap['capture_pct']}% @ {zcap['strength']} zone "
                               f"{zcap['zone_price']:.1f} · {len(zcap['orders'])} ticket(s) reduïts · "
                               f"≈${zcap['total_realized']:+.2f} booked")
                    except Exception:
                        pass
                    log.info(f"[FAST] ZONE CAPTURE fired @ {zcap['zone_price']:.1f} "
                             f"[{zcap['strength']}] {zcap['capture_pct']}% — "
                             f"{len(zcap['orders'])} order(s) sent")

            # ── 5b4. FAST ENGINE — blend-ladder proportional capture ──
            # Every BLEND_LADDER_STEP_USD ($10) of weighted-blend price advance,
            # fire a 25% proportional capture across all tickets — independent
            # of zone availability. Acts like a fixed-distance MODERATE target.
            bcap = None if (_partial_fired or _skip_reflex) else engine.check_blend_ladder_capture(bars_cache, account)
            if bcap:
                any_sent = False
                for o in bcap['orders']:
                    sent = partial_close_pct(o['ticket'], o['pct'])
                    if sent:
                        any_sent = True
                        try:
                            trade_history.log_event(
                                type='PARTIAL_CLOSE', ticket=o['ticket'],
                                direction=sig_state.get('direction', ''),
                                lot=o['lot_close'], price=price,
                                pnl_delta=o['profit_portion'],
                                reasoning=(f"Blend-ladder L{bcap['level']} "
                                           f"(advance ${bcap['blend_advance']:.2f} from blend {bcap['blend']:.2f})"),
                                source='fast_blend_ladder',
                            )
                        except Exception:
                            pass
                if any_sent:
                    _partial_fired = True
                    engine.mark_blend_ladder_fired(bcap['level'])
                    try:
                        notify("partial_close",
                               f"📈 BLEND LADDER L{bcap['level']} {bcap['capture_pct']}% "
                               f"(blend {bcap['blend']:.1f} · advance ${bcap['blend_advance']:.2f}) · "
                               f"{len(bcap['orders'])} ticket(s) reduïts · "
                               f"≈${bcap['total_realized']:+.2f} booked")
                    except Exception:
                        pass
                    log.info(f"[FAST] BLEND LADDER L{bcap['level']} fired — "
                             f"{bcap['capture_pct']}% × {len(bcap['orders'])} ticket(s)")

            # ── 5.5. STAGED SETUP TRIGGER — fire prepared setups when price confirms ──
            if autonomous_mode:
                try_fire_staged_setup(sig_state, bars_cache, account, price)

            # ── 5.54. STALLED TRADE TIMEOUT (2026-05-07) ──
            # Si 5 min després del fire el preu és ±$1 de l'entry i BE encara
            # no s'ha activat (no hem arribat ni a TP1), el setup ha fallat
            # estructuralment — tanquem flat per alliberar capital.
            try:
                if (sig_state.is_active() and account.get('positions')
                        and not bool(account.get('closing'))
                        and not (hasattr(sig_state, 'is_breakeven') and sig_state.is_breakeven())):
                    _entry_price = float(sig_state._data.get('entry_price') or 0)
                    _open_ts = float(sig_state._data.get('opened_at') or 0)
                    if _entry_price > 0 and _open_ts > 0:
                        _elapsed = time.time() - _open_ts
                        if _elapsed >= 300:  # 5 min
                            _dist = abs(price - _entry_price)
                            if _dist <= 1.0:
                                log.warning(
                                    f"[STALL-TIMEOUT] ⏱ Trade stalled — {_elapsed:.0f}s "
                                    f"sense moviment, preu {price:.2f} a {_dist:.2f}$ "
                                    f"d'entry {_entry_price:.2f}, BE no activat → FLAT CLOSE"
                                )
                                try:
                                    notify('auto_close',
                                           f"⏱ TIMEOUT 5min · trade tancat flat\n"
                                           f"📍 Sense moviment des d'entry ${_entry_price:.2f} "
                                           f"(preu actual ${price:.2f})")
                                except Exception:
                                    pass
                                try:
                                    close_all_brain()
                                    sig_state._data['closing'] = True
                                    sig_state._data['status'] = 'CLOSING'
                                    sig_state.save()
                                except Exception as _ce:
                                    log.warning(f"[STALL-TIMEOUT] close failed: {_ce}")
                                _force_executor_review('stall_timeout_5min')
            except Exception as _ste:
                log.debug(f"[STALL-TIMEOUT] watcher error: {_ste}")

            # ── 5.54-bis. TRADE MONITOR — thread paral·lel ──
            # El monitor corre en un thread separat. Aquí només l'engeguem
            # un cop al primer cycle. El thread llegeix dades fresques cada
            # vegada que arriba resposta de DeepSeek (cycle real ~3-5s).
            if not getattr(main, '_monitor_started', False):
                try:
                    import trade_monitor as _tm
                    _mon = _tm.get_monitor()

                    def _monitor_context_provider():
                        """Provideix context fresc al thread del monitor.
                        Usa funcions module-level per garantir frescor + thread safety.
                        """
                        try:
                            _ss = get_state()  # signal state singleton
                            _account = get_account_state() or {}
                            # Bars: usem la variable global _MTF_BARS_CACHE.M5
                            _bars = []
                            try:
                                _m5_cache = _MTF_BARS_CACHE.get('m5', {}) or {}
                                _bars = list(_m5_cache.get('bars') or [])
                            except Exception:
                                _bars = []
                            try:
                                _fp = _flow_proxy_dict() or {}
                            except Exception:
                                _fp = {}
                            _approach = {}
                            try:
                                _at_obj = _get_approach_tracker()
                                if _at_obj is not None:
                                    _states = _at_obj.get_payload_dict() or {}
                                    _approach = next(iter(_states.values()), {}) if _states else {}
                            except Exception:
                                _approach = {}
                            log.debug(f"[MONITOR-CTX] active={_ss.is_active()} pos={len(_account.get('positions') or [])} bars={len(_bars)}")
                            return (_ss, _account, _bars, _fp, _approach)
                        except Exception as _ce:
                            log.debug(f"[MONITOR-CTX] failed: {_ce}")
                            return None

                    _mon.start_thread(
                        context_provider=_monitor_context_provider,
                        system_prompt=_tm.get_prompt(),
                        call_claude_fn=_call_claude,
                        call_deepseek_fn=_call_deepseek,
                    )
                    main._monitor_started = True
                except Exception as _mte:
                    log.warning(f"[MONITOR] thread start failed: {_mte}")

            # ── 5.55. WICK VIRTUAL SL WATCHER ──
            # Per a trades en mode wick, el SL "dinàmic" és el wick top (SELL)
            # o wick bottom (BUY) de la vela que va disparar. Si el preu
            # creua aquest nivell EN DIRECCIÓ ADVERSA, tanquem AL MOMENT.
            # Aquest és el SL real (no el TP del broker, que és el destí).
            try:
                if (sig_state.is_active() and account.get('positions')
                        and not bool(account.get('closing'))):
                    _plan_wsl = sig_state._data.get('executor_plan') or {}
                    _wsl = _plan_wsl.get('wick_dynamic_sl')
                    _dir_open = account.get('direction') or sig_state._data.get('direction')
                    if _wsl is not None and _dir_open in ('BUY', 'SELL'):
                        _wsl_price = float(_wsl)
                        _breach = False
                        if _dir_open == 'SELL' and price >= _wsl_price:
                            _breach = True
                        elif _dir_open == 'BUY' and price <= _wsl_price:
                            _breach = True
                        if _breach:
                            log.warning(
                                f"[WICK-SL] 🛑 SL VIRTUAL TOCAT — preu {price:.2f} {_dir_open} "
                                f"creua wick_sl {_wsl_price:.2f} → FULL_CLOSE immediat"
                            )
                            try:
                                notify('auto_close',
                                       f"🛑 STOP LOSS · trade tancat\n"
                                       f"📍 Preu ${price:.2f} ha travessat SL ${_wsl_price:.2f} ({_dir_open})")
                            except Exception:
                                pass
                            try:
                                close_all_brain()
                                sig_state._data['closing'] = True
                                sig_state._data['status'] = 'CLOSING'
                                sig_state.save()
                            except Exception as _ce:
                                log.warning(f"[WICK-SL] close_all_brain failed: {_ce}")
                            _force_executor_review('wick_sl_breach')
            except Exception as _wse:
                log.debug(f"[WICK-SL] watcher error: {_wse}")

            # ── 5.6. AUTO-CLOSE WATCHER (Mode Recorregut Institucional) ──
            # Quan hi ha trade obert i el LLM ha pre-aprovat condicions de
            # tancament, vigilem cycle a cycle. NO és una regla del codi: cada
            # condició la va posar el LLM raonant la tesi qualitativa del
            # trade concret. Si dispara amb action=FULL_CLOSE, tanquem.
            try:
                if (sig_state.is_active() and account.get('positions')
                        and not bool(account.get('closing'))):
                    _plan_open = sig_state._data.get('executor_plan') or {}
                    _conds = _plan_open.get('auto_close_conditions') or []
                    if isinstance(_conds, list) and _conds:
                        # Estat M5/M15 last_close_ts per evitar re-disparos
                        if not hasattr(main, '_auto_close_state'):
                            main._auto_close_state = {'last_m5_ts': 0, 'last_m15_ts': 0}
                        _acs = main._auto_close_state

                        # flow_proxy + approach state actuals (cache-friendly)
                        try:
                            _fp_now = _flow_proxy_dict() or {}
                        except Exception:
                            _fp_now = {}
                        _approach_now = {}
                        try:
                            _at_obj = _get_approach_tracker()
                            if _at_obj is not None and _plan_open.get('entry_price'):
                                # Trobem state de la zona del trade actual
                                _z_for_lookup = float(_plan_open.get('entry_price') or 0)
                                # Aproximació: fem mitjana del state més proper a la zona
                                _states = _at_obj.get_payload_dict() or {}
                                if _states:
                                    # agafem el primer (l'API exposa per zone_id)
                                    _approach_now = next(iter(_states.values()), {})
                        except Exception:
                            _approach_now = {}

                        _fired = _evaluate_auto_close_conditions(
                            _conds, bars_cache, account, sig_state,
                            _fp_now, _approach_now,
                            _acs.get('last_m5_ts', 0),
                            _acs.get('last_m15_ts', 0),
                        )

                        # Update last_m5/m15 ts si una condició bar_close va córrer
                        try:
                            if any(c.get('kind') == 'bar_close' and c.get('tf') == 'M5' for c in _conds):
                                _bm5 = aggregate_bars(bars_cache, 5) if bars_cache else []
                                if _bm5:
                                    _acs['last_m5_ts'] = float(_bm5[-1].get('time') or 0)
                            if any(c.get('kind') == 'bar_close' and c.get('tf') == 'M15' for c in _conds):
                                _bm15 = aggregate_bars(bars_cache, 15) if bars_cache else []
                                if _bm15:
                                    _acs['last_m15_ts'] = float(_bm15[-1].get('time') or 0)
                        except Exception:
                            pass

                        if _fired:
                            for _tc in _fired:
                                _act = _tc.get('action')
                                _cid = _tc.get('id', '?')
                                _tc['fired_at'] = time.time()
                                if _act == 'FULL_CLOSE':
                                    log.warning(
                                        f"[AUTO_CLOSE] 🤖 LLM-pre-approved: {_cid} "
                                        f"[{_tc.get('kind')}] → FULL_CLOSE"
                                    )
                                    try:
                                        notify('auto_close',
                                               f"🛑 INVALIDACIÓ · trade tancat\n"
                                               f"📝 Condició LLM activada: {_cid}\n"
                                               f"💀 La tesi del trade ha mort")
                                    except Exception:
                                        pass
                                    try:
                                        close_all_brain()
                                        # Flag closing so the immediate ghost handler
                                        # (Case A in section 2.1) skips this signal —
                                        # we want the MANUAL_CLOSE detector at §3.6
                                        # to handle the cleanup with its 10s grace
                                        # period + authoritative end_balance, so the
                                        # TG "🔒 CLOSED ..." message reports the real
                                        # net P&L (balance delta) instead of the
                                        # stale realized_profit ($0.00) that we read
                                        # before the EA finished settling the close.
                                        try:
                                            sig_state._data['closing'] = True
                                            sig_state._data['status'] = 'CLOSING'
                                            sig_state.save()
                                        except Exception:
                                            pass
                                    except Exception as _ce:
                                        log.warning(f"[AUTO_CLOSE] close_all_brain failed: {_ce}")
                                    _force_executor_review(f'auto_close_fired:{_cid}')
                                elif _act == 'PARTIAL_50':
                                    log.warning(
                                        f"[AUTO_CLOSE] 🤖 LLM-pre-approved: {_cid} → PARTIAL_50"
                                    )
                                    try:
                                        for _p in (account.get('positions') or []):
                                            _tk = int(_p.get('ticket', 0) or 0)
                                            if _tk:
                                                partial_close_pct(_tk, 50)
                                        notify('auto_close',
                                               f"⚠️ PARCIAL 50% · reducció de risc\n"
                                               f"📝 Condició LLM: {_cid}")
                                    except Exception as _pe:
                                        log.warning(f"[AUTO_CLOSE] partial failed: {_pe}")
                                    _force_executor_review(f'auto_partial:{_cid}')
                                elif _act == 'FORCE_REVIEW':
                                    log.info(
                                        f"[AUTO_CLOSE] condition met: {_cid} → FORCE_REVIEW (LLM decideix)"
                                    )
                                    _force_executor_review(f'condition_met:{_cid}')
                            # Persistim fired_at perquè no repetim
                            try:
                                sig_state._data['executor_plan']['auto_close_conditions'] = _conds
                                sig_state.save()
                            except Exception as _se:
                                log.warning(f"[AUTO_CLOSE] save fired_at failed: {_se}")
            except Exception as _wt_e:
                log.warning(f"[AUTO_CLOSE] watcher loop error: {_wt_e}")

            # ── 6. INDICATOR+REVIEWER PIPELINE — async, triggered by M15 close / session ──
            # Cadence: once per M15 slot (XX:00, :15, :30, :45) — and by extension once per
            # session transition since those fall on :00 boundaries. The first cycle also
            # fires immediately (last_m15_slot starts as None).
            if _indicator_future is not None and _indicator_future.done():
                try:
                    response = _indicator_future.result(timeout=0.1)
                    if response:
                        zones = response.get('reversal_zones', [])
                        bias = response.get('bias', 'NEUTRAL')
                        _ind_bias = bias
                        _ind_regime = response.get('regime', '?')
                        ctx = response.get('context', '')
                        log.info(f"[INDICATOR] {len(zones)} active zones post-review, bias={bias} -- {ctx}")
                        _ind_status = "OK"
                        # Zone state already persisted by run_indicator_pipeline via zone_store.
                        engine.update_zones(zones)
                        try:
                            draw_reasoning(response, bars_cache, account, sig_state)
                        except Exception as e:
                            log.warning(f"Draw reasoning failed: {e}")
                    else:
                        _ind_status = "ERROR"
                except Exception as e:
                    log.warning(f"INDICATOR future error: {e}")
                    _ind_status = "ERROR"
                _indicator_future = None
                last_indicator = time.time()
                _llm_mark_done('indicator')

            # Trigger: new M15 slot just entered, OR dashboard force button pressed
            now_utc = datetime.now(timezone.utc)
            current_m15_slot = (now_utc.year, now_utc.month, now_utc.day, now_utc.hour, now_utc.minute // 15)
            # Peek at force_indicator flag without consuming it (so we enter the block)
            _force_indicator_pending = False
            try:
                if os.path.exists(_CTRL_FILE):
                    with open(_CTRL_FILE, 'r', encoding='utf-8') as _cf:
                        _ctl = json.load(_cf)
                    _force_indicator_pending = bool(_ctl.get('force_indicator'))
            except Exception:
                pass
            if _indicator_future is not None and _force_indicator_pending:
                # Flag set while a previous call is still in-flight; log ONCE.
                if not getattr(main, '_force_ind_logged', False):
                    log.info("[INDICATOR] FORCE queued (previous call still running)")
                    main._force_ind_logged = True
            elif _indicator_future is None:
                main._force_ind_logged = False
            if _indicator_future is None and (current_m15_slot != last_m15_slot or _force_indicator_pending):
                # ── Relevance gate: only fire if something meaningful changed ──
                should_run, reason = _should_run_indicator(
                    now_ts=time.time(),
                    bars_cache=bars_cache,
                    account=account,
                    sig_state=sig_state,
                    last_run_ts=_ind_last_run_ts,
                    last_price=_ind_last_price,
                    last_zones_updated=_ind_last_zones_updated,
                    last_signal_id=_ind_last_signal_id,
                )
                last_m15_slot = current_m15_slot  # always advance so we don't re-check every tick

                # Dashboard force button — overrides throttle
                if _consume_control('force_indicator'):
                    should_run, reason = True, "FORCE (dashboard button)"

                if not should_run:
                    log.info(f"[INDICATOR] Skipped cycle — {reason}")
                else:
                    prompt = build_brain_prompt(bars_cache, account, screenshot_path)
                    if prompt:
                        _ind_status = "CALLING"
                        cur_price = bars_cache[-1]['close'] if bars_cache else 0
                        cur_atr = atr(bars_cache, 14) or 0
                        bars_m15 = aggregate_bars(bars_cache, 3)
                        cur_atr_m15 = atr(bars_m15, 14) or cur_atr
                        bias_prev = load_zones().get('bias', 'NEUTRAL')
                        log.info(f"[INDICATOR] Running — {reason}")
                        _indicator_future = _ai_pool.submit(
                            run_indicator_pipeline, prompt, bars_cache, cur_price, cur_atr, bias_prev,
                            account, cur_atr_m15,
                        )
                        _llm_mark_submit('indicator')
                        # Snapshot state so the next gate comparison works
                        _ind_last_run_ts = time.time()
                        _ind_last_price = cur_price
                        try:
                            from zone_store import read_state as _rs
                            _ind_last_zones_updated = _rs(COMMON).get('updated_at')
                        except Exception:
                            pass
                        _ind_last_signal_id = (sig_state.get('id') if hasattr(sig_state, 'get') else None)


            # ── 7. EXECUTOR BRAIN — async, only when signal active ──
            # Process completed future first
            if _executor_future is not None and _executor_future.done():
                try:
                    response = _executor_future.result(timeout=0.1)
                    if response:
                        action = response.get('action', 'WAIT')
                        conf = response.get('confidence', 0)
                        reasoning = response.get('reasoning', '')
                        mental = response.get('mental_state', '?')
                        next_plan = response.get('next_plan', '')

                        _exec_status = "OK"
                        _exec_action = action
                        _exec_conf = conf
                        _exec_mental = mental
                        _exec_reasoning = reasoning
                        _exec_plan = next_plan

                        log.info(f"[EXECUTOR] {action} (conf={conf:.0%}) [{mental}] -- {reasoning}")

                        # ── EXECUTOR BREAKEVEN TRIGGER ──
                        # Executor decides the price at which the SL should
                        # auto-move to entry/blend. Persist + the main loop
                        # tick later checks bar high/low to fire MOVE_SL_ENTRY.
                        try:
                            _be = response.get('breakeven_trigger')
                            if (isinstance(_be, dict)
                                    and _be.get('price') is not None
                                    and sig_state.is_active()
                                    and not sig_state.is_breakeven()):
                                _be_price = float(_be.get('price'))
                                # 2026-04-30: Validate BE trigger is in the FAVORABLE
                                # direction with a minimum distance. Otherwise the LLM
                                # places trigger == blend and the next natural wick
                                # closes the trade.
                                _be_dir = sig_state.get('direction')
                                _be_blend = float(account.get('entry_price') or 0)
                                _be_atr_m5 = atr(bars_cache, 14) or 0
                                _be_min_dist = max(2.0, 0.4 * _be_atr_m5)  # ≥0.4×ATR M5
                                _be_valid = True
                                _be_reject_reason = ''
                                if _be_dir == 'BUY':
                                    if _be_price <= _be_blend + _be_min_dist:
                                        _be_valid = False
                                        _be_reject_reason = (
                                            f"BUY trigger {_be_price:.2f} no està prou per sobre del blend "
                                            f"{_be_blend:.2f} (mínim +{_be_min_dist:.2f}). Sabotatge mecànic."
                                        )
                                elif _be_dir == 'SELL':
                                    if _be_price >= _be_blend - _be_min_dist:
                                        _be_valid = False
                                        _be_reject_reason = (
                                            f"SELL trigger {_be_price:.2f} no està prou per sota del blend "
                                            f"{_be_blend:.2f} (mínim -{_be_min_dist:.2f}). Sabotatge mecànic."
                                        )
                                if not _be_valid:
                                    log.warning(
                                        f"[EXECUTOR] BE trigger REJECTED — {_be_reject_reason} "
                                        f"reasoning was: {(_be.get('reasoning') or '')[:80]}"
                                    )
                                else:
                                    _ep_now2 = dict(sig_state._data.get('executor_plan') or {})
                                    _ep_now2['breakeven_trigger'] = {
                                        'price': _be_price,
                                        'reasoning': str(_be.get('reasoning') or ''),
                                        'set_at': time.time(),
                                    }
                                    sig_state._data['executor_plan'] = _ep_now2
                                    sig_state.save()
                                    log.info(
                                        f"[EXECUTOR] BE trigger set @ {_be_price:.2f} "
                                        f"({_be_dir} blend={_be_blend:.2f}, dist={abs(_be_price-_be_blend):.2f}) "
                                        f"— {(_be.get('reasoning') or '')[:60]}"
                                    )
                        except Exception as _bee:
                            log.warning(f"[EXECUTOR] BE trigger handling failed: {_bee}")

                        # ── EXECUTOR PROFIT TARGETS / LADDER REFRESH ──
                        # The Executor (in MANAGE mode) can publish/refresh the
                        # profit_targets for the active trade. Even on adopted
                        # trades — the LLM owns the exit plan once the trade
                        # is in its hands. We:
                        #   1. Persist new profit_targets onto sig_state.executor_plan
                        #   2. Re-initialize the ladder so PARTIAL_CLOSE_PCT fires
                        #      at the new levels with the LLM's distribution.
                        # Missing field ⇒ keep prior plan untouched (so we don't
                        # wipe the ladder on every WAIT cycle when the LLM
                        # forgot to repeat itself).
                        try:
                            _new_pt_raw = response.get('profit_targets')
                            if _new_pt_raw and isinstance(_new_pt_raw, list) and sig_state.is_active():
                                # Normalize (executor_ladder also normalizes,
                                # but we keep the raw shape on sig_state).
                                _direction = sig_state.get('direction')
                                if _direction in ('BUY', 'SELL'):
                                    # Persist on signal state
                                    _ep_now = dict(sig_state._data.get('executor_plan') or {})
                                    _ep_now['profit_targets'] = _new_pt_raw
                                    if 'staged_at' not in _ep_now:
                                        _ep_now['staged_at'] = time.time()
                                    _ep_now['source'] = _ep_now.get('source') or 'EXECUTOR_MANAGE'
                                    _ep_now['updated_at'] = time.time()
                                    sig_state._data['executor_plan'] = _ep_now
                                    sig_state.save()
                                    # 2026-05-06 BUG FIX: usar refresh_preserving_hits
                                    # en lloc d'init_from_signal. init_from_signal
                                    # resetejava TOTS els hit=False, fent que TP1 ja
                                    # disparat tornés a disparar quan el preu encara
                                    # estava prop → 75% del lot tancava a TP1 en lloc
                                    # de 50%, eating runner. Ara conservem fired state.
                                    try:
                                        import executor_ladder as _el
                                        _entry_for_refresh = None
                                        try:
                                            _entry_for_refresh = float(sig_state.get('entry_price') or 0) or None
                                        except Exception:
                                            _entry_for_refresh = None
                                        _el.refresh_preserving_hits(
                                            _new_pt_raw, _direction,
                                            entry_price=_entry_for_refresh,
                                        )
                                        _summary = ", ".join(
                                            f"{(t.get('price') if isinstance(t, dict) else t):.1f}"
                                            f"@{(t.get('close_pct') if isinstance(t, dict) else '?')}%"
                                            for t in _new_pt_raw[:4]
                                        )
                                        log.info(f"[EXECUTOR] Refreshed ladder ({_direction}): {_summary}")
                                    except Exception as _le:
                                        log.warning(f"[EXECUTOR] ladder refresh failed: {_le}")
                        except Exception as _ept:
                            log.warning(f"[EXECUTOR] profit_targets handling failed: {_ept}")

                        # ── 2026-05-04: refresh / promoció auto_close_conditions ──
                        # Si l'EXECUTOR proposa auto_close_conditions (camp nou Mode
                        # Recorregut), els persistim al executor_plan. Si era un trade
                        # legacy (mode != recorregut) el promocionem automàticament
                        # — els trades manuals adoptats també poden rebre conditions
                        # i passar a single-trade philosophy en el primer cycle de
                        # gestió on l'EXECUTOR articuli la tesi.
                        try:
                            _new_acc = response.get('auto_close_conditions')
                            if (_new_acc is not None
                                    and isinstance(_new_acc, list)
                                    and sig_state.is_active()):
                                # Normalitzar via staged_setups per validar conditions
                                try:
                                    import staged_setups as _ss_norm
                                    _normalized_acc = _ss_norm._normalize_auto_close_list(_new_acc)
                                except Exception:
                                    _normalized_acc = _new_acc  # passa-les sense validar si falla import
                                _ep_now = dict(sig_state._data.get('executor_plan') or {})
                                _was_legacy = (_ep_now.get('mode') != 'institutional_recorregut')
                                _ep_now['auto_close_conditions'] = _normalized_acc
                                _ep_now['updated_at'] = time.time()
                                # Promoció: si el trade era legacy, marquem recorregut
                                if _normalized_acc:
                                    _ep_now['mode'] = 'institutional_recorregut'
                                    if 'entry_price' not in _ep_now:
                                        try:
                                            _ep_now['entry_price'] = float(sig_state.get('entry_price') or 0)
                                        except Exception:
                                            pass
                                    # Capturem tesis si l'LLM les ha proposat
                                    if response.get('trap_thesis'):
                                        _ep_now['trap_thesis'] = str(response.get('trap_thesis') or '')
                                    if response.get('tp_thesis'):
                                        _ep_now['tp_thesis'] = str(response.get('tp_thesis') or '')
                                    if response.get('invalidation_thesis'):
                                        _ep_now['invalidation_thesis'] = str(response.get('invalidation_thesis') or '')
                                    if response.get('tp_target'):
                                        try:
                                            _ep_now['tp_target'] = float(response.get('tp_target') or 0)
                                        except Exception:
                                            pass
                                sig_state._data['executor_plan'] = _ep_now
                                sig_state.save()
                                if _normalized_acc:
                                    if _was_legacy:
                                        log.info(
                                            f"[EXECUTOR] 🎯 Trade promocionat a Mode Recorregut "
                                            f"({len(_normalized_acc)} auto_close_conditions). "
                                            f"AVERAGE bloquejat des d'ara, single-trade philosophy."
                                        )
                                    else:
                                        log.info(
                                            f"[EXECUTOR] auto_close_conditions actualitzades: "
                                            f"{len(_normalized_acc)} conditions actives"
                                        )
                                    for _i, _c in enumerate(_normalized_acc):
                                        log.info(f"[EXECUTOR]   cond #{_i}: {_c.get('id')} "
                                                 f"[{_c.get('kind')}] → {_c.get('action')}")
                                else:
                                    log.warning(
                                        f"[EXECUTOR] auto_close_conditions proposades però totes "
                                        f"rebutjades per validació schema. Trade sense conditions."
                                    )
                        except Exception as _acce:
                            log.warning(f"[EXECUTOR] auto_close_conditions handling failed: {_acce}")

                        # WAIT-streak throttle: track recent decisions so we
                        # can extend cooldown when the Executor is repeatedly
                        # confident in WAIT — re-asking burns tokens for the
                        # same answer. Reset on any non-WAIT action.
                        try:
                            if not hasattr(main, '_wait_streak'):
                                main._wait_streak = []  # list of recent (action, conf) tuples, max 3
                            if action == 'WAIT' and conf is not None:
                                main._wait_streak.append((action, float(conf)))
                                main._wait_streak = main._wait_streak[-3:]
                            else:
                                main._wait_streak = []  # reset on actionable decision
                        except Exception:
                            pass

                        # ── Pre-placed snipers proposed by the Executor ──
                        # Sniper persistence rules (2026-04-27):
                        #   · `pre_place_orders` MISSING or null → KEEP existing snipers
                        #     (the Executor wasn't asked about snipers this cycle, or it
                        #     trusts the existing list — don't silently wipe).
                        #   · `pre_place_orders: []` (explicit empty list) → WIPE all
                        #     (the Executor explicitly says "no snipers right now").
                        #   · `pre_place_orders: [...]` → REPLACE with the new list.
                        # Earlier behavior wiped on missing too, which caused snipers
                        # to vanish whenever the Executor forgot to re-emit them
                        # (incident 2026-04-27 10:08).
                        try:
                            _pre_raw = response.get('pre_place_orders')
                            if _pre_raw is None:
                                # Field absent or null → preserve existing
                                log.debug("[SNIPER] No pre_place_orders in response — existing snipers preserved")
                            elif isinstance(_pre_raw, list):
                                _pre_orders = _pre_raw  # may be empty (explicit wipe)
                                import snipers as _snp
                                _sig_id = sig_state.get('id') if hasattr(sig_state, 'get') else None
                                _sig_dir = account.get('direction')
                                _n, _skipped, _no_struct = _snp.replace_for_signal(_pre_orders, _sig_dir, _sig_id)
                                if _n > 0:
                                    _txt = ", ".join(
                                        f"{(s.get('direction') or '?')} {float(s.get('price') or 0):.1f}"
                                        f"×{float(s.get('multiplier') or 1):.1f}"
                                        for s in _pre_orders[:4])
                                    log.info(f"[SNIPER] Executor proposed {_n} sniper(s): {_txt}")
                                elif not _pre_orders:
                                    log.info("[SNIPER] Executor sent empty list — all snipers cleared")
                                if _skipped > 0:
                                    log.info(f"[SNIPER] Dropped {_skipped} re-proposal(s) at already-fired price(s) (one-shot per signal)")
                                if _no_struct > 0:
                                    log.warning(f"[SNIPER] Dropped {_no_struct} proposal(s) without STRONG zone confluence (structural gate)")
                            else:
                                log.warning(f"[SNIPER] pre_place_orders has unexpected type {type(_pre_raw).__name__} — ignored, existing snipers preserved")
                        except Exception as _snpe:
                            log.debug(f"[SNIPER] persist failed: {_snpe}")

                        # ── VALIDATOR GATE (v3.2 anti-bug + soft DD 3.4%) ──
                        # Last-mile sanity check before any order reaches the EA. If it
                        # fails, force WAIT + send a TG alert so the human can see why.
                        try:
                            import validator as _validator
                            _sig_for_val = {
                                "direction": account.get('direction'),
                                "breakeven_set": bool(sig_state.is_breakeven()) if hasattr(sig_state, 'is_breakeven') else False,
                                "flag_closing": bool(account.get('closing')),
                            }
                            # Merge cfg sections for validator: executor bounds + sizing + risk.
                            _cfg_all = _load_app_config()
                            _sz = _cfg_all.get('sizing', {}) or {}
                            _rc = _cfg_all.get('risk_control', {}) or {}
                            _val_cfg = dict(executor_cfg)
                            _val_cfg.update({
                                "base_lot": float(_sz.get('base_lot', 0.03)),
                                "max_multiplier": int(_sz.get('max_multiplier', 5)),
                                "dd_soft_pct": float(_rc.get('dd_soft_pct', 3.4)),
                                "validator_adverse_atr_factor": float(_rc.get('validator_adverse_atr_factor', 1.5)),
                            })
                            # Enrich account with current ATR M5 for ATR-based adverse estimate.
                            _acc_for_val = dict(account)
                            try:
                                _acc_for_val['atr_m5'] = atr(bars_cache, 14) or 0
                            except Exception:
                                _acc_for_val['atr_m5'] = 0
                            _ok, _rej = _validator.check(response, _sig_for_val, _acc_for_val, _val_cfg)
                        except Exception as _e:
                            # FAIL-CLOSED: any exception in the last-mile firewall forces WAIT.
                            # A silently-disengaged validator on a live-money system is worse than
                            # a false reject; the human can override via TG if needed.
                            log.error(f"[VALIDATOR] check error (fail-CLOSED, forcing WAIT): {_e}")
                            _ok = False
                            _rej = {
                                "code": "VALIDATOR_EXCEPTION",
                                "detail": f"validator.check raised: {type(_e).__name__}: {_e}",
                                "action": "WAIT",
                            }

                        if not _ok and _rej is not None:
                            log.warning(f"[VALIDATOR] REJECTED action={action} code={_rej['code']} detail={_rej['detail']}")
                            notify("validator_reject", f"🚫 Validator REJECT: {_rej['code']} — {_rej['detail']}")
                            # Journal the rejection — currently invisible elsewhere.
                            try:
                                _orig_action = response.get('action')
                                _tid_jr = sig_state.get_trade_id() if hasattr(sig_state, 'get_trade_id') else None
                                brain_journal.write(
                                    "order_rejected", "VALIDATOR",
                                    {
                                        "code": _rej.get('code'),
                                        "detail": _rej.get('detail'),
                                        "original_action": _orig_action,
                                        "forced_action": _validator.FORCED_FALLBACK_ACTION,
                                        "order": response.get('order'),
                                        "close_ticket": response.get('close_ticket'),
                                        "close_pct": response.get('close_pct'),
                                    },
                                    trade_id=_tid_jr,
                                    snapshot=brain_journal.build_snapshot(price, account, sig_state),
                                )
                            except Exception:
                                pass
                            # Force fallback — override the Executor's action.
                            action = _validator.FORCED_FALLBACK_ACTION
                            _exec_action = action
                            # Persist the rejection alongside the original response.
                            response = dict(response)
                            response['action'] = action
                            response['validator_rejection'] = _rej

                        # Persist decision to brain_executor_decisions.jsonl (commit 10)
                        try:
                            # v3.3: prefer FSM-persisted UUID over legacy derived id
                            _trade_id = sig_state.get_trade_id() if hasattr(sig_state, 'get_trade_id') else None
                            if not _trade_id and account.get('has_signal'):
                                _trade_id = f"{account.get('direction','?')}_{account.get('entry_price',0)}"
                            _inv_cond = parse_invalidation_condition(response.get('invalidation_condition'))
                            append_executor_decision({
                                "ts": time.time(),
                                "iso": datetime.now(timezone.utc).isoformat(),
                                "trade_id": _trade_id,
                                "trigger_events": _last_trigger_events,
                                "action": action,
                                "confidence": conf,
                                "mental_state": mental,
                                "thesis": response.get('thesis') or reasoning,
                                "reasoning_full": reasoning,
                                "next_plan": next_plan,
                                "invalidation_condition": _inv_cond,
                                "order": response.get('order'),
                                "close_ticket": response.get('close_ticket'),
                                "close_pct": response.get('close_pct'),
                                "response_raw": response,
                            })
                            # Mirror to unified journal — review tools read this.
                            try:
                                brain_journal.write(
                                    "decision_executor", "EXECUTOR",
                                    {
                                        "action": action,
                                        "confidence": conf,
                                        "mental_state": mental,
                                        "thesis": response.get('thesis'),
                                        "reasoning": (reasoning or '')[:1500],
                                        "next_plan": next_plan,
                                        "invalidation_condition": _inv_cond,
                                        "order": response.get('order'),
                                        "close_ticket": response.get('close_ticket'),
                                        "close_pct": response.get('close_pct'),
                                        "trigger_events": _last_trigger_events,
                                        "validator_rejection": response.get('validator_rejection'),
                                    },
                                    trade_id=_trade_id,
                                    snapshot=brain_journal.build_snapshot(price, account, sig_state),
                                )
                            except Exception:
                                pass
                        except Exception as _e:
                            log.warning(f"[EXECUTOR] decision persistence failed: {_e}")

                        if conf >= 0.7:
                            # 2026-05-04: AVERAGE PROHIBIT en Mode Recorregut Institucional
                            # (filosofia single-trade). Es rebutja sempre.
                            if action == "AVERAGE":
                                log.warning(
                                    f"[EXECUTOR] AVERAGE rebutjat — single-trade philosophy. Forçat WAIT."
                                )
                                try:
                                    notify("avg_rejected",
                                           "🚫 AVERAGE rebutjat: single-trade philosophy. "
                                           "EXECUTOR ha de proposar WAIT/CLOSE/PARTIAL.")
                                except Exception:
                                    pass
                                action = "WAIT"

                            if action == "PARTIAL_CLOSE" and response.get('close_ticket'):
                                pct = response.get('close_pct', 100)
                                tkt_llm = response['close_ticket']
                                # ── User policy 2026-04-30: NEVER close the best-positioned ticket ──
                                # Closing the ticket with the smallest loss (or in profit) removes
                                # our capacity to harvest the rebound. If we must reduce risk, close
                                # the WORST-positioned ticket(s) first — they're the ones bleeding.
                                # Override the LLM's choice to the worst ticket in the same direction.
                                tkt = tkt_llm
                                _positions = account.get('positions', []) or []
                                _direction = sig_state.get('direction', '')
                                if _positions and _direction:
                                    if _direction == 'BUY':
                                        # Worst BUY = HIGHEST entry (bought most expensive)
                                        _worst = max(_positions, key=lambda p: float(p.get('price_open', 0) or 0))
                                    else:
                                        # Worst SELL = LOWEST entry (sold cheapest)
                                        _worst = min(_positions, key=lambda p: float(p.get('price_open', 0) or 0))
                                    _worst_tkt = _worst.get('ticket') or _worst.get('ticket_id')
                                    if _worst_tkt and _worst_tkt != tkt_llm:
                                        log.info(
                                            f"[EXECUTOR] PARTIAL_CLOSE ticket override: "
                                            f"LLM chose {tkt_llm} but redirecting to worst-positioned "
                                            f"{_worst_tkt} (entry {_worst.get('price_open')}). "
                                            f"User policy: never close the best-positioned ticket."
                                        )
                                        tkt = _worst_tkt
                                tkt_profit = 0.0
                                tkt_lot = 0.0
                                for p in account.get('positions', []):
                                    if p.get('ticket') == tkt:
                                        tkt_profit = broker_position_pnl(p)
                                        tkt_lot = p.get('volume', p.get('lot', 0))
                                        break
                                expected_realized = tkt_profit * (pct / 100.0)
                                # 2026-05-06: ELIMINAT noise threshold de $20-30.
                                # Per a scalping de $5-12, parcials de $5-15 són
                                # normals i exactament el que volem assegurar.
                                # min_profit_usd hardcoded a $0.50 — només bloca
                                # parcials trivialment petits (sota frequencia normal).
                                min_profit_usd = 0.50
                                tkt_realized_non_positive = (expected_realized <= 0)
                                tkt_profit_too_small = (expected_realized > 0 and expected_realized < min_profit_usd)
                                # User policy 2026-04-30: manual closures must not
                                # crystallize net loss (only block negative or trivial).
                                if tkt_realized_non_positive:
                                    log.info(
                                        f"[EXECUTOR] PARTIAL_CLOSE blocked: expected realized "
                                        f"${expected_realized:.2f} <= $0.00 (no crystallize loss; auto_close handles invalidation)"
                                    )
                                elif tkt_profit_too_small:
                                    log.info(f"[EXECUTOR] PARTIAL_CLOSE blocked: ${expected_realized:.2f} trivially small (<$0.50)")
                                elif pct >= 100:
                                    close_ticket(tkt)
                                    log.info(f"[EXECUTOR] CLOSE TICKET {tkt} (full)")
                                    trade_history.log_event(
                                        type='FULL_CLOSE', ticket=tkt, direction=sig_state.get('direction', ''),
                                        lot=tkt_lot, price=price, source='EXECUTOR',
                                        pnl_delta=tkt_profit, reason=(reasoning or '')[:200]
                                    )
                                    sign = "+" if tkt_profit >= 0 else "−"
                                    notify("partial_close",
                                           f"💰 FULL CLOSE ticket#{tkt} · {sign}${abs(tkt_profit):.2f} "
                                           f"· reason: {(reasoning or '')[:60]}")
                                else:
                                    partial_close_pct(tkt, pct)
                                    log.info(f"[EXECUTOR] PARTIAL CLOSE ticket {tkt} ({pct}%)")
                                    realized = round(tkt_profit * pct / 100, 2)
                                    trade_history.log_event(
                                        type='PARTIAL_CLOSE', ticket=tkt, direction=sig_state.get('direction', ''),
                                        lot=round(tkt_lot * pct / 100, 3), price=price, source='EXECUTOR',
                                        pnl_delta=realized,
                                        reason=f"{pct}% — " + (reasoning or '')[:180]
                                    )
                                    sign = "+" if realized >= 0 else "−"
                                    notify("partial_close",
                                           f"💰 PARTIAL {pct}% ticket#{tkt} · {sign}${abs(realized):.2f} "
                                           f"· reason: {(reasoning or '')[:60]}")

                            elif action == "MOVE_SL_BE":
                                move_sl_entry()
                                sig_state.request_breakeven()
                                log.info(f"[EXECUTOR] MOVE_SL_BE executed")

                            elif action == "REDUCE_RISK":
                                # Defensive retreat: partial-close 50% UNIFORMLY
                                # across ALL tickets (preserves position structure),
                                # then move SL of all tickets to blend. Conf ≥ 0.75
                                # already validated above. Does NOT close the whole
                                # book — keeps exposure alive at half size.
                                # User policy 2026-04-30: NEVER close just the best
                                # ticket — that loses rebound capacity. Reduce
                                # proportionally so worst AND best both shrink.
                                _positions = account.get('positions') or []
                                _orders_rr = []
                                _real_total = 0.0
                                _rr_min_profit_usd = 0.50  # 2026-05-06: scalping ho permet petit
                                if _positions:
                                    def _tpr(p):
                                        try: return broker_position_pnl(p)
                                        except Exception: return 0.0
                                    for _p in _positions:
                                        try:
                                            _tk = int(_p.get('ticket') or _p.get('ticket_id') or 0)
                                            _lot_p = float(_p.get('volume', 0) or 0)
                                        except Exception:
                                            continue
                                        if not _tk or _lot_p < 0.01:
                                            continue
                                        _close_lot = round(_lot_p * 0.5, 2)
                                        _remain = round(_lot_p - _close_lot, 2)
                                        # Promote-to-full if either side falls below broker min (0.01)
                                        if _close_lot < 0.01 or _remain < 0.01:
                                            _orders_rr.append({"action": "CLOSE_TICKET", "ticket": _tk})
                                        else:
                                            _orders_rr.append({
                                                "action": "PARTIAL_CLOSE_PCT",
                                                "ticket": _tk,
                                                "pct": 50,
                                            })
                                        _real_total += _tpr(_p) * 0.5
                                if _real_total <= 0:
                                    log.info(
                                        f"[EXECUTOR] REDUCE_RISK blocked: expected realized "
                                        f"${_real_total:.2f} <= $0.00 (no manual red reduction; SL handles loss)"
                                    )
                                elif _real_total < _rr_min_profit_usd:
                                    log.info(
                                        f"[EXECUTOR] REDUCE_RISK blocked: expected realized "
                                        f"${_real_total:.2f} < min ${_rr_min_profit_usd:.2f} (noise threshold)"
                                    )
                                elif _orders_rr:
                                    write_order({"ts": int(time.time()), "orders": _orders_rr}, urgent=True)
                                    try:
                                        trade_history.log_event(
                                            type='PARTIAL_CLOSE', ticket=None,
                                            direction=sig_state.get('direction', ''),
                                            lot=None, price=price,
                                            pnl_delta=round(_real_total, 2),
                                            reasoning=f"REDUCE_RISK 50% uniform — {(reasoning or '')[:140]}",
                                            source='EXECUTOR_REDUCE_RISK',
                                        )
                                    except Exception:
                                        pass
                                    try:
                                        sign = "+" if _real_total >= 0 else "−"
                                        notify("partial_close",
                                               f"🛡️ REDUCE_RISK · 50% uniform on {len(_orders_rr)} tickets · "
                                               f"{sign}${abs(_real_total):.2f} · SL→blend · "
                                               f"{(reasoning or '')[:60]}")
                                    except Exception:
                                        pass
                                    # Always also move SL to blend (BE) so remaining tickets protected
                                    move_sl_entry()
                                    sig_state.request_breakeven()
                                    log.info(f"[EXECUTOR] REDUCE_RISK executed — 50% uniform + BE SL")

                            elif action == "ALERT":
                                log.info(f"[EXECUTOR] ALERT: {reasoning[:60]}")

                        elif action not in ("WAIT", "ALERT"):
                            log.info(f"[EXECUTOR] Low confidence ({conf:.0%}), not executing {action}")
                    else:
                        _exec_status = "ERROR"
                except Exception as e:
                    log.warning(f"EXECUTOR future error: {e}")
                    _exec_status = "ERROR"
                _executor_future = None
                last_executor = time.time()
                _llm_mark_done('executor')

            # Submit new EXECUTOR call if conditions met — EVENT-DRIVEN since commit 10.
            # Consumes the event_detector queue. If empty but heartbeat interval elapsed,
            # the detector emits a synthetic HEARTBEAT event so the trade still gets a
            # periodic reassessment even if the detector missed everything.
            if account.get('has_signal') and not account.get('closing'):
                # Dashboard force button — inject synthetic event to bypass cooldown
                if _executor_future is None and _consume_control('force_executor'):
                    log.info("[EXECUTOR] FORCE requested from dashboard")
                    try:
                        from event_detector import _make_event
                        event_detector._enqueue(_make_event('DASHBOARD_FORCE', {}))
                    except Exception:
                        pass
                    last_executor = 0  # bypass cooldown
                elif _executor_future is not None:
                    # Flag set while a previous call is still in-flight — log ONCE.
                    try:
                        if os.path.exists(_CTRL_FILE):
                            with open(_CTRL_FILE, 'r', encoding='utf-8') as _cf:
                                _pending = json.load(_cf)
                            if _pending.get('force_executor') and not getattr(main, '_force_exec_logged', False):
                                log.info("[EXECUTOR] FORCE queued (previous call still running)")
                                main._force_exec_logged = True
                            elif not _pending.get('force_executor'):
                                main._force_exec_logged = False
                    except Exception:
                        pass
                # Cooldown ONLY applies to synthetic HEARTBEAT events. Any real
                # event in the queue (structural, zone reached, position
                # change, TG message, etc.) fires the Executor immediately
                # regardless of when the last call was. HEARTBEAT alone +
                # WAIT-streak gets the doubled cooldown to avoid burning
                # tokens on "ask the same question" loops.
                _peek = event_detector.next_event()
                _peek_type = _peek.get('type') if _peek else None
                _is_real_event = _peek_type is not None and _peek_type != 'HEARTBEAT'

                if _is_real_event:
                    _proceed = True  # bypass cooldown for any real event
                else:
                    # Empty queue (heartbeat fallback path) or just HEARTBEAT.
                    # Apply cooldown + WAIT-streak doubling.
                    _eff_cooldown = event_detector.cooldown_seconds
                    try:
                        _streak = getattr(main, '_wait_streak', [])
                        if len(_streak) >= 3 and all(c >= 0.7 for _, c in _streak):
                            _eff_cooldown = event_detector.cooldown_seconds * 2
                    except Exception:
                        pass
                    _proceed = (time.time() - last_executor) >= _eff_cooldown

                if _executor_future is None and _proceed:
                    pending = event_detector.drain_pending(current_ts=time.time())
                    # Trading hours gate: skip LLM calls during off-hours UNLESS
                    # there's a non-HEARTBEAT event (structural events matter even at night).
                    if pending and not is_within_trading_hours():
                        structural = [e for e in pending if e.get('type') != 'HEARTBEAT']
                        if not structural:
                            pending = []  # suppress pure-heartbeat calls off-hours
                    if pending:
                        exec_prompt = build_executor_prompt(
                            bars_cache, account,
                            trigger_events=pending,
                            sig_state=sig_state,
                        )
                        if exec_prompt:
                            _exec_status = "CALLING"
                            _last_trigger_events = pending  # retained for post-response persistence
                            _executor_future = _ai_pool.submit(call_executor, exec_prompt)
                            _llm_mark_submit('executor')
                            event_detector.mark_executor_called(time.time())
                            try:
                                log_invoked(COMMON, pending)
                            except Exception:
                                pass
                elif _executor_future is not None:
                    _exec_status = "THINKING"

            # ── AUTONOMOUS STAGING — Executor in IDLE mode to pre-stage entries ──
            if not account.get('has_signal') and _staging_future is None:
                should_stage, stage_reason = should_run_executor_staging(
                    time.time(), last_staging_ts, bars_cache, sig_state,
                    last_staging_price, account,
                    staging_ctx=_staging_gate_ctx,
                )
                # Dashboard force button — overrides throttle (still requires IDLE).
                # force_executor also counts here: when in IDLE, forcing the executor
                # means "re-evaluate entries", which is exactly staging.
                if _consume_control('force_staging') or _consume_control('force_executor'):
                    should_stage, stage_reason = True, "FORCE (dashboard button)"
                if should_stage:
                    log.info(f"[STAGING] {stage_reason}")
                    exec_prompt = build_executor_prompt(
                        bars_cache, account,
                        trigger_events=[{'type': 'IDLE_STAGING', 'reason': stage_reason}],
                        sig_state=sig_state,
                    )
                    if exec_prompt:
                        # Prepend IDLE mode flag so the LLM knows to produce staged_setups
                        exec_prompt = ("MODE: IDLE — stage entry setups (no trade open).\n"
                                       "Respon amb schema IDLE (staged_setups array), no amb schema manage.\n\n"
                                       + exec_prompt)
                        _staging_future = _ai_pool.submit(call_executor, exec_prompt)
                        _llm_mark_submit('staging')
                        last_staging_ts = time.time()
                        last_staging_price = bars_cache[-1]['close'] if bars_cache else None

            # Handle staging future completion
            if _staging_future is not None and _staging_future.done():
                try:
                    stage_response = _staging_future.result(timeout=0.1)
                    if stage_response:
                        # Persist staging narrative for dashboard visibility
                        try:
                            _stage_log_path = os.path.join(COMMON, 'brain_staging_last.json')
                            with open(_stage_log_path, 'w', encoding='utf-8') as _slf:
                                json.dump({
                                    'ts': time.time(),
                                    'action': stage_response.get('action'),
                                    'reasoning': stage_response.get('reasoning', ''),
                                    'staged_setups': stage_response.get('staged_setups') or [],
                                    'mental_state': stage_response.get('mental_state'),
                                }, _slf, indent=2, ensure_ascii=False)
                        except Exception:
                            pass
                        # Derive top-level thesis/reasoning from the setups if the
                        # LLM only populated them at setup level (common with
                        # staging prompt — the "narrative" lives inside each setup).
                        _raw = stage_response.get('staged_setups') or []
                        _top_thesis = stage_response.get('thesis') or ''
                        _top_reason = stage_response.get('reasoning') or ''
                        _top_action = stage_response.get('action')
                        if _raw and not _top_reason:
                            parts = []
                            for s in _raw[:3]:
                                zn = s.get('zone_price') or s.get('zone') or s.get('price')
                                dr = s.get('direction', '?')
                                rs = (s.get('reasoning') or s.get('reason') or s.get('thesis') or '')[:160]
                                if zn and rs:
                                    parts.append(f"{dr}@{zn}: {rs}")
                            _top_reason = " | ".join(parts)
                        if _raw and not _top_thesis:
                            dirs = {s.get('direction') for s in _raw if s.get('direction')}
                            zones_txt = ", ".join(str(s.get('zone_price') or s.get('zone') or '?') for s in _raw[:4])
                            _top_thesis = f"Staged {len(_raw)} setups ({'/'.join(sorted(dirs))}): {zones_txt}"
                        if _raw and not _top_action:
                            _top_action = 'STAGE_SETUPS'
                        # ALSO append to executor_decisions.jsonl so the dashboard
                        # "last executor decision" panel reflects the latest IDLE
                        # staging call — otherwise it's stuck on the pre-IDLE trade.
                        try:
                            append_executor_decision({
                                'ts': time.time(),
                                'trade_id': 'IDLE_STAGING',
                                'mode': 'IDLE_STAGING',
                                'action': _top_action or 'WAIT',
                                'confidence': float(stage_response.get('confidence', 0) or 0),
                                'mental_state': stage_response.get('mental_state'),
                                'thesis': _top_thesis,
                                'reasoning': _top_reason,
                                'next_plan': stage_response.get('next_plan') or '',
                                'invalidation_condition': stage_response.get('invalidation_condition') or {},
                                'trigger_events': [{'type': 'IDLE_STAGING'}],
                                'staged_setups': _raw,
                                'response_raw': stage_response,
                            })
                        except Exception:
                            pass
                        raw_setups = stage_response.get('staged_setups') or []
                        # NORMALIZE first (maps zone→zone_price, conviction→confidence, etc.)
                        try:
                            import staged_setups as _ss_pre
                            normalized = [_ss_pre._normalize_setup(s) for s in raw_setups]
                            normalized = [s for s in normalized if s is not None]
                        except Exception as _ne:
                            log.warning(f"[STAGING] normalize pre-filter failed: {_ne}")
                            normalized = []
                        # 2026-05-05: ELIMINAT el filtre dur de confidence.
                        # Motiu filosòfic: era una regla determinista que matava
                        # setups que el LLM havia decidit qualitativament. Si
                        # l'LLM ha tornat STAGE amb conf=0.62, és el seu judici
                        # — si no estigués prou segur retornaria WAIT, no STAGE.
                        # La confidence numèrica queda com a INFORMACIÓ al setup
                        # (visible al dashboard, payload del watcher, etc.),
                        # no com a filtre.
                        new_setups = list(normalized)
                        if raw_setups:
                            _conf_list = [round(float(s.get('confidence', 0) or 0), 2)
                                          for s in normalized]
                            log.info(f"[STAGING] {len(raw_setups)} setup(s) acceptats — confs={_conf_list} (sense filtre dur)")
                        if new_setups:
                            try:
                                import staged_setups as _ss
                                # Tag as executor source so merge dedup works correctly
                                # (Hunter setups should NOT be wiped by Executor staging)
                                for _s in new_setups:
                                    _s.setdefault('source', 'executor')
                                # Wipe-and-replace: every Executor staging is
                                # authoritative for source='executor'. Hunter
                                # setups are preserved (different source).
                                # 2026-05-04: passar current_price perquè rebutgi
                                # setups amb geometria errònia (SELL sota preu = break-down).
                                _cur_price_for_geo = None
                                try:
                                    if bars_cache:
                                        _cur_price_for_geo = float(bars_cache[-1].get('close') or 0)
                                except Exception:
                                    pass
                                _ss.replace_for_source(new_setups, source='executor',
                                                       current_price=_cur_price_for_geo)
                                # Read back the NORMALIZED list from disk so we log
                                # canonical fields even if the LLM used aliases.
                                try:
                                    saved = _ss.load()
                                except Exception:
                                    saved = new_setups
                                def _zp(s):
                                    return s.get('zone_price') or s.get('price') or s.get('entry_price') or 0
                                log.info(f"[STAGING] Staged {len(saved)} setup(s): "
                                         + ", ".join(f"{s.get('direction','?')}@{_zp(s)} ({int(float(s.get('confidence',0))*100)}%)"
                                                      for s in saved))
                                # Redraw TV so the new Executor setup is visible
                                try:
                                    redraw_tv(bars_cache, account, sig_state)
                                except Exception:
                                    pass
                                # Use notify_update (pinned/editable message) instead of
                                # notify(): staged setups change every cycle and would
                                # otherwise spam the chat. The same TG message is edited
                                # in place — operator sees latest staging in one place.
                                try:
                                    _msg = "🎯 STAGED: " + " · ".join(
                                        f"{s.get('direction','?')} {float(_zp(s)):.1f} ({int(float(s.get('confidence',0))*100)}%)"
                                        for s in saved
                                    )
                                    notify_update("staged_executor", _msg)
                                except Exception:
                                    pass
                            except Exception as _se:
                                log.warning(f"[STAGING] save failed: {_se}")
                        else:
                            action = stage_response.get('action', 'WAIT')
                            rsn = (stage_response.get('reasoning') or '')[:120]
                            log.info(f"[STAGING] {action} — {rsn}")
                            # 2026-05-04: si EXECUTOR retorna sense setups (action=WAIT
                            # o staged_setups buit), esborrar els setups antics
                            # d'executor. Cada cycle reavalua: si ara no hi ha A+,
                            # els antics tampoc continuen vàlids.
                            try:
                                import staged_setups as _ss_clear
                                _existing = _ss_clear.load() or []
                                _executor_setups = [s for s in _existing if (s.get('source') or '').lower() == 'executor']
                                if _executor_setups:
                                    log.info(
                                        f"[STAGING] EXECUTOR no proposa setups aquest cycle "
                                        f"({action}) — esborrant {len(_executor_setups)} setup(s) antic(s) d'executor"
                                    )
                                    _ss_clear.replace_for_source([], source='executor')
                                    try:
                                        notify_update_clear('staged_executor')
                                    except Exception:
                                        pass
                            except Exception as _ce:
                                log.warning(f"[STAGING] clear-old failed: {_ce}")
                except Exception as _sfe:
                    log.warning(f"[STAGING] future error: {_sfe}")
                finally:
                    _staging_future = None
                    _llm_mark_done('staging')

            # ══════════════════════════════════════════════════════════════
            # HUNTER — DESACTIVAT 2026-05-04 (Mode Recorregut Institucional).
            # Era scanner DeepSeek de scalps reversion en paral·lel a l'Executor.
            # Eliminat per simplificar: l'EXECUTOR és l'única font de setups.
            # Codi conservat com a referència, però short-circuit a None.
            # ══════════════════════════════════════════════════════════════
            if False and _hunter_future is None:
                pass  # disabled

            # Handle Hunter future completion
            if _hunter_future is not None and _hunter_future.done():
                try:
                    _h_resp = _hunter_future.result(timeout=0.1)
                    if _h_resp:
                        raw_h_setups = _h_resp.get('setups') or []
                        no_setup_reason = _h_resp.get('no_setups_reason') or ''
                        if not raw_h_setups:
                            log.info(f"[HUNTER] no setups — {no_setup_reason[:120]}")
                        else:
                            try:
                                _hcfg = (_load_app_config().get('hunter', {}) or {})
                            except Exception:
                                _hcfg = {}
                            min_conf = float(_hcfg.get('min_confidence', 0.70))
                            tgt_min = float(_hcfg.get('target_distance_min', 8))
                            tgt_max = float(_hcfg.get('target_distance_max', 12))
                            stop_max = float(_hcfg.get('stop_distance_max', 5))
                            min_dist_price = float(_hcfg.get('min_distance_from_price', 3))
                            exp_idle = int(_hcfg.get('expiration_idle_min', 20))
                            exp_active = int(_hcfg.get('expiration_active_min', 30))
                            cur_price = bars_cache[-1].get('close', 0) if bars_cache else 0
                            regime_now = None
                            try:
                                from zone_store import read_state as _rs
                                regime_now = (_rs(COMMON) or {}).get('regime')
                            except Exception:
                                pass
                            has_sig = bool(account and account.get('has_signal'))
                            active_dir = sig_state.get('direction') if (sig_state and has_sig) else None

                            kept = []
                            for s in raw_h_setups:
                                # Required fields + sanity
                                dirn = s.get('direction')
                                zp = s.get('trigger_zone') or s.get('zone_price')
                                pt = s.get('profit_target')
                                inv = s.get('invalidation') or s.get('invalidation_price')
                                try:
                                    zp = float(zp) if zp is not None else None
                                    pt = float(pt) if pt is not None else None
                                    inv = float(inv) if inv is not None else None
                                except Exception:
                                    continue
                                if dirn not in ('BUY', 'SELL') or not (zp and pt and inv):
                                    continue
                                # Distance filters
                                target_dist = abs(pt - zp)
                                stop_dist = abs(inv - zp)
                                if target_dist < tgt_min or target_dist > tgt_max:
                                    log.info(f"[HUNTER] reject {dirn}@{zp} — target_dist {target_dist:.1f} outside [{tgt_min},{tgt_max}]")
                                    continue
                                if stop_dist > stop_max:
                                    log.info(f"[HUNTER] reject {dirn}@{zp} — stop_dist {stop_dist:.1f} > {stop_max}")
                                    continue
                                # Distance from current price (broker stop-level)
                                if abs(cur_price - zp) < min_dist_price:
                                    log.info(f"[HUNTER] reject {dirn}@{zp} — too close to price ({abs(cur_price - zp):.2f}$ < {min_dist_price}$)")
                                    continue
                                # Anchor check: trigger_zone MUST be within $2 of a real
                                # structural reference (active zone, HTF pivot, swing point).
                                # Prevents Hunter from inventing prices out of thin air.
                                try:
                                    anchors = []
                                    # Active zones
                                    try:
                                        from zone_store import active_zones as _az, read_state as _rs
                                        for _z in _az(_rs(COMMON)):
                                            anchors.append(('zone', float(_z.get('price', 0) or 0)))
                                    except Exception:
                                        pass
                                    # HTF pivots from market_context
                                    try:
                                        from market_context import build_market_context as _bmc
                                        _mc = _bmc(bars_cache, account, tv_helper=None,
                                                    now_utc=datetime.now(timezone.utc), for_executor=True) or {}
                                        _htf = (_mc.get('htf') or {})
                                        for k in ('d1_high', 'd1_low', 'd1_close', 'weekly_open',
                                                  'weekly_high', 'weekly_low', 'nearest_round'):
                                            v = _htf.get(k)
                                            if v:
                                                try:
                                                    anchors.append((f'htf_{k}', float(v)))
                                                except Exception:
                                                    pass
                                    except Exception:
                                        pass
                                    # Nearest anchor distance
                                    if anchors:
                                        nearest_name, nearest_d = None, 1e9
                                        for label_, ap in anchors:
                                            d = abs(ap - zp)
                                            if d < nearest_d:
                                                nearest_d, nearest_name = d, f"{label_}@{ap:.2f}"
                                        ANCHOR_MAX_DIST = 2.0  # dollar distance
                                        if nearest_d > ANCHOR_MAX_DIST:
                                            log.info(f"[HUNTER] reject {dirn}@{zp} — no structural anchor within ${ANCHOR_MAX_DIST} (nearest: {nearest_name}, {nearest_d:.2f}$ away)")
                                            continue
                                        s['anchored_to'] = nearest_name
                                        s['anchor_distance_usd'] = round(nearest_d, 2)
                                except Exception as _ae:
                                    log.debug(f"[HUNTER] anchor check error: {_ae}")
                                # Confidence filter
                                try:
                                    conf = float(s.get('confidence', 0) or 0)
                                except Exception:
                                    conf = 0
                                if conf < min_conf:
                                    continue
                                # Alt hypothesis: only opposite direction while trade active
                                if has_sig:
                                    if dirn == active_dir:
                                        continue
                                    s['post_close'] = True
                                    s['expiration_minutes'] = exp_active
                                else:
                                    s['post_close'] = False
                                    s['expiration_minutes'] = exp_idle
                                # Hunter marker fields
                                s['source'] = 'hunter'
                                s['regime_at_stage'] = regime_now
                                s['zone_price'] = zp  # normalize name
                                s['invalidation_price'] = inv
                                s['target_distance_usd'] = round(target_dist, 2)
                                s['stop_distance_usd'] = round(stop_dist, 2)
                                kept.append(s)

                            if kept:
                                try:
                                    import staged_setups as _ss
                                    bars_m15 = aggregate_bars(bars_cache, 3)
                                    atr_m15_val = atr(bars_m15, 14) or 5.0
                                    _ss.add_setups_merge(kept, atr_m15=atr_m15_val)
                                    # Stats record
                                    try:
                                        import hunter_stats as hs
                                        for s in kept:
                                            hs.record_stage(s)
                                    except Exception:
                                        pass
                                    log.info(f"[HUNTER] Staged {len(kept)} setup(s): "
                                             + ", ".join(f"{s.get('direction')}@{s.get('zone_price')} "
                                                         f"(conf={int(float(s.get('confidence',0))*100)}%"
                                                         f"{', post_close' if s.get('post_close') else ''})"
                                                         for s in kept))
                                    # Redraw TV so the new Hunter setup is visible immediately
                                    try:
                                        redraw_tv(bars_cache, account, sig_state)
                                    except Exception:
                                        pass
                                    # Pinned/editable message for Hunter setups (same
                                    # rationale as Executor staged: avoid TG spam).
                                    # 2026-05-04: SUPPRESS si Executor ja té un setup en
                                    # la mateixa zona+direcció (±$2) — eviten 2 missatges TG
                                    # per la mateixa idea de trade.
                                    try:
                                        try:
                                            import staged_setups as _ss2
                                            _all_existing = _ss2.load() or []
                                        except Exception:
                                            _all_existing = []
                                        _kept_filtered = []
                                        for s in kept:
                                            zp = float(s.get('zone_price', 0) or 0)
                                            d = s.get('direction')
                                            _dup = False
                                            for ex in _all_existing:
                                                if ex.get('source') == 'hunter':
                                                    continue  # skip self
                                                if ex.get('direction') != d:
                                                    continue
                                                _ex_zp = float(ex.get('zone_price', 0) or 0)
                                                if abs(_ex_zp - zp) <= 2.0:
                                                    _dup = True
                                                    break
                                            if not _dup:
                                                _kept_filtered.append(s)
                                        if _kept_filtered:
                                            _h_msg = "🏹 HUNTER: " + " · ".join(
                                                f"{s.get('direction')} {float(s.get('zone_price',0)):.1f}"
                                                + (' [post_close]' if s.get('post_close') else '')
                                                for s in _kept_filtered
                                            )
                                            notify_update("staged_hunter", _h_msg)
                                        else:
                                            log.info(f"[HUNTER] TG alert suppressed — {len(kept)} setup(s) duplicat de Executor (mateixa zona ±$2)")
                                    except Exception:
                                        pass
                                except Exception as _ae:
                                    log.warning(f"[HUNTER] add_setups_merge failed: {_ae}")
                            else:
                                log.info(f"[HUNTER] {len(raw_h_setups)} raw setups, 0 kept after filters")
                except Exception as _hfe:
                    log.warning(f"[HUNTER] future error: {_hfe}")
                finally:
                    _hunter_future = None
                    _llm_mark_done('hunter')

            # ── LLM fallback auto-restore (throttled internally to 60s) ──
            try:
                import llm_fallback
                llm_fallback.check_restore()
            except Exception:
                pass

            # ── Sleep ──
            elapsed = time.time() - t0
            time.sleep(max(0.5, FAST_INTERVAL - elapsed))

    except KeyboardInterrupt:
        pass
    finally:
        cleanup_pid()
        log.info("Stopped.")


if __name__ == '__main__':
    main()
