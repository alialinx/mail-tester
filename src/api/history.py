from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException

from src.api.functions import get_request_info, optional_current_user
from src.api.rate_limit import enforce
from src.config import HISTORY_MAX_PAGE_SIZE, HISTORY_PAGE_SIZE, READ_RATE_LIMIT, READ_RATE_WINDOW
from src.db.db import get_db

router = APIRouter()

SUMMARY_FIELDS = {
    "score": 1,
    "title": 1,
    "meta.to_address": 1,
    "meta.sender_domain": 1,
    "meta.sender_ip": 1,
    "meta.subject": 1,
    "meta.received_at": 1,
    "checks.spamassassin.score": 1,
}


def require_user(current_user) -> str:
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return str(current_user["user_id"])


def summary_view(document: dict) -> dict:
    meta = document.get("meta") or {}
    spam = ((document.get("checks") or {}).get("spamassassin") or {})

    return {
        "id": str(document["_id"]),
        "score": document.get("score"),
        "grade": document.get("title"),
        "to_address": meta.get("to_address"),
        "sender_domain": meta.get("sender_domain"),
        "sender_ip": meta.get("sender_ip"),
        "subject": meta.get("subject"),
        "received_at": meta.get("received_at"),
        "spam_score": spam.get("score"),
    }


@router.get("/history", tags=["history"], summary="Past reports of the current user")
def list_history(limit: int = HISTORY_PAGE_SIZE, before: str = None, db=Depends(get_db),
                 req_info=Depends(get_request_info), current_user=Depends(optional_current_user)):
    user_id = require_user(current_user)
    enforce("history", user_id, READ_RATE_LIMIT, READ_RATE_WINDOW)

    page_size = max(1, min(int(limit or HISTORY_PAGE_SIZE), HISTORY_MAX_PAGE_SIZE))
    query = {"owner.user_id": user_id}

    if before:
        try:
            query["_id"] = {"$lt": ObjectId(before)}
        except (InvalidId, TypeError):
            raise HTTPException(status_code=400, detail="Invalid cursor")

    documents = list(db.analyses.find(query, SUMMARY_FIELDS).sort("_id", -1).limit(page_size + 1))
    has_more = len(documents) > page_size
    documents = documents[:page_size]

    return {
        "reports": [summary_view(d) for d in documents],
        "next_cursor": str(documents[-1]["_id"]) if has_more and documents else None,
    }


@router.get("/history/{report_id}", tags=["history"], summary="One past report in full")
def get_report(report_id: str, db=Depends(get_db), req_info=Depends(get_request_info),
               current_user=Depends(optional_current_user)):
    user_id = require_user(current_user)
    enforce("history", user_id, READ_RATE_LIMIT, READ_RATE_WINDOW)

    try:
        object_id = ObjectId(report_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail="Invalid report id")

    document = db.analyses.find_one({"_id": object_id, "owner.user_id": user_id})

    if not document:
        raise HTTPException(status_code=404, detail="Report not found")

    document["_id"] = str(document["_id"])

    return {"status": "analyzed", "result": document}
