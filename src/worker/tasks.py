import email
from datetime import datetime, timezone
from email.utils import parseaddr

import gridfs
from bson import ObjectId
from celery.exceptions import Retry

from src.db.cache import publish_status
from src.db.db import get_db
from src.imap.imap import get_email_from_imap
from src.processor.analyzer import Analyzer
from src.processor.service import get_sender_ip
from src.worker.celery_app import celery_app
from src.worker.limits import get_test_email_context, try_consume_quota_once


def get_sender_domain(msg) -> str:
    _, address = parseaddr(msg.get("From") or "")
    if "@" not in address:
        return ""
    return address.rsplit("@", 1)[-1].strip().lower()


@celery_app.task(bind=True, max_retries=30)
def pull_and_analyze(self, to_address: str):
    db = get_db()

    try:


        msg = get_email_from_imap(to_address)

        if not msg:
            db.test_emails.update_one({"to_address": to_address},{"$set": {"last_error": "waiting_for_email"}})
            raise self.retry(countdown=10)

        email_context = get_test_email_context(db, to_address)

        if not email_context:
            return None

        allowed = try_consume_quota_once(db=db,to_address=to_address,email_context=email_context,)

        if not allowed:
            return None

        now = datetime.now(timezone.utc)

        db.test_emails.update_one(
            {"to_address": to_address},
            {"$set": {
                "status": "processing",
                "receiver_at": now,
                "last_error": None,
            }}
        )

        from_header = msg.get("From") or ""
        domain = from_header.split("@")[-1].replace(">", "").strip()
        sender_ip = get_sender_ip(msg)

        analyzer = Analyzer(email_message=msg,domain=domain,sender_ip=sender_ip)
        result = analyzer.analyze()

        result["meta"] = {
            "to_address": to_address,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "sender_domain": domain,
            "sender_ip": sender_ip,
            "message_id": msg.get("Message-ID"),
            "subject": msg.get("Subject"),
        }

        # Analyzer taramayı çoktan yaptı, ikinci kez spamd'ye göndermiyoruz
        result["spamassassin"] = result["checks"]["spamassassin"]
        result["owner"] = {
            "type": "user" if email_context.get("owner_user_id") else "anonymous",
            "user_id": email_context.get("owner_user_id"),
            "ip": email_context.get("created_ip"),
        }

        inserted = db.analyses.insert_one(result)

        now = datetime.now(timezone.utc)

        db.test_emails.update_one(
            {"to_address": to_address},
            {
                "$set": {
                    "status": "analyzed",
                    "analysis_id": str(inserted.inserted_id),
                    "analyzed_at": now,
                    "last_error": None
                },
                "$unset": {
                    "expires_at": ""
                }
            }
        )

        return None

    except Retry:
        raise

    except Exception as e:
        db.test_emails.update_one(
            {"to_address": to_address},
            {"$set": {
                "status": "error",
                "last_error": repr(e)
            }}
        )
        raise


@celery_app.task(bind=True, max_retries=3)
def analyze_received_mail(self, mail_event_id: str):
    """ingest maili teslim aldıktan sonra çağrılır. IMAP yok, bekleme yok."""
    db = get_db()

    event = db.mail_events.find_one({"_id": ObjectId(mail_event_id)})
    if not event:
        return None

    to_address = event["to_address"]

    try:
        email_context = get_test_email_context(db, to_address)
        if not email_context:
            return None

        if not try_consume_quota_once(db=db, to_address=to_address, email_context=email_context):
            return None

        db.test_emails.update_one(
            {"to_address": to_address},
            {"$set": {
                "status": "processing",
                "last_error": None,
            }}
        )
        publish_status(to_address, "processing")

        fs = gridfs.GridFS(db, collection="raw_mails")
        raw = fs.get(ObjectId(event["raw_id"])).read()
        msg = email.message_from_bytes(raw)

        connection = event.get("connection") or {}

        # Gönderen IP'si artık tahmin değil: SMTP bağlantısının kendisinden geliyor.
        sender_ip = connection.get("client_ip") or get_sender_ip(msg)
        domain = get_sender_domain(msg)

        analyzer = Analyzer(email_message=msg, domain=domain, sender_ip=sender_ip)
        result = analyzer.analyze()

        result["spamassassin"] = result["checks"]["spamassassin"]
        result["connection"] = connection

        # Analyzer meta içine message_detail koyuyor, ezmeyip üstüne ekliyoruz
        result["meta"].update({
            "to_address": to_address,
            "received_at": event["received_at"].isoformat(),
            "sender_domain": domain,
            "sender_ip": sender_ip,
            "envelope_from": event.get("mail_from"),
        })
        result["owner"] = {
            "type": "user" if email_context.get("owner_user_id") else "anonymous",
            "user_id": email_context.get("owner_user_id"),
            "ip": email_context.get("created_ip"),
        }

        inserted = db.analyses.insert_one(result)

        db.test_emails.update_one(
            {"to_address": to_address},
            {
                "$set": {
                    "status": "analyzed",
                    "analysis_id": str(inserted.inserted_id),
                    "analyzed_at": datetime.now(timezone.utc),
                    "last_error": None,
                },
                "$unset": {
                    "expires_at": ""
                }
            }
        )
        publish_status(to_address, "analyzed")

        return None

    except Exception as e:
        db.test_emails.update_one(
            {"to_address": to_address},
            {"$set": {
                "status": "error",
                "last_error": repr(e)
            }}
        )
        publish_status(to_address, "error")
        raise
