from datetime import datetime, timezone
from celery.exceptions import Retry

from src.db.db import get_db
from src.imap.imap import get_email_from_imap
from src.processor.analyzer import Analyzer
from src.processor.service import get_sender_ip
from src.worker.celery_app import celery_app


@celery_app.task(bind=True, max_retries=30)
def pull_and_analyze(self, to_address: str):
    db = get_db()

    try:

        db.test_emails.update_one(
            {"to_address": to_address},
            {"$set": {"status": "processing"}}
        )


        msg = get_email_from_imap(to_address)

        if not msg:
            db.test_emails.update_one(
                {"to_address": to_address},
                {"$set": {"last_error": "waiting_for_email"}}
            )
            raise self.retry(countdown=10)


        db.test_emails.update_one(
            {"to_address": to_address},
            {"$set": {
                "receiver_at": datetime.now(timezone.utc),
                "last_error": None
            }}
        )

        from_header = msg.get("From") or ""
        domain = from_header.split("@")[-1].replace(">", "").strip()
        sender_ip = get_sender_ip(msg)

        analyzer = Analyzer(
            email_message=msg,
            domain=domain,
            sender_ip=sender_ip
        )
        result = analyzer.analyze()

        inserted = db.analyses.insert_one(result)
        analysis_id = str(inserted.inserted_id)

        db.test_emails.update_one(
            {"to_address": to_address},
            {"$set": {
                "status": "analyzed",
                "analysis_id": analysis_id,
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
