"""Endpoints HTTP para consultar, exportar y cambiar el estado de las órdenes (RF-20, RF-47, RF-54, etapa 05)."""

import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field

from internal.config import get_settings
from internal.schemas.order_schema import Order
from internal.services import excel_service, order_service, sheets_service


def require_operator(
    x_operator_token: str | None = Header(default=None, alias="X-Operator-Token"),
    authorization: str | None = Header(default=None),
) -> None:
    """Valida la credencial de operador para acceder a los endpoints protegidos de gestión."""
    expected = get_settings().operator_token
    token = x_operator_token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    # Se compara en bytes: `compare_digest` con `str` lanza TypeError ante caracteres no ASCII
    if not expected or not token or not secrets.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credencial de operador requerida o inválida",
        )


router = APIRouter(prefix="/orders", tags=["orders"], dependencies=[Depends(require_operator)])


# --- Exportación a Excel (RF-54, docs/spec/planillas.md §3.4) ---
# Va antes de GET /{order_id}: si no, FastAPI toma «export» como un id de orden.

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get(
    "/export",
    response_class=Response,
    responses={200: {"content": {XLSX_MEDIA_TYPE: {}}, "description": "Las órdenes en un archivo .xlsx"}},
)
def export_orders() -> Response:
    """Descarga todas las órdenes en Excel: hoja «Órdenes» (como Sheet 2) y hoja «Líneas» (una por producto)."""
    content = excel_service.build_orders_workbook(order_service.list_orders())
    filename = f"pedidos-{datetime.now(sheets_service.AR_TZ):%Y%m%d-%H%M}.xlsx"
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Consulta y cancelación ---


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
    except order_service.OrderNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except order_service.InvalidTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


class StatusChange(BaseModel):
    status: str = Field(min_length=1, description="Estado nuevo: Preparado, Retirado o Cancelado")


@router.post("/{order_id}/status", response_model=Order)
def change_status(order_id: str, body: StatusChange) -> Order:
    """Pasa una orden a otro estado según `can_transition` y guarda el cambio en su historial (RF-47).

    `Recibido → Preparado → Retirado`, y `Cancelado` desde `Recibido` o `Preparado`. Devuelve 422 si el estado no
    existe, 404 si la orden no existe y 400 si la transición no es válida.
    """
    try:
        nuevo = order_service.parse_status(body.status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        return order_service.change_status(order_id, nuevo)
    except order_service.OrderNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except order_service.InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
