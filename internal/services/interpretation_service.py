"""Convierte lo que propone el LLM ({sku, quantity, note}) en líneas de pedido validadas contra el catálogo."""

import re
import unicodedata
from typing import Any

from internal.schemas.agent_schema import ProposedItem, ToolFailure
from internal.schemas.catalog_schema import Product
from internal.schemas.order_schema import OrderItem, clean_note
from internal.services import busqueda_service, inventory_service
from internal.services.palabras_clave import empieza_con_saludo, es_saludo

MAX_PALABRAS_ENCABEZADO = 3
MIN_RENGLONES = 2  # un mensaje de un solo renglón no se numera: se trata como hasta ahora

# Formas de un renglón que no admiten dudas (RF-86). Lo demás queda para el LLM
MAX_CANTIDAD_PRECARGA = 99  # «250 shampoo» es más probable una medida que 250 unidades
_CANTIDAD_ADELANTE = re.compile(r"^(?P<cantidad>\d{1,2})(?![\d.,/])\s*(?:x\s+|(?P<unidad>[a-z]+)\s+)?(?P<resto>.+)$")
# «tinte 8.1/2»: tono con punto y cantidad. «821/2» (8.21 o 82.1) y «7/3» sin producto quedan para el LLM
_TONO_Y_CANTIDAD = re.compile(r"^(?P<resto>.*[a-z].*?)\s+(?P<tono>\d{1,2}[.,]\d{1,2})\s*/\s*(?P<cantidad>\d{1,2})$")
# La viñeta o el número de orden de una lista («- 6 shampoo», «• 2 tinte», «1) 6 shampoo») no es parte del renglón
_MARCA_DE_LISTA = re.compile(r"^(?:[-–—•*·]+\s*|\d{1,2}[.)]\s+)")
# Un renglón que depende del historial o de otro mensaje no es inequívoco
_DEPENDE_DE_OTRA_COSA = re.compile(r"\b(siempre|mismo|misma|mismos|mismas|anterior|otra vez|igual|ese|esa|eso)\b")


# La cantidad cuenta otra cosa que unidades del catálogo, o el número puede ser parte del producto: no se controla
_CANTIDAD_DUDOSA = re.compile(
    r"\b(docenas?|doc|media|par|pares|cajas?|packs?|bultos?|displays?|combos?|blisters?|unid\w*|u|cada|todos?|todas?)\b"
)
_OTRA_CANTIDAD = re.compile(r"^(?:[ox/+-]|y|de)?\s*\d|^(?:o|y)\b")


def depende_de_otra_cosa(texto: str) -> bool:
    """El cliente se refiere a algo que no nombra: «lo de siempre», «lo mismo que la otra vez»."""
    return bool(_DEPENDE_DE_OTRA_COSA.search(_minusculas(texto)))


def cantidad_inequivoca(renglon: dict[str, Any]) -> int | None:
    """Las unidades que pide un renglón cuando está escrito de una sola forma posible; None si no (RF-114)."""
    if renglon.get("unidad") or _CANTIDAD_DUDOSA.search(_minusculas(renglon["texto"])):
        return None
    separado = renglon.get("buscar") or separar_renglon(renglon["texto"])
    if separado is None or _OTRA_CANTIDAD.search(_minusculas(separado["texto"])):
        return None
    return separado["cantidad"]


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


def _es_introduccion(linea: str) -> bool:
    """La línea que presenta el pedido y no pide nada: «Buenas! Para el salón necesito:» (RF-65).

    Sin números, y que termine en dos puntos o abra con un saludo. No depende del catálogo: una palabra suelta
    como «salón» o «paso» puede estar en algún producto y haría pasar la línea por un renglón.
    """
    if any(caracter.isdigit() for caracter in linea):
        return False
    return linea.rstrip().endswith(":") or empieza_con_saludo(linea)


def numerar_renglones(texto: str, productos: list[Product], desde: int = 1) -> list[dict[str, Any]]:
    """Parte un pedido en renglones numerados y le pone a cada uno la marca de su encabezado (RF-65).

    No interpreta productos: eso es del LLM. Saca del medio lo que seguro no es un renglón de pedido, que son los
    encabezados de marca, los saludos, la línea que presenta el pedido («Buenas! Para el salón necesito:») y las
    líneas que no nombran nada del catálogo ni traen un número. Una línea
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
        if _es_introduccion(linea):
            # También puede ser una marca que el catálogo no tiene («Kerastase:»): corta la marca anterior
            marca = None
            continue
        if not (tiene_numero or del_catalogo):
            marca = None
            continue
        renglones.append({"n": desde + len(renglones), "texto": linea, "marca": marca, "estado": "sin_resolver"})
    return renglones


def _minusculas(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", texto.lower())
    return " ".join("".join(c for c in sin_tildes if unicodedata.category(c) != "Mn").split())


def separar_renglon(texto: str) -> dict[str, Any] | None:
    """Separa cantidad y tono de un renglón cuando la forma no admite dudas; None si no (RF-86, CA-149).

    `2 mascara argan x 1k` es cantidad 2; `tinte 8.1/2` es tono 8.1 y cantidad 2. Un número adelante seguido de una
    unidad («500 ml shampoo») es una medida, no una cantidad. La viñeta o el número de orden de una lista no
    cuentan. Lo que no tiene una de estas formas queda para el LLM.
    """
    linea = _MARCA_DE_LISTA.sub("", _minusculas(texto), count=1)
    if _DEPENDE_DE_OTRA_COSA.search(linea):
        return None
    if tono := _TONO_Y_CANTIDAD.match(linea):
        resto = tono["resto"].strip()
        if any(caracter.isdigit() for caracter in resto.split()[0]):
            return None  # también trae una cantidad adelante: se lee de dos formas
        return {"texto": resto, "cantidad": int(tono["cantidad"]), "tono": tono["tono"].replace(",", ".")}
    adelante = _CANTIDAD_ADELANTE.match(linea)
    if adelante is None:
        return None
    unidad = adelante["unidad"]
    if unidad and busqueda_service.UNIDADES.get(unidad, ("", 0))[0] != "unid":
        if unidad in busqueda_service.UNIDADES:
            return None  # «500 ml»: es una medida
        resto = f"{unidad} {adelante['resto']}"  # la primera palabra del producto, no una unidad
    else:
        resto = adelante["resto"].removeprefix("de ").strip()
    cantidad = int(adelante["cantidad"])
    if not 0 < cantidad <= MAX_CANTIDAD_PRECARGA or not any(c.isalpha() for c in resto) or "/" in resto:
        return None
    return {"texto": resto, "cantidad": cantidad, "tono": None}
