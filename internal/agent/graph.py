"""Loop del agente con LangGraph (spec del agente §3.2)."""

import json
import logging
import time
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from internal.agent import context, guardrails, observabilidad, tool_registry
from internal.agent.state import EstadoNorma
from internal.schemas.order_schema import Channel, ConfirmationAction, OrderDraft
from internal.services import interpretation_service, inventory_service
from internal.services.palabras_clave import es_saludo, interpretar_palabra_clave
from internal.services.skill_service import get_skills

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 8  # RNF-16 (antes 15, D-07)
MAX_SEGUNDOS_TURNO = 60.0  # RNF-16: plazo total de un turno, se controla entre una iteración y la siguiente
MAX_FAILURES = 2  # D-07
# Con una propuesta vigente, un mensaje así de corto y sin números casi siempre la acepta, la rechaza o duda
MAX_PALABRAS_RESPUESTA_A_PROPUESTA = 3
_reloj = time.time  # los tests lo reemplazan para no esperar
FAILURE_MESSAGE = "Tuve un problema, probá de nuevo en unos minutos."
# Cada iteración pasa por tres nodos. Es una red de seguridad: el tope real es MAX_ITERATIONS
RECURSION_LIMIT = MAX_ITERATIONS * 3 + 12


def _failure(state: EstadoNorma) -> dict[str, Any]:
    """Registra una falla. La propuesta sigue vigente: el cliente puede volver a responder."""
    return {
        "fallas": state.get("fallas", 0) + 1,
        "falla_turno": True,
        "respuesta": FAILURE_MESSAGE,
        "kind": "error",
        "messages": [AIMessage(content=FAILURE_MESSAGE)],
    }


def _registrar_intento(state: EstadoNorma, config: RunnableConfig) -> None:
    """Un mensaje que pide cambiar las reglas queda en el log y en la traza, sin su texto (RNF-03).

    El mensaje sigue al LLM: lo que conteste pasa por el validador de salida, y el estado actual le avisa.
    """
    entrada = state["messages"][state.get("inicio_turno", 0)]
    codigos = guardrails.detectar_intento(entrada.text) if isinstance(entrada, HumanMessage) else []
    if not codigos:
        return
    conversation_id = config.get("configurable", {}).get("thread_id", "-")
    logger.warning(
        "Guardrail: conversation_id=%s posible intento de cambiar las reglas (patrones=%s)",
        conversation_id,
        ",".join(codigos),
    )
    with observabilidad.paso("guardrail_entrada", "GUARDRAIL", patrones=codigos, intento_de_cambiar_reglas=True):
        pass


def skill_clara(texto: str, hay_propuesta: bool) -> str | None:
    """La skill del turno cuando el mensaje no admite dudas; None si tiene que elegir el modelo (RF-62)."""
    tiene_numero = any(caracter.isdigit() for caracter in texto)
    if hay_propuesta:
        corto = len(texto.split()) <= MAX_PALABRAS_RESPUESTA_A_PROPUESTA
        return "confirmar_pedido" if corto and not tiene_numero else None
    if es_saludo(texto):
        return "informar"
    renglones = sum(bool(linea.strip()) for linea in texto.splitlines())
    return "tomar_pedido" if renglones >= 2 or tiene_numero else None


def encaminar(state: EstadoNorma) -> dict[str, Any]:
    """Abre el turno: si la ruta es clara, lee la skill antes de la primera llamada al LLM (RF-62).

    El texto de la skill va en el contexto (`context._skill_activa`), no como una tool call inventada: Gemini
    rechaza una llamada que no trae su firma (CA-35). El modelo puede leer otra skill con `leer_skill`.
    """
    entrada = state["messages"][state.get("inicio_turno", 0)]
    skill = skill_clara(entrada.text, bool(state.get("propuesta"))) if isinstance(entrada, HumanMessage) else None
    if skill not in get_skills():
        skill = None
    renglones = list(state.get("renglones") or [])
    nuevos = _numerar(entrada.text, renglones) if isinstance(entrada, HumanMessage) else []
    with observabilidad.paso("encaminar", "CHAIN", skill_preactivada=skill or "-", renglones=len(nuevos)):
        pass
    return {
        "skills_activas": [skill] if skill else [],
        "skill_preactivada": skill,
        "br_entregadas": [],
        "turno_desde": _reloj(),
        "renglones": renglones + nuevos,
    }


def _numerar(texto: str, renglones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Los renglones nuevos de un mensaje con forma de lista, numerados a continuación de los que ya había (RF-65)."""
    if len(texto.splitlines()) < interpretation_service.MIN_RENGLONES:
        return []
    desde = max((renglon["n"] for renglon in renglones), default=0) + 1
    try:
        return interpretation_service.numerar_renglones(texto, inventory_service.get_catalog(), desde)
    except OSError:  # sin catálogo no se numera: las tools le avisan al agente que no está disponible
        logger.warning("Agente: no se pudo leer el catálogo para numerar los renglones")
        return []


def descubrir_tools(state: EstadoNorma, config: RunnableConfig) -> dict[str, Any]:
    if not state.get("iteraciones"):
        _registrar_intento(state, config)
    if state.get("iteraciones", 0) >= MAX_ITERATIONS:
        logger.warning("Agente: se alcanzó el tope de %d iteraciones", MAX_ITERATIONS)
        return _failure(state)
    if _reloj() - state.get("turno_desde", _reloj()) > MAX_SEGUNDOS_TURNO:
        logger.warning("Agente: el turno pasó el plazo de %d s", MAX_SEGUNDOS_TURNO)
        return _failure(state)
    return {"tools_disponibles": tool_registry.available(state.get("skills_activas", []))}


def _conversation_id(config: RunnableConfig) -> int | None:
    """El `thread_id` es el id de la conversación en la base (conversation_service)."""
    thread_id = str(config.get("configurable", {}).get("thread_id", ""))
    return int(thread_id) if thread_id.isdigit() else None


def _turn_context(state: EstadoNorma, config: RunnableConfig, disponibles: list[str]) -> tool_registry.TurnContext:
    """Contexto de las tools. Completa la conversación y el nombre de una propuesta de un checkpoint anterior."""
    conversation_id = _conversation_id(config)
    customer_name = state.get("customer_name")
    propuesta = OrderDraft.model_validate(state["propuesta"]) if state.get("propuesta") else None
    if propuesta and propuesta.conversation_id is None:
        propuesta = propuesta.model_copy(update={"conversation_id": conversation_id})
    if propuesta and propuesta.customer_name is None and customer_name:
        propuesta = OrderDraft.model_validate({**propuesta.model_dump(), "customer_name": customer_name})
    return tool_registry.TurnContext(
        customer_id=state["customer_id"],
        channel=Channel(state["channel"]),
        disponibles=disponibles,
        customer_name=customer_name,
        conversation_id=conversation_id,
        skills_activas=list(state.get("skills_activas", [])),
        br_entregadas=list(state.get("br_entregadas", [])),
        renglones=[dict(renglon) for renglon in state.get("renglones") or []],
        propuesta=propuesta,
    )


def build_graph(model: BaseChatModel, checkpointer: BaseCheckpointSaver) -> CompiledStateGraph:
    def llamar_modelo(state: EstadoNorma) -> dict[str, Any]:
        iterations = state.get("iteraciones", 0) + 1
        with observabilidad.paso("llm", "CHAT_MODEL", iteracion=iterations) as paso:
            try:
                # El modelo tiene que elegir una tool (RF-61): el texto suelto queda como red de seguridad
                message = model.bind_tools(
                    tool_registry.schemas(state["tools_disponibles"]), tool_choice="any"
                ).invoke(context.build_messages(state))
            # Cada proveedor lanza sus propios errores; el mensaje puede traer texto del cliente
            except Exception as exc:
                logger.warning("Agente: falló el LLM (%s)", type(exc).__name__)
                paso.fallo(type(exc).__name__)
                return {**_failure(state), "iteraciones": iterations}
            paso.respuesta_llm(message)
        if message.tool_calls:
            return {"messages": [message], "iteraciones": iterations}
        text = message.text.strip()
        if not text:
            logger.warning("Agente: el LLM no devolvió texto ni tools")
            return {**_failure(state), "iteraciones": iterations}
        logger.warning("Agente: el LLM respondió sin una tool terminal")
        # El texto suelto es un respond_to_customer(kind="informacion") sin propuesta: pasa por el mismo validador
        turno = guardrails.contexto_turno(state["messages"][state.get("inicio_turno", 0) :])
        vigente = OrderDraft.model_validate(state["propuesta"]) if state.get("propuesta") else None
        rechazos = guardrails.validar_salida(text, "informacion", turno=turno, vigente=vigente)
        if rechazos:
            reglas = ",".join(rechazo.regla for rechazo in rechazos)
            with observabilidad.paso("validar_texto_suelto", "GUARDRAIL", iteracion=iterations) as paso:
                paso.fallo(guardrails.CODIGO_RECHAZO)
                paso.atributos(reglas=reglas)
            # Sin una tool call a la que contestar, el aviso va como mensaje marcado: el texto no llega al cliente
            aviso = guardrails.aviso_texto_suelto(rechazos)
            if turno.rechazos < guardrails.MAX_RECHAZOS:
                logger.warning("Guardrail: texto suelto rechazado (reglas=%s)", reglas)
                return {"messages": [message, aviso], "iteraciones": iterations}
            logger.warning("Guardrail: intentos agotados, sale el mensaje fijo (reglas=%s)", reglas)
            text = guardrails.mensaje_seguro(None)
            return {
                "messages": [message, aviso, AIMessage(content=text)],
                "iteraciones": iterations,
                "respuesta": text,
                "kind": "informacion",
                "propuesta": None,
            }
        return {
            "messages": [message],
            "iteraciones": iterations,
            "respuesta": text,
            "kind": "informacion",
            "propuesta": None,
        }

    def ejecutar_tools(state: EstadoNorma, config: RunnableConfig) -> dict[str, Any]:
        ctx = _turn_context(state, config, state["tools_disponibles"])
        del_turno = state["messages"][state.get("inicio_turno", 0) :]
        ctx.turno = guardrails.contexto_turno(del_turno)
        ctx.coberturas_fallidas = sum(
            isinstance(m, ToolMessage) and '"code": "renglones_sin_cuenta"' in m.content for m in del_turno
        )
        messages: list[Any] = []
        for call in state["messages"][-1].tool_calls:
            if ctx.resultado == "continuar":
                with observabilidad.paso(call["name"], "TOOL", iteracion=state.get("iteraciones")) as paso:
                    result = tool_registry.execute(call["name"], call["args"], ctx)
                    paso.resultado_tool(call["args"], result)
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
            "br_entregadas": ctx.br_entregadas,
            "renglones": ctx.renglones,
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

    def confirmar(state: EstadoNorma, config: RunnableConfig) -> dict[str, Any]:
        """Una aceptación exacta («ok», «dale», «listo»…): registra la propuesta vigente sin llamar al LLM.

        Las palabras están en `palabras_clave.ACEPTACIONES` (RF-13, RF-64, CA-18, CA-120).
        """
        ctx = _turn_context({**state, "skills_activas": []}, config, ["armar_pedido"])
        with observabilidad.paso("armar_pedido", "TOOL", via="confirmar_directo") as paso:
            result = tool_registry.execute("armar_pedido", {}, ctx)
            paso.resultado_tool({}, result)
        if not result["ok"]:
            # Por ejemplo, ya no alcanza el stock: el agente le explica al cliente y le ofrece lo que queda
            logger.info("Agente: no se pudo confirmar sin el LLM (%s)", result["error"]["code"])
            return {"resultado": "continuar"}
        return {
            "messages": [AIMessage(content=ctx.respuesta)],
            "propuesta": None,
            "respuesta": ctx.respuesta,
            "kind": ctx.kind,
            "order_id": ctx.order_id,
            "resultado": ctx.resultado,
        }

    def decidir_tras_pausa(state: EstadoNorma) -> str:
        confirma = interpretar_palabra_clave(state["messages"][-1].content) == ConfirmationAction.CONFIRM
        return "confirmar" if confirma and state.get("propuesta") else "agente"

    def ruta_falla(state: EstadoNorma) -> str:
        return "handoff" if state.get("fallas", 0) >= MAX_FAILURES else "error"

    def decidir_iteracion(state: EstadoNorma) -> str:
        return ruta_falla(state) if state.get("falla_turno") else "llm"

    def decidir(state: EstadoNorma) -> str:
        if state.get("falla_turno"):
            return ruta_falla(state)
        ultimo = state["messages"][-1]
        if guardrails.es_aviso(ultimo):
            return "rechazado"  # el texto suelto no pasó el validador: el LLM reescribe
        return "tool_calls" if ultimo.tool_calls else "texto"

    def decidir_resultado(state: EstadoNorma) -> str:
        return state["resultado"]

    graph = StateGraph(EstadoNorma)
    graph.add_node("encaminar", encaminar)
    graph.add_node("descubrir_tools", descubrir_tools)
    graph.add_node("llm", llamar_modelo)
    graph.add_node("tools", ejecutar_tools)
    graph.add_node("pausa", pausa)
    graph.add_node("confirmar", confirmar)

    graph.add_edge(START, "encaminar")
    graph.add_edge("encaminar", "descubrir_tools")
    graph.add_conditional_edges(
        "descubrir_tools", decidir_iteracion, {"llm": "llm", "error": "pausa", "handoff": END}
    )
    graph.add_conditional_edges(
        "llm",
        decidir,
        {"tool_calls": "tools", "texto": "pausa", "rechazado": "descubrir_tools", "error": "pausa", "handoff": END},
    )
    graph.add_conditional_edges(
        "tools", decidir_resultado, {"continuar": "descubrir_tools", "pausar": "pausa", "final": END}
    )
    graph.add_conditional_edges("pausa", decidir_tras_pausa, {"confirmar": "confirmar", "agente": "encaminar"})
    graph.add_conditional_edges("confirmar", decidir_resultado, {"continuar": "encaminar", "final": END})
    return graph.compile(checkpointer=checkpointer)
