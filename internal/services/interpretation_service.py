"""Convierte lo que propone el LLM ({sku, quantity, note}) en líneas de pedido validadas contra el catálogo."""

from typing import Any

from internal.schemas.agent_schema import ProposedItem, ToolFailure
from internal.schemas.catalog_schema import Product
from internal.schemas.order_schema import OrderItem, clean_note
from internal.services import busqueda_service, inventory_service
from internal.services.palabras_clave import es_saludo

MAX_PALABRAS_ENCABEZADO = 3
MIN_RENGLONES = 2  # un mensaje de un solo renglón no se numera: se trata como hasta ahora


def normalize_items(items: list[ProposedItem], renglones: list[dict[str, Any]] | None = None) -> list[OrderItem]:
    """Completa nombre, presentación, marca, categoría y precio desde el catálogo.

    Las líneas con el mismo SKU y la misma nota se suman en una; con notas distintas quedan separadas (dos tonos
    de una coloración que es un solo SKU, V-01). El stock se controla por SKU, con la suma de todas sus líneas.
    Con `renglones`, una línea que nombra su renglón guarda cómo lo escribió el cliente y si la coincidencia fue
    aproximada (RF-75, D-24).
    """
    catalog = {product.sku: product for product in inventory_service.get_catalog()}
    numerados = {renglon["n"]: renglon for renglon in renglones or []}
    lines: dict[tuple[str, str | None], int] = {}
    escritos: dict[tuple[str, str | None], list[str]] = {}
    coincidencias: dict[tuple[str, str | None], str] = {}
    for item in items:
        key = (item.sku, clean_note(item.note))
        lines[key] = lines.get(key, 0) + item.quantity
        if (renglon := numerados.get(item.renglon)) is not None:
            if renglon["texto"] not in escritos.setdefault(key, []):
                escritos[key].append(renglon["texto"])
            if coincidencias.get(key) != "aproximada":
                coincidencias[key] = renglon.get("coincidencia", "exacta")

    per_sku: dict[str, int] = {}
    for (sku, _), quantity in lines.items():
        per_sku[sku] = per_sku.get(sku, 0) + quantity

    unknown = [sku for sku in per_sku if sku not in catalog]
    if unknown:
        raise ToolFailure(
            "sku_inexistente",
            f"No existen estos SKU: {', '.join(unknown)}. "
            "Buscá de nuevo el producto con search_products, no pruebes SKU de memoria",
            skus=unknown,
        )

    short = [
        {"sku": sku, "requested": quantity, "available": catalog[sku].stock}
        for sku, quantity in per_sku.items()
        if quantity > catalog[sku].stock
    ]
    if short:
        raise ToolFailure(
            "stock_insuficiente",
            "No alcanza el stock de alguna línea: "
            "usá las cantidades de `lineas` y seguí BR-01, de la skill tomar_pedido",
            lineas=short,
        )

    return [
        OrderItem(
            sku=sku,
            product_name=catalog[sku].name,
            presentation=catalog[sku].presentation,
            quantity=quantity,
            unit_price=catalog[sku].price,
            note=note,
            brand=catalog[sku].brand,
            category=catalog[sku].category,
            match=coincidencias.get((sku, note)),
            source_text=" + ".join(escritos.get((sku, note), [])) or None,
        )
        for (sku, note), quantity in lines.items()
    ]


def _marca_del_encabezado(linea: str, marcas: dict[str, tuple[str, ...]]) -> str | None:
    """La marca del catálogo que nombra la línea, si la línea no dice nada más (RF-65).

    «belcor» es BELKOR y «issue» es ISSUE PROFESIONAL: cada palabra de la línea tiene que parecerse a una de la marca.
    """
    if any(caracter.isdigit() for caracter in linea):
        return None
    palabras = [p.canon for p in busqueda_service.normalizar(linea).palabras]
    if not 0 < len(palabras) <= MAX_PALABRAS_ENCABEZADO:
        return None
    mejor: tuple[float, str] | None = None
    for marca, de_la_marca in marcas.items():
        parecidos = [max((busqueda_service.parecido(p, m) for m in de_la_marca), default=0.0) for p in palabras]
        if min(parecidos) >= busqueda_service.MIN_PALABRA:
            promedio = sum(parecidos) / len(parecidos)
            if mejor is None or promedio > mejor[0]:
                mejor = (promedio, marca)
    return mejor[1] if mejor else None


def numerar_renglones(texto: str, productos: list[Product], desde: int = 1) -> list[dict[str, Any]]:
    """Parte un pedido en renglones numerados y le pone a cada uno la marca de su encabezado (RF-65).

    No interpreta productos: eso es del LLM. Saca del medio lo que seguro no es un renglón de pedido, que son los
    encabezados de marca, los saludos y las líneas que no nombran nada del catálogo ni traen un número. Una línea
    así puede ser una marca que el catálogo no tiene («Kerastase») o un «sumale»: corta la marca anterior, y los
    renglones que siguen quedan sin marca hasta el próximo encabezado. Un mensaje de una sola línea devuelve una
    lista vacía.
    """
    lineas = [linea.strip() for linea in texto.splitlines() if linea.strip()]
    if len(lineas) < MIN_RENGLONES:
        return []
    indice = busqueda_service.obtener_indice(productos)
    marcas = {
        marca: tuple(p.canon for p in busqueda_service.normalizar(marca).palabras)
        for marca in sorted({product.brand for product in productos if product.brand})
    }
    renglones: list[dict[str, Any]] = []
    marca: str | None = None
    for linea in lineas:
        if encabezado := _marca_del_encabezado(linea, marcas):
            marca = encabezado
            continue
        tiene_numero = any(caracter.isdigit() for caracter in linea)
        del_catalogo = any(indice.parecidos(p) for p in busqueda_service.normalizar(linea).palabras)
        if es_saludo(linea):
            continue
        if not (tiene_numero or del_catalogo):
            marca = None
            continue
        renglones.append({"n": desde + len(renglones), "texto": linea, "marca": marca, "estado": "sin_resolver"})
    return renglones
