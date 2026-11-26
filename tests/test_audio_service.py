import logging
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from internal.agent import graph, guardrails
from internal.agent.norma_agent import NormaAgent
from internal.config import get_settings
from internal.database.connection import SessionLocal
from internal.database.models import ConversationRecord, DraftRecord
from internal.schemas.catalog_schema import Product
from internal.schemas.order_schema import Channel, DraftStatus, IncomingMessage, MessageType, OrderDraft, OrderItem
from internal.services import audio_service, conversation_service, message_service, order_service, sheets_service


@pytest.fixture(autouse=True)
def _limpiar_estado():
    """Limpia cache de settings y el limitador de ráfaga antes y después de cada test."""
    get_settings.cache_clear()
    guardrails.limitador.reiniciar()
    yield
    get_settings.cache_clear()
    guardrails.limitador.reiniciar()


def test_audio_deshabilitado_por_defecto():
    """Por defecto AUDIO_ENABLED=false y el bot responde con WRITE_IT_PLEASE."""
    msg = IncomingMessage(
        customer_id="telegram:test", channel=Channel.TELEGRAM, type=MessageType.AUDIO, media_url="telegram:aud1"
    )
    reply = conversation_service.handle_message(msg)
    assert reply.text == conversation_service.WRITE_IT_PLEASE


def test_protocolo_no_soportado_no_filtra_url_completa():
    """RNF-14: URLs no soportadas (que pueden traer tokens firmados) no se incluyen completas en el error."""
    with pytest.raises(audio_service.AudioProcessingError) as exc_info:
        audio_service.download_audio_bytes("https://media.example.com/audio.ogg?token=SECRETO_123")
    assert "SECRETO_123" not in str(exc_info.value)
    assert "(https)" in str(exc_info.value)


def test_falta_telegram_token_envuelve_en_audio_processing_error(monkeypatch):
    """Si falta TELEGRAM_BOT_TOKEN, get_client() lanza ValueError y se envuelve en AudioProcessingError."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    get_settings.cache_clear()

    with pytest.raises(audio_service.AudioProcessingError, match="descargar el audio"):
        audio_service.download_audio_bytes("telegram:aud1")


def test_telegram_getfile_file_is_too_big_lanza_archivo_muy_grande():
    """Telegram devuelve ok:false con 'file is too big' para >20 MB y se mapea a TelegramArchivoMuyGrande."""
    from internal.services import telegram_service

    mock_http = Mock(spec=httpx.Client)
    mock_response = Mock()
    mock_response.status_code = 400
    mock_response.json.return_value = {"ok": False, "description": "Bad Request: file is too big"}
    mock_http.post.return_value = mock_response

    client = telegram_service.TelegramClient("fake-token", http=mock_http)
    with pytest.raises(telegram_service.TelegramArchivoMuyGrande):
        client.download_file_with_path("aud-big")


def test_protocolo_mock_no_esta_soportado():
    """El esquema mock: no existe en el camino de producción."""
    with pytest.raises(audio_service.AudioProcessingError, match="no soportado"):
        audio_service.download_audio_bytes("mock:dummy")


def test_proveedor_mock_no_esta_soportado(monkeypatch):
    """AUDIO_PROVIDER=mock no inventa pedidos en producción: falla como proveedor no soportado."""
    monkeypatch.setenv("AUDIO_PROVIDER", "mock")
    get_settings.cache_clear()

    with pytest.raises(audio_service.AudioProcessingError, match="no soportado"):
        audio_service.transcribe_audio_bytes(b"RAW_AUDIO", "audio/ogg")


def test_download_audio_telegram(monkeypatch):
    """Descarga de nota de voz desde Telegram usando download_file_with_path."""
    from internal.services import telegram_service

    mock_client = Mock()
    mock_client.download_file_with_path.return_value = (b"TELEGRAM_AUDIO_BYTES", "voice/file_1.oga")
    monkeypatch.setattr(telegram_service, "get_client", lambda: mock_client)

    data, mime = audio_service.download_audio_bytes("telegram:aud123")
    assert data == b"TELEGRAM_AUDIO_BYTES"
    assert mime == "audio/ogg"
    mock_client.download_file_with_path.assert_called_once_with("aud123")


def test_download_audio_telegram_mp3(monkeypatch):
    """Descarga de audio mp3 desde Telegram detecta el mime correcto."""
    from internal.services import telegram_service

    mock_client = Mock()
    mock_client.download_file_with_path.return_value = (b"MP3_BYTES", "music/audio_pedido.mp3")
    monkeypatch.setattr(telegram_service, "get_client", lambda: mock_client)

    data, mime = audio_service.download_audio_bytes("telegram:aud456")
    assert data == b"MP3_BYTES"
    assert mime == "audio/mpeg"


def test_transcribe_audio_vacio_falla():
    """Audio vacío genera AudioEmptyError."""
    with pytest.raises(audio_service.AudioEmptyError):
        audio_service.transcribe_audio_bytes(b"")


def test_transcribe_audio_demasiado_largo_falla(monkeypatch):
    """Audio que excede MAX_AUDIO_BYTES genera AudioTooLongError."""
    monkeypatch.setattr(audio_service, "MAX_AUDIO_BYTES", 10)
    with pytest.raises(audio_service.AudioTooLongError):
        audio_service.transcribe_audio_bytes(b"123456789012345")


def test_transcribe_gemini_exitoso(monkeypatch):
    """Llamada exitosa a la API de Gemini (enviando API key por header, no en la URL)."""
    monkeypatch.setenv("AUDIO_PROVIDER", "gemini")
    monkeypatch.setenv("AUDIO_API_KEY", "test-key-gemini")
    monkeypatch.setenv("AUDIO_MODEL", "gemini-3.5-flash")
    get_settings.cache_clear()

    mock_http = Mock(spec=httpx.Client)
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "Hola Norma, anotame dos oxidantes de 20 y tres tinturas 7.1"}]
                }
            }
        ]
    }
    mock_http.post.return_value = mock_response

    res = audio_service.transcribe_audio_bytes(b"RAW_AUDIO", "audio/ogg", http_client=mock_http)
    assert res == "Hola Norma, anotame dos oxidantes de 20 y tres tinturas 7.1"
    assert mock_http.post.call_count == 1
    call_args, call_kwargs = mock_http.post.call_args
    # RNF-14: La key va por header, nunca en la URL
    assert "key=" not in call_args[0]
    assert call_kwargs["headers"]["x-goog-api-key"] == "test-key-gemini"
    assert "gemini-3.5-flash" in call_args[0]
    assert call_kwargs["json"]["contents"][0]["parts"][0]["inlineData"]["mimeType"] == "audio/ogg"


def test_transcribe_gemini_respuesta_no_dict_falla_controladamente(monkeypatch):
    """Si Gemini devuelve una lista JSON en vez de un objeto, se mapea a AudioProcessingError (no AttributeError)."""
    monkeypatch.setenv("AUDIO_PROVIDER", "gemini")
    monkeypatch.setenv("AUDIO_API_KEY", "test-key-gemini")
    get_settings.cache_clear()

    mock_http = Mock(spec=httpx.Client)
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = [{"error": "unexpected"}]
    mock_http.post.return_value = mock_response

    with pytest.raises(audio_service.AudioProcessingError, match="inesperada"):
        audio_service.transcribe_audio_bytes(b"RAW_AUDIO", "audio/ogg", http_client=mock_http)


def test_transcribe_gemini_no_filtra_llm_api_key_de_otro_proveedor(monkeypatch):
    """Si AUDIO_PROVIDER=gemini y falta AUDIO_API_KEY, no usa LLM_API_KEY de OpenRouter (RNF-01)."""
    monkeypatch.setenv("AUDIO_PROVIDER", "gemini")
    monkeypatch.delenv("AUDIO_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLM_API_KEY", "sk-openrouter-secret")
    get_settings.cache_clear()

    with pytest.raises(audio_service.AudioProcessingError, match="Falta AUDIO_API_KEY"):
        audio_service.transcribe_audio_bytes(b"RAW_AUDIO", "audio/ogg")


def test_audio_provider_vacio_no_cae_en_openrouter_y_avisa_al_arrancar(monkeypatch, caplog):
    """Si AUDIO_PROVIDER está vacío y LLM_PROVIDER=openrouter, no cae en openrouter y avisa en startup."""
    monkeypatch.setenv("AUDIO_ENABLED", "true")
    monkeypatch.delenv("AUDIO_PROVIDER", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLM_API_KEY", "sk-openrouter")
    get_settings.cache_clear()

    with caplog.at_level(logging.WARNING, logger="internal.services.audio_service"):
        audio_service.check_audio_config()
    assert any("AUDIO_PROVIDER no es un proveedor soportado" in r.message for r in caplog.records)

    with pytest.raises(audio_service.AudioProcessingError, match="AUDIO_PROVIDER no está configurado"):
        audio_service.transcribe_audio_bytes(b"RAW_AUDIO", "audio/ogg")


def test_check_audio_config_avisa_si_falta_api_key(monkeypatch, caplog):
    """Si AUDIO_ENABLED=true con proveedor válido pero sin clave, startup deja un warning."""
    monkeypatch.setenv("AUDIO_ENABLED", "true")
    monkeypatch.setenv("AUDIO_PROVIDER", "gemini")
    monkeypatch.delenv("AUDIO_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    get_settings.cache_clear()

    with caplog.at_level(logging.WARNING, logger="internal.services.audio_service"):
        audio_service.check_audio_config()
    assert any("falta AUDIO_API_KEY" in r.message for r in caplog.records)


def test_transcribe_groq_exitoso(monkeypatch):
    """Llamada exitosa a la API de Groq Whisper."""
    monkeypatch.setenv("AUDIO_PROVIDER", "groq")
    monkeypatch.setenv("AUDIO_API_KEY", "test-key-groq")
    monkeypatch.setenv("AUDIO_MODEL", "whisper-large-v3")
    get_settings.cache_clear()

    mock_http = Mock(spec=httpx.Client)
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"text": "Quiero 2 shampoo de litro"}
    mock_http.post.return_value = mock_response

    res = audio_service.transcribe_audio_bytes(b"RAW_AUDIO", "audio/mpeg", http_client=mock_http)
    assert res == "Quiero 2 shampoo de litro"
    assert mock_http.post.call_count == 1
    call_kwargs = mock_http.post.call_args.kwargs
    assert call_kwargs["data"]["model"] == "whisper-large-v3"
    # El archivo para audio/mpeg debe tener extensión adecuada
    assert call_kwargs["files"]["file"][0] == "audio.mp3"


def test_transcribe_openai_exitoso(monkeypatch):
    """Llamada exitosa a la API de OpenAI Whisper (audio/ogg envía extensión .ogg, no .oga)."""
    monkeypatch.setenv("AUDIO_PROVIDER", "openai")
    monkeypatch.setenv("AUDIO_API_KEY", "test-key-openai")
    get_settings.cache_clear()

    mock_http = Mock(spec=httpx.Client)
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"text": "Anotame 1 baño de crema"}
    mock_http.post.return_value = mock_response

    res = audio_service.transcribe_audio_bytes(b"RAW_AUDIO", "audio/ogg", http_client=mock_http)
    assert res == "Anotame 1 baño de crema"
    assert mock_http.post.call_args.kwargs["files"]["file"][0] == "audio.ogg"


def test_whisper_text_null_y_gemini_part_no_str_lanzan_audio_empty(monkeypatch):
    """Respuestas con `text: null` en Whisper o `part.text` no-string en Gemini dan AudioEmptyError, no crash."""
    mock_http = Mock(spec=httpx.Client)
    mock_response = Mock()
    mock_response.status_code = 200
    mock_http.post.return_value = mock_response

    monkeypatch.setenv("AUDIO_PROVIDER", "groq")
    monkeypatch.setenv("AUDIO_API_KEY", "test-key-groq")
    get_settings.cache_clear()
    mock_response.json.return_value = {"text": None}
    with pytest.raises(audio_service.AudioEmptyError):
        audio_service.transcribe_audio_bytes(b"RAW_AUDIO", "audio/ogg", http_client=mock_http)

    monkeypatch.setenv("AUDIO_PROVIDER", "gemini")
    monkeypatch.setenv("AUDIO_API_KEY", "test-key-gemini")
    get_settings.cache_clear()
    mock_response.json.return_value = {"candidates": [{"content": {"parts": [{"text": None}]}}]}
    with pytest.raises(audio_service.AudioEmptyError):
        audio_service.transcribe_audio_bytes(b"RAW_AUDIO", "audio/ogg", http_client=mock_http)

    mock_response.json.return_value = {"candidates": [{"content": {"parts": None}}]}
    with pytest.raises(audio_service.AudioEmptyError):
        audio_service.transcribe_audio_bytes(b"RAW_AUDIO", "audio/ogg", http_client=mock_http)


def test_llm_provider_con_mayusculas_se_normaliza_para_respaldo(monkeypatch):
    """LLM_PROVIDER con mayúsculas (ej. Gemini) se normaliza y funciona como respaldo de proveedor y clave."""
    monkeypatch.delenv("AUDIO_PROVIDER", raising=False)
    monkeypatch.delenv("AUDIO_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "Gemini")
    monkeypatch.setenv("LLM_API_KEY", "key-gemini-respaldo")
    get_settings.cache_clear()

    provider, api_key = audio_service._resolve_provider_and_key()
    assert provider == "gemini"
    assert api_key == "key-gemini-respaldo"


def test_imagen_con_palabra_clave_no_confirma_ni_cancela_sin_llm():
    """`sin_marca_audio` conserva `[imagen leída]`: una foto con «Dale» o «Cancelado» nunca dispara atajos."""
    from internal.services.palabras_clave import interpretar_palabra_clave

    assert message_service.sin_marca_audio("[imagen leída]\nDale") == "[imagen leída]\nDale"
    assert interpretar_palabra_clave(message_service.sin_marca_audio("[imagen leída]\nDale")) is None
    assert interpretar_palabra_clave(message_service.sin_marca_audio("[imagen leída]\nCancelado")) is None


def test_transcribe_error_json_no_corta_excepcion_cruda(monkeypatch):
    """Si el proveedor devuelve respuesta no JSON (ValueError), se mapea a AudioProcessingError."""
    monkeypatch.setenv("AUDIO_PROVIDER", "groq")
    monkeypatch.setenv("AUDIO_API_KEY", "test-key-groq")
    get_settings.cache_clear()

    mock_http = Mock(spec=httpx.Client)
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.side_effect = ValueError("No JSON")
    mock_http.post.return_value = mock_response

    with pytest.raises(audio_service.AudioProcessingError):
        audio_service.transcribe_audio_bytes(b"RAW_AUDIO", "audio/ogg", http_client=mock_http)


def test_handle_message_audio_habilitado_invoca_agente_con_origen_audio(monkeypatch, base_de_datos_inicializada):
    """Con AUDIO_ENABLED=true, transcribe e invoca al agente pasando origen='audio' y la marca solo al agente."""
    from internal.services import telegram_service

    mock_client = Mock()
    mock_client.download_file_with_path.return_value = (b"RAW_OGG", "voice/test.oga")
    monkeypatch.setattr(telegram_service, "get_client", lambda: mock_client)
    monkeypatch.setattr(audio_service, "transcribe_audio_bytes", lambda *_a, **_kw: "dos shampoo neutro de litro")

    monkeypatch.setenv("AUDIO_ENABLED", "true")
    get_settings.cache_clear()

    llamadas_agente = []

    class MockAgent:
        def handle(self, **kwargs):
            llamadas_agente.append(kwargs)
            return SimpleNamespace(cerrada=False, kind="propuesta", message="Te armé la propuesta con 2 shampoo.")

    monkeypatch.setattr(conversation_service, "get_agent", lambda: MockAgent())

    msg = IncomingMessage(
        customer_id="telegram:cliente-audio",
        channel=Channel.TELEGRAM,
        type=MessageType.AUDIO,
        media_url="telegram:aud1",
    )
    reply = conversation_service.handle_message(msg)

    assert reply.text == "Te armé la propuesta con 2 shampoo."
    assert len(llamadas_agente) == 1
    assert llamadas_agente[0]["origen"] == "audio"
    assert llamadas_agente[0]["text"] == "[audio transcripto]\ndos shampoo neutro de litro"


def test_audio_cancelalo_con_propuesta_descarta_sin_llm(monkeypatch, base_de_datos_inicializada):
    """Un audio «cancelalo» con propuesta pendiente descarta el borrador sin llamar al agente."""
    monkeypatch.setenv("AUDIO_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(audio_service, "transcribe_audio", lambda _url: "cancelalo")

    with SessionLocal() as session:
        conv = ConversationRecord(customer_id="telegram:audio-cancel", channel="telegram", status="abierta")
        session.add(conv)
        session.flush()
        draft = DraftRecord(
            draft_id="draft-audio-cancel-1",
            version=1,
            conversation_id=conv.id,
            customer_id="telegram:audio-cancel",
            channel="telegram",
            items=[{"sku": "SH-1", "product_name": "Shampoo", "quantity": 2}],
            status=DraftStatus.PENDING_CONFIRMATION.value,
        )
        session.add(draft)
        session.commit()

    def _no_llamar_agente():
        raise AssertionError("No debería llamar al agente en una cancelación determinística por audio")

    monkeypatch.setattr(conversation_service, "get_agent", _no_llamar_agente)

    msg = IncomingMessage(
        customer_id="telegram:audio-cancel",
        channel=Channel.TELEGRAM,
        type=MessageType.AUDIO,
        media_url="telegram:aud-cancel",
    )
    reply = conversation_service.handle_message(msg)
    assert reply.text == conversation_service.DRAFT_DISCARDED_MESSAGE


def test_audio_si_con_propuesta_confirma_sin_llm(monkeypatch, base_de_datos_inicializada):
    """Un audio «sí» tras una propuesta vigente confirma la orden por el atajo sin llamar al LLM."""
    monkeypatch.setenv("AUDIO_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(
        sheets_service,
        "get_catalog",
        lambda: [Product(sku="SH-AUD-1", name="Shampoo Neutro", presentation="1L", price=Decimal("5000"), stock=10)],
    )
    monkeypatch.setattr(sheets_service, "append_order", lambda _o: None)

    respuestas = iter(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "leer_skill", "args": {"nombre": "tomar_pedido"}, "id": "c1"},
                    {"name": "search_products", "args": {"query": "shampoo neutro 1L"}, "id": "c1b"},
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "respond_to_customer",
                        "args": {
                            "kind": "propuesta",
                            "message": "Te armé 2 Shampoo Neutro 1L. ¿Lo confirmo?",
                            "propuesta": [{"sku": "SH-AUD-1", "quantity": 2}],
                        },
                        "id": "c2",
                    }
                ],
            ),
        ]
    )
    llamadas_llm = []

    class _Modelo:
        def bind_tools(self, *_a, **_kw):
            return self

        def invoke(self, messages):
            llamadas_llm.append(messages)
            return next(respuestas)

    agent = NormaAgent(model=_Modelo(), checkpointer=InMemorySaver())
    monkeypatch.setattr(conversation_service, "get_agent", lambda: agent)

    # Turno 1: el cliente pide por texto y recibe propuesta
    r1 = conversation_service.handle_message(
        IncomingMessage(customer_id="telegram:audio-si", channel=Channel.TELEGRAM, text="2 shampoo neutro 1L")
    )
    assert "Shampoo Neutro" in r1.text
    llamadas_antes = len(llamadas_llm)

    # Turno 2: el cliente confirma diciendo «sí» en un audio -> confirma sin volver a llamar al LLM
    monkeypatch.setattr(audio_service, "transcribe_audio", lambda _url: "sí")
    r2 = conversation_service.handle_message(
        IncomingMessage(
            customer_id="telegram:audio-si",
            channel=Channel.TELEGRAM,
            type=MessageType.AUDIO,
            media_url="telegram:aud-si",
        )
    )
    assert len(llamadas_llm) == llamadas_antes
    assert "ORD-" in r2.text
    # Y la orden quedó guardada con el producto pedido
    ordenes = [o for o in order_service.list_orders() if o.customer_id == "telegram:audio-si"]
    assert len(ordenes) == 1
    assert ordenes[0].items[0].sku == "SH-AUD-1"


@pytest.mark.parametrize(
    ("texto_audio", "hay_propuesta", "esperada"),
    [
        ("¿cómo viene mi pedido?", False, "informar"),
        ("hola, buen día", False, "informar"),
        ("sí, dale", True, "confirmar_pedido"),
    ],
)
def test_audio_ruteo_skill_clara_ignora_marca_de_origen(texto_audio, hay_propuesta, esperada):
    """La marca `[audio transcripto]` no altera `skill_clara` ni `encaminar`."""
    from langchain_core.messages import HumanMessage

    marcado = message_service.marcar_origen(texto_audio, MessageType.AUDIO)
    propuesta = (
        OrderDraft(
            draft_id="d1",
            customer_id="telegram:1",
            channel=Channel.TELEGRAM,
            items=[OrderItem(sku="SH-1", product_name="Shampoo", quantity=1)],
        ).model_dump(mode="json")
        if hay_propuesta
        else None
    )
    estado = {"messages": [HumanMessage(content=marcado)], "inicio_turno": 0, "propuesta": propuesta}
    resultado = graph.encaminar(estado)
    assert resultado["skill_preactivada"] == esperada


def test_rafaga_de_audios_frena_antes_de_transcribir(monkeypatch):
    """El tope de ráfaga frena antes de llamar a `prepare`/transcripción, ahorrando llamadas pagas."""
    monkeypatch.setenv("AUDIO_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(guardrails, "limitador", guardrails.Limitador(maximo=2))

    transcripciones = []
    monkeypatch.setattr(
        audio_service,
        "transcribe_audio",
        lambda url: (transcripciones.append(url), "hola")[1],
    )
    monkeypatch.setattr(
        conversation_service,
        "get_agent",
        lambda: SimpleNamespace(
            handle=lambda **_kw: SimpleNamespace(cerrada=False, kind="informacion", message="¡Hola!")
        ),
    )

    msg = IncomingMessage(
        customer_id="telegram:rafaga-audio",
        channel=Channel.TELEGRAM,
        type=MessageType.AUDIO,
        media_url="telegram:aud",
    )
    conversation_service.handle_message(msg)
    conversation_service.handle_message(msg)
    tercero = conversation_service.handle_message(msg)

    assert tercero.text == guardrails.MENSAJE_RAFAGA
    assert len(transcripciones) == 2


def test_telegram_archivo_muy_grande_devuelve_audio_too_long(monkeypatch):
    """Un audio > 20 MB (TelegramArchivoMuyGrande) se mapea a AudioTooLongError sin depender del texto del error."""
    from internal.services import telegram_service

    mock_client = Mock()
    mock_client.download_file_with_path.side_effect = telegram_service.TelegramArchivoMuyGrande("límite superado")
    monkeypatch.setattr(telegram_service, "get_client", lambda: mock_client)
    monkeypatch.setenv("AUDIO_ENABLED", "true")
    get_settings.cache_clear()

    msg = IncomingMessage(
        customer_id="telegram:1", channel=Channel.TELEGRAM, type=MessageType.AUDIO, media_url="telegram:grande"
    )
    assert conversation_service.handle_message(msg).text == conversation_service.AUDIO_TOO_LONG_MESSAGE


def test_gemini_rechaza_audio_que_excede_limite_base64(monkeypatch):
    """Con Gemini, audios > MAX_GEMINI_INLINE_BYTES (14 MB) fallan con AudioTooLongError antes de enviar base64."""
    monkeypatch.setenv("AUDIO_PROVIDER", "gemini")
    monkeypatch.setenv("AUDIO_API_KEY", "test-key-gemini")
    monkeypatch.setattr(audio_service, "MAX_GEMINI_INLINE_BYTES", 10)
    get_settings.cache_clear()

    with pytest.raises(audio_service.AudioTooLongError):
        audio_service.transcribe_audio_bytes(b"123456789012345", "audio/ogg")


def test_canal_o_protocolo_no_soportado_devuelve_audio_processing_error(monkeypatch):
    """Una media_url de un protocolo aún no soportado (ej. https) lanza AudioProcessingError y no corta el turno."""
    monkeypatch.setenv("AUDIO_ENABLED", "true")
    get_settings.cache_clear()

    msg = IncomingMessage(
        customer_id="whatsapp:1",
        channel=Channel.WHATSAPP,
        type=MessageType.AUDIO,
        media_url="https://example.com/audio.ogg",
    )
    assert conversation_service.handle_message(msg).text == conversation_service.AUDIO_PROCESSING_ERROR_MESSAGE


def test_handle_message_errores_audio_devuelven_mensajes_amigables(monkeypatch):
    """Errores de audio generan respuestas cordiales sin romper el bot."""
    monkeypatch.setenv("AUDIO_ENABLED", "true")
    get_settings.cache_clear()

    # Caso 1: audio inaudible / vacío
    monkeypatch.setattr(message_service, "preparar", Mock(side_effect=audio_service.AudioEmptyError()))
    msg = IncomingMessage(
        customer_id="telegram:1", channel=Channel.TELEGRAM, type=MessageType.AUDIO, media_url="telegram:aud1"
    )
    assert conversation_service.handle_message(msg).text == conversation_service.AUDIO_NOT_CLEAR_MESSAGE

    # Caso 2: audio demasiado largo
    monkeypatch.setattr(message_service, "preparar", Mock(side_effect=audio_service.AudioTooLongError()))
    assert conversation_service.handle_message(msg).text == conversation_service.AUDIO_TOO_LONG_MESSAGE

    # Caso 3: falla de servicio/proveedor
    monkeypatch.setattr(message_service, "preparar", Mock(side_effect=audio_service.AudioProcessingError("API error")))
    assert conversation_service.handle_message(msg).text == conversation_service.AUDIO_PROCESSING_ERROR_MESSAGE
