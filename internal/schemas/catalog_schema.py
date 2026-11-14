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
    # Algún término no coincidió exacto (tipeo, sonido, otra medida): es un candidato a confirmar con el cliente
    aproximado: bool = False


class SearchResult(BaseModel):
    """Resultado de `search_products` (spec `busqueda-tolerante.md` §4)."""

    matches: list[ProductMatch] = Field(default_factory=list)  # coincidencias confiables
    sugerencias: list[ProductMatch] = Field(default_factory=list)  # solo si no hay matches
    corregido: dict[str, str] = Field(default_factory=dict)  # palabra del cliente → cómo se interpretó
    total: int = 0  # cuántos productos coinciden igual de bien, aunque `matches` muestre menos (RF-69)
    difieren_en: str | None = None  # `marca`, `medida` o `variante`: qué preguntar cuando hay más de uno


class StockCheck(BaseModel):
    sku: str
    requested: int = Field(gt=0)
    available: int = Field(ge=0)

    @computed_field
    @property
    def is_enough(self) -> bool:
        return self.available >= self.requested
