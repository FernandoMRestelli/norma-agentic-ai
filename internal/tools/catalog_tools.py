"""Tools de catálogo que el agente puede invocar (solo lectura sobre Google Sheet 1)."""

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from internal.schemas.catalog_schema import Product, ProductMatch
from internal.services import inventory_service

MIN_SCORE = 0.6
MIN_WORD_SCORE = 0.75  # debajo de esto, dos palabras son distintas: «coca» no es «accion»
STOPWORDS = {"de", "del", "el", "la", "los", "las", "un", "una", "unos", "unas", "y", "con", "para", "por", "favor"}


def _palabras(texto: str) -> list[str]:
    """Separa en palabras sin tildes. «400ml» queda como «400 ml», y «6,1» como «6.1»."""
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")
    decimales = re.sub(r"(\d),(\d)", r"\1.\2", sin_tildes)
    separado = re.sub(r"(\d)([a-z])", r"\1 \2", decimales)
    return re.findall(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", separado)


def _score(palabra: str, partes: list[str]) -> float:
    """Qué tan bien aparece una palabra de la búsqueda en el producto, tolerando plurales y errores de tipeo."""
    if any(c.isdigit() for c in palabra):
        # Los números no se aproximan: 340 no es 400, ni 6.1 es 7.1. «6» sí abarca los tonos 6.0 y 6.1
        return float(any(parte == palabra or parte.startswith(f"{palabra}.") for parte in partes))
    mejor = max((SequenceMatcher(None, palabra, parte).ratio() for parte in partes), default=0.0)
    return mejor if mejor >= MIN_WORD_SCORE else 0.0


def search_products(query: str, limit: int = 5) -> list[ProductMatch]:
    """Busca productos por nombre, marca o presentación (búsqueda aproximada, RF-07)."""
    todas = _palabras(query)
    palabras = [palabra for palabra in todas if palabra not in STOPWORDS] or todas
    if not palabras:
        return []
    puntuados = []
    for product in inventory_service.get_catalog():
        partes = _palabras(f"{product.sku} {product.name} {product.brand or ''} {product.presentation or ''}")
        # Cada palabra de la búsqueda tiene que aparecer: «pantene 400» no trae un Pantene de 200 ml adelante
        score = sum(_score(palabra, partes) for palabra in palabras) / len(palabras)
        if score >= MIN_SCORE:
            puntuados.append(ProductMatch(**product.model_dump(exclude={"is_low_stock"}), score=round(score, 2)))
    puntuados.sort(key=lambda match: match.score, reverse=True)
    return puntuados[:limit]


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
    from collections import Counter
    products = inventory_service.get_catalog()
    b_norm = _normalizar(brand) if brand else None
    c_norm = _normalizar(category) if category else None

    # Caso 1: Solo categoría (ej. ¿qué marcas de shampoo tenés?)
    if c_norm and not b_norm:
        matched = [p for p in products if c_norm in _normalizar(p.category or "")]
        marcas = sorted(list({p.brand for p in matched if p.brand}))
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
                    "precio": float(p.price),
                    "stock": p.stock,
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

