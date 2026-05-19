"""
backfill.py — Pobla Neon amb dades històriques que tenim al SQLite local
i a Pionex. Idempotent: pots executar-ho diverses vegades sense duplicar.

Què omple:
  1. `bots` master amb els 6 bots actius + 2 closed
  2. `bot_epochs` amb els 6 epochs que tenim al SQLite
  3. `capital_events` amb els events que podem reconstruir:
     - 'create' de cada bot (createTime de Pionex)
     - 'deposit_external' del $400 PAXG inicial (transactions table)
     - 'rebalance_in' del +68.55 a BTC (bot_investments.json)
     - 'rebalance_in' del +130 a USOX (Pionex quoteTotalInvestment ho confirma)
     - 'rebalance_in' del +200 a SPYX
  4. `recolocations` amb les 40 que tenim al SQLite
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cloud.db_cloud import (
    upsert_bot, mark_bot_closed, open_epoch, close_epoch,
    log_capital_event, log_recolocation, get_open_epoch_id,
)

SQLITE = Path(__file__).resolve().parent.parent / "db" / "grid_manager.sqlite"
INVEST_JSON = Path(__file__).resolve().parent.parent / "db" / "bot_investments.json"


# Pionex bot data (que vam baixar fa una hora)
# Format: name -> (bot_id, base, quote, strategy_id, created_at_ms, status, qti, gridProfit)
BOTS = {
    "PAXG_USDT": ("76e6b1c8-af3b-42a0-b813-7462d60b303e", "PAXG", "USDT", 124,
                  1778588327000, "running", 400.0, 4.034197589625),
    "BTC_USDT":  ("35720ef3-45ea-4864-9347-52b6dad0e222", "BTC",  "USDT", 121,
                  1778564299000, "running", 268.5501, 3.33596409385),
    "ETH_USDT":  ("b9b4db3c-e6cf-45fb-abad-0c1d185c5ea4", "ETH",  "USDT", 128,
                  1778651265000, "running", 190.0, 2.6355535742),
    "SOL_USDT":  ("1a71efd2-4955-4587-8823-04eac3f4a367", "SOL",  "USDT", 127,
                  1778651186000, "running", 95.0, 1.5196188),
    "USOX_USDT": ("c3b1a652-7673-4757-8405-00e69532ae1c", "USOX", "USDT", 129,
                  1778750344000, "running", 130.0, 1.79344447),
    "SPYX_USDT": ("ed1e50c8-8191-478e-9551-93de23d976f7", "SPYX", "USDT", 130,
                  1778751427000, "running", 200.0, 0.270328884),
}

# Bots tancats (els que apareixen a bot_investments.json)
CLOSED_BOTS = {
    # name -> (bot_id, base, quote, opened_at, closed_at, initial, final)
    "ETH_BTC_closed_2026_05_12": (
        "75e40c3d-7092-44c4-bb34-9eca9a93ec94", "ETH", "BTC",
        datetime.fromisoformat("2026-05-12T19:52:14+00:00"),
        datetime.fromisoformat("2026-05-13T05:50:56+00:00"),
        199.82, 190.52,
    ),
    "SOL_BTC_closed_2026_05_12": (
        "9fbdc1bf-f513-4c71-b3c5-1c6cbea3d2d5", "SOL", "BTC",
        datetime.fromisoformat("2026-05-12T19:52:27+00:00"),
        datetime.fromisoformat("2026-05-13T05:50:56+00:00"),
        99.91, 94.20,
    ),
    # Primer epoch del PAXG (5e9e92b0...) i altres bots que vam re-crear
    "PAXG_first_epoch": (
        "5e9e92b0-6730-4b40-8be2-93443ba2f9c4", "PAXG", "USDT",
        datetime.fromisoformat("2026-05-11T16:10:44+00:00"),
        datetime.fromisoformat("2026-05-12T12:22:59+00:00"),
        400.0, 400.0,
    ),
}


def _utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def step1_upsert_bots():
    print("=== Step 1: upsert bots master ===")
    for name, (bot_id, base, quote, strat, created_ms, status, qti, gp) in BOTS.items():
        upsert_bot(bot_id, name, base, quote,
                   pionex_strategy_id=strat,
                   created_at=_utc(created_ms),
                   status=status,
                   notes=f"Active. qti={qti} gp={gp} backfilled 2026-05-19")
        print(f"  + {name} ({bot_id[:8]})")

    for cname, (bot_id, base, quote, opened_at, closed_at, init, final) in CLOSED_BOTS.items():
        upsert_bot(bot_id, cname, base, quote,
                   created_at=opened_at, status="closed",
                   notes=f"Closed bot. initial={init} final={final}")
        mark_bot_closed(bot_id, closed_at)
        print(f"  [x] {cname} ({bot_id[:8]}) closed")


def step2_open_epochs():
    print("\n=== Step 2: bot_epochs ===")
    # Per bots actius: 1 epoch obert
    for name, (bot_id, _, _, _, created_ms, _, qti, _) in BOTS.items():
        if get_open_epoch_id(bot_id):
            print(f"  - {name}: ja té epoch obert, skip")
            continue
        eid = open_epoch(bot_id, initial_capital_usdt=qti, opened_at=_utc(created_ms),
                         notes=f"Initial epoch (backfilled)")
        print(f"  + {name} epoch_id={eid} initial={qti}")

    # Per bots tancats: epoch obert + tancat
    for cname, (bot_id, _, _, opened_at, closed_at, init, final) in CLOSED_BOTS.items():
        if not get_open_epoch_id(bot_id):
            # No té cap obert — crear i tancar immediatament
            eid = open_epoch(bot_id, initial_capital_usdt=init, opened_at=opened_at,
                             notes=f"Closed bot, backfilled")
            close_epoch(bot_id, final_capital_usdt=final, closed_at=closed_at,
                        true_net_pnl=final - init, notes="Backfilled closure")
            print(f"  + {cname} epoch_id={eid} closed (pnl={final-init:+.2f})")


def step3_capital_events():
    print("\n=== Step 3: capital_events ===")
    # 3.1: 'create' events per cada bot (incloent tancats)
    for name, (bot_id, _, _, _, created_ms, _, qti, _) in BOTS.items():
        idem = f"create_{bot_id}"
        rid = log_capital_event(
            bot_id=bot_id, bot_name=name, event_type="create",
            amount_usdt=qti, source="backfill",
            qti_before=0, qti_after=qti,
            grid_profit_snapshot=0, lifetime_profit_calc=0,
            idempotency_key=idem,
            ts=_utc(created_ms),
            notes="Initial bot creation",
        )
        print(f"  + create {name}: {'INSERTED' if rid else 'already exists'}")

    for cname, (bot_id, _, _, opened_at, closed_at, init, final) in CLOSED_BOTS.items():
        log_capital_event(
            bot_id=bot_id, bot_name=cname, event_type="create",
            amount_usdt=init, source="backfill",
            qti_before=0, qti_after=init,
            idempotency_key=f"create_{bot_id}",
            ts=opened_at, notes="Closed bot creation",
        )
        log_capital_event(
            bot_id=bot_id, bot_name=cname, event_type="close",
            amount_usdt=final, source="backfill",
            qti_before=init, qti_after=0,
            idempotency_key=f"close_{bot_id}",
            ts=closed_at, notes=f"Bot closed, final={final}",
        )
        print(f"  + {cname}: create + close events")

    # 3.2: depòsit extern inicial $400 PAXG (de transactions table SQLite)
    paxg_first = CLOSED_BOTS["PAXG_first_epoch"][0]  # bot_id del primer epoch
    log_capital_event(
        bot_id=paxg_first, bot_name="PAXG_first_epoch",
        event_type="deposit_external", amount_usdt=400.0,
        source="backfill", idempotency_key="deposit_initial_400_paxg",
        ts=datetime.fromisoformat("2026-05-11T16:10:44+00:00"),
        notes="Initial deposit for PAXG grid bot (from old transactions table)",
    )
    print("  + deposit_external $400 PAXG inicial")

    # 3.3: rebalance_in events que sabem del bot_investments.json
    # BTC +68.55 al 2026-05-12 17:07
    btc_id = BOTS["BTC_USDT"][0]
    log_capital_event(
        bot_id=btc_id, bot_name="BTC_USDT",
        event_type="rebalance_in", amount_usdt=68.55,
        source="backfill", idempotency_key="rebalance_in_btc_2026_05_12_165500",
        qti_before=200.0, qti_after=268.55,
        ts=datetime.fromisoformat("2026-05-12T17:07:00+00:00"),
        notes="REBALANCE manual a 16:00 — pre-tracker",
    )
    print("  + rebalance_in BTC +68.55")

    # USOX inicialitzat amb 130 (no era 0): suposem 1 sol invest_in del valor total
    usox_id = BOTS["USOX_USDT"][0]
    log_capital_event(
        bot_id=usox_id, bot_name="USOX_USDT",
        event_type="rebalance_in", amount_usdt=130.0,
        source="backfill", idempotency_key="rebalance_in_usox_2026_05_14_init",
        qti_before=0, qti_after=130.0,
        ts=datetime.fromisoformat("2026-05-14T06:39:00+00:00"),
        notes="USOX created amb 130 USDT (rebalance origen)",
    )
    print("  + rebalance_in USOX +130 (creació)")

    # SPYX igual: 200 d'origen
    spyx_id = BOTS["SPYX_USDT"][0]
    log_capital_event(
        bot_id=spyx_id, bot_name="SPYX_USDT",
        event_type="rebalance_in", amount_usdt=200.0,
        source="backfill", idempotency_key="rebalance_in_spyx_2026_05_14_init",
        qti_before=0, qti_after=200.0,
        ts=datetime.fromisoformat("2026-05-14T06:57:00+00:00"),
        notes="SPYX created amb 200 USDT (from sale of BTC, per config)",
    )
    print("  + rebalance_in SPYX +200 (creació)")


def step4_recolocations():
    print("\n=== Step 4: recolocations (40 rows del SQLite) ===")
    if not SQLITE.exists():
        print(f"  ! SQLite not at {SQLITE}, skip")
        return

    con = sqlite3.connect(SQLITE)
    cur = con.execute("""
        SELECT ts, bot_id, bot_name, trigger, price, new_top, new_bottom,
               grid_profit_before, grid_profit_after,
               fee_pool_before, fee_pool_after, total_cost_usdt
        FROM recolocation_costs ORDER BY ts
    """)
    count = inserted = 0
    for row in cur.fetchall():
        count += 1
        ts, bot_id, bot_name, trigger, price, ntop, nbot, gpb, gpa, fpb, fpa, cost = row
        # idempotency: ts + bot_id (unic)
        idem = f"reloc_{bot_id}_{ts}"
        # Per dades antigues no tenim old_top/old_bottom; els deixem com a new_top/new_bottom-1
        # Aproximació: agafem el rang nou com a old_* (no és precís, però és el que sabem).
        result = log_recolocation(
            bot_id=bot_id, bot_name=bot_name,
            trigger=trigger or "unknown",
            price_at_trigger=price or 0,
            old_top=ntop or 0, old_bottom=nbot or 0,  # desconegut → mateix que new
            new_top=ntop or 0, new_bottom=nbot or 0,
            grid_profit_before=gpb or 0,
            grid_profit_after=gpa,
            fee_consumed_before=fpb or 0,
            fee_consumed_after=fpa,
            cost_usdt=cost or 0,
            executed=True,
            action_taken="adjust_params_ok (backfill)",
            idempotency_key=idem,
            ts=datetime.fromisoformat(ts.replace("Z", "+00:00")),
        )
        if result:
            inserted += 1
    con.close()
    print(f"  + {inserted}/{count} recolocations insertades (resta ja existia)")


def main():
    print("Backfill Neon des de SQLite + bot_investments.json")
    print("=" * 60)
    step1_upsert_bots()
    step2_open_epochs()
    step3_capital_events()
    step4_recolocations()
    print("\n" + "=" * 60)
    print("Backfill COMPLET. Comprovant comptes:")

    from cloud.db_cloud import conn
    with conn() as c, c.cursor() as cur:
        for t in ["bots", "bot_epochs", "capital_events", "recolocations"]:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            print(f"  {t}: {cur.fetchone()[0]} files")


if __name__ == "__main__":
    main()
