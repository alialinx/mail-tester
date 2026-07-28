import asyncio
import email
from datetime import datetime, timezone

import gridfs

from src.config import MAIL_DOMAIN, MESSAGE_SIZE_LIMIT
from src.db.db import get_db
from src.ingest.connection import get_connection_info


def store_message(to_address: str, mail_from: str, raw: bytes) -> str:
    db = get_db()
    fs = gridfs.GridFS(db, collection="raw_mails")

    test_email = db.test_emails.find_one(
        {"to_address": to_address},
        {"created_ip": 1, "owner_user_id": 1}
    ) or {}

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
        "created_ip": test_email.get("created_ip"),
        "owner_user_id": test_email.get("owner_user_id"),
        "analysis_started_at": None,
        "analysis_id": None,
        "analyzed_at": None,
        "last_error": None,
    }

    inserted = db.mail_events.insert_one(event)

    db.test_emails.update_one(
        {"to_address": to_address},
        {
            "$set": {
                "status": "received",
                "receiver_at": now,
                "last_mail_event_id": str(inserted.inserted_id),
                "last_error": None,
            },
            "$inc": {"mail_count": 1},
        }
    )

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
                return "451 Temporary failure, try again"

            print("mail alındı:", to_address, event_id, flush=True)

            from src.worker.tasks import analyze_received_mail
            analyze_received_mail.delay(event_id)

        return "250 Message accepted"
