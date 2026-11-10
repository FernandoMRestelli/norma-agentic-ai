"""Punto de entrada de la API. Ejecutar con: uvicorn internal.main:app --reload"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from internal.api import chat, orders, telegram_webhook, whatsapp_webhook
from internal.config import get_settings
from internal.services import conversation_service


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    conversation_service.startup()
    yield


app = FastAPI(title="Norma Agentic AI", version=get_settings().app_version, lifespan=lifespan)

app.include_router(chat.router)
app.include_router(orders.router)
app.include_router(whatsapp_webhook.router)
app.include_router(telegram_webhook.router)


@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok"}
