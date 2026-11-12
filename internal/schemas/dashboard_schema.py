"""Esquemas para el dashboard operativo (RF-22 a RF-25)."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from internal.schemas.order_schema import OrderStatus


class DashboardSummary(BaseModel):
    orders_today: int = Field(ge=0)
    orders_by_status: dict[str, int]
    distinct_products_today: int = Field(ge=0)
    low_stock_products_count: int = Field(ge=0)
    low_stock_skus: list[str] = Field(default_factory=list)
    period: str = "all"


class DashboardOrderLineItem(BaseModel):
    sku: str
    product_name: str
    presentation: str | None = None
    quantity: int
    unit_price: Decimal | None = None
    subtotal: Decimal


class DashboardOrderItem(BaseModel):
    order_id: str
    created_at: datetime
    customer_id: str
    channel: str
    status: str
    notes: str | None = None
    items_summary: str
    items: list[DashboardOrderLineItem] = Field(default_factory=list)
    total: Decimal
    stock_applied: bool = False
    read_only: bool = False  # solo está en la copia de Sheet 2: no se puede cambiar de estado (RF-61)


class DashboardStockItem(BaseModel):
    sku: str
    name: str
    brand: str | None = None
    presentation: str | None = None
    category: str | None = None
    price: Decimal
    stock: int
    min_stock: int
    is_low_stock: bool


class DashboardConversationItem(BaseModel):
    id: int
    customer_id: str
    channel: str
    status: str
    created_at: datetime
    updated_at: datetime


class UpdateOrderStatusInput(BaseModel):
    status: OrderStatus


class TimeSeriesPoint(BaseModel):
    period_key: str
    period_label: str
    revenue: Decimal
    orders_count: int = Field(ge=0)
    units_count: int = Field(default=0, ge=0)


class BreakdownItem(BaseModel):
    name: str
    revenue: Decimal
    units: int = Field(ge=0)
    orders_count: int = Field(ge=0)
    percentage: float = Field(ge=0)


class TopProductItem(BaseModel):
    sku: str
    name: str
    brand: str
    category: str
    units: int = Field(ge=0)
    revenue: Decimal


class DashboardMetrics(BaseModel):
    total_revenue: Decimal
    completed_revenue: Decimal
    in_progress_revenue: Decimal = Decimal("0.00")
    cancelled_revenue: Decimal = Decimal("0.00")
    average_ticket: Decimal
    active_orders_count: int = Field(ge=0)
    completed_orders_count: int = Field(default=0, ge=0)
    in_progress_orders_count: int = Field(default=0, ge=0)
    cancelled_orders_count: int = Field(ge=0)
    cancellation_rate: float = Field(ge=0)
    completion_rate: float = Field(default=0.0, ge=0)
    catalog_valuation: Decimal
    stock_health_pct: float = Field(default=100.0, ge=0)
    healthy_stock_count: int = Field(default=0, ge=0)
    total_catalog_count: int = Field(default=0, ge=0)
    granularity: Literal["day", "week", "month", "year"]
    breakdown_by: Literal["category", "brand"]
    period: Literal["all", "today", "yesterday", "7d", "30d"] = "all"
    channel: str | None = None
    time_series: list[TimeSeriesPoint]
    breakdown: list[BreakdownItem]
    status_breakdown: list[BreakdownItem] = Field(default_factory=list)
    channel_breakdown: list[BreakdownItem] = Field(default_factory=list)
    top_products: list[TopProductItem]
    no_movement_products: list[DashboardStockItem] = Field(default_factory=list)
