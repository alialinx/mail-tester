import hashlib
import secrets
from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Request

from src.api.functions import get_request_info, system_log, utc_tomorrow_start
from src.api.rate_limit import enforce
from src.api.schema import ApiKeyCreate
from src.api.token import current_user
from src.config import (
    API_DAILY_LIMIT,
    API_KEYS_PER_USER,
    API_KEY_PREFIX,
    API_RATE_LIMIT,
    API_RATE_WINDOW,
    AUTH_RATE_LIMIT,
    AUTH_RATE_WINDOW,
)
from src.db.db import get_db

router = APIRouter()

KEY_BYTES = 24
PREVIEW_LENGTH = 8


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def new_key() -> str:
    return f"{API_KEY_PREFIX}_{secrets.token_hex(KEY_BYTES)}"


def key_preview(raw_key: str) -> str:
    return raw_key[:len(API_KEY_PREFIX) + 1 + PREVIEW_LENGTH]


def public_key_view(document: dict) -> dict:
    quota = document.get("quota") or {}

    return {
        "id": str(document["_id"]),
        "name": document.get("name"),
        "preview": document.get("preview"),
        "created_at": document.get("created_at"),
        "last_used_at": document.get("last_used_at"),
        "daily_limit": quota.get("daily_limit", API_DAILY_LIMIT),
        "daily_used": quota.get("daily_used", 0),
        "reset_at": quota.get("reset_at"),
    }


def resolve_api_key(request: Request, db) -> dict:
    raw_key = (request.headers.get("x-api-key") or "").strip()

    if not raw_key:
        return None

    document = db.api_keys.find_one({"key_hash": hash_key(raw_key), "revoked_at": None})

    if not document:
        raise HTTPException(status_code=401, detail="Invalid API key")

    enforce("api_key", str(document["_id"]), API_RATE_LIMIT, API_RATE_WINDOW)

    db.api_keys.update_one({"_id": document["_id"]}, {"$set": {"last_used_at": datetime.now(timezone.utc)}})

    return document


@router.get("/keys", tags=["keys"], summary="List the API keys of the current user")
def list_keys(db=Depends(get_db), token_doc=Depends(current_user)):
    user_id = token_doc["user_id"]
    keys = db.api_keys.find({"user_id": user_id, "revoked_at": None}).sort("_id", -1)

    return {"keys": [public_key_view(k) for k in keys], "max_keys": API_KEYS_PER_USER}


@router.post("/keys", tags=["keys"], summary="Create a new API key")
def create_key(info: ApiKeyCreate, db=Depends(get_db), token_doc=Depends(current_user),
               req_info=Depends(get_request_info)):
    enforce("keys_create", req_info.get("ip"), AUTH_RATE_LIMIT, AUTH_RATE_WINDOW)

    user_id = token_doc["user_id"]
    active = db.api_keys.count_documents({"user_id": user_id, "revoked_at": None})

    if active >= API_KEYS_PER_USER:
        raise HTTPException(status_code=400, detail=f"You can have at most {API_KEYS_PER_USER} active keys")

    raw_key = new_key()
    now = datetime.now(timezone.utc)

    document = {
        "user_id": user_id,
        "name": (info.name or "").strip()[:60] or "default",
        "key_hash": hash_key(raw_key),
        "preview": key_preview(raw_key),
        "created_at": now,
        "last_used_at": None,
        "revoked_at": None,
        "created_ip": req_info.get("ip"),
        "quota": {
            "daily_limit": API_DAILY_LIMIT,
            "daily_used": 0,
            "reset_at": utc_tomorrow_start(now),
            "updated_at": now,
        },
    }

    result = db.api_keys.insert_one(document)
    document["_id"] = result.inserted_id

    system_log(db, "api_key.created", user_id=user_id, request_info=req_info)

    view = public_key_view(document)
    view["key"] = raw_key

    return view


@router.delete("/keys/{key_id}", tags=["keys"], summary="Revoke an API key")
def revoke_key(key_id: str, db=Depends(get_db), token_doc=Depends(current_user),
               req_info=Depends(get_request_info)):
    try:
        object_id = ObjectId(key_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail="Invalid key id")

    result = db.api_keys.update_one(
        {"_id": object_id, "user_id": token_doc["user_id"], "revoked_at": None},
        {"$set": {"revoked_at": datetime.now(timezone.utc)}}
    )

    if not result.matched_count:
        raise HTTPException(status_code=404, detail="Key not found")

    system_log(db, "api_key.revoked", user_id=token_doc["user_id"], request_info=req_info)

    return {"revoked": True}
