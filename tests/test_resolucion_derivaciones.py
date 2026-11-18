"""Pruebas exhaustivas para la resolución de derivaciones humanas y descuentos autorizados.

Cubre:
1. Cálculo determinístico de subtotales y descuentos en OrderDraft y Order.
2. Formato de propuestas y órdenes con descuento en validation_service.
3. Guardrails de promesas comerciales y precios con descuento autorizado.
4. Flujo completo: derivación -> silenciado -> resolución operador -> reactivación -> confirmación con descuento.
5. Silencio persistente cuando el operador no reactiva el bot.
6. Manejo de errores de conexión con Telegram API (502 Bad Gateway).
"""

from decimal import Decimal

import pytest
from fastapi import HTTPException
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from sqlalchemy import delete

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


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    init_db()
    with SessionLocal() as session:
        session.execute(delete(OrderRecord))
        session.execute(delete(DraftRecord))
        session.execute(delete(ConversationRecord))
        session.commit()
    monkeypatch.setattr(norma_agent, "get_chat_model", lambda: GenericFakeChatModel(messages=iter([])))
    norma_agent.get_agent.cache_clear()
    yield
    with SessionLocal() as session:
        session.execute(delete(OrderRecord))
        session.execute(delete(DraftRecord))
        session.execute(delete(ConversationRecord))
        session.commit()
    norma_agent.get_agent.cache_clear()


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

    # Con descuento autorizado: guardrail lo permite limpiamente
    rechazos_con = guardrails.validar_salida(
        message=message,
        kind="propuesta",
        propuesta=draft,
        descuento_autorizado=10.0,
    )
    reglas_con = [r.regla for r in rechazos_con]
    assert "promesa" not in reglas_con


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
