"""Lógica de negocio de la confirmación: el único lugar donde nace una `Order`."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from backend.schemas.order_schema import OrderStatus

if TYPE_CHECKING:
    from backend.schemas.order_schema import Order

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


def confirm_draft(draft_id: str) -> Order:
    """Confirma un borrador y registra la orden UNA sola vez.

    Debe ser idempotente: si el borrador ya fue confirmado (reintento, doble click,
    webhook repetido) devuelve la orden existente en lugar de crear otra.
    Pasos: revalidar stock -> generar order_id -> guardar en base -> sheets_service.append_order.
    """
    raise NotImplementedError
