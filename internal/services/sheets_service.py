"""Acceso a Google Sheets: Sheet 1 (catálogo y stock, lectura) y Sheet 2 (órdenes, escritura).

Es la única capa que conoce la fuente del catálogo; al migrar a la API del sistema de gestión
real se reemplaza este servicio sin tocar agente ni tools.

PROVISORIO: el primer corte del agente lee `data_example/catalogo.csv`, que tiene las columnas de la Sheet 1.
"""

import csv
from pathlib import Path

from internal.schemas.catalog_schema import Product
from internal.schemas.order_schema import Order

CATALOG_CSV = Path(__file__).resolve().parent.parent.parent / "data_example" / "catalogo.csv"


def get_catalog() -> list[Product]:
    with open(CATALOG_CSV, encoding="utf-8", newline="") as file:
        return [
            Product(
                sku=row["sku"],
                name=row["nombre"],
                brand=row["marca"] or None,
                presentation=row["presentacion"] or None,
                price=row["precio"],
                stock=row["stock"],
                min_stock=row["stock_minimo"] or 0,
            )
            for row in csv.DictReader(file)
        ]


def append_order(order: Order) -> None:
    """Agrega una fila por pedido en la planilla de seguimiento."""
    raise NotImplementedError
