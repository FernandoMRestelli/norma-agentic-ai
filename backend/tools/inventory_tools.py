"""Tools de stock que el agente puede invocar (solo lectura)."""

from backend.schemas.catalog_schema import StockCheck


def check_stock(sku: str, quantity: int) -> StockCheck:
    """Indica si hay stock suficiente para la cantidad pedida."""
    # TODO: leer stock desde catalog_tools.get_product / sheets_service
    raise NotImplementedError
