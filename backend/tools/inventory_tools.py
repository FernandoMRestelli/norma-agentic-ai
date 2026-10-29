"""Tools de stock que el agente puede invocar (solo lectura)."""

from pydantic import BaseModel


class StockCheck(BaseModel):
    sku: str
    requested: int
    available: int

    @property
    def is_enough(self) -> bool:
        return self.available >= self.requested


def check_stock(sku: str, quantity: int) -> StockCheck:
    """Indica si hay stock suficiente para la cantidad pedida."""
    # TODO: leer stock desde catalog_tools.get_product / sheets_service
    raise NotImplementedError
