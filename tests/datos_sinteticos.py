"""Catálogos, mensajes y borradores sintéticos para los tests.

Con semilla fija: un test que falla vuelve a fallar igual, en local y en el CI.
"""

import random
from decimal import Decimal

from backend.schemas.catalog_schema import Product
from backend.schemas.order_schema import Channel, IncomingMessage, MessageType, OrderDraft, OrderItem

SEMILLA = 20261029

_PRODUCTOS = ["Shampoo", "Acondicionador", "Jabón líquido", "Detergente", "Lavandina", "Suavizante", "Desodorante"]
_MARCAS = ["Pantene", "Dove", "Ala", "Magistral", "Ayudín", "Comfort", "Rexona"]
_PRESENTACIONES = ["250 ml", "400 ml", "500 ml", "750 ml", "1 l", "3 l"]


def catalogo(cantidad: int = 20, semilla: int = SEMILLA) -> list[Product]:
    """Catálogo con SKU únicos, precios con centavos y stock entre 0 y 50."""
    rng = random.Random(semilla)
    productos = []
    for i in range(cantidad):
        nombre = rng.choice(_PRODUCTOS)
        productos.append(
            Product(
                sku=f"{nombre[:2].upper()}-{i:04d}",
                name=nombre,
                brand=rng.choice(_MARCAS),
                presentation=rng.choice(_PRESENTACIONES),
                price=Decimal(rng.randint(500, 25000)) / 100,
                stock=rng.randint(0, 50),
            )
        )
    return productos


def sin_stock(productos: list[Product], sku: str) -> list[Product]:
    """Escenario "se agotó": el mismo catálogo con un producto en stock 0."""
    return [p.model_copy(update={"stock": 0}) if p.sku == sku else p for p in productos]


def borrador(
    productos: list[Product],
    items: int = 3,
    canal: Channel = Channel.WEB,
    semilla: int = SEMILLA,
) -> OrderDraft:
    """Borrador con productos distintos del catálogo y cantidades entre 1 y 10."""
    rng = random.Random(semilla)
    elegidos = rng.sample(productos, items)
    return OrderDraft(
        draft_id=f"d-{semilla}",
        customer_id="c-sintetico",
        channel=canal,
        items=[
            OrderItem(
                sku=p.sku,
                product_name=p.name,
                presentation=p.presentation,
                quantity=rng.randint(1, 10),
                unit_price=p.price,
            )
            for p in elegidos
        ],
    )


def mensaje(tipo: MessageType, canal: Channel = Channel.WEB) -> IncomingMessage:
    """Mensaje válido del tipo pedido: texto con contenido, o audio/imagen con su URL."""
    if tipo == MessageType.TEXT:
        return IncomingMessage(customer_id="c-sintetico", channel=canal, type=tipo, text="5 shampoo Pantene 400")
    return IncomingMessage(customer_id="c-sintetico", channel=canal, type=tipo, media_url=f"https://ejemplo.test/{tipo}")
