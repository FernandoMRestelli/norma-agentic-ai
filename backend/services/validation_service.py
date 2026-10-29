"""Arma el resumen del borrador que ve el cliente con las opciones Confirmar / Corregir."""

from backend.schemas.order_schema import OrderDraft


def build_summary(draft: OrderDraft) -> str:
    lines = ["Esto es lo que entendí:"]
    for item in draft.items:
        presentation = f" {item.presentation}" if item.presentation else ""
        lines.append(f"• {item.quantity} x {item.product_name}{presentation}")
    if draft.notes:
        lines.append(f"Notas: {draft.notes}")
    lines.append("¿Confirmás el pedido o querés corregir algo?")
    return "\n".join(lines)
