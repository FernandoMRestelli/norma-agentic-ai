"""Lógica de negocio de la confirmación: el único lugar donde nace una `Order`."""

from __future__ import annotations

import logging
import re

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from internal.database.connection import SessionLocal
from internal.database.models import DraftRecord, OrderEventRecord, OrderItemRecord, OrderRecord
from internal.schemas.order_schema import (
    DraftStatus,
    Order,
    OrderDraft,
    OrderEvent,
    OrderItem,
    OrderStatus,
)
from internal.services import inventory_service, sheets_service

logger = logging.getLogger(__name__)

ORDER_ID_PREFIX = "ORD-"
ORDER_ID_REGEX = re.compile(r"^ORD-(\d{4,})$")

VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.RECEIVED: {OrderStatus.PREPARED, OrderStatus.CANCELLED},
    OrderStatus.PREPARED: {OrderStatus.PICKED_UP, OrderStatus.DISPATCHED, OrderStatus.CANCELLED},
    OrderStatus.DISPATCHED: {OrderStatus.DELIVERED},
    OrderStatus.PICKED_UP: set(),
    OrderStatus.DELIVERED: set(),
    OrderStatus.CANCELLED: set(),
}

# Al pasar a uno de estos estados la mercadería sale: se descuenta del catálogo (Issue #61)
STOCK_OUT_STATES = (OrderStatus.PICKED_UP, OrderStatus.DISPATCHED)

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


class EventSource:
    """Quién hizo un cambio de estado (`order_events.source`)."""

    AGENT = "agente"
    OPERATOR = "operador"
    MIGRATION = "migracion"


def _begin_immediate(session: Session) -> None:
    """Toma el lock de escritura de SQLite antes de leer: dos escrituras a la vez ven el estado de la otra."""
    if session.bind and session.bind.dialect.name == "sqlite":
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")


def _to_order(record: OrderRecord) -> Order:
    return Order(
        order_id=record.order_id,
        draft_id=record.draft_id,
        created_at=record.created_at,
        customer_id=record.customer_id,
        customer_name=record.customer_name,
        channel=record.channel,
        conversation_id=record.conversation_id,
        items=[
            OrderItem(
                sku=line.sku,
                product_name=line.product_name,
                presentation=line.presentation,
                quantity=line.quantity,
                unit_price=line.unit_price,
                note=line.note,
            )
            for line in record.lines
        ],
        notes=record.notes,
        total=record.total,
        status=record.status,
        sheets_synced=record.sheets_synced,
        stock_applied=record.stock_applied,
        history=[
            OrderEvent(
                from_status=event.from_status,
                to_status=event.to_status,
                source=event.source,
                created_at=event.created_at,
            )
            for event in record.events
        ],
    )


def _find(session: Session, order_id: str) -> OrderRecord | None:
    return session.scalar(select(OrderRecord).where(OrderRecord.order_id == order_id))


def get_order(order_id: str) -> Order | None:
    with SessionLocal() as session:
        record = _find(session, order_id)
        return _to_order(record) if record else None


def _draft_items(draft: OrderDraft) -> list[dict]:
    return [item.model_dump(mode="json") for item in draft.items]


def _upsert_draft(session: Session, draft: OrderDraft, status: DraftStatus, order_id: str | None = None) -> None:
    """Guarda una versión del borrador en `drafts`, o actualiza su estado si ya estaba (RF-46).

    Si la versión ya estaba y sigue pendiente (un reintento después de una respuesta rechazada), queda con las
    líneas, las notas y el total nuevos. Una versión con desenlace no cambia de estado por volver a registrarla
    pendiente.
    """
    record = session.scalar(
        select(DraftRecord).where(DraftRecord.draft_id == draft.draft_id, DraftRecord.version == draft.version)
    )
    if record is None:
        record = DraftRecord(
            draft_id=draft.draft_id,
            version=draft.version,
            conversation_id=draft.conversation_id,
            customer_id=draft.customer_id,
            channel=draft.channel.value,
        )
        session.add(record)
    elif record.status != DraftStatus.PENDING_CONFIRMATION.value and status == DraftStatus.PENDING_CONFIRMATION:
        return  # ya tuvo desenlace
    if record.status in (None, DraftStatus.PENDING_CONFIRMATION.value):  # nueva, o pendiente que se repite
        record.items = _draft_items(draft)
        record.notes = draft.notes
        record.total = draft.total
    record.status = status.value
    record.order_id = order_id
    # Las otras versiones pendientes del mismo borrador ya no valen: solo se registra la última (RF-16)
    session.execute(
        update(DraftRecord)
        .where(
            DraftRecord.draft_id == draft.draft_id,
            DraftRecord.version != draft.version,
            DraftRecord.status == DraftStatus.PENDING_CONFIRMATION.value,
        )
        .values(status=DraftStatus.REPLACED.value)
    )


def record_draft(draft: OrderDraft) -> None:
    """Registra una versión propuesta. Es trazabilidad: si falla, se anota y la conversación sigue."""
    try:
        with SessionLocal() as session:
            _upsert_draft(session, draft, DraftStatus.PENDING_CONFIRMATION)
            session.commit()
    except Exception:
        logger.warning(
            "No se pudo registrar la versión %d del borrador %s", draft.version, draft.draft_id, exc_info=True
        )


def discard_draft(draft: OrderDraft) -> None:
    """La propuesta dejó de valer sin convertirse en orden: el agente respondió sin volver a proponerla."""
    try:
        with SessionLocal() as session:
            session.execute(
                update(DraftRecord)
                .where(
                    DraftRecord.draft_id == draft.draft_id,
                    DraftRecord.status == DraftStatus.PENDING_CONFIRMATION.value,
                )
                .values(status=DraftStatus.DISCARDED.value)
            )
            session.commit()
    except Exception:
        logger.warning("No se pudo descartar el borrador %s", draft.draft_id, exc_info=True)


def confirm_draft(draft: OrderDraft) -> Order:
    """Registra la orden de un borrador UNA sola vez (RF-14, RF-15).

    Es idempotente: si el borrador ya tiene orden (reintento, mensaje repetido) devuelve la existente.
    Pasos: buscar orden existente -> revalidar stock con una lectura fresca -> guardar la orden con sus líneas, su
    primer evento y la versión aceptada del borrador, todo en la misma transacción.
    """
    with SessionLocal() as session:
        _begin_immediate(session)

        existing = session.scalar(select(OrderRecord).where(OrderRecord.draft_id == draft.draft_id))
        if existing:
            return _to_order(existing)

        stock = {product.sku: product.stock for product in inventory_service.get_catalog(session=session, fresh=True)}
        requested: dict[str, int] = {}
        for item in draft.items:  # un SKU puede venir en varias líneas, una por nota
            requested[item.sku] = requested.get(item.sku, 0) + item.quantity
        short = [
            {"sku": sku, "requested": quantity, "available": stock.get(sku, 0)}
            for sku, quantity in requested.items()
            if quantity > stock.get(sku, 0)
        ]
        if short:
            raise InsufficientStockError(short)

        record = OrderRecord(
            draft_id=draft.draft_id,
            customer_id=draft.customer_id,
            customer_name=draft.customer_name,
            channel=draft.channel.value,
            conversation_id=draft.conversation_id,
            notes=draft.notes,
            total=draft.total,
            status=OrderStatus.RECEIVED.value,
            lines=[
                OrderItemRecord(
                    position=position,
                    sku=item.sku,
                    product_name=item.product_name,
                    presentation=item.presentation,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    note=item.note,
                )
                for position, item in enumerate(draft.items, start=1)
            ],
            events=[OrderEventRecord(from_status=None, to_status=OrderStatus.RECEIVED.value, source=EventSource.AGENT)],
        )
        session.add(record)
        try:
            session.flush()  # el id correlativo sale de la base
            record.order_id = format_order_id(record.id)
            _upsert_draft(session, draft, DraftStatus.CONFIRMED, record.order_id)
            session.commit()
        except IntegrityError:  # otro proceso registró el mismo borrador primero
            session.rollback()
            return _to_order(session.scalars(select(OrderRecord).where(OrderRecord.draft_id == draft.draft_id)).one())

        order = _to_order(record)
        try:
            sheets_service.append_order(order)
            record.sheets_synced = True
            session.commit()
        except sheets_service.Sheet2EnPausaError as exc:
            logger.info("%s", exc)
        except Exception:
            logger.warning(
                "Error al volcar la orden %s en la planilla/archivo de órdenes",
                order.order_id,
                exc_info=True,
            )
        return _to_order(record)


def change_status(order_id: str, nuevo: OrderStatus, source: str = EventSource.OPERATOR) -> Order:
    """Pasa una orden a otro estado si la transición es válida y guarda el evento (RF-20, RF-47).

    Si pasa a Despachado o Retirado y todavía no se descontó, resta sus unidades del catálogo y marca
    `stock_applied`: desde ahí la orden deja de reservar stock (Issue #61). Con el catálogo en Sheet 1 (solo lectura)
    o el de ejemplo no se descuenta, y la orden sigue reservando.
    """
    with SessionLocal() as session:
        _begin_immediate(session)
        record = _find(session, order_id)
        if not record:
            raise OrderNotFoundError(f"Orden no encontrada: {order_id!r}")
        actual = parse_status(record.status)
        if not can_transition(actual, nuevo):
            if nuevo == OrderStatus.CANCELLED:
                raise InvalidTransitionError(f"No se puede cancelar una orden en estado {actual.value!r}")
            raise InvalidTransitionError(f"No se puede pasar una orden de {actual.value!r} a {nuevo.value!r}")
        if nuevo in STOCK_OUT_STATES and not record.stock_applied:
            lineas = [{"sku": line.sku, "quantity": line.quantity} for line in record.lines]
            record.stock_applied = sheets_service.deduct_catalog_stock(lineas)
        record.status = nuevo.value
        record.events.append(OrderEventRecord(from_status=actual.value, to_status=nuevo.value, source=source))
        session.commit()
        sheets_service.update_order_status_in_sheets(order_id, nuevo.value)
        return _to_order(record)


def cancel_order(order_id: str, source: str = EventSource.OPERATOR) -> Order:
    """Cancela una orden activa y libera su stock comprometido (RF-20)."""
    return change_status(order_id, OrderStatus.CANCELLED, source)


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
                sheets_service.append_order(order, reintento=True)
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
