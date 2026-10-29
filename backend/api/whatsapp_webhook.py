"""Webhook de WhatsApp Cloud API: verificación del endpoint y recepción de mensajes."""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from backend.config import get_settings

router = APIRouter(prefix="/webhook/whatsapp", tags=["whatsapp"])


@router.get("", response_class=PlainTextResponse)
def verify(
    mode: str = Query(alias="hub.mode"),
    token: str = Query(alias="hub.verify_token"),
    challenge: str = Query(alias="hub.challenge"),
) -> str:
    """Meta llama a este endpoint al registrar el webhook y espera recibir `challenge`."""
    expected = get_settings().whatsapp_verify_token
    if mode != "subscribe" or not expected or token != expected:
        raise HTTPException(status_code=403, detail="Token de verificación inválido")
    return challenge


@router.post("")
async def receive(request: Request) -> dict:
    # TODO: convertir el payload de Meta en IncomingMessage y seguir el mismo flujo que el chat web.
    # Meta reintenta si no recibe 200 rápido: procesar en segundo plano y deduplicar por message id.
    await request.json()
    return {"status": "received"}
