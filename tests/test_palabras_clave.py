import pytest

from internal.schemas.order_schema import ConfirmationAction
from internal.services.palabras_clave import es_saludo, interpretar_palabra_clave


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
