"""Agente Norma: interpreta el pedido del cliente y arma un borrador usando las tools."""

from pathlib import Path

from pydantic import BaseModel

from backend.agent.llm_client import LLMClient
from backend.schemas.order_schema import Channel, OrderDraft

INSTRUCTIONS_PATH = Path(__file__).with_name("instructions.md")


class AgentReply(BaseModel):
    """Respuesta del agente: o un borrador para confirmar, o una pregunta de aclaración."""

    message: str
    draft: OrderDraft | None = None


class NormaAgent:
    def __init__(self, llm: LLMClient):
        self.llm = llm
        self.instructions = INSTRUCTIONS_PATH.read_text(encoding="utf-8")

    def handle(
        self, customer_id: str, channel: Channel, text: str, draft_id: str | None = None
    ) -> AgentReply:
        """Procesa el texto ya preparado por message_service.

        `channel` se pasa a create_order_draft para que el borrador registre su origen.
        Si `draft_id` viene informado, el cliente está corrigiendo ese borrador.
        """
        # TODO: bucle LLM <-> tools (catalog, inventory, order) hasta obtener borrador o pregunta
        raise NotImplementedError
