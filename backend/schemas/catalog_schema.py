"""Contrato de datos del catálogo: lo comparten las tools y sheets_service."""

from decimal import Decimal

from pydantic import BaseModel, Field, computed_field


class Product(BaseModel):
    sku: str
    name: str
    brand: str | None = None
    presentation: str | None = None
    price: Decimal = Field(ge=0)
    stock: int
    min_stock: int = Field(default=0, ge=0)  # debajo de este valor el dashboard marca stock bajo

    @computed_field
    @property
    def is_low_stock(self) -> bool:
        return self.stock < self.min_stock


class StockCheck(BaseModel):
    sku: str
    requested: int
    available: int

    @computed_field
    @property
    def is_enough(self) -> bool:
        return self.available >= self.requested
