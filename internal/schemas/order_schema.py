"""Contrato de datos del pedido: lo que comparten agente, servicios, API y frontend.

Distinción clave: un `OrderDraft` (borrador) NO es una `Order`. La orden solo
existe después de que el cliente confirma explícitamente el borrador.
"""

import ipaddress
import re
import socket
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Self
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

MAX_QUANTITY: int = 999
MAX_MEDIA_URL_LENGTH: int = 2048
_NUMERIC_OR_HEX_HOST = re.compile(
    r"^(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+))*$", re.IGNORECASE
)
_TELEGRAM_FILE_REFERENCE = re.compile(r"telegram:[A-Za-z0-9_-]+")


class Channel(StrEnum):
    WEB = "web"
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"  # canal de prueba de la beta


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
    external_id: str | None = None  # id del mensaje en el canal, para deduplicar

    @field_validator("media_url")
    @classmethod
    def validate_media_url(cls, v: str | None, info: ValidationInfo) -> str | None:
        if v is None:
            return None
        if len(v) > MAX_MEDIA_URL_LENGTH:
            raise ValueError(
                f"media_url excede el largo máximo permitido ({MAX_MEDIA_URL_LENGTH} caracteres)"
            )
        # Un file_id no es una dirección: se resuelve con la Bot API, nunca conectándose a lo que trae
        if info.data.get("channel") == Channel.TELEGRAM and _TELEGRAM_FILE_REFERENCE.fullmatch(v):
            return v
        parsed = urlparse(v)
        if parsed.scheme != "https":
            raise ValueError(
                f"media_url debe usar el esquema 'https', se recibió {parsed.scheme!r}"
            )
        hostname = parsed.hostname
        if not hostname:
            raise ValueError("media_url debe incluir un host válido")

        host = hostname.lower().rstrip(".")
        if not host:
            raise ValueError("media_url debe incluir un host válido")

        if host == "localhost" or host.endswith(".localhost"):
            raise ValueError(
                "media_url no permite direcciones locales o privadas ('localhost')"
            )

        if host.endswith(".local") or host.endswith(".internal"):
            raise ValueError(
                f"media_url no permite direcciones de red interna o metadata ({hostname})"
            )

        try:
            ip = ipaddress.ip_address(host)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_unspecified
            ):
                raise ValueError(
                    f"media_url no permite direcciones IP privadas o internas ({hostname})"
                )
            return v
        except ValueError as err:
            if "media_url no permite" in str(err):
                raise
            # No es una IP literal estándar; probar representaciones alternativas de IPv4

        try:
            raw_bytes = socket.inet_aton(host)
            ip_alt = ipaddress.IPv4Address(raw_bytes)
            if (
                ip_alt.is_private
                or ip_alt.is_loopback
                or ip_alt.is_link_local
                or ip_alt.is_reserved
                or ip_alt.is_unspecified
            ):
                raise ValueError(
                    f"media_url no permite direcciones IP privadas o internas ({hostname})"
                )
            if str(ip_alt) != host:
                raise ValueError(
                    f"media_url no permite direcciones IP en formato no canónico ({hostname})"
                )
        except OSError:
            if _NUMERIC_OR_HEX_HOST.fullmatch(host):
                raise ValueError(
                    f"media_url no permite direcciones de host no válidas ({hostname})"
                ) from None

        return v

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

    @field_validator("order_id")
    @classmethod
    def validate_order_id_format(cls, v: str) -> str:
        from internal.services.order_service import parse_order_id

        parse_order_id(v)
        return v
