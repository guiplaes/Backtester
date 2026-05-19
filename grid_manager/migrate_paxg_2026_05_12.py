"""
Migracio del bot PAXG (2026-05-12):
- Tanca l'epoch del bot antic 5e9e92b0 (que ja s'ha cancellat al Pionex)
- Obre nou epoch del bot 76e6b1c8 (8 rows optimitzat)

Run once. No-op if ja s'ha aplicat (idempotent via UNIQUE bot_id+closed_ts).
"""
from db import open_epoch, close_epoch, init_db


OLD_BOT_ID = "5e9e92b0-6730-4b40-8be2-93443ba2f9c4"
NEW_BOT_ID = "76e6b1c8-af3b-42a0-b813-7462d60b303e"

# Estat al moment de tancar el bot antic
OLD_CLOSING_USDT = 400.0          # tot convertit a USDT (TO_QUOTE)
OLD_CLOSING_PAXG = 0.0
OLD_CLOSING_PRICE = 4694.93       # preu al moment del cancel
OLD_GRID_PROFIT = 0.395           # reported per Pionex (5 paired cycles)
OLD_CYCLES = 5
OLD_COST_TO_CLOSE = 0.94          # diferencial preu mig compra (4712.36) vs venda (4694.93) x 0.054 PAXG

# Estat del nou bot
NEW_CAPITAL = 400.0
NEW_OPEN_PRICE = 4698.10
NEW_COST_TO_CREATE = 0.0


def main():
    con = init_db()
    # Sanity: hi ha epoch obert per al bot antic?
    cur = con.execute(
        "SELECT id FROM epochs WHERE bot_id = ? AND closed_ts IS NULL ORDER BY id DESC LIMIT 1",
        (OLD_BOT_ID,)
    )
    row = cur.fetchone()
    con.close()

    if row:
        print(f"Tancant epoch antic id={row[0]} ({OLD_BOT_ID})...")
        close_epoch(
            bot_id=OLD_BOT_ID,
            closing_usdt=OLD_CLOSING_USDT,
            closing_paxg=OLD_CLOSING_PAXG,
            closing_price=OLD_CLOSING_PRICE,
            cost_to_close=OLD_COST_TO_CLOSE,
            grid_profit_reported=OLD_GRID_PROFIT,
            cycles=OLD_CYCLES,
            notes="Recreat amb 8 rows optimitzat (era 12 rows, ratio fee/profit 2.65x -> 4.0x)"
        )
        print("Epoch antic tancat [OK]")
    else:
        print(f"No hi ha epoch obert per al bot antic {OLD_BOT_ID} — saltant tancament")

    # Sanity: ja hi ha epoch obert per al bot nou?
    con = init_db()
    cur = con.execute(
        "SELECT id FROM epochs WHERE bot_id = ? AND closed_ts IS NULL ORDER BY id DESC LIMIT 1",
        (NEW_BOT_ID,)
    )
    row = cur.fetchone()
    con.close()

    if row:
        print(f"Ja existeix epoch obert per al bot nou (id={row[0]}) — saltant creacio")
    else:
        print(f"Obrint nou epoch per al bot {NEW_BOT_ID}...")
        open_epoch(
            bot_id=NEW_BOT_ID,
            capital_usdt=NEW_CAPITAL,
            paxg_amount=0.0,
            price=NEW_OPEN_PRICE,
            cost_to_create=NEW_COST_TO_CREATE,
            symbol="PAXG_USDT",
        )
        print("Nou epoch obert [OK]")

    print("\nMigracio completada.")


if __name__ == "__main__":
    main()
