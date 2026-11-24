"""Preprocesamiento multimodal: convierte texto, audio o imagen en texto para el agente."""

import logging
import uuid
from dataclasses import dataclass

from internal.schemas.order_schema import IncomingMessage, MessageType
from internal.schemas.vision_schema import ImagenLeida
from internal.services import vision_service

logger = logging.getLogger(__name__)


class ImagenIlegible(ValueError):
    """La imagen no se pudo bajar o leer, o no tiene texto útil: se le pide al cliente que lo escriba (spec §6)."""


@dataclass(frozen=True)
class Entrada:
    """Un mensaje preparado para el agente.

    `lectura` es la lectura de una imagen con renglones: el agente arma los renglones del pedido desde ahí, sin
    volver a partir el texto. `mensaje_id` enlaza la traza de la lectura con la del turno.
    """

    texto: str
    lectura: ImagenLeida | None = None
    mensaje_id: str | None = None


def preparar(message: IncomingMessage) -> Entrada:
    match message.type:
        case MessageType.TEXT:
            return Entrada(normalize_text(message.text))
        case MessageType.AUDIO:
            return Entrada(transcribe_audio(message.media_url))
        case MessageType.IMAGE:
            return leer_imagen_del_mensaje(message.media_url, message.text, message.external_id)
        case _:
            raise ValueError(f"Tipo de mensaje no soportado: {message.type}")


def prepare(message: IncomingMessage) -> str:
    return preparar(message).texto


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
    # TODO: speech-to-text con el proveedor elegido
    raise NotImplementedError


def download_media(media_url: str) -> bytes:
    """Baja el archivo de un mensaje. Hoy solo Telegram (`telegram:<file_id>`); WhatsApp y la web, más adelante."""
    if media_url.startswith("telegram:"):
        from internal.services import telegram_service  # import local: telegram_service importa este módulo

        return telegram_service.get_client().download_file(media_url.removeprefix("telegram:"))
    raise ImagenIlegible("Todavía no se pueden bajar imágenes de este canal")


def extract_text_from_image(media_url: str, caption: str | None = None) -> str:
    """Lee la imagen con el modelo de LLM_VISION_PROVIDER y devuelve el texto para el agente, con el epígrafe
    (si lo hay) adelante. Lanza ImagenIlegible si no hay nada que leer."""
    return leer_imagen_del_mensaje(media_url, caption).texto


def leer_imagen_del_mensaje(media_url: str, caption: str | None = None, external_id: str | None = None) -> Entrada:
    """Baja y lee la imagen. Devuelve el texto para el agente, con el epígrafe (si lo hay) adelante, y la lectura
    si trae renglones. Lanza ImagenIlegible si no hay nada que leer."""
    from internal.agent import observabilidad  # import local: el agente importa los servicios

    mensaje_id = external_id or uuid.uuid4().hex
    with observabilidad.lectura(mensaje_id) as paso:
        try:
            imagen = download_media(media_url)
            lectura = vision_service.leer_imagen(imagen)
        except ImagenIlegible:
            paso.fallo("no_se_pudo_bajar")
            raise
        except Exception as exc:  # bajar o leer puede fallar de muchas formas; para el cliente es lo mismo
            logger.warning("Imagen: no se pudo leer (%s)", type(exc).__name__)
            paso.fallo(type(exc).__name__)
            raise ImagenIlegible(str(exc)) from exc

        leida = lectura.resultado
        # Solo números: sin el texto de la imagen (RNF-03)
        logger.info(
            "Imagen: proveedor=%s modelo=%s segundos=%.1f legible=%s renglones=%d tachados=%d tokens_entrada=%s "
            "tokens_salida=%s error=%s",
            lectura.proveedor,
            lectura.modelo,
            lectura.segundos,
            leida.legible if leida else "-",
            len(leida.renglones) if leida else 0,
            len(leida.tachados) if leida else 0,
            lectura.tokens_entrada,
            lectura.tokens_salida,
            "json_invalido" if lectura.error else "-",
        )
        paso.atributos(
            proveedor=lectura.proveedor,
            modelo=lectura.modelo,
            segundos=round(lectura.segundos, 1),
            legible=leida.legible if leida else None,
            renglones=len(leida.renglones) if leida else 0,
            variantes=sum(len(renglon.variantes) for renglon in leida.renglones) if leida else 0,
            tachados=len(leida.tachados) if leida else 0,
            con_duda=sum(1 for renglon in leida.renglones if renglon.duda) if leida else 0,
            tokens_entrada=lectura.tokens_entrada,
            tokens_salida=lectura.tokens_salida,
        )
        if leida is None or not leida.legible or not leida.texto.strip():
            paso.fallo("json_invalido" if lectura.error else "sin_texto")
            raise ImagenIlegible(lectura.error or "La imagen no tiene texto legible")
        paso.texto(salida=leida.model_dump())

    texto = leida.como_texto_para_el_agente()
    if caption and caption.strip():
        texto = f"{normalize_text(caption)}\n{texto}"
    return Entrada(texto, leida if leida.renglones else None, mensaje_id)
