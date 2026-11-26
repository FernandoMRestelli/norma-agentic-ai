"""Observabilidad del agente: el resumen de cada turno para el log (RNF-07) y las trazas opcionales en MLflow.

Con `MLFLOW_ENABLED` apagado (el valor por defecto) no se importa mlflow y los pasos no hacen nada: los tests y el CI
siguen sin red (RNF-08). Prendido, cada turno es una traza con un span por llamada al LLM y otro por tool.
La traza lleva IDs, nombres y números, nunca el texto de los mensajes (RNF-03). `MLFLOW_TRACE_TEXT` suma el texto y el
prompt completo, y solo se puede prender en desarrollo, con datos simulados.
"""

import json
import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from types import ModuleType
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from internal.agent.state import EstadoNorma
from internal.config import get_settings
from internal.schemas.agent_schema import AgentOutcome

logger = logging.getLogger(__name__)

# Atributo que MLflow suma por traza para mostrar los tokens (mlflow.tracing.constant.SpanAttributeKey.CHAT_USAGE)
_USO_TOKENS = "mlflow.chat.tokenUsage"
# Con los contenedores arrancando a la vez, MLflow puede tardar más que el primer mensaje: se vuelve a probar
REINTENTO_SEGUNDOS = 60.0
MAX_AVISOS = 2  # los de guardrails.MAX_RECHAZOS: importarlo de ahí cierra un ciclo
_ultimo_intento = 0.0


@dataclass
class ResumenTurno:
    """Lo que pasó en un turno, sacado del estado del grafo. Sin texto de los mensajes (RNF-03)."""

    tools: list[str] = field(default_factory=list)  # en el orden en que las pidió el LLM
    errores: list[str] = field(default_factory=list)  # códigos de las tools que devolvieron ok: false
    skills: list[str] = field(default_factory=list)
    tokens_entrada: int = 0
    tokens_salida: int = 0
    fallas: int = 0  # en toda la conversación
    draft_id: str | None = None
    via: str = "llm"  # confirmar_directo: «OK» registró la propuesta sin llamar al LLM (RF-13)
    # Los sub-loops, aparte del orquestador (RF-94): sus mensajes no quedan en el estado, sí su uso en la nota
    llamadas_sub_loop: int = 0
    tokens_entrada_sub_loop: int = 0
    tokens_salida_sub_loop: int = 0
    sub_loops: list[str] = field(default_factory=list)  # la skill de cada delegación, en orden
    # Cuando el turno salió, pero no como debía (RF-123): mensaje_fijo, cierre, confirmacion_pedida o tolerado:<código>
    rescates: list[str] = field(default_factory=list)


def _rescates(data: dict[str, Any]) -> list[str]:
    """Lo que una tool dice que resolvió el sistema en lugar del LLM."""
    rescates = [nombre for nombre in ("mensaje_fijo", "confirmacion_pedida") if data.get(nombre)]
    return rescates + [f"tolerado:{codigo}" for codigo in data.get("tolerado") or []]


def resumir(state: EstadoNorma) -> ResumenTurno:
    resumen = ResumenTurno(skills=list(state.get("skills_activas", [])), fallas=state.get("fallas", 0))
    avisos = 0
    for message in state.get("messages", [])[state.get("inicio_turno", 0) :]:
        if isinstance(message, AIMessage):
            resumen.tools.extend(call["name"] for call in message.tool_calls)
            if uso := message.usage_metadata:
                resumen.tokens_entrada += uso.get("input_tokens", 0)
                resumen.tokens_salida += uso.get("output_tokens", 0)
        elif isinstance(message, ToolMessage):
            result = json.loads(message.content)
            if not result.get("ok"):
                resumen.errores.append(result["error"]["code"])
            else:
                resumen.rescates += _rescates(result["data"])
        elif isinstance(message, HumanMessage) and "guardrail" in message.additional_kwargs:
            resumen.errores.append("respuesta_rechazada")  # texto suelto que no pasó el validador
            avisos += 1
    for nota in state.get("notas_turno") or []:
        if nota.get("tipo") == "sub_loop":
            resumen.sub_loops.append(nota["skill"])
            resumen.llamadas_sub_loop += nota.get("llamadas", 0)
            resumen.tokens_entrada_sub_loop += nota.get("tokens_entrada", 0)
            resumen.tokens_salida_sub_loop += nota.get("tokens_salida", 0)
    if avisos > MAX_AVISOS:  # el texto suelto agotó los intentos: salió el mensaje fijo
        resumen.rescates.append("mensaje_fijo")
    if state.get("cierre_desde") is not None:
        resumen.rescates.append("cierre")
    if propuesta := state.get("propuesta"):
        resumen.draft_id = propuesta.get("draft_id")
    if state.get("kind") == "pedido" and not state.get("iteraciones"):
        resumen.via = "confirmar_directo"
    return resumen


def cliente() -> ModuleType | None:
    """El módulo mlflow ya conectado, o None si las trazas están apagadas o MLflow no respondió."""
    if not get_settings().mlflow_enabled:
        return None
    modulo = _configurar()
    if modulo is None and time.monotonic() - _ultimo_intento >= REINTENTO_SEGUNDOS:
        _configurar.cache_clear()
        modulo = _configurar()
    return modulo


@lru_cache(maxsize=1)
def _configurar() -> ModuleType | None:
    """Conecta una sola vez por proceso. Si MLflow no responde, el agente sigue sin trazas y se reintenta después."""
    global _ultimo_intento
    _ultimo_intento = time.monotonic()
    settings = get_settings()
    # Sin el servidor, MLflow reintenta por minutos y frenaría la primera respuesta al cliente
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "1")
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", "5")
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    try:
        import mlflow

        mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
        mlflow.set_experiment(settings.mlflow_experiment)
        if settings.mlflow_trace_text:
            mlflow.langchain.autolog()  # suma los spans de LangChain con el prompt y las respuestas completas
    except Exception as exc:  # la URI puede llevar credenciales: no se loguea (RNF-14)
        logger.warning("MLflow: no se pudo configurar (%s); el agente sigue sin trazas", type(exc).__name__)
        return None
    logger.info("MLflow: trazas del agente en el experimento %s", settings.mlflow_experiment)
    return mlflow


class Paso:
    """Un span de MLflow, o nada si las trazas están apagadas."""

    def __init__(self, span: Any = None):
        self._span = span

    def atributos(self, **valores: Any) -> None:
        if self._span is not None:
            self._span.set_attributes({clave: valor for clave, valor in valores.items() if valor is not None})

    def texto(self, entrada: Any = None, salida: Any = None) -> None:
        """Solo con MLFLOW_TRACE_TEXT (RNF-03)."""
        if self._span is None or not get_settings().mlflow_trace_text:
            return
        if entrada is not None:
            self._span.set_inputs(entrada)
        if salida is not None:
            self._span.set_outputs(salida)

    def fallo(self, motivo: str) -> None:
        """El motivo es un nombre o un código, nunca el mensaje de la excepción: puede traer texto del cliente."""
        if self._span is not None:
            self._span.set_attribute("error", motivo)
            self._span.set_status("ERROR")

    def respuesta_llm(self, message: AIMessage) -> None:
        if self._span is None:
            return
        settings = get_settings()
        self.atributos(proveedor=settings.llm_provider, modelo=settings.llm_model)
        self._span.set_attribute("tools_pedidas", [call["name"] for call in message.tool_calls])
        if uso := message.usage_metadata:
            tokens = {clave: uso.get(clave, 0) for clave in ("input_tokens", "output_tokens", "total_tokens")}
            self._span.set_attribute(_USO_TOKENS, tokens)

    def resultado_tool(self, argumentos: dict[str, Any], result: dict[str, Any]) -> None:
        if self._span is None:
            return
        self._span.set_attribute("ok", result["ok"])
        if not result["ok"]:
            self.fallo(result["error"]["code"])
        self.texto(entrada=argumentos, salida=result)


@contextmanager
def paso(nombre: str, tipo: str = "UNKNOWN", **atributos: Any) -> Iterator[Paso]:
    """Un span hijo del turno: una llamada al LLM (`CHAT_MODEL`) o una tool (`TOOL`)."""
    mlflow = cliente()
    if mlflow is None:
        yield Paso()
        return
    with mlflow.start_span(nombre, span_type=tipo) as span:
        actual = Paso(span)
        actual.atributos(**atributos)
        yield actual


class Turno(Paso):
    def cerrar(self, outcome: AgentOutcome, resumen: ResumenTurno, latencia_ms: int, prompt_version: str) -> None:
        mlflow = cliente()
        if self._span is None or mlflow is None:
            return
        settings = get_settings()
        try:
            self.atributos(
                kind=outcome.kind,
                iteraciones=outcome.iterations,
                fallas=resumen.fallas,
                tools=resumen.tools,
                errores=resumen.errores,
                rescates=resumen.rescates,
                skills=resumen.skills,
                tokens_entrada=resumen.tokens_entrada,
                tokens_salida=resumen.tokens_salida,
                llamadas_sub_loop=resumen.llamadas_sub_loop,
                tokens_entrada_sub_loop=resumen.tokens_entrada_sub_loop,
                tokens_salida_sub_loop=resumen.tokens_salida_sub_loop,
                sub_loops=resumen.sub_loops,
                latencia_ms=latencia_ms,
                draft_id=resumen.draft_id,
                order_id=outcome.order_id,
            )
            self.texto(salida={"respuesta": outcome.message})
            if outcome.kind == "error":
                self._span.set_status("ERROR")
            etiquetas = {
                "kind": outcome.kind,
                "via": resumen.via,
                "cerrada": str(outcome.cerrada).lower(),
                "proveedor": settings.llm_provider or "",
                "modelo": settings.llm_model or "",
                "prompt_version": prompt_version,
                "skills": ",".join(resumen.skills),
                "app_version": settings.app_version,
                "app_env": settings.app_env,
            }
            mlflow.update_current_trace(tags=etiquetas)
        except Exception as exc:  # una falla de las trazas nunca le cambia la respuesta al cliente
            logger.warning("MLflow: no se pudo cerrar la traza del turno (%s)", type(exc).__name__)


@contextmanager
def turno(conversation_id: str, canal: str, texto: str, mensaje_id: str | None = None) -> Iterator[Turno]:
    """La traza de un turno. Agrupa los turnos por conversación con el id interno, no con el del cliente.

    Con `mensaje_id`, la del turno que usa la lectura de una imagen lleva el mismo tag que la lectura.
    """
    mlflow = cliente()
    if mlflow is None:
        yield Turno()
        return
    with mlflow.start_span("turno", span_type="AGENT") as span:
        actual = Turno(span)
        actual.atributos(conversation_id=conversation_id, canal=canal)
        actual.texto(entrada={"mensaje": texto})
        try:
            mlflow.update_current_trace(session_id=conversation_id)
            if mensaje_id:
                mlflow.update_current_trace(tags={"mensaje_id": mensaje_id})
        except Exception as exc:
            logger.warning("MLflow: no se pudo abrir la traza del turno (%s)", type(exc).__name__)
        yield actual


@contextmanager
def lectura(mensaje_id: str) -> Iterator[Paso]:
    """La traza de la lectura de una imagen. Con `MLFLOW_TRACE_TEXT`, la llamada al modelo de visión queda adentro,
    con la imagen adjunta."""
    with _traza_previa("lectura_imagen", mensaje_id) as actual:
        yield actual


@contextmanager
def transcripcion(mensaje_id: str) -> Iterator[Paso]:
    """La traza de la transcripción de un audio."""
    with _traza_previa("transcripcion_audio", mensaje_id) as actual:
        yield actual


@contextmanager
def _traza_previa(nombre: str, mensaje_id: str) -> Iterator[Paso]:
    """Una traza que pasa antes del turno. Lleva el tag `mensaje_id`, el mismo que la traza del turno que la usa."""
    mlflow = cliente()
    if mlflow is None:
        yield Paso()
        return
    with mlflow.start_span(nombre, span_type="PARSER") as span:
        actual = Paso(span)
        try:
            mlflow.update_current_trace(tags={"mensaje_id": mensaje_id})
        except Exception as exc:
            logger.warning("MLflow: no se pudo abrir la traza %s (%s)", nombre, type(exc).__name__)
        yield actual
