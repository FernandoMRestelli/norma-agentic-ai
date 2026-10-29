"""Modelos de la base interna: borradores, órdenes e historial para trazabilidad."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# TODO: tablas previstas
# - DraftRecord: draft_id, customer_id, channel, items (JSON), status, created_at, updated_at
# - OrderRecord: order_id, draft_id (único -> evita órdenes duplicadas), items (JSON), status, created_at
# - OrderItemRecord: detalle por ítem para análisis por producto
