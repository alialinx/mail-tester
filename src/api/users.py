from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm

from src.api.functions import get_request_info, hash_password, system_log, verify_password
from src.api.schema import UserRegister
from src.api.token import get_active_or_new_token
from src.db.db import get_db

router = APIRouter()



@router.post("/register", summary="Register a new user")
def register(info:UserRegister, db=Depends(get_db), req_info=Depends(get_request_info)):

    exists = db.users.find_one({"email": info.email})

    if exists:
        raise HTTPException(status_code=400, detail="Email already registered")

    now = datetime.now(timezone.utc)

    payload = {
        "email": info.email,
        "password_hash": hash_password(info.password),
        "status": "active",
        "role":"user",
        "created_at":now,
        "updated_at":now,
        "last_login_at":None,
        "reqister_ip":req_info.get("ip")
    }

    result = db.users.insert_one(payload)
    user_id = result.inserted_id

    system_log(db,"register.success",user_id=user_id,request_info=req_info)

    return {"success": True, "user_id": str(user_id), "message": "User registered"}



@router.post("/login", summary="Login a user")
def login(form_data: OAuth2PasswordRequestForm = Depends(),db=Depends(get_db), req_info=Depends(get_request_info)):

    now = datetime.now(timezone.utc)

    username = form_data.username
    password = form_data.password

    user = db.users.find_one({"email": username})


    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    hashed_password = user.get("password_hash")

    if not verify_password(password, hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect password")

    token, expires_at = get_active_or_new_token(user,db)

    system_log(db,"login",user_id=user["_id"],request_info=req_info )

    db.users.update_one({"email": username}, {"$set": {"last_login_at": now}, "token_expires_at":expires_at.isoformat()})

    return {"success":True, "message":"login successfull", "access_token":token, "token_type":"bearer", "expires_at":expires_at.isoformat()}


@router.get("/logout", summary="Logout a user")
def logout():
    return {"success": True, "message": "Logout successful"}
