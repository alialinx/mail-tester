import asyncio

from aiosmtpd.lmtp import LMTP

from src.config import INGEST_LMTP_PORT, INGEST_MAP_PORT, MAIL_DOMAIN, MESSAGE_SIZE_LIMIT
from src.ingest.lmtp_server import MailHandler
from src.ingest.recipient_map import handle_client


async def main():
    if not MAIL_DOMAIN:
        raise RuntimeError("MAIL_DOMAIN tanımlı değil, .env dosyasını kontrol et")

    loop = asyncio.get_running_loop()
    handler = MailHandler()

    lmtp = await loop.create_server(
        lambda: LMTP(handler, data_size_limit=MESSAGE_SIZE_LIMIT, enable_SMTPUTF8=True),
        host="0.0.0.0",
        port=INGEST_LMTP_PORT,
    )

    recipient_map = await asyncio.start_server(
        handle_client,
        host="0.0.0.0",
        port=INGEST_MAP_PORT,
    )

    print(f"ingest çalışıyor: lmtp={INGEST_LMTP_PORT} recipient_map={INGEST_MAP_PORT} domain={MAIL_DOMAIN}", flush=True)

    async with lmtp, recipient_map:
        await asyncio.gather(lmtp.serve_forever(), recipient_map.serve_forever())


if __name__ == "__main__":
    asyncio.run(main())
