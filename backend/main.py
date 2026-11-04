"""Punto de entrada de la API. Ejecutar con: uvicorn backend.main:app --reload"""

from fastapi import FastAPI

from backend.api import chat, telegram_webhook, whatsapp_webhook
from backend.config import get_settings

app = FastAPI(title="Norma Agentic AI", version=get_settings().app_version)

app.include_router(chat.router)
app.include_router(whatsapp_webhook.router)
app.include_router(telegram_webhook.router)


@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok"}
