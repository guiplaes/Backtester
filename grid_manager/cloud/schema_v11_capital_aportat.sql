-- Schema v11 — view capital_aportat
--
-- Separa el capital EXTERN aportat (deposits + topups + reasignacions entre bots)
-- del capital REINVERTIT (profit del propi sistema que es torna a posar al bot).
-- Permet calcular un ROI VERITABLE que no es deforma amb compounding setmanal.
--
-- Regla de signe per event_type:
--   IN  com aportat:  create, invest_in, rebalance_in, deposit_external
--   OUT de aportat:   reduce, rebalance_out, withdraw_external
--   PROFIT (no compta com aportat):  reinvest_profit (+),  withdraw_profit (-)
--   CLOSE: NO compta (els bots tancats queden fora — només es mostren bots actius)
--
-- Per bot RUNNING:
--   capital_aportat_estim = sum(IN aportat) − sum(OUT aportat)
--   reinvested_into       = sum(reinvest_profit)  -- ONLY positive inflows
--   current_invested      = capital_aportat_estim + reinvested_into
--                           (coincideix amb quoteTotalInvestment del bot a Pionex)
--
-- IMPORTANT: withdraw_profit NO redueix `reinvested_into` ni `current_invested`
-- perquè Pionex NO redueix `quoteTotalInvestment` quan extreuem via /spotGrid/profit
-- (la "reserva" queda dins del bot conceptualment). El withdraw_profit només
-- documenta que el comptador `gridProfit` de Pionex es va resetar.

DROP VIEW IF EXISTS capital_aportat CASCADE;

CREATE OR REPLACE VIEW capital_aportat AS
WITH event_sums AS (
    SELECT
        ce.bot_id,
        ce.bot_name,
        SUM(CASE
            WHEN ce.event_type IN ('create', 'invest_in', 'rebalance_in', 'deposit_external') THEN ce.amount_usdt
            WHEN ce.event_type IN ('reduce', 'rebalance_out', 'withdraw_external') THEN -ce.amount_usdt
            ELSE 0
        END)::numeric(20,8) AS aportat_sum,
        -- withdraw_profit NO compta aquí (Pionex no redueix quoteTotalInvestment)
        SUM(CASE
            WHEN ce.event_type = 'reinvest_profit' THEN ce.amount_usdt
            ELSE 0
        END)::numeric(20,8) AS reinvested_sum,
        SUM(CASE
            WHEN ce.event_type = 'deposit_external' THEN ce.amount_usdt
            ELSE 0
        END)::numeric(20,8) AS deposits_ext
    FROM capital_events ce
    WHERE ce.success = true
    GROUP BY ce.bot_id, ce.bot_name
)
SELECT
    b.name AS bot_name,
    b.bot_id AS active_bot_id,
    COALESCE(es.aportat_sum + es.reinvested_sum, 0)::numeric(20,8) AS current_invested,
    COALESCE(es.reinvested_sum, 0)::numeric(20,8) AS reinvested_into,
    COALESCE(es.deposits_ext, 0)::numeric(20,8) AS deposits_external,
    COALESCE(es.aportat_sum, 0)::numeric(20,8) AS capital_aportat_estim
FROM bots b
LEFT JOIN event_sums es ON es.bot_id = b.bot_id
WHERE b.status = 'running'
ORDER BY b.name;

COMMENT ON VIEW capital_aportat IS
  'v11: per bot running, separa capital aportat (extern + reasignacions) de capital reinvertit (profit del propi sistema). ROI veritable = profit / capital_aportat_estim.';
