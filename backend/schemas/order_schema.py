"""Contrato de datos del pedido: lo que comparten agente, servicios, API y frontend.

Distinción clave: un `OrderDraft` (borrador) NO es una `Order`. La orden solo
existe después de que el cliente confirma explícitamente el borrador.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Channel(StrEnum):
    WEB = "web"
    WHATSAPP = "whatsapp"


class MessageType(StrEnum):
    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"


class IncomingMessage(BaseModel):
    """Mensaje del cliente ya recibido por la API, antes del preprocesamiento."""

    customer_id: str
    channel: Channel
    type: MessageType = MessageType.TEXT
    text: str | None = None
    media_url: str | None = None  # audio o imagen, según `type`


class OrderItem(BaseModel):
    sku: str  # siempre un SKU existente en el catálogo, nunca inventado
    product_name: str
    presentation: str | None = None
    quantity: int = Field(gt=0)
    unit_price: float | None = Field(default=None, ge=0)


class DraftStatus(StrEnum):
    PENDING_CONFIRMATION = "pendiente_confirmacion"
    CONFIRMED = "confirmado"
    DISCARDED = "descartado"


class OrderDraft(BaseModel):
    """Lo que el agente entendió del pedido; se muestra al cliente para confirmar o corregir."""

    draft_id: str
    customer_id: str
    channel: Channel
    items: list[OrderItem] = Field(min_length=1)
    notes: str | None = None
    status: DraftStatus = DraftStatus.PENDING_CONFIRMATION


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

    order_id: str
    draft_id: str
    created_at: datetime
    customer_id: str
    channel: Channel
    items: list[OrderItem] = Field(min_length=1)
    notes: str | None = None
    status: OrderStatus = OrderStatus.RECEIVED
