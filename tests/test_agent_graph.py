"""Loop del agente con un modelo guionado: sin red y sin LLM real (spec del agente §10)."""

import json
import sqlite3
from decimal import Decimal

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy import delete, func, select

from internal.agent import context, graph, norma_agent, tool_registry
from internal.agent.graph import FAILURE_MESSAGE, MAX_ITERATIONS
from internal.agent.norma_agent import NormaAgent
from internal.database.connection import SessionLocal, init_db
from internal.database.models import ConversationRecord, OrderRecord
from internal.schemas.catalog_schema import Product
from internal.schemas.order_schema import Channel, IncomingMessage
from internal.services import conversation_service, order_service, sheets_service, skill_service

SKU = "CR-NIV-100"
CLIENTE = {"customer_id": "telegram:1", "channel": Channel.TELEGRAM}


def _call(name, **args):
    return {"name": name, "args": args, "id": f"call-{name}", "type": "tool_call"}


def _ai(*calls):
    return AIMessage(content="", tool_calls=list(calls))


def _propuesta(mensaje, sku, cantidad):
    propuesta = [{"sku": sku, "quantity": cantidad}]
    return _call("respond_to_customer", kind="propuesta", message=mensaje, propuesta=propuesta)


class ModeloGuionado:
    """Devuelve las respuestas en orden y registra qué tools le ofrecieron en cada llamada."""

    def __init__(self, *guion):
        self.guion = list(guion)
        self.ofrecidas: list[list[str]] = []
        self.recibidos: list[list] = []
        self._tools: list[str] = []

    def bind_tools(self, tools, **kwargs):
        self._tools = [tool["function"]["name"] for tool in tools]
        self.tool_choice = kwargs.get("tool_choice")
        return self

    def invoke(self, messages):
        self.ofrecidas.append(self._tools)
        self.recibidos.append(messages)
        respuesta = self.guion.pop(0)
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta


GUION_OFERTA = (
    _ai(_call("search_products", query="crema")),
    _ai(_call("leer_skill", nombre="tomar_pedido")),
    _ai(_call("leer_br", id="BR-01")),
    _ai(
        _call(
            "respond_to_customer",
            kind="sin_stock",
            message="Hola Pedro, solo tengo 3, ¿te armo el pedido igual?",
            propuesta=[{"sku": SKU, "quantity": 3}],
        )
    ),
)
GUION_ACEPTA = (_ai(_call("leer_skill", nombre="confirmar_pedido")), _ai(_call("armar_pedido")))
OFERTA_CON_DETALLE = (
    "Hola Pedro, solo tengo 3, ¿te armo el pedido igual?\n\n"
    "• 3 x Crema Nivea 100 ml ($1000.00 c/u = $3000.00)\nTotal estimado: $3000.00"
)


@pytest.fixture
def catalogo(monkeypatch):
    productos = [Product(sku=SKU, name="Crema Nivea", presentation="100 ml", price=Decimal("1000"), stock=3)]
    monkeypatch.setattr(sheets_service, "get_catalog", lambda: productos)
    return productos


@pytest.fixture(autouse=True)
def base_limpia():
    init_db()
    with SessionLocal() as session:
        session.execute(delete(OrderRecord))
        session.execute(delete(ConversationRecord))
        session.commit()


def _ordenes() -> int:
    with SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(OrderRecord))


def _agente(*guion):
    modelo = ModeloGuionado(*guion)
    return NormaAgent(modelo, InMemorySaver()), modelo


def _resultados(agent, nombre, thread="1"):
    mensajes = agent.graph.get_state({"configurable": {"thread_id": thread}}).values["messages"]
    return [json.loads(m.content) for m in mensajes if isinstance(m, ToolMessage) and m.name == nombre]


def test_conversacion_completa(catalogo):
    """CA-41: el primer turno ofrece lo disponible sin crear nada; «dale» crea la orden con 3 y cierra."""
    agent, _ = _agente(*GUION_OFERTA, *GUION_ACEPTA)

    primero = agent.handle("1", text="Hola Juan, te encargo 5 cremas", **CLIENTE)
    assert primero.kind == "sin_stock"
    assert primero.message == OFERTA_CON_DETALLE
    assert primero.iterations == 4
    assert not primero.cerrada
    assert primero.draft.items[0].quantity == 3
    assert _ordenes() == 0

    segundo = agent.handle("1", text="sí, dale", **CLIENTE)
    assert segundo.kind == "pedido"
    assert segundo.cerrada
    assert segundo.order_id
    assert "3 x Crema Nivea 100 ml" in segundo.message
    assert segundo.order_id in segundo.message
    orden = order_service.get_order(segundo.order_id)
    assert [(item.sku, item.quantity) for item in orden.items] == [(SKU, 3)]
    assert orden.customer_id == CLIENTE["customer_id"]


def test_rechazo_no_crea_orden_y_descarta_la_propuesta(catalogo):
    """CA-17: «no, gracias» no registra, y un «dale» posterior no revive la propuesta (CA-42)."""
    agent, _ = _agente(
        *GUION_OFERTA,
        _ai(_call("leer_skill", nombre="confirmar_pedido")),
        _ai(_call("respond_to_customer", kind="informacion", message="Dale, cualquier cosa me avisás")),
        *GUION_ACEPTA,
        AIMessage(content="No tengo ninguna propuesta"),
    )
    agent.handle("1", text="5 cremas", **CLIENTE)
    rechazo = agent.handle("1", text="no, gracias", **CLIENTE)
    assert rechazo.draft is None
    assert _ordenes() == 0

    agent.handle("1", text="sí, dale", **CLIENTE)
    assert _resultados(agent, "armar_pedido")[-1]["error"]["code"] == "sin_propuesta"
    assert _ordenes() == 0


def test_correccion_sube_la_version_del_mismo_borrador(catalogo):
    """BR-05 y CA-17: «dale, pero 2» es una propuesta nueva, no un pedido."""
    corregir = _propuesta("¿Te armo 2?", SKU, 2)
    agent, _ = _agente(*GUION_OFERTA, _ai(_call("leer_skill", nombre="tomar_pedido")), _ai(corregir))
    primero = agent.handle("1", text="5 cremas", **CLIENTE)
    segundo = agent.handle("1", text="dale, pero 2", **CLIENTE)
    assert segundo.kind == "propuesta"
    assert segundo.draft.draft_id == primero.draft.draft_id
    assert segundo.draft.version == 2
    assert segundo.draft.items[0].quantity == 2
    assert _ordenes() == 0


def test_pregunta_sobre_la_propuesta_la_mantiene_vigente(catalogo):
    """CA-47: responder con las mismas líneas la mantiene, y al aceptar se crea una sola orden."""
    agent, _ = _agente(
        *GUION_OFERTA,
        _ai(_call("leer_skill", nombre="confirmar_pedido")),
        _ai(_propuesta("Sale $1000 cada una. ¿Te armo el pedido?", SKU, 3)),
        *GUION_ACEPTA,
    )
    agent.handle("1", text="te encargo 5 cremas", **CLIENTE)
    pregunta = agent.handle("1", text="¿cuánto sale?", **CLIENTE)
    assert pregunta.kind == "propuesta"
    assert pregunta.draft.version == 2
    assert _ordenes() == 0

    final = agent.handle("1", text="sí, dale", **CLIENTE)
    assert final.kind == "pedido"
    assert _ordenes() == 1
    assert order_service.get_order(final.order_id).items[0].quantity == 3


@pytest.mark.parametrize("skill", ["tomar_pedido", "informar"])
def test_cada_skill_habilita_una_respuesta(catalogo, skill):
    agent, modelo = _agente(
        _ai(_call("leer_skill", nombre=skill)),
        _ai(_call("respond_to_customer", kind="informacion", message="Hola, ¿qué querés pedir?")),
    )
    resultado = agent.handle("1", text="necesito hacer una consulta", **CLIENTE)  # ninguna skill es obvia
    assert "respond_to_customer" not in modelo.ofrecidas[0]
    assert "respond_to_customer" in modelo.ofrecidas[1]
    assert "armar_pedido" not in modelo.ofrecidas[1]
    assert resultado.kind == "informacion"


def test_estado_muestra_la_presentacion_de_la_propuesta(catalogo):
    agent, modelo = _agente(*GUION_OFERTA, _ai(_call("search_products", query="crema")), AIMessage(content="ok"))
    agent.handle("1", text="te encargo 5 cremas", **CLIENTE)
    agent.handle("1", text="¿es la de 100?", **CLIENTE)
    assert f"3 x Crema Nivea 100 ml ({SKU})" in modelo.recibidos[-1][0].content


@pytest.mark.parametrize("texto", ["ok", "OK", "  Confirmar\n", "dale", "Sí", "¡Listo!", "de una", "mandale."])
def test_ok_exacto_registra_la_propuesta_sin_llamar_al_llm(catalogo, texto):
    """CA-18 y CA-120: la propuesta se registra y el modelo no vuelve a recibir nada."""
    agent, modelo = _agente(*GUION_OFERTA)
    agent.handle("1", text="te encargo 5 cremas", **CLIENTE)
    llamadas = len(modelo.recibidos)

    final = agent.handle("1", text=texto, **CLIENTE)

    assert final.kind == "pedido"
    assert final.cerrada
    assert len(modelo.recibidos) == llamadas
    assert _ordenes() == 1
    assert order_service.get_order(final.order_id).items[0].quantity == 3


def test_ok_sin_stock_lo_resuelve_el_agente(catalogo):
    """CA-18: si ya no alcanza no se crea la orden, y el agente ofrece lo que queda."""
    agent, modelo = _agente(
        *GUION_OFERTA,
        _ai(_call("leer_skill", nombre="tomar_pedido")),
        _ai(_call("respond_to_customer", kind="sin_stock", message="Ahora queda 1. ¿Te lo armo?",
                  propuesta=[{"sku": SKU, "quantity": 1}])),
    )
    agent.handle("1", text="te encargo 5 cremas", **CLIENTE)
    catalogo[0].stock = 1

    respuesta = agent.handle("1", text="ok", **CLIENTE)

    assert respuesta.kind == "sin_stock"
    assert respuesta.draft.items[0].quantity == 1
    assert _ordenes() == 0
    assert modelo.recibidos[-1][-1].type == "tool"


def test_ok_sin_propuesta_va_al_agente(catalogo):
    """CA-18: sin propuesta vigente no hay nada que registrar."""
    def informar():  # mensajes distintos: add_messages reemplaza a uno con el mismo id
        return (
            _ai(_call("leer_skill", nombre="informar")),
            _ai(_call("respond_to_customer", kind="informacion", message="¿Qué querés pedir?")),
        )

    agent, modelo = _agente(*informar(), *informar())
    agent.handle("1", text="hola", **CLIENTE)
    respuesta = agent.handle("1", text="ok", **CLIENTE)
    assert respuesta.kind == "informacion"
    assert len(modelo.recibidos) == 4
    assert _ordenes() == 0


def test_corregir_va_al_agente(catalogo):
    agent, modelo = _agente(
        *GUION_OFERTA,
        _ai(_call("leer_skill", nombre="tomar_pedido")),
        _ai(_call("respond_to_customer", kind="aclaracion", message="¿Qué querés cambiar?", options=[SKU])),
    )
    agent.handle("1", text="te encargo 5 cremas", **CLIENTE)
    respuesta = agent.handle("1", text="CORREGIR", **CLIENTE)
    assert respuesta.kind == "aclaracion"
    assert _ordenes() == 0


def test_aceptar_registra_la_ultima_version(catalogo):
    """CA-14: después de una corrección, se registra lo último que se propuso."""
    agent, _ = _agente(
        *GUION_OFERTA,
        _ai(_call("leer_skill", nombre="tomar_pedido")),
        _ai(_propuesta("Quedan 2. ¿Te lo armo?", SKU, 2)),
    )
    agent.handle("1", text="te encargo 5 cremas", **CLIENTE)
    corregida = agent.handle("1", text="mejor 2", **CLIENTE)
    assert corregida.draft.version == 2

    final = agent.handle("1", text="ok", **CLIENTE)
    assert order_service.get_order(final.order_id).items[0].quantity == 2


def test_texto_sin_tools_descarta_la_propuesta(catalogo):
    """Regla 3 del loop: lo último que se le dijo al cliente ya no es la propuesta."""
    agent, _ = _agente(*GUION_OFERTA, AIMessage(content="Dale, avisame."))
    agent.handle("1", text="te encargo 5 cremas", **CLIENTE)
    respuesta = agent.handle("1", text="lo pienso", **CLIENTE)
    assert respuesta.draft is None
    assert _ordenes() == 0


def test_checkpoints_esperan_si_la_base_esta_ocupada(monkeypatch):
    """Sin esto, otro proceso escribiendo hace fallar el turno con «database is locked» a los 5 s."""
    recibido = {}
    conectar = sqlite3.connect

    def espia(path, **kwargs):
        recibido.update(kwargs)
        return conectar(":memory:", check_same_thread=False)

    monkeypatch.setattr(norma_agent.sqlite3, "connect", espia)
    monkeypatch.setattr(norma_agent, "get_chat_model", ModeloGuionado)
    norma_agent.get_agent.cache_clear()
    try:
        norma_agent.get_agent()
    finally:
        norma_agent.get_agent.cache_clear()
    assert recibido["timeout"] == norma_agent.CHECKPOINT_BUSY_TIMEOUT


def test_propuesta_con_mas_stock_del_disponible_vuelve_al_llm(catalogo):
    """CA-12: no se guarda una propuesta sin stock."""
    agent, _ = _agente(
        _ai(_call("leer_skill", nombre="tomar_pedido")),
        _ai(_propuesta("¿Te armo 5?", SKU, 5)),
        AIMessage(content="Solo tengo 3"),
    )
    resultado = agent.handle("1", text="5 cremas", **CLIENTE)
    assert _resultados(agent, "respond_to_customer")[0]["error"]["code"] == "stock_insuficiente"
    assert resultado.draft is None


def test_propuesta_con_sku_inventado_vuelve_al_llm(catalogo):
    """CA-12 / RF-08."""
    agent, _ = _agente(
        _ai(_call("leer_skill", nombre="tomar_pedido")),
        _ai(_propuesta("¿Te armo?", "XX-000", 1)),
        AIMessage(content="No lo tengo"),
    )
    agent.handle("1", text="1 cosa", **CLIENTE)
    assert _resultados(agent, "respond_to_customer")[0]["error"]["code"] == "sku_inexistente"


def test_armar_pedido_sin_leer_la_skill_no_se_ejecuta(catalogo):
    """CA-37."""
    agent, _ = _agente(_ai(_call("armar_pedido")), AIMessage(content="¿Qué necesitás?"))
    agent.handle("1", text="sí, dale", **CLIENTE)
    assert _resultados(agent, "armar_pedido")[0]["error"]["code"] == "tool_no_permitida"
    assert _ordenes() == 0


def test_armar_pedido_sin_propuesta(catalogo):
    """CA-42."""
    agent, _ = _agente(*GUION_ACEPTA, AIMessage(content="¿Qué necesitás?"))
    agent.handle("1", text="sí, dale", **CLIENTE)
    assert _resultados(agent, "armar_pedido")[0]["error"]["code"] == "sin_propuesta"
    assert _ordenes() == 0


def test_armar_pedido_ignora_productos_que_mande_el_llm(catalogo):
    """RNF-02: el LLM no elige qué se registra."""
    agent, _ = _agente(
        *GUION_OFERTA,
        _ai(_call("leer_skill", nombre="confirmar_pedido")),
        _ai(_call("armar_pedido", items=[{"sku": SKU, "quantity": 999}])),
    )
    agent.handle("1", text="5 cremas", **CLIENTE)
    resultado = agent.handle("1", text="sí, dale", **CLIENTE)
    assert [item.quantity for item in order_service.get_order(resultado.order_id).items] == [3]


def test_stock_que_baja_antes_de_aceptar_no_crea_orden(catalogo, monkeypatch):
    """CA-43 / RF-14."""
    agent, _ = _agente(*GUION_OFERTA, *GUION_ACEPTA, AIMessage(content="Ya no me queda"))
    agent.handle("1", text="5 cremas", **CLIENTE)
    sin_stock = [catalogo[0].model_copy(update={"stock": 1})]
    monkeypatch.setattr(sheets_service, "get_catalog", lambda: sin_stock)
    resultado = agent.handle("1", text="sí, dale", **CLIENTE)
    assert _resultados(agent, "armar_pedido")[0]["error"]["code"] == "stock_insuficiente"
    assert resultado.order_id is None
    assert _ordenes() == 0


def test_reintento_no_duplica_la_orden(catalogo):
    """CA-15 / RF-15."""
    agent, _ = _agente(*GUION_OFERTA, *GUION_ACEPTA)
    primero = agent.handle("1", text="5 cremas", **CLIENTE)
    resultado = agent.handle("1", text="sí, dale", **CLIENTE)
    repetida = order_service.confirm_draft(primero.draft)
    assert repetida.order_id == resultado.order_id
    assert _ordenes() == 1


def test_indice_muestra_confirmar_pedido_solo_con_propuesta(catalogo):
    """CA-54: sin propuesta vigente no hay nada que confirmar."""
    agent, modelo = _agente(*GUION_OFERTA, *GUION_ACEPTA)
    agent.handle("1", text="5 cremas", **CLIENTE)
    agent.handle("1", text="sí, dale", **CLIENTE)
    indice_sin_propuesta = modelo.recibidos[0][0].content
    indice_con_propuesta = modelo.recibidos[4][0].content
    assert "- `tomar_pedido`:" in indice_sin_propuesta
    assert "- `informar`:" in indice_sin_propuesta
    assert "- `confirmar_pedido`:" not in indice_sin_propuesta
    assert "- `confirmar_pedido`:" in indice_con_propuesta


def test_indice_muestra_que_hace_cada_skill_y_cuando_usarla(catalogo):
    """El agente elige la skill con su función y su `cuando`, sin tener que leerla."""
    agent, modelo = _agente(
        _ai(_call("leer_skill", nombre="informar")),
        _ai(_call("respond_to_customer", kind="informacion", message="¡Hola! ¿En qué te ayudo?")),
    )
    assert agent.handle("1", text="hola", **CLIENTE).kind == "informacion"
    indice = modelo.recibidos[0][0].content
    for skill in skill_service.get_skills().values():
        if not skill.requiere_propuesta:
            assert f"- `{skill.nombre}`: {skill.funcion} Cuándo: {skill.cuando}" in indice


def test_tools_ofrecidas_dependen_de_la_skill_y_se_reinician(catalogo):
    """CA-38 y CA-39."""
    agent, modelo = _agente(*GUION_OFERTA, *GUION_ACEPTA)
    agent.handle("1", text="te encargo cremas", **CLIENTE)  # sin cantidad ni lista: elige el modelo
    assert "respond_to_customer" not in modelo.ofrecidas[0]
    assert "armar_pedido" not in modelo.ofrecidas[0]
    assert "respond_to_customer" in modelo.ofrecidas[2]

    agent.handle("1", text="sí dale, mandalo nomás", **CLIENTE)  # más de tres palabras: tampoco se preactiva
    assert "respond_to_customer" not in modelo.ofrecidas[4]  # primera llamada del turno nuevo
    assert "armar_pedido" in modelo.ofrecidas[5]


def test_lecturas_de_turnos_anteriores_no_se_reenvian(catalogo):
    """Spec del agente §8: la charla sigue, las tools de otro turno no."""
    agent, modelo = _agente(*GUION_OFERTA, *GUION_ACEPTA)
    agent.handle("1", text="5 cremas", **CLIENTE)
    agent.handle("1", text="sí, dale", **CLIENTE)
    primer_llamado_turno_2 = modelo.recibidos[4]
    assert not any(isinstance(m, ToolMessage) for m in primer_llamado_turno_2)
    textos = [m.text for m in primer_llamado_turno_2[1:]]
    assert textos == ["5 cremas", OFERTA_CON_DETALLE, "sí, dale"]
    assert "Propuesta vigente: 3 x Crema Nivea" in primer_llamado_turno_2[0].text


def test_el_mensaje_con_tools_vuelve_entero_al_modelo(catalogo):
    """CA-35: dentro del turno no se reconstruye, para conservar las firmas de Gemini."""
    firmado = AIMessage(
        content="",
        tool_calls=[_call("search_products", query="crema")],
        additional_kwargs={"__gemini_function_call_thought_signatures__": {"call-search_products": "firma"}},
    )
    agent, modelo = _agente(firmado, AIMessage(content="Hola"))
    agent.handle("1", text="hola", **CLIENTE)
    reenviado = next(m for m in modelo.recibidos[1] if isinstance(m, AIMessage))
    assert reenviado.additional_kwargs == firmado.additional_kwargs


def test_texto_sin_tools_pausa_como_informacion(catalogo):
    """Regla 3 del loop: no cierra la conversación."""
    agent, _ = _agente(AIMessage(content="¡Hola! ¿Qué necesitás?"))
    resultado = agent.handle("1", text="hola", **CLIENTE)
    assert resultado.kind == "informacion"
    assert not resultado.cerrada


def test_tope_de_iteraciones(catalogo):
    """CA-20: corta con mensaje fijo y suma una falla, sin cerrar la conversación."""
    agent, modelo = _agente(*[_ai(_call("get_product", sku=SKU)) for _ in range(MAX_ITERATIONS + 1)])
    resultado = agent.handle("1", text="hola", **CLIENTE)
    assert resultado.message == FAILURE_MESSAGE
    assert resultado.kind == "error"
    assert not resultado.cerrada
    assert len(modelo.ofrecidas) == MAX_ITERATIONS


def test_plazo_del_turno(catalogo, monkeypatch):
    """RNF-16: pasado el plazo total, el turno corta con el mensaje fijo aunque le queden iteraciones."""
    reloj = iter([0.0, 0.0, 61.0, 61.0])
    monkeypatch.setattr(graph, "_reloj", lambda: next(reloj, 61.0))
    agent, modelo = _agente(*[_ai(_call("get_product", sku=SKU)) for _ in range(3)])

    resultado = agent.handle("1", text="tenés cremas", **CLIENTE)

    assert resultado.message == FAILURE_MESSAGE
    assert resultado.kind == "error"
    assert len(modelo.ofrecidas) == 1


def test_tool_obligatoria(catalogo):
    """CA-116: cada llamada al modelo lo obliga a elegir una tool."""
    agent, modelo = _agente(*GUION_OFERTA)
    agent.handle("1", text="te encargo cremas", **CLIENTE)
    assert modelo.tool_choice == "any"


@pytest.mark.parametrize(
    ("texto", "hay_propuesta", "skill"),
    [
        ("Pantene\n2 sh 400\n1 acond", False, "tomar_pedido"),  # una lista
        ("5 cremas nivea", False, "tomar_pedido"),  # una cantidad
        ("hola, buen día", False, "informar"),
        ("muchas gracias!", False, "informar"),
        ("tenés crema nivea?", False, None),  # consulta sin cantidad: elige el modelo
        ("hola, tenés shampoo?", False, None),
        ("sí, dale", True, "confirmar_pedido"),
        ("no, gracias", True, "confirmar_pedido"),
        ("mmm dejame pensarlo", True, "confirmar_pedido"),
        ("dale, pero 2", True, None),  # cambia la propuesta
        ("sumale también un shampoo pantene", True, None),
        ("Pantene\n2 sh 400", True, None),
    ],
)
def test_skill_clara(texto, hay_propuesta, skill):
    """RF-62: el backend preactiva una skill solo cuando el mensaje no admite dudas."""
    assert graph.skill_clara(texto, hay_propuesta) == skill


def test_preactiva_tomar_pedido(catalogo):
    """CA-117: con una lista, la primera llamada ya tiene las tools de `tomar_pedido` y el turno cierra en una."""
    responder = _call(
        "respond_to_customer",
        kind="propuesta",
        message="Listo, te armé las cremas. El jabón no lo trabajamos. ¿Lo confirmo?",
        propuesta=[{"sku": SKU, "quantity": 2, "renglon": 1}],
        afuera=[{"renglon": 2, "motivo": "fuera_de_catalogo"}],
    )
    agent, modelo = _agente(_ai(responder))

    resultado = agent.handle("1", text="2 cremas nivea\n1 jabón", **CLIENTE)

    assert resultado.kind == "propuesta"
    assert resultado.iterations == 1
    assert "respond_to_customer" in modelo.ofrecidas[0]
    assert "armar_pedido" not in modelo.ofrecidas[0]
    sistema = modelo.recibidos[0][0].text
    assert "## Skill activa: `tomar_pedido`" in sistema
    assert skill_service.get_skills()["tomar_pedido"].instrucciones in sistema
    estado = agent.graph.get_state({"configurable": {"thread_id": "1"}}).values
    assert estado["skills_activas"] == ["tomar_pedido"]


def test_saludo_cierra_en_una_llamada(catalogo):
    """RF-62: un saludo preactiva `informar`, con sus datos del negocio a la vista."""
    saludar = _call("respond_to_customer", kind="informacion", message="¡Buen día! ¿Qué te preparo?")
    agent, modelo = _agente(_ai(saludar))

    resultado = agent.handle("1", text="hola, buenas tardes", **CLIENTE)

    assert (resultado.kind, resultado.iterations) == ("informacion", 1)
    assert "## Skill activa: `informar`" in modelo.recibidos[0][0].text
    assert "`datos_negocio`:" in modelo.recibidos[0][0].text


def test_la_skill_preactivada_no_sigue_en_el_turno_siguiente(catalogo):
    """RF-62: cada turno se encamina de nuevo; si el mensaje no es claro, vuelve a elegir el modelo."""
    agent, modelo = _agente(
        _ai(_propuesta("Listo, te armé las cremas. ¿Lo confirmo?", SKU, 2)),
        _ai(_call("leer_skill", nombre="tomar_pedido")),
        _ai(_propuesta("Ahora son 3. ¿Lo dejamos así?", SKU, 3)),
    )
    agent.handle("1", text="2 cremas nivea", **CLIENTE)
    agent.handle("1", text="mejor que sean tres, no dos", **CLIENTE)

    assert "## Skill activa" not in modelo.recibidos[1][0].text
    assert "respond_to_customer" not in modelo.ofrecidas[1]


def test_cambia_la_skill_preactivada(catalogo):
    """CA-118: si la skill preactivada no corresponde, el modelo lee otra y usa sus BR."""
    agent, modelo = _agente(
        _ai(_call("leer_br", id="BR-03")),
        _ai(_call("leer_skill", nombre="tomar_pedido")),
        _ai(_call("leer_br", id="BR-03")),
        _ai(_call("respond_to_customer", kind="informacion", message="¡Hola! ¿Qué te preparo?")),
    )

    agent.handle("1", text="hola", **CLIENTE)  # preactiva `informar`, que no tiene BR

    lecturas = _resultados(agent, "leer_br")
    assert lecturas[0]["error"]["code"] == "br_no_permitida"
    assert lecturas[1]["data"]["id"] == "BR-03"
    estado = agent.graph.get_state({"configurable": {"thread_id": "1"}}).values
    assert estado["skills_activas"] == ["informar", "tomar_pedido"]


def test_segunda_falla_termina_la_conversacion(catalogo):
    agent, _ = _agente(RuntimeError("uno"), RuntimeError("dos"))
    primera = agent.handle("1", text="hola", **CLIENTE)
    assert primera.message == FAILURE_MESSAGE
    assert not primera.cerrada
    segunda = agent.handle("1", text="hola?", **CLIENTE)
    assert segunda.message == FAILURE_MESSAGE
    assert segunda.cerrada


def test_la_conversacion_sigue_tras_reiniciar(catalogo, tmp_path):
    """CA-44: otro proceso retoma la pausa desde el archivo."""
    path = tmp_path / "checkpoints.db"
    conexion = sqlite3.connect(path, check_same_thread=False)
    NormaAgent(ModeloGuionado(*GUION_OFERTA), SqliteSaver(conexion)).handle("1", text="5 cremas", **CLIENTE)
    conexion.close()

    conexion = sqlite3.connect(path, check_same_thread=False)
    resultado = NormaAgent(ModeloGuionado(*GUION_ACEPTA), SqliteSaver(conexion)).handle("1", text="sí, dale", **CLIENTE)
    conexion.close()
    assert resultado.kind == "pedido"
    assert _ordenes() == 1


def test_nombre_del_cliente_llega_como_dato(catalogo):
    agent, modelo = _agente(AIMessage(content="Hola Pedro"))
    agent.handle("1", text="hola", customer_name="Pedro\nGonzález", **CLIENTE)
    sistema = modelo.recibidos[0][0].text
    # Un nombre con instrucciones llega como «desconocido»: ver test_guardrails.py (CA-80)
    assert "Nombre del cliente según su perfil: Pedro González\n" in sistema


def test_historial_previo_empieza_con_el_cliente():
    previo = [AIMessage(content="suelto"), HumanMessage(content="hola"), _ai(_call("get_product", sku=SKU))]
    assert [m.text for m in context._previous_history(previo)] == ["hola"]


def test_orquestador_abre_y_cierra_conversaciones(catalogo, monkeypatch):
    """Tras un pedido, el mensaje siguiente abre otra conversación."""
    agent, _ = _agente(*GUION_OFERTA, *GUION_ACEPTA, AIMessage(content="¡Hola de nuevo!"))
    monkeypatch.setattr(conversation_service, "get_agent", lambda: agent)

    def enviar(texto):
        return conversation_service.handle_message(
            IncomingMessage(customer_name="Pedro", text=texto, **CLIENTE)
        ).text

    enviar("5 cremas")
    assert "Tu número es" in enviar("dale")
    assert enviar("hola") == "¡Hola de nuevo!"
    with SessionLocal() as session:
        estados = session.scalars(select(ConversationRecord.status).order_by(ConversationRecord.id)).all()
    assert estados == ["cerrada", "abierta"]

def test_agente_explorar_catalogo_marcas_de_shampoo(catalogo):
    """CA-55: el agente puede explorar marcas de una categoría con explorar_catalogo y responder con informacion."""
    agent, modelo = _agente(
        _ai(_call("explorar_catalogo", category="shampoo")),
        _ai(_call("leer_skill", nombre="tomar_pedido")),
        _ai(_call("respond_to_customer", kind="informacion", message="En shampoo trabajamos Pantene, Sedal y Elvive.")),
    )
    resultado = agent.handle("1", text="¿Qué marcas de shampoo tenés?", **CLIENTE)
    assert resultado.kind == "informacion"
    assert "Pantene" in resultado.message



def test_aclaracion_no_suma_detalle(catalogo):
    """CA-58: sin propuesta no hay detalle; el mensaje es el del agente tal cual."""
    agent, _ = _agente(
        _ai(_call("search_products", query="crema")),
        _ai(_call("leer_skill", nombre="tomar_pedido")),
        _ai(_call("respond_to_customer", kind="aclaracion", message="¿Cuántas cremas querés?", options=[SKU])),
    )
    resultado = agent.handle("1", text="quiero crema", **CLIENTE)
    assert resultado.message == "¿Cuántas cremas querés?"


# Pedido ficticio con el formato de los pedidos reales por WhatsApp: marcas como encabezado, abreviaturas y
# un renglón con dos tamaños posibles. Ver docs/validaciones_pendientes.md
PEDIDO_LARGO = """Hola!! te paso el pedido

Marca Uno
2 sh keratina x900
1 caja ampollas argan
3 mascara argan
2 gel fijacion extrema x 250

Marca Dos
2 alisado x 250
3 crema peinar rulos

Marca Tres
2 oxi 20 vol x 1l
4 protector termico
1 decolorante x 700"""

CATALOGO_LARGO = [
    ("MU-SH-KER-900", "Marca Uno Shampoo Keratina", "Marca Uno", "900 ml", "4500.00"),
    ("MU-AMP-ARG", "Marca Uno Ampolla Argan 15 ml x 12 unid", "Marca Uno", "caja x 12", "9800.00"),
    ("MU-MAS-ARG-250", "Marca Uno Máscara Argan", "Marca Uno", "250 g", "3900.00"),
    ("MU-MAS-ARG-1K", "Marca Uno Máscara Argan", "Marca Uno", "1 kg", "11200.00"),
    ("MU-GEL-EXT-250", "Marca Uno Gel Fijación Extrema", "Marca Uno", "250 g", "2100.00"),
    ("MD-ALI-250", "Marca Dos Alisado sin formol", "Marca Dos", "250 g", "15600.00"),
    ("MD-CRE-RUL-250", "Marca Dos Crema de peinar rulos", "Marca Dos", "250 g", "3300.00"),
    ("MT-OXI-20-1L", "Marca Tres Oxidante 20 vol", "Marca Tres", "1 L", "5200.00"),
    ("MT-PRO-TER", "Marca Tres Protector térmico", "Marca Tres", "200 ml", "6700.00"),
    ("MT-DECO-700", "Marca Tres Decolorante", "Marca Tres", "700 g", "18900.00"),
]
# Lo que el agente arma con lo claro; la máscara argan queda como duda (250 g o 1 kg)
LINEAS_CLARAS = [
    ("MU-SH-KER-900", 2),
    ("MU-AMP-ARG", 1),
    ("MU-GEL-EXT-250", 2),
    ("MD-ALI-250", 2),
    ("MD-CRE-RUL-250", 3),
    ("MT-OXI-20-1L", 2),
    ("MT-PRO-TER", 4),
    ("MT-DECO-700", 1),
]
BUSQUEDAS_LARGO = (
    "shampoo keratina 900",
    "ampolla argan",
    "mascara argan",
    "gel fijacion extrema 250",
    "alisado 250",
    "crema peinar rulos",
    "oxidante 20 vol 1 l",
    "protector termico",
    "decolorante 700",
)


@pytest.fixture
def catalogo_largo(monkeypatch):
    productos = [
        Product(sku=sku, name=nombre, brand=marca, presentation=presentacion, price=Decimal(precio), stock=50)
        for sku, nombre, marca, presentacion, precio in CATALOGO_LARGO
    ]
    monkeypatch.setattr(sheets_service, "get_catalog", lambda: productos)
    return productos


def _busquedas_en_paralelo(*queries):
    calls = [
        {"name": "search_products", "args": {"query": q}, "id": f"call-search-{i}", "type": "tool_call"}
        for i, q in enumerate(queries)
    ]
    return AIMessage(content="", tool_calls=calls)


def test_pedido_largo_propone_lo_claro_y_junta_las_dudas(catalogo_largo):
    """CA-58: un pedido de muchos renglones se resuelve en un turno: lo claro va en la propuesta, con el detalle
    armado por el sistema, y las dudas van juntas en el mensaje del agente."""
    mensaje_agente = (
        "¡Hola! Te armé el pedido con todo lo que encontré. Me queda una duda: "
        "1) La máscara argan de Marca Uno, ¿la querés de 250 g ($3900) o de 1 kg ($11200)? "
        "Si me confirmás así, la máscara queda afuera. ¿Te armo el pedido o me contestás la duda?"
    )
    # El backend numeró los nueve renglones: el 3, la máscara, es la duda (RF-65, RF-68)
    renglones = (1, 2, 4, 5, 6, 7, 8, 9)
    propuesta = [
        {"sku": sku, "quantity": cantidad, "renglon": n}
        for n, (sku, cantidad) in zip(renglones, LINEAS_CLARAS, strict=True)
    ]
    agent, modelo = _agente(
        _busquedas_en_paralelo(*BUSQUEDAS_LARGO),
        _ai(_call("leer_skill", nombre="tomar_pedido")),
        _ai(_call("leer_br", id="BR-03")),
        _ai(
            _call(
                "respond_to_customer",
                kind="propuesta",
                message=mensaje_agente,
                pendientes=[{"renglon": 3, "opciones": ["MU-MAS-ARG-250", "MU-MAS-ARG-1K"]}],
                propuesta=propuesta,
            )
        ),
    )

    resultado = agent.handle("1", text=PEDIDO_LARGO, **CLIENTE)

    assert resultado.kind == "propuesta"
    assert resultado.iterations == 4
    busquedas = _resultados(agent, "search_products")
    assert len(busquedas) == len(BUSQUEDAS_LARGO)
    primeros = [b["data"]["matches"][0]["sku"] for b in busquedas]
    assert primeros == [
        "MU-SH-KER-900",
        "MU-AMP-ARG",
        primeros[2],  # la máscara argan tiene dos tamaños: cualquiera de los dos sale primero
        "MU-GEL-EXT-250",
        "MD-ALI-250",
        "MD-CRE-RUL-250",
        "MT-OXI-20-1L",
        "MT-PRO-TER",
        "MT-DECO-700",
    ]
    assert {m["sku"] for m in busquedas[2]["data"]["matches"]} >= {"MU-MAS-ARG-250", "MU-MAS-ARG-1K"}
    assert [(item.sku, item.quantity) for item in resultado.draft.items] == LINEAS_CLARAS
    assert resultado.options == ["MU-MAS-ARG-250", "MU-MAS-ARG-1K"]
    assert resultado.message.startswith(mensaje_agente)
    assert "• 4 x Marca Tres Protector térmico 200 ml ($6700.00 c/u = $26800.00)" in resultado.message
    total = sum(Decimal(p.price) * c for p in catalogo_largo for sku, c in LINEAS_CLARAS if p.sku == sku)
    assert resultado.message.endswith(f"Total estimado: ${total:.2f}")
    # El detalle no cuenta para el tope de 600 caracteres del mensaje que escribe el agente
    assert len(resultado.message) > 600
    assert _ordenes() == 0


# --- Pedido por renglones (M3 de docs/spec/toma-de-pedidos-por-texto.md) -----------------------------------------


def _renglones(agent, thread="1"):
    estado = agent.graph.get_state({"configurable": {"thread_id": thread}}).values
    return {renglon["n"]: renglon for renglon in estado["renglones"]}


def _buscar_todo():
    renglones = [
        {"n": 1, "texto": "sh keratina x900", "marca": "Marca Uno", "cantidad": 2},
        {"n": 2, "texto": "caja ampollas argan", "marca": "Marca Uno", "cantidad": 1},
        {"n": 3, "texto": "mascara argan", "marca": "Marca Uno", "cantidad": 3},
        {"n": 4, "texto": "gel fijacion extrema x 250", "marca": "Marca Uno", "cantidad": 2},
        {"n": 5, "texto": "alisado x 250", "marca": "Marca Dos", "cantidad": 2},
        {"n": 6, "texto": "crema peinar rulos", "marca": "Marca Dos", "cantidad": 3},
        {"n": 7, "texto": "oxi 20 vol x 1l", "marca": "Marca Tres", "cantidad": 2},
        {"n": 8, "texto": "protector termico", "marca": "Marca Tres", "cantidad": 4},
        {"n": 9, "texto": "decolorante x 700", "marca": "Marca Tres", "cantidad": 1},
    ]
    return _ai(_call("buscar_renglones", renglones=renglones))


def _responder_lista(*, sin=(), pendientes=((3, ["MU-MAS-ARG-250", "MU-MAS-ARG-1K"]),), afuera=()):
    numeros = (1, 2, 4, 5, 6, 7, 8, 9)
    propuesta = [
        {"sku": sku, "quantity": cantidad, "renglon": n}
        for n, (sku, cantidad) in zip(numeros, LINEAS_CLARAS, strict=True)
        if n not in sin
    ]
    return _ai(
        _call(
            "respond_to_customer",
            kind="propuesta",
            message="Te armé lo claro. La máscara argan, ¿de 250 o de 1 kilo? Si lo confirmás así, queda afuera.",
            propuesta=propuesta,
            pendientes=[{"renglon": n, "opciones": opciones} for n, opciones in pendientes],
            afuera=[{"renglon": n, "motivo": motivo} for n, motivo in afuera],
        )
    )


def test_pedido_por_renglones_en_dos_llamadas(catalogo_largo):
    """RF-65 a RF-68: la lista llega numerada, se busca en una llamada y cada renglón queda con su cuenta."""
    agent, modelo = _agente(_buscar_todo(), _responder_lista())

    resultado = agent.handle("1", text=PEDIDO_LARGO, **CLIENTE)

    assert (resultado.kind, resultado.iterations) == ("propuesta", 2)
    assert "buscar_renglones" in modelo.ofrecidas[0] and "respond_to_customer" in modelo.ofrecidas[0]
    sistema = modelo.recibidos[0][0].text
    assert "1. [Marca Uno] 2 sh keratina x900" in sistema and "9. [Marca Tres] 1 decolorante x 700" in sistema
    assert "te paso el pedido" not in sistema.split("## Estado actual")[1]  # el saludo no es un renglón
    (busqueda,) = _resultados(agent, "buscar_renglones")
    estados = {r["n"]: r["estado"] for r in busqueda["data"]["renglones"]}
    assert estados == {**dict.fromkeys(range(1, 10), "unico"), 3: "varias"}
    assert [regla["id"] for regla in busqueda["reglas"]] == ["BR-03"]  # la skill estaba preactivada (RF-63)
    assert [(item.sku, item.quantity) for item in resultado.draft.items] == LINEAS_CLARAS
    assert resultado.options == ["MU-MAS-ARG-250", "MU-MAS-ARG-1K"]
    renglones = _renglones(agent)
    assert renglones[1]["estado"] == "resuelto" and renglones[1]["lineas"] == [{"sku": "MU-SH-KER-900", "cantidad": 2}]
    assert (renglones[3]["estado"], renglones[3]["opciones"]) == ("pendiente", ["MU-MAS-ARG-250", "MU-MAS-ARG-1K"])


def test_control_de_cobertura_en_el_turno(catalogo_largo):
    """CA-126: una respuesta que deja un renglón sin cuenta vuelve al modelo con el número que falta."""
    agent, _ = _agente(_buscar_todo(), _responder_lista(sin=(9,)), _responder_lista())

    resultado = agent.handle("1", text=PEDIDO_LARGO, **CLIENTE)

    rechazo, aceptada = _resultados(agent, "respond_to_customer")
    assert rechazo["error"]["code"] == "renglones_sin_cuenta"
    assert rechazo["error"]["details"]["renglones"] == [9]
    assert aceptada["ok"] and resultado.iterations == 3
    assert len(resultado.draft.items) == len(LINEAS_CLARAS)


def test_cobertura_se_tolera_tras_dos_intentos(catalogo_largo):
    """RF-68: al tercer intento la respuesta sale igual y lo que falta queda pendiente, para el turno siguiente."""
    incompleta = [_responder_lista(sin=(9,)) for _ in range(3)]
    agent, _ = _agente(_buscar_todo(), *incompleta)

    resultado = agent.handle("1", text=PEDIDO_LARGO, **CLIENTE)

    assert resultado.kind == "propuesta"
    assert (_renglones(agent)[9]["estado"], _renglones(agent)[9]["opciones"]) == ("pendiente", [])


def test_la_respuesta_a_una_duda_completa_el_pedido_sin_volver_a_buscar(catalogo_largo):
    """RF-67: los renglones resueltos y el pendiente siguen en el estado actual del turno siguiente."""
    completar = _ai(
        _call(
            "respond_to_customer",
            kind="propuesta",
            message="Listo, sumé las 3 máscaras de 1 kilo. ¿Lo cerramos?",
            propuesta=[
                *({"sku": sku, "quantity": cantidad} for sku, cantidad in LINEAS_CLARAS),
                {"sku": "MU-MAS-ARG-1K", "quantity": 3, "renglon": 3},
            ],
        )
    )
    leer = _ai(_call("leer_skill", nombre="tomar_pedido"))
    agent, modelo = _agente(_buscar_todo(), _responder_lista(), leer, completar)
    agent.handle("1", text=PEDIDO_LARGO, **CLIENTE)

    resultado = agent.handle("1", text="la de 1 kilo", **CLIENTE)

    estado_actual = modelo.recibidos[2][0].text
    pendiente = "3. [Marca Uno] 3 mascara argan → pendiente, le preguntaste por: MU-MAS-ARG-250, MU-MAS-ARG-1K"
    assert pendiente in estado_actual
    assert "1. [Marca Uno] 2 sh keratina x900 → resuelto: 2 x MU-SH-KER-900" in estado_actual
    assert len(_resultados(agent, "buscar_renglones")) == 1  # no volvió a buscar
    assert len(resultado.draft.items) == len(LINEAS_CLARAS) + 1
    renglones = _renglones(agent)
    assert all(renglon["estado"] == "resuelto" for renglon in renglones.values())


def test_un_renglon_que_el_cliente_saca_queda_afuera(catalogo_largo):
    """RF-67: si la propuesta nueva ya no trae el producto de un renglón resuelto, ese renglón pasa a `afuera`."""
    sin_decolorante = _ai(
        _call(
            "respond_to_customer",
            kind="propuesta",
            message="Hecho, saqué el decolorante. ¿Lo dejamos así?",
            propuesta=[{"sku": sku, "quantity": c} for sku, c in LINEAS_CLARAS if sku != "MT-DECO-700"],
            pendientes=[{"renglon": 3, "opciones": ["MU-MAS-ARG-250", "MU-MAS-ARG-1K"]}],
        )
    )
    leer = _ai(_call("leer_skill", nombre="tomar_pedido"))
    agent, _ = _agente(_buscar_todo(), _responder_lista(), leer, sin_decolorante)
    agent.handle("1", text=PEDIDO_LARGO, **CLIENTE)
    agent.handle("1", text="sacame el decolorante", **CLIENTE)

    assert (_renglones(agent)[9]["estado"], _renglones(agent)[9]["motivo"]) == ("afuera", "lo_saco_el_cliente")
    assert _renglones(agent)[8]["estado"] == "resuelto"


def test_renglon_que_no_existe_vuelve_al_modelo(catalogo_largo):
    agent, _ = _agente(_buscar_todo(), _responder_lista(afuera=((15, "fuera_de_catalogo"),)), _responder_lista())
    agent.handle("1", text=PEDIDO_LARGO, **CLIENTE)
    assert _resultados(agent, "respond_to_customer")[0]["error"]["code"] == "renglon_inexistente"


def test_el_tono_de_la_coloracion_va_a_la_nota(monkeypatch):
    """RF-69: el tono viaja aparte de la búsqueda y termina en la nota de la línea, aunque el modelo no la escriba."""
    from tests import datos_sinteticos

    productos = datos_sinteticos.catalogo_mayorista()
    monkeypatch.setattr(sheets_service, "get_catalog", lambda: productos)
    buscar = _ai(
        _call(
            "buscar_renglones",
            renglones=[
                {"n": 2, "texto": "coloracion", "marca": "Duomo", "tono": "7", "cantidad": 3},
                {"n": 3, "texto": "coloracion", "marca": "Duomo", "tono": "8.21", "cantidad": 2},
            ],
        )
    )
    responder = _ai(
        _call(
            "respond_to_customer",
            kind="propuesta",
            message="Van las coloraciones Duomo, 3 del tono 7 y 2 del 8.21. ¿Lo confirmo?",
            propuesta=[
                {"sku": "DUO-CO-60", "quantity": 3, "renglon": 2},
                {"sku": "DUO-CO-60", "quantity": 2, "renglon": 3},
            ],
            afuera=[{"renglon": 1, "motivo": "no_es_producto"}],
        )
    )
    agent, _ = _agente(buscar, responder)

    resultado = agent.handle("1", text="Duomo\ncoloracion\n7/3\n821/2", **CLIENTE)

    assert [(item.quantity, item.note) for item in resultado.draft.items] == [(3, "tono 7"), (2, "tono 8.21")]
    assert _renglones(agent)[1]["motivo"] == "no_es_producto"


# --- Memoria entre turnos y entre pedidos (M4 de docs/spec/toma-de-pedidos-por-texto.md) -------------------------


@pytest.fixture
def catalogo_pantene(monkeypatch):
    """El shampoo en dos medidas: alcanza para una pregunta con opciones."""
    productos = [
        Product(sku="SH-200", name="Shampoo Pantene", brand="Pantene", presentation="200 ml", price=Decimal("4200"),
                stock=20),
        Product(sku="SH-400", name="Shampoo Pantene", brand="Pantene", presentation="400 ml", price=Decimal("7350"),
                stock=20),
    ]
    monkeypatch.setattr(sheets_service, "get_catalog", lambda: productos)
    return productos


def test_aclaracion_sin_volver_a_buscar(catalogo_pantene):
    """CA-127: el turno siguiente a una pregunta ve las opciones ofrecidas, con su SKU, y cierra en una llamada."""
    preguntar = _call(
        "respond_to_customer",
        kind="aclaracion",
        message="El shampoo Pantene lo tengo en 200 y en 400. ¿Cuál te mando?",
        options=["SH-200", "SH-400"],
    )
    agent, modelo = _agente(_ai(preguntar), _ai(_propuesta("Listo, van los 2 de 400. ¿Lo confirmo?", "SH-400", 2)))
    agent.handle("1", text="2 shampoo pantene", **CLIENTE)  # una cantidad: preactiva `tomar_pedido`

    resultado = agent.handle("1", text="el de 400", **CLIENTE)

    estado_actual = modelo.recibidos[1][0].text
    assert (
        "Opciones que le ofreciste en tu mensaje anterior: SH-200 (Shampoo Pantene 200 ml); "
        "SH-400 (Shampoo Pantene 400 ml)"
    ) in estado_actual
    assert resultado.kind == "propuesta" and resultado.draft.items[0].sku == "SH-400"
    assert resultado.iterations == 1
    assert _resultados(agent, "search_products") == []  # no hizo falta buscar


def test_las_opciones_son_las_de_la_ultima_respuesta(catalogo_pantene):
    """RF-71: las opciones de una pregunta ya contestada no quedan colgadas en los turnos siguientes."""
    opciones = ["SH-200", "SH-400"]
    preguntar = _call("respond_to_customer", kind="aclaracion", message="¿De 200 o de 400?", options=opciones)
    proponer = _ai(_propuesta("Van los 2 de 400. ¿Lo confirmo?", "SH-400", 2))
    otra = _ai(_propuesta("Ahora son 3 de 400. ¿Lo dejamos así?", "SH-400", 3))
    agent, modelo = _agente(_ai(preguntar), proponer, otra)
    agent.handle("1", text="2 shampoo pantene", **CLIENTE)
    agent.handle("1", text="2 del de 400", **CLIENTE)
    agent.handle("1", text="mejor 3 de esos", **CLIENTE)

    assert "Opciones que le ofreciste" in modelo.recibidos[1][0].text
    assert "Opciones que le ofreciste" not in modelo.recibidos[2][0].text
    assert "(versión 2)" not in modelo.recibidos[1][0].text and "Propuesta vigente: 2 x" in modelo.recibidos[2][0].text


@pytest.mark.parametrize("texto", ["ok", "sí, dale"])
def test_precio_cambiado_no_registra(catalogo, texto):
    """CA-128: si un precio cambió desde la propuesta, no se crea la orden y sale la propuesta con el precio nuevo."""
    agent, modelo = _agente(*GUION_OFERTA, *GUION_ACEPTA)
    agent.handle("1", text="te encargo 5 cremas", **CLIENTE)
    catalogo[0].price = Decimal("1200")

    respuesta = agent.handle("1", text=texto, **CLIENTE)

    assert respuesta.kind == "propuesta" and not respuesta.cerrada
    assert _ordenes() == 0
    assert respuesta.message.startswith("Desde que te armé el pedido cambió el precio")
    assert "• 3 x Crema Nivea 100 ml ($1200.00 c/u = $3600.00)" in respuesta.message
    assert respuesta.draft.version == 2 and respuesta.draft.items[0].unit_price == Decimal("1200")

    final = agent.handle("1", text="ok", **CLIENTE)  # ahora sí: confirma el total que vio
    assert final.kind == "pedido"
    assert order_service.get_order(final.order_id).total == Decimal("3600")


def test_un_pedido_ya_registrado_no_se_reprecia(catalogo):
    """RF-72: el reintento de una orden que ya existe la devuelve como quedó, aunque el precio haya cambiado."""
    agent, _ = _agente(*GUION_OFERTA)
    primero = agent.handle("1", text="te encargo 5 cremas", **CLIENTE)
    orden = agent.handle("1", text="ok", **CLIENTE)
    catalogo[0].price = Decimal("1200")

    repetida = order_service.confirm_draft(primero.draft)

    assert repetida.order_id == orden.order_id and repetida.total == Decimal("3000")


def _registrar(customer_id: str, sku: str, cantidad: int, draft_id: str, precio: str = "1000"):
    from internal.schemas.order_schema import OrderDraft, OrderItem

    draft = OrderDraft(
        draft_id=draft_id,
        customer_id=customer_id,
        channel=Channel.TELEGRAM,
        items=[OrderItem(sku=sku, product_name="Producto", quantity=cantidad, unit_price=Decimal(precio))],
    )
    return order_service.confirm_draft(draft)


def test_pedidos_anteriores_del_cliente(catalogo):
    """CA-129: la tool trae solo las órdenes del cliente del turno, sin precios, número de orden ni estado."""
    _registrar(CLIENTE["customer_id"], SKU, 1, "previa-1")
    cancelada = _registrar(CLIENTE["customer_id"], SKU, 1, "previa-2")
    order_service.cancel_order(cancelada.order_id)
    _registrar("telegram:999", SKU, 1, "de-otro")
    responder = _call("respond_to_customer", kind="informacion", message="La otra vez llevaste 1 crema. ¿Te la armo?")
    agent, modelo = _agente(_ai(_call("pedidos_anteriores", limite=5, customer_id="telegram:999")), _ai(responder))

    agent.handle("1", text="mandame lo de siempre", **CLIENTE)

    assert "pedidos_anteriores" in modelo.ofrecidas[0]  # es una tool de base
    (resultado,) = _resultados(agent, "pedidos_anteriores")
    assert resultado["ok"]
    assert [pedido["lineas"] for pedido in resultado["data"]["pedidos"]] == [
        [{"sku": SKU, "name": "Producto", "cantidad": 1}]
    ]
    texto = json.dumps(resultado)
    assert "ORD-" not in texto and "price" not in texto and "Recibido" not in texto and "1000" not in texto


def test_lo_que_el_cliente_compro_desempata_la_busqueda(catalogo_pantene):
    """RF-73: entre dos productos que coinciden igual, va primero el que ese cliente ya pidió."""
    ctx = tool_registry_ctx("telegram:1")
    sin_historial = tool_registry.execute("search_products", {"query": "shampoo pantene"}, ctx)["data"]["matches"]
    assert [match["sku"] for match in sin_historial] == ["SH-200", "SH-400"]
    assert "comprado_antes" not in sin_historial[0]

    _registrar("telegram:1", "SH-400", 2, "compra-400", precio="7350")
    ctx = tool_registry_ctx("telegram:1")
    con_historial = tool_registry.execute("search_products", {"query": "shampoo pantene"}, ctx)
    matches = con_historial["data"]["matches"]
    assert [match["sku"] for match in matches] == ["SH-400", "SH-200"]
    assert matches[0]["comprado_antes"] is True and "comprado_antes" not in matches[1]
    assert con_historial["data"]["total"] == 2  # sigue habiendo dos: se pregunta, no se elige por él
    otro = tool_registry.execute("search_products", {"query": "shampoo pantene"}, tool_registry_ctx("telegram:2"))
    assert [match["sku"] for match in otro["data"]["matches"]] == ["SH-200", "SH-400"]


def tool_registry_ctx(customer_id: str):
    return tool_registry.TurnContext(
        customer_id=customer_id, channel=Channel.TELEGRAM, disponibles=list(tool_registry.BASE_TOOLS)
    )
