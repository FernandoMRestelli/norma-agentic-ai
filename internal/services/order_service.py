"""Lógica de negocio de la confirmación: el único lugar donde nace una `Order`."""

from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from internal.database.connection import SessionLocal
from internal.database.models import OrderRecord
from internal.schemas.order_schema import Order, OrderDraft, OrderStatus
from internal.services import inventory_service, sheets_service

logger = logging.getLogger(__name__)

ORDER_ID_PREFIX = "ORD-"
ORDER_ID_REGEX = re.compile(r"^ORD-(\d{4,})$")

VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.RECEIVED: {OrderStatus.PREPARED, OrderStatus.CANCELLED},
    OrderStatus.PREPARED: {OrderStatus.PICKED_UP, OrderStatus.CANCELLED},
    OrderStatus.PICKED_UP: set(),
    OrderStatus.CANCELLED: set(),
}

STATUS_BY_LOWER: dict[str, OrderStatus] = {
    status.value.lower(): status for status in OrderStatus
}


def format_order_id(n: int) -> str:
    """Formatea un número correlativo a formato de orden (RF-17).

    Rellena con ceros hasta 4 dígitos (1 -> 'ORD-0001', 42 -> 'ORD-0042').
    Para números superiores a 9999, no trunca (10000 -> 'ORD-10000').
    Lanza ValueError si n <= 0.
    """
    if n <= 0:
        raise ValueError(f"El correlativo de orden debe ser positivo, se recibió {n}")
    return f"{ORDER_ID_PREFIX}{n:04d}"


def parse_order_id(order_id: str) -> int:
    """Parsea y valida estrictamente un order_id (RF-17).

    Exige el prefijo 'ORD-' y al menos 4 dígitos, sin ceros redundantes.
    Lanza ValueError si el formato no es válido o el número no es positivo.
    """
    match = ORDER_ID_REGEX.match(order_id)
    if not match:
        raise ValueError(
            f"Formato de order_id inválido: {order_id!r}. Se esperaba 'ORD-0001'"
        )
    num = int(match.group(1))
    if num <= 0:
        raise ValueError(f"El número de orden debe ser mayor a 0: {order_id!r}")
    if format_order_id(num) != order_id:
        raise ValueError(
            f"Formato de order_id inválido (ceros excedentes o longitud inconsistente): {order_id!r}"
        )
    return num


def can_transition(actual: OrderStatus, nuevo: OrderStatus) -> bool:
    """Indica si una transición entre estados de orden es válida (RF-20).

    Recibido -> Preparado, Cancelado
    Preparado -> Retirado, Cancelado
    Retirado / Cancelado -> Estados terminales (no permiten transiciones).
    """
    return nuevo in VALID_TRANSITIONS.get(actual, set())


def parse_status(valor: str) -> OrderStatus:
    """Parsea un estado de orden tolerando mayúsculas y espacios en blanco extremos (RF-20).

    Lanza ValueError ante estados desconocidos.
    """
    key = valor.strip().lower()
    if key not in STATUS_BY_LOWER:
        validos = [s.value for s in OrderStatus]
        raise ValueError(f"Estado de orden desconocido: {valor!r}. Estados válidos: {validos}")
    return STATUS_BY_LOWER[key]


class OrderNotFoundError(ValueError):
    """La orden solicitada no existe."""


class InvalidTransitionError(ValueError):
    """La transición de estado de la orden no es válida."""


class InsufficientStockError(Exception):
    """El stock ya no alcanza para crear la orden. `lines` indica qué líneas faltan."""

    def __init__(self, lines: list[dict]):
        super().__init__("No alcanza el stock")
        self.lines = lines


def _to_order(record: OrderRecord) -> Order:
    return Order(
        order_id=record.order_id,
        draft_id=record.draft_id,
        created_at=record.created_at,
        customer_id=record.customer_id,
        channel=record.channel,
        items=record.items,
        notes=record.notes,
        status=record.status,
        sheets_synced=record.sheets_synced,
    )


def get_order(order_id: str) -> Order | None:
    with SessionLocal() as session:
        record = session.scalar(select(OrderRecord).where(OrderRecord.order_id == order_id))
        return _to_order(record) if record else None


def confirm_draft(draft: OrderDraft) -> Order:
    """Registra la orden de un borrador UNA sola vez (RF-14, RF-15).

    Es idempotente: si el borrador ya tiene orden (reintento, mensaje repetido) devuelve la existente.
    Pasos: buscar orden existente -> revalidar stock con una lectura fresca -> guardar en la base.
    """
    with SessionLocal() as session:
        if session.bind and session.bind.dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")

        existing = session.scalar(select(OrderRecord).where(OrderRecord.draft_id == draft.draft_id))
        if existing:
            return _to_order(existing)

        stock = {product.sku: product.stock for product in inventory_service.get_catalog(session=session, fresh=True)}
        short = [
            {"sku": item.sku, "requested": item.quantity, "available": stock.get(item.sku, 0)}
            for item in draft.items
            if item.quantity > stock.get(item.sku, 0)
        ]
        if short:
            raise InsufficientStockError(short)

        record = OrderRecord(
            draft_id=draft.draft_id,
            customer_id=draft.customer_id,
            channel=draft.channel.value,
            items=[item.model_dump(mode="json") for item in draft.items],
            notes=draft.notes,
            status=OrderStatus.RECEIVED.value,
        )
        session.add(record)
        try:
            session.flush()  # el id correlativo sale de la base
            record.order_id = format_order_id(record.id)
            session.commit()
        except IntegrityError:  # otro proceso registró el mismo borrador primero
            session.rollback()
            return _to_order(session.scalars(select(OrderRecord).where(OrderRecord.draft_id == draft.draft_id)).one())

        order = _to_order(record)
        try:
            sheets_service.append_order(order)
            record.sheets_synced = True
            session.commit()
        except Exception:
            logger.warning(
                "Error al volcar la orden %s en la planilla/archivo de órdenes",
                order.order_id,
                exc_info=True,
            )
        return _to_order(record)


def cancel_order(order_id: str) -> Order:
    """Cancela una orden activa y libera su stock comprometido (RF-20)."""
    with SessionLocal() as session:
        record = session.scalar(select(OrderRecord).where(OrderRecord.order_id == order_id))
        if not record:
            raise OrderNotFoundError(f"Orden no encontrada: {order_id!r}")
        actual = parse_status(record.status)
        if not can_transition(actual, OrderStatus.CANCELLED):
            raise InvalidTransitionError(f"No se puede cancelar una orden en estado {actual.value!r}")
        record.status = OrderStatus.CANCELLED.value
        session.commit()
        return _to_order(record)


def sync_pending_orders() -> int:
    """Reintenta sincronizar órdenes con sheets_synced=False (CA-19, RNF-04).

    Devuelve la cantidad de órdenes sincronizadas exitosamente en esta pasada.
    """
    synced_count = 0
    with SessionLocal() as session:
        records = session.scalars(select(OrderRecord).where(OrderRecord.sheets_synced.is_(False))).all()
        for record in records:
            order = _to_order(record)
            try:
                sheets_service.append_order(order)
                record.sheets_synced = True
                synced_count += 1
            except Exception:
                logger.warning(
                    "Error al sincronizar orden %s en reintento",
                    record.order_id,
                    exc_info=True,
                )
        if synced_count > 0:
            session.commit()
    return synced_count


def list_orders() -> list[Order]:
    """Todas las órdenes registradas, de la más vieja a la más nueva (exportación a Excel, RF-54)."""
    with SessionLocal() as session:
        records = session.scalars(
            select(OrderRecord).where(OrderRecord.order_id.is_not(None)).order_by(OrderRecord.id)
        ).all()
        return [_to_order(record) for record in records]
