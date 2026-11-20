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
    comprado_antes: bool = False  # el cliente del turno ya lo pidió en una orden anterior (RF-73)


class Cercano(BaseModel):
    """Un producto que coincide en parte con la búsqueda, y qué partes de la búsqueda no tiene."""

    sku: str
    name: str
    falta: list[str] = Field(default_factory=list)  # palabras o medidas del cliente, como las escribió


class Pistas(BaseModel):
    """Por qué una búsqueda no encontró una coincidencia confiable, para que el agente no busque a ciegas."""

    no_estan: list[str] = Field(default_factory=list)  # palabras que no aparecen en ningún producto
    cercanos: list[Cercano] = Field(default_factory=list)  # los que tienen más partes de la búsqueda
    medidas: list[str] = Field(default_factory=list)  # las que hay de lo buscado, si lo que no coincide es la medida


class SearchResult(BaseModel):
    """Resultado de `search_products` (spec `busqueda-tolerante.md` §4)."""

    matches: list[ProductMatch] = Field(default_factory=list)  # coincidencias confiables
    sugerencias: list[ProductMatch] = Field(default_factory=list)  # solo si no hay matches
    corregido: dict[str, str] = Field(default_factory=dict)  # palabra del cliente → cómo se interpretó
    total: int = 0  # cuántos productos coinciden igual de bien, aunque `matches` muestre menos (RF-69)
    difieren_en: str | None = None  # `marca`, `medida` o `variante`: qué preguntar cuando hay más de uno
    pistas: Pistas | None = None  # sin matches, o con el mejor aproximado: por qué no coincidió todo


class StockCheck(BaseModel):
    sku: str
    requested: int = Field(gt=0)
    available: int = Field(ge=0)

    @computed_field
    @property
    def is_enough(self) -> bool:
        return self.available >= self.requested
