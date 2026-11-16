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

from internal.agent import (
    context,
    contexto_cliente,
    guardrails,
    observabilidad,
    precarga,
    sub_loop,
    tool_registry,
)
from internal.agent.state import EstadoNorma
from internal.config import get_settings
from internal.schemas.order_schema import Channel, ConfirmationAction, OrderDraft
from internal.services import interpretation_service, inventory_service
from internal.services.palabras_clave import (
    es_cancelacion,
    es_pedido_humano,
    es_saludo,
    es_solicitud_comercial,
    interpretar_palabra_clave,
)
from internal.services.skill_service import get_skills

logger = logging.getLogger(__name__)

# RNF-16: el presupuesto de un turno sale de AGENT_MAX_ITERATIONS y AGENT_MAX_SECONDS_PER_TURN (config.py). El plazo
# se controla entre una iteración y la siguiente. Al agotarse, el turno no falla: cierra respondiendo
ITERACIONES_CIERRE = 2  # para responder con lo encontrado; la segunda es por si el validador rechaza la primera
TOOL_CIERRE = "respond_to_customer"
MAX_FAILURES = 2  # D-07
# Con una propuesta vigente, un mensaje así de corto y sin números casi siempre la acepta, la rechaza o duda
MAX_PALABRAS_RESPUESTA_A_PROPUESTA = 3
_reloj = time.time  # los tests lo reemplazan para no esperar
FAILURE_MESSAGE = "Tuve un problema, probá de nuevo en unos minutos."
HANDOFF_MESSAGE = "Te va a responder una persona del equipo."


def max_iteraciones() -> int:
    return get_settings().agent_max_iterations


def max_segundos_turno() -> float:
    return get_settings().agent_max_seconds_per_turn


def recursion_limit() -> int:
    """Cada iteración pasa por tres nodos. Es una red de seguridad: el tope real es `max_iteraciones()`."""
    return (max_iteraciones() + ITERACIONES_CIERRE) * 3 + 12


def _failure(state: EstadoNorma) -> dict[str, Any]:
    """Registra una falla. Con 2 o más fallas deriva a una persona (RF-27, CA-21)."""
    fallas = state.get("fallas", 0) + 1
    es_handoff = fallas >= MAX_FAILURES
    msg = HANDOFF_MESSAGE if es_handoff else FAILURE_MESSAGE
    return {
        "fallas": fallas,
        "falla_turno": True,
        "respuesta": msg,
        "kind": "derivada" if es_handoff else "error",
        "derivation_reason": "fallas_repetidas" if es_handoff else None,
        "resultado": "final" if es_handoff else "pausar",
        "messages": [AIMessage(content=msg)],
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
    if es_pedido_humano(texto) or es_solicitud_comercial(texto) or (hay_propuesta and es_cancelacion(texto)):
        return "derivar_o_cancelar"
    tiene_numero = any(caracter.isdigit() for caracter in texto)
    renglones = sum(bool(linea.strip()) for linea in texto.splitlines())
    if hay_propuesta:
        corto = len(texto.split()) <= MAX_PALABRAS_RESPUESTA_A_PROPUESTA
        if corto and not tiene_numero:
            return "confirmar_pedido"
        # Una lista es un pedido nuevo o cambios sobre la propuesta: los dos los resuelve tomar_pedido
        return "tomar_pedido" if renglones >= 2 else None
    if es_saludo(texto):
        return "informar"
    return "tomar_pedido" if renglones >= 2 or tiene_numero else None


def armar_contexto_cliente(state: EstadoNorma) -> dict[str, Any]:
    """Arma el contexto del cliente sin LLM si no hay uno de hoy; si lo hay, no toca la base (RF-84, CA-145)."""
    if contexto_cliente.vigente(state.get("contexto_cliente")):
        return {}
    with observabilidad.paso("contexto_cliente", "RETRIEVER") as paso:
        contexto = contexto_cliente.armar(state["customer_id"], state.get("customer_name"))
        paso.atributos(pedidos=len(contexto["pedidos"]), comprados=len(contexto["comprados"]))
        paso.texto(salida=contexto)
        if not contexto["completo"]:
            paso.fallo("historial_no_disponible")
    return {"contexto_cliente": contexto}


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
        "cierre_desde": None,
        "renglones": renglones + nuevos,
        "notas_turno": [],
        "iteraciones_sub_loop": 0,
    }


def precarga_busqueda(state: EstadoNorma) -> dict[str, Any]:
    """Busca sin LLM los renglones nuevos de forma inequívoca y deja la nota de búsqueda (RF-86, CA-148)."""
    renglones = [dict(renglon) for renglon in state.get("renglones") or []]
    if not any(renglon["estado"] == "sin_resolver" for renglon in renglones):
        return {}
    skills_activas = list(state.get("skills_activas") or [])
    br_entregadas = list(state.get("br_entregadas") or [])
    comprados = contexto_cliente.comprados(state.get("contexto_cliente"))
    with observabilidad.paso("precarga_busqueda", "RETRIEVER") as paso:
        sin_resolver = sum(1 for renglon in renglones if renglon["estado"] == "sin_resolver")  # antes de buscar
        nota = precarga.precargar(renglones, comprados, skills_activas, br_entregadas)
        paso.atributos(**precarga.cuentas(nota, sin_resolver))
        if nota:
            paso.texto(entrada={"buscados": nota["buscados"]}, salida={"resultados": nota["resultados"]})
    if nota is None:
        return {}
    return {
        "renglones": renglones,
        "notas_turno": [*(state.get("notas_turno") or []), nota],
        "br_entregadas": br_entregadas,
    }


def _sumar_origen(anterior: str | None, nuevo: str) -> str:
    """Los orígenes de la conversación, sin repetir y en orden: un audio corregido por texto es `audio+texto`."""
    origenes = [parte for parte in (anterior or "").split("+") if parte]
    return "+".join(origenes if nuevo in origenes else [*origenes, nuevo])


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
    iteraciones = state.get("iteraciones", 0)
    # Las vueltas de los sub-loops cuentan para el tope del turno (RF-93); el cierre cuenta las del orquestador
    usadas = iteraciones + state.get("iteraciones_sub_loop", 0)
    if (cierre_desde := state.get("cierre_desde")) is not None:
        if iteraciones >= cierre_desde + ITERACIONES_CIERRE:
            logger.warning("Agente: tampoco pudo responder después de agotar el presupuesto del turno")
            return _failure(state)
        return {"tools_disponibles": [TOOL_CIERRE]}
    motivo = None
    if usadas >= max_iteraciones():
        motivo = "iteraciones"
        logger.warning("Agente: se alcanzó el tope de %d iteraciones, cierra con lo encontrado", max_iteraciones())
    elif _reloj() - state.get("turno_desde", _reloj()) > max_segundos_turno():
        motivo = "plazo"
        logger.warning("Agente: el turno pasó el plazo de %d s, cierra con lo encontrado", max_segundos_turno())
    if motivo:
        # En vez de cortar con el mensaje de falla, el modelo responde con lo que ya averiguó (RNF-16)
        with observabilidad.paso("cierre", "CHAIN", motivo=motivo, iteraciones=iteraciones):
            pass
        return {"cierre_desde": iteraciones, "tools_disponibles": [TOOL_CIERRE]}
    orquestador = tool_registry.orquestador_activo()
    return {"tools_disponibles": tool_registry.available(state.get("skills_activas", []), orquestador)}


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
        origen=state.get("origen") or "texto",
        comprados=contexto_cliente.comprados(state.get("contexto_cliente")),
        propuesta=propuesta,
    )


def _tras_registrar(order_id: str | None) -> dict[str, Any]:
    """Un pedido registrado cambia el historial: el contexto del cliente se vuelve a armar (D-31, CA-146)."""
    return {"contexto_cliente": None} if order_id else {}


def _contexto_validacion(state: EstadoNorma) -> guardrails.ContextoTurno:
    """Lo que vio el LLM en el turno, para el validador de salida: los resultados de tools, las notas del turno y los
    SKU que figuran en su prompt (opciones previas y pedidos anteriores), así tampoco se le escapa uno de esos."""
    turno = guardrails.contexto_turno(state["messages"][state.get("inicio_turno", 0) :])
    guardrails.sumar_notas(turno, state.get("notas_turno") or [])
    turno.skus_del_prompt |= set(state.get("opciones_previas") or [])
    for pedido in (state.get("contexto_cliente") or {}).get("pedidos") or []:
        turno.skus_del_prompt |= {linea["sku"] for linea in pedido["lineas"]}
    return turno


def _plazo_vencido(state: EstadoNorma) -> bool:
    return _reloj() - state.get("turno_desde", _reloj()) > max_segundos_turno()


def build_graph(model: BaseChatModel, checkpointer: BaseCheckpointSaver) -> CompiledStateGraph:
    def delegador(state: EstadoNorma) -> tool_registry.Delegar:
        """`delegar_skill` corre el sub-loop con el mismo modelo (D-38) y lo que le queda al turno (RF-93)."""
        entrada = state["messages"][state.get("inicio_turno", 0)]
        opciones_previas = list(state.get("opciones_previas") or [])

        def delegar(skill, encargo, numeros, ctx):
            usadas = state.get("iteraciones", 0) + ctx.iteraciones_sub_loop
            return sub_loop.correr(
                model,
                skill,
                encargo,
                numeros,
                ctx,
                contexto=state.get("contexto_cliente"),
                ultimo_mensaje=entrada.text,
                opciones_previas=opciones_previas,
                opciones_texto=context.opciones_previas(opciones_previas),
                presupuesto=max(max_iteraciones() - usadas, 0),
                plazo_vencido=lambda: _plazo_vencido(state),
            )

        return delegar

    def llamar_modelo(state: EstadoNorma) -> dict[str, Any]:
        iterations = state.get("iteraciones", 0) + 1
        with observabilidad.paso("llm", "CHAT_MODEL", iteracion=iterations) as paso:
            try:
                # El modelo tiene que elegir una tool (RF-61): el texto suelto queda como red de seguridad
                message = model.bind_tools(
                    tool_registry.schemas(state["tools_disponibles"]), tool_choice="any"
                ).invoke(context.build_messages(state, tool_registry.orquestador_activo()))
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
        turno = _contexto_validacion(state)
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
        ctx.turno = _contexto_validacion(state)
        ctx.coberturas_fallidas = sum(
            isinstance(m, ToolMessage) and '"code": "renglones_sin_cuenta"' in m.content for m in del_turno
        )
        ctx.orquestador = tool_registry.orquestador_activo()
        ctx.notas_turno = list(state.get("notas_turno") or [])
        ctx.iteraciones_sub_loop = state.get("iteraciones_sub_loop", 0)
        if ctx.orquestador:
            ctx.delegar = delegador(state)
        messages: list[Any] = []
        for call in state["messages"][-1].tool_calls:
            if ctx.resultado == "continuar":
                with observabilidad.paso(call["name"], "TOOL", iteracion=state.get("iteraciones")) as paso:
                    try:
                        result = tool_registry.execute(call["name"], call["args"], ctx)
                    except tool_registry.FallaSubLoop:
                        # Como una falla del LLM del orquestador: la propuesta vigente no cambia (§7)
                        paso.fallo("falla_sub_loop")
                        return {
                            **_failure(state),
                            "notas_turno": ctx.notas_turno,
                            "iteraciones_sub_loop": ctx.iteraciones_sub_loop,
                            "resultado": "continuar",
                        }
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

        # 3 aclaraciones seguidas sin borrador derivan a humano (RF-27)
        aclaraciones = state.get("aclaraciones_seguidas", 0)
        if ctx.kind == "aclaracion" and not ctx.propuesta:
            aclaraciones += 1
            if aclaraciones >= 3:
                ctx.kind = "derivada"
                ctx.respuesta = HANDOFF_MESSAGE
                ctx.derivation_reason = "aclaraciones_repetidas"
                ctx.resultado = "final"
                messages[-1] = AIMessage(content=HANDOFF_MESSAGE)
        elif ctx.propuesta or ctx.kind in ("propuesta", "pedido"):
            aclaraciones = 0

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
            "derivation_reason": ctx.derivation_reason,
            "aclaraciones_seguidas": aclaraciones,
            "resultado": ctx.resultado,
            "notas_turno": ctx.notas_turno,
            "iteraciones_sub_loop": ctx.iteraciones_sub_loop,
            **_tras_registrar(ctx.order_id),
        }

    def pausa(state: EstadoNorma) -> dict[str, Any]:
        # El nodo vuelve a correr desde el principio al retomar: antes de interrupt() no puede haber efectos
        entrada = interrupt({"mensaje": state["respuesta"]})
        # Al retomar llega el texto y de dónde salió; un checkpoint anterior retoma con el texto solo
        text, origen = (entrada["texto"], entrada["origen"]) if isinstance(entrada, dict) else (entrada, "texto")
        return {
            "messages": [HumanMessage(content=text)],
            "origen": _sumar_origen(state.get("origen"), origen),
            "opciones_previas": list(state.get("options") or []),
            "inicio_turno": len(state["messages"]),
            "skills_activas": [],
            "iteraciones": 0,
            "iteraciones_sub_loop": 0,
            "notas_turno": [],
            "cierre_desde": None,
            "falla_turno": False,
            "respuesta": None,
            "kind": None,
            "options": [],
            "resultado": "continuar",
            "derivation_reason": None,
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
            # Queda una propuesta solo si cambió un precio: sale de nuevo con los de hoy y espera otra confirmación
            "propuesta": ctx.propuesta.model_dump(mode="json") if ctx.propuesta else None,
            "respuesta": ctx.respuesta,
            "kind": ctx.kind,
            "order_id": ctx.order_id,
            "resultado": ctx.resultado,
            **_tras_registrar(ctx.order_id),
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
    graph.add_node("armar_contexto", armar_contexto_cliente)
    graph.add_node("encaminar", encaminar)
    graph.add_node("precarga_busqueda", precarga_busqueda)
    graph.add_node("descubrir_tools", descubrir_tools)
    graph.add_node("llm", llamar_modelo)
    graph.add_node("tools", ejecutar_tools)
    graph.add_node("pausa", pausa)
    graph.add_node("confirmar", confirmar)

    graph.add_edge(START, "armar_contexto")
    graph.add_edge("armar_contexto", "encaminar")
    graph.add_edge("encaminar", "precarga_busqueda")
    graph.add_edge("precarga_busqueda", "descubrir_tools")
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
    graph.add_conditional_edges(
        "pausa", decidir_tras_pausa, {"confirmar": "confirmar", "agente": "armar_contexto"}
    )
    graph.add_conditional_edges(
        "confirmar", decidir_resultado, {"continuar": "armar_contexto", "pausar": "pausa", "final": END}
    )
    return graph.compile(checkpointer=checkpointer)
