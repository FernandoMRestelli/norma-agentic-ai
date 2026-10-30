"""Bot de Telegram, canal de prueba de la beta: Bot API, lista blanca y traducción de updates.

Es la única capa que conoce Telegram; el resto del sistema ve `IncomingMessage` y `ChannelReply`.
Detalle en docs/spec/canal-telegram.md.
"""

import logging
import threading
from collections import deque
from typing import Any

import httpx
from pydantic import BaseModel

from backend.config import get_settings
from backend.schemas.conversation_schema import ChannelReply
from backend.schemas.order_schema import Channel, ConfirmationAction, IncomingMessage, MessageType
from backend.services import conversation_service

logger = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT_SECONDS = 10

NOT_ALLOWED = (
    "Hola 👋 Este bot es de prueba y solo atiende a integrantes del equipo de Norma.\n"
    "Tu id de Telegram es {user_id}: si sos del equipo, sumalo a TELEGRAM_ALLOWED_USER_IDS."
)
UNSUPPORTED = "Por ahora solo entiendo mensajes de texto, audios y fotos."
SOMETHING_FAILED = "Tuve un problema, probá de nuevo en unos minutos."

ACTION_CODES = {ConfirmationAction.CONFIRM: "c", ConfirmationAction.CORRECT: "e"}


class TelegramError(RuntimeError):
    """La Bot API respondió con error o no se pudo llamar. El mensaje nunca incluye el token."""


class TelegramClient:
    def __init__(self, token: str, http: httpx.Client | None = None):
        self._token = token
        self.http = http or httpx.Client(timeout=TIMEOUT_SECONDS)

    def call(self, method: str, http_timeout: float = TIMEOUT_SECONDS, **params: Any) -> Any:
        url = API_URL.format(token=self._token, method=method)
        try:
            response = self.http.post(url, json=params, timeout=http_timeout)
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # El error de httpx incluye la URL, que tiene el token: no se encadena (RNF-14)
            raise TelegramError(f"{method}: no se pudo llamar a Telegram ({type(exc).__name__})") from None
        if not data.get("ok"):
            raise TelegramError(f"{method}: {data.get('description', response.status_code)}")
        return data["result"]

    def get_me(self) -> dict:
        return self.call("getMe")

    def delete_webhook(self) -> None:
        self.call("deleteWebhook")

    def get_updates(self, offset: int | None, poll_seconds: int) -> list[dict]:
        params: dict[str, Any] = {"timeout": poll_seconds, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        return self.call("getUpdates", http_timeout=poll_seconds + 10, **params)

    def send_message(self, chat_id: int, text: str, buttons: list[list[dict]] | None = None) -> None:
        params: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        self.call("sendMessage", **params)

    def answer_callback_query(self, callback_query_id: str) -> None:
        self.call("answerCallbackQuery", callback_query_id=callback_query_id)

    def remove_buttons(self, chat_id: int, message_id: int) -> None:
        self.call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id)


def get_client() -> TelegramClient:
    token = get_settings().telegram_bot_token
    if not token:
        raise ValueError("Falta TELEGRAM_BOT_TOKEN en .env")
    return TelegramClient(token)


class TelegramMessage(BaseModel):
    chat_id: int
    user_id: int
    message: IncomingMessage | None  # None: tipo de mensaje que el sistema no maneja


class TelegramButton(BaseModel):
    chat_id: int
    user_id: int
    message_id: int
    callback_query_id: str
    action: ConfirmationAction
    draft_id: str
    version: int


def customer_id_for(user_id: int) -> str:
    return f"telegram:{user_id}"


def draft_buttons(draft_id: str, version: int) -> list[list[dict]]:
    """Inline keyboard de Confirmar / Corregir. `callback_data` tiene un límite de 64 bytes."""
    return [
        [
            {"text": "✅ Confirmar", "callback_data": f"c:{draft_id}:{version}"},
            {"text": "✏️ Corregir", "callback_data": f"e:{draft_id}:{version}"},
        ]
    ]


def parse_update(update: dict) -> TelegramMessage | TelegramButton | None:
    """Traduce un update de Telegram. Devuelve None para lo que se ignora (grupos, otros tipos de update)."""
    if query := update.get("callback_query"):
        return _parse_button(query)
    message = update.get("message")
    if not message or message.get("chat", {}).get("type") != "private" or "from" not in message:
        return None

    user_id = message["from"]["id"]
    fields: dict[str, Any] = {
        "customer_id": customer_id_for(user_id),
        "channel": Channel.TELEGRAM,
        "external_id": str(update["update_id"]),
    }
    if text := message.get("text"):
        incoming = IncomingMessage(type=MessageType.TEXT, text=text, **fields)
    elif media := message.get("voice") or message.get("audio"):
        incoming = IncomingMessage(type=MessageType.AUDIO, media_url=f"telegram:{media['file_id']}", **fields)
    elif photos := message.get("photo"):
        # Telegram manda varias resoluciones de menor a mayor; la URL de descarga tiene el token, se guarda el id
        incoming = IncomingMessage(
            type=MessageType.IMAGE,
            media_url=f"telegram:{photos[-1]['file_id']}",
            text=message.get("caption"),
            **fields,
        )
    else:
        incoming = None
    return TelegramMessage(chat_id=message["chat"]["id"], user_id=user_id, message=incoming)


def _parse_button(query: dict) -> TelegramButton | None:
    message = query.get("message")
    code, _, rest = query.get("data", "").partition(":")
    draft_id, _, version = rest.rpartition(":")
    actions = {value: key for key, value in ACTION_CODES.items()}
    if not message or code not in actions or not draft_id or not version.isdigit():
        return None
    return TelegramButton(
        chat_id=message["chat"]["id"],
        user_id=query["from"]["id"],
        message_id=message["message_id"],
        callback_query_id=query["id"],
        action=actions[code],
        draft_id=draft_id,
        version=int(version),
    )


class _ProcessedUpdates:
    """Últimos update_id procesados, en memoria (RF-31). Pasa a la tabla processed_messages cuando exista."""

    def __init__(self, size: int = 1000):
        self._order: deque[int] = deque(maxlen=size)
        self._ids: set[int] = set()
        self._lock = threading.Lock()

    def first_time(self, update_id: int) -> bool:
        with self._lock:
            if update_id in self._ids:
                return False
            if len(self._order) == self._order.maxlen:
                self._ids.discard(self._order[0])
            self._order.append(update_id)
            self._ids.add(update_id)
            return True

    def clear(self) -> None:
        with self._lock:
            self._order.clear()
            self._ids.clear()


processed_updates = _ProcessedUpdates()


def handle_update(update: dict, client: TelegramClient) -> None:
    """Procesa un update completo: deduplica, aplica la lista blanca, llama al orquestador y responde.

    Los logs llevan ids, nunca el texto de los mensajes (RNF-03).
    """
    update_id = update.get("update_id")
    if update_id is None or not processed_updates.first_time(update_id):
        return
    event = parse_update(update)
    if event is None:
        return

    try:
        if event.user_id not in get_settings().telegram_allowed_ids:
            logger.info("Telegram: usuario %s fuera de la lista blanca", event.user_id)
            if isinstance(event, TelegramButton):
                client.answer_callback_query(event.callback_query_id)
            client.send_message(event.chat_id, NOT_ALLOWED.format(user_id=event.user_id))
            return

        if isinstance(event, TelegramButton):
            client.answer_callback_query(event.callback_query_id)
            client.remove_buttons(event.chat_id, event.message_id)
            reply = conversation_service.handle_action(
                customer_id_for(event.user_id), Channel.TELEGRAM, event.draft_id, event.version, event.action
            )
        elif event.message is None:
            reply = ChannelReply(text=UNSUPPORTED)
        else:
            reply = conversation_service.handle_message(event.message)
        send_reply(client, event.chat_id, reply)
    except Exception:
        logger.exception("Telegram: error procesando el update %s", update_id)
        try:
            client.send_message(event.chat_id, SOMETHING_FAILED)
        except TelegramError:
            logger.warning("Telegram: tampoco se pudo avisar el error del update %s", update_id)


def send_reply(client: TelegramClient, chat_id: int, reply: ChannelReply) -> None:
    buttons = draft_buttons(reply.draft_id, reply.draft_version or 1) if reply.draft_id else None
    client.send_message(chat_id, reply.text, buttons)
