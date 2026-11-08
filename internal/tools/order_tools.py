"""Tool que registra el pedido. No recibe productos: solo puede crear la propuesta vigente (spec del agente §5)."""

from internal.schemas.agent_schema import ToolFailure
from internal.schemas.order_schema import Order, OrderDraft
from internal.services import order_service


def armar_pedido(vigente: OrderDraft | None) -> Order:
    if vigente is None:
        raise ToolFailure("sin_propuesta", "No hay ninguna propuesta vigente: proponé el pedido antes de armarlo")
    try:
        return order_service.confirm_draft(vigente)
    except order_service.InsufficientStockError as exc:
        raise ToolFailure("stock_insuficiente", "Ya no alcanza el stock: proponé de nuevo", lineas=exc.lines) from exc


def cancelar_pedido(order_id: str) -> Order:
    """Cancela una orden existente liberando su stock comprometido (RF-20)."""
    try:
        return order_service.cancel_order(order_id)
    except (order_service.OrderNotFoundError, order_service.InvalidTransitionError) as exc:
        raise ToolFailure("orden_invalida", str(exc)) from exc
