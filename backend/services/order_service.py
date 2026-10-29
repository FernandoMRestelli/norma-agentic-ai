"""Lógica de negocio de la confirmación: el único lugar donde nace una `Order`."""

from backend.schemas.order_schema import Order


def confirm_draft(draft_id: str) -> Order:
    """Confirma un borrador y registra la orden UNA sola vez.

    Debe ser idempotente: si el borrador ya fue confirmado (reintento, doble click,
    webhook repetido) devuelve la orden existente en lugar de crear otra.
    Pasos: revalidar stock -> generar order_id -> guardar en base -> sheets_service.append_order.
    """
    raise NotImplementedError
