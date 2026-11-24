"""Preprocesamiento multimodal: convierte texto, audio o imagen en texto para el agente."""

import logging

from internal.schemas.order_schema import IncomingMessage, MessageType
from internal.services import audio_service, vision_service

logger = logging.getLogger(__name__)

MARCA_AUDIO = "[audio transcripto]"


class ImagenIlegible(ValueError):
    """La imagen no se pudo bajar o leer, o no tiene texto útil: se le pide al cliente que lo escriba (spec §6)."""


def marcar_origen(texto: str, tipo: MessageType) -> str:
    """Agrega la marca de origen multimedia solo en el texto que llega al agente."""
    if tipo == MessageType.AUDIO:
        return f"{MARCA_AUDIO}\n{texto}"
    return texto


def sin_marca_audio(texto: str) -> str:
    """Quita solo `[audio transcripto]` para ruteo y palabras clave; `[imagen leída]` se conserva."""
    return "\n".join(linea for linea in texto.splitlines() if linea.strip() != MARCA_AUDIO)


def prepare(message: IncomingMessage) -> str:
    match message.type:
        case MessageType.TEXT:
            return normalize_text(message.text)
        case MessageType.AUDIO:
            return transcribe_audio(message.media_url)
        case MessageType.IMAGE:
            return extract_text_from_image(message.media_url, message.text)
        case _:
            raise ValueError(f"Tipo de mensaje no soportado: {message.type}")


def normalize_text(text: str) -> str:
    """Saca los espacios de más sin juntar los renglones: una lista de pedido llega renglón por renglón (RF-65)."""
    lineas = (" ".join(linea.split()) for linea in text.splitlines())
    return "\n".join(linea for linea in lineas if linea)


def partir(texto: str, limite: int) -> list[str]:
    """Parte un mensaje que pasa el límite del canal en varios, sin cortar un renglón por la mitad (RF-70).

    El detalle de una propuesta larga tiene una línea por producto: cada parte termina en un fin de línea. Solo un
    renglón que por sí solo pasa el límite se corta.
    """
    if len(texto) <= limite:
        return [texto]
    partes: list[str] = []
    actual = ""
    for linea in texto.split("\n"):
        while len(linea) > limite:
            if actual:
                partes.append(actual)
                actual = ""
            partes.append(linea[:limite])
            linea = linea[limite:]
        candidata = f"{actual}\n{linea}" if actual else linea
        if len(candidata) > limite:
            partes.append(actual)
            actual = linea
        else:
            actual = candidata
    if actual.strip():
        partes.append(actual)
    return [parte for parte in partes if parte.strip()]


def transcribe_audio(media_url: str) -> str:
    """Delega la descarga y transcripción en audio_service y normaliza el texto sin marca de origen (CA-06)."""
    raw_text = audio_service.transcribe_audio(media_url)
    texto = normalize_text(raw_text)
    if not texto:
        raise audio_service.AudioEmptyError("La transcripción del audio está vacía")
    return texto


def download_media(media_url: str) -> bytes:
    """Baja el archivo de un mensaje. Hoy solo Telegram (`telegram:<file_id>`); WhatsApp y la web, más adelante."""
    if media_url.startswith("telegram:"):
        from internal.services import telegram_service  # import local: telegram_service importa este módulo

        return telegram_service.get_client().download_file(media_url.removeprefix("telegram:"))
    raise ImagenIlegible("Todavía no se pueden bajar imágenes de este canal")


def extract_text_from_image(media_url: str, caption: str | None = None) -> str:
    """Lee la imagen con el modelo de LLM_VISION_PROVIDER y devuelve el texto para el agente, con el epígrafe
    (si lo hay) adelante. Lanza ImagenIlegible si no hay nada que leer."""
    try:
        imagen = download_media(media_url)
        lectura = vision_service.leer_imagen(imagen)
    except ImagenIlegible:
        raise
    except Exception as exc:  # bajar o leer puede fallar de muchas formas; para el cliente es lo mismo
        logger.warning("Imagen: no se pudo leer (%s)", type(exc).__name__)
        raise ImagenIlegible(str(exc)) from exc

    leida = lectura.resultado
    # Solo números: sin el texto de la imagen (RNF-03)
    logger.info(
        "Imagen: proveedor=%s modelo=%s segundos=%.1f legible=%s renglones=%d tokens_entrada=%s tokens_salida=%s "
        "error=%s",
        lectura.proveedor,
        lectura.modelo,
        lectura.segundos,
        leida.legible if leida else "-",
        len(leida.renglones) if leida else 0,
        lectura.tokens_entrada,
        lectura.tokens_salida,
        "json_invalido" if lectura.error else "-",
    )
    if leida is None or not leida.legible or not leida.texto.strip():
        raise ImagenIlegible(lectura.error or "La imagen no tiene texto legible")

    texto = leida.como_texto_para_el_agente()
    if caption and caption.strip():
        texto = f"{normalize_text(caption)}\n{texto}"
    return texto
