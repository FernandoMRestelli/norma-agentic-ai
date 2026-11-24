from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from internal.config import get_settings
from internal.database.connection import SessionLocal, init_db
from internal.database.models import ConversationRecord, DraftRecord
from internal.schemas.order_schema import Channel, DraftStatus, IncomingMessage, MessageType
from internal.schemas.vision_schema import ImagenLeida, RenglonLeido
from internal.services import conversation_service, message_service
from internal.services.vision_service import Lectura


@pytest.fixture
def vision(monkeypatch):
    """Configura LLM_VISION_PROVIDER y reemplaza la descarga y la lectura de la imagen."""

    def configurar(resultado: ImagenLeida | None, falla: Exception | None = None):
        monkeypatch.setenv("LLM_VISION_PROVIDER", "huggingface")
        get_settings.cache_clear()
        monkeypatch.setattr(message_service, "download_media", lambda url: b"bytes-de-la-foto")

        def leer(imagen):
            if falla:
                raise falla
            return Lectura("huggingface", "modelo-de-prueba", resultado, "", 1.0)

        monkeypatch.setattr(message_service.vision_service, "leer_imagen", leer)

    yield configurar
    get_settings.cache_clear()


def foto(caption: str | None = None) -> IncomingMessage:
    return IncomingMessage(
        customer_id="telegram:1", channel=Channel.TELEGRAM, type=MessageType.IMAGE, media_url="telegram:X",
        text=caption,
    )


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
    id1, _, tenia_propuesta = conversation_service._abrir_conversacion("telegram:10", Channel.TELEGRAM)
    assert not tenia_propuesta

    with SessionLocal() as session:
        conv1 = session.get(ConversationRecord, id1)
        assert conv1.status == "abierta"
        assert conv1.customer_id == "telegram:10"

    # Pasan 24 horas y 1 minuto sin actividad
    reloj[0] = t0 + timedelta(hours=24, minutes=1)

    # Siguiente mensaje del mismo cliente: vence la anterior y abre una nueva (CA-28)
    id2, _, tenia_propuesta = conversation_service._abrir_conversacion("telegram:10", Channel.TELEGRAM)
    assert not tenia_propuesta
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

    id1 = conversation_service._abrir_conversacion("telegram:10", Channel.TELEGRAM).id

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
    id2, _, tenia_propuesta = conversation_service._abrir_conversacion("telegram:10", Channel.TELEGRAM)
    assert tenia_propuesta

    with SessionLocal() as session:
        draft_bd = session.scalar(select(DraftRecord).where(DraftRecord.draft_id == "d-test-1"))
        assert draft_bd.status == DraftStatus.DISCARDED.value


def test_intento_de_confirmar_propuesta_vencida_avisa_al_cliente(clean_db, monkeypatch):
    """RF-28: si el cliente intenta confirmar una propuesta que venció, recibe un aviso y 0 llamadas al LLM."""
    t0 = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
    reloj = [t0]
    monkeypatch.setattr(conversation_service, "_clock", lambda: reloj[0])

    # El cliente abrió conversación con una propuesta hace 30 horas
    id1 = conversation_service._abrir_conversacion("telegram:10", Channel.TELEGRAM).id
    with SessionLocal() as session:
        draft = DraftRecord(
            draft_id="d-test-exp",
            version=1,
            conversation_id=id1,
            customer_id="telegram:10",
            channel=Channel.TELEGRAM.value,
            items=[{"sku": "P-1", "quantity": 2}],
            status=DraftStatus.PENDING_CONFIRMATION.value,
        )
        session.add(draft)
        session.commit()

    reloj[0] = t0 + timedelta(hours=30)

    def _no_llamar_agente():
        raise AssertionError("No debería invocarse al agente/LLM ante confirmación de propuesta vencida")

    monkeypatch.setattr(conversation_service, "get_agent", _no_llamar_agente)

    # Ahora el cliente responde «dale»
    msg = IncomingMessage(customer_id="telegram:10", channel=Channel.TELEGRAM, text="dale")
    reply = conversation_service.handle_message(msg)

    assert "propuesta ya venció" in reply.text
    assert "24 h" in reply.text

    # La conversación anterior quedó vencida y la nueva abierta
    with SessionLocal() as session:
        conv1 = session.get(ConversationRecord, id1)
        assert conv1.status == "vencida"


def test_confirmacion_tras_vencimiento_sin_borrador_no_muestra_aviso(clean_db, monkeypatch):
    """Si no había propuesta pendiente y pasaron 25 h, 'ok' va al agente sin mostrar aviso de propuesta vencida."""
    t0 = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
    reloj = [t0]
    monkeypatch.setattr(conversation_service, "_clock", lambda: reloj[0])

    conversation_service._abrir_conversacion("telegram:10", Channel.TELEGRAM)
    # No se crea ningún borrador (el agente sólo había hecho una pregunta)
    reloj[0] = t0 + timedelta(hours=25)

    llamado_al_agente = []

    class MockAgent:
        def handle(self, **kwargs):
            llamado_al_agente.append(kwargs)
            from internal.schemas.agent_schema import AgentOutcome

            return AgentOutcome(kind="mensaje", message="¡Hola! ¿Qué necesitás?", cerrada=False)

    monkeypatch.setattr(conversation_service, "get_agent", lambda: MockAgent())

    msg = IncomingMessage(customer_id="telegram:10", channel=Channel.TELEGRAM, text="ok")
    reply = conversation_service.handle_message(msg)

    assert "propuesta ya venció" not in reply.text
    assert reply.text == "¡Hola! ¿Qué necesitás?"
    assert len(llamado_al_agente) == 1


def test_ok_tras_vencer_con_propuesta_ya_descartada_por_el_agente(clean_db, monkeypatch):
    """Si el agente ya había descartado el borrador antes de vencer, 'ok' tras 25 h va al agente sin aviso."""
    t0 = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
    reloj = [t0]
    monkeypatch.setattr(conversation_service, "_clock", lambda: reloj[0])

    id1 = conversation_service._abrir_conversacion("telegram:10", Channel.TELEGRAM).id
    with SessionLocal() as session:
        session.add(
            DraftRecord(
                draft_id="d-descartado-previo",
                version=1,
                conversation_id=id1,
                customer_id="telegram:10",
                channel=Channel.TELEGRAM.value,
                items=[],
                status=DraftStatus.DISCARDED.value,
            )
        )
        session.commit()

    reloj[0] = t0 + timedelta(hours=25)

    llamado_al_agente = []

    class MockAgent:
        def handle(self, **kwargs):
            llamado_al_agente.append(kwargs)
            from internal.schemas.agent_schema import AgentOutcome

            return AgentOutcome(kind="mensaje", message="hola", cerrada=False)

    monkeypatch.setattr(conversation_service, "get_agent", lambda: MockAgent())

    reply = conversation_service.handle_message(
        IncomingMessage(customer_id="telegram:10", channel=Channel.TELEGRAM, text="ok")
    )
    assert "propuesta ya venció" not in reply.text
    assert reply.text == "hola"
    assert len(llamado_al_agente) == 1


def test_aviso_propuesta_vencida_respeta_conversation_timeout_hours(clean_db, monkeypatch):
    """Minor 1: el mensaje de propuesta vencida refleja dinámicamente conversation_timeout_hours."""
    from internal.config import Settings

    settings = Settings(conversation_timeout_hours=12)
    monkeypatch.setattr(conversation_service, "get_settings", lambda: settings)

    assert "12 h" in conversation_service.proposal_expired_message()


def test_actividad_renueva_plazo_de_vencimiento(clean_db, monkeypatch):
    """RF-28: la actividad intermedia renueva updated_at y evita que venza prematuramente."""
    t0 = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
    reloj = [t0]
    monkeypatch.setattr(conversation_service, "_clock", lambda: reloj[0])

    id1 = conversation_service._abrir_conversacion("telegram:10", Channel.TELEGRAM).id

    # Actividad a las 20 h: renueva
    reloj[0] = t0 + timedelta(hours=20)
    conversation_service._touch_conversation(id1)

    # Mensaje a las 30 h (a 10 h de la última actividad): sigue abierta
    reloj[0] = t0 + timedelta(hours=30)
    id_actual, _, vencio = conversation_service._abrir_conversacion("telegram:10", Channel.TELEGRAM)
    assert not vencio
    assert id_actual == id1


def test_vencimiento_no_toca_borrador_de_otra_conversacion(clean_db, monkeypatch):
    """Al vencer una conversación abierta, no descarta ni reasigna borradores de otras conversaciones."""
    t0 = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
    reloj = [t0]
    monkeypatch.setattr(conversation_service, "_clock", lambda: reloj[0])

    # Conversación previa fallida que dejó un borrador pendiente asociado a ella
    id_fallida = conversation_service._abrir_conversacion("telegram:10", Channel.TELEGRAM).id
    conversation_service._close_conversation(id_fallida, "fallida")

    # Nueva conversación abierta sin borrador propio
    id_abierta = conversation_service._abrir_conversacion("telegram:10", Channel.TELEGRAM).id

    with SessionLocal() as session:
        session.add(
            DraftRecord(
                draft_id="d-de-fallida",
                version=1,
                conversation_id=id_fallida,
                customer_id="telegram:10",
                channel=Channel.TELEGRAM.value,
                items=[{"sku": "P-1", "quantity": 1}],
                status=DraftStatus.PENDING_CONFIRMATION.value,
            )
        )
        session.commit()

    # Pasan 25 h y vence id_abierta
    reloj[0] = t0 + timedelta(hours=25)
    id_nueva, _, tenia_propuesta = conversation_service._abrir_conversacion("telegram:10", Channel.TELEGRAM)
    assert id_nueva != id_abierta
    assert not tenia_propuesta

    with SessionLocal() as session:
        borrador = session.scalar(select(DraftRecord).where(DraftRecord.draft_id == "d-de-fallida"))
        assert borrador.conversation_id == id_fallida
        assert borrador.status == DraftStatus.PENDING_CONFIRMATION.value


def test_cancelacion_deterministica_descarta_borrador_y_cierra_conversacion(clean_db):
    """RF-26 / CA-23: 'cancelar' descarta el borrador y pasa la conversación a 'descartada' sin tokens."""
    id1 = conversation_service._abrir_conversacion("telegram:cancel-1", Channel.TELEGRAM).id

    # Creamos un borrador pendiente
    with SessionLocal() as session:
        draft = DraftRecord(
            draft_id="d-cancel-1",
            version=1,
            conversation_id=id1,
            customer_id="telegram:cancel-1",
            channel=Channel.TELEGRAM.value,
            items=[{"sku": "P-1", "quantity": 1}],
            status=DraftStatus.PENDING_CONFIRMATION.value,
        )
        session.add(draft)
        session.commit()

    msg = IncomingMessage(customer_id="telegram:cancel-1", channel=Channel.TELEGRAM, text="cancelar")
    reply = conversation_service.handle_message(msg)

    assert "cancelé la propuesta" in reply.text.lower()

    with SessionLocal() as session:
        conv = session.get(ConversationRecord, id1)
        assert conv.status == "descartada"
        assert conv.derivation_reason == "cliente_cancelo"

        draft_bd = session.scalar(select(DraftRecord).where(DraftRecord.draft_id == "d-cancel-1"))
        assert draft_bd.status == DraftStatus.DISCARDED.value

    # Siguiente mensaje abre una conversación nueva
    id2 = conversation_service._abrir_conversacion("telegram:cancel-1", Channel.TELEGRAM).id
    assert id2 != id1
    with SessionLocal() as session:
        assert session.get(ConversationRecord, id2).status == "abierta"


def test_no_gracias_no_cancela_propuesta(clean_db, monkeypatch):
    """«no gracias» no dispara la cancelación determinística (evita cancelar ante «¿sumás algo más?»)."""
    id1 = conversation_service._abrir_conversacion("telegram:nogracias", Channel.TELEGRAM).id
    with SessionLocal() as session:
        session.add(
            DraftRecord(
                draft_id="d-nogracias",
                version=1,
                conversation_id=id1,
                customer_id="telegram:nogracias",
                channel=Channel.TELEGRAM.value,
                items=[{"sku": "P-1", "quantity": 1}],
                status=DraftStatus.PENDING_CONFIRMATION.value,
            )
        )
        session.commit()

    llamado = []

    class MockAgent:
        def handle(self, **kwargs):
            llamado.append(kwargs)
            from internal.schemas.agent_schema import AgentOutcome

            return AgentOutcome(kind="mensaje", message="Perfecto, ¿confirmo entonces?", cerrada=False)

    monkeypatch.setattr(conversation_service, "get_agent", lambda: MockAgent())

    reply = conversation_service.handle_message(
        IncomingMessage(customer_id="telegram:nogracias", channel=Channel.TELEGRAM, text="no gracias")
    )
    assert reply.text == "Perfecto, ¿confirmo entonces?"
    assert len(llamado) == 1
    with SessionLocal() as session:
        draft_bd = session.scalar(select(DraftRecord).where(DraftRecord.draft_id == "d-nogracias"))
        assert draft_bd.status == DraftStatus.PENDING_CONFIRMATION.value


def test_cancelar_sin_borrador_pendiente_no_cierra_conversacion(clean_db, monkeypatch):
    """«cancelar» sin propuesta pendiente no cierra la conversación como descartada ni miente que canceló."""
    id1 = conversation_service._abrir_conversacion("telegram:cancel-vacio", Channel.TELEGRAM).id

    llamado = []

    class MockAgent:
        def handle(self, **kwargs):
            llamado.append(kwargs)
            from internal.schemas.agent_schema import AgentOutcome

            return AgentOutcome(kind="informacion", message="No tenés ningún pedido armado.", cerrada=False)

    monkeypatch.setattr(conversation_service, "get_agent", lambda: MockAgent())

    reply = conversation_service.handle_message(
        IncomingMessage(customer_id="telegram:cancel-vacio", channel=Channel.TELEGRAM, text="cancelar")
    )
    assert "cancelé la propuesta" not in reply.text.lower()
    assert len(llamado) == 1
    with SessionLocal() as session:
        assert session.get(ConversationRecord, id1).status == "abierta"


def test_derivada_a_humano_silencia_al_bot_durante_24h(clean_db, monkeypatch):
    """RF-27 / CA-21 / CA-22: tras derivación a humano, el bot no responde por 24 h y luego abre nueva conversación."""
    t0 = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
    reloj = [t0]
    monkeypatch.setattr(conversation_service, "_clock", lambda: reloj[0])

    id1 = conversation_service._abrir_conversacion("telegram:deriv-1", Channel.TELEGRAM).id
    conversation_service._close_conversation(id1, "derivada", reason="solicitud_humano")

    with SessionLocal() as session:
        conv = session.get(ConversationRecord, id1)
        assert conv.status == "derivada"
        assert conv.derivation_reason == "solicitud_humano"

    # Cliente escribe 2 horas después: el bot se mantiene en silencio (RF-27)
    reloj[0] = t0 + timedelta(hours=2)
    msg = IncomingMessage(customer_id="telegram:deriv-1", channel=Channel.TELEGRAM, text="hola")
    reply = conversation_service.handle_message(msg)
    assert reply.text == ""

    # Pasan 24 horas y 1 minuto: expira el silencio y se abre una nueva conversación
    reloj[0] = t0 + timedelta(hours=24, minutes=1)
    id2 = conversation_service._abrir_conversacion("telegram:deriv-1", Channel.TELEGRAM).id
    assert id2 != id1
    with SessionLocal() as session:
        assert session.get(ConversationRecord, id2).status == "abierta"


def test_audio_sigue_pidiendo_el_pedido_escrito_con_vision(vision):
    """RF-32: el audio todavía no se transcribe, aunque haya modelo de visión."""
    vision(None)
    message = IncomingMessage(customer_id="telegram:1", channel=Channel.TELEGRAM, type=MessageType.AUDIO,
                              media_url="telegram:X")
    assert conversation_service.handle_message(message).text == conversation_service.WRITE_IT_PLEASE


def test_imagen_leida_llega_al_agente_con_el_epigrafe(vision, monkeypatch):
    """CA-07: el texto de la imagen entra al agente marcado como `imagen leída`, con el epígrafe adelante."""
    vision(ImagenLeida(legible=True, tipo="lista_pedido", texto="5 shampoo pantene 400",
                       renglones=[RenglonLeido(texto="5 shampoo pantene 400", cantidad=5)]))
    recibido = {}

    class _Agente:
        def handle(self, **kwargs):
            recibido.update(kwargs)

            class _Outcome:
                message, cerrada, kind = "Te propongo…", False, "borrador"

            return _Outcome()

    monkeypatch.setattr(conversation_service, "get_agent", lambda: _Agente())
    reply = conversation_service.handle_message(foto("esto   para el lunes"))

    assert reply.text == "Te propongo…"
    esperado = "esto para el lunes\n[imagen leída: lista de pedido]\nRenglones:\n- 5 shampoo pantene 400"
    assert recibido["text"] == esperado
    # RF-102: la lectura viaja entera, para que los renglones salgan de ahí; RF-109: con el id que enlaza las trazas
    assert recibido["lectura"]["renglones"][0]["cantidad"] == 5
    assert recibido["mensaje_id"]


def test_imagen_sin_renglones_llega_como_texto_plano(vision, monkeypatch):
    """RF-110: una lectura sin renglones (una captura que no es un pedido) llega como hoy y sin lectura."""
    vision(ImagenLeida(legible=True, tipo="captura_chat", texto="hola, ¿abren el sábado?"))
    recibido = {}

    class _Agente:
        def handle(self, **kwargs):
            recibido.update(kwargs)

            class _Outcome:
                message, cerrada, kind = "Sí, de 9 a 13", False, "informacion"

            return _Outcome()

    monkeypatch.setattr(conversation_service, "get_agent", lambda: _Agente())
    conversation_service.handle_message(foto())

    assert recibido["text"] == "[imagen leída]\nhola, ¿abren el sábado?"
    assert recibido["lectura"] is None


@pytest.mark.parametrize(
    ("resultado", "falla"),
    [
        (ImagenLeida(legible=False, tipo="otro", texto=""), None),  # sin texto
        (None, None),  # el modelo no devolvió un JSON válido
        (None, RuntimeError("se cortó")),  # la descarga o el modelo fallaron
    ],
)
def test_imagen_sin_texto_util_no_llama_al_agente(vision, monkeypatch, resultado, falla):
    """Spec del agente §6: sin texto útil se le pide que lo escriba, sin llamar al LLM."""
    vision(resultado, falla)
    monkeypatch.setattr(conversation_service, "get_agent", lambda: pytest.fail("no tenía que llamar al agente"))
    assert conversation_service.handle_message(foto()).text == conversation_service.COULD_NOT_READ_IMAGE


def test_imagen_de_un_canal_sin_descarga(monkeypatch):
    """Hoy solo se bajan las fotos de Telegram: de otro canal se pide que lo escriba."""
    with pytest.raises(message_service.ImagenIlegible):
        message_service.download_media("https://example.com/foto.jpg")
