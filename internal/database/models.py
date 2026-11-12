"""Modelos de la base interna: conversaciones, órdenes con sus líneas e historial, propuestas y mensajes procesados.

El esquema de una base existente lo actualiza `migrations.migrate` al arrancar: un cambio acá que toque una tabla
existente necesita su paso de migración (docs/spec/guardado-de-pedidos.md §3.3).
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def _now() -> datetime:
    return datetime.now(UTC)


class DecimalText(TypeDecorator):
    """Importe exacto guardado como texto (`"1234.50"`): SQLite no tiene decimal y un REAL redondea."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> str | None:
        return None if value is None else str(Decimal(value))

    def process_result_value(self, value: str | None, dialect: Dialect) -> Decimal | None:
        return None if value is None else Decimal(value)


class Base(DeclarativeBase):
    pass


class ConversationRecord(Base):
    """Intento de armar un pedido. Su id es el `thread_id` del grafo del agente."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[str] = mapped_column(String, index=True)
    channel: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="abierta")  # abierta, cerrada o fallida
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class OrderRecord(Base):
    """Orden registrada. `draft_id` único: un reintento no puede crear otra orden del mismo borrador."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str | None] = mapped_column(String, unique=True)  # sale del id, se completa al insertar
    draft_id: Mapped[str] = mapped_column(String, unique=True)
    customer_id: Mapped[str] = mapped_column(String, index=True)
    channel: Mapped[str] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String)
    sheets_synced: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Agregadas en la migración 1: en una base de la 0.1.1 son columnas nuevas, por eso aceptan nulos
    customer_name: Mapped[str | None] = mapped_column(String(64))  # nombre del perfil: dato personal (RNF-03)
    # Sin clave foránea: la conversación puede borrarse por retención (D-08) y la orden tiene que quedar
    conversation_id: Mapped[int | None] = mapped_column(Integer, index=True)
    total: Mapped[Decimal | None] = mapped_column(DecimalText)

    lines: Mapped[list["OrderItemRecord"]] = relationship(
        back_populates="order",
        order_by="OrderItemRecord.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )
    events: Mapped[list["OrderEventRecord"]] = relationship(
        back_populates="order",
        order_by="OrderEventRecord.id",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class OrderItemRecord(Base):
    """Una línea de una orden (RF-21, RF-45)."""

    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_pk: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    sku: Mapped[str] = mapped_column(String, index=True)
    product_name: Mapped[str] = mapped_column(String)
    presentation: Mapped[str | None] = mapped_column(String)
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal | None] = mapped_column(DecimalText)
    note: Mapped[str | None] = mapped_column(Text)

    order: Mapped[OrderRecord] = relationship(back_populates="lines")


class OrderEventRecord(Base):
    """Un cambio de estado de una orden (RF-47). `from_status` es nulo en el alta."""

    __tablename__ = "order_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_pk: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str | None] = mapped_column(String)
    to_status: Mapped[str] = mapped_column(String)
    source: Mapped[str] = mapped_column(String)  # agente, operador o migracion
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    order: Mapped[OrderRecord] = relationship(back_populates="events")


class DraftRecord(Base):
    """Una versión de una propuesta y su desenlace (RF-46). Sin el nombre del cliente ni texto de mensajes."""

    __tablename__ = "drafts"
    __table_args__ = (UniqueConstraint("draft_id", "version", name="uq_drafts_draft_id_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_id: Mapped[str] = mapped_column(String, index=True)
    version: Mapped[int] = mapped_column(Integer)
    conversation_id: Mapped[int | None] = mapped_column(Integer, index=True)
    customer_id: Mapped[str] = mapped_column(String)
    channel: Mapped[str] = mapped_column(String)
    items: Mapped[list[dict]] = mapped_column(JSON)  # las líneas propuestas, con sus notas
    notes: Mapped[str | None] = mapped_column(Text)
    total: Mapped[Decimal | None] = mapped_column(DecimalText)
    status: Mapped[str] = mapped_column(String)  # pendiente_confirmacion, reemplazado, descartado o confirmado
    order_id: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class ProcessedMessageRecord(Base):
    """Mensaje de un canal ya procesado, para no procesarlo dos veces (RF-31). Solo ids, nunca el texto."""

    __tablename__ = "processed_messages"
    __table_args__ = (UniqueConstraint("channel", "external_id", name="uq_processed_messages_channel_external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String)
    external_id: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
