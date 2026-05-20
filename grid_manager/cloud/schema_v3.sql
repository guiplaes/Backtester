-- ═══════════════════════════════════════════════════════════════════════
-- Schema v3 — tot el sistema operatiu queda registrat a Neon
-- Idempotent: pots executar-ho diverses vegades.
-- ═══════════════════════════════════════════════════════════════════════

-- ─── grid_trades: TOT fill individual dels grids ─────────────────────
-- Cada execució (buy o sell) de cada nivell del grid queda aquí.
-- Permet analitzar volum, fees pagades, distribució de fills, etc.
CREATE TABLE IF NOT EXISTS grid_trades (
    id BIGSERIAL PRIMARY KEY,
    pionex_fill_id BIGINT UNIQUE NOT NULL,     -- idempotency
    pionex_order_id BIGINT NOT NULL,
    bot_id TEXT REFERENCES bots(bot_id),
    bot_name TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    role TEXT,                                  -- TAKER / MAKER
    price NUMERIC(30,12) NOT NULL,
    size NUMERIC(30,12) NOT NULL,
    quote_value NUMERIC(30,12),                 -- price × size
    fee NUMERIC(30,12),
    fee_coin TEXT,
    ts TIMESTAMPTZ NOT NULL                     -- timestamp Pionex
);

CREATE INDEX IF NOT EXISTS idx_trades_bot_ts ON grid_trades(bot_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_trades_symbol_ts ON grid_trades(symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_trades_ts ON grid_trades(ts DESC);


-- ─── operation_log: cada acció del sistema ───────────────────────────
-- monitor.py, rebalancer.py, weekly_rebalance.py, daily_snapshot.py,
-- reconcile.py, sync_health.py, live-refresh endpoint, etc.
-- Aquesta taula ES la traçabilitat operativa.
CREATE TABLE IF NOT EXISTS operation_log (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    component TEXT NOT NULL,                    -- 'monitor', 'rebalancer', etc
    operation TEXT NOT NULL,                    -- 'recolocation', 'rebalance', 'reinvest', etc
    bot_id TEXT REFERENCES bots(bot_id),
    bot_name TEXT,
    status TEXT NOT NULL CHECK (status IN ('success','partial','failed','skipped')),
    duration_ms INT,
    details JSONB,                              -- camp lliure per detalls
    error_msg TEXT
);

CREATE INDEX IF NOT EXISTS idx_oplog_ts ON operation_log(ts DESC);
CREATE INDEX IF NOT EXISTS idx_oplog_comp_op ON operation_log(component, operation);
CREATE INDEX IF NOT EXISTS idx_oplog_bot ON operation_log(bot_id);


-- ─── cron_runs: traceabilitat de tasques programades ─────────────────
-- Quan ha corregut cada cron i si va anar bé.
CREATE TABLE IF NOT EXISTS cron_runs (
    id BIGSERIAL PRIMARY KEY,
    task_name TEXT NOT NULL,                    -- 'sync_health', 'daily_snapshot', etc
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    duration_ms INT,
    status TEXT CHECK (status IN ('success','partial','failed')),
    items_processed INT,
    notes TEXT,
    error_msg TEXT
);

CREATE INDEX IF NOT EXISTS idx_cron_task_ts ON cron_runs(task_name, started_at DESC);


-- ─── bot_lifecycle_events: registre de crear/tancar bots ─────────────
-- Quan detectem que un bot s'ha tancat o creat a Pionex (manualment o auto).
CREATE TABLE IF NOT EXISTS bot_lifecycle_events (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    bot_id TEXT NOT NULL,
    bot_name TEXT,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'detected_created',     -- bot nou trobat a Pionex (no estava al nostre DB)
        'detected_closed',      -- bot al DB ja no apareix a Pionex
        'manual_close',         -- tancament manual via web/script
        'manual_recreate',      -- recreate del mateix símbol
        'config_change'         -- canvi width/rows/threshold
    )),
    last_grid_profit NUMERIC(20,8),             -- gridProfit al moment del event
    last_quote_invested NUMERIC(20,8),
    notes TEXT,
    detected_by TEXT                            -- 'sync_health', 'manual', etc
);

CREATE INDEX IF NOT EXISTS idx_lifecycle_bot_ts ON bot_lifecycle_events(bot_id, ts DESC);


-- ─── Mark version ────────────────────────────────────────────────────
INSERT INTO schema_version (version, notes)
VALUES (3, 'v3: grid_trades, operation_log, cron_runs, bot_lifecycle_events')
ON CONFLICT (version) DO NOTHING;
