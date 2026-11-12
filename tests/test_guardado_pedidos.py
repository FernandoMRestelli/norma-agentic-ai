"""Tests del guardado de pedidos en la base (docs/spec/guardado-de-pedidos.md)."""

import json
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import delete, func, inspect, select

from internal.agent import tool_registry
from internal.agent.norma_agent import NormaAgent
from internal.config import get_settings
from internal.database.connection import SessionLocal, init_db, make_engine
from internal.database.migrations import LATEST
from internal.database.models import ConversationRecord, DraftRecord, OrderRecord, ProcessedMessageRecord
from internal.main import app
from internal.schemas.agent_schema import ProposedItem, ToolFailure
from internal.schemas.catalog_schema import Product
from internal.schemas.order_schema import Channel, IncomingMessage, Order, OrderDraft, OrderItem, OrderStatus
from internal.services import conversation_service, inventory_service, order_service, sheets_service, telegram_service
from internal.services.interpretation_service import normalize_items

# Esquema de `orders` y `conversations` de la 0.1.1 (models.py en main)
ESQUEMA_0_1_1 = """
CREATE TABLE conversations (id INTEGER PRIMARY KEY, customer_id VARCHAR NOT NULL, channel VARCHAR NOT NULL,
    status VARCHAR NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL);
CREATE TABLE orders (id INTEGER PRIMARY KEY, order_id VARCHAR UNIQUE, draft_id VARCHAR NOT NULL UNIQUE,
    customer_id VARCHAR NOT NULL, channel VARCHAR NOT NULL, items JSON NOT NULL, notes TEXT, status VARCHAR NOT NULL,
    sheets_synced BOOLEAN NOT NULL, created_at DATETIME NOT NULL);
CREATE INDEX ix_orders_customer_id ON orders (customer_id);
CREATE INDEX ix_conversations_customer_id ON conversations (customer_id);
"""

ORDENES_0_1_1 = [
    ("ORD-0001", "Recibido", [{"sku": "SH-1", "product_name": "Shampoo", "presentation": "400 ml", "quantity": 2,
                               "unit_price": "100.50"}]),
    ("ORD-0002", "Preparado", [{"sku": "SH-1", "product_name": "Shampoo", "presentation": None, "quantity": 1,
                                "unit_price": None},
                               {"sku": "TIN-7", "product_name": "Tintura", "presentation": "kit", "quantity": 3,
                                "unit_price": "10"}]),
    ("ORD-0003", "Cancelado", [{"sku": "TIN-7", "product_name": "Tintura", "presentation": "kit", "quantity": 5,
                                "unit_price": "10"}]),
]


def _base_0_1_1(path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(ESQUEMA_0_1_1)
        for n, (order_id, status, items) in enumerate(ORDENES_0_1_1, start=1):
            conn.execute(
                "INSERT INTO orders VALUES "
                "(?, ?, ?, 'telegram:1', 'telegram', ?, ?, ?, 1, '2026-11-01 10:00:00.000000')",
                (n, order_id, f"d-{n}", json.dumps(items), "nota vieja" if n == 1 else None, status),
            )


def _esquema(engine) -> dict:
    insp = inspect(engine)
    return {
        table: (
            {column["name"] for column in insp.get_columns(table)},
            {index["name"] for index in insp.get_indexes(table)},
        )
        for table in insp.get_table_names()
    }


def test_migra_una_base_de_la_0_1_1(tmp_path):
    """CA-88: una base de la 0.1.1 arranca con la versión nueva sin perder órdenes ni líneas."""
    path = tmp_path / "norma.db"
    _base_0_1_1(path)
    assert init_db(make_engine(f"sqlite:///{path}")) == LATEST

    with sqlite3.connect(path) as conn:
        orders = conn.execute("SELECT order_id, status, notes, total FROM orders ORDER BY id").fetchall()
        assert orders == [
            ("ORD-0001", "Recibido", "nota vieja", "201.00"),
            ("ORD-0002", "Preparado", None, "30"),
            ("ORD-0003", "Cancelado", None, "50"),
        ]
        lines = conn.execute(
            "SELECT order_pk, position, sku, quantity, unit_price FROM order_items ORDER BY id"
        ).fetchall()
        assert lines == [(1, 1, "SH-1", 2, "100.50"), (2, 1, "SH-1", 1, None), (2, 2, "TIN-7", 3, "10"),
                         (3, 1, "TIN-7", 5, "10")]
        events = conn.execute(
            "SELECT order_pk, from_status, to_status, source FROM order_events ORDER BY id"
        ).fetchall()
        assert events == [(1, None, "Recibido", "migracion"), (2, None, "Recibido", "migracion"),
                          (2, "Recibido", "Preparado", "migracion"), (3, None, "Recibido", "migracion"),
                          (3, "Recibido", "Cancelado", "migracion")]
        assert "items" not in {row[1] for row in conn.execute("PRAGMA table_info(orders)")}


def test_migracion_idempotente_y_igual_a_base_nueva(tmp_path):
    """CA-89: migrar dos veces no duplica nada, y la base migrada tiene el mismo esquema que una nueva."""
    vieja, nueva = tmp_path / "vieja.db", tmp_path / "nueva.db"
    _base_0_1_1(vieja)
    engine_vieja = make_engine(f"sqlite:///{vieja}")
    init_db(engine_vieja)
    init_db(engine_vieja)
    engine_nueva = make_engine(f"sqlite:///{nueva}")
    init_db(engine_nueva)

    with sqlite3.connect(vieja) as conn:
        assert conn.execute("SELECT COUNT(*) FROM order_items").fetchone() == (4,)
        assert conn.execute("SELECT version FROM schema_version").fetchall() == [(LATEST,)]
    assert _esquema(engine_vieja) == _esquema(engine_nueva)


# --- Pedidos nuevos: del agente a la base -------------------------------------------------------------------

SKU_TINTURA = "TIN-SINT-1"
SKU_SHAMPOO = "SH-SINT-1"
NOMBRE = "Marta Gómez"
OPERATOR_TOKEN = "token-operador-prueba"
AUTH = {"X-Operator-Token": OPERATOR_TOKEN}


@pytest.fixture
def catalogo(monkeypatch):
    productos = [
        Product(sku=SKU_TINTURA, name="Tintura Sintética", presentation="kit", price=Decimal("1000"), stock=6),
        Product(sku=SKU_SHAMPOO, name="Shampoo Sintético", presentation="400 ml", price=Decimal("250.50"), stock=10),
    ]
    monkeypatch.setattr(sheets_service, "get_catalog", lambda: productos)


@pytest.fixture(autouse=True)
def base_limpia(monkeypatch):
    monkeypatch.setenv("OPERATOR_TOKEN", OPERATOR_TOKEN)
    get_settings.cache_clear()
    with SessionLocal() as session:
        for model in (OrderRecord, DraftRecord, ConversationRecord, ProcessedMessageRecord):
            session.execute(delete(model))
        session.commit()
    yield
    get_settings.cache_clear()


def _call(name, **args):
    return {"name": name, "args": args, "id": f"call-{name}", "type": "tool_call"}


def _ai(*calls):
    return AIMessage(content="", tool_calls=list(calls))


class ModeloGuionado:
    def __init__(self, *guion):
        self.guion = list(guion)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return self.guion.pop(0)


PROPUESTA_CON_NOTAS = _call(
    "respond_to_customer",
    kind="propuesta",
    message="Te armé el pedido",
    propuesta=[
        {"sku": SKU_TINTURA, "quantity": 2, "note": "tono 7.1"},
        {"sku": SKU_TINTURA, "quantity": 1, "note": "tono  8.21 "},
        {"sku": SKU_SHAMPOO, "quantity": 2},
    ],
    notes="Retira el sábado",
)


def _draft(*lineas, draft_id="d-guardado", version=1, conversation_id=None, notes=None) -> OrderDraft:
    return OrderDraft(
        draft_id=draft_id,
        version=version,
        customer_id="telegram:1",
        customer_name=NOMBRE,
        channel=Channel.TELEGRAM,
        conversation_id=conversation_id,
        notes=notes,
        items=[
            OrderItem(sku=sku, product_name="Producto", quantity=cantidad, unit_price=Decimal("10"), note=nota)
            for sku, cantidad, nota in lineas
        ],
    )


def test_orden_se_reconstruye_desde_la_base(catalogo, monkeypatch, caplog):
    """CA-81: desde la base, sin el checkpoint, se reconstruye la orden entera; el nombre no va a los logs."""
    agent = NormaAgent(ModeloGuionado(_ai(_call("leer_skill", nombre="tomar_pedido")), _ai(PROPUESTA_CON_NOTAS)),
                       InMemorySaver())
    monkeypatch.setattr(conversation_service, "get_agent", lambda: agent)
    caplog.set_level(logging.DEBUG)

    def enviar(texto):
        return conversation_service.handle_message(
            IncomingMessage(customer_id="telegram:1", customer_name=NOMBRE, channel=Channel.TELEGRAM, text=texto)
        )

    enviar("quiero tintura 7/2 y 821/1 y dos shampoo, retiro el sábado")
    final = enviar("OK")
    assert "Tu número es" in final.text

    with SessionLocal() as session:
        conversation_id = session.scalar(select(ConversationRecord.id))
    # Una sesión nueva sobre la base: nada viene del grafo
    order = order_service.get_order(session_order_id())
    assert order.customer_id == "telegram:1"
    assert order.customer_name == NOMBRE
    assert order.conversation_id == conversation_id
    assert [(i.sku, i.quantity, i.note) for i in order.items] == [
        (SKU_TINTURA, 2, "tono 7.1"),
        (SKU_TINTURA, 1, "tono 8.21"),
        (SKU_SHAMPOO, 2, None),
    ]
    assert order.notes == "Retira el sábado"
    assert order.total == Decimal("3501.00")
    assert [(e.from_status, e.to_status, e.source) for e in order.history] == [
        (None, OrderStatus.RECEIVED, "agente")
    ]
    assert NOMBRE not in caplog.text and "Gómez" not in caplog.text


def session_order_id() -> str:
    with SessionLocal() as session:
        return session.scalar(select(OrderRecord.order_id))


def test_notas_llegan_a_la_orden_y_al_mensaje(catalogo):
    """CA-82: las notas llegan al borrador, a la orden y a los mensajes que ve el cliente."""
    agent = NormaAgent(ModeloGuionado(_ai(_call("leer_skill", nombre="tomar_pedido")), _ai(PROPUESTA_CON_NOTAS)),
                       InMemorySaver())
    propuesta = agent.handle("1", customer_id="telegram:1", channel=Channel.TELEGRAM, text="hola", customer_name=NOMBRE)
    assert propuesta.draft.notes == "Retira el sábado"
    assert propuesta.draft.customer_name == NOMBRE
    assert propuesta.draft.conversation_id == 1
    assert "• 2 x Tintura Sintética kit ($1000.00 c/u = $2000.00) — tono 7.1" in propuesta.message
    assert "Observaciones: Retira el sábado" in propuesta.message

    pedido = agent.handle("1", customer_id="telegram:1", channel=Channel.TELEGRAM, text="OK")
    assert "— tono 8.21" in pedido.message
    assert "Observaciones: Retira el sábado" in pedido.message
    assert order_service.get_order(pedido.order_id).notes == "Retira el sábado"


def test_lineas_del_mismo_sku_con_igual_nota_se_suman(catalogo):
    """CA-83: líneas del mismo SKU con la misma nota se suman; con notas distintas quedan separadas."""
    items = normalize_items([
        ProposedItem(sku=SKU_TINTURA, quantity=1, note="tono 7"),
        ProposedItem(sku=SKU_TINTURA, quantity=2, note=" tono   7 "),
        ProposedItem(sku=SKU_TINTURA, quantity=1, note="tono 8"),
        ProposedItem(sku=SKU_SHAMPOO, quantity=1),
        ProposedItem(sku=SKU_SHAMPOO, quantity=1, note=""),
    ])
    assert [(i.sku, i.quantity, i.note) for i in items] == [
        (SKU_TINTURA, 3, "tono 7"),
        (SKU_TINTURA, 1, "tono 8"),
        (SKU_SHAMPOO, 2, None),
    ]


def test_lineas_del_mismo_sku_controlan_el_stock_sumadas(catalogo):
    """CA-83: el stock se controla con la suma de las líneas de un SKU, al proponer y al confirmar."""
    with pytest.raises(ToolFailure) as exc:
        normalize_items([
            ProposedItem(sku=SKU_TINTURA, quantity=4, note="tono 7"),
            ProposedItem(sku=SKU_TINTURA, quantity=3, note="tono 8"),
        ])
    assert exc.value.code == "stock_insuficiente"
    assert exc.value.details["lineas"] == [{"sku": SKU_TINTURA, "requested": 7, "available": 6}]

    with pytest.raises(order_service.InsufficientStockError):
        order_service.confirm_draft(_draft((SKU_TINTURA, 4, "tono 7"), (SKU_TINTURA, 3, "tono 8")))
    assert session_order_id() is None


def test_stock_comprometido_sale_de_order_items(catalogo):
    """CA-84: el stock comprometido suma todas las líneas de un SKU y no cuenta las órdenes canceladas."""
    activa = order_service.confirm_draft(_draft((SKU_TINTURA, 2, "tono 7"), (SKU_TINTURA, 1, "tono 8"), draft_id="a"))
    cancelada = order_service.confirm_draft(_draft((SKU_SHAMPOO, 4, None), draft_id="b"))
    order_service.cancel_order(cancelada.order_id)

    assert inventory_service.get_committed_stock() == {SKU_TINTURA: 3}
    assert inventory_service.get_available_stock(SKU_TINTURA) == 3
    assert inventory_service.get_available_stock(SKU_SHAMPOO) == 10
    order_service.cancel_order(activa.order_id)
    assert inventory_service.get_committed_stock() == {}


def _drafts() -> list[tuple]:
    with SessionLocal() as session:
        rows = session.scalars(select(DraftRecord).order_by(DraftRecord.id)).all()
        return [(r.draft_id, r.version, r.conversation_id, r.status, r.order_id) for r in rows]


def _responder(vigente, *lineas):
    """Una llamada a respond_to_customer por el registro de tools, como en un turno del agente."""
    ctx = tool_registry.TurnContext(
        customer_id="telegram:1",
        channel=Channel.TELEGRAM,
        disponibles=["respond_to_customer"],
        customer_name=NOMBRE,
        conversation_id=7,
        propuesta=vigente,
    )
    args = {
        "kind": "propuesta" if lineas else "informacion",
        "message": "ok",
        "propuesta": [{"sku": sku, "quantity": q} for sku, q in lineas] or None,
    }
    resultado = tool_registry.execute("respond_to_customer", args, ctx)
    return ctx.propuesta, resultado


def test_drafts_registra_cada_version(catalogo):
    """CA-85: cada versión queda en drafts con su conversación y su desenlace."""

    def responder(vigente, *lineas):
        return _responder(vigente, *lineas)[0]

    v1 = responder(None, (SKU_SHAMPOO, 1))
    v2 = responder(v1, (SKU_SHAMPOO, 2))
    assert responder(v2) is None  # el agente respondió sin propuesta: la vigente se descarta
    otra = responder(None, (SKU_TINTURA, 1))
    order = order_service.confirm_draft(otra)

    assert _drafts() == [
        (v1.draft_id, 1, 7, "reemplazado", None),
        (v1.draft_id, 2, 7, "descartado", None),
        (otra.draft_id, 1, 7, "confirmado", order.order_id),
    ]
    with SessionLocal() as session:  # el nombre del cliente no se guarda en drafts (D-C4)
        assert NOMBRE not in json.dumps([r.items for r in session.scalars(select(DraftRecord))])


def test_drafts_version_repetida_queda_con_las_ultimas_lineas(catalogo, monkeypatch):
    """CA-85: una respuesta rechazada no toca drafts, y una versión pendiente repetida queda con las últimas líneas."""
    v1, _ = _responder(None, (SKU_SHAMPOO, 1))

    # Se rechaza después de armar la propuesta, como un control de la salida: ni versión nueva ni la vigente cambia
    def rechazar(_):
        raise RuntimeError("respuesta rechazada")

    with monkeypatch.context() as m:
        m.setattr(tool_registry.validation_service, "build_proposal_detail", rechazar)
        vigente, resultado = _responder(v1, (SKU_SHAMPOO, 2))
        assert resultado["ok"] is False and vigente == v1
    assert _drafts() == [(v1.draft_id, 1, 7, "pendiente_confirmacion", None)]

    # El reintento después de un rechazo propone otra vez la versión 1, con otras líneas y notas
    reintento = _draft((SKU_TINTURA, 2, "tono 7"), draft_id=v1.draft_id, notes="Retira el lunes")
    order_service.record_draft(reintento)
    with SessionLocal() as session:
        fila = session.scalars(select(DraftRecord)).one()
        assert (fila.version, fila.status) == (1, "pendiente_confirmacion")
        assert [(i["sku"], i["quantity"], i["note"]) for i in fila.items] == [(SKU_TINTURA, 2, "tono 7")]
        assert (fila.notes, fila.total) == ("Retira el lunes", Decimal("20"))

    # Una versión ya confirmada no cambia aunque se registre de nuevo
    order = order_service.confirm_draft(reintento)
    order_service.record_draft(_draft((SKU_SHAMPOO, 5, None), draft_id=v1.draft_id))
    with SessionLocal() as session:
        fila = session.scalars(select(DraftRecord)).one()
        assert (fila.status, fila.order_id, fila.total) == ("confirmado", order.order_id, Decimal("20"))
        assert [i["sku"] for i in fila.items] == [SKU_TINTURA]


client = TestClient(app)


def test_operador_cambia_el_estado(catalogo):
    """CA-86: el operador pasa la orden a Preparado y Retirado, o la cancela, y cada cambio queda en el historial."""
    order = order_service.confirm_draft(_draft((SKU_SHAMPOO, 1, None), draft_id="e1"))
    resp = client.post(f"/orders/{order.order_id}/status", json={"status": "preparado"}, headers=AUTH)
    assert resp.status_code == 200
    resp = client.post(f"/orders/{order.order_id}/status", json={"status": "Retirado"}, headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["status"] == "Retirado"
    assert [(e["from_status"], e["to_status"], e["source"]) for e in resp.json()["history"]] == [
        (None, "Recibido", "agente"),
        ("Recibido", "Preparado", "operador"),
        ("Preparado", "Retirado", "operador"),
    ]

    otra = order_service.confirm_draft(_draft((SKU_SHAMPOO, 1, None), draft_id="e2"))
    assert client.post(f"/orders/{otra.order_id}/cancel", headers=AUTH).status_code == 200
    detalle = client.get(f"/orders/{otra.order_id}", headers=AUTH).json()
    assert detalle["history"][-1]["to_status"] == "Cancelado"
    assert detalle["customer_name"] == NOMBRE
    assert Decimal(detalle["total"]) == Decimal("10")


@pytest.mark.parametrize(
    ("headers", "order_id", "status", "codigo"),
    [
        ({}, None, "Preparado", 401),
        (AUTH, "ORD-9999", "Preparado", 404),
        (AUTH, None, "Enviado", 422),
        (AUTH, None, "Retirado", 400),
    ],
    ids=["sin-credencial", "orden-inexistente", "estado-desconocido", "transicion-invalida"],
)
def test_cambio_de_estado_rechazado_sin_cambios(catalogo, headers, order_id, status, codigo):
    """CA-87: credencial, orden, estado o transición inválidos se rechazan y la orden no cambia."""
    order = order_service.confirm_draft(_draft((SKU_SHAMPOO, 1, None), draft_id="e3"))
    resp = client.post(f"/orders/{order_id or order.order_id}/status", json={"status": status}, headers=headers)
    assert resp.status_code == codigo
    sin_cambios = order_service.get_order(order.order_id)
    assert sin_cambios.status == OrderStatus.RECEIVED
    assert len(sin_cambios.history) == 1


def test_migracion_concurrente(tmp_path):
    """CA-90: si la API y el bot arrancan a la vez, la migración corre una sola vez y las dos terminan bien."""
    path = tmp_path / "norma.db"
    _base_0_1_1(path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        versiones = list(pool.map(lambda _: init_db(make_engine(f"sqlite:///{path}")), range(2)))
    assert versiones == [LATEST, LATEST]
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM order_items").fetchone() == (4,)
        assert conn.execute("SELECT COUNT(*) FROM order_events").fetchone() == (5,)


def test_update_repetido_tras_reinicio():
    """CA-91: un update de Telegram ya procesado no se procesa de nuevo aunque el proceso se reinicie."""
    assert telegram_service.processed_updates.first_time(4242)
    assert not telegram_service.processed_updates.first_time(4242)
    reiniciado = telegram_service._ProcessedUpdates()  # lo que queda después de reiniciar: solo la base
    assert not reiniciado.first_time(4242)
    assert reiniciado.first_time(4243)
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ProcessedMessageRecord)) == 2


def test_fila_de_pedido_con_columnas_nuevas():
    """CA-92: la fila trae nombre y total al final, y la nota de cada línea entre corchetes."""
    order = Order(
        order_id="ORD-0001",
        draft_id="d-1",
        created_at=datetime(2026, 11, 10, 15, 0, tzinfo=UTC),
        customer_id="telegram:1",
        customer_name=NOMBRE,
        channel=Channel.TELEGRAM,
        items=[
            OrderItem(sku=SKU_TINTURA, product_name="Tintura", presentation="kit", quantity=2,
                      unit_price=Decimal("1000"), note="tono 7.1; el claro"),
            OrderItem(sku=SKU_SHAMPOO, product_name="Shampoo", quantity=1, unit_price=Decimal("250.5")),
        ],
        notes="Retira el sábado",
    )
    assert sheets_service.ORDER_COLUMNS[-2:] == ["nombre_cliente", "total"]
    assert sheets_service.format_order_row(order) == [
        "ORD-0001",
        "2026-11-10T12:00:00-03:00",
        "telegram:1",
        "telegram",
        f"2 x Tintura kit ({SKU_TINTURA}) [tono 7.1, el claro]; 1 x Shampoo ({SKU_SHAMPOO})",
        "Retira el sábado",
        "Recibido",
        NOMBRE,
        "2250.50",
    ]
