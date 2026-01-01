from datetime import datetime, timezone, timedelta
from bson import ObjectId



def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_utc_day_start(current_time: datetime) -> datetime:
    return current_time.replace(hour=0, minute=0, second=0, microsecond=0)


def get_utc_tomorrow_start(current_time: datetime) -> datetime:
    return get_utc_day_start(current_time) + timedelta(days=1)




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


def reset_user_daily_quota_if_needed(db,user_id: str,current_time: datetime,) -> int:

    user_document = db.users.find_one(
        {"_id": ObjectId(user_id)},
        {"quota": 1}
    )

    analyze_quota = (
        (user_document.get("quota") or {})
        .get("analyze", {})
    )

    daily_used = int(analyze_quota.get("daily_used", 0))
    reset_at = analyze_quota.get("reset_at")

    if not reset_at or reset_at <= current_time:
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



def get_anonymous_daily_usage(db,client_ip: str,current_time: datetime,) -> int:

    return db.test_emails.count_documents({
        "created_ip": client_ip,
        "analysis_started_at": {
            "$gte": get_utc_day_start(current_time)
        }
    })



def can_start_email_analysis(db, email_context: dict) -> bool:

    current_time = get_utc_now()

    if email_context.get("analysis_started_at"):
        return True

    if email_context.get("owner_user_id"):
        user_id = email_context["owner_user_id"]
        DAILY_ANALYZE_LIMIT = 10

        daily_used = reset_user_daily_quota_if_needed(
            db=db,
            user_id=user_id,
            current_time=current_time,
        )

        if daily_used >= DAILY_ANALYZE_LIMIT:
            return False

        consume_user_daily_quota(db, user_id)

    else:
        client_ip = email_context.get("created_ip")
        DAILY_ANALYZE_LIMIT = 5

        daily_used = get_anonymous_daily_usage(
            db=db,
            client_ip=client_ip,
            current_time=current_time,
        )

        if daily_used >= DAILY_ANALYZE_LIMIT:
            return False

    return True


def mark_email_analysis_started(db, to_address: str) -> None:

    db.test_emails.update_one(
        {"to_address": to_address},
        {"$set": {"analysis_started_at": get_utc_now()}}
    )
