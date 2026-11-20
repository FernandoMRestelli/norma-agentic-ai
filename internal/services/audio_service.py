"""Módulo de interpretación y transcripción de audios (Speech-to-Text).

Permite recibir notas de voz desde canales soportados (Telegram, WhatsApp) o datos directos,
transcribirlos a texto en lenguaje natural y normalizarlos para el pipeline del agente de Norma.
"""

import base64
import logging

import httpx

from internal.config import get_settings
from internal.schemas.order_schema import Channel

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 30.0
MAX_AUDIO_BYTES = 20 * 1024 * 1024  # 20 MB límite máximo de audio

PROMPT_TRANSCRIPCION = (
    "Transcribí fielmente este audio en español. "
    "El cliente es dueño o empleado de un salón de belleza o peluquería y está pidiendo productos a una distribuidora. "
    "Devolvé únicamente el texto transcripto de lo que dice la persona, sin comentarios adicionales ni explicaciones."
)


class AudioProcessingError(RuntimeError):
    """Error general al procesar o transcribir el audio."""


class AudioTooLongError(AudioProcessingError):
    """El audio supera el tamaño o duración máxima permitida."""


class AudioEmptyError(AudioProcessingError):
    """El audio está vacío o no contiene voz reconocible."""


def is_audio_enabled() -> bool:
    """Indica si el módulo de audio está habilitado por configuración."""
    return get_settings().audio_enabled


def download_audio_bytes(media_url: str, channel: Channel | None = None) -> tuple[bytes, str]:
    """Descarga los bytes del audio y determina su mime type según la referencia y canal.

    Devuelve: (bytes_del_audio, mime_type)
    """
    if media_url.startswith("data:"):
        # Formato data URI: data:audio/ogg;base64,...
        header, b64_data = media_url.split(",", 1)
        mime = header.split(";")[0].removeprefix("data:") or "audio/ogg"
        return base64.b64decode(b64_data), mime

    if media_url.startswith("mock:"):
        # Referencia para pruebas internas
        return b"MOCK_AUDIO_DATA", "audio/ogg"

    if media_url.startswith("telegram:"):
        from internal.services.telegram_service import TelegramError, get_client

        file_id = media_url.removeprefix("telegram:")
        client = get_client()
        try:
            file_info = client.get_file(file_id)
            file_path = file_info.get("file_path")
            if not file_path:
                raise AudioProcessingError("Telegram no devolvió la ruta del archivo de audio")
            raw_bytes = client.download_file(file_path, http_timeout=TIMEOUT_SECONDS)
        except TelegramError as exc:
            logger.warning("Fallo al descargar audio de Telegram: %s", exc)
            raise AudioProcessingError("No se pudo descargar el audio de Telegram") from None

        mime = "audio/ogg" if file_path.endswith((".oga", ".ogg")) else "audio/mpeg"
        return raw_bytes, mime

    raise NotImplementedError(f"Protocolo o canal de audio no soportado: {media_url}")


def transcribe_audio_bytes(
    audio_bytes: bytes,
    mime_type: str = "audio/ogg",
    http_client: httpx.Client | None = None,
) -> str:
    """Transcribe bytes de audio a texto utilizando el proveedor configurado."""
    if not audio_bytes:
        raise AudioEmptyError("El archivo de audio está vacío")

    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise AudioTooLongError(f"El audio excede el límite permitido de {MAX_AUDIO_BYTES} bytes")

    settings = get_settings()
    provider = settings.audio_provider or settings.llm_provider
    api_key = settings.audio_api_key or settings.llm_api_key
    model = settings.audio_model

    client = http_client or httpx.Client(timeout=TIMEOUT_SECONDS)

    try:
        match provider:
            case "gemini":
                return _transcribe_gemini(audio_bytes, mime_type, api_key, model, client)
            case "groq":
                return _transcribe_groq(audio_bytes, mime_type, api_key, model, client)
            case "openai":
                return _transcribe_openai(audio_bytes, mime_type, api_key, model, client)
            case "mock":
                return _transcribe_mock(audio_bytes)
            case None | "":
                raise AudioProcessingError(
                    "AUDIO_PROVIDER (o LLM_PROVIDER) no está configurado para transcripción de audio"
                )
            case other:
                raise AudioProcessingError(f"Proveedor de audio no soportado: {other}")
    finally:
        if http_client is None:
            client.close()


def _transcribe_gemini(
    audio_bytes: bytes,
    mime_type: str,
    api_key: str | None,
    model: str | None,
    client: httpx.Client,
) -> str:
    if not api_key:
        raise AudioProcessingError("Falta AUDIO_API_KEY o LLM_API_KEY para transcribir con Gemini")
    model_name = model or "gemini-3.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"

    b64_data = base64.b64encode(audio_bytes).decode("ascii")
    payload = {
        "contents": [
            {
                "parts": [
                    {"inlineData": {"mimeType": mime_type, "data": b64_data}},
                    {"text": PROMPT_TRANSCRIPCION},
                ]
            }
        ],
        "generationConfig": {"temperature": 0.0},
    }

    try:
        response = client.post(url, json=payload, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
        candidates = data.get("candidates", [])
        if not candidates:
            raise AudioEmptyError("Gemini no detectó contenido de voz en el audio")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(part.get("text", "") for part in parts).strip()
        if not text:
            raise AudioEmptyError("La transcripción del audio está vacía")
        return text
    except httpx.HTTPStatusError as exc:
        logger.warning("Error HTTP de Gemini al transcribir audio: %s", exc.response.status_code)
        raise AudioProcessingError(
            f"Falla en el servicio de transcripción de Gemini ({exc.response.status_code})"
        ) from None
    except httpx.HTTPError as exc:
        logger.warning("Error de red con Gemini al transcribir audio: %s", type(exc).__name__)
        raise AudioProcessingError(f"Error de conexión con Gemini ({type(exc).__name__})") from None


def _transcribe_groq(
    audio_bytes: bytes,
    mime_type: str,
    api_key: str | None,
    model: str | None,
    client: httpx.Client,
) -> str:
    if not api_key:
        raise AudioProcessingError("Falta AUDIO_API_KEY o LLM_API_KEY para transcribir con Groq")
    model_name = model or "whisper-large-v3"
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}
    files = {"file": ("audio.oga", audio_bytes, mime_type)}
    data = {"model": model_name, "language": "es"}

    try:
        response = client.post(url, headers=headers, files=files, data=data, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        text = response.json().get("text", "").strip()
        if not text:
            raise AudioEmptyError("Groq devolvió transcripción vacía")
        return text
    except httpx.HTTPError as exc:
        logger.warning("Error en transcripción con Groq: %s", type(exc).__name__)
        raise AudioProcessingError(f"Falla en transcripción con Groq ({type(exc).__name__})") from None


def _transcribe_openai(
    audio_bytes: bytes,
    mime_type: str,
    api_key: str | None,
    model: str | None,
    client: httpx.Client,
) -> str:
    if not api_key:
        raise AudioProcessingError("Falta AUDIO_API_KEY para transcribir con OpenAI")
    model_name = model or "whisper-1"
    url = "https://api.openai.com/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}
    files = {"file": ("audio.oga", audio_bytes, mime_type)}
    data = {"model": model_name, "language": "es"}

    try:
        response = client.post(url, headers=headers, files=files, data=data, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        text = response.json().get("text", "").strip()
        if not text:
            raise AudioEmptyError("OpenAI devolvió transcripción vacía")
        return text
    except httpx.HTTPError as exc:
        logger.warning("Error en transcripción con OpenAI: %s", type(exc).__name__)
        raise AudioProcessingError(f"Falla en transcripción con OpenAI ({type(exc).__name__})") from None


def _transcribe_mock(audio_bytes: bytes) -> str:
    if audio_bytes.startswith(b"mock_text:"):
        return audio_bytes.removeprefix(b"mock_text:").decode("utf-8")
    return "Hola, necesito dos shampoo neutro de litro y un oxidante de 20."


def transcribe_audio(media_url: str, channel: Channel | None = None) -> str:
    """Descarga y transcribe un audio referenciado por `media_url`."""
    audio_bytes, mime_type = download_audio_bytes(media_url, channel)
    return transcribe_audio_bytes(audio_bytes, mime_type)
