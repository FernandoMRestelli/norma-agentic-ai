import base64
from unittest.mock import Mock

import httpx
import pytest

from internal.config import get_settings
from internal.schemas.order_schema import Channel, IncomingMessage, MessageType
from internal.services import audio_service, conversation_service, message_service


@pytest.fixture(autouse=True)
def _limpiar_cache_settings():
    """Limpia el cache de settings antes y después de cada test para no contaminar la suite."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_audio_deshabilitado_por_defecto():
    """Por defecto AUDIO_ENABLED=false y el bot responde con WRITE_IT_PLEASE."""
    msg = IncomingMessage(
        customer_id="telegram:test", channel=Channel.TELEGRAM, type=MessageType.AUDIO, media_url="telegram:aud1"
    )
    reply = conversation_service.handle_message(msg)
    assert reply.text == conversation_service.WRITE_IT_PLEASE


def test_download_audio_bytes_data_uri():
    """Descarga de audio vía data URI en base64."""
    raw = b"OggS_TEST_AUDIO_BYTES"
    b64 = base64.b64encode(raw).decode("ascii")
    uri = f"data:audio/ogg;base64,{b64}"

    data, mime = audio_service.download_audio_bytes(uri)
    assert data == raw
    assert mime == "audio/ogg"


def test_download_audio_bytes_mock():
    """Descarga con referencia mock para tests."""
    data, mime = audio_service.download_audio_bytes("mock:dummy")
    assert data == b"MOCK_AUDIO_DATA"
    assert mime == "audio/ogg"


def test_download_audio_telegram(monkeypatch):
    """Descarga de nota de voz desde Telegram usando download_file_with_path."""
    from internal.services import telegram_service

    mock_client = Mock()
    mock_client.download_file_with_path.return_value = (b"TELEGRAM_AUDIO_BYTES", "voice/file_1.oga")
    monkeypatch.setattr(telegram_service, "get_client", lambda: mock_client)

    data, mime = audio_service.download_audio_bytes("telegram:aud123", Channel.TELEGRAM)
    assert data == b"TELEGRAM_AUDIO_BYTES"
    assert mime == "audio/ogg"
    mock_client.download_file_with_path.assert_called_once_with("aud123")


def test_download_audio_telegram_mp3(monkeypatch):
    """Descarga de audio mp3 desde Telegram detecta el mime correcto."""
    from internal.services import telegram_service

    mock_client = Mock()
    mock_client.download_file_with_path.return_value = (b"MP3_BYTES", "music/audio_pedido.mp3")
    monkeypatch.setattr(telegram_service, "get_client", lambda: mock_client)

    data, mime = audio_service.download_audio_bytes("telegram:aud456", Channel.TELEGRAM)
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


def test_transcribe_mock_provider(monkeypatch):
    """Proveedor mock devuelve texto de prueba o prefijado."""
    monkeypatch.setenv("AUDIO_PROVIDER", "mock")
    get_settings.cache_clear()

    texto = audio_service.transcribe_audio_bytes(b"mock_text:mandame 3 tinturas 8.1")
    assert texto == "mandame 3 tinturas 8.1"

    texto_defecto = audio_service.transcribe_audio_bytes(b"cualquier_cosa")
    assert "dos shampoo neutro" in texto_defecto


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


def test_transcribe_gemini_no_filtra_llm_api_key_de_otro_proveedor(monkeypatch):
    """Si AUDIO_PROVIDER=gemini y falta AUDIO_API_KEY, no usa LLM_API_KEY de OpenRouter (RNF-01)."""
    monkeypatch.setenv("AUDIO_PROVIDER", "gemini")
    monkeypatch.delenv("AUDIO_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLM_API_KEY", "sk-openrouter-secret")
    get_settings.cache_clear()

    with pytest.raises(audio_service.AudioProcessingError, match="Falta AUDIO_API_KEY"):
        audio_service.transcribe_audio_bytes(b"RAW_AUDIO", "audio/ogg")


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
    """Llamada exitosa a la API de OpenAI Whisper."""
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
    """Con AUDIO_ENABLED=true, transcribe e invoca al agente pasando origen='audio'."""
    from internal.services import telegram_service

    mock_client = Mock()
    mock_client.download_file_with_path.return_value = (
        b"mock_text:dos shampoo neutro de litro",
        "voice/test.oga",
    )
    monkeypatch.setattr(telegram_service, "get_client", lambda: mock_client)

    monkeypatch.setenv("AUDIO_ENABLED", "true")
    monkeypatch.setenv("AUDIO_PROVIDER", "mock")
    get_settings.cache_clear()

    llamadas_agente = []

    class MockAgent:
        def handle(self, **kwargs):
            llamadas_agente.append(kwargs)
            from types import SimpleNamespace

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
    assert "dos shampoo neutro" in llamadas_agente[0]["text"]


def test_handle_message_errores_audio_devuelven_mensajes_amigables(monkeypatch):
    """Errores de audio generan respuestas cordiales sin romper el bot."""
    monkeypatch.setenv("AUDIO_ENABLED", "true")
    get_settings.cache_clear()

    # Caso 1: audio inaudible / vacío
    monkeypatch.setattr(message_service, "prepare", Mock(side_effect=audio_service.AudioEmptyError()))
    msg = IncomingMessage(
        customer_id="telegram:1", channel=Channel.TELEGRAM, type=MessageType.AUDIO, media_url="telegram:aud1"
    )
    assert conversation_service.handle_message(msg).text == conversation_service.AUDIO_NOT_CLEAR_MESSAGE

    # Caso 2: audio demasiado largo
    monkeypatch.setattr(message_service, "prepare", Mock(side_effect=audio_service.AudioTooLongError()))
    assert conversation_service.handle_message(msg).text == conversation_service.AUDIO_TOO_LONG_MESSAGE

    # Caso 3: falla de servicio/proveedor
    monkeypatch.setattr(message_service, "prepare", Mock(side_effect=audio_service.AudioProcessingError("API error")))
    assert conversation_service.handle_message(msg).text == conversation_service.AUDIO_PROCESSING_ERROR_MESSAGE
