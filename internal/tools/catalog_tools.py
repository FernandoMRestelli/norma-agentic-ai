"""Tools de catálogo que el agente puede invocar (solo lectura sobre Google Sheet 1)."""

import re
import unicodedata
from difflib import SequenceMatcher

from internal.schemas.catalog_schema import Product, ProductMatch
from internal.services import sheets_service

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
    for product in sheets_service.get_catalog():
        partes = _palabras(f"{product.sku} {product.name} {product.brand or ''} {product.presentation or ''}")
        # Cada palabra de la búsqueda tiene que aparecer: «pantene 400» no trae un Pantene de 200 ml adelante
        score = sum(_score(palabra, partes) for palabra in palabras) / len(palabras)
        if score >= MIN_SCORE:
            puntuados.append(ProductMatch(**product.model_dump(exclude={"is_low_stock"}), score=round(score, 2)))
    puntuados.sort(key=lambda match: match.score, reverse=True)
    return puntuados[:limit]


def get_product(sku: str) -> Product | None:
    """Devuelve el producto con ese SKU, o None si no existe."""
    return next((product for product in sheets_service.get_catalog() if product.sku == sku), None)
