from datetime import datetime, timezone, timedelta

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from fastapi import Request

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


@router.get("/debug/ip")
def debug_ip(request: Request):
    return {
        "client": request.client.host if request.client else None,
        "x_real_ip": request.headers.get("x-real-ip"),
        "x_forwarded_for": request.headers.get("x-forwarded-for"),
    }


@router.get("/limits", tags=["Generate"])
def get_limits(db=Depends(get_db), req_info=Depends(get_request_info), current_user=Depends(optional_current_user)):
    quota = get_quota_state(db, owner_of(current_user), req_info.get("ip"))

    return {
        "scope": quota["scope"],
        "limit": quota["limit"],
        "used": quota["used"],
        "remaining": quota["remaining"],
        "reset_at": quota["reset_at"],
        "address_ttl_seconds": TEST_ADDRESS_TTL_MINUTES * 60,
    }


@router.post("/generate", tags=["Generate"])
def generate_random(db=Depends(get_db), req_info=Depends(get_request_info), current_user=Depends(optional_current_user), ):
    created_ip = req_info.get("ip")
    owner_user_id = owner_of(current_user)

    enforce("generate", owner_user_id or created_ip, GENERATE_RATE_LIMIT, GENERATE_RATE_WINDOW)

    quota = get_quota_state(db, owner_user_id, created_ip)

    if quota["remaining"] <= 0:
        raise HTTPException(
            status_code=429,
            detail="Daily test limit reached",
            headers={"X-Quota-Limit": str(quota["limit"]), "X-Quota-Used": str(quota["used"])},
        )

    now = datetime.now(timezone.utc)
    to_address = generate_random_email()

    query = {"status": "pending", "expires_at": {"$gt": now}, }

    if owner_user_id:
        query["owner_user_id"] = owner_user_id
    else:
        query["created_ip"] = created_ip

    cache = get_cache()

    for previous in db.test_emails.find(query, {"to_address": 1}):
        cache.delete(address_key(previous["to_address"]))

    db.test_emails.update_many(query, {"$set": {"status": "expired"}})

    expires_at = now + timedelta(minutes=TEST_ADDRESS_TTL_MINUTES)

    doc = {
        "to_address": to_address,
        "status": "pending",
        "created_at": now,
        "expires_at": expires_at,
        "created_ip": created_ip,
        "owner_user_id": owner_user_id,
        "receiver_at": None,
        "mail_event_id": None,
        "analysis_id": None,
        "analysis_started_at": None,
        "last_error": None,
    }

    db.test_emails.insert_one(doc)

    cache.set(address_key(to_address), "1", ex=TEST_ADDRESS_TTL_MINUTES * 60)

    return {
        "result": to_address,
        "expires_at": expires_at,
        "expires_in": TEST_ADDRESS_TTL_MINUTES * 60,
        "limits": {
            "scope": quota["scope"],
            "limit": quota["limit"],
            "used": quota["used"],
            "remaining": quota["remaining"],
            "reset_at": quota["reset_at"],
        },
    }


@router.get("/result/{to_address}", tags=["result"])
def get_result(to_address: str, db=Depends(get_db)):
    email = db.test_emails.find_one({"to_address": to_address})

    if not email:
        return {"status": "not found"}

    expires_at = email.get("expires_at")
    if expires_at:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if expires_at < datetime.now(timezone.utc):
            return {"status": "expired"}

    status = email["status"]
    if status != "analyzed":
        return {"status": status, "last_error": email.get("last_error")}

    analysis = db.analyses.find_one({"_id": ObjectId(email["analysis_id"])})

    if not analysis:
        return {"status": "analysis not found"}

    analysis["_id"] = str(analysis["_id"])
    return {"status": "analyzed", "result": analysis}
