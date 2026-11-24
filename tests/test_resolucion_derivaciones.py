"""Pruebas exhaustivas para la resolución de derivaciones humanas y descuentos autorizados.

Cubre:
1. Cálculo determinístico de subtotales y descuentos en OrderDraft y Order.
2. Formato de propuestas y órdenes con descuento en validation_service.
3. Guardrails de promesas comerciales y precios con descuento autorizado.
4. Flujo completo: derivación -> silenciado -> resolución operador -> reactivación -> confirmación con descuento.
5. Silencio persistente cuando el operador no reactiva el bot.
6. Manejo de errores de conexión con Telegram API (502 Bad Gateway).
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import HumanMessage
from sqlalchemy import delete, select

from internal.agent import guardrails, norma_agent
from internal.database.connection import SessionLocal, init_db
from internal.database.models import ConversationRecord, DraftRecord, OrderRecord
from internal.schemas.dashboard_schema import ConversationResolveRequest
from internal.schemas.order_schema import (
    Channel,
    IncomingMessage,
    Order,
    OrderDraft,
    OrderItem,
)
from internal.services import (
    conversation_service,
    dashboard_service,
    telegram_service,
    validation_service,
)


class _FakeToolChatModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture(autouse=True)
def clean_state(monkeypatch, tmp_path):
    from internal.config import get_settings

    monkeypatch.setenv("CHECKPOINT_DB", str(tmp_path / "checkpoints.db"))
    get_settings.cache_clear()
    init_db()
    with SessionLocal() as session:
        session.execute(delete(OrderRecord))
        session.execute(delete(DraftRecord))
        session.execute(delete(ConversationRecord))
        session.commit()
    monkeypatch.setattr(norma_agent, "get_chat_model", lambda: _FakeToolChatModel(messages=iter([])))
    norma_agent.get_agent.cache_clear()
    yield
    with SessionLocal() as session:
        session.execute(delete(OrderRecord))
        session.execute(delete(DraftRecord))
        session.execute(delete(ConversationRecord))
        session.commit()
    norma_agent.get_agent.cache_clear()
    get_settings.cache_clear()


def test_order_draft_discount_calculations():
    items = [
        OrderItem(sku="SKU-1", product_name="Aceite 1L", quantity=10, unit_price=Decimal("2000.00")),
        OrderItem(sku="SKU-2", product_name="Fideos 500g", quantity=5, unit_price=Decimal("1000.00")),
    ]
    # Subtotal = 10*2000 + 5*1000 = 25000.00
    draft_no_discount = OrderDraft(
        draft_id="d-1",
        customer_id="cust-1",
        channel=Channel.TELEGRAM,
        items=items,
    )
    assert draft_no_discount.subtotal == Decimal("25000.00")
    assert draft_no_discount.total == Decimal("25000.00")

    # Con 10% de descuento: descuento = 2500.00, total = 22500.00
    draft_with_discount = OrderDraft(
        draft_id="d-2",
        customer_id="cust-1",
        channel=Channel.TELEGRAM,
        items=items,
        discount_percentage=10.0,
    )
    assert draft_with_discount.subtotal == Decimal("25000.00")
    assert draft_with_discount.total == Decimal("22500.00")

    # Con 15.5% de descuento: descuento = 3875.00, total = 21125.00
    draft_fractional = OrderDraft(
        draft_id="d-3",
        customer_id="cust-1",
        channel=Channel.TELEGRAM,
        items=items,
        discount_percentage=15.5,
    )
    assert draft_fractional.total == Decimal("21125.00")


def test_validation_service_formatting_with_discount():
    from datetime import UTC, datetime

    items = [
        OrderItem(sku="SKU-1", product_name="Aceite 1L", quantity=10, unit_price=Decimal("2000.00")),
    ]
    draft = OrderDraft(
        draft_id="d-1",
        customer_id="cust-1",
        channel=Channel.TELEGRAM,
        items=items,
        discount_percentage=10.0,
    )
    detail = validation_service.build_proposal_detail(draft)
    assert "Subtotal: $20000.00" in detail
    assert "Descuento (10%): -$2000.00" in detail
    assert "Total estimado: $18000.00" in detail

    order = Order(
        order_id="ORD-0001",
        draft_id="d-1",
        created_at=datetime.now(UTC),
        customer_id="cust-1",
        channel=Channel.TELEGRAM,
        items=items,
        total=draft.total,
        discount_percentage=draft.discount_percentage,
    )
    assert order.total == Decimal("18000.00")
    assert order.discount_percentage == 10.0

    order_msg = validation_service.build_order_message(order)
    assert "Subtotal: $20000.00" in order_msg
    assert "Descuento (10%): -$2000.00" in order_msg
    assert "Total: $18000.00" in order_msg


def test_guardrails_discount_authorization():
    message = "Te hago un 10% de descuento en el total."
    items = [OrderItem(sku="SKU-1", product_name="Aceite 1L", quantity=10, unit_price=Decimal("2000.00"))]
    draft = OrderDraft(
        draft_id="d-1", customer_id="c-1", channel=Channel.TELEGRAM, items=items, discount_percentage=10.0
    )

    # Sin descuento autorizado: guardrail rechaza promesas comerciales ("descuentos")
    rechazos_sin = guardrails.validar_salida(
        message=message,
        kind="propuesta",
        propuesta=draft,
        descuento_autorizado=None,
    )
    reglas_sin = [r.regla for r in rechazos_sin]
    assert "promesa" in reglas_sin

    # Con descuento autorizado coincidente (10%): guardrail lo permite limpiamente
    rechazos_con = guardrails.validar_salida(
        message=message,
        kind="propuesta",
        propuesta=draft,
        descuento_autorizado=10.0,
    )
    reglas_con = [r.regla for r in rechazos_con]
    assert "promesa" not in reglas_con

    # Con descuento autorizado distinto (5% autorizado pero el mensaje dice 10%): guardrail lo rechaza
    rechazos_distinto = guardrails.validar_salida(
        message=message,
        kind="propuesta",
        propuesta=draft,
        descuento_autorizado=5.0,
    )
    reglas_distinto = [r.regla for r in rechazos_distinto]
    assert "promesa" in reglas_distinto

    # Con descuento autorizado (10%), pero prometiendo además algo gratis: guardrail lo rechaza
    rechazos_gratis = guardrails.validar_salida(
        message="Te hago un 10% de descuento en el total y el envío te queda gratis.",
        kind="propuesta",
        propuesta=draft,
        descuento_autorizado=10.0,
    )
    reglas_gratis = [r.regla for r in rechazos_gratis]
    assert "promesa" in reglas_gratis


def test_full_derivation_resolution_and_reactivation_flow(monkeypatch):
    customer_id = "telegram:777888"
    channel = Channel.TELEGRAM

    # 1. Simular conversación que se deriva a humano
    with SessionLocal() as session:
        conv = ConversationRecord(
            customer_id=customer_id,
            channel=channel.value,
            status="derivada",
            derivation_reason="condicion_comercial",
        )
        session.add(conv)
        session.commit()
        session.refresh(conv)
        conv_id = conv.id

    # Comprobamos que mientras esté derivada, el bot está silenciado
    assert conversation_service._is_silenced_by_derivation(customer_id, channel) is True

    # Mensaje entrante durante silencio: el bot devuelve respuesta vacía y no procesa
    reply_silenced = conversation_service.handle_message(
        IncomingMessage(customer_id=customer_id, channel=channel, text="Hola, ¿hay alguien?")
    )
    assert reply_silenced.text == ""

    # 2. Operador resuelve desde el Dashboard
    telegram_sent = []

    class FakeTelegram:
        def send_message(self, chat_id: int, text: str):
            telegram_sent.append((chat_id, text))

    monkeypatch.setattr(telegram_service, "get_client", lambda: FakeTelegram())

    req = ConversationResolveRequest(
        resolution_type="descuento_volumen",
        message="¡Hola! Te confirmamos un 10% de descuento en tu compra. Confirmame si te lo preparamos.",
        discount_percentage=10.0,
        operator_notes="Autorizado por gerente",
        reactivate_bot=True,
    )
    detail = dashboard_service.resolve_conversation(conv_id, req)

    assert detail.status == "abierta"
    assert detail.discount_percentage == 10.0
    assert len(telegram_sent) == 1
    assert telegram_sent[0][0] == 777888

    # 3. Al reactivar, el bot deja de estar silenciado
    assert conversation_service._is_silenced_by_derivation(customer_id, channel) is False

    # 4. Estado en checkpointer tiene el descuento y mensaje del operador
    agent = norma_agent.get_agent()
    st = agent.graph.get_state({"configurable": {"thread_id": str(conv_id)}})
    assert st.values.get("descuento_autorizado") == 10.0
    messages = st.values.get("messages", [])
    assert any("[Operador]" in str(m.content) for m in messages)


def test_derivation_resolution_without_reactivation(monkeypatch):
    customer_id = "telegram:555666"
    channel = Channel.TELEGRAM

    with SessionLocal() as session:
        conv = ConversationRecord(
            customer_id=customer_id,
            channel=channel.value,
            status="derivada",
            derivation_reason="reclamo",
        )
        session.add(conv)
        session.commit()
        session.refresh(conv)
        conv_id = conv.id

    monkeypatch.setattr(telegram_service, "get_client", lambda: type("C", (), {"send_message": lambda *a: None})())

    req = ConversationResolveRequest(
        resolution_type="mensaje_directo",
        message="Hola, lo estamos revisando.",
        discount_percentage=None,
        operator_notes="Seguimiento manual",
        reactivate_bot=False,
    )
    detail = dashboard_service.resolve_conversation(conv_id, req)
    assert detail.status == "derivada"
    # El bot debe continuar silenciado
    assert conversation_service._is_silenced_by_derivation(customer_id, channel) is True


def test_telegram_error_raises_http_502(monkeypatch):
    with SessionLocal() as session:
        conv = ConversationRecord(
            customer_id="telegram:111222",
            channel="telegram",
            status="derivada",
            derivation_reason="condicion_comercial",
        )
        session.add(conv)
        session.commit()
        session.refresh(conv)
        conv_id = conv.id

    class FailingTelegram:
        def send_message(self, chat_id: int, text: str):
            raise telegram_service.TelegramError("Telegram API down")

    monkeypatch.setattr(telegram_service, "get_client", lambda: FailingTelegram())

    req = ConversationResolveRequest(
        resolution_type="descuento_volumen",
        message="Hola",
        discount_percentage=10.0,
        reactivate_bot=True,
    )
    with pytest.raises(HTTPException) as exc_info:
        dashboard_service.resolve_conversation(conv_id, req)
    assert exc_info.value.status_code == 502
    assert "Telegram API down" in str(exc_info.value.detail)


def test_silenced_messages_recorded_for_operator_view(monkeypatch):
    customer_id = "telegram:333444"
    channel = Channel.TELEGRAM

    with SessionLocal() as session:
        conv = ConversationRecord(
            customer_id=customer_id,
            channel=channel.value,
            status="derivada",
            derivation_reason="condicion_comercial",
        )
        session.add(conv)
        session.commit()
        session.refresh(conv)
        conv_id = conv.id

    # 1. Cliente envía mensaje mientras está derivado
    msg = IncomingMessage(
        customer_id=customer_id,
        channel=channel,
        type="text",
        text="Hola, quiero pedir 20 botellas de aceite Natura con descuento por bulto",
    )
    reply = conversation_service.handle_message(msg)
    # El bot permanece silenciado (RF-27)
    assert reply.text == ""

    # 2. El operador en el dashboard ve el mensaje que envió el cliente
    detail = dashboard_service.get_conversation_detail(conv_id)
    assert any("20 botellas de aceite Natura" in m.content for m in detail.messages)

    # 3. Operador resuelve y reactiva el bot
    monkeypatch.setattr(telegram_service, "get_client", lambda: type("C", (), {"send_message": lambda *a: None})())
    req = ConversationResolveRequest(
        resolution_type="descuento_volumen",
        message="Te autorizamos 15% de descuento.",
        discount_percentage=15.0,
        reactivate_bot=True,
    )
    resolved_detail = dashboard_service.resolve_conversation(conv_id, req)
    assert resolved_detail.status == "abierta"

    # 4. El grafo no debe tener tareas intermedias encoladas (next debe ser vacío o el nodo inicial)
    agent = norma_agent.get_agent()
    st = agent.graph.get_state({"configurable": {"thread_id": str(conv_id)}})
    assert st.next in ((), ("armar_contexto",), ("encaminar",))


def test_resolve_conversation_canal_no_soportado_devuelve_501():
    """Enviar mensaje desde el dashboard por un canal sin salida implementada (ej. web) devuelve 501."""
    with SessionLocal() as session:
        conv = ConversationRecord(
            customer_id="web:cliente-1",
            channel="web",
            status="derivada",
            derivation_reason="condicion_comercial",
        )
        session.add(conv)
        session.commit()
        session.refresh(conv)
        conv_id = conv.id

    req = ConversationResolveRequest(
        resolution_type="descuento_volumen",
        message="Te autorizamos un 10%.",
        discount_percentage=10.0,
        reactivate_bot=True,
    )
    with pytest.raises(HTTPException) as exc_info:
        dashboard_service.resolve_conversation(conv_id, req)
    assert exc_info.value.status_code == 501
    assert "web" in str(exc_info.value.detail)


def test_guardrails_rechaza_porcentaje_en_letras_distinto():
    """Con 5% autorizado, porcentajes no coincidentes, no listados («dieciocho») o sin cifra se rechazan."""
    msg = "Te hago un veinte por ciento de descuento en el total."
    rechazos_mal = guardrails.validar_salida(message=msg, kind="informacion", descuento_autorizado=5.0)
    assert "promesa" in [r.regla for r in rechazos_mal]

    rechazos_no_listado = guardrails.validar_salida(
        message="Te hago un dieciocho por ciento de descuento.", kind="informacion", descuento_autorizado=5.0
    )
    assert "promesa" in [r.regla for r in rechazos_no_listado]

    rechazos_sin_cifra = guardrails.validar_salida(
        message="Te hago un descuento extra por cantidad.", kind="informacion", descuento_autorizado=5.0
    )
    assert "promesa" in [r.regla for r in rechazos_sin_cifra]

    rechazos_ok = guardrails.validar_salida(message=msg, kind="informacion", descuento_autorizado=20.0)
    assert "promesa" not in [r.regla for r in rechazos_ok]


def test_merge_order_notes_conserva_solicitud_y_no_duplica():
    """Combina solicitud original del cliente + acuerdo confirmado sin duplicar al reintentar."""
    solicitud = "Solicitud del cliente: retiro mañana a la mañana."
    merged1 = validation_service.merge_order_notes(solicitud, "retiro de 9 a 12")
    assert merged1 == "Solicitud del cliente: retiro mañana a la mañana. Acuerdo confirmado: retiro de 9 a 12"

    # Reintento con el mismo acuerdo: idempotente
    merged2 = validation_service.merge_order_notes(merged1, "retiro de 9 a 12")
    assert merged2 == merged1

    # Reintento con acuerdo actualizado: reemplaza el acuerdo sin tocar la solicitud original
    merged3 = validation_service.merge_order_notes(merged2, "Acuerdo confirmado: retiro de 10 a 13")
    assert merged3 == "Solicitud del cliente: retiro mañana a la mañana. Acuerdo confirmado: retiro de 10 a 13"


def test_resolucion_con_close_conversation_marca_resuelta(monkeypatch):
    """Con reactivate_bot=False y close_conversation=True, la conversación pasa a estado 'resuelta'."""
    customer_id = "telegram:998877"
    with SessionLocal() as session:
        conv = ConversationRecord(
            customer_id=customer_id,
            channel="telegram",
            status="derivada",
            derivation_reason="reclamo",
        )
        session.add(conv)
        session.commit()
        session.refresh(conv)
        conv_id = conv.id

    monkeypatch.setattr(telegram_service, "get_client", lambda: type("C", (), {"send_message": lambda *a: None})())
    req = ConversationResolveRequest(
        resolution_type="mensaje_directo",
        message="Ya quedó resuelto tu reclamo, ¡gracias!",
        reactivate_bot=False,
        close_conversation=True,
    )
    detail = dashboard_service.resolve_conversation(conv_id, req)
    assert detail.status == "resuelta"
    assert conversation_service._is_silenced_by_derivation(customer_id, Channel.TELEGRAM) is False


def test_confirmacion_con_borrador_actualizado_muestra_total_antes_de_crear_orden(monkeypatch):
    """Si el operador cambia descuento/acuerdos sobre una propuesta previa, 'dale' muestra el nuevo total primero."""
    from langchain_core.messages import AIMessage
    from sqlalchemy import select

    from internal.schemas.catalog_schema import Product
    from internal.services import inventory_service

    prod = Product(sku="CR-NIV-100", name="Crema Nivea", presentation="100 ml", price=Decimal("1000.00"), stock=20)
    monkeypatch.setattr(inventory_service, "get_catalog", lambda *a, **kw: [prod])

    customer_id = "telegram:445566"
    with SessionLocal() as session:
        conv = ConversationRecord(
            customer_id=customer_id,
            channel="telegram",
            status="derivada",
            derivation_reason="condicion_comercial",
        )
        session.add(conv)
        session.commit()
        session.refresh(conv)
        conv_id = conv.id

    # Simulamos una conversación pausada con propuesta vigente de 2 x $1000 = $2000
    call_propuesta = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "respond_to_customer",
                "args": {
                    "kind": "propuesta",
                    "message": "Te armé 2 Cremas Nivea. ¿Confirmo?",
                    "propuesta": [{"sku": "CR-NIV-100", "quantity": 2}],
                    "notes": "Solicitud del cliente: retiro mañana a la mañana.",
                },
                "id": "call-prop-1",
                "type": "tool_call",
            }
        ],
    )
    monkeypatch.setattr(norma_agent, "get_chat_model", lambda: _FakeToolChatModel(messages=iter([call_propuesta])))
    norma_agent.get_agent.cache_clear()
    agent = norma_agent.get_agent()

    r1 = agent.handle(str(conv_id), customer_id, Channel.TELEGRAM, "quiero 2 cremas nivea")
    assert r1.kind == "propuesta"
    assert r1.draft is not None
    assert r1.draft.total == Decimal("2000.00")

    # Operador autoriza 15% de descuento y acuerdo operativo de retiro
    monkeypatch.setattr(telegram_service, "get_client", lambda: type("C", (), {"send_message": lambda *a: None})())
    dashboard_service.resolve_conversation(
        conv_id,
        ConversationResolveRequest(
            resolution_type="descuento_volumen",
            message="Te autorizamos 15% de descuento y retiro de 9 a 12.",
            discount_percentage=15.0,
            order_notes="retiro de 9 a 12",
            operator_notes="INTERNO_NO_FILTRAR",
            reactivate_bot=True,
        ),
    )

    # Primer 'dale': NO crea la orden todavía; muestra el total actualizado ($1700) y el acuerdo confirmado
    r2 = agent.handle(str(conv_id), customer_id, Channel.TELEGRAM, "dale")
    assert r2.kind == "propuesta"
    assert r2.order_id is None
    assert "Descuento (15%): -$300.00" in r2.message
    assert "Total estimado: $1700.00" in r2.message
    assert "Acuerdo confirmado: retiro de 9 a 12" in r2.message
    assert "INTERNO_NO_FILTRAR" not in r2.message

    # Segundo 'dale' tras ver el total actualizado: ahora sí confirma y crea la orden
    r3 = agent.handle(str(conv_id), customer_id, Channel.TELEGRAM, "dale")
    assert r3.kind == "pedido"
    assert r3.order_id is not None
    assert "Total: $1700.00" in r3.message

    with SessionLocal() as session:
        order = session.scalar(select(OrderRecord).where(OrderRecord.order_id == r3.order_id))
        assert order is not None
        assert order.total == Decimal("1700.00")
        assert order.discount_percentage == Decimal("15.0")
        assert "Solicitud del cliente: retiro mañana a la mañana." in (order.notes or "")
        assert "Acuerdo confirmado: retiro de 9 a 12" in (order.notes or "")
        assert "INTERNO_NO_FILTRAR" not in (order.notes or "")


def test_flujo_completo_resolucion_propuesta_confirmacion_csv_y_sheet2_sin_notas_internas(monkeypatch, tmp_path):
    """Prueba E2E con servicios falsos: resolución -> propuesta -> confirmación -> BD/CSV/Sheet 2.

    Verifica que:
    - `operator_notes` NUNCA aparece en el contexto del LLM, la propuesta, la BD, el CSV ni Sheet 2.
    - `order_notes` (acuerdo confirmado) se combina con la solicitud del cliente sin duplicar al reintentar.
    - El descuento numérico se aplica de punta a punta.
    """
    import csv

    from langchain_core.messages import AIMessage
    from sqlalchemy import select

    from internal.agent import context
    from internal.config import get_settings
    from internal.schemas.catalog_schema import Product
    from internal.services import google_sheets_client, inventory_service
    from tests.test_planillas import ORDERS_ID, FakeSheets

    # Configurar catálogo, CSV temporal y Sheet 2 falsa
    prod = Product(sku="SH-PAN-200", name="Shampoo Pantene", presentation="200 ml", price=Decimal("4000.00"), stock=50)
    monkeypatch.setattr(inventory_service, "get_catalog", lambda *a, **kw: [prod])

    csv_path = tmp_path / "pedidos.csv"
    monkeypatch.setenv("ORDERS_FILE", str(csv_path))
    monkeypatch.setenv("GOOGLE_CREDENTIALS_FILE", "credentials/no-se-lee-en-los-tests.json")
    monkeypatch.setenv("ORDERS_SHEET_ID", ORDERS_ID)
    get_settings.cache_clear()

    fake_sheets = FakeSheets()
    monkeypatch.setattr(google_sheets_client, "get_client", lambda: fake_sheets)
    monkeypatch.setattr(telegram_service, "get_client", lambda: type("C", (), {"send_message": lambda *a: None})())

    nota_interna = "CONFIDENCIAL_MARGEN_INTERNO_999"
    customer_id = "telegram:860907231"
    with SessionLocal() as session:
        conv = ConversationRecord(
            customer_id=customer_id,
            channel="telegram",
            status="derivada",
            derivation_reason="condicion_comercial",
        )
        session.add(conv)
        session.commit()
        session.refresh(conv)
        conv_id = conv.id

    # 1. Resolución del operador (con reintento antes de reactivar para verificar no-duplicación)
    req_previa = ConversationResolveRequest(
        resolution_type="descuento_volumen",
        message="Te autorizamos 15% de descuento y retiro de 9 a 12.",
        discount_percentage=15.0,
        order_notes="retiro de 9 a 12",
        operator_notes=nota_interna,
        reactivate_bot=False,
    )
    req_final = ConversationResolveRequest(
        resolution_type="descuento_volumen",
        message="Confirmamos las condiciones y te paso con Norma para cerrar el pedido.",
        discount_percentage=15.0,
        order_notes="retiro de 9 a 12",
        operator_notes=nota_interna,
        reactivate_bot=True,
    )
    dashboard_service.resolve_conversation(conv_id, req_previa)
    dashboard_service.resolve_conversation(conv_id, req_final)  # reintento idempotente

    # 2. Comprobar que el contexto que ve el LLM NO incluye `operator_notes` pero SÍ `acuerdo_operativo` y 15%
    agent = norma_agent.get_agent()
    st_values = agent.graph.get_state({"configurable": {"thread_id": str(conv_id)}}).values
    llm_msgs = context.build_messages(st_values)
    llm_dump = "\n".join(str(m.content) for m in llm_msgs)
    assert nota_interna not in llm_dump
    assert "retiro de 9 a 12" in llm_dump
    assert "15%" in llm_dump

    # 3. Propuesta armada por el agente falso conservando la solicitud original del cliente
    call_propuesta = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "respond_to_customer",
                "args": {
                    "kind": "propuesta",
                    "message": "Te armo 10 Shampoo Pantene con el 15% de descuento autorizado. ¿Confirmás?",
                    "propuesta": [{"sku": "SH-PAN-200", "quantity": 10}],
                    "notes": "Solicitud del cliente: retiro mañana a la mañana.",
                },
                "id": "call-e2e-1",
                "type": "tool_call",
            }
        ],
    )
    monkeypatch.setattr(norma_agent, "get_chat_model", lambda: _FakeToolChatModel(messages=iter([call_propuesta])))
    norma_agent.get_agent.cache_clear()
    agent = norma_agent.get_agent()

    res_prop = agent.handle(str(conv_id), customer_id, Channel.TELEGRAM, "armame 10 shampoo pantene")
    assert res_prop.kind == "propuesta"
    assert "Subtotal: $40000.00" in res_prop.message
    assert "Descuento (15%): -$6000.00" in res_prop.message
    assert "Total estimado: $34000.00" in res_prop.message
    assert "Solicitud del cliente: retiro mañana a la mañana. Acuerdo confirmado: retiro de 9 a 12" in res_prop.message
    assert nota_interna not in res_prop.message
    assert res_prop.message.count("Acuerdo confirmado:") == 1

    # 4. Confirmación del cliente
    res_conf = agent.handle(str(conv_id), customer_id, Channel.TELEGRAM, "dale")
    assert res_conf.kind == "pedido"
    assert res_conf.order_id is not None
    assert nota_interna not in res_conf.message

    # 5. Verificar BD
    with SessionLocal() as session:
        order = session.scalar(select(OrderRecord).where(OrderRecord.order_id == res_conf.order_id))
        assert order is not None
        assert order.total == Decimal("34000.00")
        assert order.discount_percentage == Decimal("15.0")
        assert order.notes == "Solicitud del cliente: retiro mañana a la mañana. Acuerdo confirmado: retiro de 9 a 12"
        assert nota_interna not in (order.notes or "")

    # 6. Verificar CSV
    assert csv_path.exists()
    with csv_path.open("r", encoding="utf-8") as fh:
        csv_rows = list(csv.DictReader(fh))
    assert len(csv_rows) == 1
    assert csv_rows[0]["notas"] == (
        "Solicitud del cliente: retiro mañana a la mañana. Acuerdo confirmado: retiro de 9 a 12"
    )
    assert nota_interna not in str(csv_rows[0])

    # 7. Verificar columna `notas` en Sheet 2 (FakeSheets)
    sheet_rows = fake_sheets.hojas[ORDERS_ID]
    assert len(sheet_rows) >= 2  # encabezado + fila del pedido
    header = sheet_rows[0]
    notas_idx = header.index("notas")
    row_notas = sheet_rows[-1][notas_idx]
    assert row_notas == "Solicitud del cliente: retiro mañana a la mañana. Acuerdo confirmado: retiro de 9 a 12"
    assert nota_interna not in str(sheet_rows[-1])


def test_resolve_conversation_rechaza_conversacion_no_derivada_con_409():
    """Intentar resolver una conversación que no está 'derivada' (ej. 'cerrada') devuelve 409 Conflict."""
    with SessionLocal() as session:
        conv = ConversationRecord(
            customer_id="telegram:555111",
            channel="telegram",
            status="cerrada",
        )
        session.add(conv)
        session.commit()
        session.refresh(conv)
        conv_id = conv.id

    req = ConversationResolveRequest(
        resolution_type="mensaje_directo",
        message="Hola",
        reactivate_bot=True,
    )
    with pytest.raises(HTTPException) as exc_info:
        dashboard_service.resolve_conversation(conv_id, req)
    assert exc_info.value.status_code == 409


def test_resolve_conversation_segunda_resolucion_mantiene_consistencia_bd_y_estado(monkeypatch):
    """Una segunda resolución sin campos opcionales conserva los previos; enviarlos en None limpia ambos lados."""
    monkeypatch.setattr(telegram_service, "get_client", lambda: type("C", (), {"send_message": lambda *a: None})())
    with SessionLocal() as session:
        conv = ConversationRecord(
            customer_id="telegram:777333",
            channel="telegram",
            status="derivada",
            derivation_reason="condicion_comercial",
        )
        session.add(conv)
        session.commit()
        session.refresh(conv)
        conv_id = conv.id

    # 1. Primera resolución: otorga 10% + notas, sin reactivar
    dashboard_service.resolve_conversation(
        conv_id,
        ConversationResolveRequest(
            resolution_type="descuento_volumen",
            message="Te autorizamos 10%.",
            discount_percentage=10.0,
            order_notes="retiro 10 a 12",
            operator_notes="aprobado por gerencia",
            reactivate_bot=False,
        ),
    )

    # 2. Segunda resolución: manda solo mensaje omitiendo descuento y notas -> preserva en BD y checkpointer
    detail2 = dashboard_service.resolve_conversation(
        conv_id,
        ConversationResolveRequest(
            resolution_type="mensaje_directo",
            message="¿Necesitás algo más?",
            reactivate_bot=False,
        ),
    )
    agent = norma_agent.get_agent()
    st2 = agent.graph.get_state({"configurable": {"thread_id": str(conv_id)}}).values
    assert detail2.discount_percentage == 10.0
    assert detail2.operator_notes == "aprobado por gerencia"
    assert detail2.order_notes == "retiro 10 a 12"
    assert st2.get("descuento_autorizado") == 10.0
    assert st2.get("acuerdo_operativo") == "retiro 10 a 12"

    # 3. Tercera resolución: borra explícitamente descuento y acuerdo con None -> limpia en BD y checkpointer
    detail3 = dashboard_service.resolve_conversation(
        conv_id,
        ConversationResolveRequest(
            resolution_type="mensaje_directo",
            message="Finalmente queda precio de lista.",
            discount_percentage=None,
            order_notes=None,
            reactivate_bot=True,
        ),
    )
    st3 = agent.graph.get_state({"configurable": {"thread_id": str(conv_id)}}).values
    assert detail3.discount_percentage is None
    assert detail3.order_notes is None
    assert st3.get("descuento_autorizado") is None
    assert st3.get("acuerdo_operativo") is None


def _conversacion(status: str = "derivada", customer_id: str = "telegram:424242") -> int:
    with SessionLocal() as session:
        conv = ConversationRecord(
            customer_id=customer_id, channel="telegram", status=status, derivation_reason="condicion_comercial"
        )
        session.add(conv)
        session.commit()
        return conv.id


def _telegram(monkeypatch) -> list[tuple[int, str]]:
    enviados: list[tuple[int, str]] = []
    cliente = type("C", (), {"send_message": lambda self, chat_id, text: enviados.append((chat_id, text))})()
    monkeypatch.setattr(telegram_service, "get_client", lambda: cliente)
    return enviados


def test_guardrails_rechaza_otro_descuento_sin_porcentaje():
    """Con un 10% autorizado, prometer «otro descuento» sin porcentaje es una promesa nueva: se rechaza."""
    otra = guardrails.validar_salida(
        message="Te hago otro descuento si sumás una caja más.", kind="informacion", descuento_autorizado=10.0
    )
    assert "promesa" in [r.regla for r in otra]
    autorizado = guardrails.validar_salida(
        message="Ya tenés aplicado el 10% de descuento.", kind="informacion", descuento_autorizado=10.0
    )
    assert "promesa" not in [r.regla for r in autorizado]


@pytest.mark.parametrize("status", ["cerrada", "vencida", "descartada", "abierta", "resuelta"])
def test_solo_se_resuelve_una_conversacion_derivada(monkeypatch, status):
    """Resolver una conversación que no está derivada la reabriría y saltearía el vencimiento de 24 h (RF-28)."""
    conv_id = _conversacion(status)
    enviados = _telegram(monkeypatch)
    req = ConversationResolveRequest(resolution_type="mensaje_directo", message="Hola", reactivate_bot=True)
    with pytest.raises(HTTPException) as exc_info:
        dashboard_service.resolve_conversation(conv_id, req)
    assert exc_info.value.status_code == 409
    assert enviados == []
    with SessionLocal() as session:
        assert session.get_one(ConversationRecord, conv_id).status == status


def test_si_falla_el_estado_del_agente_la_conversacion_sigue_derivada(monkeypatch):
    """Si falla el estado del agente, no se guarda ni se envía nada: el operador puede volver a resolverla."""
    conv_id = _conversacion()
    enviados = _telegram(monkeypatch)

    class GrafoRoto:
        def get_state(self, config):
            return type("S", (), {"values": {}})()

        def update_state(self, config, values):
            raise RuntimeError("checkpointer caído")

    monkeypatch.setattr(dashboard_service, "get_agent", lambda: type("A", (), {"graph": GrafoRoto()})())
    req = ConversationResolveRequest(
        resolution_type="descuento_volumen", message="Te autorizamos 10%.", discount_percentage=10.0
    )
    with pytest.raises(HTTPException) as exc_info:
        dashboard_service.resolve_conversation(conv_id, req)
    assert exc_info.value.status_code == 500
    assert enviados == []  # el mensaje sale después de actualizar al agente: el cliente no recibe nada a medias
    with SessionLocal() as session:
        conv = session.get_one(ConversationRecord, conv_id)
        assert conv.status == "derivada"
        assert conv.discount_percentage is None


def test_borrar_el_acuerdo_lo_saca_tambien_de_la_propuesta(monkeypatch):
    """Borrar descuento y acuerdo los saca de la base, del agente y de la propuesta vigente, no solo de la base."""
    conv_id = _conversacion()
    _telegram(monkeypatch)
    agent = norma_agent.get_agent()
    config = {"configurable": {"thread_id": str(conv_id)}}
    items = [OrderItem(sku="SKU-1", product_name="Aceite 1L", quantity=2, unit_price=Decimal("100.00"))]
    propuesta = OrderDraft(
        draft_id="d-res", customer_id="telegram:424242", channel=Channel.TELEGRAM, items=items, notes="Sin bolsa."
    )
    agent.graph.update_state(config, {"propuesta": propuesta.model_dump(mode="json")})

    con_descuento = ConversationResolveRequest(
        resolution_type="descuento_volumen",
        discount_percentage=10.0,
        order_notes="retiro de 9 a 12",
        reactivate_bot=False,
    )
    dashboard_service.resolve_conversation(conv_id, con_descuento)
    valores = agent.graph.get_state(config).values
    assert valores["descuento_autorizado"] == 10.0
    assert valores["propuesta"]["discount_percentage"] == 10.0
    assert valores["propuesta"]["notes"] == "Sin bolsa. Acuerdo confirmado: retiro de 9 a 12"

    sin_descuento = ConversationResolveRequest(
        resolution_type="mensaje_directo", discount_percentage=None, order_notes=None, reactivate_bot=False
    )
    detail = dashboard_service.resolve_conversation(conv_id, sin_descuento)
    assert detail.discount_percentage is None
    assert detail.order_notes is None
    valores = agent.graph.get_state(config).values
    assert valores["descuento_autorizado"] is None
    assert valores["acuerdo_operativo"] is None
    assert valores["propuesta"]["discount_percentage"] is None
    assert valores["propuesta"]["notes"] == "Sin bolsa."
    assert valores["propuesta_pendiente_mostrar"] is True


def test_sheet2_explica_el_descuento_del_total():
    """Con descuento, `total` es menor que la suma de las líneas: la columna `descuento` dice por qué."""
    from datetime import UTC, datetime

    from internal.services import sheets_service

    items = [OrderItem(sku="SKU-1", product_name="Aceite 1L", quantity=10, unit_price=Decimal("100.00"))]
    order = Order(
        order_id="ORD-0001",
        draft_id="d-1",
        created_at=datetime(2026, 11, 10, 15, 0, tzinfo=UTC),
        customer_id="telegram:1",
        channel=Channel.TELEGRAM,
        items=items,
        discount_percentage=12.5,
    )
    fila = dict(zip(sheets_service.ORDER_COLUMNS, sheets_service.format_order_row(order), strict=True))
    assert fila["total"] == "875.00"
    assert fila["descuento"] == "12.5"


def test_fallo_telegram_no_deja_descuento_en_agente(monkeypatch):
    conv_id = _conversacion()
    from internal.agent import norma_agent

    agent = norma_agent.get_agent()
    config = {"configurable": {"thread_id": str(conv_id)}}
    agent.graph.update_state(config, {"descuento_autorizado": None, "acuerdo_operativo": None})

    class TelegramCaido:
        def send_message(self, chat_id, text):
            raise telegram_service.TelegramError("Falla sintética")

    monkeypatch.setattr(telegram_service, "get_client", lambda: TelegramCaido())
    req = ConversationResolveRequest(
        resolution_type="descuento_volumen",
        discount_percentage=10,
        order_notes="Retiro de 9 a 12",
        message="Te paso las condiciones",
        reactivate_bot=True,
    )
    with pytest.raises(HTTPException) as exc:
        dashboard_service.resolve_conversation(conv_id, req)
    assert exc.value.status_code == 502
    with SessionLocal() as session:
        conv = session.get_one(ConversationRecord, conv_id)
        assert conv.status == "derivada"
        assert conv.discount_percentage is None
        assert conv.order_notes is None
    # El reintento sin descuento debería conservar el estado anterior (sin autorización).
    dashboard_service.resolve_conversation(
        conv_id,
        ConversationResolveRequest(
            resolution_type="mensaje_directo",
            reactivate_bot=True,
        ),
    )
    with SessionLocal() as session:
        conv = session.get_one(ConversationRecord, conv_id)
        assert conv.status == "abierta"
        assert conv.discount_percentage is None
    estado = agent.graph.get_state(config).values
    assert estado.get("descuento_autorizado") is None, "El rollback no revirtió el descuento del agente"
    assert estado.get("acuerdo_operativo") is None


@pytest.mark.parametrize("with_draft", [False, True])
def test_notas_largas_rechazan_resolucion_sin_recortar_ni_enviar(monkeypatch, with_draft):
    """Si el acuerdo no entra completo, 422 sin cambiar base/agente ni enviar mensaje."""
    from internal.schemas.order_schema import MAX_ORDER_NOTES_LENGTH

    conv_id = _conversacion()
    enviados = _telegram(monkeypatch)
    agent = norma_agent.get_agent()
    config = {"configurable": {"thread_id": str(conv_id)}}
    if with_draft:
        notas = ("Pedido sin bolsa. " * 17)[: MAX_ORDER_NOTES_LENGTH - 1]
        prop = OrderDraft(
            draft_id="d-notas-largas",
            customer_id="telegram:424242",
            channel=Channel.TELEGRAM,
            items=[OrderItem(sku="SKU-1", product_name="Producto sintético", quantity=1, unit_price=Decimal("100"))],
            notes=notas,
        )
        agent.graph.update_state(config, {"propuesta": prop.model_dump(mode="json")})
        from internal.services import order_service

        order_service.record_draft(prop.model_copy(update={"conversation_id": conv_id}))
        acuerdo = "Retirar de 9 a 12"
    else:
        acuerdo = "x" * MAX_ORDER_NOTES_LENGTH
    previo = agent.graph.get_state(config).values
    req = ConversationResolveRequest(
        resolution_type="envio_especial", order_notes=acuerdo, message="Solicitud sintética", discount_percentage=10
    )
    with pytest.raises(HTTPException) as exc:
        dashboard_service.resolve_conversation(conv_id, req)
    assert exc.value.status_code == 422
    assert enviados == []
    assert agent.graph.get_state(config).values == previo
    with SessionLocal() as session:
        conv = session.get_one(ConversationRecord, conv_id)
        assert conv.status == "derivada"
        assert conv.order_notes is None
        assert conv.discount_percentage is None


def test_notas_limite_exacto_se_conservan_completas():
    from internal.schemas.order_schema import MAX_ORDER_NOTES_LENGTH

    acuerdo = "Retirar de 9 a 12"
    sufijo = f". Acuerdo confirmado: {acuerdo}"
    base = "x" * (MAX_ORDER_NOTES_LENGTH - len(sufijo))
    combinado = validation_service.merge_order_notes(base, acuerdo)
    assert combinado == base + sufijo
    assert len(combinado) == MAX_ORDER_NOTES_LENGTH
    with pytest.raises(validation_service.OrderNotesTooLongError):
        validation_service.merge_order_notes(base + "x", acuerdo)


def test_fallo_commit_restaura_condiciones_del_agente(monkeypatch):
    """Un error al confirmar la base tampoco deja autorizaciones nuevas en el checkpointer."""
    from sqlalchemy.orm import Session

    conv_id = _conversacion()
    agent = norma_agent.get_agent()
    config = {"configurable": {"thread_id": str(conv_id)}}
    agent.graph.update_state(config, {"descuento_autorizado": 5, "acuerdo_operativo": "Retiro previo"})
    previo = agent.graph.get_state(config).values

    def fallar_commit(self):
        raise RuntimeError("Falla sintética de commit")

    monkeypatch.setattr(Session, "commit", fallar_commit)
    req = ConversationResolveRequest(
        resolution_type="condicion_pago", discount_percentage=10, order_notes="Retiro nuevo", reactivate_bot=True
    )
    with pytest.raises(HTTPException) as exc:
        dashboard_service.resolve_conversation(conv_id, req)
    assert exc.value.status_code == 500
    actual = agent.graph.get_state(config).values
    for key in ("descuento_autorizado", "acuerdo_operativo", "derivation_reason", "condicion_comercial"):
        assert actual.get(key) == previo.get(key)
    with SessionLocal() as session:
        assert session.get_one(ConversationRecord, conv_id).status == "derivada"
    monkeypatch.undo()


def test_tool_devuelve_error_corregible_si_no_entran_las_notas(monkeypatch):
    """El modelo puede corregir las notas; nunca recibe un falso enviado ni una propuesta recortada."""
    from internal.agent import tool_registry
    from internal.schemas.catalog_schema import Product
    from internal.services import sheets_service

    monkeypatch.setattr(
        sheets_service,
        "get_catalog",
        lambda: [Product(sku="SKU-SINT", name="Producto sintético", price=Decimal("100"), stock=10)],
    )
    ctx = tool_registry.TurnContext(
        customer_id="cliente-sintetico",
        channel=Channel.TELEGRAM,
        disponibles=["respond_to_customer"],
        acuerdo_operativo="Retiro de 9 a 12",
    )
    result = tool_registry.execute(
        "respond_to_customer",
        {
            "kind": "propuesta",
            "message": "¿Lo confirmás?",
            "propuesta": [{"sku": "SKU-SINT", "quantity": 1}],
            "notes": "x" * 299,
        },
        ctx,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "notas_demasiado_largas"
    assert ctx.propuesta is None
    assert ctx.respuesta is None


def test_si_falla_compensacion_no_informa_resolucion_exitosa(monkeypatch):
    """Un checkpointer caído durante la restauración deja un 503 explícito y la base sin resolver."""
    from types import SimpleNamespace

    conv_id = _conversacion()

    class Grafo:
        intentos = 0

        def get_state(self, config):
            return SimpleNamespace(values={})

        def update_state(self, config, values):
            self.intentos += 1
            if self.intentos > 1:
                raise RuntimeError("Checkpointer sintético caído")

    class TelegramCaido:
        def send_message(self, chat_id, text):
            raise telegram_service.TelegramError("Falla sintética")

    monkeypatch.setattr(dashboard_service, "get_agent", lambda: SimpleNamespace(graph=Grafo()))
    monkeypatch.setattr(telegram_service, "get_client", lambda: TelegramCaido())
    req = ConversationResolveRequest(resolution_type="mensaje_directo", message="Hola", discount_percentage=10)
    with pytest.raises(HTTPException) as exc:
        dashboard_service.resolve_conversation(conv_id, req)
    assert exc.value.status_code == 503
    with SessionLocal() as session:
        conv = session.get_one(ConversationRecord, conv_id)
        assert conv.status == "derivada"
        assert conv.discount_percentage is None


def test_reintento_repara_propuesta_residual_desde_base(monkeypatch):
    """Si una compensación anterior falló, el reintento sin campos no conserva un descuento residual."""
    conv_id = _conversacion()
    agent = norma_agent.get_agent()
    config = {"configurable": {"thread_id": str(conv_id)}}
    prop = OrderDraft(
        draft_id="d-residual",
        customer_id="telegram:424242",
        channel=Channel.TELEGRAM,
        items=[OrderItem(sku="SKU-SINT", product_name="Producto sintético", quantity=1, unit_price=Decimal("100"))],
        discount_percentage=10,
        notes="Sin bolsa. Acuerdo confirmado: retirar mañana",
    )
    agent.graph.update_state(
        config,
        {
            "propuesta": prop.model_dump(mode="json"),
            "descuento_autorizado": 10,
            "acuerdo_operativo": "retirar mañana",
        },
    )
    dashboard_service.resolve_conversation(
        conv_id,
        ConversationResolveRequest(
            resolution_type="mensaje_directo",
            reactivate_bot=True,
        ),
    )
    estado = agent.graph.get_state(config).values
    assert estado["descuento_autorizado"] is None
    assert estado["acuerdo_operativo"] is None
    assert estado["propuesta"]["discount_percentage"] is None
    assert estado["propuesta"]["notes"] == "Sin bolsa."
    assert estado["propuesta_pendiente_mostrar"] is True


def test_reactivar_vence_otra_abierta_y_descarta_borradores(monkeypatch):
    """Reactivar una conversación derivada vence cualquier otra conversación abierta y descarta sus borradores."""
    monkeypatch.setattr(telegram_service, "get_client", lambda: type("C", (), {"send_message": lambda *a: None})())
    customer_id = "telegram:123999"

    with SessionLocal() as session:
        # Conversación A (derivada hace unas horas)
        conv_a = ConversationRecord(
            customer_id=customer_id,
            channel="telegram",
            status="derivada",
            derivation_reason="solicitud_humano",
        )
        session.add(conv_a)
        session.flush()
        conv_a_id = conv_a.id

        # Conversación B (abierta más recientemente porque pasaron 24h, con un borrador pendiente)
        conv_b = ConversationRecord(
            customer_id=customer_id,
            channel="telegram",
            status="abierta",
        )
        session.add(conv_b)
        session.flush()
        conv_b_id = conv_b.id

        draft_b = DraftRecord(
            draft_id="draft-b-test",
            conversation_id=conv_b_id,
            customer_id=customer_id,
            channel="telegram",
            version=1,
            status="pendiente_confirmacion",
            items=[{"sku": "SKU-1", "product_name": "Prod", "quantity": 1}],
        )
        session.add(draft_b)
        session.commit()

    # Operador resuelve y reactiva la conversación A
    dashboard_service.resolve_conversation(
        conv_a_id,
        ConversationResolveRequest(
            resolution_type="mensaje_directo",
            reactivate_bot=True,
        ),
    )

    with SessionLocal() as session:
        a = session.get_one(ConversationRecord, conv_a_id)
        b = session.get_one(ConversationRecord, conv_b_id)
        draft = session.scalar(select(DraftRecord).where(DraftRecord.draft_id == "draft-b-test"))

        assert a.status == "abierta"
        assert b.status == "vencida"
        assert draft.status == "descartado"

    # Verificar que _abrir_conversacion devuelve la conversación reactivada
    active_id = conversation_service._abrir_conversacion(customer_id, Channel.TELEGRAM).id
    assert active_id == conv_a_id


def test_reactivar_reinicia_aclaraciones_seguidas_y_fallas(monkeypatch):
    """Reactivar desde el dashboard pone en 0 aclaraciones_seguidas y fallas en el agente."""
    monkeypatch.setattr(telegram_service, "get_client", lambda: type("C", (), {"send_message": lambda *a: None})())
    conv_id = _conversacion(status="derivada")

    agent = norma_agent.get_agent()
    config = {"configurable": {"thread_id": str(conv_id)}}
    agent.graph.update_state(config, {"aclaraciones_seguidas": 3, "fallas": 2})

    dashboard_service.resolve_conversation(
        conv_id,
        ConversationResolveRequest(
            resolution_type="mensaje_directo",
            reactivate_bot=True,
        ),
    )

    valores = agent.graph.get_state(config).values
    assert valores.get("aclaraciones_seguidas") == 0
    assert valores.get("fallas") == 0


def test_subtotal_y_total_consistentes_con_line_price(monkeypatch):
    """En get_orders y _order_revenue, líneas sin unit_price usan precio de catálogo de forma consistente."""
    from internal.schemas.catalog_schema import Product
    from internal.services import inventory_service

    monkeypatch.setattr(
        inventory_service,
        "get_catalog",
        lambda *a, **k: [
            Product(sku="SKU-CAT-1", name="Vaso Vidrio", price=Decimal("1000.00"), stock=10)
        ],
    )
    monkeypatch.setattr(
        dashboard_service,
        "_get_all_orders",
        lambda: [
            {
                "order_id": "ORD-TEST-PRICE",
                "customer_id": "c-price-1",
                "channel": "web",
                "status": "CONFIRMED",
                "created_at": datetime.now(UTC),
                "items": [{"sku": "SKU-CAT-1", "product_name": "Vaso Vidrio", "quantity": 2, "unit_price": None}],
                "total": Decimal("0"),  # Si se calculó con items_total valdría 0
                "discount_percentage": Decimal("10"),
            }
        ],
    )

    orders = dashboard_service.get_orders()
    matching = [o for o in orders if o.order_id == "ORD-TEST-PRICE"]
    assert len(matching) == 1
    item = matching[0]
    # 2 * $1000 = $2000 subtotal, con 10% de descuento = $1800 total
    assert item.subtotal == Decimal("2000")
    assert item.total == Decimal("1800.00")

    # Y en _order_revenue
    rev = dashboard_service._order_revenue(
        {"items": [{"sku": "SKU-CAT-1", "quantity": 2}], "discount_percentage": Decimal("10")},
        {"SKU-CAT-1": Decimal("1000")},
    )
    assert rev == Decimal("1800.00")


def test_silenced_message_con_audio_e_imagen(monkeypatch):
    """Si el cliente envía audio o foto durante el silencio de 24h, queda registrado en el historial."""
    from internal.schemas.order_schema import MessageType

    conv_id = _conversacion(status="derivada", customer_id="telegram:888999")
    agent = norma_agent.get_agent()
    config = {"configurable": {"thread_id": str(conv_id)}}

    msg_audio = IncomingMessage(
        customer_id="telegram:888999",
        channel=Channel.TELEGRAM,
        type=MessageType.AUDIO,
        text=None,
        media_url="telegram:audio_123",
    )
    conversation_service.handle_message(msg_audio)

    msg_image = IncomingMessage(
        customer_id="telegram:888999",
        channel=Channel.TELEGRAM,
        type=MessageType.IMAGE,
        text=None,
        media_url="telegram:image_123",
    )
    conversation_service.handle_message(msg_image)

    messages = agent.graph.get_state(config).values.get("messages", [])
    textos = [m.content for m in messages if isinstance(m, HumanMessage)]
    assert "[audio]" in textos
    assert "[imagen]" in textos
