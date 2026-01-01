from datetime import datetime, timezone, timedelta

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from src.config import TOKEN_EXPIRE_MINUTES, SECRET_KEY, ALGORITHM
from src.db.db import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")


def create_access_token(data: dict):
    expire_at = datetime.now(timezone.utc) + timedelta(minutes=TOKEN_EXPIRE_MINUTES)

    payload = {
        "user_id": data.get("user_id"),
        "exp": int(expire_at.timestamp()),
    }

    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

    return token, expire_at


def token_save(token: str, user_id: str, expire_at: datetime, db=Depends(get_db)):
    db.tokens.delete_one({"user_id": user_id})
    now = datetime.now(timezone.utc)

    payload = {
        "user_id": user_id,
        "token": token,
        "expire_at": expire_at,
        "created_at": now,
    }

    db.tokens.insert_one(payload)

    return {"success": True}


def check_token(token: str, db=Depends(get_db)):
    doc = db.tokens.find_one({"token": token})

    if not doc:
        raise HTTPException(status_code=404, detail="Token not found")

    expire_at = doc.get("expire_at")

    if not expire_at:
        raise HTTPException(status_code=500, detail="Token configuration error")

    now = datetime.now(timezone.utc)
    if expire_at < now:
        raise HTTPException(status_code=401, detail="Token expired")

    return doc


def current_user(token: str = Depends(oauth2_scheme), db=Depends(get_db)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user_id not found")

    token_doc = check_token(token=token, db=db)

    if token_doc.get("user_id") != user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token user mismatch")

    return token_doc


def get_active_or_new_token(user: dict, db=Depends(get_db)):
    user_id = str(user["_id"])

    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user_id missing")

    now = datetime.now(timezone.utc)

    existing = db.tokens.find_one({"user_id": user_id}, {"token": 1, "expire_at": 1})

    if existing:
        expire_at = ensure_utc_aware(existing.get("expire_at"))
        token = existing.get("token")
        if token and expire_at and expire_at > now:
            return token, expire_at

    new_token, expire_at = create_access_token(data={"user_id": user_id})
    token_save(new_token, user_id, expire_at, db)
    return new_token, expire_at
