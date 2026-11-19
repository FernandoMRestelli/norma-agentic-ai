"""Orquestador de la conversación: recibe los eventos de los canales y decide la respuesta.

Maneja el ciclo de vida de cada conversación: apertura, vencimiento por inactividad a las 24 h (RF-28, CA-28),
cierre y despacho al agente.
"""

import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime

from langchain_core.messages import HumanMessage
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
from internal.services import message_service
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


DRAFT_DISCARDED_MESSAGE = (
    "Listo, cancelé la propuesta. Si más adelante querés pedir algo, avisame y arrancamos de nuevo."
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


def _discard_pending_drafts(session: Session, conversation_id: int | None, customer_id: str, channel: str) -> int:
    result = session.execute(
        update(DraftRecord)
        .where(
            or_(
                DraftRecord.conversation_id == conversation_id,
                DraftRecord.conversation_id.is_(None)
                & (DraftRecord.customer_id == customer_id)
                & (DraftRecord.channel == channel),
            ),
            DraftRecord.status == DraftStatus.PENDING_CONFIRMATION.value,
        )
        .values(
            status=DraftStatus.DISCARDED.value,
            conversation_id=conversation_id,
        )
    )
    return result.rowcount


def _expire_conversation(session: Session, conversation: ConversationRecord) -> int:
    """Marca la conversación como vencida y descarta sus borradores pendientes (RF-28). Devuelve cuántos descartó."""
    conversation.status = "vencida"
    return _discard_pending_drafts(session, conversation.id, conversation.customer_id, conversation.channel)


def _is_silenced_by_derivation(customer_id: str, channel: Channel, now: datetime | None = None) -> bool:
    """Durante 24 h tras una derivación a humano, el bot no responde automáticamente (RF-27, CA-21, spec §7)."""
    momento = now or _clock()
    max_seconds = get_settings().conversation_timeout_hours * 3600
    with SessionLocal() as session:
        derivada = session.scalar(
            select(ConversationRecord)
            .where(
                ConversationRecord.customer_id == customer_id,
                ConversationRecord.channel == channel.value,
                ConversationRecord.status == "derivada",
            )
            .order_by(ConversationRecord.updated_at.desc())
        )
        if derivada is not None:
            updated_at = _con_zona(derivada.updated_at)
            if (momento - updated_at).total_seconds() <= max_seconds:
                return True
    return False


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


def _close_conversation(
    conversation_id: int, status: str, reason: str | None = None, now: datetime | None = None
) -> None:
    momento = now or _clock()
    with SessionLocal() as session:
        conv = session.get_one(ConversationRecord, conversation_id)
        conv.status = status
        conv.updated_at = momento
        if reason is not None:
            conv.derivation_reason = reason
        session.commit()


def _record_silenced_message(message: IncomingMessage) -> None:
    """Registra en el historial el mensaje que envió el cliente mientras la conversación está derivada (RF-27).

    El bot no contesta automáticamente, pero el operador puede leer lo que el cliente escribió desde el Dashboard.
    """
    if not message.text:
        return
    with SessionLocal() as session:
        derivada = session.scalar(
            select(ConversationRecord)
            .where(
                ConversationRecord.customer_id == message.customer_id,
                ConversationRecord.channel == message.channel.value,
                ConversationRecord.status == "derivada",
            )
            .order_by(ConversationRecord.updated_at.desc())
        )
        if derivada is not None:
            try:
                agent = get_agent()
                config = {"configurable": {"thread_id": str(derivada.id)}}
                agent.graph.update_state(config, {"messages": [HumanMessage(content=message.text)]})
            except Exception:
                logger.warning("No se pudo registrar mensaje silenciado en el checkpointer", exc_info=True)


def handle_message(message: IncomingMessage) -> ChannelReply:
    # RF-27, CA-21: si el cliente fue derivado hace menos de 24 h, el bot no responde automáticamente
    if _is_silenced_by_derivation(message.customer_id, message.channel):
        logger.info(
            "Conversación silenciada: cliente %s por %s derivado a humano hace menos de 24 h (RF-27)",
            message.customer_id,
            message.channel.value,
        )
        _record_silenced_message(message)
        return ChannelReply(text="")

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

    # Los turnos de una conversación se procesan de a uno (RNF-05)
    with _locks[(message.customer_id, message.channel)]:
        conversation_id, tenia_propuesta_vencida = _open_conversation_id(message.customer_id, message.channel)
        clave = interpretar_palabra_clave(text)
        if tenia_propuesta_vencida and clave == ConfirmationAction.CONFIRM:
            return ChannelReply(text=proposal_expired_message())
        if clave == ConfirmationAction.CANCEL:
            # RF-26, CA-23: cancelación determinística solo si hay un borrador pendiente en la conversación
            with SessionLocal() as session:
                descartados = _discard_pending_drafts(
                    session, conversation_id, message.customer_id, message.channel.value
                )
                session.commit()
            if descartados > 0:
                _close_conversation(conversation_id, "descartada", reason="cliente_cancelo")
                return ChannelReply(text=DRAFT_DISCARDED_MESSAGE)
        outcome = get_agent().handle(
            thread_id=str(conversation_id),
            customer_id=message.customer_id,
            channel=message.channel,
            text=text,
            customer_name=message.customer_name,
            origen=ORIGEN_POR_TIPO[message.type.value],
        )
        if outcome.cerrada:
            if outcome.kind == "pedido":
                nuevo_estado = "cerrada"
            elif outcome.kind == "descartado":
                nuevo_estado = "descartada"
            elif outcome.kind == "derivada":
                nuevo_estado = "derivada"
            else:
                nuevo_estado = "fallida"
            _close_conversation(conversation_id, nuevo_estado, reason=outcome.derivation_reason)
        else:
            _touch_conversation(conversation_id)
    return ChannelReply(text=outcome.message)
