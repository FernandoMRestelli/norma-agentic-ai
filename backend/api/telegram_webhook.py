"""Webhook del bot de Telegram (canal de prueba de la beta), para cuando haya servidor público.

En desarrollo se usa el polling (`python -m backend.telegram_polling`), que no necesita URL pública.
"""

import secrets

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from backend.config import get_settings
from backend.services import telegram_service

router = APIRouter(prefix="/webhook/telegram", tags=["telegram"])


@router.post("")
async def receive(
    request: Request,
    background: BackgroundTasks,
    secret: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
) -> dict:
    """Telegram manda el secret que se fijó con setWebhook. Sin secret o sin token configurados, está apagado."""
    settings = get_settings()
    expected = settings.telegram_webhook_secret
    # Se compara en bytes: `compare_digest` con `str` lanza TypeError ante un carácter no ASCII y daría 500
    if (
        not expected
        or not settings.telegram_bot_token
        or not secret
        or not secrets.compare_digest(secret.encode(), expected.encode())
    ):
        raise HTTPException(status_code=403, detail="Secret token inválido")
    try:
        update = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="El cuerpo no es JSON") from None
    # Telegram reintenta si no recibe 200 rápido: se procesa después de responder
    background.add_task(telegram_service.handle_update, update, telegram_service.get_client())
    return {"status": "received"}
