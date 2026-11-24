"""Orquestador de la conversación: recibe los eventos de los canales y decide la respuesta.

Maneja el ciclo de vida de cada conversación: apertura, vencimiento por inactividad a las 24 h (RF-28, CA-28),
cierre y despacho al agente.
"""

import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import NamedTuple

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
from internal.services import audio_service, message_service, topic_filter
from internal.services.palabras_clave import interpretar_palabra_clave
from internal.tools import reply_tools

logger = logging.getLogger(__name__)

WRITE_IT_PLEASE = "Por ahora solo puedo leer pedidos escritos. ¿Me lo mandás por texto?"
COULD_NOT_READ_IMAGE = "No pude entender la imagen, ¿me lo escribís?"
AUDIO_NOT_CLEAR_MESSAGE = "No pude escuchar el audio con claridad. ¿Me lo podrás reenviar o escribir?"
AUDIO_TOO_LONG_MESSAGE = "El audio es muy largo. ¿Me lo podés resumir en un texto o un audio más corto?"
AUDIO_PROCESSING_ERROR_MESSAGE = "Tuve un problema al procesar el audio, ¿me lo podés escribir?"


def proposal_expired_message() -> str:
    h = get_settings().conversation_timeout_hours
    horas = int(h) if isinstance(h, (int, float)) and h.is_integer() else h
    return (
        f"Esa propuesta ya venció (tienen validez de {horas} h). "
        "Para asegurarte stock y precios actualizados, pasame de nuevo lo que necesitás "
        "y te armo el pedido en el momento."
    )


DRAFT_DISCARDED_MESSAGE = reply_tools.DEFAULT_DISCARD_MESSAGE

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
    audio_service.check_audio_config()


class ConversacionAbierta(NamedTuple):
    id: int
    nueva: bool  # recién creada: el agente no puede tener estado guardado con este ID (RF-100)
    tenia_propuesta_vencida: bool


def _abrir_conversacion(customer_id: str, channel: Channel, now: datetime | None = None) -> ConversacionAbierta:
    """La conversación abierta del cliente en ese canal, o una nueva.

    Si había una conversación abierta pero pasaron más de conversation_timeout_hours sin actividad,
    la marca como vencida y descarta sus borradores pendientes (RF-28, CA-28), abriendo una conversación nueva.
    `nueva` es True si la creó recién, y `tenia_propuesta_vencida` si y solo si la conversación previa vencida tenía
    una propuesta pendiente que quedó descartada.
    """
    momento = now or _clock()
    with SessionLocal() as session:
        conversation = session.scalar(
            select(ConversationRecord)
            .where(
                ConversationRecord.customer_id == customer_id,
                ConversationRecord.channel == channel.value,
                ConversationRecord.status == "abierta",
            )
            .order_by(ConversationRecord.updated_at.desc())
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
                return ConversacionAbierta(conversation.id, nueva=False, tenia_propuesta_vencida=False)

        nueva = ConversationRecord(
            customer_id=customer_id,
            channel=channel.value,
            status="abierta",
            created_at=momento,
            updated_at=momento,
        )
        session.add(nueva)
        session.commit()
        return ConversacionAbierta(nueva.id, nueva=True, tenia_propuesta_vencida=tenia_propuesta_vencida)


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
    Si el cliente mandó audio o foto durante el silencio, se registra un marcador legible.
    """
    contenido = message.text
    if not contenido:
        if message.type == MessageType.AUDIO:
            contenido = "[audio]"
        elif message.type == MessageType.IMAGE:
            contenido = "[imagen]"
        else:
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
                agent.graph.update_state(config, {"messages": [HumanMessage(content=contenido)]})
            except Exception:
                logger.warning("No se pudo registrar mensaje silenciado en el checkpointer", exc_info=True)


def _sin_lector(message: IncomingMessage) -> bool:
    """RF-32: audio solo si está habilitado en configuración, imagen solo con LLM_VISION_PROVIDER."""
    if message.type == MessageType.AUDIO:
        return not audio_service.is_audio_enabled()
    if message.type == MessageType.IMAGE:
        return not get_settings().llm_vision_provider
    return False


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

    if _sin_lector(message):
        return ChannelReply(text=WRITE_IT_PLEASE)

    # Tope de ráfaga antes de descargar/transcribir audio o imagen (evita gastar llamadas pagas en ráfagas)
    if rechazo := guardrails.revisar_rafaga((message.customer_id, message.channel)):
        logger.warning(
            "Guardrail: mensaje de %s por %s no llega al agente (rafaga)",
            message.customer_id,
            message.channel.value,
        )
        return ChannelReply(text=rechazo)

    try:
        text = message_service.prepare(message)
    except message_service.ImagenIlegible:
        # Spec del agente §6: sin texto útil no se llama al LLM
        return ChannelReply(text=COULD_NOT_READ_IMAGE)
    except audio_service.AudioTooLongError:
        return ChannelReply(text=AUDIO_TOO_LONG_MESSAGE)
    except audio_service.AudioEmptyError:
        return ChannelReply(text=AUDIO_NOT_CLEAR_MESSAGE)
    except audio_service.AudioProcessingError as exc:
        logger.warning("Fallo al interpretar audio de %s: %s", message.customer_id, exc)
        return ChannelReply(text=AUDIO_PROCESSING_ERROR_MESSAGE)
    # Tope de largo antes de abrir la conversación y sin llamar al LLM (spec de guardrails §3.3)
    if rechazo := guardrails.revisar_largo(text):
        logger.warning(
            "Guardrail: mensaje de %s por %s no llega al agente (largo, %d caracteres)",
            message.customer_id,
            message.channel.value,
            len(text),
        )
        return ChannelReply(text=rechazo)

    texto_limpio = message_service.sin_marca_audio(text)

    # Fuera de tema evidente: sin abrir/cerrar conversación ni modificar la propuesta vigente.
    if topic_filter.is_off_topic(texto_limpio):
        logger.info("Filtro de tema: respuesta fija sin llamar al agente")
        return ChannelReply(text=topic_filter.OFF_TOPIC_REPLY)

    # Los turnos de una conversación se procesan de a uno (RNF-05)
    with _locks[(message.customer_id, message.channel)]:
        conversation_id, nueva, tenia_propuesta_vencida = _abrir_conversacion(message.customer_id, message.channel)
        clave = interpretar_palabra_clave(texto_limpio)
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
            text=message_service.marcar_origen(text, message.type),
            customer_name=message.customer_name,
            origen=ORIGEN_POR_TIPO[message.type.value],
            nueva=nueva,
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
