"""Tool que registra el pedido. No recibe productos: solo puede crear la propuesta vigente (spec del agente §5)."""

from internal.schemas.agent_schema import ToolFailure
from internal.schemas.order_schema import Order, OrderDraft
from internal.services import order_service


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


def cancelar_pedido(order_id: str) -> Order:
    """Cancela una orden existente liberando su stock comprometido (RF-20)."""
    try:
        return order_service.cancel_order(order_id, source=order_service.EventSource.AGENT)
    except (order_service.OrderNotFoundError, order_service.InvalidTransitionError) as exc:
        raise ToolFailure("orden_invalida", str(exc)) from exc
