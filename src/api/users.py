from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from src.api.functions import get_request_info, hash_password, system_log
from src.api.schema import UserRegister
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

