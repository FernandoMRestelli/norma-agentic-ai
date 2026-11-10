"""Acceso a Google Sheets: Sheet 1 (catálogo y stock, lectura) y Sheet 2 (órdenes, escritura).

Es la única capa que conoce la fuente del catálogo y el destino de las órdenes;
al migrar a la API del sistema de gestión real o Google Sheets se reemplaza este servicio sin tocar agente ni tools.

El catálogo se lee del archivo configurado en `Settings.catalog_file` (por defecto `data_example/catalogo.csv`),
con fallback a este último si el archivo configurado no existe.
Las órdenes se vuelcan localmente en `Settings.orders_file` (por defecto `data/pedidos.csv`).
"""

import csv
from datetime import UTC
from pathlib import Path
from zoneinfo import ZoneInfo

from internal.config import get_settings
from internal.schemas.catalog_schema import Product
from internal.schemas.order_schema import Order

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
FALLBACK_CATALOG_CSV = ROOT_DIR / "data_example" / "catalogo.csv"

ORDER_COLUMNS = ["id_orden", "fecha_hora", "cliente", "canal", "productos", "notas", "estado"]
AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


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


def get_orders_path() -> Path:
    """Devuelve la ruta al archivo de órdenes configurado."""
    configured = Path(get_settings().orders_file)
    if not configured.is_absolute():
        configured = ROOT_DIR / configured
    return configured


def format_order_row(order: Order) -> list[str]:
    """Formatea una orden según las columnas del Sheet 2 (pedidos.csv)."""
    created_at = order.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    fecha_hora = created_at.astimezone(AR_TZ).isoformat()

    items_str = "; ".join(
        f"{item.quantity} x {' '.join(filter(None, (item.product_name, item.presentation)))} ({item.sku})"
        for item in order.items
    )

    canal = order.channel.value if hasattr(order.channel, "value") else str(order.channel)
    estado = order.status.value if hasattr(order.status, "value") else str(order.status)

    return [
        order.order_id,
        fecha_hora,
        order.customer_id,
        canal,
        items_str,
        order.notes or "",
        estado,
    ]


def append_order(order: Order) -> None:
    """Agrega una fila por pedido en la planilla o archivo local de seguimiento (RF-19, RNF-04).

    Es idempotente: si la orden ya existe en el archivo, no la duplica.
    """
    path = get_orders_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    file_exists = path.is_file() and path.stat().st_size > 0
    if file_exists:
        with open(path, mode="r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            _header = next(reader, None)
            for row in reader:
                if row and row[0] == order.order_id:
                    return

    with open(path, mode="a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(ORDER_COLUMNS)
        writer.writerow(format_order_row(order))
