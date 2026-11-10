"""Servicio del dashboard operativo: métricas, pedidos, stock y derivaciones (RF-22 a RF-25)."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from internal.database.connection import SessionLocal
from internal.database.models import ConversationRecord, OrderRecord
from internal.schemas.dashboard_schema import (
    DashboardConversationItem,
    DashboardOrderItem,
    DashboardStockItem,
    DashboardSummary,
)
from internal.schemas.order_schema import OrderStatus
from internal.services import inventory_service, sheets_service

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def get_summary() -> DashboardSummary:
    """Calcula los indicadores principales para el dashboard (RF-22, CA-25)."""
    now_ar = datetime.now(AR_TZ)
    today_date = now_ar.date()

    orders = _get_all_orders()

    orders_by_status: dict[str, int] = {
        OrderStatus.RECEIVED.value: 0,
        OrderStatus.PREPARED.value: 0,
        OrderStatus.PICKED_UP.value: 0,
        OrderStatus.DISPATCHED.value: 0,
        OrderStatus.DELIVERED.value: 0,
        OrderStatus.CANCELLED.value: 0,
    }

    orders_today_count = 0
    distinct_skus_today: set[str] = set()

    for o in orders:
        st = o["status"]
        if st in orders_by_status:
            orders_by_status[st] += 1
        else:
            orders_by_status[st] = 1

        created_at: datetime = o["created_at"]
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        order_date = created_at.astimezone(AR_TZ).date()

        if order_date == today_date:
            orders_today_count += 1
            for it in o.get("items", []):
                sku = it.get("sku")
                if sku:
                    distinct_skus_today.add(sku)

    # Catálogo y stock bajo
    catalog = inventory_service.get_catalog()
    low_stock_products = [p for p in catalog if p.is_low_stock]
    low_stock_skus = [p.sku for p in low_stock_products]

    return DashboardSummary(
        orders_today=orders_today_count,
        orders_by_status=orders_by_status,
        distinct_products_today=len(distinct_skus_today),
        low_stock_products_count=len(low_stock_products),
        low_stock_skus=low_stock_skus,
    )


def get_orders(status: str | None = None, limit: int = 50, offset: int = 0) -> list[DashboardOrderItem]:
    """Lista pedidos para la vista de gestión operativa (RF-23)."""
    all_orders = _get_all_orders()
    if status:
        all_orders = [o for o in all_orders if o["status"].lower() == status.lower()]

    # Ordenar por fecha descendente
    all_orders.sort(key=lambda o: o["created_at"], reverse=True)
    page = all_orders[offset : offset + limit]

    result: list[DashboardOrderItem] = []
    for o in page:
        items = o.get("items", [])
        total = sum(
            (Decimal(str(it.get("unit_price", 0))) * it.get("quantity", 0) for it in items),
            Decimal(0),
        )
        items_summary = "; ".join(
            f"{it.get('quantity', 1)} x {it.get('product_name', '')} ({it.get('sku', '')})"
            for it in items
        )
        result.append(
            DashboardOrderItem(
                order_id=o["order_id"],
                created_at=o["created_at"],
                customer_id=o["customer_id"],
                channel=o["channel"],
                status=o["status"],
                notes=o.get("notes"),
                items_summary=items_summary,
                total=total,
                stock_applied=o.get("stock_applied", False),
            )
        )
    return result


def get_stock(low_only: bool = False) -> list[DashboardStockItem]:
    """Lista productos del catálogo con su stock disponible neto y alerta de stock bajo (RF-23)."""
    catalog = inventory_service.get_catalog()
    if low_only:
        catalog = [p for p in catalog if p.is_low_stock]

    return [
        DashboardStockItem(
            sku=p.sku,
            name=p.name,
            brand=p.brand,
            presentation=p.presentation,
            category=p.category,
            price=p.price,
            stock=p.stock,
            min_stock=p.min_stock,
            is_low_stock=p.is_low_stock,
        )
        for p in catalog
    ]


def get_conversations(state: str | None = None) -> list[DashboardConversationItem]:
    """Lista conversaciones, con filtro opcional por estado (ej. 'derivada') (RF-24)."""
    with SessionLocal() as session:
        query = select(ConversationRecord)
        if state:
            query = query.where(ConversationRecord.status == state)
        query = query.order_by(ConversationRecord.updated_at.desc())
        records = session.scalars(query).all()
        return [
            DashboardConversationItem(
                id=r.id,
                customer_id=r.customer_id,
                channel=r.channel,
                status=r.status,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in records
        ]


def _get_all_orders() -> list[dict[str, Any]]:
    """Obtiene órdenes unificadas de la base interna o Sheet 2 según disponibilidad."""
    with SessionLocal() as session:
        records = session.scalars(select(OrderRecord)).all()
        if records:
            return [
                {
                    "order_id": r.order_id,
                    "created_at": r.created_at,
                    "customer_id": r.customer_id,
                    "channel": r.channel,
                    "items": r.items,
                    "notes": r.notes,
                    "status": r.status,
                    "stock_applied": getattr(r, "stock_applied", False),
                }
                for r in records
            ]

    # Si la base aún no tiene registros, lee desde Sheet 2 (ej. data_example/pedidos.csv para CA-25)
    sheet_orders = sheets_service.get_orders()
    return [
        {
            "order_id": o.order_id,
            "created_at": o.created_at,
            "customer_id": o.customer_id,
            "channel": o.channel.value if hasattr(o.channel, "value") else str(o.channel),
            "items": [it.model_dump() for it in o.items],
            "notes": o.notes,
            "status": o.status.value if hasattr(o.status, "value") else str(o.status),
            "stock_applied": getattr(o, "stock_applied", False),
        }
        for o in sheet_orders
    ]
