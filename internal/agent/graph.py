"""Loop del agente con LangGraph (spec del agente §3.2)."""

import json
import logging
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from internal.agent import context, tool_registry
from internal.agent.state import EstadoNorma
from internal.schemas.order_schema import Channel, OrderDraft

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 15  # D-07
MAX_FAILURES = 2  # D-07
FAILURE_MESSAGE = "Tuve un problema, probá de nuevo en unos minutos."
# Cada iteración pasa por tres nodos. Es una red de seguridad: el tope real es MAX_ITERATIONS
RECURSION_LIMIT = MAX_ITERATIONS * 3 + 10


def _failure(state: EstadoNorma) -> dict[str, Any]:
    """Registra una falla. La propuesta sigue vigente: el cliente puede volver a responder."""
    return {
        "fallas": state.get("fallas", 0) + 1,
        "falla_turno": True,
        "respuesta": FAILURE_MESSAGE,
        "kind": "error",
        "messages": [AIMessage(content=FAILURE_MESSAGE)],
    }


def descubrir_tools(state: EstadoNorma) -> dict[str, Any]:
    if state.get("iteraciones", 0) >= MAX_ITERATIONS:
        logger.warning("Agente: se alcanzó el tope de %d iteraciones", MAX_ITERATIONS)
        return _failure(state)
    return {"tools_disponibles": tool_registry.available(state.get("skills_activas", []))}


def build_graph(model: BaseChatModel, checkpointer: BaseCheckpointSaver) -> CompiledStateGraph:
    def llamar_modelo(state: EstadoNorma) -> dict[str, Any]:
        iterations = state.get("iteraciones", 0) + 1
        try:
            message = model.bind_tools(tool_registry.schemas(state["tools_disponibles"])).invoke(
                context.build_messages(state)
            )
        except Exception as exc:  # cada proveedor lanza sus propios errores; el mensaje puede traer texto del cliente
            logger.warning("Agente: falló el LLM (%s)", type(exc).__name__)
            return {**_failure(state), "iteraciones": iterations}
        if message.tool_calls:
            return {"messages": [message], "iteraciones": iterations}
        text = message.text.strip()
        if not text:
            logger.warning("Agente: el LLM no devolvió texto ni tools")
            return {**_failure(state), "iteraciones": iterations}
        logger.warning("Agente: el LLM respondió sin una tool terminal")
        return {
            "messages": [message],
            "iteraciones": iterations,
            "respuesta": text,
            "kind": "informacion",
            "propuesta": None,
        }

    def ejecutar_tools(state: EstadoNorma) -> dict[str, Any]:
        propuesta = state.get("propuesta")
        ctx = tool_registry.TurnContext(
            customer_id=state["customer_id"],
            channel=Channel(state["channel"]),
            disponibles=state["tools_disponibles"],
            skills_activas=list(state.get("skills_activas", [])),
            propuesta=OrderDraft.model_validate(propuesta) if propuesta else None,
        )
        messages: list[Any] = []
        for call in state["messages"][-1].tool_calls:
            if ctx.resultado == "continuar":
                result = tool_registry.execute(call["name"], call["args"], ctx)
            else:
                # Cada llamada necesita su resultado, pero la primera tool terminal que tiene éxito cierra el turno
                error = {"code": "turno_cerrado", "message": "El turno ya terminó", "details": {}}
                result = {"ok": False, "error": error}
            messages.append(
                ToolMessage(content=json.dumps(result, ensure_ascii=False), tool_call_id=call["id"], name=call["name"])
            )
        if ctx.respuesta:
            messages.append(AIMessage(content=ctx.respuesta))
        return {
            "messages": messages,
            "skills_activas": ctx.skills_activas,
            "propuesta": ctx.propuesta.model_dump(mode="json") if ctx.propuesta else None,
            "respuesta": ctx.respuesta,
            "kind": ctx.kind,
            "options": ctx.options,
            "order_id": ctx.order_id,
            "resultado": ctx.resultado,
        }

    def pausa(state: EstadoNorma) -> dict[str, Any]:
        # El nodo vuelve a correr desde el principio al retomar: antes de interrupt() no puede haber efectos
        text = interrupt({"mensaje": state["respuesta"]})
        return {
            "messages": [HumanMessage(content=text)],
            "inicio_turno": len(state["messages"]),
            "skills_activas": [],
            "iteraciones": 0,
            "falla_turno": False,
            "respuesta": None,
            "kind": None,
            "options": [],
            "resultado": "continuar",
        }

    def ruta_falla(state: EstadoNorma) -> str:
        return "handoff" if state.get("fallas", 0) >= MAX_FAILURES else "error"

    def decidir_iteracion(state: EstadoNorma) -> str:
        return ruta_falla(state) if state.get("falla_turno") else "llm"

    def decidir(state: EstadoNorma) -> str:
        if state.get("falla_turno"):
            return ruta_falla(state)
        return "tool_calls" if state["messages"][-1].tool_calls else "texto"

    def decidir_resultado(state: EstadoNorma) -> str:
        return state["resultado"]

    graph = StateGraph(EstadoNorma)
    graph.add_node("descubrir_tools", descubrir_tools)
    graph.add_node("llm", llamar_modelo)
    graph.add_node("tools", ejecutar_tools)
    graph.add_node("pausa", pausa)

    graph.add_edge(START, "descubrir_tools")
    graph.add_conditional_edges(
        "descubrir_tools", decidir_iteracion, {"llm": "llm", "error": "pausa", "handoff": END}
    )
    graph.add_conditional_edges(
        "llm", decidir, {"tool_calls": "tools", "texto": "pausa", "error": "pausa", "handoff": END}
    )
    graph.add_conditional_edges(
        "tools", decidir_resultado, {"continuar": "descubrir_tools", "pausar": "pausa", "final": END}
    )
    graph.add_edge("pausa", "descubrir_tools")
    return graph.compile(checkpointer=checkpointer)
