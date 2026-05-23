-- ═══════════════════════════════════════════════════════════════════════
-- Vault Inventory Schema (additions to existing Neon DB)
-- Reuses: bots, bot_chain, bot_lineage, capital_events, recolocations,
--         lifetime_summary_v3, daily_snapshots_active
-- Adds:   vault_inventory, injection_queue, vault_audit (view)
-- ═══════════════════════════════════════════════════════════════════════

-- Inventari fungible: una fila per asset (qty + cost basis per a avg cost).
-- USDT és tractat com asset més (qty=USDT amount, cost_total_usdt=qty, avg=1).
CREATE TABLE IF NOT EXISTS vault_inventory (
    asset           VARCHAR(10) PRIMARY KEY,
    qty             NUMERIC(20,8) NOT NULL DEFAULT 0,
    cost_total_usdt NUMERIC(15,4) NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes           TEXT
);

COMMENT ON TABLE vault_inventory IS
'Inventari de actius/USDT del sistema FORA de qualsevol bot actiu de Pionex.
S''omple per: bot_close (afegeix base recuperat + USDT), profit_harvest (afegeix USDT),
manual_injection (afegeix USDT). Es buida per: funding_engine waterfall (vendes vault per
finançar relocations), grid_create (consumeix USDT).
avg_cost = cost_total_usdt / qty (NULL si qty=0).';


-- Cua d''injeccions manuals des de ComptesLab UI.
-- El sistema (VPS) la consumeix amb un cron de 60s.
CREATE TABLE IF NOT EXISTS injection_queue (
    id           BIGSERIAL PRIMARY KEY,
    ts           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    amount_usdt  NUMERIC(15,4) NOT NULL,
    status       VARCHAR(15) NOT NULL DEFAULT 'pending',  -- pending | consumed | failed
    consumed_at  TIMESTAMPTZ,
    consumed_by  VARCHAR(50),                              -- nom del cron/host
    error_msg    TEXT,
    note         TEXT
);

CREATE INDEX IF NOT EXISTS idx_injection_queue_pending
    ON injection_queue (ts) WHERE status = 'pending';

COMMENT ON TABLE injection_queue IS
'Cua d''aportacions manuals d''USDT a l''inventari del sistema. ComptesLab UI fa
INSERT amb status=pending. El cron consume_injections.py (60s) processa,
crida vault_inventory.add_usdt() i marca status=consumed.';


-- Vista per al dashboard: inventari + MTM (necessita JOIN amb price source)
-- NOTE: el preu actual no està a Neon a temps real. La vista retorna les dades
-- bàsiques; ComptesLab fa el MTM al frontend amb preus de Pionex API o
-- price_snapshots més recents.
CREATE OR REPLACE VIEW vault_inventory_with_avg AS
SELECT
    asset,
    qty,
    cost_total_usdt,
    CASE WHEN qty > 0 THEN cost_total_usdt / qty ELSE NULL END AS avg_cost,
    updated_at,
    notes
FROM vault_inventory
ORDER BY
    CASE WHEN asset = 'USDT' THEN 0 ELSE 1 END,  -- USDT primer
    asset;


-- ═══════════════════════════════════════════════════════════════════════
-- Initialization rows: 6 assets (qty=0) + USDT (qty=$20.25 ja extret)
-- $20.25 = SUM(profitWithdrawn) actual de tots els bots = el que ja ha sortit
-- dels grids cap al wallet i és part funcional del sistema.
-- ═══════════════════════════════════════════════════════════════════════
INSERT INTO vault_inventory (asset, qty, cost_total_usdt, notes) VALUES
    ('BTC',  0, 0, 'Inicialitzat 2026-05-23. Buit, s''omplirà al primer bot_close.'),
    ('ETH',  0, 0, 'Inicialitzat 2026-05-23. Buit.'),
    ('PAXG', 0, 0, 'Inicialitzat 2026-05-23. Buit.'),
    ('SOL',  0, 0, 'Inicialitzat 2026-05-23. Buit.'),
    ('USOX', 0, 0, 'Inicialitzat 2026-05-23. Buit.'),
    ('SPYX', 0, 0, 'Inicialitzat 2026-05-23. Buit.'),
    ('USDT', 20.25, 20.25, 'Inicialitzat amb suma profitWithdrawn dels 6 bots al 2026-05-23.')
ON CONFLICT (asset) DO NOTHING;
