"""Acceso a Google Sheets: Sheet 1 (catálogo y stock, lectura) y Sheet 2 (órdenes, escritura).

Es la única capa que conoce Google Sheets; al migrar a la API del sistema de gestión
real se reemplaza este servicio sin tocar agente ni tools.
"""

from backend.schemas.order_schema import Order
from backend.tools.catalog_tools import Product


def get_catalog() -> list[Product]:
    # TODO: gspread + credenciales de cuenta de servicio (GOOGLE_CREDENTIALS_FILE)
    raise NotImplementedError


def append_order(order: Order) -> None:
    """Agrega una fila por pedido en la planilla de seguimiento."""
    raise NotImplementedError
