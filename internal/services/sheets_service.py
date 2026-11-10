"""Acceso a Google Sheets: Sheet 1 (catálogo y stock, lectura) y Sheet 2 (órdenes, escritura).

Es la única capa que conoce la fuente del catálogo y el destino de las órdenes;
al migrar a la API del sistema de gestión real o Google Sheets se reemplaza este servicio sin tocar agente ni tools.

El catálogo se lee del archivo configurado en `Settings.catalog_file` (por defecto `data_example/catalogo.csv`),
con fallback a este último si el archivo configurado no existe.
Las órdenes se vuelcan localmente en `Settings.orders_file` (por defecto `data/pedidos.csv`).
"""

import csv
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from internal.config import get_settings
from internal.schemas.catalog_schema import Product
from internal.schemas.order_schema import Channel, Order, OrderItem, OrderStatus

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


def get_orders() -> list[Order]:
    """Lee todas las órdenes del archivo local de pedidos (Sheet 2) si existe."""
    path = get_orders_path()
    if not path.is_file():
        return []
    try:
        catalog_map = {p.sku: p for p in get_catalog()}
    except Exception:
        catalog_map = {}
    orders: list[Order] = []
    with open(path, mode="r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row or not row.get("id_orden"):
                continue
            order_id = row["id_orden"]
            fecha_str = row.get("fecha_hora", "")
            try:
                created_at = datetime.fromisoformat(fecha_str)
            except Exception:
                created_at = datetime.now(UTC)
            customer_id = row.get("cliente", "")
            canal_str = row.get("canal", "web")
            try:
                channel = Channel(canal_str)
            except Exception:
                channel = Channel.WEB

            productos_str = row.get("productos", "")
            items: list[OrderItem] = []
            for part in productos_str.split(";"):
                part = part.strip()
                if not part:
                    continue
                match = re.match(r"^(?P<qty>\d+)\s*x\s*(?P<name>.+?)(?:\s*\((?P<sku>[A-Z0-9-]+)\))?$", part)
                if match:
                    qty = int(match.group("qty"))
                    sku = match.group("sku") or ""
                    name = match.group("name").strip()
                    prod = catalog_map.get(sku)
                    unit_price = prod.price if prod else Decimal("0.00")
                    presentation = prod.presentation if prod else None
                    items.append(
                        OrderItem(
                            sku=sku or "SKU-DESC",
                            product_name=name,
                            presentation=presentation,
                            quantity=qty,
                            unit_price=unit_price,
                        )
                    )
            if not items:
                items.append(
                    OrderItem(
                        sku="DESCONOCIDO",
                        product_name="Producto sin detalle",
                        quantity=1,
                        unit_price=Decimal("0.00"),
                    )
                )

            estado_str = row.get("estado", OrderStatus.RECEIVED.value)
            try:
                status = OrderStatus(estado_str)
            except Exception:
                status = OrderStatus.RECEIVED

            orders.append(
                Order(
                    order_id=order_id,
                    draft_id=f"draft-{order_id}",
                    created_at=created_at,
                    customer_id=customer_id,
                    channel=channel,
                    items=items,
                    notes=row.get("notas") or None,
                    status=status,
                    sheets_synced=True,
                )
            )
    return orders


def update_order_status_in_sheets(order_id: str, new_status: str) -> bool:
    """Actualiza el estado de una orden en el archivo local de órdenes si existe (Issue #61)."""
    path = get_orders_path()
    if not path.is_file():
        return False
    rows: list[list[str]] = []
    updated = False
    with open(path, mode="r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header:
            rows.append(header)
            status_idx = header.index("estado") if "estado" in header else 6
            for row in reader:
                if row and row[0] == order_id:
                    if len(row) > status_idx:
                        row[status_idx] = new_status
                    else:
                        row.append(new_status)
                    updated = True
                rows.append(row)
    if updated:
        with open(path, mode="w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(rows)
    return updated


def deduct_catalog_stock(items: list[dict[str, Any]] | list[OrderItem]) -> None:
    """Resta las cantidades de los productos del stock del catálogo local (Issue #61)."""
    path = get_catalog_path()
    if not path.is_file():
        return
    to_deduct: dict[str, int] = {}
    for item in items:
        if isinstance(item, dict):
            sku = item.get("sku")
            qty = item.get("quantity", 0)
        else:
            sku = item.sku
            qty = item.quantity
        if sku and isinstance(qty, int) and qty > 0:
            to_deduct[sku] = to_deduct.get(sku, 0) + qty

    if not to_deduct:
        return

    rows: list[dict[str, str]] = []
    fieldnames: list[str] = []
    with open(path, mode="r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        for row in reader:
            sku = row.get("sku")
            if sku in to_deduct and "stock" in row:
                try:
                    curr_stock = int(row["stock"])
                    new_stock = max(0, curr_stock - to_deduct[sku])
                    row["stock"] = str(new_stock)
                except ValueError:
                    pass
            rows.append(row)

    with open(path, mode="w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
