"""Arma los textos del pedido que ve el cliente, siempre a partir de datos validados."""

from internal.schemas.order_schema import Order, OrderDraft, OrderItem


def _line(item: OrderItem) -> str:
    presentation = f" {item.presentation}" if item.presentation else ""
    return f"• {item.quantity} x {item.product_name}{presentation}"


def build_summary(draft: OrderDraft) -> str:
    lines = ["Esto es lo que entendí:"]
    lines.extend(_line(item) for item in draft.items)
    if draft.notes:
        lines.append(f"Notas: {draft.notes}")
    lines.append("¿Confirmás el pedido o querés corregir algo?")
    return "\n".join(lines)


def build_order_message(order: Order) -> str:
    """Mensaje final: muestra lo que quedó registrado, sin depender de lo que escribió el LLM (RF-11)."""
    lines = ["Listo, pedido hecho:", *(_line(item) for item in order.items), f"Tu número es {order.order_id}."]
    return "\n".join(lines)
