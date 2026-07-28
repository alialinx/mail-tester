# app/main.py
import os

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

from src.api import auth, events, mail_tests

# Arayüz bu repoda gelmiyor. Kendi arayüzünü /app/public içine mount edebilirsin,
# mount edilmezse API tek başına çalışmaya devam ediyor.
WEB_ROOT = os.getenv("WEB_ROOT", "public")

app = FastAPI(
    title="Mail Tester",
    description="Mail Tester API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json",
    contact={"name": "Ali A.", "email": "alialinxz@gmail.com"},
)

# CORS
# allow_credentials ile "*" birlikte kullanılamıyor, tarayıcı reddediyor.
# Arayüz aynı origin'den servis edildiği için credentials'a ihtiyacımız yok.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Swagger artık /docs altında, ana sayfayı arayüze bıraktık
@app.get("/docs", include_in_schema=False)
async def swagger():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title="Mail Tester",
    )


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok", "web": os.path.isfile(os.path.join(WEB_ROOT, "index.html"))}


# Router
app.include_router(mail_tests.router)
app.include_router(events.router)
app.include_router(auth.router)

# Arayüz mount edilmişse kök adresten servis ediyoruz. Mount en sonda olmalı,
# yoksa router'ları gölgeliyor.
if os.path.isfile(os.path.join(WEB_ROOT, "index.html")):
    app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="web")
else:
    @app.get("/", include_in_schema=False)
    def index():
        return {"service": "mail-tester", "docs": "/docs"}
