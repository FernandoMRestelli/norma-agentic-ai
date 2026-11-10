"""Esquemas para el dashboard operativo (RF-22 a RF-25)."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from internal.schemas.order_schema import OrderStatus


class DashboardSummary(BaseModel):
    orders_today: int = Field(ge=0)
    orders_by_status: dict[str, int]
    distinct_products_today: int = Field(ge=0)
    low_stock_products_count: int = Field(ge=0)
    low_stock_skus: list[str] = Field(default_factory=list)


class DashboardOrderItem(BaseModel):
    order_id: str
    created_at: datetime
    customer_id: str
    channel: str
    status: str
    notes: str | None = None
    items_summary: str
    total: Decimal
    stock_applied: bool = False


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
