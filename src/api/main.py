# app/main.py

from fastapi import FastAPI, APIRouter, Depends
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.security import HTTPBasic
from starlette.middleware.cors import CORSMiddleware

from src.db.db import get_db
from src.imap.imap import get_email_from_imap
from src.processor.analyzer import Analyzer
from src.processor.generator import generate_random_email
from src.processor.service import get_mx_record, check_a_record, get_sender_ip

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

@router.get("/test", tags=["test"])
def get_aaa(db=Depends(get_db)):
    result = list(db.test_emails.find())

    for res in result:
        res["_id"] = str(res["_id"])

    return {"result": result}


@router.post("/generate", tags=["Generate"])
def generate_random():

    result = generate_random_email()
    return {"result": result}


@router.post("/analyze/{to_address}", summary="Analyze", tags=["Analyze"])
def analyze(to_address: str, db=Depends(get_db)):

    msg = get_email_from_imap(to_address)

    from_header = msg.get("From")
    domain = from_header.split("@")[-1].replace(">", "").strip()

    sender_ip = get_sender_ip(msg)

    analyzer = Analyzer(email_message=msg,domain=domain,sender_ip=sender_ip)
    result = analyzer.analyze()


    db.analyses.insert_one(result)


    return {"result": result}



app.include_router(router)
