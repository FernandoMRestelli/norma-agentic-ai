"""Webhook de WhatsApp Cloud API: verificación del endpoint y recepción de mensajes."""

import hashlib
import hmac
import secrets

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from backend.config import get_settings

router = APIRouter(prefix="/webhook/whatsapp", tags=["whatsapp"])


def _compare_digest(left: str, right: str) -> bool:
    """Compara en tiempo constante textos que pueden no ser ASCII.

    `secrets.compare_digest` con `str` solo acepta ASCII y lanza `TypeError` con otro carácter,
    así que un token o una firma malformados terminaban en 500 en vez del 403 que pide la spec.
    """
    return secrets.compare_digest(left.encode(), right.encode())


@router.get("", response_class=PlainTextResponse)
def verify(
    mode: str = Query(alias="hub.mode"),
    token: str = Query(alias="hub.verify_token"),
    challenge: str = Query(alias="hub.challenge"),
) -> str:
    """Meta llama a este endpoint al registrar el webhook y espera recibir `challenge`."""
    expected = get_settings().whatsapp_verify_token
    if mode != "subscribe" or not expected or not _compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Token de verificación inválido")
    return challenge


def is_valid_signature(body: bytes, signature: str | None, app_secret: str | None) -> bool:
    """Valida `X-Hub-Signature-256` (HMAC-SHA256 del cuerpo con el app secret de Meta)."""
    if not signature or not app_secret:
        return False
    expected = "sha256=" + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return _compare_digest(signature, expected)


@router.post("")
async def receive(
    request: Request,
    signature: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> dict:
    body = await request.body()
    if not is_valid_signature(body, signature, get_settings().whatsapp_app_secret):
        raise HTTPException(status_code=403, detail="Firma inválida")
    # TODO: convertir el payload de Meta en IncomingMessage y seguir el mismo flujo que el chat web.
    # Meta reintenta si no recibe 200 rápido: procesar en segundo plano y deduplicar por message id.
    return {"status": "received"}
