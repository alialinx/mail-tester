import asyncio
from urllib.parse import quote, unquote

from src.db.cache import address_key, get_cache

# Postfix tcp_table(5) protokolü. Postfix her RCPT TO için "get <adres>" gönderiyor,
# biz access(5) aksiyonu döndürüyoruz:
#   200 OK        -> adres canlı, maili al
#   200 REJECT .. -> adres yok veya süresi geçmiş, SMTP konuşmasında reddet
#   400 ..        -> geçici hata, Postfix sonra tekrar denesin
#
# Böylece rastgele adreslere gelen spam kuyruğa hiç girmiyor, diske yazılmıyor.

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
                # Redis çağrısı bloklayıcı, event loop'u tutmasın
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
