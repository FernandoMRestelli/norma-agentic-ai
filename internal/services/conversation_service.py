"""Orquestador de la conversación: recibe los eventos de los canales y decide la respuesta.

Maneja el ciclo de vida de cada conversación: apertura, vencimiento por inactividad a las 24 h (RF-28, CA-28),
cierre y despacho al agente.
"""

import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from internal.agent import guardrails
from internal.agent.norma_agent import get_agent
from internal.agent.tool_registry import validate_skills
from internal.config import get_settings
from internal.database.connection import SessionLocal, init_db
from internal.database.models import ConversationRecord, DraftRecord
from internal.schemas.conversation_schema import ChannelReply
from internal.schemas.order_schema import (
    ORIGEN_POR_TIPO,
    Channel,
    ConfirmationAction,
    DraftStatus,
    IncomingMessage,
    MessageType,
)
from internal.services import message_service, topic_filter
from internal.services.palabras_clave import interpretar_palabra_clave

logger = logging.getLogger(__name__)

WRITE_IT_PLEASE = "Por ahora solo puedo leer pedidos escritos. ¿Me lo mandás por texto?"


def proposal_expired_message() -> str:
    h = get_settings().conversation_timeout_hours
    horas = int(h) if isinstance(h, (int, float)) and h.is_integer() else h
    return (
        f"Esa propuesta ya venció (tienen validez de {horas} h). "
        "Para asegurarte stock y precios actualizados, pasame de nuevo lo que necesitás "
        "y te armo el pedido en el momento."
    )


_locks: defaultdict[tuple[str, Channel], threading.Lock] = defaultdict(threading.Lock)


def _now() -> datetime:
    return datetime.now(UTC)


_clock: Callable[[], datetime] = _now  # los tests lo reemplazan para simular el paso del tiempo


def _con_zona(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _is_expired(conversation: ConversationRecord, now: datetime) -> bool:
    """Una conversación abierta sin actividad durante conversation_timeout_hours vence (RF-28, CA-28)."""
    max_seconds = get_settings().conversation_timeout_hours * 3600
    updated_at = _con_zona(conversation.updated_at)
    return (now - updated_at).total_seconds() > max_seconds


def _expire_conversation(session: Session, conversation: ConversationRecord) -> int:
    """Marca la conversación como vencida y descarta sus borradores pendientes (RF-28). Devuelve cuántos descartó."""
    conversation.status = "vencida"
    result = session.execute(
        update(DraftRecord)
        .where(
            or_(
                DraftRecord.conversation_id == conversation.id,
                DraftRecord.conversation_id.is_(None)
                & (DraftRecord.customer_id == conversation.customer_id)
                & (DraftRecord.channel == conversation.channel),
            ),
            DraftRecord.status == DraftStatus.PENDING_CONFIRMATION.value,
        )
        .values(
            status=DraftStatus.DISCARDED.value,
            conversation_id=conversation.id,
        )
    )
    return result.rowcount


def startup() -> None:
    """Se llama al arrancar la API y el bot: sin skills válidas o sin tablas, mejor no arrancar."""
    validate_skills()
    init_db()


def _open_conversation_id(customer_id: str, channel: Channel, now: datetime | None = None) -> tuple[int, bool]:
    """Devuelve (conversation_id, tenia_propuesta_vencida).

    Si había una conversación abierta pero pasaron más de conversation_timeout_hours sin actividad,
    la marca como vencida y descarta sus borradores pendientes (RF-28, CA-28), abriendo una conversación nueva.
    Devuelve True en el segundo elemento si y solo si la conversación previa vencida tenía una propuesta
    pendiente que quedó descartada.
    """
    momento = now or _clock()
    with SessionLocal() as session:
        conversation = session.scalar(
            select(ConversationRecord).where(
                ConversationRecord.customer_id == customer_id,
                ConversationRecord.channel == channel.value,
                ConversationRecord.status == "abierta",
            )
        )
        tenia_propuesta_vencida = False
        if conversation is not None:
            if _is_expired(conversation, momento):
                descartados = _expire_conversation(session, conversation)
                session.commit()
                tenia_propuesta_vencida = descartados > 0
            else:
                conversation.updated_at = momento
                session.commit()
                return conversation.id, False

        nueva = ConversationRecord(
            customer_id=customer_id,
            channel=channel.value,
            status="abierta",
            created_at=momento,
            updated_at=momento,
        )
        session.add(nueva)
        session.commit()
        return nueva.id, tenia_propuesta_vencida


def _touch_conversation(conversation_id: int, now: datetime | None = None) -> None:
    """Actualiza la fecha de última actividad de una conversación que sigue abierta (RF-28)."""
    momento = now or _clock()
    with SessionLocal() as session:
        conv = session.get(ConversationRecord, conversation_id)
        if conv and conv.status == "abierta":
            conv.updated_at = momento
            session.commit()


def _close_conversation(conversation_id: int, status: str) -> None:
    with SessionLocal() as session:
        session.get_one(ConversationRecord, conversation_id).status = status
        session.commit()


def handle_message(message: IncomingMessage) -> ChannelReply:
    if message.type != MessageType.TEXT:
        # RF-32: sin modelo de visión, audio e imagen no llegan al LLM
        return ChannelReply(text=WRITE_IT_PLEASE)

    text = message_service.prepare(message)
    # Topes de entrada antes de abrir la conversación y sin llamar al LLM (spec de guardrails §3.3)
    if rechazo := guardrails.revisar_entrada((message.customer_id, message.channel), text):
        logger.warning(
            "Guardrail: mensaje de %s por %s no llega al agente (%s, %d caracteres)",
            message.customer_id,
            message.channel.value,
            "rafaga" if rechazo == guardrails.MENSAJE_RAFAGA else "largo",
            len(text),
        )
        return ChannelReply(text=rechazo)

    # Fuera de tema evidente: sin abrir/cerrar conversación ni modificar la propuesta vigente.
    if topic_filter.is_off_topic(text):
        logger.info("Filtro de tema: respuesta fija sin llamar al agente")
        return ChannelReply(text=topic_filter.OFF_TOPIC_REPLY)

    # Los turnos de una conversación se procesan de a uno (RNF-05)
    with _locks[(message.customer_id, message.channel)]:
        conversation_id, tenia_propuesta_vencida = _open_conversation_id(message.customer_id, message.channel)
        if tenia_propuesta_vencida and interpretar_palabra_clave(text) == ConfirmationAction.CONFIRM:
            return ChannelReply(text=proposal_expired_message())
        outcome = get_agent().handle(
            thread_id=str(conversation_id),
            customer_id=message.customer_id,
            channel=message.channel,
            text=text,
            customer_name=message.customer_name,
            origen=ORIGEN_POR_TIPO[message.type.value],
        )
        if outcome.cerrada:
            _close_conversation(conversation_id, "cerrada" if outcome.kind == "pedido" else "fallida")
        else:
            _touch_conversation(conversation_id)
    return ChannelReply(text=outcome.message)
