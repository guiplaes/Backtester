-- ═══════════════════════════════════════════════════════════════════════
-- Grid Portfolio — Neon Postgres schema v1
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS schema_version (
    version INT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes TEXT
);

-- ─── Bots master ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bots (
    bot_id TEXT PRIMARY KEY,                 -- Pionex buOrderId (UUID format)
    name TEXT UNIQUE NOT NULL,               -- 'PAXG_USDT', etc
    base TEXT NOT NULL,
    quote TEXT NOT NULL,
    pionex_strategy_id INT,
    created_at TIMESTAMPTZ NOT NULL,
    closed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','closed','paused')),
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_bots_name ON bots(name);
CREATE INDEX IF NOT EXISTS idx_bots_status ON bots(status);

-- ─── Capital events: ledger immutable de moviments de capital ───────
-- Cada DEPOSIT/WITHDRAW/INVEST_IN/REDUCE/REINVEST queda aquí amb
-- verificació empírica (qti_before/qti_after) i idempotency key.
CREATE TABLE IF NOT EXISTS capital_events (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    bot_id TEXT NOT NULL REFERENCES bots(bot_id),
    bot_name TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'create',
        'invest_in',
        'reduce',
        'reinvest_profit',
        'withdraw_profit',
        'close',
        'rebalance_in',
        'rebalance_out',
        'deposit_external',
        'withdraw_external'
    )),
    amount_usdt NUMERIC(20,8) NOT NULL,
    qti_before NUMERIC(20,8),                    -- Pionex quoteTotalInvestment abans (verificat)
    qti_after NUMERIC(20,8),                     -- després (verificat empíricament)
    grid_profit_snapshot NUMERIC(20,8),          -- gridProfit Pionex al moment de l'event
    lifetime_profit_calc NUMERIC(20,8),          -- el nostre comptador acumulat lifetime (independent de Pionex)
    source TEXT NOT NULL,                        -- 'manual'|'weekly_cron'|'rebalancer'|'monitor'|'backfill'
    idempotency_key TEXT UNIQUE,                 -- evita duplicats si un cron es dispara dues vegades
    success BOOLEAN NOT NULL DEFAULT TRUE,
    error_msg TEXT,
    raw_response JSONB,                          -- guarda la resposta raw de Pionex per auditoria
    notes TEXT,
    created_by TEXT                              -- script o usuari
);

CREATE INDEX IF NOT EXISTS idx_cap_bot_ts ON capital_events(bot_id, ts);
CREATE INDEX IF NOT EXISTS idx_cap_type ON capital_events(event_type);
CREATE INDEX IF NOT EXISTS idx_cap_ts ON capital_events(ts);

-- ─── Bot epochs: cicle de vida d'un bot ─────────────────────────────
-- Quan tanquem un bot i en creem un de nou amb el mateix nom (ex: PAXG re-creat
-- amb diferent config), cada període és un "epoch".
CREATE TABLE IF NOT EXISTS bot_epochs (
    epoch_id BIGSERIAL PRIMARY KEY,
    bot_id TEXT NOT NULL REFERENCES bots(bot_id),
    epoch_num INT NOT NULL,
    opened_at TIMESTAMPTZ NOT NULL,
    closed_at TIMESTAMPTZ,
    initial_capital_usdt NUMERIC(20,8),
    final_capital_usdt NUMERIC(20,8),
    cycles_completed INT,
    grid_profit_at_close NUMERIC(20,8),
    realized_profit_at_close NUMERIC(20,8),
    true_net_pnl NUMERIC(20,8),
    initial_top NUMERIC(20,8),
    initial_bottom NUMERIC(20,8),
    initial_rows INT,
    notes TEXT,
    UNIQUE(bot_id, epoch_num)
);

CREATE INDEX IF NOT EXISTS idx_epochs_bot ON bot_epochs(bot_id);

-- ─── Recolocations: cada adjust_params executat ─────────────────────
CREATE TABLE IF NOT EXISTS recolocations (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    bot_id TEXT NOT NULL REFERENCES bots(bot_id),
    bot_name TEXT NOT NULL,
    epoch_id BIGINT REFERENCES bot_epochs(epoch_id),
    trigger TEXT,
    price_at_trigger NUMERIC(20,8),
    old_top NUMERIC(20,8),
    old_bottom NUMERIC(20,8),
    new_top NUMERIC(20,8),
    new_bottom NUMERIC(20,8),
    grid_profit_before NUMERIC(20,8),
    grid_profit_after NUMERIC(20,8),
    fee_consumed_before NUMERIC(20,8),
    fee_consumed_after NUMERIC(20,8),
    cost_usdt NUMERIC(20,8),                    -- cost real de la recolocació
    executed BOOLEAN NOT NULL,
    action_taken TEXT,                          -- 'adjust_params_ok'|'adjust_params_ok_after_ssl_verify'|'adjust_params_ok_after_retry'|'adjust_params_error*'
    error_msg TEXT,
    idempotency_key TEXT UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_reloc_bot_ts ON recolocations(bot_id, ts);
CREATE INDEX IF NOT EXISTS idx_reloc_ts ON recolocations(ts);

-- ─── Daily snapshots: 1 fila per bot per dia ────────────────────────
-- Per a gràfics i seguiment d'evolució a llarg termini.
CREATE TABLE IF NOT EXISTS daily_snapshots (
    date DATE NOT NULL,
    bot_id TEXT NOT NULL REFERENCES bots(bot_id),
    bot_name TEXT NOT NULL,
    gross_grid_profit NUMERIC(20,8),            -- Pionex gridProfit del dia
    cum_lifetime_profit NUMERIC(20,8),          -- nostre comptador acumulat
    invested_capital NUMERIC(20,8),              -- qti del dia
    base_amount NUMERIC(30,12),                  -- base coins held by bot
    base_value_usdt NUMERIC(20,8),
    quote_value_usdt NUMERIC(20,8),
    current_value_total NUMERIC(20,8),
    cycles_completed_today INT,
    cycles_total INT,
    price_close NUMERIC(20,8),
    top NUMERIC(20,8),
    bottom NUMERIC(20,8),
    PRIMARY KEY (date, bot_id)
);

CREATE INDEX IF NOT EXISTS idx_daily_bot ON daily_snapshots(bot_id);

-- ─── Reconciliation log: auditoria automàtica diària ────────────────
-- Compara el nostre lifetime_profit_calc vs Pionex i alerta de discrepàncies.
CREATE TABLE IF NOT EXISTS reconciliation_log (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    bot_id TEXT NOT NULL REFERENCES bots(bot_id),
    pionex_grid_profit NUMERIC(20,8),
    pionex_quote_invested NUMERIC(20,8),
    our_lifetime_profit NUMERIC(20,8),
    our_capital_invested NUMERIC(20,8),
    profit_discrepancy NUMERIC(20,8),
    capital_discrepancy NUMERIC(20,8),
    severity TEXT CHECK (severity IN ('ok','warn','critical')),
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_recon_bot_ts ON reconciliation_log(bot_id, ts);

-- ─── Mark schema version ────────────────────────────────────────────
INSERT INTO schema_version (version, notes) VALUES (1, 'Initial schema')
ON CONFLICT (version) DO NOTHING;
