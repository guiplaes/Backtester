-- ═══════════════════════════════════════════════════════════════════════
-- Schema v2 — taules per al dashboard Next.js (parity total amb Streamlit)
-- Idempotent: pots executar-ho diverses vegades.
-- ═══════════════════════════════════════════════════════════════════════

-- ─── B1: wallet_snapshots ────────────────────────────────────────────
-- Snapshot del balance del wallet Pionex (USDT free, BTC free, etc).
-- L'escriu el daily_snapshot.py al servidor Windows.
CREATE TABLE IF NOT EXISTS wallet_snapshots (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    coin TEXT NOT NULL,                       -- 'USDT', 'BTC', 'ETH', etc
    free NUMERIC(30,12) NOT NULL DEFAULT 0,
    frozen NUMERIC(30,12) NOT NULL DEFAULT 0,
    value_usdt NUMERIC(20,8),                 -- valor estimat en USDT (free × preu)
    source TEXT NOT NULL DEFAULT 'daily_snapshot'
);
CREATE INDEX IF NOT EXISTS idx_wallet_ts ON wallet_snapshots(ts DESC);
CREATE INDEX IF NOT EXISTS idx_wallet_coin_ts ON wallet_snapshots(coin, ts DESC);


-- ─── B2: system_health ──────────────────────────────────────────────
-- Heartbeat del monitor.py i altres processos del sistema.
-- Cada execució del monitor escriu una fila.
CREATE TABLE IF NOT EXISTS system_health (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    component TEXT NOT NULL,                  -- 'monitor', 'rebalancer', 'weekly_rebalance', 'reconcile', 'daily_snapshot'
    status TEXT NOT NULL CHECK (status IN ('ok','warn','error','running')),
    last_cycle_ms INT,                        -- duració del cicle
    triggers_today INT DEFAULT 0,             -- només per monitor: nombre de TRIGGER avui
    adjusts_ok_today INT DEFAULT 0,
    adjusts_fail_today INT DEFAULT 0,
    last_trigger_text TEXT,                   -- línia raw de l'últim TRIGGER
    last_adjust_text TEXT,
    error_msg TEXT,
    extra JSONB                               -- camps lliures per a futur
);
CREATE INDEX IF NOT EXISTS idx_health_component_ts ON system_health(component, ts DESC);


-- ─── B3: monitor_log ─────────────────────────────────────────────────
-- Últimes N línies del monitor.log (les MÉS RECENTS, no totes).
-- El monitor escriu cada N cicles (per evitar saturar Neon amb logs).
CREATE TABLE IF NOT EXISTS monitor_log (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    level TEXT,                                -- 'INFO', 'WARNING', 'ERROR'
    component TEXT,                            -- 'monitor', 'rebalance', etc
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mon_log_ts ON monitor_log(ts DESC);


-- ─── B4: price_snapshots ─────────────────────────────────────────────
-- Snapshot horari de preus dels assets que tenim a bots.
-- Pel chart històric de preus per bot.
CREATE TABLE IF NOT EXISTS price_snapshots (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol TEXT NOT NULL,                     -- 'BTC_USDT', 'ETH_USDT', ...
    price NUMERIC(30,12) NOT NULL,
    bot_top NUMERIC(30,12),                   -- rang del bot en aquest moment
    bot_bottom NUMERIC(30,12)
);
CREATE INDEX IF NOT EXISTS idx_price_symbol_ts ON price_snapshots(symbol, ts DESC);


-- ─── MT5 strategy state ──────────────────────────────────────────────
-- Estat actual de l'EA MT5 XAUUSD (per a la tab dedicada).
CREATE TABLE IF NOT EXISTS mt5_state (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    available BOOLEAN NOT NULL,
    equity NUMERIC(20,8),
    balance NUMERIC(20,8),
    floating_pnl NUMERIC(20,8),
    daily_profit NUMERIC(20,8),
    cum_profit NUMERIC(20,8),
    open_positions INT,
    raw_json JSONB
);
CREATE INDEX IF NOT EXISTS idx_mt5_ts ON mt5_state(ts DESC);


-- ─── Mark version ────────────────────────────────────────────────────
INSERT INTO schema_version (version, notes)
VALUES (2, 'v2: wallet_snapshots, system_health, monitor_log, price_snapshots, mt5_state')
ON CONFLICT (version) DO NOTHING;
