"""Agente Norma: inicia o retoma el grafo de una conversación y devuelve el resultado del turno."""

import logging
import sqlite3
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from internal.agent import context, observabilidad
from internal.agent.graph import FAILURE_MESSAGE, build_graph, recursion_limit
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
        self.checkpointer = checkpointer
        self.graph = build_graph(model, checkpointer)
        self.system_prompt = context.load_system_prompt()
        self.prompt_version = context.prompt_version()

    def handle(
        self,
        thread_id: str,
        customer_id: str,
        channel: Channel,
        text: str,
        customer_name: str | None = None,
        origen: str = "texto",
        nueva: bool = False,
        lectura: dict[str, Any] | None = None,
        mensaje_id: str | None = None,
    ) -> AgentOutcome:
        """Procesa un mensaje ya preparado por message_service. `thread_id` identifica la conversación.

        `origen` dice de dónde salió el texto (`texto`, `audio` o `imagen`) y queda en la orden (RF-79).
        `nueva` dice que el orquestador acaba de abrir la conversación: no puede tener estado guardado (RF-100).
        `lectura` es la lectura de una imagen con renglones (`ImagenLeida`): los renglones del pedido salen de ahí
        `mensaje_id` enlaza la traza del turno con la de la lectura.
        """
        inicio = time.perf_counter()
        with observabilidad.turno(thread_id, channel.value, text, mensaje_id) as turno:
            self._descartar_estado_ajeno(thread_id, customer_id, channel, nueva)
            outcome, state = self._run(thread_id, customer_id, channel, text, customer_name, origen, lectura)
            resumen = observabilidad.resumir(state)
            latencia_ms = round((time.perf_counter() - inicio) * 1000)
            turno.cerrar(outcome, resumen, latencia_ms, self.prompt_version)
        # Solo IDs, nombres y números: sin el texto de los mensajes (RNF-03, RNF-07)
        logger.info(
            "Agente: conversation_id=%s draft_id=%s order_id=%s kind=%s via=%s iteraciones=%d fallas=%d tools=%s "
            "errores=%s rescates=%s "
            "skills=%s latencia_ms=%d tokens_entrada=%d tokens_salida=%d sub_loops=%s llamadas_sub_loop=%d "
            "tokens_entrada_sub_loop=%d prompt_version=%s",
            thread_id,
            resumen.draft_id or "-",
            outcome.order_id or "-",
            outcome.kind,
            resumen.via,
            outcome.iterations,
            resumen.fallas,
            ",".join(resumen.tools) or "-",
            ",".join(resumen.errores) or "-",
            ",".join(resumen.rescates) or "-",
            ",".join(resumen.skills) or "-",
            latencia_ms,
            resumen.tokens_entrada,
            resumen.tokens_salida,
            ",".join(resumen.sub_loops) or "-",
            resumen.llamadas_sub_loop,
            resumen.tokens_entrada_sub_loop,
            self.prompt_version,
        )
        return outcome

    def _descartar_estado_ajeno(self, thread_id: str, customer_id: str, channel: Channel, nueva: bool) -> None:
        """Borra el estado guardado con este `thread_id` si no es de esta conversación (RF-100, CA-165, CA-166).

        El ID sale de `norma.db` y el estado vive en `CHECKPOINT_DB`: si la base vuelve a un backup o se recrea, un
        ID nuevo puede ya tener la historia y la propuesta de otra conversación, incluso de otro cliente.
        """
        guardado = self.graph.get_state({"configurable": {"thread_id": thread_id}}).values
        if not guardado:
            return
        if nueva:
            motivo = "conversación nueva"
        elif guardado.get("customer_id", customer_id) != customer_id:
            motivo = "otro cliente"
        elif guardado.get("channel", channel.value) != channel.value:
            motivo = "otro canal"
        else:
            return
        # Solo el ID y el motivo: el estado guardado tiene texto de clientes (RNF-03)
        logger.error(
            "Agente: la conversación %s tenía un estado guardado que no es suyo (%s); se descarta. "
            "¿norma.db volvió a un backup o se recreó sin CHECKPOINT_DB?",
            thread_id,
            motivo,
        )
        self.checkpointer.delete_thread(thread_id)

    def _run(
        self,
        thread_id: str,
        customer_id: str,
        channel: Channel,
        text: str,
        customer_name: str | None,
        origen: str,
        lectura: dict[str, Any] | None = None,
    ) -> tuple[AgentOutcome, EstadoNorma]:
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": recursion_limit()}
        if self.graph.get_state(config).next == ("pausa",):
            entry: Command | EstadoNorma = Command(resume={"texto": text, "origen": origen, "lectura": lectura})
        else:
            prev_state = self.graph.get_state(config).values
            entry = {
                "messages": [HumanMessage(content=text)],
                "customer_id": customer_id,
                "customer_name": customer_name,
                "channel": channel.value,
                "origen": origen,
                "lectura": lectura,
                "inicio_turno": len(prev_state.get("messages", [])) if prev_state else 0,
                "skills_activas": [],
                "iteraciones": 0,
                "fallas": 0,
                "falla_turno": False,
                "aclaraciones_seguidas": 0,
                "mensaje_modelo": None,  # cada turno escribe el suyo, o ninguno
                "propuesta": prev_state.get("propuesta"),
                "renglones": prev_state.get("renglones", []),
                "descuento_autorizado": prev_state.get("descuento_autorizado"),
                "condicion_comercial": prev_state.get("condicion_comercial"),
                "acuerdo_operativo": prev_state.get("acuerdo_operativo"),
                "propuesta_pendiente_mostrar": bool(prev_state.get("propuesta_pendiente_mostrar", False)),
            }
        try:
            state = self.graph.invoke(entry, config)
        except GraphRecursionError:
            logger.error("Agente: conversación %s superó el límite de recursión", thread_id)
            outcome = AgentOutcome(kind="error", message=FAILURE_MESSAGE, cerrada=True)
            return outcome, self.graph.get_state(config).values

        propuesta = state.get("propuesta")
        outcome = AgentOutcome(
            kind=state.get("kind") or "error",
            message=state.get("respuesta") or FAILURE_MESSAGE,
            draft=OrderDraft.model_validate(propuesta) if propuesta else None,
            order_id=state.get("order_id"),
            options=state.get("options", []),
            iterations=state.get("iteraciones", 0),
            llamadas_sub_loop=state.get("iteraciones_sub_loop", 0),
            cerrada="__interrupt__" not in state,
            derivation_reason=state.get("derivation_reason"),
            mensaje_modelo=state.get("mensaje_modelo"),
        )
        return outcome, state


@lru_cache
def get_agent() -> NormaAgent:
    path = Path(get_settings().checkpoint_db)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=CHECKPOINT_BUSY_TIMEOUT, check_same_thread=False)
    return NormaAgent(get_chat_model(), SqliteSaver(connection))
