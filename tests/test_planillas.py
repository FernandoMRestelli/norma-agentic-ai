"""Tests del frente D: catálogo desde Sheet 1, pedidos en Sheet 2, reintento, Excel y bloqueo (docs/spec/planillas.md).

Google se reemplaza por `FakeSheets`, un cliente falso sin red. Los productos son sintéticos (SKU `PL-…`).
"""

import asyncio
import csv
import logging
import threading
import time
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import delete

from internal import main, verificar_claves
from internal.agent import tool_registry
from internal.config import get_settings
from internal.database.connection import SessionLocal
from internal.database.models import OrderRecord
from internal.schemas.order_schema import Channel, Order, OrderDraft, OrderItem
from internal.services import google_sheets_client, inventory_service, order_service, sheets_service

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

CATALOG_ID = "planilla-catalogo-de-prueba"
ORDERS_ID = "planilla-pedidos-de-prueba"
SERVICE_ACCOUNT = "norma-test@proyecto-de-prueba.iam.gserviceaccount.com"

# Encabezado en otro orden y con mayúsculas y espacios: la lectura no depende de eso (CA-93)
CATALOG_HEADER = [" Nombre", "SKU", "Precio", "Stock", "Marca", "Presentacion", "Categoria", "Stock_Minimo"]
CATALOG_ROWS = [
    ["Shampoo Planilla Keratina", "PL-SH-900", 8900.5, 12, "Planilla", "900 ml", "Shampoo", 3],
    ["Máscara Planilla Argán", "PL-MA-1K", 15000, 4, "Planilla", "1 kg", "Tratamiento y Máscara", ""],
    ["", "", "", "", "", "", "", ""],
    ["Producto Planilla Roto", "PL-ROTO", "a consultar", 3, "Planilla", "", "", ""],
    ["Tintura Planilla 7.1", 7790001, 5200, 10.0, "Planilla", "kit"],  # fila corta, SKU y stock numéricos
]


class FakeSheets:
    """`SheetsClient` en memoria. `caida` simula Google sin responder; `falla_append`, una escritura que falla."""

    def __init__(self):
        self.service_account_email = SERVICE_ACCOUNT
        self.hojas: dict[str, list[list]] = {}
        self.lecturas = 0
        self.caida = False
        self.falla_append: str | None = None  # "antes" o "despues" de escribir la fila
        self.celdas: list[tuple] = []
        self.llamadas_pedidos = 0  # lecturas y escrituras de Sheet 2

    def _hoja(self, spreadsheet_id, worksheet):
        if spreadsheet_id == ORDERS_ID:
            self.llamadas_pedidos += 1
        if self.caida:
            raise ConnectionError("Google no responde")
        return self.hojas.setdefault(spreadsheet_id if worksheet is None else f"{spreadsheet_id}/{worksheet}", [])

    def get_values(self, spreadsheet_id, worksheet):
        self.lecturas += 1
        return [list(row) for row in self._hoja(spreadsheet_id, worksheet)]

    def get_column(self, spreadsheet_id, worksheet, col):
        return [row[col - 1] if len(row) >= col else "" for row in self._hoja(spreadsheet_id, worksheet)]

    def append_row(self, spreadsheet_id, worksheet, values):
        hoja = self._hoja(spreadsheet_id, worksheet)
        if self.falla_append == "antes":
            raise ConnectionError("Falla simulada antes de escribir")
        hoja.append(list(values))
        if self.falla_append == "despues":
            raise TimeoutError("La fila se escribió pero la respuesta no llegó")

    def update_cell(self, spreadsheet_id, worksheet, row, col, value):
        hoja = self._hoja(spreadsheet_id, worksheet)
        hoja[row - 1][col - 1] = value
        self.celdas.append((row, col, value))

    def pedidos(self) -> list[list]:
        return self.hojas.get(ORDERS_ID, [])


@pytest.fixture(autouse=True)
def catalogo_sin_copia():
    sheets_service.reset_catalog_cache()
    sheets_service.reset_orders_pause()
    yield
    sheets_service.reset_catalog_cache()
    sheets_service.reset_orders_pause()
    get_settings.cache_clear()


@pytest.fixture
def reloj(monkeypatch):
    """Reloj que avanza solo cuando el test lo dice: `reloj[0] += 301`."""
    ahora = [10_000.0]
    monkeypatch.setattr(sheets_service, "_clock", lambda: ahora[0])
    return ahora


@pytest.fixture
def orders_csv(monkeypatch, tmp_path):
    path = tmp_path / "pedidos.csv"
    monkeypatch.setenv("ORDERS_FILE", str(path))
    get_settings.cache_clear()
    return path


@pytest.fixture
def google(monkeypatch, orders_csv, reloj):
    """Las dos planillas configuradas, con el cliente falso en lugar de Google."""
    fake = FakeSheets()
    fake.hojas[CATALOG_ID] = [list(CATALOG_HEADER), *[list(row) for row in CATALOG_ROWS]]
    monkeypatch.setenv("GOOGLE_CREDENTIALS_FILE", "credentials/no-se-lee-en-los-tests.json")
    monkeypatch.setenv("CATALOG_SHEET_ID", CATALOG_ID)
    monkeypatch.setenv("ORDERS_SHEET_ID", ORDERS_ID)
    monkeypatch.setattr(google_sheets_client, "get_client", lambda: fake)
    get_settings.cache_clear()
    return fake


@pytest.fixture
def sin_google(monkeypatch):
    """Falla si algo intenta crear el cliente de Google."""

    def prohibido():
        raise AssertionError("Sin planillas configuradas no se crea el cliente de Google")

    monkeypatch.setattr(google_sheets_client, "get_client", prohibido)


@pytest.fixture
def base_limpia():
    """Sin órdenes de otros tests: el stock comprometido y el reintento solo ven las de este test."""

    def limpiar():
        with SessionLocal() as session:
            session.execute(delete(OrderRecord))
            session.commit()

    limpiar()
    yield
    limpiar()


def borrador(sku: str = "PL-SH-900", quantity: int = 2, **extra) -> OrderDraft:
    return OrderDraft(
        draft_id=f"draft-planillas-{uuid4().hex}",
        customer_id="cli-planillas",
        channel=Channel.TELEGRAM,
        items=[OrderItem(sku=sku, product_name="Producto de prueba", quantity=quantity, unit_price=Decimal("100"))],
        **extra,
    )


def orden(order_id: str = "ORD-0901", notes: str | None = None) -> Order:
    return Order(
        order_id=order_id,
        draft_id=f"d-{order_id}",
        created_at=datetime(2026, 11, 10, 15, 30, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires")),
        customer_id="cli-planillas",
        channel=Channel.TELEGRAM,
        items=[OrderItem(sku="PL-SH-900", product_name="Shampoo Planilla Keratina", presentation="900 ml", quantity=1)],
        notes=notes,
    )


def stock_de(sku: str) -> int:
    return next(product.stock for product in inventory_service.get_catalog() if product.sku == sku)


def buscar(query: str = "shampoo") -> dict:
    ctx = tool_registry.TurnContext(customer_id="c", channel=Channel.WEB, disponibles=["search_products"])
    return tool_registry.execute("search_products", {"query": query}, ctx)


def filas_csv(path) -> list[list[str]]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.reader(f))


# --- Catálogo desde Sheet 1 (RF-50) ---


def test_catalogo_sale_de_la_planilla(google, caplog):
    """CA-93: con CATALOG_SHEET_ID, el catálogo sale de Sheet 1 con los campos del CSV, sin filas vacías ni rotas."""
    with caplog.at_level(logging.WARNING, logger="internal.services.sheets_service"):
        products = {product.sku: product for product in sheets_service.get_catalog()}

    assert set(products) == {"PL-SH-900", "PL-MA-1K", "7790001"}
    shampoo = products["PL-SH-900"]
    assert shampoo.name == "Shampoo Planilla Keratina"
    assert shampoo.brand == "Planilla"
    assert shampoo.presentation == "900 ml"
    assert shampoo.category == "Shampoo"
    assert shampoo.price == Decimal("8900.5")
    assert shampoo.stock == 12
    assert shampoo.min_stock == 3
    assert products["PL-MA-1K"].min_stock == 0
    assert products["7790001"].stock == 10
    assert products["7790001"].category is None

    # La fila inválida queda en el log con su número, sin los valores del catálogo
    assert "fila 5" in caplog.text
    assert "a consultar" not in caplog.text and "Roto" not in caplog.text


def test_sin_planillas_todo_sigue_con_los_csv(orders_csv, sin_google):
    """CA-93: sin CATALOG_SHEET_ID ni ORDERS_SHEET_ID, el catálogo y los pedidos usan los CSV y no se llama a Google."""
    assert not sheets_service.catalog_from_sheet()
    assert not sheets_service.orders_to_sheet()
    assert {"SH-PAN-400", "CE-SED-340"} <= {product.sku for product in sheets_service.get_catalog()}

    sheets_service.append_order(orden())
    assert filas_csv(orders_csv) == [sheets_service.ORDER_COLUMNS, sheets_service.format_order_row(orden())]


def test_sheet1_sin_columna_obligatoria_no_se_usa(google):
    """CA-93 (borde): a Sheet 1 le falta `precio`: es una falla de lectura y el log dice qué columna falta."""
    google.hojas[CATALOG_ID] = [["sku", "nombre", "stock"], ["PL-1", "Algo", 3]]
    with pytest.raises(sheets_service.CatalogoNoDisponibleError, match="precio"):
        sheets_service.get_catalog()


# --- Caché del catálogo (RF-51, RNF-13) ---


def test_catalogo_se_cachea_cinco_minutos(google, reloj):
    """CA-94: dentro de los 5 minutos no se vuelve a leer Sheet 1; después sí, y se ven los cambios."""
    assert stock_de("PL-SH-900") == 12
    assert google.lecturas == 1

    google.hojas[CATALOG_ID][1][3] = 7  # el equipo cambia el stock en la planilla
    reloj[0] += 4 * 60
    assert stock_de("PL-SH-900") == 12
    assert buscar()["ok"] is True
    assert google.lecturas == 1

    reloj[0] += 61
    assert stock_de("PL-SH-900") == 7
    assert google.lecturas == 2


def test_google_caido_usa_la_copia_de_la_ultima_hora(google, reloj):
    """CA-95: sin Google se usa la última copia buena hasta 1 hora, sin esperar a Google en cada búsqueda."""
    assert stock_de("PL-SH-900") == 12
    google.caida = True

    reloj[0] += 6 * 60
    assert stock_de("PL-SH-900") == 12  # probó Google, falló y usó la copia
    assert google.lecturas == 2

    reloj[0] += 30
    assert stock_de("PL-SH-900") == 12  # dentro del minuto tras la falla no vuelve a probar
    assert google.lecturas == 2

    reloj[0] += 31
    assert buscar()["ok"] is True
    assert google.lecturas == 3

    reloj[0] = 10_000.0 + 60 * 60 + 1  # la copia ya tiene más de una hora
    result = buscar()
    assert result["ok"] is False
    assert result["error"]["code"] == "catalogo_no_disponible"


def test_sin_copia_y_sin_google_el_catalogo_no_esta_disponible(google):
    """CA-95: Google caído desde el arranque, sin copia: la tool dice `catalogo_no_disponible` y no vuelve al CSV."""
    google.caida = True
    with pytest.raises(OSError):
        sheets_service.get_catalog()
    assert buscar()["error"]["code"] == "catalogo_no_disponible"


def test_confirmar_lee_el_catalogo_fresco(google, base_limpia):
    """CA-96: con una copia reciente, la confirmación igual lee Sheet 1 y usa el stock nuevo."""
    assert stock_de("PL-SH-900") == 12
    google.hojas[CATALOG_ID][1][3] = 2  # quedan 2 en la planilla
    assert stock_de("PL-SH-900") == 12  # la búsqueda sigue con la copia

    with pytest.raises(order_service.InsufficientStockError) as exc:
        order_service.confirm_draft(borrador(quantity=3))
    assert exc.value.lines == [{"sku": "PL-SH-900", "requested": 3, "available": 2}]
    assert google.lecturas == 2
    assert stock_de("PL-SH-900") == 2  # la lectura fresca renovó la copia


def test_confirmar_con_google_caido_usa_la_copia(google, reloj, base_limpia):
    """CA-96 / DP-01: si Google falla justo al confirmar, se confirma con la copia de la última hora."""
    assert stock_de("PL-SH-900") == 12
    google.caida = True
    reloj[0] += 10 * 60

    order = order_service.confirm_draft(borrador(quantity=2))
    assert order.order_id.startswith("ORD-")
    assert order.sheets_synced is False  # Sheet 2 también está caído: queda para el reintento

    google.caida = False
    assert order_service.sync_pending_orders() == 1
    assert [row[0] for row in google.pedidos()] == ["id_orden", order.order_id]


# --- Pedidos en Sheet 2 (RF-52) ---


def test_pedido_en_sheet2_con_encabezado_y_sin_duplicar(google, orders_csv):
    """CA-97: Sheet 2 vacío recibe el encabezado y una sola fila aunque se agregue dos veces; el CSV también."""
    order = orden(notes="=IMPORTXML(\"https://ejemplo.invalid\")")
    sheets_service.append_order(order)
    sheets_service.append_order(order)

    assert google.pedidos() == [sheets_service.ORDER_COLUMNS, sheets_service.format_order_row(order)]
    assert filas_csv(orders_csv) == [sheets_service.ORDER_COLUMNS, sheets_service.format_order_row(order)]

    otra = orden("ORD-0902")
    sheets_service.append_order(otra)
    assert [row[0] for row in google.pedidos()] == ["id_orden", "ORD-0901", "ORD-0902"]


def test_cliente_gspread_escribe_sin_interpretar(monkeypatch):
    """CA-97: el cliente real agrega filas y celdas con RAW (un `=` no es fórmula) y solo pide permiso de planillas."""
    import gspread

    llamadas = {}

    class HojaFalsa:
        def append_row(self, values, **kwargs):
            llamadas["append"] = (values, kwargs)

        def update(self, values, **kwargs):
            llamadas["update"] = (values, kwargs)

    class PlanillaFalsa:
        def get_worksheet(self, index):
            llamadas["hoja"] = index
            return HojaFalsa()

    class ClienteFalso:
        def set_timeout(self, timeout):
            llamadas["timeout"] = timeout

        def open_by_key(self, key):
            llamadas["planilla"] = key
            return PlanillaFalsa()

    def service_account(filename, scopes):
        llamadas["scopes"] = scopes
        return ClienteFalso()

    monkeypatch.setattr(gspread, "service_account", service_account)
    client = google_sheets_client.GspreadClient("credenciales.json")
    client.append_row(ORDERS_ID, None, ["ORD-0901", "=1+1"])
    client.update_cell(ORDERS_ID, None, 1, 1, "id_orden")

    assert llamadas["scopes"] == ["https://www.googleapis.com/auth/spreadsheets"]
    assert llamadas["timeout"] == google_sheets_client.TIMEOUT_SECONDS
    assert llamadas["planilla"] == ORDERS_ID and llamadas["hoja"] == 0
    assert llamadas["append"] == (["ORD-0901", "=1+1"], {"value_input_option": "RAW", "table_range": "A1"})
    assert llamadas["update"] == ([["id_orden"]], {"range_name": "A1", "value_input_option": "RAW"})


@pytest.mark.parametrize("momento", ["antes", "despues"])
def test_falla_sheet2_y_el_reintento_agrega_una_sola_fila(google, base_limpia, momento):
    """CA-98 / CA-19: Sheet 2 falla antes o después de escribir; la orden queda en la base y el reintento no duplica."""
    google.hojas[ORDERS_ID] = [list(sheets_service.ORDER_COLUMNS)]  # así la que falla es la fila de la orden
    google.falla_append = momento
    order = order_service.confirm_draft(borrador())

    assert order.order_id.startswith("ORD-")  # el cliente recibe su número igual
    assert order.sheets_synced is False
    assert order_service.get_order(order.order_id).sheets_synced is False

    google.falla_append = None
    assert order_service.sync_pending_orders() == 1
    assert order_service.get_order(order.order_id).sheets_synced is True
    assert [row[0] for row in google.pedidos()].count(order.order_id) == 1

    assert order_service.sync_pending_orders() == 0
    assert [row[0] for row in google.pedidos()].count(order.order_id) == 1


def test_tras_una_falla_de_sheet2_la_confirmacion_no_espera_a_google(google, reloj, orders_csv, base_limpia):
    """CA-98: en el minuto que sigue a una falla de Sheet 2, confirmar no llama a Google; el CSV se escribe igual."""
    google.hojas[ORDERS_ID] = [list(sheets_service.ORDER_COLUMNS)]
    google.falla_append = "antes"
    primera = order_service.confirm_draft(borrador())
    assert primera.sheets_synced is False

    google.falla_append = None  # Google ya anda, pero la pausa no lo sabe
    google.llamadas_pedidos = 0
    reloj[0] += sheets_service.ORDERS_PAUSE_AFTER_FAILURE_SECONDS - 1
    segunda = order_service.confirm_draft(borrador())
    assert segunda.order_id.startswith("ORD-")
    assert segunda.sheets_synced is False
    assert google.llamadas_pedidos == 0
    assert segunda.order_id in [row[0] for row in filas_csv(orders_csv)]

    reloj[0] += 2  # pasó el minuto: se vuelve a probar Sheet 2 al confirmar
    tercera = order_service.confirm_draft(borrador())
    assert tercera.sheets_synced is True
    assert google.llamadas_pedidos > 0

    assert order_service.sync_pending_orders() == 2
    ids = [row[0] for row in google.pedidos()]
    assert [ids.count(o.order_id) for o in (primera, segunda, tercera)] == [1, 1, 1]


def test_el_reintento_no_respeta_la_pausa_y_la_levanta(google, reloj, base_limpia):
    """CA-98: el reintento prueba Sheet 2 aunque esté en pausa; si anda, la confirmación siguiente vuelve a Google."""
    google.hojas[ORDERS_ID] = [list(sheets_service.ORDER_COLUMNS)]
    google.falla_append = "antes"
    primera = order_service.confirm_draft(borrador())
    assert primera.sheets_synced is False

    google.falla_append = None
    reloj[0] += 10
    assert order_service.sync_pending_orders() == 1

    segunda = order_service.confirm_draft(borrador())
    assert segunda.sheets_synced is True
    assert [row[0] for row in google.pedidos()] == ["id_orden", primera.order_id, segunda.order_id]


# --- Reintento en segundo plano (RF-53) ---


def test_reintento_en_segundo_plano_sigue_tras_una_falla(monkeypatch, caplog):
    """CA-99: la tarea llama a sync_pending_orders en cada intervalo; una pasada que falla no la corta."""
    llamadas = []

    def sync():
        llamadas.append(1)
        if len(llamadas) == 1:
            raise RuntimeError("Google no responde")
        return 0

    monkeypatch.setattr(order_service, "sync_pending_orders", sync)

    async def correr():
        tarea = asyncio.create_task(main.reintentar_pedidos_pendientes(0.001))
        while len(llamadas) < 3:
            await asyncio.sleep(0.005)
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea

    with caplog.at_level(logging.ERROR, logger="internal.main"):
        asyncio.run(asyncio.wait_for(correr(), timeout=5))

    assert len(llamadas) >= 3
    assert "Falló el reintento de pedidos pendientes" in caplog.text


def test_la_api_arranca_y_apaga_el_reintento(monkeypatch):
    """CA-99: al arrancar, la API lanza el reintento en segundo plano; al apagarse, lo cancela."""
    llamadas = []
    monkeypatch.setattr(main, "SYNC_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(order_service, "sync_pending_orders", lambda: llamadas.append(1) or 0)

    with TestClient(main.app) as api:
        assert api.get("/health").status_code == 200
        limite = time.monotonic() + 5
        while not llamadas and time.monotonic() < limite:
            time.sleep(0.01)
        tarea = main.app.state.reintento_pedidos
        assert not tarea.done()

    assert llamadas
    assert tarea.cancelled()


# --- Exportación a Excel (RF-54) ---


OPERATOR_TOKEN = "token-operador-planillas"


@pytest.fixture
def operador(monkeypatch, orders_csv):
    monkeypatch.setenv("OPERATOR_TOKEN", OPERATOR_TOKEN)
    get_settings.cache_clear()


def test_exportar_pedidos_a_excel(operador, base_limpia):
    """CA-100: GET /orders/export da un .xlsx con la hoja Órdenes (como Sheet 2) y la hoja Líneas, sin fórmulas."""
    draft = OrderDraft(
        draft_id=f"draft-planillas-{uuid4().hex}",
        customer_id="cli-planillas",
        channel=Channel.WEB,
        items=[
            OrderItem(sku="SH-PAN-400", product_name="Shampoo Pantene", presentation="400 ml", quantity=2,
                      unit_price=Decimal("7350.00")),
            OrderItem(sku="CE-SED-340", product_name="Crema Sedal", quantity=1),
        ],
        notes="=1+1",
    )
    order = order_service.confirm_draft(draft)

    response = TestClient(main.app).get("/orders/export", headers={"X-Operator-Token": OPERATOR_TOKEN})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert response.headers["content-disposition"].startswith('attachment; filename="pedidos-')
    workbook = load_workbook(BytesIO(response.content))
    assert workbook.sheetnames == ["Órdenes", "Líneas"]

    ordenes = [[cell.value or "" for cell in row] for row in workbook["Órdenes"].iter_rows()]
    assert ordenes[0] == sheets_service.ORDER_COLUMNS
    assert ordenes[1:] == [sheets_service.format_order_row(order)]
    notas = workbook["Órdenes"].cell(row=2, column=sheets_service.ORDER_COLUMNS.index("notas") + 1)
    assert notas.value == "=1+1" and notas.data_type == "s"  # texto, no fórmula

    lineas = [[cell.value for cell in row] for row in workbook["Líneas"].iter_rows()]
    assert lineas[0] == ["id_orden", "sku", "producto", "presentacion", "cantidad", "precio_unitario", "subtotal"]
    assert lineas[1] == [order.order_id, "SH-PAN-400", "Shampoo Pantene", "400 ml", 2, 7350, 14700]
    assert lineas[2][:5] == [order.order_id, "CE-SED-340", "Crema Sedal", None, 1]
    assert lineas[2][5:] == [None, None]


def test_exportar_pide_credencial(operador):
    """CA-100: sin la credencial de operador, /orders/export responde 401 como el resto de /orders/*."""
    client = TestClient(main.app)
    assert client.get("/orders/export").status_code == 401
    assert client.get("/orders/export", headers={"X-Operator-Token": "otro"}).status_code == 401


# --- Bloqueo del CSV local (RF-52) ---


def test_escrituras_simultaneas_no_duplican_ni_mezclan(orders_csv, sin_google):
    """CA-101: varios hilos agregan pedidos a la vez, algunos repetidos: cada uno queda una vez y entero."""
    ids = [f"ORD-{n:04d}" for n in range(911, 919)]
    trabajos = [orden(order_id, notes="nota " * 200) for order_id in ids for _ in range(3)]
    largada = threading.Barrier(len(trabajos))
    errores = []

    def escribir(order):
        try:
            largada.wait()
            sheets_service.append_order(order)
        except Exception as exc:  # pragma: no cover - se muestra en el assert
            errores.append(exc)

    hilos = [threading.Thread(target=escribir, args=(order,)) for order in trabajos]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    assert not errores
    rows = filas_csv(orders_csv)
    assert rows[0] == sheets_service.ORDER_COLUMNS
    assert sorted(row[0] for row in rows[1:]) == ids
    assert all(len(row) == len(sheets_service.ORDER_COLUMNS) for row in rows)


@pytest.mark.skipif(fcntl is None, reason="sin fcntl el bloqueo es solo entre hilos")
def test_el_bloqueo_vale_entre_procesos(orders_csv, sin_google):
    """CA-101: mientras otro proceso (el bot) tiene el bloqueo de pedidos, la API espera para escribir."""
    lock_path = orders_csv.with_name(orders_csv.name + ".lock")
    with open(lock_path, "a") as otro_proceso:
        fcntl.flock(otro_proceso, fcntl.LOCK_EX)
        hilo = threading.Thread(target=sheets_service.append_order, args=(orden(),))
        hilo.start()
        hilo.join(timeout=0.3)
        assert hilo.is_alive()
        assert not orders_csv.exists()
    hilo.join(timeout=5)
    assert not hilo.is_alive()
    assert [row[0] for row in filas_csv(orders_csv)] == ["id_orden", "ORD-0901"]


# --- verificar_claves (CA-102) ---


def test_verificar_claves_prueba_las_planillas_sin_mostrar_datos(google, capsys):
    """CA-102: informa cuántos productos y pedidos leyó y prueba la edición de Sheet 2, sin filas ni claves."""
    assert verificar_claves.check_catalog_sheet() is True
    assert verificar_claves.check_orders_sheet() is True  # Sheet 2 vacío: escribe el encabezado
    assert google.pedidos() == [sheets_service.ORDER_COLUMNS]

    sheets_service.append_order(orden())
    assert verificar_claves.check_orders_sheet() is True  # ya tiene encabezado: reescribe A1 igual
    assert google.celdas == [(1, 1, "id_orden")]
    assert google.pedidos()[0] == sheets_service.ORDER_COLUMNS

    salida = capsys.readouterr().out
    assert "productos leídos: 3" in salida
    assert "pedidos cargados: 1" in salida
    for dato in ("Keratina", "PL-SH-900", "8900", "ORD-0901", "cli-planillas"):
        assert dato not in salida

    google.caida = True
    assert verificar_claves.check_orders_sheet() is False
    assert verificar_claves.check_catalog_sheet() is False
    salida = capsys.readouterr().out
    assert f"Compartí la planilla como Editor con {SERVICE_ACCOUNT}" in salida
    assert f"Compartí la planilla como Lector con {SERVICE_ACCOUNT}" in salida


def test_verificar_claves_rechaza_una_planilla_que_no_es_la_de_pedidos(google, capsys):
    """CA-102 (borde): si A1 de Sheet 2 no es `id_orden`, no escribe nada y avisa."""
    google.hojas[ORDERS_ID] = [["sku", "nombre"], ["PL-1", "Algo"]]
    assert verificar_claves.check_orders_sheet() is False
    assert google.celdas == []
    assert "ORDERS_SHEET_ID" in capsys.readouterr().out


def test_verificar_claves_sin_planillas(sin_google, capsys):
    """CA-102: sin planillas configuradas, dice que se usan los CSV y no llama a Google."""
    assert verificar_claves.check_catalog_sheet() is True
    assert verificar_claves.check_orders_sheet() is True
    salida = capsys.readouterr().out
    assert "CATALOG_FILE" in salida and "ORDERS_FILE" in salida
