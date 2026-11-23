"""Tools de órdenes: registro de propuestas, historial para repetir y consulta de estado (spec del agente §4.2)."""

from datetime import UTC
from zoneinfo import ZoneInfo

from internal.schemas.agent_schema import ToolFailure
from internal.schemas.order_schema import Order, OrderDraft, OrderStatus
from internal.services import order_service

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")

_DESCRIPCION_ESTADO: dict[OrderStatus, str] = {
    OrderStatus.RECEIVED: "Recibido, pendiente de preparación",
    OrderStatus.PREPARED: "Preparado y listo para retirar o despachar",
    OrderStatus.DISPATCHED: "Despachado",
    OrderStatus.PICKED_UP: "Retirado",
    OrderStatus.DELIVERED: "Entregado",
    OrderStatus.CANCELLED: "Cancelado",
}


def armar_pedido(vigente: OrderDraft | None) -> Order:
    if vigente is None:
        raise ToolFailure(
            "sin_propuesta",
            "No hay ninguna propuesta vigente, así que no hay nada para registrar: "
            "preguntale al cliente qué quiere pedir",
        )
    try:
        return order_service.confirm_draft(vigente)
    except order_service.InsufficientStockError as exc:
        raise ToolFailure(
            "stock_insuficiente",
            "Ya no alcanza el stock: usá las cantidades de `lineas` y seguí BR-01, de la skill tomar_pedido",
            lineas=exc.lines,
        ) from exc


def pedidos_anteriores(customer_id: str, limite: int = 3) -> list[dict]:
    """Los últimos pedidos del cliente, con sus líneas (RF-73).

    Sin precios, número de orden ni estado: los precios pueden haber cambiado y el estado de un pedido no se
    consulta por el chat. El `customer_id` lo pone el backend, nunca el LLM.
    """
    return [
        {
            "fecha": order.created_at.date().isoformat(),
            "lineas": [
                {"sku": item.sku, "name": item.product_name, "cantidad": item.quantity}
                | ({"nota": item.note} if item.note else {})
                for item in order.items
            ],
        }
        for order in order_service.customer_history(customer_id, limite)
    ]


def consultar_estado_pedido(
    customer_id: str,
    order_id: str | None = None,
    limite: int = 3,
) -> list[dict]:
    """Consulta el estado actual de los últimos pedidos del cliente (Issue #26, RNF-02).

    El `customer_id` lo inyecta el backend, nunca el LLM. No expone SKUs ni precios internos.
    """
    orders = order_service.list_customer_orders(customer_id, limit=limite, order_id=order_id)
    resultado: list[dict] = []
    for order in orders:
        dt = order.created_at if order.created_at.tzinfo else order.created_at.replace(tzinfo=UTC)
        fecha_ar = dt.astimezone(AR_TZ).date().isoformat()
        entrada: dict = {
            "order_id": order.order_id,
            "fecha": fecha_ar,
            "estado": order.status.value,
            "estado_detalle": _DESCRIPCION_ESTADO.get(order.status, order.status.value),
            "lineas": [
                {
                    "name": item.product_name,
                    "cantidad": item.quantity,
                }
                | ({"presentacion": item.presentation} if item.presentation else {})
                for item in order.items
            ],
        }
        if order.notes:
            entrada["notas"] = order.notes
        resultado.append(entrada)
    return resultado


def cancelar_pedido(order_id: str) -> Order:
    """Cancela una orden existente liberando su stock comprometido (RF-20)."""
    try:
        return order_service.cancel_order(order_id, source=order_service.EventSource.AGENT)
    except (order_service.OrderNotFoundError, order_service.InvalidTransitionError) as exc:
        raise ToolFailure("orden_invalida", str(exc)) from exc
