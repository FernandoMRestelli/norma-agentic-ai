"""Acceso a Google Sheets: Sheet 1 (catálogo y stock, lectura) y Sheet 2 (órdenes, escritura).

Es la única capa que conoce la fuente del catálogo; al migrar a la API del sistema de gestión
real se reemplaza este servicio sin tocar agente ni tools.

El catálogo se lee del archivo configurado en `Settings.catalog_file` (por defecto `data_example/catalogo.csv`),
con fallback a este último si el archivo configurado no existe.
"""

import csv
from pathlib import Path

from internal.config import get_settings
from internal.schemas.catalog_schema import Product
from internal.schemas.order_schema import Order

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
FALLBACK_CATALOG_CSV = ROOT_DIR / "data_example" / "catalogo.csv"


def get_catalog_path() -> Path:
    """Devuelve la ruta al catálogo configurado, o el fallback si no existe."""
    configured = Path(get_settings().catalog_file)
    if not configured.is_absolute():
        configured = ROOT_DIR / configured
    if configured.is_file():
        return configured
    if FALLBACK_CATALOG_CSV.is_file():
        return FALLBACK_CATALOG_CSV
    raise FileNotFoundError(f"No se encontró el catálogo en {configured} ni en {FALLBACK_CATALOG_CSV}")


def get_catalog() -> list[Product]:
    path = get_catalog_path()
    with open(path, encoding="utf-8", newline="") as file:
        return [
            Product(
                sku=row["sku"],
                name=row["nombre"],
                brand=row["marca"] or None,
                presentation=row["presentacion"] or None,
                category=row.get("categoria") or None,
                price=row["precio"],
                stock=row["stock"] or 0,
                min_stock=row["stock_minimo"] or 0,
            )
            for row in csv.DictReader(file)
        ]


def append_order(order: Order) -> None:
    """Agrega una fila por pedido en la planilla de seguimiento."""
    raise NotImplementedError
