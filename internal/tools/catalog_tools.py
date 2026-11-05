"""Tools de catálogo que el agente puede invocar (solo lectura sobre Google Sheet 1)."""

import unicodedata
from difflib import SequenceMatcher

from internal.schemas.catalog_schema import Product
from internal.services import sheets_service

MIN_SCORE = 0.6


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")


def _score(palabra: str, texto: str) -> float:
    """Qué tan bien aparece una palabra de la búsqueda en el producto, tolerando plurales y errores de tipeo."""
    return max((SequenceMatcher(None, palabra, parte).ratio() for parte in texto.split()), default=0.0)


def search_products(query: str, limit: int = 5) -> list[Product]:
    """Busca productos por nombre, marca o presentación (búsqueda aproximada, RF-07)."""
    palabras = _normalizar(query).split()
    if not palabras:
        return []
    puntuados = []
    for product in sheets_service.get_catalog():
        texto = _normalizar(f"{product.sku} {product.name} {product.brand or ''} {product.presentation or ''}")
        # Cada palabra de la búsqueda tiene que aparecer: «pantene 400» no trae un Pantene de 200 ml adelante
        score = sum(_score(palabra, texto) for palabra in palabras) / len(palabras)
        if score >= MIN_SCORE:
            puntuados.append((score, product))
    puntuados.sort(key=lambda par: par[0], reverse=True)
    return [product for _, product in puntuados[:limit]]


def get_product(sku: str) -> Product | None:
    """Devuelve el producto con ese SKU, o None si no existe."""
    return next((product for product in sheets_service.get_catalog() if product.sku == sku), None)
