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


# --- Los hechos de la respuesta los escribe el código (M8-2) -------------------------------------------------------
#
# El LLM escribe el marco: qué armó, cómo cierra y la pregunta. Las opciones de cada duda, lo que no entra y cuánto
# queda salen de los campos estructurados de `respond_to_customer` y del catálogo: son correctos por construcción y
# no hace falta revisarlos con reglas sobre el texto del modelo.

ENCABEZADO_DUDAS = "Me falta que me digas:"
ENCABEZADO_AFUERA = "Lo que no va como lo pediste:"
CIERRE_OPCIONES = "¿Cuál preferís?"
# Lo que se le dice al cliente de cada motivo de `afuera`. Lo que sacó él mismo o una línea que no pedía nada no se
# le repite: ya lo sabe
MOTIVOS_PARA_EL_CLIENTE = {"fuera_de_catalogo": "no lo trabajamos", "sin_stock": "está agotado por ahora"}
_VINETA_DEL_MODELO = re.compile(r"^\s*(?:[•·*-]|\d{1,2}[.)])\s+\S")
_X_INICIAL = re.compile(r"^x\s+", re.IGNORECASE)


def nombre_comercial(sku: str) -> str | None:
    """El nombre del producto con su presentación, si no la trae el nombre. None si el SKU no existe."""
    from internal.tools.catalog_tools import get_product

    product = get_product(sku)
    if product is None:
        return None
    if product.presentation and product.presentation.lower() not in product.name.lower():
        return f"{product.name} {product.presentation}"
    return product.name


def distintivos(nombres: list[str]) -> list[str]:
    """Lo que distingue a cada opción: sin las palabras que todas comparten al principio.

    «NORDIKA SHAMPOO KERATINA X 300 ML» y «… X 900 ML» quedan «300 ML» y «900 ML». Si lo que queda no alcanza para
    distinguirlas, van los nombres completos.
    """
    if len(nombres) < 2:
        return nombres
    palabras = [nombre.split() for nombre in nombres]
    comunes = 0
    while all(len(p) > comunes + 1 for p in palabras) and len({p[comunes].lower() for p in palabras}) == 1:
        comunes += 1
    cortos = [_X_INICIAL.sub("", " ".join(p[comunes:])) for p in palabras]
    if len({corto.lower() for corto in cortos}) < len(cortos) or not all(cortos):
        return nombres
    return cortos


def _o(opciones: list[str]) -> str:
    """«a, b o c»; «u» delante de una palabra que empieza con o («6 u 8»)."""
    if len(opciones) == 1:
        return opciones[0]
    ultimo = opciones[-1]
    conjuncion = "u" if ultimo.lower().startswith(("o", "ho", "8")) else "o"
    return f"{', '.join(opciones[:-1])} {conjuncion} {ultimo}"


def build_dudas(pendientes: list[tuple[str, list[str]]]) -> str:
    """Cada renglón que se le pregunta al cliente, como lo escribió él, con sus opciones (M8-2, cierra el #107).

    `pendientes` son pares (texto del renglón, SKU de las opciones). Una sola opción es una confirmación
    («¿es …?», BR-09); sin opciones, se le pide que lo aclare.
    """
    lineas = []
    for texto, skus in pendientes:
        nombres = [nombre for sku in dict.fromkeys(skus) if (nombre := nombre_comercial(sku))]
        if not nombres:
            pregunta = "¿me lo aclarás?"
        elif len(nombres) == 1:
            pregunta = f"¿es {nombres[0]}?"
        else:
            pregunta = f"¿{_o(distintivos(nombres))}?"
        lineas.append(f"• {texto}: {pregunta}")
    return "\n".join([ENCABEZADO_DUDAS, *lineas]) if lineas else ""


def build_afuera(afuera: list[tuple[str, str]], cortos: list[tuple[str, int]]) -> str:
    """Lo que no entra como lo pidió: cada renglón con su motivo, y cuánto queda del que no alcanzó (M8-2, BR-01).

    `afuera` son pares (texto del renglón, motivo); `cortos`, pares (texto del renglón, unidades que se proponen).
    """
    lineas = [f"• {texto}: {MOTIVOS_PARA_EL_CLIENTE[motivo]}" for texto, motivo in afuera
              if motivo in MOTIVOS_PARA_EL_CLIENTE]  # fmt: skip
    lineas += [f"• {texto}: quedan {unidades}, te puse esas" for texto, unidades in cortos]
    return "\n".join([ENCABEZADO_AFUERA, *lineas]) if lineas else ""


def sin_vinetas(message: str) -> str:
    """El mensaje del LLM sin los renglones con viñeta: las listas las escribe el sistema, y no van dos veces."""
    lineas = [linea for linea in message.splitlines() if not _VINETA_DEL_MODELO.match(linea)]
    limpio = "\n".join(lineas).strip()
    return limpio or message


def armar_respuesta(
    message: str,
    propuesta: OrderDraft | None = None,
    *,
    dudas: str = "",
    afuera: str = "",
    opciones: str = "",
) -> str:
    """Lo que ve el cliente: el marco que escribió el LLM y, debajo, los hechos que arma el sistema.

    Primero el detalle de la propuesta, después lo que no va como lo pidió y al final lo que tiene que contestar.
    """
    bloques = [bloque for bloque in (afuera, dudas, opciones) if bloque]
    texto = sin_vinetas(message) if bloques else message
    if opciones and "?" not in texto:
        bloques[-1] = f"{opciones}\n{CIERRE_OPCIONES}"
    detalle = [build_proposal_detail(propuesta)] if propuesta else []
    return "\n\n".join([texto, *detalle, *bloques])


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
