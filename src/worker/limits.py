from datetime import datetime, timezone, timedelta

from bson import ObjectId

from src.api.utils.time import ensure_utc_aware

# --- Time helpers ---
def utc_now():
    return datetime.now(timezone.utc)

def utc_day_start(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)

def get_utc_tomorrow_start(current_time: datetime) -> datetime:
    return get_utc_day_start(current_time) + timedelta(days=1)

# --- Read helpers ---
def get_test_email_context(db, to_address: str) -> dict:
    return db.test_emails.find_one(
        {"to_address": to_address},
        {
            "to_address": 1,
            "owner_user_id": 1,
            "created_ip": 1,
            "analysis_started_at": 1,
        }
    )



# --- Quota helpers ---
def reset_user_daily_quota_if_needed(db, user_id: str, current_time: datetime, ) -> int:
    user_document = db.users.find_one(
        {"_id": ObjectId(user_id)},
        {"quota": 1}
    )

    analyze_quota = (
        (user_document.get("quota") or {})
        .get("analyze", {})
    )

    daily_used = int(analyze_quota.get("daily_used", 0))
    reset_at = ensure_utc_aware(analyze_quota.get("reset_at"))

    if (reset_at is None) or (reset_at <= current_time):

        db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {
                "quota.analyze.daily_used": 0,
                "quota.analyze.reset_at": get_utc_tomorrow_start(current_time),
            }}
        )
        return 0

    return daily_used

def consume_user_daily_quota(db, user_id: str) -> None:
    db.users.update_one(
        {"_id": ObjectId(user_id)},
        {"$inc": {"quota.analyze.daily_used": 1}}
    )

def get_anonymous_daily_usage(db, client_ip: str, current_time: datetime) -> int:
    return db.test_emails.count_documents({
        "created_ip": client_ip,
        "status": "analyzed",
        "analyzed_at": {"$gte": utc_day_start(current_time)}
    })

def try_consume_quota_once(db, to_address: str, email_context: dict) -> bool:
    now = utc_now()

    claimed = db.test_emails.update_one(
        {"to_address": to_address, "analysis_started_at": None},
        {"$set": {"analysis_started_at": now}}
    )

    if claimed.modified_count == 0:
        return True

    if email_context.get("owner_user_id"):
        user_id = email_context["owner_user_id"]

        user = db.users.find_one({"_id": ObjectId(user_id)}, {"quota": 1}) or {}
        q = (user.get("quota") or {}).get("analyze", {}) or {}
        daily_limit = int(q.get("daily_limit", 10))
        daily_used = int(q.get("daily_used", 0))
        reset_at = q.get("reset_at")


        if reset_at and reset_at.tzinfo is None:
            reset_at = reset_at.replace(tzinfo=timezone.utc)

        if (reset_at is None) or (reset_at <= now):
            daily_used = 0
            tomorrow = utc_day_start(now) + timedelta(days=1)
            db.users.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": {"quota.analyze.daily_used": 0, "quota.analyze.reset_at": tomorrow}}
            )

        if daily_used >= daily_limit:

            db.test_emails.update_one(
                {"to_address": to_address},
                {"$set": {"status": "error", "last_error": "daily_analyze_limit_exceeded"}}
            )
            return False

        db.users.update_one({"_id": ObjectId(user_id)}, {"$inc": {"quota.analyze.daily_used": 1}})
        return True

    else:
        client_ip = email_context.get("created_ip") or "unknown"
        daily_limit = 5

        used = db.test_emails.count_documents({
            "created_ip": client_ip,
            "status": "analyzed",
            "analyzed_at": {"$gte": utc_day_start(now)}
        })

        if used >= daily_limit:
            db.test_emails.update_one(
                {"to_address": to_address},
                {"$set": {"status": "error", "last_error": "daily_analyze_limit_exceeded"}}
            )
            return False

        return True