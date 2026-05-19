"""Llegeix els missatges dels ultims 7 dies d'un canal TG i mostra tot."""
import asyncio
import sys
import yaml
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from telethon import TelegramClient

cfg = yaml.safe_load(open(Path(__file__).parent / "config.yaml", "r", encoding="utf-8"))
tg = cfg.get("telegram", {})
api_id = tg.get("api_id")
api_hash = tg.get("api_hash")

# El channel id: provarem varies formes
TARGETS = [
    -1003711770973,
    -3711770973,
    3711770973,
    "https://t.me/+3711770973",  # si es un invite link
]


async def main():
    client = TelegramClient(
        str(Path(__file__).parent / "session"),
        api_id, api_hash
    )
    await client.start()
    me = await client.get_me()
    print(f"Logged in as: {me.first_name} ({me.id})")
    print()

    # Llistem primer els dialogs per veure si el canal hi és
    print("=== Buscant el canal a la llista de dialogs ===")
    found_entity = None
    async for dialog in client.iter_dialogs():
        name = dialog.name or ""
        did = dialog.id
        # Buscar per nom o id parcial
        if "3711770973" in str(did) or "3711770973" in name:
            print(f"  CANDIDAT: name={name!r}, id={did}, type={type(dialog.entity).__name__}")
            found_entity = dialog.entity
            break

    if not found_entity:
        # Llistem els 30 primers per ajudar a identificar
        print("  No trobat directament. Llista dels 30 primers dialogs:")
        count = 0
        async for dialog in client.iter_dialogs():
            print(f"    [{dialog.id}] {dialog.name!r}")
            count += 1
            if count >= 30:
                break
        print()
        # Provem amb get_entity directe
        for target in TARGETS:
            try:
                print(f"=== Provant get_entity({target!r}) ===")
                found_entity = await client.get_entity(target)
                print(f"  OK: title={getattr(found_entity, 'title', 'N/A')}, id={found_entity.id}")
                break
            except Exception as e:
                print(f"  FAIL: {e}")

    if not found_entity:
        print("\nNo s'ha pogut accedir al canal. Probablement:")
        print("  - El compte logueat no esta unit al canal")
        print("  - Cal unir-se primer via l'app de Telegram")
        await client.disconnect()
        return

    # OK, tenim entity. Llegim ultims 7 dies
    print(f"\n=== Llegint missatges dels ultims 7 dies de '{getattr(found_entity, 'title', '?')}' ===\n")
    since = datetime.now(timezone.utc) - timedelta(days=7)
    msgs = []
    async for msg in client.iter_messages(found_entity, limit=5000):
        if msg.date and msg.date < since:
            break
        if msg.text:
            msgs.append(msg)

    msgs.reverse()  # crono ascendent
    out_path = Path(__file__).parent / "xisco_channel_dump.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"Total missatges en 7 dies: {len(msgs)}\n")
        f.write("="*80 + "\n")
        for m in msgs:
            ts = m.date.strftime("%Y-%m-%d %H:%M UTC")
            text = m.text.replace("\n", " | ")
            f.write(f"[{ts}] {text}\n")
        f.write("="*80 + "\n")
    print(f"Total missatges en 7 dies: {len(msgs)}")
    print(f"Dump escrit a: {out_path}")

    await client.disconnect()


asyncio.run(main())
