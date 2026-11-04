"""Contrato de datos del pedido: lo que comparten agente, servicios, API y frontend.

Distinción clave: un `OrderDraft` (borrador) NO es una `Order`. La orden solo
existe después de que el cliente confirma explícitamente el borrador.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_QUANTITY: int = 999


class Channel(StrEnum):
    WEB = "web"
    WHATSAPP = "whatsapp"


class MessageType(StrEnum):
    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"


class IncomingMessage(BaseModel):
    """Mensaje del cliente ya recibido por la API, antes del preprocesamiento."""

    model_config = ConfigDict(str_strip_whitespace=True)

    customer_id: str = Field(min_length=1)
    channel: Channel
    type: MessageType = MessageType.TEXT
    text: str | None = None
    media_url: str | None = None  # audio o imagen, según `type`

    @model_validator(mode="after")
    def check_content_matches_type(self) -> Self:
        if self.type == MessageType.TEXT and not (self.text and self.text.strip()):
            raise ValueError("Un mensaje de texto requiere `text`")
        if self.type in (MessageType.AUDIO, MessageType.IMAGE) and not self.media_url:
            raise ValueError(f"Un mensaje de tipo {self.type} requiere `media_url`")
        return self


class OrderItem(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    sku: str = Field(min_length=1)  # siempre un SKU existente en el catálogo, nunca inventado
    product_name: str = Field(min_length=1)
    presentation: str | None = None
    quantity: int = Field(gt=0, le=MAX_QUANTITY)
    unit_price: Decimal | None = Field(default=None, ge=0)


class DraftStatus(StrEnum):
    PENDING_CONFIRMATION = "pendiente_confirmacion"
    CONFIRMED = "confirmado"
    DISCARDED = "descartado"


class OrderDraft(BaseModel):
    """Lo que el agente entendió del pedido; se muestra al cliente para confirmar o corregir."""

    model_config = ConfigDict(str_strip_whitespace=True)

    draft_id: str
    customer_id: str = Field(min_length=1)
    channel: Channel
    items: list[OrderItem] = Field(min_length=1)
    notes: str | None = None
    status: DraftStatus = DraftStatus.PENDING_CONFIRMATION
    version: int = Field(default=1, ge=1)


class ConfirmationAction(StrEnum):
    CONFIRM = "confirmar"
    CORRECT = "corregir"


class OrderStatus(StrEnum):
    RECEIVED = "Recibido"
    PREPARED = "Preparado"
    PICKED_UP = "Retirado"
    CANCELLED = "Cancelado"


class Order(BaseModel):
    """Orden confirmada; es lo que se registra en el Google Sheet de seguimiento."""

    model_config = ConfigDict(str_strip_whitespace=True)

    order_id: str
    draft_id: str
    created_at: datetime
    customer_id: str = Field(min_length=1)
    channel: Channel
    items: list[OrderItem] = Field(min_length=1)
    notes: str | None = None
    status: OrderStatus = OrderStatus.RECEIVED
    sheets_synced: bool = False
