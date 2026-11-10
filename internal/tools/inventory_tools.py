"""Tools de stock que el agente puede invocar (solo lectura)."""

from internal.schemas.agent_schema import ToolFailure
from internal.schemas.catalog_schema import StockCheck
from internal.tools.catalog_tools import get_product


def check_stock(sku: str, quantity: int) -> StockCheck:
    """Indica si hay stock suficiente para la cantidad pedida."""
    product = get_product(sku)
    if product is None:
        raise ToolFailure(
            "sku_inexistente",
            f"No existe el SKU {sku}: buscá de nuevo el producto con search_products, no pruebes SKU de memoria",
            sku=sku,
        )
    return StockCheck(sku=sku, requested=quantity, available=product.stock)
