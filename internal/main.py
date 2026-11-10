"""Punto de entrada de la API. Ejecutar con: uvicorn internal.main:app --reload"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from internal.api import chat, dashboard, orders, telegram_webhook, whatsapp_webhook
from internal.config import get_settings
from internal.services import conversation_service

ROOT_DIR = Path(__file__).resolve().parent.parent
DASHBOARD_HTML_PATH = ROOT_DIR / "frontend" / "src" / "Dashboard" / "index.html"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    conversation_service.startup()
    yield


app = FastAPI(title="Norma Agentic AI", version=get_settings().app_version, lifespan=lifespan)

app.include_router(chat.router)
app.include_router(orders.router)
app.include_router(dashboard.router)
app.include_router(whatsapp_webhook.router)
app.include_router(telegram_webhook.router)


@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok"}


@app.get("/dashboard/ui", response_class=HTMLResponse, include_in_schema=False)
@app.get("/dashboard/app", response_class=HTMLResponse, include_in_schema=False)
def dashboard_ui() -> HTMLResponse:
    if DASHBOARD_HTML_PATH.is_file():
        return HTMLResponse(content=DASHBOARD_HTML_PATH.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Dashboard UI no encontrado</h1>", status_code=404)
