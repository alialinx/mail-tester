from datetime import datetime, timezone, timedelta

from bson import ObjectId

from src.api.utils.time import ensure_utc_aware
from src.config import ANON_DAILY_LIMIT, USER_DAILY_LIMIT, API_DAILY_LIMIT


def utc_now():
    return datetime.now(timezone.utc)

def utc_day_start(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)

def get_utc_tomorrow_start(current_time: datetime) -> datetime:
    return utc_day_start(current_time) + timedelta(days=1)


def get_test_email_context(db, to_address: str) -> dict:
    return db.test_emails.find_one(
        {"to_address": to_address},
        {
            "to_address": 1,
            "owner_user_id": 1,
            "created_ip": 1,
        }
    )


def get_anonymous_daily_usage(db, client_ip: str, current_time: datetime) -> int:
    return db.mail_events.count_documents({
        "created_ip": client_ip,
        "analysis_id": {"$ne": None},
        "analyzed_at": {"$gte": utc_day_start(current_time)}
    })


def claim_mail_event(db, event_id) -> bool:
    claimed = db.mail_events.update_one(
        {"_id": ObjectId(event_id), "analysis_started_at": None},
        {"$set": {"analysis_started_at": utc_now()}}
    )
    return claimed.modified_count == 1


def quota_holder(db, owner_user_id: str = None, api_key_id: str = None):
    if api_key_id:
        return db.api_keys, {"_id": ObjectId(api_key_id)}, "quota", API_DAILY_LIMIT, "api_key"
    if owner_user_id:
        return db.users, {"_id": ObjectId(owner_user_id)}, "quota.analyze", USER_DAILY_LIMIT, "user"
    return None, None, None, None, "anonymous"


def consume_daily_quota(db, owner_user_id: str = None, client_ip: str = None, api_key_id: str = None) -> bool:
    now = utc_now()
    collection, query, path, default_limit, scope = quota_holder(db, owner_user_id, api_key_id)

    if collection is not None:
        holder = collection.find_one(query, {path.split(".")[0]: 1}) or {}
        quota = holder
        for part in path.split("."):
            quota = (quota or {}).get(part) or {}

        daily_limit = int(quota.get("daily_limit", default_limit))
        daily_used = int(quota.get("daily_used", 0))
        reset_at = ensure_utc_aware(quota.get("reset_at"))

        if (reset_at is None) or (reset_at <= now):
            daily_used = 0
            collection.update_one(query, {"$set": {
                f"{path}.daily_used": 0,
                f"{path}.reset_at": get_utc_tomorrow_start(now),
            }})

        if daily_used >= daily_limit:
            return False

        collection.update_one(query, {"$inc": {f"{path}.daily_used": 1}})
        return True

    return get_anonymous_daily_usage(db, client_ip or "unknown", now) < ANON_DAILY_LIMIT


def get_quota_state(db, owner_user_id: str = None, client_ip: str = None, api_key_id: str = None) -> dict:
    now = utc_now()
    collection, query, path, default_limit, scope = quota_holder(db, owner_user_id, api_key_id)

    if collection is not None:
        holder = collection.find_one(query, {path.split(".")[0]: 1}) or {}
        quota = holder
        for part in path.split("."):
            quota = (quota or {}).get(part) or {}

        limit = int(quota.get("daily_limit", default_limit))
        used = int(quota.get("daily_used", 0))
        reset_at = ensure_utc_aware(quota.get("reset_at"))

        if (reset_at is None) or (reset_at <= now):
            used = 0
            reset_at = get_utc_tomorrow_start(now)

        return {"scope": scope, "limit": limit, "used": used,
                "remaining": max(0, limit - used), "reset_at": reset_at}

    used = get_anonymous_daily_usage(db, client_ip or "unknown", now)

    return {"scope": "anonymous", "limit": ANON_DAILY_LIMIT, "used": used,
            "remaining": max(0, ANON_DAILY_LIMIT - used),
            "reset_at": get_utc_tomorrow_start(now)}


