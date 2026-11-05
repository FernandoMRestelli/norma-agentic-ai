"""Modelos de la base interna: conversaciones y órdenes."""

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(UTC)


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
    items: Mapped[list[dict]] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String)
    sheets_synced: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
