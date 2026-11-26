import pytest

from internal.schemas.order_schema import ConfirmationAction
from internal.services.palabras_clave import (
    es_aceptacion_explicita,
    es_aceptacion_pura,
    es_saludo,
    interpretar_palabra_clave,
)


@pytest.mark.parametrize(
    "texto",
    [
        "ok",
        "OK",
        "Ok",
        "  ok  ",
        "\nok\t",
        "confirmar",
        "CONFIRMAR",
        "Confirmar",
        "  confirmar  ",
        "  CONFIRMAR\n",
    ],
)
def test_ok_y_confirmar_confirman_sin_importar_mayusculas_ni_espacios(texto: str):
    """CA-18: 'ok' o 'confirmar' (sin mayúsculas ni espacios) devuelven ConfirmationAction.CONFIRM."""
    assert interpretar_palabra_clave(texto) == ConfirmationAction.CONFIRM


@pytest.mark.parametrize(
    "texto",
    [
        "corregir",
        "CORREGIR",
        "Corregir",
        "  corregir  ",
        "corregír",
        "CORREGÍR",
        "  corregír  ",
    ],
)
def test_corregir_con_tildes_y_espacios(texto: str):
    """CA-18: 'corregir' (incluso con tilde o espacios) devuelve ConfirmationAction.CORRECT."""
    assert interpretar_palabra_clave(texto) == ConfirmationAction.CORRECT


@pytest.mark.parametrize(
    "texto",
    [
        "dale, mandalo",
        "ok gracias",
        "ok dale",
        "ok ok",
        "confirmar pedido",
        "por favor confirmar",
        "corregir cantidad",
        "",
        "   ",
        "1",
        "bueno",
        "ok?",
        "sí...",
        "listo, ya está",
        "si puede ser",
    ],
)
def test_texto_libre_no_es_palabra_clave(texto: str):
    """CA-17: cualquier texto que no sea exactamente la palabra clave devuelve None."""
    assert interpretar_palabra_clave(texto) is None


@pytest.mark.parametrize(
    "texto", ["dale", "Dale", "sí", "si", "SI", "listo", "de una", "De  una", "mandale", "¡dale!", "listo.", "Sí!!"]
)
def test_aceptaciones_exactas(texto: str):
    """CA-120: una aceptación pura, como mensaje completo, confirma sin pasar por el LLM (RF-64, D-23)."""
    assert interpretar_palabra_clave(texto) == ConfirmationAction.CONFIRM


@pytest.mark.parametrize("texto", ["dale, pero 2", "sí, pero mandame 3", "listo, y sumale un shampoo", "dale gracias"])
def test_aceptacion_con_cambio_va_al_agente(texto: str):
    """CA-121: si el mensaje dice algo más que aceptar, decide el agente (BR-06)."""
    assert interpretar_palabra_clave(texto) is None


@pytest.mark.parametrize("texto", ["hola", "Hola, buen día!", "buenas tardes", "muchas gracias", "hola, ¿cómo va?"])
def test_saludos(texto: str):
    assert es_saludo(texto)


@pytest.mark.parametrize(
    "texto", ["hola, tenés shampoo?", "gracias, sumale 2 más", "", "2", "buenas, te hago un pedido"]
)
def test_un_saludo_con_algo_mas_no_es_solo_un_saludo(texto: str):
    assert not es_saludo(texto)


@pytest.mark.parametrize(
    "texto",
    [
        *("dale", "sí, dale", "si dale", "SÍ!!", "siii", "daleee", "ok gracias", "dale gracias", "ok ok", "ok si dale"),
        *("perfecto", "perfecto, mandalo", "buenísimo, confirmalo", "confirmo el pedido", "por favor confirmar"),
        *("listo, ya está", "de una", "está bien", "sí, está bien", "si esta bien", "sí, así está bien", "de acuerdo"),
        *("lo confirmo", "cerralo así nomás", "nada más, confirmalo", "bueno dale", "hola, dale"),
        *("👍", "dale 👍🏽", "dale 😊"),
        *("si por favor", "si gracias", "mandamelo", "si perfecto"),
        "dale\ngracias",
    ],
)
def test_aceptacion_pura(texto: str):
    """CA-187: aceptar con otras palabras, o con cortesía, sigue siendo solo aceptar (RF-111)."""
    assert es_aceptacion_pura(texto)


@pytest.mark.parametrize(
    "texto",
    [
        *("", "   ", "gracias", "hola", "bueno", "👎", "🤔", "[imagen leída]\ndale"),
        *("ok?", "¿dale?", "sí...", "dale…", "si puede ser"),  # duda o pregunta
        *("dale, pero 2", "dale pero que sean dos", "dale 3", "ok, la de 400", "si, sumale un shampoo"),  # cambia algo
        *("dale, sacá la crema", "sí, la de kilo", "dale, mandame una", "dale, más"),
        *("dale si llega el lunes", "confirmo si esta todo listo", "dale si es el de argan"),  # condición
        *("si esta todo listo mandalo", "dale pero aguantame al lunes que cobro", "ok, pago como siempre"),
        *("dale x2", "dale ２", "dale ½", "dale ②", "dale 2️⃣", "dale 两个", "dale 👎"),
        *("mandalo si podes", "dale, si está todo"),
        *("no", "no, dale", "obvio que no"),
        *("no, gracias", "todavía no confirmo", "dejame pensarlo", "bueno, ahora veo"),  # rechaza
    ],
)
def test_lo_que_no_es_una_aceptacion_pura(texto: str):
    """CA-187: lo que no se reconoce no registra; a lo sumo, se le pide otra confirmación (D-47)."""
    assert not es_aceptacion_pura(texto)


@pytest.mark.parametrize(
    ("texto", "explicita"),
    [
        *((t, True) for t in ("confirmalo", "confirmo", "sí, confirmalo así", "cerralo", "dale, así")),
        *((t, False) for t in ("dale", "sí", "ok", "perfecto", "confirmalo pero con 2", "¿lo confirmo?")),
    ],
)
def test_aceptacion_explicita(texto: str, explicita: bool):
    """CA-186: nombra la confirmación, así que no puede estar contestando otra pregunta."""
    assert es_aceptacion_explicita(texto) is explicita
