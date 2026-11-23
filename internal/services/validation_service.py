"""Arma los textos del pedido que ve el cliente, siempre a partir de datos validados."""

import re

from internal.schemas.order_schema import (
    MAX_ORDER_NOTES_LENGTH,
    Order,
    OrderDraft,
    OrderItem,
    descuento,
    items_total,
)

_ACUERDO_RE = re.compile(r"(?:^|\s*)Acuerdo confirmado:\s*.*$", re.IGNORECASE)
_ACUERDO_PREFIJO_RE = re.compile(r"^Acuerdo confirmado:\s*", re.IGNORECASE)


def quitar_acuerdo(notes: str | None) -> str | None:
    """Las notas sin el bloque «Acuerdo confirmado: ...»: el operador lo sacó al resolver de nuevo."""
    if not notes:
        return notes
    return _ACUERDO_RE.sub("", notes).strip() or None


class OrderNotesTooLongError(ValueError):
    """La solicitud y el acuerdo no entran completos; nunca se recortan silenciosamente."""


def _complete_notes(text: str) -> str:
    if len(text) > MAX_ORDER_NOTES_LENGTH:
        raise OrderNotesTooLongError(
            f"Las observaciones y el acuerdo superan los {MAX_ORDER_NOTES_LENGTH} caracteres. "
            "Resumilos conservando las condiciones importantes y volvé a intentar; no se guardó el cambio."
        )
    return text


def merge_order_notes(existing_notes: str | None, order_notes: str | None) -> str | None:
    """Combina las notas del cliente con el acuerdo operativo confirmado sin duplicar al reintentar.

    Conserva la solicitud original del cliente (si existe) y reemplaza o agrega el bloque
    «Acuerdo confirmado: ...» al final.
    """
    if not order_notes or not order_notes.strip():
        return existing_notes
    acuerdo = _ACUERDO_PREFIJO_RE.sub("", order_notes.strip()).strip()
    if not acuerdo:
        return existing_notes
    segmento = f"Acuerdo confirmado: {acuerdo}"
    if not existing_notes or not existing_notes.strip():
        return _complete_notes(segmento)
    base = _ACUERDO_RE.sub("", existing_notes.strip()).strip()
    if not base:
        return _complete_notes(segmento)
    separador = " " if base.endswith((".", ";", "!", "?")) else ". "
    return _complete_notes(f"{base}{separador}{segmento}")


def porcentaje_legible(porcentaje: float) -> str:
    """«10» en vez de «10.0»; «12.5» queda igual."""
    return f"{porcentaje:g}"


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


def _lines_and_total(
    items: list[OrderItem], total_label: str, discount_percentage: float | None = None
) -> list[str]:
    lines = [_line(item) for item in items]
    subtotal = items_total(items)
    if subtotal > 0:
        if discount_percentage:
            monto = descuento(subtotal, discount_percentage)
            lines.append(f"Subtotal: ${subtotal:.2f}")
            lines.append(f"Descuento ({porcentaje_legible(discount_percentage)}%): -${monto:.2f}")
            lines.append(f"{total_label}: ${subtotal - monto:.2f}")
        else:
            lines.append(f"{total_label}: ${subtotal:.2f}")
    return lines


def build_options_list(skus: list[str]) -> str:
    """Lista legible de opciones comerciales a partir de sus SKU validados en el catálogo (Issue #107)."""
    from internal.tools.catalog_tools import get_product

    lines: list[str] = []
    for sku in dict.fromkeys(skus):
        product = get_product(sku)
        if product is None:
            continue
        presentation = (
            f" {product.presentation}"
            if product.presentation and product.presentation.lower() not in product.name.lower()
            else ""
        )
        price_info = f" (${product.price:.2f})" if product.price > 0 else ""
        lines.append(f"• {product.name}{presentation}{price_info}")
    return "\n".join(lines)


def build_proposal_detail(draft: OrderDraft) -> str:
    """Líneas, precios y total de una propuesta: el LLM no los escribe, así no se equivoca ni se pasa del largo."""
    lines = _lines_and_total(draft.items, "Total estimado", draft.discount_percentage)
    if draft.notes:
        lines.append(f"Observaciones: {draft.notes}")
    return "\n".join(lines)


def build_summary(draft: OrderDraft) -> str:
    lines = ["Esto es lo que entendí:", *_lines_and_total(draft.items, "Total estimado", draft.discount_percentage)]
    if draft.notes:
        lines.append(f"Notas: {draft.notes}")
    lines.append("¿Confirmás el pedido o querés corregir algo?")
    return "\n".join(lines)


def build_order_message(order: Order) -> str:
    """Mensaje final: muestra lo que quedó registrado, sin depender de lo que escribió el LLM (RF-11, D-04)."""
    lines = ["Listo, pedido hecho:", *_lines_and_total(order.items, "Total", order.discount_percentage)]
    if order.notes:
        lines.append(f"Observaciones: {order.notes}")
    lines.append(f"Tu número es {order.order_id}.")
    return "\n".join(lines)
