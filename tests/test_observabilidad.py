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
# Lo que ve el cliente: la respuesta del agente más el detalle de la propuesta, que arma el sistema
MENSAJE_FINAL = f"{RESPUESTA}\n\n• 3 x Crema Nivea 100 ml ($1000.00 c/u = $3000.00)\nTotal estimado: $3000.00"
EXPERIMENTO = "norma-tests"


def _call(name, **args):
    return {"name": name, "args": args, "id": f"call-{name}", "type": "tool_call"}


def _ai(*calls, tokens=(0, 0)):
    uso = {"input_tokens": tokens[0], "output_tokens": tokens[1], "total_tokens": sum(tokens)}
    return AIMessage(content="", tool_calls=list(calls), usage_metadata=uso)


class ModeloGuionado:
    def __init__(self, *guion):
        self.guion = list(guion)

    def bind_tools(self, tools, **kwargs):
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
    assert outcome.message == MENSAJE_FINAL


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
    assert raiz.outputs == {"respuesta": MENSAJE_FINAL}


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
    assert outcome.message == MENSAJE_FINAL
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


@pytest.fixture
def orquestador_en_mlflow(mlflow_local, monkeypatch):
    """Un pedido de dos renglones delegado en un sub-loop, con el catálogo del orquestador y la traza prendida."""
    from tests import test_orquestador as orq

    monkeypatch.setenv("AGENT_ORQUESTADOR", "true")
    productos = [
        Product(sku=sku, name=nombre, brand="Marca Uno", presentation=medida, price=Decimal("1000"), stock=20)
        for sku, nombre, medida in (
            ("SH-KER-900", "Shampoo Keratina", "900 ml"),
            ("SH-KER-300", "Shampoo Keratina", "300 ml"),
            ("MAS-ARG-250", "Máscara Argan", "250 g"),
            ("MAS-ARG-1K", "Máscara Argan", "1 kg"),
        )
    ]
    monkeypatch.setattr(sheets_service, "get_catalog", lambda: productos)

    def correr():
        agent = NormaAgent(ModeloGuionado(orq._delegar(), orq._nota(), orq._responder()), InMemorySaver())
        agent.handle("44", customer_id="telegram:secreto-77", channel=Channel.TELEGRAM, text=orq.LISTA)
        trace = _traza(mlflow_local)
        return trace, {span.name: span for span in trace.data.spans}

    return correr


def test_el_orquestador_deja_sus_cuentas_en_la_traza(orquestador_en_mlflow):
    """Precarga, sub-loop y nota cuentan qué buscaron y qué resolvieron, sin texto del cliente (RNF-03)."""
    trace, spans = orquestador_en_mlflow()

    precarga = spans["precarga_busqueda"].attributes
    assert (precarga["buscados"], precarga["sin_separar"], precarga["varias"]) == (2, 0, 2)
    sub_loop = spans["sub_loop"].attributes
    assert (sub_loop["resueltos"], sub_loop["pendientes"], sub_loop["afuera"]) == (1, 1, 0)
    assert (sub_loop["tokens_entrada"], sub_loop["cerrada_por"]) == (0, "nota")  # el modelo guionado no da uso
    nota = spans["nota"].attributes
    assert (nota["entrega"], nota["resueltos"], nota["pendientes"]) == (1, 1, 1)

    guardado = trace.to_json()
    assert "keratina" not in guardado.lower()
    assert "Resolvé producto" not in guardado  # el encargo lo escribe el LLM y puede traer texto del cliente


def test_con_texto_el_orquestador_guarda_encargo_y_nota(orquestador_en_mlflow, monkeypatch):
    """CA-57: con MLFLOW_TRACE_TEXT, el sub-loop guarda su encargo y su nota, y la precarga lo que buscó."""
    monkeypatch.setenv("MLFLOW_TRACE_TEXT", "true")

    _, spans = orquestador_en_mlflow()

    assert spans["sub_loop"].inputs == {"encargo": "Resolvé producto, medida y cantidad", "renglones": [1, 2]}
    assert spans["sub_loop"].outputs["resueltos"][0]["sku"] == "SH-KER-900"
    assert spans["nota"].inputs["resueltos"][0]["sku"] == "SH-KER-900"
    assert [b["texto"] for b in spans["precarga_busqueda"].inputs["buscados"]] == ["shampoo keratina", "mascara argan"]
    assert {r["estado"] for r in spans["precarga_busqueda"].outputs["resultados"]} == {"varias"}


def test_lectura_y_turno_comparten_mensaje(mlflow_local, monkeypatch):
    """La traza de la lectura y la del turno que la usa llevan el mismo `mensaje_id`."""
    from internal.schemas.vision_schema import ImagenLeida, RenglonLeido
    from internal.services import message_service, vision_service

    leida = ImagenLeida(legible=True, tipo="lista_pedido", texto="5 cremas nivea",
                        renglones=[RenglonLeido(texto="5 cremas nivea", producto="cremas nivea", cantidad=5)])
    monkeypatch.setattr(message_service, "download_media", lambda url: b"imagen")
    monkeypatch.setattr(
        vision_service, "leer_imagen", lambda imagen: vision_service.Lectura("openai", "m", leida, "", 1.0, 10, 20)
    )

    entrada = message_service.leer_imagen_del_mensaje("telegram:abc", external_id="tg-99")
    lectura = _traza(mlflow_local)
    agent = NormaAgent(ModeloGuionado(*_guion()), InMemorySaver())
    agent.handle("43", customer_id="telegram:1", channel=Channel.TELEGRAM, text=entrada.texto, origen="imagen",
                 lectura=entrada.lectura.model_dump(), mensaje_id=entrada.mensaje_id)  # fmt: skip
    turno = _traza(mlflow_local)

    assert entrada.mensaje_id == "tg-99"
    assert lectura.info.tags["mensaje_id"] == turno.info.tags["mensaje_id"] == "tg-99"
    (raiz,) = [span for span in lectura.data.spans if span.parent_id is None]
    assert raiz.name == "lectura_imagen"
    assert (raiz.attributes["renglones"], raiz.attributes["tokens_salida"]) == (1, 20)
    assert raiz.outputs is None  # sin MLFLOW_TRACE_TEXT, sin el texto de la imagen (RNF-03)


def test_transcripcion_y_turno_comparten_mensaje(mlflow_local, monkeypatch):
    """CA-168: un audio deja su traza `transcripcion_audio`, enlazada con la del turno y sin el texto (RNF-03)."""
    from internal.schemas.order_schema import IncomingMessage, MessageType
    from internal.services import audio_service, message_service

    monkeypatch.setenv("AUDIO_PROVIDER", "openai")
    monkeypatch.setattr(audio_service, "transcribe_audio", lambda url: TEXTO_CLIENTE)
    mensaje = IncomingMessage(customer_id="telegram:secreto-77", channel=Channel.TELEGRAM, type=MessageType.AUDIO,
                              media_url="telegram:abc", external_id="tg-100")  # fmt: skip

    entrada = message_service.preparar(mensaje)
    audio = _traza(mlflow_local)
    agent = NormaAgent(ModeloGuionado(*_guion()), InMemorySaver())
    agent.handle("45", customer_id="telegram:1", channel=Channel.TELEGRAM, text=entrada.texto, origen="audio",
                 mensaje_id=entrada.mensaje_id)  # fmt: skip
    turno = _traza(mlflow_local)

    assert audio.info.tags["mensaje_id"] == turno.info.tags["mensaje_id"] == "tg-100"
    (raiz,) = audio.data.spans
    assert raiz.name == "transcripcion_audio"
    assert raiz.attributes["proveedor"] == "openai"
    assert raiz.attributes["palabras"] == len(TEXTO_CLIENTE.split())
    assert TEXTO_CLIENTE not in audio.to_json()
    assert "secreto-77" not in audio.to_json()


def test_transcripcion_fallida_queda_en_la_traza_sin_el_mensaje(mlflow_local, monkeypatch):
    """CA-168: si la transcripción falla, la traza dice el tipo de error y la excepción llega igual."""
    from internal.services import audio_service, message_service

    def falla(url):
        raise audio_service.AudioProcessingError("detalle-privado")

    monkeypatch.setattr(audio_service, "transcribe_audio", falla)

    with pytest.raises(audio_service.AudioProcessingError):
        message_service.transcribir_audio_del_mensaje("telegram:abc")

    traza = _traza(mlflow_local)
    (raiz,) = traza.data.spans
    assert raiz.attributes["error"] == "AudioProcessingError"
    assert "detalle-privado" not in traza.to_json()


def test_mlflow_caido_al_arrancar_se_reintenta(monkeypatch):
    """CA-169: si MLflow no respondió al principio, se vuelve a probar al minuto, sin reiniciar el proceso."""
    import sys
    from types import SimpleNamespace

    intentos, caido = [], [True]

    def set_experiment(nombre):
        intentos.append(nombre)
        if caido[0]:
            raise ConnectionError

    langchain = SimpleNamespace(autolog=lambda **_: None)  # el teardown lo apaga
    falso = SimpleNamespace(set_tracking_uri=lambda uri: None, set_experiment=set_experiment, langchain=langchain)
    reloj = [1000.0]
    monkeypatch.setitem(sys.modules, "mlflow", falso)
    monkeypatch.setenv("MLFLOW_ENABLED", "true")
    monkeypatch.setattr(observabilidad.time, "monotonic", lambda: reloj[0])

    assert observabilidad.cliente() is None
    caido[0] = False
    reloj[0] += observabilidad.REINTENTO_SEGUNDOS - 1
    assert observabilidad.cliente() is None
    assert len(intentos) == 1  # todavía no: no frena cada mensaje
    reloj[0] += 1
    assert observabilidad.cliente() is falso
    assert len(intentos) == 2
