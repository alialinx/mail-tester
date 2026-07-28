import os

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware

from src.api import auth, events, mail_tests

WEB_ROOT = os.getenv("WEB_ROOT", "public")


class WebFiles(StaticFiles):

    async def get_response(self, path: str, scope):
        for part in path.split("/"):
            if part.startswith(".") and part != ".":
                raise HTTPException(status_code=404)
        return await super().get_response(path, scope)


app = FastAPI(
    title="Mail Tester",
    description="Mail Tester API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json",
    contact={"name": "Ali A.", "email": "alialinxz@gmail.com"},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/docs", include_in_schema=False)
async def swagger():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title="Mail Tester",
    )


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok", "web": os.path.isfile(os.path.join(WEB_ROOT, "index.html"))}


app.include_router(mail_tests.router)
app.include_router(events.router)
app.include_router(auth.router)

if os.path.isfile(os.path.join(WEB_ROOT, "index.html")):
    app.mount("/", WebFiles(directory=WEB_ROOT, html=True), name="web")
else:
    @app.get("/", include_in_schema=False)
    def index():
        return {"service": "mail-tester", "docs": "/docs"}
