"""Lee una imagen y devuelve un `ImagenLeida` en JSON, con un modelo de frontera por API o con uno local.

El proveedor sale de LLM_VISION_PROVIDER (o se pasa explícito, para comparar):

- `openai`: API de OpenAI (por ejemplo `gpt-6-luna`), con salida estructurada por JSON Schema.
- `openrouter`: cualquier modelo con visión de OpenRouter, misma API que OpenAI.
- `gemini`: Gemini por la API de Google AI Studio.
- `huggingface`: modelo de Hugging Face corriendo en la máquina (ver `vision_local.py`), sin API key.

Los tres primeros comparten el mismo camino de LangChain; el local arma el JSON a partir del texto que genera.
"""

import base64
import io
import json
import logging
import time
from dataclasses import dataclass

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from internal.agent.llm import OPENROUTER_BASE_URL
from internal.config import get_settings
from internal.schemas.vision_schema import ImagenLeida

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 30  # RNF-06: imagen p95 < 20 s, timeout del LLM 30 s
MAX_RETRIES = 1
MAX_LADO_PX = 1600  # más grande no mejora la lectura de un pedido y encarece los tokens de imagen

PROVEEDORES_API = ("openai", "openrouter", "gemini")
PROVEEDORES = (*PROVEEDORES_API, "huggingface")

INSTRUCCIONES = """Sos el lector de imágenes de un distribuidor de perfumería que recibe pedidos por chat.
Te llega una foto o captura que manda un cliente: una lista escrita a mano o impresa, una captura de otro chat,
la foto de un producto o un ticket.

Tu trabajo es solo leer, no interpretar contra ningún catálogo:
- `texto`: copiá todo el texto visible, en orden de lectura, con un salto de línea por renglón. Sin corregir
  la ortografía ni completar abreviaturas.
- `renglones`: un elemento por producto pedido. `cantidad` como número (media docena = 6, "una" = 1); si no hay
  cantidad, null. No inventes marcas ni presentaciones que no estén escritas.
- `dudas`: lo que no se lee bien o es ambiguo (un número tapado, una palabra borrosa).
- Si la imagen no tiene texto o no se puede leer, `legible` en false y `renglones` vacío.
- Saludos, firmas, horarios y direcciones van en `texto` pero no en `renglones`."""


@dataclass
class Lectura:
    """Resultado de leer una imagen, con lo necesario para comparar modelos."""

    proveedor: str
    modelo: str
    resultado: ImagenLeida | None  # None si el modelo no devolvió un JSON válido
    crudo: str  # lo que devolvió el modelo, sin procesar
    segundos: float
    tokens_entrada: int | None = None
    tokens_salida: int | None = None
    error: str | None = None


def preparar_imagen(imagen: bytes) -> tuple[bytes, str]:
    """Achica la imagen si hace falta y la pasa a JPEG o PNG. Devuelve los bytes y el MIME type."""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(imagen)) as original:
        img = ImageOps.exif_transpose(original)  # las fotos del celular vienen giradas en los metadatos
        img.thumbnail((MAX_LADO_PX, MAX_LADO_PX))
        salida = io.BytesIO()
        if img.mode in ("RGBA", "LA", "P"):
            img.save(salida, format="PNG", optimize=True)
            return salida.getvalue(), "image/png"
        img.convert("RGB").save(salida, format="JPEG", quality=90)
        return salida.getvalue(), "image/jpeg"


def get_vision_model(proveedor: str, modelo: str, api_key: str | None) -> BaseChatModel:
    """Modelo de LangChain con visión para los proveedores por API."""
    if not modelo or not api_key:
        raise ValueError(f"Para leer imágenes con {proveedor} hacen falta LLM_VISION_MODEL y LLM_VISION_API_KEY")
    match proveedor:
        case "openai":
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(model=modelo, api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES)
        case "openrouter":
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model=modelo,
                api_key=api_key,
                base_url=OPENROUTER_BASE_URL,
                timeout=TIMEOUT_SECONDS,
                max_retries=MAX_RETRIES,
            )
        case "gemini":
            from langchain_google_genai import ChatGoogleGenerativeAI

            return ChatGoogleGenerativeAI(
                model=modelo, api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES
            )
        case other:
            raise ValueError(f"Proveedor de visión por API no soportado: {other}")


def _mensajes(imagen: bytes, mime: str, instrucciones: str = INSTRUCCIONES) -> list:
    datos = base64.b64encode(imagen).decode()
    return [
        SystemMessage(content=instrucciones),
        HumanMessage(
            content=[
                {"type": "text", "text": "Leé esta imagen."},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{datos}"}},
            ]
        ),
    ]


def _leer_con_api(imagen: bytes, mime: str, proveedor: str, modelo: str, api_key: str | None) -> Lectura:
    chat = get_vision_model(proveedor, modelo, api_key)
    estructurado = chat.with_structured_output(ImagenLeida, method="json_schema", include_raw=True)
    inicio = time.perf_counter()
    try:
        respuesta = estructurado.invoke(_mensajes(imagen, mime))
    except Exception as exc:  # cada proveedor lanza sus propios errores (cuota, timeout, imagen rechazada)
        return Lectura(proveedor, modelo, None, "", time.perf_counter() - inicio, error=f"{type(exc).__name__}: {exc}")
    segundos = time.perf_counter() - inicio

    crudo_msg = respuesta["raw"]
    uso = getattr(crudo_msg, "usage_metadata", None) or {}
    error = respuesta.get("parsing_error")
    return Lectura(
        proveedor=proveedor,
        modelo=modelo,
        resultado=respuesta.get("parsed"),
        crudo=crudo_msg.text if hasattr(crudo_msg, "text") else str(crudo_msg.content),
        segundos=segundos,
        tokens_entrada=uso.get("input_tokens"),
        tokens_salida=uso.get("output_tokens"),
        error=f"JSON inválido: {error}" if error else None,
    )


def parsear_json(texto: str) -> ImagenLeida:
    """Saca el objeto JSON de lo que generó un modelo sin salida estructurada (puede venir entre ``` o con texto
    antes y después) y lo valida contra el esquema. Lanza ValueError si no hay un JSON válido."""
    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio == -1 or fin <= inicio:
        raise ValueError("La respuesta no tiene un objeto JSON")
    try:
        return ImagenLeida.model_validate(json.loads(texto[inicio : fin + 1]))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"JSON inválido: {exc}") from exc


def precargar() -> None:
    """Si LLM_VISION_PROVIDER es huggingface, carga el modelo local ahora (tarda) en vez de en la primera foto."""
    settings = get_settings()
    if settings.llm_vision_provider != "huggingface":
        return
    from internal.services import vision_local

    modelo = settings.llm_vision_model or vision_local.MODELO_POR_DEFECTO
    logger.info("Visión: cargando el modelo local %s", modelo)
    inicio = time.perf_counter()
    try:
        vision_local.cargar(modelo)
    except ImportError as exc:
        raise ValueError(
            f"LLM_VISION_PROVIDER=huggingface necesita el modelo local ({exc}): "
            "pip install -r requirements-vision-local.txt"
        ) from None
    logger.info("Visión: modelo local listo en %.0f s", time.perf_counter() - inicio)


def leer_imagen(imagen: bytes, proveedor: str | None = None, modelo: str | None = None) -> Lectura:
    """Lee una imagen con el proveedor y modelo dados, o los de LLM_VISION_* si no se pasan."""
    settings = get_settings()
    proveedor = proveedor or settings.llm_vision_provider
    modelo = modelo or settings.llm_vision_model
    if not proveedor:
        raise ValueError(f"Falta LLM_VISION_PROVIDER en .env (opciones: {', '.join(PROVEEDORES)})")
    if proveedor not in PROVEEDORES:
        raise ValueError(f"Proveedor de visión no soportado: {proveedor}")

    datos, mime = preparar_imagen(imagen)
    if proveedor == "huggingface":
        from internal.services import vision_local

        return vision_local.leer(datos, modelo or vision_local.MODELO_POR_DEFECTO)
    return _leer_con_api(datos, mime, proveedor, modelo or "", settings.llm_vision_api_key)
