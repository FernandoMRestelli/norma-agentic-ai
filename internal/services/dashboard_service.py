"""Servicio del dashboard operativo: métricas, pedidos, stock y derivaciones (RF-22 a RF-25)."""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from internal.database.connection import SessionLocal
from internal.database.models import ConversationRecord, OrderRecord
from internal.schemas.dashboard_schema import (
    BreakdownItem,
    DashboardConversationItem,
    DashboardMetrics,
    DashboardOrderItem,
    DashboardOrderLineItem,
    DashboardStockItem,
    DashboardSummary,
    TimeSeriesPoint,
    TopProductItem,
)
from internal.schemas.order_schema import OrderStatus
from internal.services import inventory_service, sheets_service

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
MESES_ES = ("", "Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic")


def get_summary(period: Literal["all", "today", "yesterday", "7d", "30d"] = "all") -> DashboardSummary:
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

    orders_count = 0
    distinct_skus: set[str] = set()

    for o in orders:
        in_selected = _in_period(o["created_at"], period, today_date)

        # Cuando period == "all" (CA-25 por defecto en API), orders_by_status suma todo el histórico
        # y orders_today/distinct_products_today cuentan el día de hoy.
        # Con otro período explícito (ej. today, yesterday, 7d, 30d), todas las tarjetas coinciden con ese período.
        if period == "all" or in_selected:
            st = o["status"]
            if st in orders_by_status:
                orders_by_status[st] += 1
            else:
                orders_by_status[st] = 1

        matches_kpi = _in_period(o["created_at"], "today" if period == "all" else period, today_date)
        if matches_kpi:
            orders_count += 1
            for it in o.get("items", []):
                sku = it.get("sku")
                if sku:
                    distinct_skus.add(sku)

    # Catálogo y stock bajo
    catalog = inventory_service.get_catalog()
    low_stock_products = [p for p in catalog if p.is_low_stock]
    low_stock_skus = [p.sku for p in low_stock_products]

    return DashboardSummary(
        orders_today=orders_count,
        orders_by_status=orders_by_status,
        distinct_products_today=len(distinct_skus),
        low_stock_products_count=len(low_stock_products),
        low_stock_skus=low_stock_skus,
        period=period,
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
        line_items: list[DashboardOrderLineItem] = []
        total = Decimal(0)
        for it in items:
            raw_price = it.get("unit_price")
            unit_price = Decimal(str(raw_price)) if raw_price is not None else None
            qty = int(it.get("quantity", 0))
            subtotal = (unit_price or Decimal(0)) * qty
            total += subtotal
            line_items.append(
                DashboardOrderLineItem(
                    sku=str(it.get("sku", "")),
                    product_name=str(it.get("product_name", "")),
                    presentation=it.get("presentation"),
                    quantity=qty,
                    unit_price=unit_price,
                    subtotal=subtotal,
                )
            )
        items_summary = "; ".join(
            f"{it.quantity} x {it.product_name} ({it.sku})"
            for it in line_items
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
                items=line_items,
                total=total,
                stock_applied=o.get("stock_applied", False),
            )
        )
    return result


def _period_bucket(dt: datetime, granularity: Literal["day", "week", "month", "year"]) -> tuple[str, str]:
    """Devuelve (period_key ordenable, period_label legible) en zona horaria Argentina."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt_ar = dt.astimezone(AR_TZ)
    if granularity == "week":
        iso_year, iso_week, _ = dt_ar.isocalendar()
        monday = dt_ar.date() - timedelta(days=dt_ar.weekday())
        return f"{iso_year}-W{iso_week:02d}", f"Sem {monday.strftime('%d/%m/%y')}"
    if granularity == "month":
        return dt_ar.strftime("%Y-%m"), f"{MESES_ES[dt_ar.month]} {dt_ar.year}"
    if granularity == "year":
        y = dt_ar.strftime("%Y")
        return y, y
    return dt_ar.strftime("%Y-%m-%d"), dt_ar.strftime("%d/%m/%Y")


def _order_revenue(order: dict[str, Any], catalog_prices: dict[str, Decimal]) -> Decimal:
    """Calcula el total de la orden usando unit_price o fallback al precio de catálogo."""
    total = Decimal(0)
    for it in order.get("items", []):
        raw_price = it.get("unit_price")
        sku = str(it.get("sku", ""))
        price = Decimal(str(raw_price)) if raw_price is not None else catalog_prices.get(sku, Decimal(0))
        total += price * int(it.get("quantity", 0))
    return total


def _in_period(dt: datetime, period: Literal["all", "today", "yesterday", "7d", "30d"], today_ar: Any) -> bool:
    if period == "all":
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    order_date = dt.astimezone(AR_TZ).date()
    if period == "today":
        return order_date == today_ar
    if period == "yesterday":
        return order_date == (today_ar - timedelta(days=1))
    days = 7 if period == "7d" else 30
    return order_date >= (today_ar - timedelta(days=days - 1))


def get_metrics(
    granularity: Literal["day", "week", "month", "year"] = "day",
    breakdown_by: Literal["category", "brand"] = "category",
    period: Literal["all", "today", "yesterday", "7d", "30d"] = "all",
    channel: str | None = None,
) -> DashboardMetrics:
    """Calcula KPIs económicos, evolución temporal de ventas y desglose por categoría/marca."""
    catalog = inventory_service.get_catalog()
    catalog_by_sku = {p.sku: p for p in catalog}
    catalog_prices = {p.sku: p.price for p in catalog}
    catalog_valuation = sum((p.price * p.stock for p in catalog), Decimal(0))
    total_catalog_count = len(catalog)
    healthy_stock_count = sum(1 for p in catalog if not p.is_low_stock)
    stock_health_pct = round((healthy_stock_count / total_catalog_count) * 100, 1) if total_catalog_count else 100.0

    today_ar = datetime.now(AR_TZ).date()
    raw_orders = _get_all_orders()
    all_orders = [
        o
        for o in raw_orders
        if _in_period(o["created_at"], period, today_ar)
        and (not channel or o["channel"].lower() == channel.lower())
    ]

    cancelled_orders = [o for o in all_orders if o["status"] == OrderStatus.CANCELLED.value]
    active_orders = [o for o in all_orders if o["status"] != OrderStatus.CANCELLED.value]

    completed_statuses = {OrderStatus.PICKED_UP.value, OrderStatus.DELIVERED.value}
    total_revenue = Decimal(0)
    completed_revenue = Decimal(0)
    in_progress_revenue = Decimal(0)
    completed_orders_count = 0
    in_progress_orders_count = 0
    cancelled_revenue = sum((_order_revenue(o, catalog_prices) for o in cancelled_orders), Decimal(0))

    series_map: dict[str, dict[str, Any]] = {}
    breakdown_map: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"revenue": Decimal(0), "units": 0, "orders": set()}
    )
    channel_map: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"revenue": Decimal(0), "units": 0, "orders": 0}
    )
    products_map: dict[str, dict[str, Any]] = {}

    for o in active_orders:
        rev = _order_revenue(o, catalog_prices)
        total_revenue += rev
        if o["status"] in completed_statuses:
            completed_revenue += rev
            completed_orders_count += 1
        else:
            in_progress_revenue += rev
            in_progress_orders_count += 1

        order_units = sum(int(it.get("quantity", 0)) for it in o.get("items", []))
        ch_name = (o.get("channel") or "web").capitalize()
        channel_map[ch_name]["revenue"] += rev
        channel_map[ch_name]["orders"] += 1
        channel_map[ch_name]["units"] += order_units

        pkey, plabel = _period_bucket(o["created_at"], granularity)
        if pkey not in series_map:
            series_map[pkey] = {"label": plabel, "revenue": Decimal(0), "orders_count": 0, "units_count": 0}
        series_map[pkey]["revenue"] += rev
        series_map[pkey]["orders_count"] += 1
        series_map[pkey]["units_count"] += order_units

        for it in o.get("items", []):
            sku = str(it.get("sku", ""))
            qty = int(it.get("quantity", 0))
            raw_price = it.get("unit_price")
            unit_price = Decimal(str(raw_price)) if raw_price is not None else catalog_prices.get(sku, Decimal(0))
            line_rev = unit_price * qty

            prod = catalog_by_sku.get(sku)
            brand = (prod.brand if prod and prod.brand else None) or "Sin marca"
            category = (prod.category if prod and prod.category else None) or "Sin categoría"
            group_key = brand if breakdown_by == "brand" else category

            breakdown_map[group_key]["revenue"] += line_rev
            breakdown_map[group_key]["units"] += qty
            breakdown_map[group_key]["orders"].add(o["order_id"])

            if sku not in products_map:
                products_map[sku] = {
                    "name": prod.name if prod else str(it.get("product_name", sku)),
                    "brand": brand,
                    "category": category,
                    "units": 0,
                    "revenue": Decimal(0),
                }
            products_map[sku]["units"] += qty
            products_map[sku]["revenue"] += line_rev

    total_orders_count = len(all_orders)
    active_count = len(active_orders)
    cancelled_count = len(cancelled_orders)
    average_ticket = (total_revenue / active_count).quantize(Decimal("0.01")) if active_count else Decimal("0.00")
    cancellation_rate = round((cancelled_count / total_orders_count) * 100, 1) if total_orders_count else 0.0
    completion_rate = round((completed_orders_count / total_orders_count) * 100, 1) if total_orders_count else 0.0

    time_series = [
        TimeSeriesPoint(
            period_key=k,
            period_label=v["label"],
            revenue=v["revenue"],
            orders_count=v["orders_count"],
            units_count=v["units_count"],
        )
        for k, v in sorted(series_map.items(), key=lambda pair: pair[0])
    ]

    breakdown = [
        BreakdownItem(
            name=name,
            revenue=vals["revenue"],
            units=vals["units"],
            orders_count=len(vals["orders"]),
            percentage=round(float(vals["revenue"] / total_revenue * 100), 1) if total_revenue > 0 else 0.0,
        )
        for name, vals in sorted(breakdown_map.items(), key=lambda pair: pair[1]["revenue"], reverse=True)
    ]

    gross_plus_cancelled = total_revenue + cancelled_revenue
    status_breakdown = [
        BreakdownItem(
            name="Concretado",
            revenue=completed_revenue,
            units=completed_orders_count,
            orders_count=completed_orders_count,
            percentage=round(float(completed_revenue / gross_plus_cancelled * 100), 1)
            if gross_plus_cancelled > 0
            else 0.0,
        ),
        BreakdownItem(
            name="En curso",
            revenue=in_progress_revenue,
            units=in_progress_orders_count,
            orders_count=in_progress_orders_count,
            percentage=round(float(in_progress_revenue / gross_plus_cancelled * 100), 1)
            if gross_plus_cancelled > 0
            else 0.0,
        ),
        BreakdownItem(
            name="Cancelado",
            revenue=cancelled_revenue,
            units=cancelled_count,
            orders_count=cancelled_count,
            percentage=round(float(cancelled_revenue / gross_plus_cancelled * 100), 1)
            if gross_plus_cancelled > 0
            else 0.0,
        ),
    ]

    channel_breakdown = [
        BreakdownItem(
            name=name,
            revenue=vals["revenue"],
            units=vals["units"],
            orders_count=vals["orders"],
            percentage=round(float(vals["revenue"] / total_revenue * 100), 1) if total_revenue > 0 else 0.0,
        )
        for name, vals in sorted(channel_map.items(), key=lambda pair: pair[1]["revenue"], reverse=True)
    ]

    top_products = [
        TopProductItem(
            sku=sku,
            name=vals["name"],
            brand=vals["brand"],
            category=vals["category"],
            units=vals["units"],
            revenue=vals["revenue"],
        )
        for sku, vals in sorted(products_map.items(), key=lambda pair: pair[1]["revenue"], reverse=True)[:10]
    ]

    sold_skus = set(products_map.keys())
    no_movement_candidates = [p for p in catalog if p.sku not in sold_skus and p.stock > 0]
    no_movement_candidates.sort(key=lambda p: p.price * p.stock, reverse=True)
    no_movement_products = [
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
        for p in no_movement_candidates[:10]
    ]

    return DashboardMetrics(
        total_revenue=total_revenue,
        completed_revenue=completed_revenue,
        in_progress_revenue=in_progress_revenue,
        cancelled_revenue=cancelled_revenue,
        average_ticket=average_ticket,
        active_orders_count=active_count,
        completed_orders_count=completed_orders_count,
        in_progress_orders_count=in_progress_orders_count,
        cancelled_orders_count=cancelled_count,
        cancellation_rate=cancellation_rate,
        completion_rate=completion_rate,
        catalog_valuation=catalog_valuation,
        stock_health_pct=stock_health_pct,
        healthy_stock_count=healthy_stock_count,
        total_catalog_count=total_catalog_count,
        granularity=granularity,
        breakdown_by=breakdown_by,
        period=period,
        channel=channel,
        time_series=time_series,
        breakdown=breakdown,
        status_breakdown=status_breakdown,
        channel_breakdown=channel_breakdown,
        top_products=top_products,
        no_movement_products=no_movement_products,
    )


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
                    # Las líneas están en order_items desde la migración 1
                    "items": [
                        {
                            "sku": line.sku,
                            "product_name": line.product_name,
                            "presentation": line.presentation,
                            "quantity": line.quantity,
                            "unit_price": line.unit_price,
                            "note": line.note,
                        }
                        for line in r.lines
                    ],
                    "notes": r.notes,
                    "status": r.status,
                    "stock_applied": r.stock_applied,
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
