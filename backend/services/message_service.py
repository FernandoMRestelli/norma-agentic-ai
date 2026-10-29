"""Preprocesamiento multimodal: convierte texto, audio o imagen en texto para el agente."""

from backend.schemas.order_schema import IncomingMessage, MessageType


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
    return " ".join(text.split())


def transcribe_audio(media_url: str) -> str:
    # TODO: speech-to-text con el proveedor elegido
    raise NotImplementedError


def extract_text_from_image(media_url: str) -> str:
    # TODO: OCR / visión con el proveedor elegido
    raise NotImplementedError
