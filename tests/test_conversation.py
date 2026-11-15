from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from internal.database.connection import SessionLocal, init_db
from internal.database.models import ConversationRecord, DraftRecord
from internal.schemas.order_schema import Channel, DraftStatus, IncomingMessage, MessageType
from internal.services import conversation_service


@pytest.mark.parametrize("tipo", [MessageType.AUDIO, MessageType.IMAGE])
def test_audio_e_imagen_piden_el_pedido_escrito(tipo):
    """CA-33 / RF-32: sin modelo de visión, audio e imagen reciben el pedido de escribirlo."""
    message = IncomingMessage(customer_id="telegram:1", channel=Channel.TELEGRAM, type=tipo, media_url="telegram:X")
    reply = conversation_service.handle_message(message)
    assert reply.text == conversation_service.WRITE_IT_PLEASE


# --- Mensajes seguidos agrupados (RF-74, docs/spec/toma-de-pedidos-por-texto.md) --------------------------------


class _Temporizador:
    """Un temporizador que no espera: el test decide cuándo vence."""

    creados: list["_Temporizador"] = []

    def __init__(self, espera, funcion, args=()):
        self.espera, self.funcion, self.args = espera, funcion, args
        self.cancelado = False
        _Temporizador.creados.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelado = True

    def vencer(self):
        if not self.cancelado:
            self.funcion(*self.args)


@pytest.fixture
def temporizadores():
    _Temporizador.creados = []
    return _Temporizador.creados


def _texto(texto: str, cliente: str = "telegram:1") -> IncomingMessage:
    return IncomingMessage(customer_id=cliente, channel=Channel.TELEGRAM, text=texto, external_id=texto[:5])


def test_agrupa_mensajes_seguidos(temporizadores):
    """CA-130: tres mensajes del mismo cliente dentro de la ventana llegan como un solo turno, con los tres textos."""
    from internal.services.agrupador import Agrupador

    ahora = [0.0]
    entregados = []
    agrupador = Agrupador(6, temporizador=_Temporizador, reloj=lambda: ahora[0])
    for segundo, texto in ((0, "Nordika"), (1, "2 sh keratina x900"), (3, "1 mascara argan 1k")):
        ahora[0] = segundo
        agrupador.recibir("telegram:1", _texto(texto), entregados.append)
    agrupador.recibir("telegram:2", _texto("hola", "telegram:2"), entregados.append)

    assert entregados == [] and agrupador.pendientes() == 2
    assert [t.cancelado for t in temporizadores] == [True, True, False, False]  # cada mensaje reinicia la espera
    for temporizador in temporizadores:
        temporizador.vencer()

    assert [m.customer_id for m in entregados] == ["telegram:1", "telegram:2"]
    assert entregados[0].text == "Nordika\n2 sh keratina x900\n1 mascara argan 1k"
    assert agrupador.pendientes() == 0


def test_la_espera_tiene_un_maximo(temporizadores):
    """RF-74: aunque el cliente siga escribiendo, la espera total no pasa del máximo."""
    from internal.services.agrupador import Agrupador

    ahora = [0.0]
    agrupador = Agrupador(6, maximo=20, temporizador=_Temporizador, reloj=lambda: ahora[0])
    for segundo in (0, 5, 10, 15, 19):
        ahora[0] = segundo
        agrupador.recibir("c", _texto(f"renglón {segundo}"), lambda m: None)

    assert [t.espera for t in temporizadores] == [6, 6, 6, 5, 1]


def test_un_error_al_entregar_no_rompe_el_agrupador(temporizadores, caplog):
    from internal.services.agrupador import Agrupador

    def falla(_):
        raise RuntimeError("no se pudo")

    agrupador = Agrupador(6, temporizador=_Temporizador)
    agrupador.recibir("c", _texto("2 shampoo pantene 400"), falla)
    temporizadores[0].vencer()

    assert agrupador.pendientes() == 0
    assert "falló la entrega de 1 mensajes" in caplog.text and "pantene" not in caplog.text


# --- Vencimiento de conversaciones a las 24 h (RF-28, CA-28) ----------------------------------------------------


@pytest.fixture
def clean_db():
    init_db()
    with SessionLocal() as session:
        session.execute(delete(DraftRecord))
        session.execute(delete(ConversationRecord))
        session.commit()
    yield
    with SessionLocal() as session:
        session.execute(delete(DraftRecord))
        session.execute(delete(ConversationRecord))
        session.commit()


def test_conversacion_abierta_vence_tras_24_horas(clean_db, monkeypatch):
    """CA-28: una conversación sin actividad durante 24 h queda vencida y el siguiente mensaje abre una nueva."""
    t0 = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
    reloj = [t0]
    monkeypatch.setattr(conversation_service, "_clock", lambda: reloj[0])

    # Primer mensaje: abre conversación 1
    id1, vencio = conversation_service._open_conversation_id("telegram:10", Channel.TELEGRAM)
    assert not vencio

    with SessionLocal() as session:
        conv1 = session.get(ConversationRecord, id1)
        assert conv1.status == "abierta"
        assert conv1.customer_id == "telegram:10"

    # Pasan 24 horas y 1 minuto sin actividad
    reloj[0] = t0 + timedelta(hours=24, minutes=1)

    # Siguiente mensaje del mismo cliente: vence la anterior y abre una nueva (CA-28)
    id2, vencio = conversation_service._open_conversation_id("telegram:10", Channel.TELEGRAM)
    assert vencio
    assert id2 != id1

    with SessionLocal() as session:
        conv1 = session.get(ConversationRecord, id1)
        conv2 = session.get(ConversationRecord, id2)
        assert conv1.status == "vencida"
        assert conv2.status == "abierta"


def test_vencimiento_descarta_borradores_pendientes(clean_db, monkeypatch):
    """RF-28: al vencer la conversación, cualquier borrador que estuviera pendiente pasa a descartado."""
    t0 = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
    reloj = [t0]
    monkeypatch.setattr(conversation_service, "_clock", lambda: reloj[0])

    id1, _ = conversation_service._open_conversation_id("telegram:10", Channel.TELEGRAM)

    # Creamos un borrador pendiente asociado a la conversación
    with SessionLocal() as session:
        draft = DraftRecord(
            draft_id="d-test-1",
            version=1,
            conversation_id=id1,
            customer_id="telegram:10",
            channel=Channel.TELEGRAM.value,
            items=[{"sku": "P-1", "quantity": 2}],
            status=DraftStatus.PENDING_CONFIRMATION.value,
        )
        session.add(draft)
        session.commit()

    # Pasan 25 horas
    reloj[0] = t0 + timedelta(hours=25)

    # Llega un mensaje nuevo: la conversación vence
    id2, vencio = conversation_service._open_conversation_id("telegram:10", Channel.TELEGRAM)
    assert vencio

    with SessionLocal() as session:
        draft_bd = session.scalar(select(DraftRecord).where(DraftRecord.draft_id == "d-test-1"))
        assert draft_bd.status == DraftStatus.DISCARDED.value


def test_intento_de_confirmar_propuesta_vencida_avisa_al_cliente(clean_db, monkeypatch):
    """RF-28: si el cliente intenta confirmar una propuesta que venció, recibe un aviso y 0 llamadas al LLM."""
    t0 = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
    reloj = [t0]
    monkeypatch.setattr(conversation_service, "_clock", lambda: reloj[0])

    # El cliente abrió conversación hace 30 horas
    id1, _ = conversation_service._open_conversation_id("telegram:10", Channel.TELEGRAM)
    reloj[0] = t0 + timedelta(hours=30)

    # Ahora el cliente responde «dale»
    msg = IncomingMessage(customer_id="telegram:10", channel=Channel.TELEGRAM, text="dale")
    reply = conversation_service.handle_message(msg)

    assert "propuesta ya venció" in reply.text
    assert "24 hs" in reply.text

    # La conversación anterior quedó vencida y la nueva abierta
    with SessionLocal() as session:
        conv1 = session.get(ConversationRecord, id1)
        assert conv1.status == "vencida"


def test_actividad_renueva_plazo_de_vencimiento(clean_db, monkeypatch):
    """RF-28: la actividad intermedia renueva updated_at y evita que venza prematuramente."""
    t0 = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
    reloj = [t0]
    monkeypatch.setattr(conversation_service, "_clock", lambda: reloj[0])

    id1, _ = conversation_service._open_conversation_id("telegram:10", Channel.TELEGRAM)

    # Actividad a las 20 h: renueva
    reloj[0] = t0 + timedelta(hours=20)
    conversation_service._touch_conversation(id1)

    # Mensaje a las 30 h (a 10 h de la última actividad): sigue abierta
    reloj[0] = t0 + timedelta(hours=30)
    id_actual, vencio = conversation_service._open_conversation_id("telegram:10", Channel.TELEGRAM)
    assert not vencio
    assert id_actual == id1


def test_expire_open_conversations_batch(clean_db, monkeypatch):
    """Vence todas las conversaciones abiertas que hayan superado el plazo en lote."""
    t0 = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
    reloj = [t0]
    monkeypatch.setattr(conversation_service, "_clock", lambda: reloj[0])

    # Dos conversaciones
    id_vieja, _ = conversation_service._open_conversation_id("telegram:viejo", Channel.TELEGRAM)

    reloj[0] = t0 + timedelta(hours=20)
    id_reciente, _ = conversation_service._open_conversation_id("telegram:reciente", Channel.TELEGRAM)

    # Pasa el reloj a t0 + 26 h (id_vieja tiene 26 h sin actividad, id_reciente tiene 6 h)
    reloj[0] = t0 + timedelta(hours=26)
    vencidas = conversation_service.expire_open_conversations()
    assert vencidas == 1

    with SessionLocal() as session:
        assert session.get(ConversationRecord, id_vieja).status == "vencida"
        assert session.get(ConversationRecord, id_reciente).status == "abierta"

