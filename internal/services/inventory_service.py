"""Servicio de inventario: calcula y gestiona el stock disponible neto (RF-14, CA-16).

El stock base proviene de Google Sheet 1 (o catalogo.csv provisorio a través de sheets_service).
El stock disponible resta las unidades comprometidas en órdenes activas de la base SQLite
(estados distintos a 'cancelado'), impidiendo la sobreventa (overselling) entre clientes concurrentes.
"""

from typing import TYPE_CHECKING

from sqlalchemy import select

from internal.database.connection import SessionLocal
from internal.database.models import OrderRecord
from internal.schemas.catalog_schema import Product
from internal.schemas.order_schema import OrderStatus
from internal.services import sheets_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def get_committed_stock(session: "Session | None" = None) -> dict[str, int]:
    """Calcula las unidades comprometidas por SKU en órdenes activas (no canceladas).

    Una orden en estado 'recibido', 'preparado' o 'retirado' reserva sus unidades.
    Solo las órdenes 'canceladas' liberan su stock.
    """

    def _calculate(s: "Session") -> dict[str, int]:
        records = s.scalars(
            select(OrderRecord).where(OrderRecord.status != OrderStatus.CANCELLED.value)
        ).all()
        committed: dict[str, int] = {}
        for record in records:
            for item in record.items:
                sku = item.get("sku")
                qty = item.get("quantity", 0)
                if sku and isinstance(qty, int) and qty > 0:
                    committed[sku] = committed.get(sku, 0) + qty
        return committed

    if session is not None:
        return _calculate(session)
    with SessionLocal() as s:
        return _calculate(s)


def get_available_stock(sku: str, session: "Session | None" = None) -> int:
    """Devuelve el stock disponible neto para un SKU (stock_base - comprometido >= 0).

    Si el SKU no existe en el catálogo, devuelve 0.
    """
    prod = get_product(sku, session=session)
    return prod.stock if prod else 0


def get_catalog(session: "Session | None" = None, fresh: bool = False) -> list[Product]:
    """Devuelve el catálogo de productos con su stock disponible actualizado.

    Llama a sheets_service.get_catalog() para obtener los productos base y resta
    las unidades comprometidas en órdenes activas. Con `fresh=True` (la confirmación) lee Sheet 1
    aunque haya una copia reciente en caché (RNF-13, RF-51).
    """
    with sheets_service.lectura_fresca(fresh):
        base_products = sheets_service.get_catalog()
    committed = get_committed_stock(session=session)
    if not committed:
        return base_products

    return [
        prod.model_copy(update={"stock": max(0, prod.stock - committed.get(prod.sku, 0))})
        for prod in base_products
    ]


def get_product(sku: str, session: "Session | None" = None) -> Product | None:
    """Devuelve el producto con ese SKU y su stock disponible neto, o None si no existe."""
    return next((prod for prod in get_catalog(session=session) if prod.sku == sku), None)
