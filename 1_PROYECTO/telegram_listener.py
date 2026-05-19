#!/usr/bin/env python3
"""
Telegram Listener for Trader Brain v3.

Runs as a background thread inside trader_brain.py.
Reads signal channels (TrueTrading, Vikingo) and data channel (FX Markets).
Writes NEW messages to pending_tg_msg.json for the brain to process.

Usage (from brain):
    from telegram_listener import start_listener
    start_listener()  # launches thread, returns immediately
"""

import os, json, threading, time, asyncio
from datetime import datetime, timezone

BASE = r"C:\Users\Administrator\Desktop\MT4 Claude\1_PROYECTO"
CONFIG_PATH = os.path.join(BASE, 'config.yaml')
SESSION_NAME = 'brain_session'  # separate session from main app (avoids conflicts)
# If 'brain_session.session' doesn't exist, listener will use main 'session.session' (read-only reads)

PENDING_FILE = r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files\brain_pending_tg.json"
SEEN_IDS_FILE = os.path.join(BASE, 'logs', 'brain_tg_seen.json')

MAX_PENDING = 50      # cap pending queue size
KEEP_SEEN_FOR = 3600  # remember message IDs for 1 hour

_lock = threading.Lock()
_listener_thread = None
_stop_flag = threading.Event()


def _log(msg):
    """Simple log helper (writes to brain log)."""
    try:
        import logging
        logging.getLogger('brain').info(f"[TG] {msg}")
    except Exception:
        print(f"[TG] {msg}")


def _read_seen():
    """Read seen message IDs (for deduplication across restarts)."""
    try:
        with open(SEEN_IDS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _write_seen(seen):
    try:
        os.makedirs(os.path.dirname(SEEN_IDS_FILE), exist_ok=True)
        with open(SEEN_IDS_FILE, 'w', encoding='utf-8') as f:
            json.dump(seen, f)
    except Exception:
        pass


def _append_pending(msg_dict):
    """Append a new TG message to pending queue."""
    with _lock:
        try:
            data = {'messages': []}
            if os.path.exists(PENDING_FILE):
                try:
                    data = json.load(open(PENDING_FILE, 'r', encoding='utf-8'))
                except Exception:
                    data = {'messages': []}
            # Cap queue size
            msgs = data.get('messages', [])
            msgs.append(msg_dict)
            if len(msgs) > MAX_PENDING:
                msgs = msgs[-MAX_PENDING:]
            data['messages'] = msgs
            with open(PENDING_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            _log(f"append_pending error: {e}")


def pop_pending():
    """Called by brain to consume and clear pending messages."""
    with _lock:
        try:
            if not os.path.exists(PENDING_FILE):
                return []
            data = json.load(open(PENDING_FILE, 'r', encoding='utf-8'))
            msgs = data.get('messages', [])
            # Clear the file
            with open(PENDING_FILE, 'w', encoding='utf-8') as f:
                json.dump({'messages': []}, f)
            return msgs
        except Exception as e:
            _log(f"pop_pending error: {e}")
            return []


def _run_async_listener():
    """Run telethon event loop in its own thread."""
    try:
        import yaml
        from telethon import TelegramClient, events
    except ImportError as e:
        _log(f"Missing dependency: {e}")
        return

    # Load config
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)
    except Exception as e:
        _log(f"Can't load config: {e}")
        return

    tg = cfg.get('telegram', {})
    api_id = tg.get('api_id')
    api_hash = tg.get('api_hash')
    channels_cfg = tg.get('channels', [])
    data_channels_cfg = tg.get('data_channels', [])

    if not api_id or not api_hash:
        _log("Missing api_id/api_hash in config")
        return

    # Use brain_session if available, else fall back to main session (read-only)
    session_path = os.path.join(BASE, SESSION_NAME)
    if not os.path.exists(session_path + '.session'):
        session_path = os.path.join(BASE, 'session')  # main app's session
        _log(f"brain_session not found, using main session (read-only)")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    client = TelegramClient(session_path, api_id, api_hash)

    seen = _read_seen()

    async def main_loop():
        try:
            await client.start()
            _log(f"Connected. Listening to {len(channels_cfg)} signal channels + {len(data_channels_cfg)} data channels")

            # Resolve channel entities
            entities = {}
            for ch_cfg in channels_cfg + data_channels_cfg:
                try:
                    ent = await client.get_entity(ch_cfg['id'])
                    entities[ch_cfg['id']] = {
                        'entity': ent,
                        'name': ch_cfg['name'],
                        'type': 'data' if ch_cfg in data_channels_cfg else 'signal',
                    }
                    _log(f"Connected to channel: {ch_cfg['name']}")
                except Exception as e:
                    _log(f"Can't resolve channel {ch_cfg.get('name','?')}: {e}")

            if not entities:
                _log("No channels available — listener exiting")
                return

            # Register event handler for NEW messages
            @client.on(events.NewMessage(chats=list(entities.keys())))
            async def on_new_message(event):
                try:
                    msg_id = str(event.message.id)
                    chat_id = event.chat_id
                    ch_info = entities.get(chat_id, {})
                    ch_name = ch_info.get('name', 'Unknown')
                    ch_type = ch_info.get('type', 'signal')

                    # Dedup
                    seen_key = f"{chat_id}:{msg_id}"
                    if seen_key in seen:
                        return
                    seen[seen_key] = time.time()

                    text = event.message.text or ''
                    # v3.3 fix: don't silently drop non-text messages. If a signal
                    # channel sends image-only / sticker / voice, we ALERT the
                    # user via TG bot so they can manually review. Previously
                    # these were dropped without trace.
                    media_kind = None
                    if event.message.photo:
                        media_kind = "photo"
                    elif event.message.sticker:
                        media_kind = "sticker"
                    elif event.message.video:
                        media_kind = "video"
                    elif event.message.voice or event.message.audio:
                        media_kind = "voice"
                    elif event.message.document:
                        media_kind = "document"

                    if not text.strip():
                        if ch_type == 'signal' and media_kind:
                            # Signal channel posted a media-only message — could be a signal image!
                            # Alert user via TG bot (fire-and-forget).
                            try:
                                from trader_brain import notify
                                notify("signal_received",
                                       f"⚠️ NON-TEXT MESSAGE from {ch_name}: {media_kind}\n"
                                       f"Could be a signal image. Check manually!")
                            except Exception:
                                pass
                            _log(f"MEDIA-ONLY from {ch_name} ({media_kind}) — alerted user, no auto-action possible")
                        else:
                            _log(f"Empty/media-only message from {ch_name} ({media_kind or 'unknown'}) — skipped")
                        return

                    msg_dict = {
                        'ts': time.time(),
                        'utc': datetime.now(timezone.utc).isoformat(),
                        'channel': ch_name,
                        'channel_id': chat_id,
                        'type': ch_type,  # 'signal' or 'data'
                        'msg_id': msg_id,
                        'text': text,
                        'media_kind': media_kind,  # if text+media, record media too
                    }
                    _append_pending(msg_dict)
                    _log(f"New message from {ch_name}: {text[:80]}")

                    # Save seen periodically
                    if len(seen) % 10 == 0:
                        # Prune old seen IDs
                        now = time.time()
                        for k in list(seen.keys()):
                            if now - seen[k] > KEEP_SEEN_FOR:
                                del seen[k]
                        _write_seen(seen)

                except Exception as e:
                    _log(f"on_new_message error: {e}")

            # v3.3 fix: TrueTrading edits almost every signal 1-4s after posting
            # (often posts image/photo first, then edits to add the caption text).
            # events.NewMessage never fires on edits — so we also register
            # events.MessageEdited. Dedup via seen_key prevents double-processing
            # if both fire (original had text + edit).
            @client.on(events.MessageEdited(chats=list(entities.keys())))
            async def on_message_edited(event):
                try:
                    msg_id = str(event.message.id)
                    chat_id = event.chat_id
                    ch_info = entities.get(chat_id, {})
                    ch_name = ch_info.get('name', 'Unknown')
                    ch_type = ch_info.get('type', 'signal')

                    # Dedup: if the original NewMessage already captured text,
                    # skip. If NOT captured (text was empty at post time), process now.
                    seen_key = f"{chat_id}:{msg_id}"
                    already_seen = seen_key in seen
                    text = event.message.text or ''
                    if not text.strip():
                        return  # edit made it empty, nothing to process
                    if already_seen:
                        # Original had text already — this edit is probably cosmetic
                        # (emoji tweak, typo fix). Don't re-process.
                        return

                    # Mark as seen now and process
                    seen[seen_key] = time.time()

                    msg_dict = {
                        'ts': time.time(),
                        'utc': datetime.now(timezone.utc).isoformat(),
                        'channel': ch_name,
                        'channel_id': chat_id,
                        'type': ch_type,
                        'msg_id': msg_id,
                        'text': text,
                        'via_edit': True,  # flag for debugging
                    }
                    _append_pending(msg_dict)
                    _log(f"Edited→processed message from {ch_name}: {text[:80]}")
                except Exception as e:
                    _log(f"on_message_edited error: {e}")

            _log("Event handler registered. Listening...")

            # Keep alive, check stop flag periodically
            while not _stop_flag.is_set():
                await asyncio.sleep(2)

            _log("Stop flag set, disconnecting...")
            await client.disconnect()
            _write_seen(seen)

        except Exception as e:
            _log(f"main_loop error: {e}")
            import traceback
            _log(traceback.format_exc())

    try:
        loop.run_until_complete(main_loop())
    except Exception as e:
        _log(f"run_until_complete error: {e}")
    finally:
        try: loop.close()
        except: pass


def start_listener():
    """Start the Telegram listener thread. Idempotent."""
    global _listener_thread
    if _listener_thread and _listener_thread.is_alive():
        return
    _stop_flag.clear()
    _listener_thread = threading.Thread(target=_run_async_listener, daemon=True, name='TG-Listener')
    _listener_thread.start()
    _log("Listener thread started")


def stop_listener():
    """Signal the listener to stop."""
    _stop_flag.set()


def is_alive():
    return _listener_thread is not None and _listener_thread.is_alive()


if __name__ == '__main__':
    # Standalone test mode
    import logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    start_listener()
    print("Listener started. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(5)
            msgs = pop_pending()
            for m in msgs:
                print(f"[{m['channel']}] {m['text'][:100]}")
    except KeyboardInterrupt:
        stop_listener()
        print("Stopping...")
