import pytest

from internal.schemas.order_schema import ConfirmationAction
from internal.services.palabras_clave import AYUDA_CONFIRMAR, interpretar_palabra_clave


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
        "dale",
        "sí",
        "si",
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
        "listo",
    ],
)
def test_texto_libre_no_es_palabra_clave(texto: str):
    """CA-17: cualquier texto que no sea exactamente la palabra clave devuelve None."""
    assert interpretar_palabra_clave(texto) is None


def test_ayuda_confirmar_contiene_instruccion_correcta():
    """F7: instrucción fija para mensajes sin botones interactivos."""
    assert AYUDA_CONFIRMAR == "Respondé OK para confirmar o CORREGIR para cambiar algo"
