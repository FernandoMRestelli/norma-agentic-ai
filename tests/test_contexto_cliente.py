"""Contexto del cliente armado por el backend, sin LLM (spec del orquestador §3.2, RF-84, RF-85)."""

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from internal.agent import contexto_cliente, graph, observabilidad
from internal.schemas.catalog_schema import Product
from internal.services import order_service, sheets_service
from internal.services.sheets_service import AR_TZ
from internal.tools import order_tools
from tests.test_agent_graph import (  # noqa: F401 (fixtures)
    CLIENTE,
    GUION_OFERTA,
    SKU,
    _agente,
    _ai,
    _call,
    base_limpia,
)

PEDIDO_ANTERIOR = {"fecha": "2026-11-02", "lineas": [{"sku": SKU, "name": "Crema Nivea", "cantidad": 2}]}


def _saludo():
    """Un turno de saludo. Mensajes nuevos cada vez: LangGraph reemplaza un mensaje que ya tiene id."""
    return (
        _ai(_call("leer_skill", nombre="informar")),
        _ai(_call("respond_to_customer", kind="informacion", message="¡Buen día! ¿Qué te preparo?")),
    )


@pytest.fixture
def catalogo(monkeypatch):
    productos = [Product(sku=SKU, name="Crema Nivea", presentation="100 ml", price=Decimal("1000"), stock=3)]
    monkeypatch.setattr(sheets_service, "get_catalog", lambda: productos)
    return productos


@pytest.fixture
def historial(monkeypatch):
    """Cuenta cuántas veces se lee la base y devuelve un cliente con un pedido anterior."""
    lecturas = {"pedidos": 0, "comprados": 0}

    def pedidos(customer_id, limite=3):
        lecturas["pedidos"] += 1
        return [PEDIDO_ANTERIOR]

    def comprados(customer_id):
        lecturas["comprados"] += 1
        return frozenset({SKU})

    monkeypatch.setattr(order_tools, "pedidos_anteriores", pedidos)
    monkeypatch.setattr(order_service, "skus_comprados", comprados)
    return lecturas


@pytest.fixture
def spans(monkeypatch):
    nombres: list[str] = []
    original = observabilidad.paso

    @contextmanager
    def registrar(nombre, tipo="UNKNOWN", **atributos):
        nombres.append(nombre)
        with original(nombre, tipo, **atributos) as paso:
            yield paso

    monkeypatch.setattr(observabilidad, "paso", registrar)
    return nombres


def _estado(agent, thread="1"):
    return agent.graph.get_state({"configurable": {"thread_id": thread}}).values


def _system(modelo, llamada=-1) -> str:
    return modelo.recibidos[llamada][0].content


def _mover_reloj(monkeypatch, dias: int) -> None:
    momento = datetime.now(AR_TZ) + timedelta(days=dias)
    monkeypatch.setattr(contexto_cliente, "ahora", lambda: momento)


def test_primer_mensaje_del_dia_arma_el_contexto(catalogo, historial, spans):
    """CA-144: el primer mensaje lee la base una vez, guarda el contexto con la fecha y deja el span."""
    agent, modelo = _agente(*_saludo())

    agent.handle("1", text="hola", customer_name="Pedro", **CLIENTE)

    assert historial == {"pedidos": 1, "comprados": 1}
    contexto = _estado(agent)["contexto_cliente"]
    assert contexto["fecha"] == contexto_cliente.hoy()
    assert contexto["nombre"] == "Pedro"
    assert contexto["pedidos"] == [PEDIDO_ANTERIOR]
    assert contexto["comprados"] == [SKU]
    assert "contexto_cliente" in spans
    # Entra como un bloque de datos del prompt, no como una tool call puesta por el backend (RF-85)
    assert "## Contexto del cliente" in _system(modelo)
    assert f"2 x Crema Nivea ({SKU})" in _system(modelo)
    pedidas = [call["name"] for m in modelo.recibidos[0] for call in getattr(m, "tool_calls", [])]
    assert "pedidos_anteriores" not in pedidas


def test_segundo_mensaje_reusa_el_contexto(catalogo, historial, spans):
    """CA-145: otro mensaje del mismo día no lee la base y el prompt trae el mismo bloque."""
    agent, modelo = _agente(*_saludo(), *_saludo())

    agent.handle("1", text="hola", **CLIENTE)
    primero = _system(modelo)
    spans.clear()
    agent.handle("1", text="buenas", **CLIENTE)

    assert historial == {"pedidos": 1, "comprados": 1}
    assert "contexto_cliente" not in spans
    bloque = contexto_cliente.bloque(_estado(agent)["contexto_cliente"])
    assert bloque in primero
    assert bloque in _system(modelo)


def test_el_contexto_vence(catalogo, historial, monkeypatch):
    """CA-146: con el cambio de fecha, o después de registrar un pedido, el contexto se vuelve a armar."""
    agent, _ = _agente(*_saludo(), *_saludo())
    agent.handle("1", text="hola", **CLIENTE)

    _mover_reloj(monkeypatch, dias=1)
    agent.handle("1", text="buenas", **CLIENTE)
    assert historial["pedidos"] == 2
    assert _estado(agent)["contexto_cliente"]["fecha"] == contexto_cliente.hoy()

    # Un pedido registrado: «dale» confirma sin LLM, el contexto se invalida y el próximo mensaje lo rehace
    agent, _ = _agente(*GUION_OFERTA, *_saludo())
    agent.handle("2", text="te encargo 5 cremas", **CLIENTE)
    lecturas = historial["pedidos"]
    confirmado = agent.handle("2", text="dale", **CLIENTE)
    assert confirmado.order_id
    assert _estado(agent, "2")["contexto_cliente"] is None
    agent.handle("2", text="hola de nuevo", **CLIENTE)
    assert historial["pedidos"] == lecturas + 1


def test_si_falla_la_base_sigue_sin_contexto(catalogo, monkeypatch, caplog):
    """CA-147: si la base falla, el turno sigue sin pedidos, con un warning sin texto del cliente."""

    def falla(*args, **kwargs):
        raise RuntimeError("base caída con datos de Pedro")

    monkeypatch.setattr(order_tools, "pedidos_anteriores", falla)
    agent, modelo = _agente(*_saludo(), *_saludo())

    with caplog.at_level(logging.WARNING):
        outcome = agent.handle("1", text="hola soy Pedro", **CLIENTE)

    assert outcome.kind == "informacion"
    contexto = _estado(agent)["contexto_cliente"]
    assert contexto["pedidos"] == [] and contexto["comprados"] == []
    assert not contexto["completo"]
    avisos = [r.getMessage() for r in caplog.records if "contexto" in r.getMessage()]
    assert avisos and all("Pedro" not in aviso for aviso in avisos)
    assert "No se pudo leer su historial" in _system(modelo)

    # El próximo mensaje vuelve a intentar
    lecturas = []
    monkeypatch.setattr(order_tools, "pedidos_anteriores", lambda *a, **k: lecturas.append(1) or [])
    agent.handle("1", text="buenas", **CLIENTE)
    assert lecturas == [1]
    assert _estado(agent)["contexto_cliente"]["completo"]


def test_las_busquedas_usan_los_comprados_del_contexto(catalogo, historial):
    """`comprados` sale del contexto: buscar no vuelve a leer la base (RF-73)."""
    agent, _ = _agente(
        _ai(_call("search_products", query="crema")),
        *_saludo(),
    )
    agent.handle("1", text="tenés crema?", **CLIENTE)
    assert historial["comprados"] == 1


def test_un_checkpoint_sin_contexto_lo_arma(catalogo, historial):
    """Una conversación abierta antes del cambio no tiene el campo: arranca sin contexto y lo arma (§7)."""
    assert not contexto_cliente.vigente(None)
    estado = graph.armar_contexto_cliente({"customer_id": "telegram:1", "customer_name": None})
    assert estado["contexto_cliente"]["completo"]
