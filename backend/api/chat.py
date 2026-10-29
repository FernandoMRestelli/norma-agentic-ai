"""Entrada del chat web: recibe mensajes y la acción Confirmar / Corregir de la tarjeta."""

from fastapi import APIRouter, HTTPException

from backend.schemas.order_schema import ConfirmationAction, IncomingMessage

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/messages")
def receive_message(message: IncomingMessage) -> dict:
    # TODO: message_service.prepare -> NormaAgent.handle -> devolver borrador o pregunta
    raise HTTPException(status_code=501, detail="Agente todavía no implementado")


@router.post("/drafts/{draft_id}/{action}")
def resolve_draft(draft_id: str, action: ConfirmationAction) -> dict:
    # TODO: confirmar -> order_service.confirm_draft; corregir -> volver al agente
    raise HTTPException(status_code=501, detail="Confirmación todavía no implementada")
