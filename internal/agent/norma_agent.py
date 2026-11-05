"""Agente Norma: inicia o retoma el grafo de una conversación y devuelve el resultado del turno."""

import logging
import sqlite3
from functools import lru_cache
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from internal.agent import context
from internal.agent.graph import FAILURE_MESSAGE, RECURSION_LIMIT, build_graph
from internal.agent.llm import get_chat_model
from internal.agent.state import EstadoNorma
from internal.config import get_settings
from internal.schemas.agent_schema import AgentOutcome
from internal.schemas.order_schema import Channel, OrderDraft

logger = logging.getLogger(__name__)

# Otro proceso, como el bot, puede estar escribiendo: se espera antes de fallar con «database is locked»
CHECKPOINT_BUSY_TIMEOUT = 15.0


class NormaAgent:
    def __init__(self, model: BaseChatModel, checkpointer: BaseCheckpointSaver):
        self.graph = build_graph(model, checkpointer)
        self.system_prompt = context.load_system_prompt()

    def handle(
        self, thread_id: str, customer_id: str, channel: Channel, text: str, customer_name: str | None = None
    ) -> AgentOutcome:
        """Procesa un mensaje ya preparado por message_service. `thread_id` identifica la conversación."""
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}
        if self.graph.get_state(config).next == ("pausa",):
            entry: Command | EstadoNorma = Command(resume=text)
        else:
            entry = {
                "messages": [HumanMessage(content=text)],
                "customer_id": customer_id,
                "customer_name": customer_name,
                "channel": channel.value,
                "inicio_turno": 0,
                "skills_activas": [],
                "iteraciones": 0,
                "fallas": 0,
                "falla_turno": False,
                "propuesta": None,
            }
        try:
            state = self.graph.invoke(entry, config)
        except GraphRecursionError:
            logger.error("Agente: conversación %s superó el límite de recursión", thread_id)
            return AgentOutcome(kind="error", message=FAILURE_MESSAGE, cerrada=True)

        propuesta = state.get("propuesta")
        outcome = AgentOutcome(
            kind=state.get("kind") or "error",
            message=state.get("respuesta") or FAILURE_MESSAGE,
            draft=OrderDraft.model_validate(propuesta) if propuesta else None,
            order_id=state.get("order_id"),
            options=state.get("options", []),
            iterations=state.get("iteraciones", 0),
            cerrada="__interrupt__" not in state,
        )
        # Sin el texto de los mensajes (RNF-03)
        logger.info("Agente: conversación %s, %s, %d iteraciones", thread_id, outcome.kind, outcome.iterations)
        return outcome


@lru_cache
def get_agent() -> NormaAgent:
    path = Path(get_settings().checkpoint_db)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=CHECKPOINT_BUSY_TIMEOUT, check_same_thread=False)
    return NormaAgent(get_chat_model(), SqliteSaver(connection))
