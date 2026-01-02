from datetime import datetime, timezone
from celery.exceptions import Retry

from src.db.db import get_db
from src.imap.imap import get_email_from_imap
from src.processor.analyzer import Analyzer
from src.processor.service import get_sender_ip
from src.worker.celery_app import celery_app
from src.worker.limits import get_test_email_context, can_start_email_analysis, mark_email_analysis_started, try_consume_quota_once
from src.worker.spamassassin_client import spamd_check


@celery_app.task(bind=True, max_retries=30)
def pull_and_analyze(self, to_address: str):
    db = get_db()

    try:


        msg = get_email_from_imap(to_address)

        if not msg:
            db.test_emails.update_one({"to_address": to_address},{"$set": {"last_error": "waiting_for_email"}})
            raise self.retry(countdown=10)

        email_context = get_test_email_context(db, to_address)

        if not try_consume_quota_once(db, email_context):
            db.test_emails.update_one(
                {"to_address": to_address},
                {"$set": {"status": "error", "last_error": "daily_analyze_limit_exceeded"}}
            )
            return None

        db.test_emails.update_one(
            {"to_address": to_address},
            {"$set": {
                "status": "processing",
                "receiver_at": datetime.now(timezone.utc),
                "last_error": None
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

        raw_bytes = (
            msg.as_bytes()
            if hasattr(msg, "as_bytes")
            else (msg.as_string() or "").encode("utf-8", "ignore")
        )

        result["spamassassin"] = spamd_check(raw_bytes)
        result["owner"] = {
            "type": "user" if email_context.get("owner_user_id") else "anonymous",
            "user_id": email_context.get("owner_user_id"),
            "ip": email_context.get("created_ip"),
        }

        inserted = db.analyses.insert_one(result)

        db.test_emails.update_one(
            {"to_address": to_address},
            {"$set": {
                "status": "analyzed",
                "analysis_id": str(inserted.inserted_id),
                "last_error": None
            }}
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
