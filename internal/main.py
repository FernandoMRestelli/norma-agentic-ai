"""Punto de entrada de la API. Ejecutar con: uvicorn internal.main:app --reload"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from internal.api import chat, orders, telegram_webhook, whatsapp_webhook
from internal.config import get_settings
from internal.services import conversation_service, order_service

logger = logging.getLogger(__name__)

# Cada cuánto se reintentan las órdenes que no llegaron a la planilla (RF-53). Parámetro, no secreto (RNF-10)
SYNC_INTERVAL_SECONDS = 3 * 60


async def reintentar_pedidos_pendientes(intervalo: float) -> None:
    """Llama a `sync_pending_orders` cada `intervalo` segundos hasta que la cancelen (RF-53, RNF-04).

    Una pasada que falla queda en el log y no corta la tarea: la siguiente vuelve a probar.
    """
    while True:
        await asyncio.sleep(intervalo)
        try:
            synced = await asyncio.to_thread(order_service.sync_pending_orders)
        except Exception:
            logger.exception("Falló el reintento de pedidos pendientes; se vuelve a probar en %d s", intervalo)
            continue
        if synced:
            logger.info("Reintento de pedidos: %d órdenes llegaron a la planilla", synced)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    conversation_service.startup()
    app.state.reintento_pedidos = asyncio.create_task(reintentar_pedidos_pendientes(SYNC_INTERVAL_SECONDS))
    try:
        yield
    finally:
        app.state.reintento_pedidos.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await app.state.reintento_pedidos


app = FastAPI(title="Norma Agentic AI", version=get_settings().app_version, lifespan=lifespan)

app.include_router(chat.router)
app.include_router(orders.router)
app.include_router(whatsapp_webhook.router)
app.include_router(telegram_webhook.router)


@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok"}
