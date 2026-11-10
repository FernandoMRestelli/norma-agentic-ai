"""Tests del dashboard operativo y endpoints de gestión de órdenes (RF-22 a RF-25, CA-24, CA-25, Issue #61)."""

import csv
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from internal.config import get_settings
from internal.database.connection import SessionLocal, init_db
from internal.database.models import ConversationRecord, OrderRecord
from internal.main import app
from internal.schemas.order_schema import Channel, OrderDraft, OrderItem, OrderStatus
from internal.services import inventory_service, order_service

client = TestClient(app)

OPERATOR_TOKEN = "token-operador-test"
AUTH_HEADERS = {"X-Operator-Token": OPERATOR_TOKEN}
DATA_EXAMPLE_DIR = Path(__file__).resolve().parent.parent / "data_example"


@pytest.fixture(autouse=True)
def clean_state_and_auth(monkeypatch):
    monkeypatch.setenv("OPERATOR_TOKEN", OPERATOR_TOKEN)
    get_settings.cache_clear()
    init_db()
    with SessionLocal() as session:
        session.execute(delete(OrderRecord))
        session.execute(delete(ConversationRecord))
        session.commit()
    yield
    with SessionLocal() as session:
        session.execute(delete(OrderRecord))
        session.execute(delete(ConversationRecord))
        session.commit()
    get_settings.cache_clear()


# ============================================================================
# CA-24: Autenticación obligatoria en /dashboard/*
# ============================================================================


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard/summary",
        "/dashboard/orders",
        "/dashboard/stock",
        "/dashboard/conversations",
    ],
)
def test_dashboard_endpoints_require_authentication(path):
    """CA-24: Se pide /dashboard/* sin token o con token inválido -> 401 Unauthorized (RF-25)."""
    # Sin token
    resp_no_auth = client.get(path)
    assert resp_no_auth.status_code == 401

    # Con token inválido
    resp_bad_token = client.get(path, headers={"X-Operator-Token": "token-falso"})
    assert resp_bad_token.status_code == 401

    # Con token válido
    resp_ok = client.get(path, headers=AUTH_HEADERS)
    assert resp_ok.status_code == 200


# ============================================================================
# CA-25: Summary con catálogo y pedidos de ejemplo
# ============================================================================


def test_dashboard_summary_datos_ejemplo(monkeypatch):
    """CA-25: Con catálogo y pedidos de ejemplo, /dashboard/summary devuelve exactamente:

    - Productos con stock bajo = 3 (CE-SED-340, ALG-EST-140, TIN-KOL-71)
    - Pedidos por estado = 2 Recibido, 2 Preparado, 2 Retirado, 1 Cancelado (RF-22, RF-23).
    """
    monkeypatch.setenv("CATALOG_FILE", str(DATA_EXAMPLE_DIR / "catalogo.csv"))
    monkeypatch.setenv("ORDERS_FILE", str(DATA_EXAMPLE_DIR / "pedidos.csv"))
    get_settings.cache_clear()

    resp = client.get("/dashboard/summary", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    # Productos con stock bajo = 3
    assert data["low_stock_products_count"] == 3
    assert set(data["low_stock_skus"]) == {"CE-SED-340", "ALG-EST-140", "TIN-KOL-71"}

    # Pedidos por estado = 2 Recibido, 2 Preparado, 2 Retirado, 1 Cancelado
    orders_by_st = data["orders_by_status"]
    assert orders_by_st[OrderStatus.RECEIVED.value] == 2
    assert orders_by_st[OrderStatus.PREPARED.value] == 2
    assert orders_by_st[OrderStatus.PICKED_UP.value] == 2
    assert orders_by_st[OrderStatus.CANCELLED.value] == 1


# ============================================================================
# RF-23: Listados de pedidos y stock
# ============================================================================


def test_dashboard_orders_listing_and_filtering(monkeypatch):
    """RF-23: Listado de últimos pedidos y filtros por estado."""
    monkeypatch.setenv("CATALOG_FILE", str(DATA_EXAMPLE_DIR / "catalogo.csv"))
    monkeypatch.setenv("ORDERS_FILE", str(DATA_EXAMPLE_DIR / "pedidos.csv"))
    get_settings.cache_clear()

    # Todos los pedidos
    resp = client.get("/dashboard/orders?limit=10", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    orders = resp.json()
    assert len(orders) == 7

    # Filtro por estado Preparado (hay 2 en data_example)
    resp_prep = client.get("/dashboard/orders?status=Preparado", headers=AUTH_HEADERS)
    assert resp_prep.status_code == 200
    orders_prep = resp_prep.json()
    assert len(orders_prep) == 2
    assert all(o["status"] == "Preparado" for o in orders_prep)


def test_dashboard_stock_listing_and_low_stock_filter(monkeypatch):
    """RF-23: Listado de stock neto y filtro de sólo stock bajo."""
    monkeypatch.setenv("CATALOG_FILE", str(DATA_EXAMPLE_DIR / "catalogo.csv"))
    get_settings.cache_clear()

    # Todo el catálogo
    resp = client.get("/dashboard/stock", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    stock = resp.json()
    assert len(stock) == 15

    # Solo stock bajo (low=true)
    resp_low = client.get("/dashboard/stock?low=true", headers=AUTH_HEADERS)
    assert resp_low.status_code == 200
    stock_low = resp_low.json()
    assert len(stock_low) == 3
    assert {p["sku"] for p in stock_low} == {"CE-SED-340", "ALG-EST-140", "TIN-KOL-71"}
    assert all(p["is_low_stock"] is True for p in stock_low)


# ============================================================================
# RF-24: Listado de conversaciones derivadas
# ============================================================================


def test_dashboard_conversations_listing():
    """RF-24: Listado de conversaciones que requieren atención humana."""
    with SessionLocal() as session:
        session.add(ConversationRecord(customer_id="cust-1", channel="whatsapp", status="abierta"))
        session.add(ConversationRecord(customer_id="cust-2", channel="telegram", status="derivada"))
        session.add(ConversationRecord(customer_id="cust-3", channel="web", status="derivada"))
        session.commit()

    # Todas
    resp = client.get("/dashboard/conversations", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()) == 3

    # Solo derivadas
    resp_deriv = client.get("/dashboard/conversations?state=derivada", headers=AUTH_HEADERS)
    assert resp_deriv.status_code == 200
    derivadas = resp_deriv.json()
    assert len(derivadas) == 2
    assert all(c["status"] == "derivada" for c in derivadas)


# ============================================================================
# Issue #61: Transición de estados y descuento físico de stock
# ============================================================================


def _create_sample_order(sku: str = "SH-PAN-400", quantity: int = 5):
    draft = OrderDraft(
        draft_id=uuid4().hex,
        customer_id="cust-test",
        channel=Channel.WEB,
        items=[
            OrderItem(
                sku=sku,
                product_name="Shampoo Pantene",
                presentation="400 ml",
                quantity=quantity,
                unit_price=Decimal("7350.00"),
            )
        ],
    )
    return order_service.confirm_draft(draft)


def test_update_order_status_workflow_and_stock_deduction(tmp_path, monkeypatch):
    """Issue #61:

    - Transición Recibido -> Preparado -> Despachado -> Entregado.
    - Al pasar a Despachado, descuenta físicamente del catálogo y marca stock_applied = True.
    - get_committed_stock deja de computar la orden cuando stock_applied = True.
    """
    # Catálogo temporal para probar descuento
    catalog_copy = tmp_path / "catalogo_test.csv"
    orders_copy = tmp_path / "pedidos_test.csv"
    with open(DATA_EXAMPLE_DIR / "catalogo.csv", encoding="utf-8") as f_in:
        content = f_in.read()
    with open(catalog_copy, "w", encoding="utf-8") as f_out:
        f_out.write(content)

    monkeypatch.setenv("CATALOG_FILE", str(catalog_copy))
    monkeypatch.setenv("ORDERS_FILE", str(orders_copy))
    get_settings.cache_clear()

    # Stock inicial en catálogo de SH-PAN-400 es 24
    order = _create_sample_order(sku="SH-PAN-400", quantity=5)
    assert order.status == OrderStatus.RECEIVED
    assert order.stock_applied is False

    # Mientras está Recibido: stock disponible = 24 - 5 = 19 (comprometido = 5)
    assert inventory_service.get_committed_stock() == {"SH-PAN-400": 5}
    assert inventory_service.get_available_stock("SH-PAN-400") == 19

    # 1. Pasa a Preparado
    resp_prep = client.post(
        f"/orders/{order.order_id}/status",
        headers=AUTH_HEADERS,
        json={"status": "Preparado"},
    )
    assert resp_prep.status_code == 200
    assert resp_prep.json()["status"] == OrderStatus.PREPARED.value
    assert resp_prep.json()["stock_applied"] is False

    # 2. Pasa a Despachado (se aplica el descuento en catálogo físico)
    resp_disp = client.post(
        f"/orders/{order.order_id}/status",
        headers=AUTH_HEADERS,
        json={"status": "Despachado"},
    )
    assert resp_disp.status_code == 200
    assert resp_disp.json()["status"] == OrderStatus.DISPATCHED.value
    assert resp_disp.json()["stock_applied"] is True

    # El stock del catálogo bajó físicamente de 24 a 19
    with open(catalog_copy, encoding="utf-8") as f:
        rows = {r["sku"]: int(r["stock"]) for r in csv.DictReader(f)}
    assert rows["SH-PAN-400"] == 19

    # Y la orden ya NO suma como stock comprometido en memoria
    assert inventory_service.get_committed_stock() == {}
    # El disponible sigue siendo exactamente 19 (sin doble descuento)
    assert inventory_service.get_available_stock("SH-PAN-400") == 19

    # 3. Pasa a Entregado
    resp_deliv = client.post(
        f"/orders/{order.order_id}/status",
        headers=AUTH_HEADERS,
        json={"status": "Entregado"},
    )
    assert resp_deliv.status_code == 200
    assert resp_deliv.json()["status"] == OrderStatus.DELIVERED.value

    # 4. Transición inválida: no se puede cancelar una orden ya entregada
    resp_inv = client.post(
        f"/orders/{order.order_id}/status",
        headers=AUTH_HEADERS,
        json={"status": "Cancelado"},
    )
    assert resp_inv.status_code == 400
    assert "No se puede cancelar una orden en estado 'Entregado'" in resp_inv.json()["detail"]


# ============================================================================
# Frontend UI route
# ============================================================================


def test_dashboard_ui_served():
    """Verifica que la SPA del dashboard se sirva como HTML en /dashboard/ui."""
    resp = client.get("/dashboard/ui")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Norma · Dashboard Operativo" in resp.text
