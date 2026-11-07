"""Endpoints HTTP para la consulta y cancelación de órdenes (RF-20, etapa 05)."""

from fastapi import APIRouter, HTTPException, status

from internal.schemas.order_schema import Order
from internal.services import order_service

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("/{order_id}", response_model=Order)
def get_order(order_id: str) -> Order:
    """Consulta una orden por su identificador correlativo (ej. 'ORD-0001')."""
    order = order_service.get_order(order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Orden no encontrada: {order_id!r}",
        )
    return order


@router.post("/{order_id}/cancel", response_model=Order)
def cancel_order(order_id: str) -> Order:
    """Cancela una orden activa y libera su stock comprometido (RF-20).

    Devuelve 404 si la orden no existe, o 400 si ya se encuentra en un estado
    terminal ('Retirado' o 'Cancelado').
    """
    try:
        return order_service.cancel_order(order_id)
    except ValueError as exc:
        msg = str(exc)
        if "Orden no encontrada" in msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=msg,
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=msg,
        ) from exc
