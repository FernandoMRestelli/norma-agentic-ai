"""Tools de catálogo que el agente puede invocar (solo lectura sobre Google Sheet 1)."""

import unicodedata
from collections import Counter
from typing import Any

from internal.schemas.catalog_schema import Product, ProductMatch, SearchResult
from internal.services import busqueda_service, inventory_service


def buscar_productos(query: str, limit: int = 5) -> SearchResult:
    """Busca productos tolerando errores, abreviaturas y medidas escritas de otra forma (RF-07, RF-36 a RF-39).

    El índice guarda el catálogo normalizado; el stock sale fresco de `inventory_service` en cada búsqueda.
    """
    return busqueda_service.buscar(query, inventory_service.get_catalog(), limit)


def search_products(query: str, limit: int = 5) -> list[ProductMatch]:
    """Solo las coincidencias confiables de `buscar_productos`."""
    return buscar_productos(query, limit).matches


def get_product(sku: str) -> Product | None:
    """Devuelve el producto con ese SKU, o None si no existe."""
    return inventory_service.get_product(sku)


MAX_CANDIDATOS = 3  # por renglón: alcanza para preguntar y mantiene chico el resultado de un pedido largo
# Un solo producto con este puntaje o más se toma como seguro aunque una palabra venga mal escrita («queratina»):
# en una lista de 30 renglones no se pregunta «¿es este?» por cada error de tipeo. Por debajo, sí (BR-09)
SCORE_SEGURO = 0.9


def _candidato(match: ProductMatch) -> dict[str, Any]:
    """Lo mínimo para proponer o preguntar. El número de stock no sale: es interno."""
    return {"sku": match.sku, "name": match.name, "price": str(match.price), "hay_stock": match.stock > 0}


def _renglon(n: int, texto: str, marca: str | None, cantidad: int, productos: list[Product]) -> dict[str, Any]:
    # La marca del encabezado entra a la búsqueda salvo que el renglón ya la nombre
    de_la_marca = {p.canon for p in busqueda_service.normalizar(marca or "").palabras}
    del_texto = {p.canon for p in busqueda_service.normalizar(texto).palabras}
    consulta = texto if de_la_marca <= del_texto else f"{texto} {marca}"
    resultado = busqueda_service.buscar(consulta, productos, MAX_CANDIDATOS)
    encontrados = resultado.matches or resultado.sugerencias
    salida: dict[str, Any] = {"n": n, "total": resultado.total, "candidatos": [_candidato(m) for m in encontrados]}
    if not encontrados:
        return {**salida, "estado": "no_encontrado"}
    primero = resultado.matches[0] if resultado.matches else None
    if primero is None or (primero.aproximado and primero.score < SCORE_SEGURO):
        return {**salida, "estado": "aproximado"}
    if resultado.total > 1:
        return {**salida, "estado": "varias", "difieren_en": resultado.difieren_en}
    unico = resultado.matches[0]
    if unico.stock < cantidad:
        # `requested` y `available` como en `stock_insuficiente`: es el único caso en que el número llega al LLM
        falta = {"sku": unico.sku, "requested": cantidad, "available": unico.stock}
        return {**salida, "estado": "sin_stock", "falta": falta}
    return {**salida, "estado": "unico"}


def buscar_renglones(renglones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Busca un lote de renglones de un pedido y devuelve un resultado compacto por cada uno (RF-66).

    Cada renglón trae `n`, `texto`, `cantidad` y, si tiene, la `marca` de su encabezado. El tono de una coloración
    no entra a la búsqueda (RF-69): el catálogo no lo distingue, queda en la nota de la línea.
    Estados: `unico` (un producto y alcanza el stock), `varias`, `aproximado`, `sin_stock` y `no_encontrado`.
    """
    productos = inventory_service.get_catalog()
    return [_renglon(r["n"], r["texto"], r.get("marca"), r.get("cantidad", 1), productos) for r in renglones]

def _normalizar(texto: str | None) -> str:
    if not texto:
        return ""
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower().strip()) if unicodedata.category(c) != "Mn")


def explorar_catalogo(
    brand: str | None = None,
    category: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Explora marcas, categorías y productos cuando el cliente consulta opciones abiertas (RF-35)."""
    products = inventory_service.get_catalog()
    b_norm = _normalizar(brand) if brand else None
    c_norm = _normalizar(category) if category else None

    # Caso 1: Solo categoría (ej. ¿qué marcas de shampoo tenés?)
    if c_norm and not b_norm:
        matched = [p for p in products if c_norm in _normalizar(p.category or "")]
        marcas = sorted({p.brand for p in matched if p.brand})
        cat_nombre = matched[0].category if matched else category
        return {
            "tipo_consulta": "marcas_por_categoria",
            "categoria": cat_nombre,
            "total_marcas": len(marcas),
            "marcas": marcas,
            "total_productos": len(matched),
        }

    # Caso 2: Solo marca (ej. ¿qué tenés de Fidelité?)
    if b_norm and not c_norm:
        matched = [p for p in products if b_norm in _normalizar(p.brand or "")]
        if not matched:
            return {
                "tipo_consulta": "marca_no_encontrada",
                "marca": brand,
                "total_productos": 0,
                "mensaje": f"No se encontraron productos de la marca '{brand}'.",
            }
        cats = Counter(p.category or "Otros" for p in matched)
        marca_real = matched[0].brand or brand
        return {
            "tipo_consulta": "categorias_por_marca",
            "marca": marca_real,
            "total_productos": len(matched),
            "categorias": dict(cats.most_common()),
        }

    # Caso 3: Marca y categoría (ej. mostrame shampoos de Fidelité)
    if b_norm and c_norm:
        matched = [
            p for p in products
            if b_norm in _normalizar(p.brand or "") and c_norm in _normalizar(p.category or "")
        ]
        return {
            "tipo_consulta": "productos_por_marca_y_categoria",
            "marca": brand,
            "categoria": category,
            "total_productos": len(matched),
            "muestra_productos": [
                {
                    "sku": p.sku,
                    "nombre": p.name,
                    "presentacion": p.presentation,
                    "precio": str(p.price) if p.price is not None else None,
                    "hay_stock": p.stock > 0,
                }
                for p in matched[:limit]
            ],
        }

    # Caso 4: General (sin filtros)
    cats = Counter(p.category or "Otros" for p in products)
    return {
        "tipo_consulta": "categorias_generales",
        "total_productos": len(products),
        "categorias": dict(cats.most_common()),
    }

