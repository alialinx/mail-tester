from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm

from src.api.functions import get_request_info, hash_password, system_log, verify_password, utc_tomorrow_start, is_valid_email
from src.api.rate_limit import enforce
from src.api.schema import UserRegister
from src.api.token import current_user, get_active_or_new_token
from src.config import AUTH_RATE_LIMIT, AUTH_RATE_WINDOW, PASSWORD_MIN_LENGTH, USER_DAILY_LIMIT
from src.db.db import get_db
from src.worker.limits import get_quota_state

router = APIRouter()


@router.post("/register", summary="Register a new user")
def register(info: UserRegister, db=Depends(get_db), req_info=Depends(get_request_info)):
    enforce("register", req_info.get("ip"), AUTH_RATE_LIMIT, AUTH_RATE_WINDOW)

    email = (info.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")

    if not is_valid_email(email):
        raise HTTPException(status_code=400, detail="Invalid email format")

    if len(info.password or "") < PASSWORD_MIN_LENGTH:
        raise HTTPException(status_code=400, detail=f"Password must be at least {PASSWORD_MIN_LENGTH} characters")

    exists = db.users.find_one({"email": email})
    if exists:
        raise HTTPException(status_code=400, detail="Email already registered")

    now = datetime.now(timezone.utc)

    user_document = {
        "email": email,
        "password_hash": hash_password(info.password),
        "status": "active",
        "role": "user",
        "created_at": now,
        "updated_at": now,
        "last_login_at": None,
        "register_ip": req_info.get("ip"),
        "quota": {
            "analyze": {
                "daily_limit": USER_DAILY_LIMIT,
                "daily_used": 0,
                "reset_at": utc_tomorrow_start(now),
                "updated_at": now,
            }
        },
    }

    result = db.users.insert_one(user_document)

    system_log(db, "register.success", user_id=result.inserted_id, request_info=req_info)

    return {
        "success": True,
        "user_id": str(result.inserted_id),
        "daily_limit": USER_DAILY_LIMIT,
        "message": "User registered",
    }


@router.post("/login", summary="Login a user")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db=Depends(get_db), req_info=Depends(get_request_info)):
    enforce("login", req_info.get("ip"), AUTH_RATE_LIMIT, AUTH_RATE_WINDOW)

    now = datetime.now(timezone.utc)
    email = (form_data.username or "").strip().lower()

    user = db.users.find_one({"email": email})

    if not user or not verify_password(form_data.password, user.get("password_hash") or ""):
        system_log(db, "login.failed", level="WARNING", request_info=req_info, payload={"email": email})
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if user.get("status") != "active":
        raise HTTPException(status_code=403, detail="Account is not active")

    token, expires_at = get_active_or_new_token(user, db)

    system_log(db, "login", user_id=user["_id"], request_info=req_info)

    db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {
            "last_login_at": now,
            "token_expires_at": expires_at,
        }}
    )

    return {
        "success": True,
        "message": "login successfull",
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at,
        "email": user["email"],
    }


@router.get("/me", summary="Current user and quota")
def me(db=Depends(get_db), token_doc=Depends(current_user)):
    user = db.users.find_one({"_id": ObjectId(token_doc["user_id"])}, {"email": 1, "created_at": 1})

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    quota = get_quota_state(db, token_doc["user_id"])

    return {
        "email": user["email"],
        "created_at": user.get("created_at"),
        "limits": {
            "scope": quota["scope"],
            "limit": quota["limit"],
            "used": quota["used"],
            "remaining": quota["remaining"],
            "reset_at": quota["reset_at"],
        },
    }


@router.post("/logout", summary="Logout a user")
def logout(db=Depends(get_db), token_doc=Depends(current_user), req_info=Depends(get_request_info)):
    db.tokens.delete_one({"token": token_doc["token"]})

    system_log(db, "logout", user_id=token_doc["user_id"], request_info=req_info)

    return {"success": True, "message": "Logout successful"}
