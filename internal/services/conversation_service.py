"""Orquestador de la conversación: recibe los eventos de los canales y decide la respuesta.

Primer corte: la conversación solo está abierta, cerrada o fallida. La máquina de estados completa de la
spec del agente (§2) se suma con el vencimiento y la derivación.
"""

import threading
from collections import defaultdict

from sqlalchemy import select

from internal.agent.norma_agent import get_agent
from internal.agent.tool_registry import validate_skills
from internal.database.connection import SessionLocal, init_db
from internal.database.models import ConversationRecord
from internal.schemas.conversation_schema import ChannelReply
from internal.schemas.order_schema import Channel, ConfirmationAction, IncomingMessage, MessageType
from internal.services import message_service

WRITE_IT_PLEASE = "Por ahora solo puedo leer pedidos escritos. ¿Me lo mandás por texto?"
CONFIRMATION_NOT_READY = "La confirmación de pedidos todavía no está implementada."

_locks: defaultdict[tuple[str, Channel], threading.Lock] = defaultdict(threading.Lock)


def startup() -> None:
    """Se llama al arrancar la API y el bot: sin skills válidas o sin tablas, mejor no arrancar."""
    validate_skills()
    init_db()


def _open_conversation_id(customer_id: str, channel: Channel) -> int:
    with SessionLocal() as session:
        conversation = session.scalar(
            select(ConversationRecord).where(
                ConversationRecord.customer_id == customer_id,
                ConversationRecord.channel == channel.value,
                ConversationRecord.status == "abierta",
            )
        )
        if conversation is None:
            conversation = ConversationRecord(customer_id=customer_id, channel=channel.value)
            session.add(conversation)
            session.commit()
        return conversation.id


def _close_conversation(conversation_id: int, status: str) -> None:
    with SessionLocal() as session:
        session.get_one(ConversationRecord, conversation_id).status = status
        session.commit()


def handle_message(message: IncomingMessage) -> ChannelReply:
    if message.type != MessageType.TEXT:
        # RF-32: sin modelo de visión, audio e imagen no llegan al LLM
        return ChannelReply(text=WRITE_IT_PLEASE)

    # Los turnos de una conversación se procesan de a uno (RNF-05)
    with _locks[(message.customer_id, message.channel)]:
        conversation_id = _open_conversation_id(message.customer_id, message.channel)
        outcome = get_agent().handle(
            thread_id=str(conversation_id),
            customer_id=message.customer_id,
            channel=message.channel,
            text=message_service.prepare(message),
            customer_name=message.customer_name,
        )
        if outcome.cerrada:
            _close_conversation(conversation_id, "cerrada" if outcome.kind == "pedido" else "fallida")
    return ChannelReply(text=outcome.message)


def handle_action(
    customer_id: str, channel: Channel, draft_id: str, version: int, action: ConfirmationAction
) -> ChannelReply:
    """Botones Confirmar / Corregir: fuera del primer corte, que confirma conversando (spec del agente §5)."""
    return ChannelReply(text=CONFIRMATION_NOT_READY)
