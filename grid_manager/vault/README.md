# Vault DCA System

Sistema d'inventari + DCA + cross-funding waterfall per al portfolio de grids de Pionex.

**Estat**: Fase 1 + Fase 2 (SHADOW) operatives des de 2026-05-23.

---

## Arquitectura completa

```
┌──────────────────────────────────────────────────────────────────┐
│ VPS (aquesta màquina)                                            │
│                                                                  │
│  Crons silenciosos (Task Scheduler + wrapper VBS):              │
│   ─ VaultConsumeInjections  cada 60s   PRODUCTION               │
│   ─ VaultCloser             cada 5min  SHADOW (cap LIVE)        │
│   ─ VaultProfitHarvester    diari 22UTC PRODUCTION              │
│                                                                  │
│  Mòduls Python:                                                  │
│   ─ vault/inventory.py      CRUD vault, MTM, +TG notify         │
│   ─ vault/consume_injections.py   Poll injection_queue          │
│   ─ vault/profit_harvester.py     Extreu profits → vault USDT   │
│   ─ vault/funding.py        Waterfall P1→P2→P3→P4 (lògica pura) │
│   ─ vault/closer.py         Detector breakout (SHADOW/LIVE)     │
│   ─ vault/relauncher.py     Orquestra close+fund+create         │
│                                                                  │
│  Existent (no modificat):                                        │
│   ─ monitor.py (chasing — convivirà fins Fase 4 rollout)        │
│   ─ cloud/* (db_cloud, weekly_rebalance, reconcile, ...)        │
│   ─ notifier.py (extended amb notify_vault_event)                │
│   ─ pionex_client.py (+cancel_bot, +create_spot_grid)            │
└──────────────────────────────────────────────────────────────────┘
                                ↕
┌──────────────────────────────────────────────────────────────────┐
│ Neon Postgres                                                    │
│   Noves:                                                         │
│    ─ vault_inventory      7 rows (USDT + 6 assets)              │
│    ─ vault_events         audit amb idempotency                  │
│    ─ injection_queue      ComptesLab → VPS                      │
│    ─ vault_breakout_state per confirmation N reads               │
│    ─ vault_inventory_with_avg (vista)                            │
│   Existents reutilitzades:                                       │
│    ─ bots, bot_chain, bot_lineage, bot_epochs                   │
│    ─ capital_events (+ vault_* event_types, + fund_p* types)    │
│    ─ recolocations, lifetime_summary_v3, daily_snapshots        │
└──────────────────────────────────────────────────────────────────┘
                                ↕
┌──────────────────────────────────────────────────────────────────┐
│ ComptesLab (Vercel, Next.js)  — pendent integració UI           │
│   3 funcions noves a src/lib/neon.ts (documentades més avall)   │
└──────────────────────────────────────────────────────────────────┘
```

---

## Fluxe complet (LIVE quan estigui activat)

```
Trigger: VaultCloser cron (cada 5min) detecta preu < bottom × (1-0.001)
         durant 3 reads consecutius (15min)

  ├─ check_breakout(BTC_USDT) → confirmed
  └─ relaunch_after_breakout(BTC_USDT):
       1. snapshot pre-close: base=0.005, quote=$50, price=$70k
          target_value = 0.005×70000 + 50 = $400
       2. pionex.cancel_bot(NOT_SELL) → recupera 0.005 BTC + $50 USDT
       3. vault.add_base(BTC, 0.005, cost=$390)  → +TG notify 📥
       4. vault.add_usdt($50)                    → +TG notify 💰
       5. shortfall = $400 - $50 = $350
       6. funding.compute_funding_plan(target=$350, asset=BTC):
            P1: vendre PAXG profit?  → +$200 (si hi ha en profit)
            P2: USDT inventory?      → +$100
            P3: vendre vault loss?   → +$50 (SOL menor pèrdua)
            P4: (no necessari)
          feasible ✓
       7. execute_funding_plan:
            ✓ Pionex market sell PAXG → +TG notify ✅
            🏦 USDT reserve usage → +TG notify
            ⚠️ Pionex market sell SOL → +TG notify ⚠️
       8. compute new range: [$66.5k, $73.5k] × 12 rows
       9. pionex.create_spot_grid(BTC, top, bottom, $400, NOT_SELL)
       10. Logs a Neon: bots, bot_lineage (parent→child), bot_epochs (close+open),
           capital_events (close+create), snapshot post.
       11. TG notify ✅ "Bot recreat" amb new_bot_id

Si funding NO és feasible → ABORT + TG notify ⚠️. Base recuperat es queda
al vault esperant reservas futures.
```

---

## Activació LIVE

Per defecte tot està en SHADOW (només logs + TG notify, cap mutació real
sobre els bots de Pionex).

Per activar LIVE per a un asset (ex: PAXG):

1. Edita `vault/closer.py`:
   ```python
   VAULT_LIVE_ASSETS = {"PAXG"}  # afegeix els assets que vols actius
   ```

2. Decide what to do amb la lògica de `monitor.py` legacy:
   - **Opció A (recomanat)**: desactiva temporalment trigger del monitor per
     l'asset live. Edita `monitor.py` skipping PAXG:
     ```python
     if name == "PAXG_USDT":
         continue  # gestionat per vault.closer
     ```
   - **Opció B**: deixa ambdós actius i el monitor adjust_grid_range farà chasing
     fins que vault.closer detecti breakout. Possibles conflictes (no recomanat).

3. Mira els TG durant 1-2 setmanes. Si tot funciona bé, amplia a més assets.

4. Després de cada recreate LIVE, **actualitza `config.py`** amb el nou
   `BOTS[asset]["id"]` (el TG t'avisarà). Aquesta és l'única acció manual.

---

## Operativa quotidiana

### Veure estat actual del vault
```bash
cd "C:\Users\Administrator\Desktop\MT4 Claude\grid_manager"
.venv\Scripts\python.exe -m vault.inventory
```

### Injection manual de USDT (sense web)
```sql
INSERT INTO injection_queue (amount_usdt, note)
VALUES (50.00, 'DCA setmanal manual');
```
Cron VaultConsumeInjections (60s) processa automàticament.

### Test funding plan amb dades reals
```bash
.venv\Scripts\python.exe -c "
from vault.funding import compute_funding_plan
from pionex_client import get_current_price
prices = {a: get_current_price(f'{a}_USDT') for a in ['BTC','ETH','PAXG','SOL','USOX','SPYX']}
print(compute_funding_plan(target_usdt=200, asset_being_funded='BTC', prices=prices).summary())
"
```

### Test profit harvester en DRY_RUN
```bash
.venv\Scripts\python.exe -c "
import vault.profit_harvester as ph
ph.DRY_RUN = True
ph.main()
"
```

### Test closer (SHADOW, sense fer res real)
```bash
.venv\Scripts\python.exe -m vault.closer
```

---

## Logs

- `logs/vault_consume.log` — cron injections
- `logs/vault_closer.log` — cron closer
- `logs/vault_harvester.log` — cron diari harvester
- Taula `cron_runs` (Neon) — historial de tots els crons
- Taula `vault_events` (Neon) — audit trail de mutacions vault
- Taula `vault_breakout_state` (Neon) — comptador consecutius per closer

---

## Notificacions Telegram

Cada moviment vault dispara TG (vegeu `notifier.notify_vault_event`):
- 💰 Aportació manual / reserva +
- 🌾 Profits recollits (resum agregat)
- 📥 Bot tancat → base al vault
- ✅ Venda vault en profit (P1)
- 🏦 USDT reserve usat (P2)
- ⚠️ Venda en pèrdua (P3, menor %)
- 🚨 ÚLTIM RECURS venda asset propi (P4)
- 🔍 [SHADOW] Breakout/Relocació planificada
- ✅ Bot recreat (amb new_bot_id)

---

## Pendent: ComptesLab UI (Fase 5)

Stack: Next.js + TypeScript + `@neondatabase/serverless`

Funcions noves a `src/lib/neon.ts`:

```typescript
export interface VaultRow {
  asset: string;
  qty: number;
  cost_total_usdt: number;
  avg_cost: number | null;
  updated_at: string;
}

export async function getVaultInventory(): Promise<VaultRow[]> {
  const sql = getNeon();
  return (await sql`
    SELECT asset, qty::float, cost_total_usdt::float,
           avg_cost::float, updated_at::text
    FROM vault_inventory_with_avg
  `) as VaultRow[];
}

export async function addInjection(amount: number, note: string = ''): Promise<number> {
  const sql = getNeon();
  const rows = await sql`
    INSERT INTO injection_queue (amount_usdt, note)
    VALUES (${amount}, ${note})
    RETURNING id
  ` as { id: number }[];
  return rows[0].id;
}

export async function getVaultEvents(limit: number = 50) {
  const sql = getNeon();
  return await sql`
    SELECT id, ts::text, event_type, asset, qty_delta::float,
           cost_delta_usdt::float, qty_after::float, source, notes
    FROM vault_events
    ORDER BY ts DESC LIMIT ${limit}
  `;
}
```

UI components:
- Vista `/grid/vault` — taula amb MTM (calcular price_now × qty al frontend)
- Botó "Add USDT to inventory" → input numèric → `addInjection()`
- Tab d'history events

---

## Riscos coneguts

| Risc | Mitigació |
|------|-----------|
| Breakout fals positiu | Confirmació 3 reads (15min) + tolerància 0.1% |
| Funding plan NO feasible | Abort + TG notify, base recuperat al vault |
| Pionex API down al moment crític | Cancel/create exception → log + abort |
| Drift Neon ↔ Pionex per vault | Cap reconciler dedicat encara (TODO Fase 4) |
| TG floods en cas crash en cascada | anti-spam per (category, key); explicar usuari |
| `config.py` desfasat post-LIVE-recreate | TG alerta manual, no auto-edit codi |

## Roadmap pendent

- **Fase 3** (1-2 setmanes obs): activar VAULT_LIVE_ASSETS = {"PAXG"}, observar
- **Fase 4** (1 setmana): rollout per BTC, ETH, SOL, USOX, SPYX
- **Fase 5** (paral·lel): ComptesLab UI integration
- **Fase 6** (opcional): reconciler vault diari, watchdog
