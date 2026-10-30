import httpx
import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.main import app
from backend.schemas.conversation_schema import ChannelReply
from backend.schemas.order_schema import Channel, ConfirmationAction, MessageType
from backend.services import conversation_service, telegram_service
from backend.services.telegram_service import TelegramClient, TelegramError, parse_update

USUARIO = 111
INTRUSO = 999


class FakeTelegram:
    """Registra lo que el bot le pediría a la Bot API, sin red."""

    def __init__(self):
        self.calls: list[tuple] = []

    def send_message(self, chat_id, text, buttons=None):
        self.calls.append(("send_message", chat_id, text, buttons))

    def answer_callback_query(self, callback_query_id):
        self.calls.append(("answer_callback_query", callback_query_id))

    def remove_buttons(self, chat_id, message_id):
        self.calls.append(("remove_buttons", chat_id, message_id))

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

    def handle_action(customer_id, channel, draft_id, version, action):
        recibido.append((customer_id, channel, draft_id, version, action))
        return ChannelReply(text="accion")

    monkeypatch.setattr(conversation_service, "handle_message", handle_message)
    monkeypatch.setattr(conversation_service, "handle_action", handle_action)
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


@pytest.mark.parametrize("data", ["x:draft-1:2", "c:draft-1:dos", "c::2", ""])
def test_ignora_botones_malformados(data):
    assert parse_update(boton(data=data)) is None


def test_mensaje_de_integrante_llega_al_orquestador(orquestador):
    bot = FakeTelegram()
    telegram_service.handle_update(mensaje(), bot)
    assert [m.text for m in orquestador] == ["Quiero 5 shampoo pantene 400"]
    assert bot.calls == [("send_message", USUARIO, "respuesta", None)]


def test_usuario_fuera_de_la_lista_blanca(orquestador):
    """CA-31: responde un texto fijo con su id y no llega al orquestador."""
    bot = FakeTelegram()
    telegram_service.handle_update(mensaje(user_id=INTRUSO), bot)
    assert orquestador == []
    ((_, chat_id, texto, _),) = bot.sent
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
    assert bot.sent == [("send_message", USUARIO, telegram_service.UNSUPPORTED, None)]


def test_borrador_con_botones_al_enviar():
    """CA-34: una respuesta con borrador va con Confirmar / Corregir con el draft_id y la versión."""
    bot = FakeTelegram()
    telegram_service.send_reply(bot, USUARIO, ChannelReply(text="resumen", draft_id="draft-1", draft_version=2))
    ((_, _, texto, botones),) = bot.sent
    assert texto == "resumen"
    assert [b["callback_data"] for b in botones[0]] == ["c:draft-1:2", "e:draft-1:2"]
    assert all(len(b["callback_data"].encode()) <= 64 for b in botones[0])


@pytest.mark.parametrize(
    ("data", "accion"), [("c:draft-1:2", ConfirmationAction.CONFIRM), ("e:draft-1:2", ConfirmationAction.CORRECT)]
)
def test_borrador_con_botones_al_tocar(orquestador, data, accion):
    """CA-34: el toque llega al orquestador, se responde el callback_query y se sacan los botones."""
    bot = FakeTelegram()
    telegram_service.handle_update(boton(data=data), bot)
    assert orquestador == [(f"telegram:{USUARIO}", Channel.TELEGRAM, "draft-1", 2, accion)]
    assert bot.calls[:2] == [("answer_callback_query", "cb-1"), ("remove_buttons", USUARIO, 20)]
    assert bot.sent == [("send_message", USUARIO, "accion", None)]


def test_error_del_orquestador_avisa_al_cliente(monkeypatch):
    def falla(message):
        raise RuntimeError("se rompió")

    monkeypatch.setattr(conversation_service, "handle_message", falla)
    bot = FakeTelegram()
    telegram_service.handle_update(mensaje(), bot)
    assert bot.sent == [("send_message", USUARIO, telegram_service.SOMETHING_FAILED, None)]


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
    client.send_message(USUARIO, "hola", telegram_service.draft_buttons("draft-1", 1))
    (request,) = requests
    assert request.url.path == "/bot123:token-de-prueba/sendMessage"
    assert b'"inline_keyboard"' in request.content


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


@pytest.mark.parametrize("headers", [{}, {"X-Telegram-Bot-Api-Secret-Token": "otro"}])
def test_webhook_con_secret_invalido(headers):
    """CA-30: sin header o con otro secret, 403."""
    assert api.post("/webhook/telegram", json=mensaje(), headers=headers).status_code == 403


def test_webhook_sin_secret_configurado(monkeypatch):
    """CA-30: sin TELEGRAM_WEBHOOK_SECRET el webhook está apagado."""
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "")
    get_settings.cache_clear()
    response = api.post("/webhook/telegram", json=mensaje(), headers={"X-Telegram-Bot-Api-Secret-Token": ""})
    assert response.status_code == 403
