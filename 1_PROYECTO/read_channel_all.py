"""Llegeix TOTS els missatges (sense limit de dies) del canal Xisco."""
import asyncio
import sys
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from telethon import TelegramClient

cfg = yaml.safe_load(open(Path(__file__).parent / "config.yaml", "r", encoding="utf-8"))
tg = cfg.get("telegram", {})
api_id = tg.get("api_id")
api_hash = tg.get("api_hash")

CHANNEL_ID = -1003711770973


async def main():
    client = TelegramClient(
        str(Path(__file__).parent / "session"),
        api_id, api_hash
    )
    await client.start()
    entity = await client.get_entity(CHANNEL_ID)
    print(f"Llegint TOT historial de '{getattr(entity, 'title', '?')}'...")

    msgs = []
    async for msg in client.iter_messages(entity, limit=None):
        if msg.text:
            msgs.append(msg)
        if len(msgs) % 1000 == 0 and len(msgs) > 0:
            print(f"  ... {len(msgs)} missatges processats (data: {msg.date.strftime('%Y-%m-%d')})")

    msgs.reverse()
    out_path = Path(__file__).parent / "xisco_full_dump.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"Total missatges: {len(msgs)}\n")
        if msgs:
            f.write(f"Primer: {msgs[0].date}\n")
            f.write(f"Ultim:  {msgs[-1].date}\n")
        f.write("="*80 + "\n")
        for m in msgs:
            ts = m.date.strftime("%Y-%m-%d %H:%M UTC")
            text = m.text.replace("\n", " | ")
            f.write(f"[{ts}] {text}\n")
    print(f"\nTotal: {len(msgs)} missatges escrits a {out_path}")

    await client.disconnect()


asyncio.run(main())
