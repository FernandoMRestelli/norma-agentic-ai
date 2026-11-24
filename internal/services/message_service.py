"""Preprocesamiento multimodal: convierte texto, audio o imagen en texto para el agente."""

import logging
import time
import uuid
from dataclasses import dataclass

from internal.schemas.order_schema import IncomingMessage, MessageType
from internal.schemas.vision_schema import ImagenLeida
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
            return transcribir_audio_del_mensaje(message.media_url, message.external_id)
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


def transcribir_audio_del_mensaje(media_url: str, external_id: str | None = None) -> Entrada:
    """Transcribe el audio y deja su traza, enlazada con la del turno. Lanza los errores de `audio_service`."""
    from internal.agent import observabilidad  # import local: el agente importa los servicios

    mensaje_id = external_id or uuid.uuid4().hex
    texto, error = "", None
    inicio = time.perf_counter()
    with observabilidad.transcripcion(mensaje_id) as paso:
        try:
            texto = transcribe_audio(media_url)
        except Exception as exc:  # se relanza fuera del span: MLflow guardaría el mensaje de la excepción (RNF-03)
            error = exc
            paso.fallo(type(exc).__name__)
        proveedor, modelo = audio_service.proveedor_y_modelo()
        # Solo números: sin el texto del audio (RNF-03)
        paso.atributos(
            proveedor=proveedor,
            modelo=modelo,
            segundos=round(time.perf_counter() - inicio, 1),
            palabras=len(texto.split()),
        )
        paso.texto(salida={"transcripcion": texto} if texto else None)
    if error is not None:
        raise error
    return Entrada(texto, mensaje_id=mensaje_id)


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
