from datetime import datetime, timezone, timedelta

from bson import ObjectId
from fastapi import APIRouter, Depends
from fastapi import Request

from src.api.functions import get_request_info, optional_current_user
from src.db.db import get_db
from src.processor.generator import generate_random_email
from src.worker.tasks import pull_and_analyze

router = APIRouter()


@router.get("/debug/ip")
def debug_ip(request: Request):
    return {
        "client": request.client.host if request.client else None,
        "x_real_ip": request.headers.get("x-real-ip"),
        "x_forwarded_for": request.headers.get("x-forwarded-for"),
    }


@router.post("/generate", tags=["Generate"])
def generate_random(db=Depends(get_db), req_info=Depends(get_request_info), current_user=Depends(optional_current_user), ):
    to_address = generate_random_email()

    now = datetime.now(timezone.utc)

    created_ip = req_info.get("ip")
    owner_user_id = (str(current_user["user_id"]) if current_user else None)

    query = {"status": "pending", "expires_at": {"$gt": now}, }

    if owner_user_id:
        query["owner_user_id"] = owner_user_id
    else:
        query["created_ip"] = created_ip

    db.test_emails.update_many(query, {"$set": {"status": "expired"}})

    doc = {
        "to_address": to_address,
        "status": "pending",
        "created_at": now,
        "expires_at": now + timedelta(minutes=5),
        "created_ip": created_ip,
        "owner_user_id": owner_user_id,
        "receiver_at": None,
        "analysis_id": None,
        "analysis_started_at": None,
        "last_error": None,
    }

    db.test_emails.insert_one(doc)
    pull_and_analyze.delay(to_address)

    return {"result": to_address}


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
        return {"status": status, }

    analysis = db.analyses.find_one({"_id": ObjectId(email["analysis_id"])})

    if not analysis:
        return {"status": "analysis not found"}

    analysis["_id"] = str(analysis["_id"])
    return {"status": "analyzed", "result": analysis}
