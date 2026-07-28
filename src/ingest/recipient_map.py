import asyncio
from urllib.parse import quote, unquote

from src.db.cache import address_key, get_cache


REJECT_MESSAGE = "REJECT test address is unknown or expired"


def lookup(to_address: str) -> str:
    cache = get_cache()
    if cache.exists(address_key(to_address)):
        return "OK"
    return REJECT_MESSAGE


async def handle_client(reader, writer):
    try:
        while True:
            line = await reader.readline()
            if not line:
                break

            parts = line.decode(errors="ignore").strip().split(" ", 1)
            if len(parts) != 2 or parts[0].lower() != "get":
                writer.write(b"400 only get is supported\n")
                await writer.drain()
                continue

            try:
                action = await asyncio.to_thread(lookup, unquote(parts[1]))
            except Exception as e:
                print("recipient lookup hatası:", repr(e), flush=True)
                writer.write(b"400 lookup failed\n")
                await writer.drain()
                continue

            writer.write(("200 " + quote(action) + "\n").encode())
            await writer.drain()
    finally:
        writer.close()
