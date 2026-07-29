import email
from datetime import datetime, timezone
from email.utils import parseaddr

import gridfs
from bson import ObjectId

from src.db.db import get_db
from src.processor.analyzer import Analyzer
from src.processor.service import get_sender_ip
from src.worker.celery_app import celery_app
from src.worker.limits import claim_mail_event, consume_daily_quota


def get_sender_domain(msg) -> str:
    _, address = parseaddr(msg.get("From") or "")
    if "@" not in address:
        return ""
    return address.rsplit("@", 1)[-1].strip().lower()


@celery_app.task(bind=True, max_retries=3)
def analyze_received_mail(self, mail_event_id: str):
    db = get_db()

    event = db.mail_events.find_one({"_id": ObjectId(mail_event_id)})
    if not event:
        return None

    to_address = event["to_address"]

    if not claim_mail_event(db, mail_event_id):
        return None

    try:
        allowed = consume_daily_quota(
            db,
            owner_user_id=event.get("owner_user_id"),
            client_ip=event.get("created_ip"),
        )

        if not allowed:
            db.mail_events.update_one(
                {"_id": ObjectId(mail_event_id)},
                {"$set": {"last_error": "daily_analyze_limit_exceeded", "analysis_started_at": None}}
            )
            db.test_emails.update_one(
                {"to_address": to_address},
                {"$set": {"status": "limit", "last_error": "daily_analyze_limit_exceeded"}}
            )
            return None

        db.test_emails.update_one(
            {"to_address": to_address},
            {"$set": {"status": "processing", "last_error": None}}
        )

        fs = gridfs.GridFS(db, collection="raw_mails")
        raw = fs.get(ObjectId(event["raw_id"])).read()
        msg = email.message_from_bytes(raw)

        connection = event.get("connection") or {}
        sender_ip = connection.get("client_ip") or get_sender_ip(msg)
        domain = get_sender_domain(msg)

        analyzer = Analyzer(email_message=msg, domain=domain, sender_ip=sender_ip)
        result = analyzer.analyze()

        result["spamassassin"] = result["checks"]["spamassassin"]
        result["connection"] = connection

        result["meta"].update({
            "to_address": to_address,
            "received_at": event["received_at"].isoformat(),
            "sender_domain": domain,
            "sender_ip": sender_ip,
            "envelope_from": event.get("mail_from"),
        })
        result["owner"] = {
            "type": "user" if event.get("owner_user_id") else "anonymous",
            "user_id": event.get("owner_user_id"),
            "ip": event.get("created_ip"),
        }
        result["mail_event_id"] = mail_event_id

        inserted = db.analyses.insert_one(result)
        now = datetime.now(timezone.utc)

        db.mail_events.update_one(
            {"_id": ObjectId(mail_event_id)},
            {"$set": {"analysis_id": str(inserted.inserted_id), "analyzed_at": now}}
        )

        db.test_emails.update_one(
            {"to_address": to_address},
            {"$set": {
                "status": "analyzed",
                "analysis_id": str(inserted.inserted_id),
                "analyzed_at": now,
                "last_error": None,
            }}
        )

        return None

    except Exception as e:
        db.mail_events.update_one(
            {"_id": ObjectId(mail_event_id)},
            {"$set": {"last_error": repr(e)}}
        )
        db.test_emails.update_one(
            {"to_address": to_address},
            {"$set": {"status": "error", "last_error": repr(e)}}
        )
        raise
