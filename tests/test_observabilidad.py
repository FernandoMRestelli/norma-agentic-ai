"""Resumen del turno para el log (RNF-07) y trazas en MLflow (CA-56, CA-57), sin red: MLflow guarda en un SQLite."""

import json
import logging
import sys
from decimal import Decimal

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from internal.agent import context, observabilidad
from internal.agent.norma_agent import NormaAgent
from internal.config import Settings, get_settings
from internal.schemas.catalog_schema import Product
from internal.schemas.order_schema import Channel
from internal.services import sheets_service

SKU = "CR-NIV-100"
TEXTO_CLIENTE = "Hola, te encargo 5 cremas Nivea"
RESPUESTA = "Solo me quedan 3 cremas Nivea, ¿te las preparo?"
EXPERIMENTO = "norma-tests"


def _call(name, **args):
    return {"name": name, "args": args, "id": f"call-{name}", "type": "tool_call"}


def _ai(*calls, tokens=(0, 0)):
    uso = {"input_tokens": tokens[0], "output_tokens": tokens[1], "total_tokens": sum(tokens)}
    return AIMessage(content="", tool_calls=list(calls), usage_metadata=uso)


class ModeloGuionado:
    def __init__(self, *guion):
        self.guion = list(guion)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return self.guion.pop(0)


def _guion():
    return (
        _ai(_call("search_products", query="crema nivea"), tokens=(1000, 20)),
        _ai(_call("armar_pedido"), tokens=(1100, 10)),  # sin skill leída: tool_no_permitida
        _ai(_call("leer_skill", nombre="tomar_pedido"), tokens=(1200, 15)),
        _ai(
            _call(
                "respond_to_customer",
                kind="sin_stock",
                message=RESPUESTA,
                propuesta=[{"sku": SKU, "quantity": 3}],
            ),
            tokens=(1500, 40),
        ),
    )


@pytest.fixture(autouse=True)
def catalogo(monkeypatch):
    productos = [Product(sku=SKU, name="Crema Nivea", presentation="100 ml", price=Decimal("1000"), stock=3)]
    monkeypatch.setattr(sheets_service, "get_catalog", lambda: productos)


@pytest.fixture(autouse=True)
def configuracion_limpia():
    """Cada test arranca leyendo su propio entorno, y ninguno deja MLflow prendido para el siguiente."""
    get_settings.cache_clear()
    observabilidad._configurar.cache_clear()
    yield
    if "mlflow" in sys.modules and sys.modules["mlflow"] is not None:
        sys.modules["mlflow"].langchain.autolog(disable=True)
    get_settings.cache_clear()
    observabilidad._configurar.cache_clear()


@pytest.fixture(scope="module")
def tracking_uri(tmp_path_factory):
    """Un SQLite temporal. El experimento se crea antes con sus artifacts ahí: si no, MLflow los deja en ./mlruns."""
    mlflow = pytest.importorskip("mlflow")
    carpeta = tmp_path_factory.mktemp("mlflow")
    uri = f"sqlite:///{carpeta / 'mlflow.db'}"
    mlflow.set_tracking_uri(uri)
    mlflow.create_experiment(EXPERIMENTO, artifact_location=(carpeta / "artifacts").as_uri())
    return uri


@pytest.fixture
def mlflow_local(monkeypatch, tracking_uri):
    mlflow = pytest.importorskip("mlflow")
    monkeypatch.setenv("MLFLOW_ENABLED", "true")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", tracking_uri)
    monkeypatch.setenv("MLFLOW_EXPERIMENT", EXPERIMENTO)
    return mlflow


def _turno():
    agent = NormaAgent(ModeloGuionado(*_guion()), InMemorySaver())
    outcome = agent.handle("42", customer_id="telegram:secreto-77", channel=Channel.TELEGRAM, text=TEXTO_CLIENTE)
    return agent, outcome


def _traza(mlflow):
    trace_id = mlflow.get_last_active_trace_id()
    mlflow.flush_trace_async_logging()
    return mlflow.get_trace(trace_id)


def test_resumen_del_turno_sale_del_estado():
    """RNF-07: tools en orden, errores por código, skills leídas y tokens sumados, sin texto."""
    agent, outcome = _turno()
    state = agent.graph.get_state({"configurable": {"thread_id": "42"}}).values

    resumen = observabilidad.resumir(state)

    assert resumen.tools == ["search_products", "armar_pedido", "leer_skill", "respond_to_customer"]
    assert resumen.errores == ["tool_no_permitida"]
    assert resumen.skills == ["tomar_pedido"]
    assert (resumen.tokens_entrada, resumen.tokens_salida) == (4800, 85)
    assert resumen.draft_id == outcome.draft.draft_id
    assert resumen.via == "llm"


def test_log_del_turno_con_ids_y_sin_texto(caplog):
    """RNF-07 con RNF-03: la línea del turno trae lo necesario para reconstruirlo, pero no lo que dijo el cliente."""
    with caplog.at_level(logging.INFO, logger="internal.agent.norma_agent"):
        _turno()

    linea = caplog.records[-1].getMessage()
    for esperado in (
        "conversation_id=42",
        "kind=sin_stock",
        "iteraciones=4",
        "tools=search_products,armar_pedido,leer_skill,respond_to_customer",
        "errores=tool_no_permitida",
        "skills=tomar_pedido",
        "tokens_entrada=4800",
        "tokens_salida=85",
        f"prompt_version={context.prompt_version()}",
    ):
        assert esperado in linea
    assert "latencia_ms=" in linea
    assert TEXTO_CLIENTE not in linea and RESPUESTA not in linea and "secreto-77" not in linea


def test_apagado_no_importa_mlflow(monkeypatch):
    """CA-56: con MLFLOW_ENABLED apagado (por defecto) el turno no toca mlflow: un import fallaría."""
    monkeypatch.setitem(sys.modules, "mlflow", None)

    def no_configurar():
        raise AssertionError("con MLflow apagado no se configura nada")

    no_configurar.cache_clear = lambda: None
    monkeypatch.setattr(observabilidad, "_configurar", no_configurar)

    _, outcome = _turno()

    assert observabilidad.cliente() is None
    assert outcome.kind == "sin_stock"
    assert outcome.iterations == 4
    assert outcome.message == RESPUESTA


def test_prendido_traza_el_turno_sin_texto(mlflow_local):
    """CA-56: un turno es una traza con un span por llamada al LLM y por tool, con IDs y números, sin texto."""
    _, outcome = _turno()
    trace = _traza(mlflow_local)

    nombres = [span.name for span in trace.data.spans]
    assert nombres[0] == "turno"
    assert nombres.count("llm") == 4
    for tool in ("search_products", "armar_pedido", "leer_skill", "respond_to_customer"):
        assert tool in nombres

    tags = trace.info.tags
    assert tags["kind"] == "sin_stock"
    assert tags["via"] == "llm"
    assert tags["prompt_version"] == context.prompt_version()
    assert tags["skills"] == "tomar_pedido"
    assert trace.info.trace_metadata["mlflow.trace.session"] == "42"
    assert trace.info.token_usage["input_tokens"] == 4800

    raiz = trace.data.spans[0]
    assert raiz.attributes["iteraciones"] == 4
    assert raiz.attributes["draft_id"] == outcome.draft.draft_id
    rechazada = next(span for span in trace.data.spans if span.name == "armar_pedido")
    assert rechazada.attributes["error"] == "tool_no_permitida"
    assert rechazada.status.status_code == "ERROR"

    # RNF-03: ni el mensaje, ni la respuesta, ni el teléfono del cliente quedan en la traza
    guardado = trace.to_json()
    assert "cremas Nivea" not in guardado
    assert RESPUESTA not in guardado
    assert "secreto-77" not in guardado


def test_falla_del_llm_queda_en_la_traza_sin_el_mensaje(mlflow_local):
    """El error del proveedor puede traer texto del cliente: la traza guarda solo el tipo."""

    class ModeloQueFalla(ModeloGuionado):
        def invoke(self, messages):
            raise TimeoutError(f"timeout procesando: {TEXTO_CLIENTE}")

    agent = NormaAgent(ModeloQueFalla(), InMemorySaver())
    outcome = agent.handle("43", customer_id="telegram:1", channel=Channel.TELEGRAM, text=TEXTO_CLIENTE)
    trace = _traza(mlflow_local)

    assert outcome.kind == "error"
    llm = next(span for span in trace.data.spans if span.name == "llm")
    assert llm.attributes["error"] == "TimeoutError"
    assert trace.info.tags["kind"] == "error"
    assert "cremas Nivea" not in trace.to_json()


def test_con_texto_en_desarrollo_guarda_mensaje_y_respuesta(mlflow_local, monkeypatch):
    """CA-57: MLFLOW_TRACE_TEXT suma el texto a la traza, para depurar con datos simulados."""
    monkeypatch.setenv("MLFLOW_TRACE_TEXT", "true")

    _turno()
    trace = _traza(mlflow_local)

    raiz = trace.data.spans[0]
    assert raiz.inputs == {"mensaje": TEXTO_CLIENTE}
    assert raiz.outputs == {"respuesta": RESPUESTA}


def test_texto_en_la_traza_solo_en_desarrollo():
    """CA-57: fuera de desarrollo, prender MLFLOW_TRACE_TEXT impide arrancar (RNF-03)."""
    with pytest.raises(ValidationError, match="MLFLOW_TRACE_TEXT"):
        Settings(app_env="production", mlflow_trace_text=True)
    assert Settings(app_env="development", mlflow_trace_text=True).mlflow_trace_text


def test_sin_servidor_el_agente_sigue_sin_trazas(monkeypatch, caplog):
    """Si MLflow no responde, el cliente recibe la misma respuesta y queda un aviso en el log."""
    pytest.importorskip("mlflow")
    monkeypatch.setenv("MLFLOW_ENABLED", "true")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:9")  # puerto cerrado
    monkeypatch.setenv("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")

    with caplog.at_level(logging.WARNING, logger="internal.agent.observabilidad"):
        _, outcome = _turno()

    assert observabilidad.cliente() is None
    assert outcome.kind == "sin_stock"
    assert outcome.message == RESPUESTA
    assert "MLflow: no se pudo configurar" in caplog.text


def test_version_del_prompt_es_estable():
    assert context.prompt_version() == context.prompt_version()
    assert len(context.prompt_version()) == 10


def test_resumen_de_confirmacion_directa():
    """«OK» registra la propuesta sin LLM: el turno no tiene iteraciones."""
    state = {"messages": [], "kind": "pedido", "iteraciones": 0, "fallas": 0}
    assert observabilidad.resumir(state).via == "confirmar_directo"


def test_errores_de_tools_por_codigo():
    from langchain_core.messages import ToolMessage

    error = {"ok": False, "error": {"code": "sku_inexistente", "message": "x", "details": {}}}
    state = {
        "messages": [
            ToolMessage(content=json.dumps({"ok": True, "data": {}}), tool_call_id="1", name="get_product"),
            ToolMessage(content=json.dumps(error), tool_call_id="2", name="respond_to_customer"),
        ]
    }
    assert observabilidad.resumir(state).errores == ["sku_inexistente"]


def _fila(caso, resultado, latencia_ms, iteraciones=3):
    return {
        "caso": caso,
        "resultado": resultado,
        "kind": "propuesta",
        "esperado": "{}",
        "iteraciones": iteraciones,
        "tools": "leer_skill,respond_to_customer",
        "skills": "tomar_pedido",
        "tokens_entrada": 1000,
        "tokens_salida": 50,
        "latencia_ms": latencia_ms,
    }


FILAS = [
    _fila("a", "correcto", 1000),
    _fila("b", "incorrecto", 3000, iteraciones=5),
    _fila("c", "correcto", 2000),
    _fila("d", "sin_evaluar", 100, iteraciones=1),
]


def test_metricas_de_la_evaluacion():
    """Los casos sin evaluar (el LLM no respondió) no cuentan para aciertos, iteraciones ni latencia."""
    from internal import evaluar_agente

    metricas = evaluar_agente._metricas(FILAS)

    assert metricas["aciertos_pct"] == 66.7
    assert (metricas["correctos"], metricas["evaluados"], metricas["sin_evaluar"]) == (2, 3, 1)
    assert metricas["iteraciones_promedio"] == 3.67
    assert metricas["tokens_entrada"] == 4000
    assert (metricas["latencia_p50_ms"], metricas["latencia_p95_ms"]) == (2000, 3000)
    assert evaluar_agente._metricas([])["aciertos_pct"] == 0.0


def test_corrida_de_evaluacion_en_mlflow(mlflow_local):
    """La corrida queda con proveedor, modelo y versión del prompt para compararla con otras."""
    from internal import evaluar_agente

    agent = NormaAgent(ModeloGuionado(), InMemorySaver())
    mlflow = observabilidad.cliente()
    with mlflow.start_run() as run:
        evaluar_agente._registrar(mlflow, agent, [{}, {}, {}, {}], None, FILAS)

    guardada = mlflow.get_run(run.info.run_id)
    assert guardada.data.params["prompt_version"] == context.prompt_version()
    assert guardada.data.params["caso"] == "todos"
    assert guardada.data.metrics["aciertos_pct"] == 66.7
