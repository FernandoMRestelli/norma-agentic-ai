"""Tests del dashboard operativo y endpoints de gestión de órdenes (RF-22 a RF-25, CA-24, CA-25, Issue #61)."""

import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from internal.config import get_settings
from internal.database.connection import SessionLocal, init_db, make_engine
from internal.database.migrations import LATEST
from internal.database.models import ConversationRecord, OrderRecord
from internal.main import app
from internal.schemas.order_schema import Channel, OrderDraft, OrderItem, OrderStatus
from internal.services import google_sheets_client, inventory_service, order_service, sheets_service
from tests.test_planillas import ORDERS_ID, FakeSheets

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
        "/dashboard/metrics",
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

    # El historial guarda cada paso (RF-47)
    historial = [(e["from_status"], e["to_status"]) for e in resp_deliv.json()["history"]]
    assert historial == [
        (None, "Recibido"),
        ("Recibido", "Preparado"),
        ("Preparado", "Despachado"),
        ("Despachado", "Entregado"),
    ]


def test_con_el_catalogo_en_sheet_1_no_se_descuenta(tmp_path, monkeypatch):
    """Issue #61 con Sheet 1: el catálogo es de solo lectura, así que la orden despachada sigue reservando su stock."""
    monkeypatch.setenv("ORDERS_FILE", str(tmp_path / "pedidos_test.csv"))
    get_settings.cache_clear()
    catalogo = DATA_EXAMPLE_DIR / "catalogo.csv"
    catalog_copy = tmp_path / "catalogo_test.csv"
    catalog_copy.write_text(catalogo.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("CATALOG_FILE", str(catalog_copy))
    get_settings.cache_clear()
    order = _create_sample_order(sku="SH-PAN-400", quantity=5)
    # Desde acá el catálogo «está» en Sheet 1: el cambio de estado no lo lee, solo pregunta de dónde sale
    monkeypatch.setattr(sheets_service, "catalog_from_sheet", lambda: True)
    order_service.change_status(order.order_id, OrderStatus.PREPARED)
    despachada = order_service.change_status(order.order_id, OrderStatus.DISPATCHED)

    assert despachada.stock_applied is False
    assert catalog_copy.read_text(encoding="utf-8") == catalogo.read_text(encoding="utf-8")
    assert inventory_service.get_committed_stock() == {"SH-PAN-400": 5}


def test_el_catalogo_de_ejemplo_no_se_descuenta(tmp_path, monkeypatch):
    """Issue #61: `data_example/catalogo.csv` es versionado: retirar una orden no lo reescribe y sigue reservando."""
    catalogo = DATA_EXAMPLE_DIR / "catalogo.csv"
    monkeypatch.setenv("CATALOG_FILE", str(catalogo))
    monkeypatch.setenv("ORDERS_FILE", str(tmp_path / "pedidos_test.csv"))
    get_settings.cache_clear()
    antes = catalogo.read_bytes()
    order = _create_sample_order(sku="SH-PAN-400", quantity=5)
    order_service.change_status(order.order_id, OrderStatus.PREPARED)
    retirada = order_service.change_status(order.order_id, OrderStatus.PICKED_UP)

    assert retirada.stock_applied is False
    assert catalogo.read_bytes() == antes
    assert inventory_service.get_committed_stock() == {"SH-PAN-400": 5}


def _catalogo_temporal(tmp_path, monkeypatch) -> Path:
    catalog_copy = tmp_path / "catalogo_test.csv"
    catalog_copy.write_text((DATA_EXAMPLE_DIR / "catalogo.csv").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("CATALOG_FILE", str(catalog_copy))
    monkeypatch.setenv("ORDERS_FILE", str(tmp_path / "pedidos_test.csv"))
    get_settings.cache_clear()
    return catalog_copy


def _stock_en(path: Path, sku: str) -> int:
    with open(path, encoding="utf-8") as f:
        return next(int(row["stock"]) for row in csv.DictReader(f) if row["sku"] == sku)


def test_si_falla_la_base_el_catalogo_no_queda_descontado(tmp_path, monkeypatch):
    """CA-115: el descuento y el cambio de estado se guardan juntos; el reintento descuenta una sola vez."""
    catalogo = _catalogo_temporal(tmp_path, monkeypatch)
    order = _create_sample_order(sku="SH-PAN-400", quantity=5)
    order_service.change_status(order.order_id, OrderStatus.PREPARED)

    commit = Session.commit
    llamadas = []

    def commit_que_falla_una_vez(self):
        llamadas.append(1)
        if len(llamadas) == 1:
            raise OperationalError("COMMIT", {}, Exception("database is locked"))
        return commit(self)

    monkeypatch.setattr(Session, "commit", commit_que_falla_una_vez)
    with pytest.raises(OperationalError):
        order_service.change_status(order.order_id, OrderStatus.DISPATCHED)

    assert _stock_en(catalogo, "SH-PAN-400") == 24  # volvió a como estaba
    sigue = order_service.get_order(order.order_id)
    assert (sigue.status, sigue.stock_applied) == (OrderStatus.PREPARED, False)

    despachada = order_service.change_status(order.order_id, OrderStatus.DISPATCHED)
    assert despachada.stock_applied is True
    assert _stock_en(catalogo, "SH-PAN-400") == 19  # una sola vez


def test_si_falla_la_planilla_el_estado_se_reintenta(tmp_path, monkeypatch):
    """CA-116: la API responde 200, la orden queda pendiente y el reintento escribe el estado en la planilla."""
    _catalogo_temporal(tmp_path, monkeypatch)
    order = _create_sample_order(sku="SH-PAN-400", quantity=2)
    escribir_estado = sheets_service.update_order_status_in_sheets

    def planilla_caida(order_id, new_status):
        raise ConnectionError("Google no responde")

    monkeypatch.setattr(sheets_service, "update_order_status_in_sheets", planilla_caida)
    resp = client.post(f"/orders/{order.order_id}/status", headers=AUTH_HEADERS, json={"status": "Preparado"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "Preparado"
    assert resp.json()["sheets_synced"] is False

    monkeypatch.setattr(sheets_service, "update_order_status_in_sheets", escribir_estado)
    assert order_service.sync_pending_orders() == 1
    with open(tmp_path / "pedidos_test.csv", encoding="utf-8") as f:
        fila = next(row for row in csv.DictReader(f) if row["id_orden"] == order.order_id)
    assert fila["estado"] == "Preparado"
    assert order_service.get_order(order.order_id).sheets_synced is True


def test_el_estado_tambien_se_escribe_en_sheet_2(tmp_path, monkeypatch):
    """RF-60: con Sheet 2 configurada, el cambio de estado actualiza la celda `estado` de la fila de la orden."""
    _catalogo_temporal(tmp_path, monkeypatch)
    fake = FakeSheets()
    monkeypatch.setenv("GOOGLE_CREDENTIALS_FILE", "credentials/no-se-lee-en-los-tests.json")
    monkeypatch.setenv("ORDERS_SHEET_ID", ORDERS_ID)
    monkeypatch.setattr(google_sheets_client, "get_client", lambda: fake)
    get_settings.cache_clear()
    sheets_service.reset_orders_pause()

    order = _create_sample_order(sku="SH-PAN-400", quantity=2)
    order_service.change_status(order.order_id, OrderStatus.PREPARED)

    fila = next(row for row in fake.pedidos() if row[0] == order.order_id)
    assert fila[sheets_service.ORDER_COLUMNS.index("estado")] == "Preparado"


def test_pedidos_del_csv_que_no_estan_en_la_base_son_de_solo_lectura(tmp_path, monkeypatch):
    """CA-117: la base es la fuente; lo que solo está en el CSV de pedidos se ve, sin acciones, y no se duplica."""
    _catalogo_temporal(tmp_path, monkeypatch)
    order = _create_sample_order(sku="SH-PAN-400", quantity=2)  # queda en la base y en el CSV
    with open(tmp_path / "pedidos_test.csv", "a", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(
            [
                "ORD-9001",
                "2026-10-27T09:12:00-03:00",
                "cli-viejo",
                "web",
                "1 x Jabón (JAB-DOV-90)",
                "",
                "Retirado",
                "",
                "",
            ]
        )

    resp = client.get("/dashboard/orders", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    por_id = {o["order_id"]: o for o in resp.json()}
    assert set(por_id) == {order.order_id, "ORD-9001"}
    assert por_id[order.order_id]["read_only"] is False
    assert por_id["ORD-9001"]["read_only"] is True

    # El que solo está en el CSV no se puede cambiar de estado: por eso la UI no le muestra acciones
    assert client.post("/orders/ORD-9001/status", headers=AUTH_HEADERS, json={"status": "Cancelado"}).status_code == 404


def test_fila_con_fecha_ilegible_se_saltea(tmp_path, monkeypatch, caplog):
    """CA-117: una fecha ilegible en el CSV no cuenta como de hoy: la fila no se muestra y queda una advertencia."""
    pedidos = tmp_path / "pedidos.csv"
    pedidos.write_text(
        "id_orden,fecha_hora,cliente,canal,productos,notas,estado,nombre_cliente,total\n"
        "ORD-0001,ayer a la tarde,cli-001,web,1 x Jabón (JAB-DOV-90),,Recibido,,\n"
        "ORD-0002,2026-10-27T09:12:00-03:00,cli-002,web,1 x Jabón (JAB-DOV-90),,Recibido,,\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ORDERS_FILE", str(pedidos))
    get_settings.cache_clear()

    assert [o.order_id for o in sheets_service.get_orders()] == ["ORD-0002"]
    assert "ORD-0001" in caplog.text and "fecha ilegible" in caplog.text
    assert client.get("/dashboard/summary?period=today", headers=AUTH_HEADERS).json()["orders_today"] == 0


def test_linea_sin_precio_usa_el_del_catalogo_en_todas_las_vistas(tmp_path, monkeypatch):
    """CA-118: Pedidos y Ventas & KPIs calculan el total con el mismo criterio."""
    pedidos = tmp_path / "pedidos.csv"
    pedidos.write_text(
        "id_orden,fecha_hora,cliente,canal,productos,notas,estado,nombre_cliente,total\n"
        "ORD-0001,2026-10-27T09:12:00-03:00,cli-001,web,2 x Shampoo Pantene 400 ml (SH-PAN-400),,Recibido,,\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CATALOG_FILE", str(DATA_EXAMPLE_DIR / "catalogo.csv"))
    monkeypatch.setenv("ORDERS_FILE", str(pedidos))
    get_settings.cache_clear()

    pedido = client.get("/dashboard/orders", headers=AUTH_HEADERS).json()[0]
    metricas = client.get("/dashboard/metrics", headers=AUTH_HEADERS).json()
    assert Decimal(str(pedido["total"])) == Decimal(str(metricas["total_revenue"])) == Decimal("14700.00")


def test_la_ui_pide_solo_conversaciones_derivadas():
    """CA-120: la pestaña Conversaciones lista las derivadas que esperan a alguien (RF-24), no todas."""
    html = client.get("/dashboard/ui").text
    assert "apiFetch('/dashboard/conversations?state=derivada')" in html
    assert "apiFetch('/dashboard/conversations')" not in html


def test_migracion_2_agrega_stock_applied(tmp_path):
    """Una base en la versión 1 (develop antes del dashboard) gana `orders.stock_applied` sin perder órdenes."""
    path = tmp_path / "norma.db"
    engine = make_engine(f"sqlite:///{path}")
    init_db(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE orders DROP COLUMN stock_applied")
        conn.exec_driver_sql(
            "INSERT INTO orders (order_id, draft_id, customer_id, channel, status, sheets_synced, created_at) "
            "VALUES ('ORD-0001', 'd-1', 'cust', 'web', 'Preparado', 0, '2026-01-01 00:00:00')"
        )
        conn.exec_driver_sql("UPDATE schema_version SET version = 1")

    assert init_db(make_engine(f"sqlite:///{path}")) == LATEST == 2
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT order_id, stock_applied FROM orders").all() == [("ORD-0001", 0)]


# ============================================================================
# Frontend UI route
# ============================================================================


def test_dashboard_ui_served():
    """Verifica que la SPA del dashboard se sirva como HTML en /dashboard/ui."""
    resp = client.get("/dashboard/ui")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Norma · Dashboard Operativo" in resp.text


# ============================================================================
# Métricas económicas y KPIs (/dashboard/metrics)
# ============================================================================


def test_dashboard_metrics_revenue_granularity_and_breakdown(monkeypatch):
    """Verifica el cálculo de métricas económicas, series temporales y desglose por categoría/marca."""
    monkeypatch.setenv("CATALOG_FILE", str(DATA_EXAMPLE_DIR / "catalogo.csv"))
    monkeypatch.setenv("ORDERS_FILE", str(DATA_EXAMPLE_DIR / "pedidos.csv"))
    get_settings.cache_clear()

    # 1. Granularidad mensual y desglose por categoría sobre data_example (7 pedidos, 1 cancelado)
    resp_cat = client.get("/dashboard/metrics?granularity=month&breakdown=category", headers=AUTH_HEADERS)
    assert resp_cat.status_code == 200
    data = resp_cat.json()

    assert data["active_orders_count"] == 6
    assert data["cancelled_orders_count"] == 1
    assert Decimal(data["total_revenue"]) > 0
    assert Decimal(data["completed_revenue"]) > 0
    assert Decimal(data["completed_revenue"]) <= Decimal(data["total_revenue"])
    assert Decimal(data["average_ticket"]) > 0
    assert Decimal(data["catalog_valuation"]) > 0
    assert data["cancellation_rate"] == pytest.approx(14.3, abs=0.2)
    assert len(data["time_series"]) >= 1
    assert data["breakdown_by"] == "category"
    assert any(item["name"] == "Shampoo" for item in data["breakdown"])
    assert len(data["top_products"]) >= 1
    assert data["total_catalog_count"] == 15
    assert data["healthy_stock_count"] == 12
    assert data["stock_health_pct"] == pytest.approx(80.0, abs=0.1)
    assert len(data["status_breakdown"]) == 3
    assert len(data["channel_breakdown"]) >= 1
    assert isinstance(data["no_movement_products"], list)

    # 2. Granularidad semanal / anual y desglose por marca
    for gran in ("day", "week", "year"):
        resp_gran = client.get(f"/dashboard/metrics?granularity={gran}&breakdown=brand", headers=AUTH_HEADERS)
        assert resp_gran.status_code == 200
        d_gran = resp_gran.json()
        assert d_gran["granularity"] == gran
        assert d_gran["breakdown_by"] == "brand"
        assert any(item["name"] == "Pantene" for item in d_gran["breakdown"])

    # 3. Filtros por período y canal
    resp_ch = client.get("/dashboard/metrics?period=all&channel=whatsapp", headers=AUTH_HEADERS)
    assert resp_ch.status_code == 200
    assert resp_ch.json()["channel"] == "whatsapp"
    assert all(pt["units_count"] >= 0 for pt in data["time_series"])
    assert sum(pt["units_count"] for pt in data["time_series"]) > 0


def test_dashboard_summary_period_filtering_and_metrics_yesterday(monkeypatch, tmp_path):
    """Verifica el filtrado por período en /dashboard/summary y units_count / yesterday en /dashboard/metrics."""
    monkeypatch.setenv("CATALOG_FILE", str(DATA_EXAMPLE_DIR / "catalogo.csv"))
    monkeypatch.setenv("ORDERS_FILE", str(tmp_path / "empty_orders.csv"))
    get_settings.cache_clear()

    ar_tz = timezone(timedelta(hours=-3))
    now_ar = datetime.now(ar_tz).replace(hour=12, minute=0, second=0, microsecond=0)
    dates_by_tag = {
        "today": now_ar,
        "yesterday": now_ar - timedelta(days=1),
        "five_days": now_ar - timedelta(days=5),
    }

    created_ids: dict[str, str] = {}
    for tag, qty, status_target in [
        ("today", 2, OrderStatus.RECEIVED),
        ("yesterday", 3, OrderStatus.PREPARED),
        ("five_days", 4, OrderStatus.DELIVERED),
    ]:
        order = _create_sample_order(sku="SH-PAN-400", quantity=qty)
        created_ids[tag] = order.order_id
        with SessionLocal() as session:
            rec = session.scalar(select(OrderRecord).where(OrderRecord.order_id == order.order_id))
            assert rec is not None
            rec.created_at = dates_by_tag[tag].astimezone(timezone.utc)
            rec.status = status_target.value
            session.commit()

    resp_today = client.get("/dashboard/summary?period=today", headers=AUTH_HEADERS)
    assert resp_today.status_code == 200
    d_today = resp_today.json()
    assert d_today["period"] == "today"
    assert d_today["orders_today"] == 1
    assert sum(d_today["orders_by_status"].values()) == 1
    assert d_today["orders_by_status"]["Recibido"] == 1

    resp_yest = client.get("/dashboard/summary?period=yesterday", headers=AUTH_HEADERS)
    assert resp_yest.status_code == 200
    d_yest = resp_yest.json()
    assert d_yest["period"] == "yesterday"
    assert d_yest["orders_today"] == 1
    assert sum(d_yest["orders_by_status"].values()) == 1
    assert d_yest["orders_by_status"]["Preparado"] == 1

    resp_7d = client.get("/dashboard/summary?period=7d", headers=AUTH_HEADERS)
    assert resp_7d.status_code == 200
    d_7d = resp_7d.json()
    assert d_7d["period"] == "7d"
    assert d_7d["orders_today"] == 3
    assert sum(d_7d["orders_by_status"].values()) == 3

    resp_m_yest = client.get("/dashboard/metrics?granularity=day&period=yesterday", headers=AUTH_HEADERS)
    assert resp_m_yest.status_code == 200
    m_yest = resp_m_yest.json()
    assert m_yest["period"] == "yesterday"
    assert m_yest["active_orders_count"] == 1
    assert len(m_yest["time_series"]) == 1
    assert m_yest["time_series"][0]["orders_count"] == 1
    assert m_yest["time_series"][0]["units_count"] == 3
