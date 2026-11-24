"""Módulo de interpretación y transcripción de audios (Speech-to-Text).

Permite recibir notas de voz desde canales soportados (Telegram),
transcribirlos a texto en lenguaje natural y pasarlos al pipeline del agente de Norma.
"""

import base64
import logging
import mimetypes
from pathlib import Path

import httpx

from internal.config import get_settings

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 30.0
MAX_AUDIO_BYTES = 20 * 1024 * 1024  # 20 MB límite máximo de audio
# Gemini limita el request completo a 20 MB y base64 suma ~33 %: 14 MB crudos entran holgados en JSON
MAX_GEMINI_INLINE_BYTES = 14 * 1024 * 1024

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
SUPPORTED_AUDIO_PROVIDERS = frozenset({"gemini", "groq", "openai"})

_WHISPER_ENDPOINTS: dict[str, tuple[str, str, str]] = {
    "groq": ("Groq", "https://api.groq.com/openai/v1/audio/transcriptions", "whisper-large-v3"),
    "openai": ("OpenAI", "https://api.openai.com/v1/audio/transcriptions", "whisper-1"),
}

PROMPT_TRANSCRIPCION = (
    "Transcribí fielmente este audio en español. "
    "El cliente es dueño o empleado de un salón de belleza o peluquería y está pidiendo productos a una distribuidora. "
    "Devolvé únicamente el texto transcripto de lo que dice la persona, sin comentarios adicionales ni explicaciones."
)

_EXTENSION_BY_MIME: dict[str, str] = {
    "audio/ogg": "audio.ogg",
    "audio/opus": "audio.opus",
    "audio/mpeg": "audio.mp3",
    "audio/mp3": "audio.mp3",
    "audio/mp4": "audio.m4a",
    "audio/m4a": "audio.m4a",
    "audio/x-m4a": "audio.m4a",
    "audio/aac": "audio.aac",
    "audio/wav": "audio.wav",
    "audio/x-wav": "audio.wav",
    "audio/webm": "audio.webm",
}


def _detect_mime(file_path: str) -> str:
    """Determina el mime type a partir de la ruta o extensión."""
    ext = Path(file_path).suffix.lower()
    if ext in (".oga", ".ogg", ".opus"):
        return "audio/ogg"
    if ext == ".mp3":
        return "audio/mpeg"
    if ext == ".aac":
        return "audio/aac"
    if ext == ".m4a":
        return "audio/m4a"
    if ext == ".mp4":
        return "audio/mp4"
    if ext == ".wav":
        return "audio/wav"
    return mimetypes.guess_type(file_path)[0] or "audio/ogg"


class AudioProcessingError(RuntimeError):
    """Error general al procesar o transcribir el audio."""


class AudioTooLongError(AudioProcessingError):
    """El audio supera el tamaño máximo permitido."""


class AudioEmptyError(AudioProcessingError):
    """El audio está vacío o no contiene voz reconocible."""


def is_audio_enabled() -> bool:
    """Indica si el módulo de audio está habilitado por configuración."""
    return get_settings().audio_enabled


def _resolve_provider_and_key() -> tuple[str | None, str | None]:
    """Resuelve el proveedor de audio y su API key sin caer en un LLM_PROVIDER no soportado."""
    settings = get_settings()
    llm_provider = (settings.llm_provider or "").strip().lower() or None
    if settings.audio_provider:
        provider = settings.audio_provider.strip().lower()
    elif llm_provider in SUPPORTED_AUDIO_PROVIDERS:
        provider = llm_provider
    else:
        provider = None

    api_key = settings.audio_api_key
    # Seguridad (RNF-01): solo usar LLM_API_KEY como respaldo si coincide el proveedor
    if not api_key and provider and provider == llm_provider:
        api_key = settings.llm_api_key
    return provider, api_key


def proveedor_y_modelo() -> tuple[str | None, str | None]:
    """Con qué se transcribe, para las trazas."""
    provider, _ = _resolve_provider_and_key()
    default_model = _WHISPER_ENDPOINTS[provider][2] if provider in _WHISPER_ENDPOINTS else DEFAULT_GEMINI_MODEL
    return provider, get_settings().audio_model or (default_model if provider else None)


def check_audio_config() -> None:
    """Avisa al arrancar si AUDIO_ENABLED=true pero falta proveedor soportado o clave."""
    if not is_audio_enabled():
        return
    provider, api_key = _resolve_provider_and_key()
    if not provider or provider not in SUPPORTED_AUDIO_PROVIDERS:
        logger.warning(
            "AUDIO_ENABLED=true pero AUDIO_PROVIDER no es un proveedor soportado (%s); "
            "los audios fallarán hasta configurar gemini, groq u openai",
            provider or "vacío",
        )
    elif not api_key:
        logger.warning(
            "AUDIO_ENABLED=true con proveedor %s pero falta AUDIO_API_KEY; los audios fallarán",
            provider,
        )


def download_audio_bytes(media_url: str) -> tuple[bytes, str]:
    """Descarga los bytes del audio y determina su mime type según la referencia.

    Devuelve: (bytes_del_audio, mime_type)
    """
    if media_url.startswith("telegram:"):
        from internal.services.telegram_service import TelegramArchivoMuyGrande, TelegramError, get_client

        file_id = media_url.removeprefix("telegram:")
        try:
            client = get_client()
            raw_bytes, file_path = client.download_file_with_path(file_id)
        except TelegramArchivoMuyGrande:
            raise AudioTooLongError("El audio supera el límite de tamaño de Telegram") from None
        except (TelegramError, ValueError) as exc:
            logger.warning("Fallo al descargar audio de Telegram (%s)", type(exc).__name__)
            raise AudioProcessingError("No se pudo descargar el audio de Telegram") from None

        return raw_bytes, _detect_mime(file_path)

    # Seguridad (RNF-14): no incluir la URL completa (puede traer tokens firmados), solo el esquema
    esquema = media_url.split(":", 1)[0] if ":" in media_url else "desconocido"
    raise AudioProcessingError(f"Protocolo o canal de audio no soportado ({esquema})")


def transcribe_audio_bytes(
    audio_bytes: bytes,
    mime_type: str = "audio/ogg",
    http_client: httpx.Client | None = None,
    filename: str | None = None,
) -> str:
    """Transcribe bytes de audio a texto utilizando el proveedor configurado."""
    if not audio_bytes:
        raise AudioEmptyError("El archivo de audio está vacío")

    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise AudioTooLongError(f"El audio excede el límite permitido de {MAX_AUDIO_BYTES} bytes")

    provider, api_key = _resolve_provider_and_key()
    model = get_settings().audio_model
    file_name = filename or _EXTENSION_BY_MIME.get(mime_type, "audio.ogg")

    client = http_client or httpx.Client(timeout=TIMEOUT_SECONDS)

    try:
        if provider == "gemini":
            return _transcribe_gemini(audio_bytes, mime_type, api_key, model, client)
        if provider in _WHISPER_ENDPOINTS:
            provider_name, url, default_model = _WHISPER_ENDPOINTS[provider]
            return _transcribe_whisper(
                provider_name, url, default_model, audio_bytes, mime_type, api_key, model, client, file_name
            )
        if not provider:
            raise AudioProcessingError(
                "AUDIO_PROVIDER no está configurado con un proveedor soportado (gemini, groq, openai)"
            )
        raise AudioProcessingError(f"Proveedor de audio no soportado: {provider}")
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
        raise AudioProcessingError("Falta AUDIO_API_KEY (o LLM_API_KEY de Gemini) para transcribir con Gemini")
    if len(audio_bytes) > MAX_GEMINI_INLINE_BYTES:
        raise AudioTooLongError(
            f"El audio excede el límite admitido por Gemini inlineData ({MAX_GEMINI_INLINE_BYTES} bytes)"
        )
    model_name = model or DEFAULT_GEMINI_MODEL
    # Seguridad (RNF-14): la clave se envía en el header x-goog-api-key, no en la URL
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
    headers = {"x-goog-api-key": api_key}

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
        response = client.post(url, headers=headers, json=payload, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise AudioProcessingError("Respuesta inesperada de Gemini al transcribir audio")
        candidates = data.get("candidates", [])
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
            raise AudioEmptyError("Gemini no detectó contenido de voz en el audio")
        content = candidates[0].get("content", {})
        raw_parts = content.get("parts") if isinstance(content, dict) else None
        parts = raw_parts if isinstance(raw_parts, list) else []
        text = "".join(
            part["text"] for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str)
        ).strip()
        if not text:
            raise AudioEmptyError("La transcripción del audio está vacía")
        return text
    except httpx.HTTPStatusError as exc:
        logger.warning("Error HTTP de Gemini al transcribir audio: %s", exc.response.status_code)
        raise AudioProcessingError(
            f"Falla en el servicio de transcripción de Gemini ({exc.response.status_code})"
        ) from None
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Error con Gemini al transcribir audio: %s", type(exc).__name__)
        raise AudioProcessingError(f"Error de conexión o respuesta con Gemini ({type(exc).__name__})") from None


def _transcribe_whisper(
    provider_name: str,
    url: str,
    default_model: str,
    audio_bytes: bytes,
    mime_type: str,
    api_key: str | None,
    model: str | None,
    client: httpx.Client,
    filename: str = "audio.ogg",
) -> str:
    if not api_key:
        raise AudioProcessingError(f"Falta AUDIO_API_KEY para transcribir con {provider_name}")
    model_name = model or default_model
    headers = {"Authorization": f"Bearer {api_key}"}
    files = {"file": (filename, audio_bytes, mime_type)}
    data = {"model": model_name, "language": "es"}

    try:
        response = client.post(url, headers=headers, files=files, data=data, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        data_resp = response.json()
        raw = data_resp.get("text") if isinstance(data_resp, dict) else None
        text = raw.strip() if isinstance(raw, str) else ""
        if not text:
            raise AudioEmptyError(f"{provider_name} devolvió transcripción vacía")
        return text
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Error en transcripción con %s: %s", provider_name, type(exc).__name__)
        raise AudioProcessingError(f"Falla en transcripción con {provider_name} ({type(exc).__name__})") from None


def transcribe_audio(media_url: str) -> str:
    """Descarga y transcribe un audio referenciado por `media_url`."""
    audio_bytes, mime_type = download_audio_bytes(media_url)
    return transcribe_audio_bytes(audio_bytes, mime_type)
