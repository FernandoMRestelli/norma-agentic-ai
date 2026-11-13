"""Tests de los endpoints de consulta y cancelación de órdenes y tool cancelar_pedido (RF-20, etapa 05)."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from internal.config import get_settings
from internal.database.connection import SessionLocal, init_db
from internal.database.models import OrderRecord
from internal.main import app
from internal.schemas.agent_schema import ToolFailure
from internal.schemas.order_schema import Channel, OrderDraft, OrderItem, OrderStatus
from internal.services import inventory_service, order_service
from internal.tools import order_tools

client = TestClient(app)

SKU_PANTENE = "SH-PAN-400"  # En catalogo.csv tiene stock base = 24
OPERATOR_TOKEN = "token-operador-prueba"
AUTH_HEADERS = {"X-Operator-Token": OPERATOR_TOKEN}


@pytest.fixture(autouse=True)
def clean_orders_and_configure_token(monkeypatch):
    """Limpia la base y configura la credencial del operador para el test."""
    monkeypatch.setenv("OPERATOR_TOKEN", OPERATOR_TOKEN)
    get_settings.cache_clear()
    init_db()
    with SessionLocal() as session:
        session.execute(delete(OrderRecord))
        session.commit()
    yield
    with SessionLocal() as session:
        session.execute(delete(OrderRecord))
        session.commit()
    get_settings.cache_clear()


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
                unit_price=None,  # sin precio: estos tests miran el stock, y al confirmar el precio se revalida
            )
        ],
    )
    return order_service.confirm_draft(draft)


def test_orders_endpoints_requieren_autenticacion():
    """CA-48: GET y POST /orders/* sin token o con token inválido devuelven 401."""
    # Sin headers
    assert client.get("/orders/ORD-0001").status_code == 401
    assert client.post("/orders/ORD-0001/cancel").status_code == 401

    # Con token erróneo
    bad_headers = {"X-Operator-Token": "token-invalido"}
    assert client.get("/orders/ORD-0001", headers=bad_headers).status_code == 401
    assert client.post("/orders/ORD-0001/cancel", headers=bad_headers).status_code == 401

    # Con token que contiene caracteres no ASCII (ej. tildes o eñe)
    non_ascii_headers = {"X-Operator-Token": "token-con-tildé-ñ".encode()}
    assert client.get("/orders/ORD-0001", headers=non_ascii_headers).status_code == 401
    assert client.post("/orders/ORD-0001/cancel", headers=non_ascii_headers).status_code == 401

    # Con Authorization Bearer correcto (mayúscula y minúscula 'bearer')
    bearer_headers = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}
    resp = client.get("/orders/ORD-9999", headers=bearer_headers)
    assert resp.status_code == 404  # Pasó la autenticación y falló por inexistencia

    bearer_lower_headers = {"Authorization": f"bearer {OPERATOR_TOKEN}"}
    resp_lower = client.get("/orders/ORD-9999", headers=bearer_lower_headers)
    assert resp_lower.status_code == 404


def test_get_order_found():
    """CA-49: GET /orders/{order_id} autenticado devuelve 200 y los datos de la orden."""
    order = _create_order()
    response = client.get(f"/orders/{order.order_id}", headers=AUTH_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["order_id"] == order.order_id
    assert data["status"] == OrderStatus.RECEIVED.value
    assert len(data["items"]) == 1
    assert data["items"][0]["sku"] == SKU_PANTENE


def test_get_order_not_found():
    """CA-49: GET /orders/{order_id} devuelve 404 para una orden inexistente."""
    response = client.get("/orders/ORD-9999", headers=AUTH_HEADERS)
    assert response.status_code == 404
    assert "Orden no encontrada" in response.json()["detail"]


def test_cancel_order_api_success_and_frees_stock():
    """CA-50: POST /orders/{order_id}/cancel pasa la orden a Cancelado y libera el stock (RF-20)."""
    order = _create_order(quantity=10)
    assert inventory_service.get_available_stock(SKU_PANTENE) == 14

    response = client.post(f"/orders/{order.order_id}/cancel", headers=AUTH_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["order_id"] == order.order_id
    assert data["status"] == OrderStatus.CANCELLED.value

    # Stock disponible restituido en el inventario
    assert inventory_service.get_available_stock(SKU_PANTENE) == 24


def test_cancel_order_api_not_found():
    """CA-51: POST /orders/{order_id}/cancel devuelve 404 si la orden no existe."""
    response = client.post("/orders/ORD-9999/cancel", headers=AUTH_HEADERS)
    assert response.status_code == 404
    assert "Orden no encontrada" in response.json()["detail"]


def test_cancel_order_api_already_cancelled():
    """CA-51: POST /orders/{order_id}/cancel devuelve 400 si la orden ya está cancelada."""
    order = _create_order()
    first_resp = client.post(f"/orders/{order.order_id}/cancel", headers=AUTH_HEADERS)
    assert first_resp.status_code == 200

    second_resp = client.post(f"/orders/{order.order_id}/cancel", headers=AUTH_HEADERS)
    assert second_resp.status_code == 400
    assert "No se puede cancelar una orden en estado 'Cancelado'" in second_resp.json()["detail"]


def test_cancel_order_api_already_picked_up():
    """CA-51: POST /orders/{order_id}/cancel devuelve 400 si la orden ya está en estado terminal Retirado."""
    order = _create_order()
    with SessionLocal() as session:
        record = session.scalar(select(OrderRecord).where(OrderRecord.order_id == order.order_id))
        record.status = OrderStatus.PICKED_UP.value
        session.commit()

    resp = client.post(f"/orders/{order.order_id}/cancel", headers=AUTH_HEADERS)
    assert resp.status_code == 400
    assert "No se puede cancelar una orden en estado 'Retirado'" in resp.json()["detail"]


def test_order_tools_cancelar_pedido():
    """CA-52: Tool cancelar_pedido devuelve la orden cancelada o lanza ToolFailure."""
    order = _create_order()
    cancelled = order_tools.cancelar_pedido(order.order_id)
    assert cancelled.status == OrderStatus.CANCELLED

    # Si se intenta cancelar de nuevo, arroja ToolFailure
    with pytest.raises(ToolFailure) as exc_info:
        order_tools.cancelar_pedido(order.order_id)
    assert exc_info.value.code == "orden_invalida"
