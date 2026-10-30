"""Orquestador de la conversación: recibe los eventos de los canales y decide la respuesta.

PROVISORIO: todavía no hay agente ni confirmación, así que responde textos fijos para poder probar los canales
de punta a punta (docs/spec/canal-telegram.md §3.4). Las etapas 03 a 05 lo reemplazan por la máquina de estados
de la spec del agente (§2 y §3): deduplicar, lock por conversación, message_service, NormaAgent y order_service.
"""

from backend.schemas.conversation_schema import ChannelReply
from backend.schemas.order_schema import Channel, ConfirmationAction, IncomingMessage, MessageType

WRITE_IT_PLEASE = "Por ahora solo puedo leer pedidos escritos. ¿Me lo mandás por texto?"
AGENT_NOT_READY = "Recibí tu mensaje 👍 Todavía no puedo interpretar pedidos: el agente se conecta en la etapa 03."
CONFIRMATION_NOT_READY = "La confirmación de pedidos todavía no está implementada."


def handle_message(message: IncomingMessage) -> ChannelReply:
    if message.type != MessageType.TEXT:
        # RF-32: sin modelo de visión, audio e imagen no llegan al LLM
        return ChannelReply(text=WRITE_IT_PLEASE)
    # TODO etapa 03: estado de la conversación -> message_service.prepare -> NormaAgent.handle
    return ChannelReply(text=AGENT_NOT_READY)


def handle_action(
    customer_id: str, channel: Channel, draft_id: str, version: int, action: ConfirmationAction
) -> ChannelReply:
    """Confirmar / Corregir un borrador. Nunca pasa por el LLM (spec del agente §5)."""
    # TODO etapas 04 y 05: verificar que el borrador es de este cliente; confirmar -> order_service.confirm_draft;
    # corregir -> «¿Qué querés cambiar?»
    return ChannelReply(text=CONFIRMATION_NOT_READY)
