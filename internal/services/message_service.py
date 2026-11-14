"""Preprocesamiento multimodal: convierte texto, audio o imagen en texto para el agente."""

from internal.schemas.order_schema import IncomingMessage, MessageType


def prepare(message: IncomingMessage) -> str:
    match message.type:
        case MessageType.TEXT:
            return normalize_text(message.text)
        case MessageType.AUDIO:
            return transcribe_audio(message.media_url)
        case MessageType.IMAGE:
            return extract_text_from_image(message.media_url)
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
    # TODO: speech-to-text con el proveedor elegido
    raise NotImplementedError


def extract_text_from_image(media_url: str) -> str:
    # TODO: OCR / visión con el proveedor elegido
    raise NotImplementedError
