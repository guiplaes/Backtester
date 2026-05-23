# Vault DCA System — Plan d'implementació

**Data**: 2026-05-23
**Estat**: Disseny aprovat, pendent connection string Neon i stack ComptesLab per començar.

---

## 1. Objectiu

Substituir la lògica actual de relocació per chasing (`EDGE_TRIGGER_PCT=0.10` a `monitor.py`) per un sistema vault DCA amb cross-funding waterfall. Objectiu: mai vendre base sota cost (excepte en cas excepcional P4), accumular base en caigudes via grid, reciclar profits per finançar relocacions a preus més baixos.

**Realitat esperada (per backtest 4.6 anys):** ~11% APR. Rebalance bot ~16% APR. Diferencial de ~$5k sobre 4.6 anys assumit conscientment a canvi de control operatiu del sistema.

---

## 2. Decisions arquitectòniques confirmades

| Decisió | Tria |
|---------|------|
| Tracking dels bots actuals | **Option B**: deixar-los corrent, sistema intervé només en breakout |
| Valor del nou bot al relocar | **Match valor del moment del close** (P4 dispara quan calgui) |
| DCA Scheduler automàtic | **No**, manual via injection web |
| Waterfall funding | **P1→P2→P3→P4** amb combinacions parcials |
| Source of truth | **Neon** (Option A) |
| Continuïtat estratègica | **Strategy + BotInstance abstraction** |
| Inicialització | **Option (a)**: bots actuals = generation 1, capital actual |
| Profit harvest | **Diari 22:00 UTC** alineat amb `DAILY_CHECK_HOUR_UTC` |

---

## 3. Waterfall de finançament (regla central)

```
NECESSITAT: $X per relocar bot de [asset_X]

P1: Vendre vault profit (qualsevol asset, ranked profit% desc)
    └─ Partial sells permesos, fins esgotar disponibles profitables

P2: USDT inventory (manual injections + harvest acumulat)
    └─ Fins esgotar pool

P3: Vendre vault en pèrdua (smallest loss% first, EXCLOENT asset_X)
    └─ Fins esgotar

P4 (últim recurs): Vendre base recuperat de asset_X mateix
    └─ Realitza pèrdua sobre porció del propi inventari
    └─ Tu vigiles que no dispari sovint

Combinacions parcials a través de tots els nivells fins arribar a $X.
Si fins i tot P4 no cobreix: ABORT relocació, retry en 24-72h.
```

---

## 4. Components a construir

### 4.1 Backend (VPS, Python)

| Component | Fitxer | Responsabilitat |
|-----------|--------|-----------------|
| Vault Tracker | `vault/tracker.py` | CRUD a inventory + events, read MTM |
| Strategy Manager | `vault/strategy.py` | Strategy + BotInstance lifecycle |
| Profit Harvester | `vault/harvester.py` | Cron diari, withdraw grid profits → inv_usdt |
| Injection Consumer | `vault/injection.py` | Poll Neon `injection_queue` cada 60s |
| Grid Closer | `vault/closer.py` | Detecta breakout, cancel bot NOT_SELL |
| Grid Re-Launcher | `vault/relauncher.py` | Crea nou bot a rang inferior |
| Funding Engine | `vault/funding.py` | Waterfall P1→P2→P3→P4 |
| Reconciler | `vault/reconcile.py` | Verifica consistència Neon ↔ Pionex |

### 4.2 Database (Neon)

```sql
-- Inventari fungible (un row per asset)
CREATE TABLE vault_inventory (
    asset VARCHAR(10) PRIMARY KEY,
    qty NUMERIC(20,8) NOT NULL DEFAULT 0,
    cost_total_usdt NUMERIC(15,4) NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Audit trail de tot el que passa
CREATE TABLE vault_events (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type VARCHAR(30) NOT NULL,
    asset VARCHAR(10),
    qty_delta NUMERIC(20,8),
    usdt_delta NUMERIC(15,4),
    avg_cost_at_event NUMERIC(20,8),
    bot_id UUID,
    strategy_asset VARCHAR(10),
    note TEXT
);
-- event_type: bot_close, manual_inject, profit_harvest,
--             fund_p1, fund_p2, fund_p3, fund_p4,
--             grid_create, grid_breakout, reconcile

-- Strategy: entitat lògica permanent (1 per asset)
CREATE TABLE strategy (
    asset VARCHAR(10) PRIMARY KEY,
    inception_date TIMESTAMPTZ NOT NULL,
    initial_capital_usdt NUMERIC(15,4) NOT NULL,
    notes TEXT
);

-- BotInstance: cada bot físic creat (continua history a través de relocs)
CREATE TABLE bot_instance (
    id BIGSERIAL PRIMARY KEY,
    strategy_asset VARCHAR(10) NOT NULL REFERENCES strategy(asset),
    pionex_bot_id UUID UNIQUE NOT NULL,
    generation INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    closed_at TIMESTAMPTZ,
    initial_value_usdt NUMERIC(15,4) NOT NULL,
    final_value_usdt NUMERIC(15,4),
    grid_profit_total NUMERIC(15,4) DEFAULT 0,
    cycles_count INTEGER DEFAULT 0,
    top_price NUMERIC(20,8),
    bottom_price NUMERIC(20,8),
    rows INTEGER,
    close_reason VARCHAR(30)
);
CREATE INDEX idx_bot_instance_active ON bot_instance(strategy_asset)
    WHERE closed_at IS NULL;

-- Cua d'injeccions manuals des de ComptesLab
CREATE TABLE injection_queue (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    amount_usdt NUMERIC(15,4) NOT NULL,
    status VARCHAR(15) DEFAULT 'pending',  -- pending | consumed | failed
    consumed_at TIMESTAMPTZ,
    consumed_by VARCHAR(50),
    note TEXT
);

-- Vista agregada per dashboard
CREATE VIEW strategy_metrics AS
SELECT
    s.asset,
    s.inception_date,
    s.initial_capital_usdt,
    COALESCE(SUM(b.grid_profit_total), 0) AS total_grid_profit,
    COALESCE(SUM(b.cycles_count), 0) AS total_cycles,
    COUNT(b.id) AS generations,
    (SELECT pionex_bot_id FROM bot_instance
     WHERE strategy_asset = s.asset AND closed_at IS NULL
     LIMIT 1) AS active_bot_id,
    (SELECT qty FROM vault_inventory WHERE asset = s.asset) AS vault_qty,
    (SELECT cost_total_usdt FROM vault_inventory WHERE asset = s.asset)
        / NULLIF((SELECT qty FROM vault_inventory WHERE asset = s.asset), 0)
        AS vault_avg_cost
FROM strategy s
LEFT JOIN bot_instance b ON b.strategy_asset = s.asset
GROUP BY s.asset;
```

### 4.3 Frontend (ComptesLab, stack pendent confirmar)

- **Vista Strategy per asset**:
  - Cycles totals (suma generations)
  - Profit grid total ($)
  - Valor actual MTM
  - % evolució des d'inception
  - Generations count
  - Bot actual (rang, valor)
  - Vault (qty, avg_cost, MTM)
- **Vista global**:
  - Total capital invertit ($)
  - Total valor actual ($)
  - % ROI inception
  - Pool USDT disponible
- **Botó "Add USDT to inventory"**: input amount → INSERT `injection_queue`
- **Log d'events** (últims 100): SELECT vault_events ORDER BY ts DESC

---

## 5. Lifecycle típic d'un cicle

```
T=0    Sistema funcionant. Bot BTC actiu a [$77k-$73k] amb $200 USDT inicial.
       Vault BTC=0. Vault USDT=$50 (acumulat).

T=10d  BTC cau a $72k → bot continua trading dins de rang.
       Cycles generats: 50. Grid profit acumulat: $5.

T=22:00 UTC diari → Harvester:
       Read bot.gridProfit = $5. Read bot.profitWithdrawn = $0.
       Withdraw $5 via API. UPDATE inv_usdt += $5. INSERT vault_event(profit_harvest).

T=20d  BTC cau a $71.5k (sota el bottom $73k de fa 2 barres consecutives).
       → Closer detecta breakout_down.
       → Cancel bot via Pionex API amb closeSellModel=NOT_SELL.
       → Read recovered: 0.0028 BTC + $25 USDT (valor total $225).
       → Update bot_instance.closed_at, final_value=$225, etc.
       → Add 0.0028 BTC a vault_inventory (avg_cost del bot anterior).
       → Add $25 a inv_usdt.

T=20d+1m  Re-Launcher invocat per asset=BTC, target_value=$225.
          → Funding Engine: need=$225, have=$25 (del close) + $55 (inv_usdt).
          → P2 cobreix els primers $80. Shortfall=$145.
          → P1 scan: vault PAXG en profit (preu $4500 vs avg $4300). Sell $145
            de PAXG vault → raised $145. UPDATE vault_inventory.
          → Total raised: $225 ✓
          → Create new bot via Pionex API amb $225 USDT, range $69k-$73k.
          → INSERT bot_instance generation=2 per strategy BTC.

T=20d+2m  ComptesLab dashboard mostra:
          BTC Strategy: 2 generations, cycles totals=50, grid_profit=$5,
          current value MTM=$225 (bot) + 0.0028×$71.5k (vault) = $425,
          inception value=$200, pct=+112%.
```

---

## 6. Fases d'implementació

### Fase 1 — Vault Tracker backend (3-5 dies) ⏳ PRÒXIMA

**Entregables:**
- Schema Neon creat (vault_inventory, vault_events, strategy, bot_instance, injection_queue, strategy_metrics)
- `vault/tracker.py` amb funcions CRUD bàsiques
- `vault/harvester.py` (cron diari)
- `vault/injection.py` (poll cada 60s)
- Script `init_vault.py` per inicialitzar amb estat actual dels 6 bots
- Cron entries afegides al Task Scheduler (silencioses via wrapper VBS)
- Testing manual amb dades reals

**No toca**: monitor.py existent, lògica de relocació actual, no crea/cancela bots.
**Risc**: nul. Només track passiu.

**Pendent per començar**: Neon connection string.

### Fase 2 — Closer + Re-Launcher + Funding (5-7 dies) — SHADOW mode

**Entregables:**
- `vault/closer.py` — detecta breakout (2 barres confirmation)
- `vault/relauncher.py` — calcula nou rang, invoca funding
- `vault/funding.py` — waterfall P1→P2→P3→P4
- Tot en **shadow mode**: log el que faria, NO executa cap call a Pionex
- Comparació amb el comportament actual de monitor.py

**No toca**: bots reals. Només logging.
**Risc**: nul.

### Fase 3 — Live en 1 asset (2 setmanes observació)

**Entregables:**
- Activar execució real per **PAXG** (capital baix, menys volàtil)
- Desactivar la lògica corresponent de monitor.py per PAXG
- Tu vigiles cada dia
- Telegram alerts en cada acció (close, create, funding event)

**Risc**: limitat a PAXG ($400). Si bug, pèrdua acotada.

### Fase 4 — Full rollout (1 setmana)

**Entregables:**
- Activar per BTC, ETH, SOL, USOX, SPYX (la resta)
- Desactivar monitor.py completament
- Reconciler diari per verificar consistència Neon ↔ Pionex
- Telegram resum diari

**Risc**: tot el sistema.

### Fase 5 — Dashboard ComptesLab (2-3 setmanes paral·lel)

**Entregables:**
- Vista Strategy per asset (continuïtat cross-generations)
- Vista global portfolio
- Botó "Add USDT to inventory"
- Log d'events
- Gràfiques de cycles, profits, value evolution

**Pendent per començar**: stack de ComptesLab (Next.js / Flask / etc.)

---

## 7. Riscos coneguts

| Risc | Mitigació |
|------|-----------|
| VPS single point of failure | Telegram heartbeat horari; backup Raspberry a casa (Fase 6 opcional) |
| Drift entre Neon i Pionex | Reconciler diari + Telegram alert si divergeix |
| Crypto correlation → P4 dispara sovint | Tu monitoreges; si massa freqüent, ajustar target_value rule |
| Bug en producció → pèrdua real | Phased rollout (Fase 3 = 1 asset, observació 2 setmanes) |
| Backtest predicció optimista | Acceptat conscientment; +11% APR esperat vs +16% Rebalance |

---

## 8. Pendents abans de començar

1. **Connection string Neon** (o credentials per construir-lo)
2. **Confirmar nom BD**: la mateixa de ComptesLab, o una nova `grid_vault`?
3. **Permisos**: pot el Python crear taules a Neon, o ho fas tu via dashboard?

Quan Fase 5: stack ComptesLab (framework, ORM, deployment).

---

## 9. Resum executiu

- **Sistema sencer**: 5-6 setmanes de feina (Fases 1-4) + 2-3 setmanes paral·lel UI (Fase 5)
- **Cap risc fins Fase 3** (només tracking + shadow)
- **Risc real a Fase 3+** controlat per single asset rollout
- **Performance esperada**: 11% APR backtested (vs 16% Rebalance benchmark)
- **El que guanyes**: control operatiu del sistema, mai vendre sota cost (excepte P4)
- **El que pagues**: ~5pts APR per any vs alternativa simple Rebalance

Quan vulguis començar: passa'm Neon connection string i procedeixo amb Fase 1.
