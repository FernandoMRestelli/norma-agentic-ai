"""Contratos de la conversación entre el orquestador y los canales."""

from pydantic import BaseModel, Field


class ChannelReply(BaseModel):
    """Lo que el orquestador le pide al canal que le muestre al cliente.

    Con `draft_id`, el canal agrega Confirmar / Corregir (botones o la alternativa textual).
    """

    text: str
    draft_id: str | None = None
    draft_version: int | None = Field(default=None, ge=1)
