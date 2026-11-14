import json

import httpx
import pytest
from fastapi.testclient import TestClient

from internal.config import get_settings
from internal.main import app
from internal.schemas.conversation_schema import ChannelReply
from internal.schemas.order_schema import Channel, MessageType
from internal.services import conversation_service, telegram_service
from internal.services.telegram_service import TelegramClient, TelegramError, parse_update

USUARIO = 111
INTRUSO = 999


class FakeTelegram:
    """Registra lo que el bot le pediría a la Bot API, sin red."""

    def __init__(self):
        self.calls: list[tuple] = []

    def send_message(self, chat_id, text):
        self.calls.append(("send_message", chat_id, text))

    @property
    def sent(self) -> list[tuple]:
        return [call for call in self.calls if call[0] == "send_message"]


@pytest.fixture(autouse=True)
def telegram_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:token-de-prueba")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", f"{USUARIO}, 222")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "secreto-webhook")
    get_settings.cache_clear()
    telegram_service.processed_updates.clear()
    yield
    get_settings.cache_clear()
    telegram_service.processed_updates.clear()


@pytest.fixture
def orquestador(monkeypatch):
    """Reemplaza al orquestador y guarda lo que recibe."""
    recibido = []

    def handle_message(message):
        recibido.append(message)
        return ChannelReply(text="respuesta")

    monkeypatch.setattr(conversation_service, "handle_message", handle_message)
    return recibido


def mensaje(update_id=1, user_id=USUARIO, chat_type="private", **contenido):
    contenido = contenido or {"text": "Quiero 5 shampoo pantene 400"}
    return {
        "update_id": update_id,
        "message": {
            "message_id": 10,
            "from": {"id": user_id, "first_name": "Prueba"},
            "chat": {"id": user_id, "type": chat_type},
            **contenido,
        },
    }


def boton(data="c:draft-1:2", update_id=5, user_id=USUARIO):
    return {
        "update_id": update_id,
        "callback_query": {
            "id": "cb-1",
            "from": {"id": user_id},
            "message": {"message_id": 20, "chat": {"id": user_id, "type": "private"}},
            "data": data,
        },
    }


def test_traduce_texto():
    """CA-33: un texto llega como IncomingMessage de tipo text del canal telegram."""
    event = parse_update(mensaje(update_id=7))
    assert event.chat_id == USUARIO
    assert event.message.type == MessageType.TEXT
    assert event.message.text == "Quiero 5 shampoo pantene 400"
    assert event.message.channel == Channel.TELEGRAM
    assert event.message.customer_id == f"telegram:{USUARIO}"
    assert event.message.external_id == "7"


@pytest.mark.parametrize("clave", ["voice", "audio"])
def test_traduce_audio(clave):
    """CA-33: un audio guarda la referencia al archivo, no la URL de descarga (que tiene el token)."""
    event = parse_update(mensaje(**{clave: {"file_id": "AUDIO1", "duration": 3}}))
    assert event.message.type == MessageType.AUDIO
    assert event.message.media_url == "telegram:AUDIO1"


def test_traduce_foto_con_la_mayor_resolucion():
    """CA-33: de una foto se toma la versión más grande y el epígrafe como texto."""
    fotos = [{"file_id": "CHICA", "width": 90}, {"file_id": "GRANDE", "width": 1280}]
    event = parse_update(mensaje(photo=fotos, caption="esto"))
    assert event.message.type == MessageType.IMAGE
    assert event.message.media_url == "telegram:GRANDE"
    assert event.message.text == "esto"


def test_traduce_tipo_no_soportado():
    event = parse_update(mensaje(sticker={"file_id": "S1"}))
    assert event.message is None


def test_ignora_grupos():
    assert parse_update(mensaje(chat_type="group")) is None


def test_ignora_callback_queries():
    """Los botones viejos de un chat no hacen nada: el cliente confirma escribiendo."""
    assert parse_update(boton()) is None


def test_mensaje_de_integrante_llega_al_orquestador(orquestador):
    bot = FakeTelegram()
    telegram_service.handle_update(mensaje(), bot)
    assert [m.text for m in orquestador] == ["Quiero 5 shampoo pantene 400"]
    assert bot.calls == [("send_message", USUARIO, "respuesta")]


def test_usuario_fuera_de_la_lista_blanca(orquestador):
    """CA-31: responde un texto fijo con su id y no llega al orquestador."""
    bot = FakeTelegram()
    telegram_service.handle_update(mensaje(user_id=INTRUSO), bot)
    assert orquestador == []
    ((_, chat_id, texto),) = bot.sent
    assert chat_id == INTRUSO
    assert str(INTRUSO) in texto


def test_lista_blanca_vacia_no_deja_pasar_a_nadie(monkeypatch, orquestador):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "")
    get_settings.cache_clear()
    telegram_service.handle_update(mensaje(), FakeTelegram())
    assert orquestador == []


def test_update_repetido_se_procesa_una_vez(orquestador):
    """CA-32: el mismo update_id no se procesa ni se responde dos veces."""
    bot = FakeTelegram()
    telegram_service.handle_update(mensaje(update_id=42), bot)
    telegram_service.handle_update(mensaje(update_id=42), bot)
    assert len(orquestador) == 1
    assert len(bot.sent) == 1


def test_tipo_no_soportado_responde_sin_orquestador(orquestador):
    bot = FakeTelegram()
    telegram_service.handle_update(mensaje(sticker={"file_id": "S1"}), bot)
    assert orquestador == []
    assert bot.sent == [("send_message", USUARIO, telegram_service.UNSUPPORTED)]


def test_error_del_orquestador_avisa_al_cliente(monkeypatch):
    def falla(message):
        raise RuntimeError("se rompió")

    monkeypatch.setattr(conversation_service, "handle_message", falla)
    bot = FakeTelegram()
    telegram_service.handle_update(mensaje(), bot)
    assert bot.sent == [("send_message", USUARIO, telegram_service.SOMETHING_FAILED)]


def test_cliente_de_telegram_no_filtra_el_token_en_errores():
    """RNF-14: un error de conexión no lleva la URL (que tiene el token) en el mensaje ni en la causa."""

    def handler(request):
        raise httpx.ConnectError(f"no se pudo conectar a {request.url}")

    client = TelegramClient("123:token-de-prueba", http=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(TelegramError) as error:
        client.get_me()
    assert "token-de-prueba" not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__


def test_cliente_de_telegram_arma_el_pedido():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": True})

    client = TelegramClient("123:token-de-prueba", http=httpx.Client(transport=httpx.MockTransport(handler)))
    client.send_message(USUARIO, "hola")
    (request,) = requests
    assert request.url.path == "/bot123:token-de-prueba/sendMessage"
    assert b'"inline_keyboard"' not in request.content


def test_cliente_de_telegram_parte_un_mensaje_largo():
    """RF-70: una propuesta de muchas líneas pasa el límite de Telegram y sale en varios mensajes, por renglón."""
    enviados = []

    def handler(request):
        enviados.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True, "result": True})

    client = TelegramClient("123:token-de-prueba", http=httpx.Client(transport=httpx.MockTransport(handler)))
    lineas = [f"• 2 x Producto de prueba con un nombre bastante largo número {n} ($12500.00 c/u)" for n in range(80)]
    client.send_message(USUARIO, "\n".join(lineas))

    assert len(enviados) == 2
    assert all(len(texto) <= telegram_service.MAX_LARGO_MENSAJE for texto in enviados)
    assert "\n".join(enviados).splitlines() == lineas


def test_cliente_de_telegram_respuesta_con_error():
    def handler(request):
        return httpx.Response(
            409, json={"ok": False, "description": "Conflict: terminated by other getUpdates request"}
        )

    client = TelegramClient("123:token-de-prueba", http=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(TelegramError, match="Conflict"):
        client.get_updates(None, 0)


api = TestClient(app)


def test_webhook_con_secret_correcto(monkeypatch):
    """CA-30: con el secret correcto responde 200 y procesa el update después."""
    procesados = []
    monkeypatch.setattr(telegram_service, "handle_update", lambda update, client: procesados.append(update))
    response = api.post(
        "/webhook/telegram", json=mensaje(), headers={"X-Telegram-Bot-Api-Secret-Token": "secreto-webhook"}
    )
    assert response.status_code == 200
    assert procesados == [mensaje()]


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-Telegram-Bot-Api-Secret-Token": "otro"}, {"X-Telegram-Bot-Api-Secret-Token": "secreto-ñ".encode()}],
)
def test_webhook_con_secret_invalido(headers):
    """CA-30: sin header, con otro secret o con uno no ASCII, 403 (no 500)."""
    assert api.post("/webhook/telegram", json=mensaje(), headers=headers).status_code == 403


def test_webhook_con_cuerpo_no_json():
    """Con el secret correcto pero un cuerpo que no es JSON, 400 y no 500."""
    response = api.post(
        "/webhook/telegram", content=b"no es json", headers={"X-Telegram-Bot-Api-Secret-Token": "secreto-webhook"}
    )
    assert response.status_code == 400


def test_webhook_sin_secret_configurado(monkeypatch):
    """CA-30: sin TELEGRAM_WEBHOOK_SECRET el webhook está apagado."""
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "")
    get_settings.cache_clear()
    response = api.post("/webhook/telegram", json=mensaje(), headers={"X-Telegram-Bot-Api-Secret-Token": ""})
    assert response.status_code == 403
