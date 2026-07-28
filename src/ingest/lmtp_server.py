import asyncio
import email
from datetime import datetime, timezone

import gridfs

from src.config import MAIL_DOMAIN, MESSAGE_SIZE_LIMIT
from src.db.cache import address_key, get_cache
from src.db.db import get_db
from src.ingest.connection import get_connection_info


def store_message(to_address: str, mail_from: str, raw: bytes) -> str:
    db = get_db()
    fs = gridfs.GridFS(db, collection="raw_mails")

    msg = email.message_from_bytes(raw)
    connection = get_connection_info(msg)
    now = datetime.now(timezone.utc)

    raw_id = fs.put(raw, filename=to_address, upload_date=now)

    event = {
        "to_address": to_address,
        "mail_from": mail_from,
        "raw_id": str(raw_id),
        "size": len(raw),
        "received_at": now,
        "connection": connection,
        "subject": msg.get("Subject"),
        "message_id": msg.get("Message-ID"),
    }

    inserted = db.mail_events.insert_one(event)

    db.test_emails.update_one(
        {"to_address": to_address},
        {"$set": {
            "status": "received",
            "receiver_at": now,
            "mail_event_id": str(inserted.inserted_id),
            "last_error": None,
        }}
    )

    # Adres tek kullanımlık: aynı adrese ikinci mail artık RCPT aşamasında reddedilir
    get_cache().delete(address_key(to_address))

    return str(inserted.inserted_id)


class MailHandler:

    async def handle_DATA(self, server, session, envelope):
        raw = envelope.content or b""

        if len(raw) > MESSAGE_SIZE_LIMIT:
            return "552 Message too large"

        mail_from = envelope.mail_from or ""

        for rcpt in envelope.rcpt_tos:
            to_address = (rcpt or "").strip().lower()

            if not to_address.endswith("@" + (MAIL_DOMAIN or "").lower()):
                print("beklenmeyen domain, atlandı:", to_address, flush=True)
                continue

            try:
                event_id = await asyncio.to_thread(store_message, to_address, mail_from, raw)
            except Exception as e:
                print("mail kaydedilemedi:", to_address, repr(e), flush=True)
                # 4xx dönüyoruz, Postfix kuyrukta tutup tekrar deniyor. Mail kaybolmuyor.
                return "451 Temporary failure, try again"

            print("mail alındı:", to_address, event_id, flush=True)

            # Task'ı burada import ediyoruz, celery_app'in ingest açılışını yavaşlatmaması için
            from src.worker.tasks import analyze_received_mail
            analyze_received_mail.delay(event_id)

        return "250 Message accepted"
