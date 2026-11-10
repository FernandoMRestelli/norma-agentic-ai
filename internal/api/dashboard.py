"""Endpoints HTTP para el dashboard operativo (RF-22 a RF-25, etapa 06)."""

from fastapi import APIRouter, Depends, Query

from internal.api.orders import require_operator
from internal.schemas.dashboard_schema import (
    DashboardConversationItem,
    DashboardOrderItem,
    DashboardStockItem,
    DashboardSummary,
)
from internal.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"], dependencies=[Depends(require_operator)])


@router.get("/summary", response_model=DashboardSummary)
def get_summary() -> DashboardSummary:
    """Indicadores principales: pedidos hoy, pedidos por estado, productos hoy y stock bajo (RF-22, CA-25)."""
    return dashboard_service.get_summary()


@router.get("/orders", response_model=list[DashboardOrderItem])
def get_orders(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[DashboardOrderItem]:
    """Lista de últimos pedidos con filtros de estado y paginación (RF-23)."""
    return dashboard_service.get_orders(status=status, limit=limit, offset=offset)


@router.get("/stock", response_model=list[DashboardStockItem])
def get_stock(low: bool = Query(default=False)) -> list[DashboardStockItem]:
    """Catálogo con stock disponible neto y alerta de stock bajo (RF-23)."""
    return dashboard_service.get_stock(low_only=low)


@router.get(
    "/conversations",
    response_model=list[DashboardConversationItem],
)
def get_conversations(state: str | None = Query(default=None)) -> list[DashboardConversationItem]:
    """Conversaciones que requieren atención humana (ej. estado 'derivada') (RF-24)."""
    return dashboard_service.get_conversations(state=state)
