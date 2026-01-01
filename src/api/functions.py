import os
import sys
from datetime import datetime, timezone, timedelta

from fastapi import Request, Depends
from fastapi.security import OAuth2PasswordBearer
from passlib.handlers.sha2_crypt import sha256_crypt

from src.api.token import current_user
from src.db.db import get_db


def system_log(db, event: str, level: str = "INFO", user_id=None, session_id: str = None, request_info: dict = None, payload: dict = None, error: str = None):

    f = sys._getframe(1)

    caller = {
        "function": f.f_code.co_name,
        "file": os.path.basename(f.f_code.co_filename),
        "line": f.f_lineno,
    }

    log_doc = {
        "timestamp": datetime.now(timezone.utc),
        "level": level,
        "event": event,
        "user_id": str(user_id) if user_id else None,
        "session_id": session_id,
        "payload": payload or {},
        "error": error,
        "caller": caller,
    }

    if request_info:
        log_doc["ip"] = request_info.get("ip")
        log_doc["user_agent"] = request_info.get("user_agent")

    return db.system_logs.insert_one(log_doc)


def get_request_info(request: Request):
    client_ip = request.client.host if request else "unknown"
    user_agent = request.headers.get("User-Agent") if request else "unknown"
    return {"ip": client_ip, "user_agent": user_agent}


def verify_password(plain: str, hashed: str) -> bool:
    return sha256_crypt.verify(plain, hashed)


def hash_password(password: str) -> str:
    return sha256_crypt.hash(password)


oauth2_optional = OAuth2PasswordBearer(tokenUrl="/login", auto_error=False)

def optional_current_user(token: str = Depends(oauth2_optional), db=Depends(get_db)):
    if not token:
        return None
    return current_user(token=token, db=db)


def utc_tomorrow_start(current_time: datetime | None = None) -> datetime:

    if current_time is None:
        current_time = datetime.now(timezone.utc)

    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    tomorrow = current_time + timedelta(days=1)
    return tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)


def ensure_utc_aware(dt):
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None