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
from typing import Literal, Self
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

MAX_QUANTITY: int = 999
MAX_MEDIA_URL_LENGTH: int = 2048
MAX_LINE_NOTE_LENGTH: int = 120  # nota de una línea: tono, variante o aclaración (RF-48)
MAX_ORDER_NOTES_LENGTH: int = 300  # observación general del pedido (RF-48)
MAX_CUSTOMER_NAME_LENGTH: int = 64
MAX_SOURCE_TEXT_LENGTH: int = 300  # el renglón como lo escribió el cliente (D-24)

# De dónde salió el pedido (RF-79). Hoy siempre `texto`: audio e imagen todavía no llegan al agente
ORIGEN_POR_TIPO = {"text": "texto", "audio": "audio", "image": "imagen"}
Coincidencia = Literal["exacta", "aproximada"]
_NUMERIC_OR_HEX_HOST = re.compile(
    r"^(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+))*$", re.IGNORECASE
)
_TELEGRAM_FILE_REFERENCE = re.compile(r"telegram:[A-Za-z0-9_-]+")


def clean_note(value: str | None) -> str | None:
    """Colapsa los espacios de una nota; una nota vacía es None."""
    if value is None:
        return None
    return " ".join(value.split()) or None


def items_total(items: "list[OrderItem]") -> Decimal:
    """Suma de cantidad por precio de las líneas con precio: es el total que ve el cliente."""
    return sum((item.quantity * item.unit_price for item in items if item.unit_price is not None), Decimal(0))


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
    customer_name: str | None = Field(default=None, max_length=MAX_CUSTOMER_NAME_LENGTH)  # nombre del perfil
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
    note: str | None = Field(default=None, max_length=MAX_LINE_NOTE_LENGTH)  # el tono mientras siga abierta V-01
    # Para quien prepara el pedido (RF-75). Los cuatro son opcionales: una orden anterior a la migración 2 no los trae
    brand: str | None = None
    category: str | None = None
    match: Coincidencia | None = None  # `aproximada`: el cliente tuvo que confirmar «¿es este?»
    source_text: str | None = None  # el renglón como lo escribió el cliente; solo en pedidos numerados (D-24)

    @field_validator("note")
    @classmethod
    def clean_line_note(cls, v: str | None) -> str | None:
        return clean_note(v)

    @field_validator("source_text")
    @classmethod
    def clean_source_text(cls, v: str | None) -> str | None:
        return (clean_note(v) or "")[:MAX_SOURCE_TEXT_LENGTH] or None


class Leftover(BaseModel):
    """Un renglón que el cliente pidió y no entró al pedido (RF-76): es una venta que el equipo puede recuperar."""

    source_text: str = Field(min_length=1, max_length=MAX_SOURCE_TEXT_LENGTH)
    reason: str  # fuera_de_catalogo, sin_stock, lo_saco_el_cliente o duda_sin_resolver


class DraftStatus(StrEnum):
    PENDING_CONFIRMATION = "pendiente_confirmacion"
    CONFIRMED = "confirmado"
    DISCARDED = "descartado"
    REPLACED = "reemplazado"  # llegó otra versión del mismo borrador


class OrderDraft(BaseModel):
    """Lo que el agente entendió del pedido; se muestra al cliente para confirmar o corregir."""

    model_config = ConfigDict(str_strip_whitespace=True)

    draft_id: str
    customer_id: str = Field(min_length=1)
    customer_name: str | None = None  # lo pone el backend desde el perfil, nunca el LLM
    channel: Channel
    conversation_id: int | None = None  # lo pone el backend
    items: list[OrderItem] = Field(min_length=1)
    notes: str | None = Field(default=None, max_length=MAX_ORDER_NOTES_LENGTH)
    status: DraftStatus = DraftStatus.PENDING_CONFIRMATION
    version: int = Field(default=1, ge=1)
    leftovers: list[Leftover] = Field(default_factory=list)  # lo que quedó afuera al proponer (RF-76)
    source: str = "texto"  # texto, audio o imagen (RF-79)

    @field_validator("notes")
    @classmethod
    def clean_notes(cls, v: str | None) -> str | None:
        return clean_note(v)

    @field_validator("customer_name")
    @classmethod
    def clean_customer_name(cls, v: str | None) -> str | None:
        return (clean_note(v) or "")[:MAX_CUSTOMER_NAME_LENGTH] or None

    @property
    def total(self) -> Decimal:
        return items_total(self.items)


class ConfirmationAction(StrEnum):
    CONFIRM = "confirmar"
    CORRECT = "corregir"


class OrderStatus(StrEnum):
    RECEIVED = "Recibido"
    PREPARED = "Preparado"
    PICKED_UP = "Retirado"
    CANCELLED = "Cancelado"


class OrderEvent(BaseModel):
    """Un cambio de estado de una orden. `from_status` es None en el alta (RF-47)."""

    from_status: OrderStatus | None = None
    to_status: OrderStatus
    source: str  # agente, operador o migracion
    created_at: datetime


class Order(BaseModel):
    """Orden confirmada; es lo que se registra en el Google Sheet de seguimiento."""

    model_config = ConfigDict(str_strip_whitespace=True)

    order_id: str
    draft_id: str
    created_at: datetime
    customer_id: str = Field(min_length=1)
    customer_name: str | None = None  # nombre del perfil: dato personal, nunca a logs ni trazas (RNF-03)
    channel: Channel
    conversation_id: int | None = None
    items: list[OrderItem] = Field(min_length=1)
    notes: str | None = None
    total: Decimal | None = None  # siempre Decimal después de validar: si no viene, sale de las líneas
    status: OrderStatus = OrderStatus.RECEIVED
    sheets_synced: bool = False
    history: list[OrderEvent] = Field(default_factory=list)
    leftovers: list[Leftover] = Field(default_factory=list)
    source: str | None = None  # None en una orden anterior a la migración 2

    @property
    def updated_at(self) -> datetime:
        """Cuándo cambió de estado por última vez; sin historial, cuándo se creó."""
        return self.history[-1].created_at if self.history else self.created_at

    @model_validator(mode="after")
    def fill_total(self) -> Self:
        if self.total is None:
            self.total = items_total(self.items)
        return self

    @field_validator("order_id")
    @classmethod
    def validate_order_id_format(cls, v: str) -> str:
        from internal.services.order_service import parse_order_id

        parse_order_id(v)
        return v
