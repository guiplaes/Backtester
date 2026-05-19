"""SQLite logger for grid manager — includes ledger for true profit tracking."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from config import DB_PATH


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
    -- State snapshots (every 5 min)
    CREATE TABLE IF NOT EXISTS state_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        price REAL,
        bot_top REAL,
        bot_bottom REAL,
        dist_top_pct REAL,
        dist_bottom_pct REAL,
        grid_profit REAL,
        realized_profit REAL,
        status TEXT,
        raw_json TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON state_snapshots(ts);

    -- Decisions logged from Claude
    CREATE TABLE IF NOT EXISTS decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        trigger TEXT,
        bot_id TEXT,
        price REAL,
        old_top REAL,
        old_bottom REAL,
        new_top REAL,
        new_bottom REAL,
        action TEXT,
        claude_reasoning TEXT,
        cost_estimated REAL,
        executed INTEGER DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts);

    -- LEDGER: track each bot lifetime (epoch)
    CREATE TABLE IF NOT EXISTS epochs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_id TEXT NOT NULL,
        opened_ts TEXT NOT NULL,
        closed_ts TEXT,
        initial_capital_usdt REAL,
        initial_paxg REAL,
        initial_paxg_price REAL,
        closing_paxg REAL,
        closing_paxg_price REAL,
        closing_usdt REAL,
        cycles_completed INTEGER DEFAULT 0,
        grid_profit_reported REAL,
        cost_to_create REAL DEFAULT 0,
        cost_to_close REAL DEFAULT 0,
        true_net_pnl REAL,
        notes TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_epochs_bot ON epochs(bot_id);

    -- Manual ledger transactions: deposits, withdrawals, transfers
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        type TEXT NOT NULL,
        amount_usdt REAL,
        amount_paxg REAL,
        price_at_tx REAL,
        notes TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_tx_ts ON transactions(ts);

    -- Daily summary
    CREATE TABLE IF NOT EXISTS daily_summary (
        date TEXT PRIMARY KEY,
        cycles_completed INTEGER,
        grid_profit REAL,
        adjusts INTEGER,
        adjust_cost REAL,
        net_profit REAL,
        notes TEXT
    );
    """)
    con.commit()
    return con


# ─── Snapshot / Decision logging ────────────────────────────────────
def log_snapshot(snapshot: dict):
    con = init_db()
    con.execute("""
        INSERT INTO state_snapshots
        (ts, price, bot_top, bot_bottom, dist_top_pct, dist_bottom_pct, grid_profit, realized_profit, status, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now(timezone.utc).isoformat(),
        snapshot.get("price"), snapshot.get("top"), snapshot.get("bottom"),
        snapshot.get("dist_to_top_pct"), snapshot.get("dist_to_bottom_pct"),
        snapshot.get("grid_profit"), snapshot.get("realized_profit"),
        snapshot.get("status"), json.dumps(snapshot),
    ))
    con.commit(); con.close()


def log_decision(trigger, bot_id, snapshot, action, reasoning,
                 new_range=None, cost=0, executed=False):
    con = init_db()
    new_top, new_bottom = (new_range or (None, None))
    con.execute("""
        INSERT INTO decisions
        (ts, trigger, bot_id, price, old_top, old_bottom, new_top, new_bottom, action, claude_reasoning, cost_estimated, executed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (datetime.now(timezone.utc).isoformat(), trigger, bot_id,
          snapshot.get("price"), snapshot.get("top"), snapshot.get("bottom"),
          new_top, new_bottom, action, reasoning, cost, 1 if executed else 0))
    con.commit(); con.close()


# ─── LEDGER ─────────────────────────────────────────────────────────
def log_deposit(amount_usdt: float = 0, amount_paxg: float = 0, price: float = 0, notes: str = ""):
    """Record a deposit (money added to account)."""
    con = init_db()
    con.execute("""
        INSERT INTO transactions (ts, type, amount_usdt, amount_paxg, price_at_tx, notes)
        VALUES (?, 'DEPOSIT', ?, ?, ?, ?)
    """, (datetime.now(timezone.utc).isoformat(), amount_usdt, amount_paxg, price, notes))
    con.commit(); con.close()


def log_withdrawal(amount_usdt: float = 0, amount_paxg: float = 0, price: float = 0, notes: str = ""):
    """Record a withdrawal (money taken out)."""
    con = init_db()
    con.execute("""
        INSERT INTO transactions (ts, type, amount_usdt, amount_paxg, price_at_tx, notes)
        VALUES (?, 'WITHDRAWAL', ?, ?, ?, ?)
    """, (datetime.now(timezone.utc).isoformat(), amount_usdt, amount_paxg, price, notes))
    con.commit(); con.close()


def _ensure_symbol_column(con):
    """Add 'symbol' column to epochs if it doesn't exist (idempotent migration)."""
    cur = con.execute("PRAGMA table_info(epochs)")
    cols = [row[1] for row in cur.fetchall()]
    if "symbol" not in cols:
        con.execute("ALTER TABLE epochs ADD COLUMN symbol TEXT")
        con.commit()


def open_epoch(bot_id: str, capital_usdt: float, paxg_amount: float = 0,
               price: float = 0, cost_to_create: float = 0, symbol: str = ""):
    """Record that a new bot was created (start of an epoch).
    symbol: e.g. 'PAXG_USDT' — preserves cumulative tracking across bot recreations.
    """
    con = init_db()
    _ensure_symbol_column(con)
    con.execute("""
        INSERT INTO epochs (bot_id, opened_ts, initial_capital_usdt, initial_paxg,
                            initial_paxg_price, cost_to_create, symbol)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (bot_id, datetime.now(timezone.utc).isoformat(),
          capital_usdt, paxg_amount, price, cost_to_create, symbol))
    con.commit(); con.close()


def log_recolocation_cost(bot_id: str, bot_name: str, trigger: str, price: float,
                          new_top: float, new_bottom: float,
                          grid_profit_before: float, grid_profit_after: float,
                          fee_pool_before: float, fee_pool_after: float):
    """Registra una recolocació amb els costos reals abans/després.

    NOTA (fix 2026-05-15): els camps fee_pool_* reciclen el seu significat
    i ara guarden el CONSUM ACUMULAT de fees (= reserve - remain), no el remain.
    El consum es monoton creixent, aixi fee_pool_after >= fee_pool_before.

    grid_profit_delta = grid_profit_after - grid_profit_before
                        (POSITIU si Pionex ha sumat profit; NEGATIU si li ha restat)
    fee_cost_delta    = fee_pool_after - fee_pool_before
                        (POSITIU = fees consumides durant la recolocacio)
    total_cost_usdt   = fee_cost_delta + max(0, -grid_profit_delta)
                        Cost real total en USDT de la recolocacio.
    """
    grid_delta = grid_profit_after - grid_profit_before
    fee_delta = fee_pool_after - fee_pool_before  # POSITIU = fees consumides
    total_cost = max(0.0, fee_delta) + max(0.0, -grid_delta)

    con = init_db()
    con.execute("""
        INSERT INTO recolocation_costs
        (ts, bot_id, bot_name, trigger, price, new_top, new_bottom,
         grid_profit_before, grid_profit_after, grid_profit_delta,
         fee_pool_before, fee_pool_after, fee_cost_delta, total_cost_usdt)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (datetime.now(timezone.utc).isoformat(), bot_id, bot_name, trigger,
          price, new_top, new_bottom,
          grid_profit_before, grid_profit_after, grid_delta,
          fee_pool_before, fee_pool_after, fee_delta, total_cost))
    con.commit(); con.close()


def get_total_recolocation_cost() -> dict:
    """Retorna el cost total acumulat de totes les recolocacions registrades.
    {"total_usdt": float, "count": int, "fee_consumed": float, "profit_reduced": float}
    """
    con = init_db()
    cur = con.execute("""
        SELECT COALESCE(SUM(total_cost_usdt), 0),
               COUNT(*),
               COALESCE(SUM(fee_cost_delta), 0),
               COALESCE(SUM(CASE WHEN grid_profit_delta < 0 THEN -grid_profit_delta ELSE 0 END), 0)
        FROM recolocation_costs
    """)
    row = cur.fetchone()
    con.close()
    return {
        "total_usdt": float(row[0] or 0),
        "count": int(row[1] or 0),
        "fee_consumed": float(row[2] or 0),
        "profit_reduced": float(row[3] or 0),
    }


def get_cumulative_stats_for_symbol(symbol: str) -> dict:
    """Sum grid_profit_reported and cycles_completed from all CLOSED epochs for a symbol.
    Returns: {"profit": float, "cycles": int, "epochs_count": int}
    """
    con = init_db()
    _ensure_symbol_column(con)
    cur = con.execute("""
        SELECT COALESCE(SUM(grid_profit_reported), 0) AS p,
               COALESCE(SUM(cycles_completed), 0)   AS c,
               COUNT(*) AS n
        FROM epochs
        WHERE symbol = ? AND closed_ts IS NOT NULL
    """, (symbol,))
    row = cur.fetchone()
    con.close()
    return {"profit": float(row[0] or 0), "cycles": int(row[1] or 0), "epochs_count": int(row[2] or 0)}


def close_epoch(bot_id: str, closing_usdt: float, closing_paxg: float = 0,
                closing_price: float = 0, cost_to_close: float = 0,
                grid_profit_reported: float = 0, cycles: int = 0, notes: str = ""):
    """Record that a bot was closed (end of an epoch)."""
    con = init_db()
    # Get the latest open epoch
    cur = con.execute("""
        SELECT id, initial_capital_usdt, initial_paxg, initial_paxg_price, cost_to_create
        FROM epochs WHERE bot_id = ? AND closed_ts IS NULL
        ORDER BY id DESC LIMIT 1
    """, (bot_id,))
    row = cur.fetchone()
    if not row:
        con.close()
        raise ValueError(f"No open epoch for bot {bot_id}")
    epoch_id, init_usdt, init_paxg, init_price, cost_create = row

    # True net PnL = (closing total value at closing price) - (initial value at initial price)
    initial_total_value = (init_usdt or 0) + (init_paxg or 0) * (init_price or 0)
    closing_total_value = (closing_usdt or 0) + (closing_paxg or 0) * (closing_price or 0)
    true_net = closing_total_value - initial_total_value - cost_create - cost_to_close

    con.execute("""
        UPDATE epochs SET
            closed_ts = ?, closing_paxg = ?, closing_paxg_price = ?,
            closing_usdt = ?, cycles_completed = ?, grid_profit_reported = ?,
            cost_to_close = ?, true_net_pnl = ?, notes = ?
        WHERE id = ?
    """, (datetime.now(timezone.utc).isoformat(), closing_paxg, closing_price,
          closing_usdt, cycles, grid_profit_reported, cost_to_close,
          true_net, notes, epoch_id))
    con.commit(); con.close()


def compute_true_total_profit(current_balance_usdt: float = 0,
                              current_paxg: float = 0,
                              current_price: float = 0) -> dict:
    """
    Calculate true total profit:
    = (current total value) - (total deposits in value terms) + (total withdrawals)
    Accounts for all bot recreations and any Pionex profit-resets.
    """
    con = init_db()
    # Total deposits
    cur = con.execute("""
        SELECT COALESCE(SUM(amount_usdt), 0) + COALESCE(SUM(amount_paxg * price_at_tx), 0)
        FROM transactions WHERE type = 'DEPOSIT'
    """)
    total_deposits = cur.fetchone()[0] or 0

    # Total withdrawals
    cur = con.execute("""
        SELECT COALESCE(SUM(amount_usdt), 0) + COALESCE(SUM(amount_paxg * price_at_tx), 0)
        FROM transactions WHERE type = 'WITHDRAWAL'
    """)
    total_withdrawals = cur.fetchone()[0] or 0

    # Sum of all closed epoch PnLs (for historical breakdown)
    cur = con.execute("""SELECT COALESCE(SUM(true_net_pnl), 0) FROM epochs WHERE closed_ts IS NOT NULL""")
    closed_epochs_pnl = cur.fetchone()[0] or 0

    # Count cycles across all epochs
    cur = con.execute("""SELECT COALESCE(SUM(cycles_completed), 0) FROM epochs""")
    total_cycles = cur.fetchone()[0] or 0

    # Total cost of all adjustments
    cur = con.execute("""SELECT COALESCE(SUM(cost_to_create + cost_to_close), 0) FROM epochs""")
    total_adjust_cost = cur.fetchone()[0] or 0

    con.close()

    # Current portfolio value
    current_value = current_balance_usdt + current_paxg * current_price

    # True net profit = current value - deposits + withdrawals
    true_net = current_value - total_deposits + total_withdrawals

    return {
        "total_deposits_usdt_equiv": total_deposits,
        "total_withdrawals_usdt_equiv": total_withdrawals,
        "current_total_value": current_value,
        "true_net_profit": true_net,
        "closed_epochs_pnl_sum": closed_epochs_pnl,
        "total_cycles_all_epochs": int(total_cycles),
        "total_adjust_cost": total_adjust_cost,
        "roi_pct": (true_net / total_deposits * 100) if total_deposits > 0 else 0,
    }


# ─── Query helpers ──────────────────────────────────────────────────
def get_recent_snapshots(limit: int = 100):
    con = init_db()
    cur = con.execute("SELECT * FROM state_snapshots ORDER BY ts DESC LIMIT ?", (limit,))
    rows = [dict(zip([c[0] for c in cur.description], row)) for row in cur.fetchall()]
    con.close()
    return rows


def get_recent_decisions(limit: int = 50):
    con = init_db()
    cur = con.execute("SELECT * FROM decisions ORDER BY ts DESC LIMIT ?", (limit,))
    rows = [dict(zip([c[0] for c in cur.description], row)) for row in cur.fetchall()]
    con.close()
    return rows


def get_all_epochs():
    con = init_db()
    cur = con.execute("SELECT * FROM epochs ORDER BY id DESC")
    rows = [dict(zip([c[0] for c in cur.description], row)) for row in cur.fetchall()]
    con.close()
    return rows


def get_all_transactions():
    con = init_db()
    cur = con.execute("SELECT * FROM transactions ORDER BY ts DESC")
    rows = [dict(zip([c[0] for c in cur.description], row)) for row in cur.fetchall()]
    con.close()
    return rows


if __name__ == "__main__":
    init_db()
    print(f"DB ready at {DB_PATH}")
    # Initialize current state — register the existing $400 deposit + first epoch
    # Run only once on setup
