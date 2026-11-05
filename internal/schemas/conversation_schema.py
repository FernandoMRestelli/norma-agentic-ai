"""Contratos de la conversación entre el orquestador y los canales."""

from pydantic import BaseModel


class ChannelReply(BaseModel):
    """Lo que el orquestador le pide al canal que le muestre al cliente."""

    text: str
