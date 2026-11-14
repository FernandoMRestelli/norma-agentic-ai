import pytest

from internal.schemas.order_schema import Channel, IncomingMessage, MessageType
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
