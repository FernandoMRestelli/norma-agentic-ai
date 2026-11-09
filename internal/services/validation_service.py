"""Arma los textos del pedido que ve el cliente, siempre a partir de datos validados."""

from decimal import Decimal

from internal.schemas.order_schema import Order, OrderDraft, OrderItem


def _line(item: OrderItem) -> str:
    presentation = f" {item.presentation}" if item.presentation else ""
    price_info = ""
    if item.unit_price is not None and item.unit_price > 0:
        if item.quantity > 1:
            subtotal = item.quantity * item.unit_price
            price_info = f" (${item.unit_price:.2f} c/u = ${subtotal:.2f})"
        else:
            price_info = f" (${item.unit_price:.2f})"
    return f"• {item.quantity} x {item.product_name}{presentation}{price_info}"


def build_summary(draft: OrderDraft) -> str:
    lines = ["Esto es lo que entendí:"]
    lines.extend(_line(item) for item in draft.items)
    total = sum((item.quantity * item.unit_price for item in draft.items if item.unit_price is not None), Decimal(0))
    if total > 0:
        lines.append(f"Total estimado: ${total:.2f}")
    if draft.notes:
        lines.append(f"Notas: {draft.notes}")
    lines.append("¿Confirmás el pedido o querés corregir algo?")
    return "\n".join(lines)


def build_order_message(order: Order) -> str:
    """Mensaje final: muestra lo que quedó registrado, sin depender de lo que escribió el LLM (RF-11, D-04)."""
    lines = ["Listo, pedido hecho:", *(_line(item) for item in order.items)]
    total = sum((item.quantity * item.unit_price for item in order.items if item.unit_price is not None), Decimal(0))
    if total > 0:
        lines.append(f"Total: ${total:.2f}")
    lines.append(f"Tu número es {order.order_id}.")
    return "\n".join(lines)
