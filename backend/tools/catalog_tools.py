"""Tools de catálogo que el agente puede invocar (solo lectura sobre Google Sheet 1)."""

from backend.schemas.catalog_schema import Product


def search_products(query: str, limit: int = 5) -> list[Product]:
    """Busca productos por nombre, marca o presentación (búsqueda aproximada)."""
    # TODO: sheets_service.get_catalog() + matching difuso
    raise NotImplementedError


def get_product(sku: str) -> Product | None:
    """Devuelve el producto con ese SKU, o None si no existe."""
    raise NotImplementedError
