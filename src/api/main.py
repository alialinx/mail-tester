# app/main.py
from fastapi import FastAPI, APIRouter
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.security import HTTPBasic
from starlette.middleware.cors import CORSMiddleware

from src.api import auth, mail_tests

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

app = FastAPI(title="Mail Tester API")

app.include_router(mail_tests.router)
app.include_router(auth.router)
