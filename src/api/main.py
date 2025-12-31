# app/main.py
from datetime import datetime, timezone, timedelta

from bson import ObjectId
from fastapi import FastAPI, APIRouter, Depends
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.security import HTTPBasic
from starlette.middleware.cors import CORSMiddleware

from src.db.db import get_db
from src.processor.generator import generate_random_email
from src.worker.tasks import pull_and_analyze

app = FastAPI(
    title="Mail Tester",
    description="Mail Tester API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json",
    contact={"name": "Ali A.", "email": "alialinxz@gmail.com"},
)

security = HTTPBasic()


# Swagger ana sayfa
@app.get("/", include_in_schema=False)
async def homepage():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title="Mail Tester",
    )


# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Router
router = APIRouter()


@router.post("/generate", tags=["Generate"])
def generate_random(db=Depends(get_db)):
    to_address = generate_random_email()

    now = datetime.now(timezone.utc)

    doc = {
        "to_address": to_address,
        "status": "pending",
        "created_at": now,
        "expires_at": now + timedelta(hours=1),
        "receiver_at": None,
        "analysis_id": None,
        "last_error": None

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


app.include_router(router)
