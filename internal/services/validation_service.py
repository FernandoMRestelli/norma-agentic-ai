"""Arma los textos del pedido que ve el cliente, siempre a partir de datos validados."""

from internal.schemas.order_schema import Order, OrderDraft, OrderItem, items_total


def _line(item: OrderItem) -> str:
    presentation = f" {item.presentation}" if item.presentation else ""
    price_info = ""
    if item.unit_price is not None and item.unit_price > 0:
        if item.quantity > 1:
            subtotal = item.quantity * item.unit_price
            price_info = f" (${item.unit_price:.2f} c/u = ${subtotal:.2f})"
        else:
            price_info = f" (${item.unit_price:.2f})"
    note = f" — {item.note}" if item.note else ""
    return f"• {item.quantity} x {item.product_name}{presentation}{price_info}{note}"


def _lines_and_total(items: list[OrderItem], total_label: str) -> list[str]:
    lines = [_line(item) for item in items]
    total = items_total(items)
    if total > 0:
        lines.append(f"{total_label}: ${total:.2f}")
    return lines


def build_proposal_detail(draft: OrderDraft) -> str:
    """Líneas, precios y total de una propuesta: el LLM no los escribe, así no se equivoca ni se pasa del largo."""
    lines = _lines_and_total(draft.items, "Total estimado")
    if draft.notes:
        lines.append(f"Observaciones: {draft.notes}")
    return "\n".join(lines)


def build_summary(draft: OrderDraft) -> str:
    lines = ["Esto es lo que entendí:", *_lines_and_total(draft.items, "Total estimado")]
    if draft.notes:
        lines.append(f"Notas: {draft.notes}")
    lines.append("¿Confirmás el pedido o querés corregir algo?")
    return "\n".join(lines)


def build_order_message(order: Order) -> str:
    """Mensaje final: muestra lo que quedó registrado, sin depender de lo que escribió el LLM (RF-11, D-04)."""
    lines = ["Listo, pedido hecho:", *_lines_and_total(order.items, "Total")]
    if order.notes:
        lines.append(f"Observaciones: {order.notes}")
    lines.append(f"Tu número es {order.order_id}.")
    return "\n".join(lines)
