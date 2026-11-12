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

