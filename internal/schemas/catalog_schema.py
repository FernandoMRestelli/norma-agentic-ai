"""Contrato de datos del catálogo: lo comparten las tools y sheets_service."""

from decimal import Decimal

from pydantic import BaseModel, Field, computed_field


class Product(BaseModel):
    sku: str
    name: str
    brand: str | None = None
    presentation: str | None = None
    category: str | None = None
    price: Decimal = Field(ge=0)
    stock: int = Field(ge=0)
    min_stock: int = Field(default=0, ge=0)  # debajo de este valor el dashboard marca stock bajo

    @computed_field
    @property
    def is_low_stock(self) -> bool:
        return self.stock < self.min_stock


class ProductMatch(Product):
    """Resultado de una búsqueda: 1 si todas las palabras coinciden exactamente."""

    score: float = Field(ge=0, le=1)


class StockCheck(BaseModel):
    sku: str
    requested: int = Field(gt=0)
    available: int = Field(ge=0)

    @computed_field
    @property
    def is_enough(self) -> bool:
        return self.available >= self.requested
