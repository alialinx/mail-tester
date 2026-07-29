from datetime import datetime, timezone, timedelta

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException

from src.api.functions import get_request_info, optional_current_user
from src.api.rate_limit import enforce
from src.config import GENERATE_RATE_LIMIT, GENERATE_RATE_WINDOW, TEST_ADDRESS_TTL_MINUTES
from src.db.cache import address_key, get_cache
from src.db.db import get_db
from src.processor.generator import generate_random_email
from src.worker.limits import get_quota_state

router = APIRouter()


def owner_of(current_user) -> str:
    return str(current_user["user_id"]) if current_user else None


def quota_payload(quota: dict) -> dict:
    return {
        "scope": quota["scope"],
        "limit": quota["limit"],
        "used": quota["used"],
        "remaining": quota["remaining"],
        "reset_at": quota["reset_at"],
    }


def address_is_live(db, to_address: str) -> bool:
    test_email = db.test_emails.find_one({"to_address": to_address}, {"expires_at": 1})

    if not test_email:
        return False

    expires_at = test_email.get("expires_at")
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    return bool(expires_at and expires_at > datetime.now(timezone.utc))


def newest_event(db, to_address: str, after: str = None) -> dict:
    query = {"to_address": to_address}

    if after:
        try:
            query["_id"] = {"$gt": ObjectId(after)}
        except (InvalidId, TypeError):
            raise HTTPException(status_code=400, detail="Invalid event id")

    return db.mail_events.find_one(query, sort=[("_id", -1)])


def read_analysis(db, analysis_id: str) -> dict:
    analysis = db.analyses.find_one({"_id": ObjectId(analysis_id)})
    if not analysis:
        return None
    analysis["_id"] = str(analysis["_id"])
    return analysis


@router.get("/limits", tags=["test"])
def get_limits(db=Depends(get_db), req_info=Depends(get_request_info), current_user=Depends(optional_current_user)):
    quota = get_quota_state(db, owner_of(current_user), req_info.get("ip"))
    payload = quota_payload(quota)
    payload["address_ttl_seconds"] = TEST_ADDRESS_TTL_MINUTES * 60
    return payload


@router.post("/generate", tags=["test"])
def generate_random(db=Depends(get_db), req_info=Depends(get_request_info), current_user=Depends(optional_current_user)):
    created_ip = req_info.get("ip")
    owner_user_id = owner_of(current_user)

    enforce("generate", owner_user_id or created_ip, GENERATE_RATE_LIMIT, GENERATE_RATE_WINDOW)

    quota = get_quota_state(db, owner_user_id, created_ip)

    now = datetime.now(timezone.utc)
    to_address = generate_random_email()

    previous = {"expires_at": {"$gt": now}}
    if owner_user_id:
        previous["owner_user_id"] = owner_user_id
    else:
        previous["created_ip"] = created_ip

    cache = get_cache()

    for old in db.test_emails.find(previous, {"to_address": 1}):
        cache.delete(address_key(old["to_address"]))

    db.test_emails.update_many(previous, {"$set": {"status": "expired"}, "$unset": {"expires_at": ""}})

    expires_at = now + timedelta(minutes=TEST_ADDRESS_TTL_MINUTES)

    db.test_emails.insert_one({
        "to_address": to_address,
        "status": "pending",
        "created_at": now,
        "expires_at": expires_at,
        "created_ip": created_ip,
        "owner_user_id": owner_user_id,
        "mail_count": 0,
        "receiver_at": None,
        "last_mail_event_id": None,
        "analysis_id": None,
        "analyzed_at": None,
        "last_error": None,
    })

    cache.set(address_key(to_address), "1", ex=TEST_ADDRESS_TTL_MINUTES * 60)

    return {
        "address": to_address,
        "result": to_address,
        "expires_at": expires_at,
        "expires_in": TEST_ADDRESS_TTL_MINUTES * 60,
        "limits": quota_payload(quota),
    }


@router.get("/check/{to_address}", tags=["test"])
def check_address(to_address: str, after: str = None, db=Depends(get_db)):
    event = newest_event(db, to_address, after)

    if not event:
        if address_is_live(db, to_address):
            return {"status": "waiting"}
        return {"status": "expired"}

    event_id = str(event["_id"])

    if event.get("analysis_id"):
        analysis = read_analysis(db, event["analysis_id"])
        if analysis:
            return {"status": "analyzed", "event_id": event_id, "result": analysis}
        return {"status": "error", "event_id": event_id, "detail": "analysis missing"}

    if event.get("last_error") == "daily_analyze_limit_exceeded":
        quota = get_quota_state(
            db,
            owner_user_id=event.get("owner_user_id"),
            client_ip=event.get("created_ip"),
        )

        if quota["remaining"] <= 0:
            return {"status": "limit", "event_id": event_id}

        db.mail_events.update_one({"_id": event["_id"]}, {"$set": {"last_error": None}})
        event["last_error"] = None

    if event.get("last_error"):
        return {"status": "error", "event_id": event_id, "detail": event["last_error"]}

    if not event.get("analysis_started_at"):
        from src.worker.tasks import analyze_received_mail
        analyze_received_mail.delay(event_id)

    return {"status": "processing", "event_id": event_id}


@router.get("/result/{to_address}", tags=["test"])
def get_result(to_address: str, db=Depends(get_db)):
    event = db.mail_events.find_one(
        {"to_address": to_address, "analysis_id": {"$ne": None}},
        sort=[("_id", -1)]
    )

    if not event:
        if address_is_live(db, to_address):
            return {"status": "waiting"}
        return {"status": "expired"}

    analysis = read_analysis(db, event["analysis_id"])
    if not analysis:
        return {"status": "error", "detail": "analysis missing"}

    return {"status": "analyzed", "event_id": str(event["_id"]), "result": analysis}
