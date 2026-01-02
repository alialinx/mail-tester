from datetime import datetime, timezone, timedelta

from bson import ObjectId

from src.api.utils.time import ensure_utc_aware

# --- Time helpers ---
def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)

def get_utc_day_start(current_time: datetime) -> datetime:
    return current_time.replace(hour=0, minute=0, second=0, microsecond=0)

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
        "analyzed_at": {"$gte": get_utc_day_start(current_time)}
    })

def try_consume_quota_once(db, email_context: dict) -> bool:

    now = get_utc_now()
    to_address = email_context["to_address"]


    res = db.test_emails.update_one(
        {"to_address": to_address, "analysis_started_at": {"$exists": False}},
        {"$set": {"analysis_started_at": now}}
    )


    if res.modified_count == 0:
        return True

    if email_context.get("owner_user_id"):
        user_id = email_context["owner_user_id"]

        quota_doc = db.users.find_one(
            {"_id": ObjectId(user_id)},
            {"quota.analyze.daily_limit": 1, "quota.analyze.daily_used": 1, "quota.analyze.reset_at": 1}
        ) or {}

        analyze_quota = ((quota_doc.get("quota") or {}).get("analyze", {}))
        daily_limit = int(analyze_quota.get("daily_limit", 10))
        reset_at = ensure_utc_aware(analyze_quota.get("reset_at"))

        if (reset_at is None) or (reset_at <= now):
            daily_used = 0
            reset_at = get_utc_tomorrow_start(now)
            db.users.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": {
                    "quota.analyze.daily_used": 0,
                    "quota.analyze.reset_at": reset_at,
                }}
            )

        if daily_used >= daily_limit:
            db.test_emails.update_one(
                {"to_address": to_address},
                {"$unset": {"analysis_started_at": ""}}
            )
            return False

        db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$inc": {"quota.analyze.daily_used": 1}}
        )
        return True

    client_ip = email_context.get("created_ip")
    daily_limit = 5
    daily_used = get_anonymous_daily_usage(db=db, client_ip=client_ip, current_time=now)

    if daily_used >= daily_limit:
        db.test_emails.update_one(
            {"to_address": to_address},
            {"$unset": {"analysis_started_at": ""}}
        )
        return False

    return True
