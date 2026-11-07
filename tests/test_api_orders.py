"""Tests de los endpoints de consulta y cancelación de órdenes (RF-20, etapa 05)."""

from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from internal.database.connection import SessionLocal, init_db
from internal.database.models import OrderRecord
from internal.main import app
from internal.schemas.agent_schema import ToolFailure
from internal.schemas.order_schema import Channel, OrderDraft, OrderItem, OrderStatus
from internal.services import inventory_service, order_service
from internal.tools import order_tools

client = TestClient(app)

SKU_PANTENE = "SH-PAN-400"  # En catalogo.csv tiene stock base = 24


@pytest.fixture(autouse=True)
def clean_orders():
    """Limpia la tabla orders antes y después de cada test."""
    init_db()
    with SessionLocal() as session:
        session.execute(delete(OrderRecord))
        session.commit()
    yield
    with SessionLocal() as session:
        session.execute(delete(OrderRecord))
        session.commit()


def _create_order(sku: str = SKU_PANTENE, quantity: int = 10):
    draft = OrderDraft(
        draft_id=uuid4().hex,
        customer_id="cust-test",
        channel=Channel.WEB,
        items=[
            OrderItem(
                sku=sku,
                product_name=f"Producto {sku}",
                presentation="Unidad",
                quantity=quantity,
                unit_price=Decimal("100.00"),
            )
        ],
    )
    return order_service.confirm_draft(draft)


def test_get_order_found():
    """GET /orders/{order_id} devuelve 200 y los datos completos de la orden."""
    order = _create_order()
    response = client.get(f"/orders/{order.order_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["order_id"] == order.order_id
    assert data["status"] == OrderStatus.RECEIVED.value
    assert len(data["items"]) == 1
    assert data["items"][0]["sku"] == SKU_PANTENE


def test_get_order_not_found():
    """GET /orders/{order_id} devuelve 404 para una orden inexistente."""
    response = client.get("/orders/ORD-9999")
    assert response.status_code == 404
    assert "Orden no encontrada" in response.json()["detail"]


def test_cancel_order_api_success_and_frees_stock():
    """POST /orders/{order_id}/cancel pasa la orden a Cancelado y libera el stock (RF-20)."""
    order = _create_order(quantity=10)
    assert inventory_service.get_available_stock(SKU_PANTENE) == 14

    response = client.post(f"/orders/{order.order_id}/cancel")
    assert response.status_code == 200
    data = response.json()
    assert data["order_id"] == order.order_id
    assert data["status"] == OrderStatus.CANCELLED.value

    # Stock disponible restituido en el inventario
    assert inventory_service.get_available_stock(SKU_PANTENE) == 24


def test_cancel_order_api_not_found():
    """POST /orders/{order_id}/cancel devuelve 404 si la orden no existe."""
    response = client.post("/orders/ORD-9999/cancel")
    assert response.status_code == 404
    assert "Orden no encontrada" in response.json()["detail"]


def test_cancel_order_api_already_cancelled():
    """POST /orders/{order_id}/cancel devuelve 400 si la orden ya está cancelada."""
    order = _create_order()
    first_resp = client.post(f"/orders/{order.order_id}/cancel")
    assert first_resp.status_code == 200

    second_resp = client.post(f"/orders/{order.order_id}/cancel")
    assert second_resp.status_code == 400
    assert "No se puede cancelar una orden en estado" in second_resp.json()["detail"]


def test_order_tools_cancelar_pedido():
    """Tool cancelar_pedido devuelve la orden cancelada o lanza ToolFailure."""
    order = _create_order()
    cancelled = order_tools.cancelar_pedido(order.order_id)
    assert cancelled.status == OrderStatus.CANCELLED

    # Si se intenta cancelar de nuevo, arroja ToolFailure
    with pytest.raises(ToolFailure) as exc_info:
        order_tools.cancelar_pedido(order.order_id)
    assert exc_info.value.code == "orden_invalida"
