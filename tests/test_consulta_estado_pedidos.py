"""Pruebas de consulta de estado de pedidos propios desde el chat (Issue #26, RNF-02)."""

from decimal import Decimal

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from internal.agent import guardrails, tool_registry
from internal.agent.graph import skill_clara
from internal.agent.norma_agent import NormaAgent
from internal.schemas.catalog_schema import Product
from internal.schemas.order_schema import Channel, OrderDraft, OrderItem, OrderStatus
from internal.services import inventory_service, order_service, sheets_service
from internal.services.palabras_clave import es_consulta_estado_pedido
from internal.tools import order_tools


@pytest.fixture
def _sin_sheets(monkeypatch):
    monkeypatch.setattr(sheets_service, "append_order", lambda *a, **k: None)
    monkeypatch.setattr(sheets_service, "update_order_status_in_sheets", lambda *a, **k: None)
    monkeypatch.setattr(
        inventory_service,
        "get_catalog",
        lambda *a, **k: [
            Product(
                sku="CR-NIV-100",
                name="Crema Nivea 100 ml",
                presentation="100 ml",
                price=Decimal("1500.00"),
                stock=100,
            )
        ],
    )


def _crear_orden(customer_id: str, draft_id: str, sku: str = "CR-NIV-100", nombre: str = "Crema Nivea 100 ml"):
    draft = OrderDraft(
        draft_id=draft_id,
        customer_id=customer_id,
        channel=Channel.TELEGRAM,
        items=[
            OrderItem(
                sku=sku,
                product_name=nombre,
                presentation="100 ml",
                quantity=2,
                unit_price=Decimal("1500.00"),
            )
        ],
    )
    return order_service.confirm_draft(draft)


def test_list_customer_orders_solo_devuelve_las_del_cliente(_sin_sheets):
    """RNF-02: un cliente nunca puede ver las órdenes de otro, ni pasando el order_id ajeno."""
    orden_a = _crear_orden("telegram:cliente-a", "d-a1")
    orden_b = _crear_orden("telegram:cliente-b", "d-b1")

    de_a = order_service.list_customer_orders("telegram:cliente-a")
    assert [o.order_id for o in de_a] == [orden_a.order_id]

    # Intentar consultar la orden de B autenticándose como A devuelve lista vacía
    ajena = order_service.list_customer_orders("telegram:cliente-a", order_id=orden_b.order_id)
    assert ajena == []


def test_list_customer_orders_ordena_por_fecha_y_respeta_el_limite(_sin_sheets):
    """Devuelve de la más nueva a la más vieja respetando el límite pedido."""
    o1 = _crear_orden("telegram:cliente-lim", "d-lim-1")
    o2 = _crear_orden("telegram:cliente-lim", "d-lim-2")
    o3 = _crear_orden("telegram:cliente-lim", "d-lim-3")

    ultimas_dos = order_service.list_customer_orders("telegram:cliente-lim", limit=2)
    assert [o.order_id for o in ultimas_dos] == [o3.order_id, o2.order_id]
    assert o1.order_id not in [o.order_id for o in ultimas_dos]


def test_list_customer_orders_incluye_todos_los_estados_y_normaliza_order_id(_sin_sheets):
    """Incluye órdenes preparadas o canceladas y acepta variantes como 'ord-1' o '1'."""
    o1 = _crear_orden("telegram:cliente-est", "d-est-1")
    o2 = _crear_orden("telegram:cliente-est", "d-est-2")
    order_service.change_status(o1.order_id, OrderStatus.PREPARED)
    order_service.cancel_order(o2.order_id)

    todas = order_service.list_customer_orders("telegram:cliente-est", limit=5)
    assert [(o.order_id, o.status) for o in todas] == [
        (o2.order_id, OrderStatus.CANCELLED),
        (o1.order_id, OrderStatus.PREPARED),
    ]

    num_o1 = str(order_service.parse_order_id(o1.order_id))
    assert order_service.list_customer_orders("telegram:cliente-est", order_id=num_o1)[0].order_id == o1.order_id
    assert (
        order_service.list_customer_orders("telegram:cliente-est", order_id=f"ord-{num_o1}")[0].order_id
        == o1.order_id
    )


def test_consultar_estado_pedido_no_recibe_customer_id_del_llm(_sin_sheets):
    """RNF-02: el esquema expuesto al LLM no incluye customer_id y el backend siempre usa el del turno."""
    (schema,) = tool_registry.schemas(["consultar_estado_pedido"])
    props = schema["function"]["parameters"]["properties"]
    assert "customer_id" not in props
    assert set(props) == {"order_id", "limite"}

    orden_a = _crear_orden("telegram:real", "d-real-1")
    _crear_orden("telegram:ajeno", "d-ajeno-1")

    ctx = tool_registry.TurnContext(
        customer_id="telegram:real",
        channel=Channel.TELEGRAM,
        disponibles=["consultar_estado_pedido"],
    )
    resultado = tool_registry.execute("consultar_estado_pedido", {"customer_id": "telegram:ajeno"}, ctx)
    assert resultado["ok"] is True
    pedidos = resultado["data"]["pedidos"]
    assert [p["order_id"] for p in pedidos] == [orden_a.order_id]


def test_consultar_estado_pedido_sin_skus_y_con_hints(_sin_sheets):
    """La salida no expone SKUs y entrega hints claros si no hay órdenes."""
    ctx_vacio = tool_registry.TurnContext(
        customer_id="telegram:sin-pedidos",
        channel=Channel.TELEGRAM,
        disponibles=["consultar_estado_pedido"],
    )
    res_vacio = tool_registry.execute("consultar_estado_pedido", {}, ctx_vacio)
    assert res_vacio["ok"] is True
    assert res_vacio["data"]["pedidos"] == []
    assert "_hint" in res_vacio["data"]

    orden = _crear_orden("telegram:con-pedido", "d-cp-1")
    order_service.change_status(orden.order_id, OrderStatus.PREPARED)
    pedidos = order_tools.consultar_estado_pedido("telegram:con-pedido")
    assert len(pedidos) == 1
    assert pedidos[0]["order_id"] == orden.order_id
    assert pedidos[0]["estado"] == "Preparado"
    assert "Preparado" in pedidos[0]["estado_detalle"]
    assert all("sku" not in linea for linea in pedidos[0]["lineas"])


def test_guardrails_permite_order_id_consultado_y_rechaza_inventado():
    """El guardrail habilita mencionar solo las órdenes reales consultadas en el turno con kind='informacion'."""
    turno_con_orden = guardrails.ContextoTurno(
        ordenes_consultadas={"ord-0005"},
        consulto_estado=True,
    )

    # Orden consultada en este turno con kind=informacion -> pasa
    assert (
        guardrails.validar_salida(
            "Tu pedido ORD-0005 del 2026-11-20 está Preparado y listo para retirar. ¿Querés sumar algo más?",
            kind="informacion",
            turno=turno_con_orden,
        )
        == []
    )

    # Frases descriptivas válidas ("Tu número de pedido...", "quedó registrado el...") -> pasan
    assert (
        guardrails.validar_salida(
            "Tu número de pedido ORD-0005 está Preparado. ¿Necesitás algo más?",
            kind="informacion",
            turno=turno_con_orden,
        )
        == []
    )
    assert (
        guardrails.validar_salida(
            "Tu pedido ORD-0005 quedó registrado el 20/11 y ya está listo para retirar.",
            kind="informacion",
            turno=turno_con_orden,
        )
        == []
    )

    # Orden pedida que no se encontró (hint: "No encontré la orden ORD-0099...") -> pasa
    turno_no_encontrado = guardrails.ContextoTurno(
        ordenes_pedidas={"ord-0099"},
        consulto_estado=True,
    )
    assert (
        guardrails.validar_salida(
            "No encontré la orden ORD-0099 a tu nombre. ¿Querés que revise tus pedidos anteriores?",
            kind="informacion",
            turno=turno_no_encontrado,
        )
        == []
    )

    # Orden inventada que no devolvió la tool -> rechazada
    rechazos_inventado = guardrails.validar_salida(
        "Tu pedido ORD-9999 está Preparado.",
        kind="informacion",
        turno=turno_con_orden,
    )
    assert [r.regla for r in rechazos_inventado] == ["pedido_no_registrado"]

    # Afirmar en primera persona que registró el pedido -> rechazada aunque coincida el ID
    rechazos_falso_reg = guardrails.validar_salida(
        "Ya registré tu pedido ORD-0005.",
        kind="informacion",
        turno=turno_con_orden,
    )
    assert [r.regla for r in rechazos_falso_reg] == ["pedido_no_registrado"]

    # Sin haber consultado ninguna orden -> rechazada
    rechazos_sin_consulta = guardrails.validar_salida(
        "Tu pedido ORD-0005 está Preparado.",
        kind="informacion",
        turno=guardrails.ContextoTurno(),
    )
    assert [r.regla for r in rechazos_sin_consulta] == ["pedido_no_registrado"]

    # Tool llamada pero sin pedidos encontrados (pedidos=[]) -> NO debe habilitar confirmaciones falsas
    turno_sin_resultados = guardrails.ContextoTurno(consulto_estado=True)
    for falsa_confirmacion in (
        "Listo, tu pedido está registrado.",
        "Tu pedido ya está confirmado, en breve lo preparamos.",
        "Tu pedido fue procesado y cargado correctamente.",
    ):
        rechazos_falsa = guardrails.validar_salida(
            falsa_confirmacion,
            kind="informacion",
            turno=turno_sin_resultados,
        )
        assert [r.regla for r in rechazos_falsa] == ["pedido_no_registrado"], falsa_confirmacion

    # Orden pedida pero NO encontrada: prohíbe afirmar su estado (solo permite decir que no se encontró)
    assert [
        r.regla
        for r in guardrails.validar_salida(
            "Tu pedido ORD-0099 está confirmado y listo.",
            kind="informacion",
            turno=turno_no_encontrado,
        )
    ] == ["pedido_no_registrado"]
    assert [
        r.regla
        for r in guardrails.validar_salida(
            "No encontré la orden ORD-0099. Tu pedido ORD-0099 está confirmado.",
            kind="informacion",
            turno=turno_no_encontrado,
        )
    ] == ["pedido_no_registrado"]

    # contexto_turno normaliza order_id pedido por el modelo ("99" / "ORD-99" -> "ord-0099")
    from langchain_core.messages import AIMessage

    for raw_id in ("99", "ORD-99"):
        call = {"name": "consultar_estado_pedido", "args": {"order_id": raw_id}, "id": "c1"}
        turno_norm = guardrails.contexto_turno([AIMessage(content="", tool_calls=[call])])
        assert "ord-0099" in turno_norm.ordenes_pedidas
        assert (
            guardrails.validar_salida(
                "No encontré la orden ORD-0099 a tu nombre.",
                kind="informacion",
                turno=turno_norm,
            )
            == []
        )


def test_skill_clara_preactiva_informar_en_consultas_de_estado():
    """Consultas de estado (incluso con número de orden ORD-XXXX) preactivan `informar` y no `tomar_pedido`."""
    consultas = [
        "¿cómo viene mi pedido?",
        "¿ya está listo mi pedido?",
        "¿cómo está el ORD-0005?",
        "estado de mi orden",
        "¿puedo retirar mi pedido?",
    ]
    for texto in consultas:
        assert es_consulta_estado_pedido(texto), texto
        assert skill_clara(texto, hay_propuesta=False) == "informar", texto

    # Un pedido normal con números sigue yendo a tomar_pedido
    assert not es_consulta_estado_pedido("2 cremas nivea 100ml")
    assert skill_clara("2 cremas nivea 100ml", hay_propuesta=False) == "tomar_pedido"

    # Números de orden sin giro de consulta NO preactivan informar
    assert not es_consulta_estado_pedido("repetime el ORD-0005")
    assert skill_clara("repetime el ORD-0005", hay_propuesta=False) == "tomar_pedido"
    assert not es_consulta_estado_pedido("quiero lo mismo que el ORD-0005 pero 3 cremas más")
    assert skill_clara("quiero lo mismo que el ORD-0005 pero 3 cremas más", hay_propuesta=False) == "tomar_pedido"
    assert not es_consulta_estado_pedido("quiero hacer un reclamo por el ORD-0005")
    assert not es_consulta_estado_pedido("quiero cancelar el pedido ORD-0005")

    # Si pide hablar con una persona, va a derivar_o_cancelar aunque pregunte por el pedido
    reclamo_con_humano = "¿qué pasó con mi pedido? quiero hablar con alguien"
    assert skill_clara(reclamo_con_humano, hay_propuesta=False) == "derivar_o_cancelar"

    # Si hay propuesta vigente, "¿ya está listo el pedido?" no preactiva informar
    assert skill_clara("¿ya está listo el pedido?", hay_propuesta=True) != "informar"

    # Pedidos comunes con cantidades o giros sueltos NO preactivan informar: van a tomar_pedido
    for pedido_comun in (
        "hola cómo va, te hago un pedido de 3 cremas nivea",
        "te paso esta lista para el pedido: 3 cremas, 2 dove",
        "cuando sale el pedido de 10 shampoo dove",
        "ya esta, sumale 2 cremas al pedido",
    ):
        assert not es_consulta_estado_pedido(pedido_comun), pedido_comun
        assert skill_clara(pedido_comun, hay_propuesta=False) == "tomar_pedido", pedido_comun


def test_flujo_completo_agente_consulta_estado_de_pedido(_sin_sheets):
    """Punta a punta: el cliente pregunta por su pedido, el agente usa consultar_estado_pedido y responde bien."""
    orden = _crear_orden("telegram:e2e-estado", "d-e2e-1")
    order_service.change_status(orden.order_id, OrderStatus.PREPARED)

    class _ModeloGuionado:
        def __init__(self, *guion):
            self.guion = list(guion)
            self.ofrecidas = []

        def bind_tools(self, tools, **_):
            self.ofrecidas.append([t["function"]["name"] for t in tools])
            return self

        def invoke(self, _):
            return self.guion.pop(0)

    modelo = _ModeloGuionado(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "consultar_estado_pedido",
                    "args": {"order_id": orden.order_id},
                    "id": "call-1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "respond_to_customer",
                    "args": {
                        "kind": "informacion",
                        "message": (
                            f"Tu pedido {orden.order_id} (2 x Crema Nivea 100 ml) está Preparado y listo para retirar. "
                            "¿Necesitás armar otro pedido?"
                        ),
                    },
                    "id": "call-2",
                    "type": "tool_call",
                }
            ],
        ),
    )

    agent = NormaAgent(modelo, InMemorySaver())
    outcome = agent.handle(
        "thread-estado-1",
        text=f"¿cómo viene mi pedido {orden.order_id}?",
        customer_id="telegram:e2e-estado",
        channel=Channel.TELEGRAM,
    )

    # En la primera iteración ya estaba habilitada por preactivación de `informar`
    assert "consultar_estado_pedido" in modelo.ofrecidas[0]
    assert outcome.kind == "informacion"
    assert orden.order_id in outcome.message
    assert "Preparado" in outcome.message
    assert not outcome.cerrada
