"""Entrada del chat web: recibe mensajes. El cliente confirma escribiendo, como en los demás canales."""

from fastapi import APIRouter, HTTPException

from internal.schemas.order_schema import IncomingMessage

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/messages")
def receive_message(message: IncomingMessage) -> dict:
    # TODO: message_service.prepare -> NormaAgent.handle -> devolver la respuesta
    raise HTTPException(status_code=501, detail="Agente todavía no implementado")
