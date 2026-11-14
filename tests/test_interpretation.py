from internal.schemas.order_schema import Channel, IncomingMessage
from internal.services.interpretation_service import numerar_renglones
from internal.services.message_service import partir, prepare
from tests import datos_sinteticos


def test_prepare_text_normalizes_whitespace():
    """Saca los espacios de más, pero cada renglón sigue en su línea: así llega una lista de pedido (RF-65)."""
    message = IncomingMessage(customer_id="c-1", channel=Channel.WEB, text="  5 shampoos   Pantene\n\n por favor ")
    assert prepare(message) == "5 shampoos Pantene\npor favor"


LISTA = """Hola!! te paso el pedido

Nordika
2 sh keratina x900
1 mascara argan x 1k
belcor
3 oxi 20 vol 900
coloracion
7/3
Kerastase
2 shampoo
Lumine
1 gel
gracias!"""


def test_numera_renglones_por_marca():
    """CA-122: cada renglón de producto tiene su número y la marca de su encabezado, aunque venga mal escrita."""
    renglones = numerar_renglones(LISTA, datos_sinteticos.catalogo_mayorista())

    assert [(r["n"], r["texto"], r["marca"]) for r in renglones] == [
        (1, "2 sh keratina x900", "NORDIKA"),
        (2, "1 mascara argan x 1k", "NORDIKA"),
        (3, "3 oxi 20 vol 900", "BELKOR"),
        (4, "coloracion", "BELKOR"),  # sin cantidad: puede ser un título, lo decide el agente
        (5, "7/3", "BELKOR"),
        (6, "2 shampoo", None),  # «Kerastase» no es del catálogo: corta la marca anterior
        (7, "1 gel", "LUMINE"),
    ]
    assert all(renglon["estado"] == "sin_resolver" for renglon in renglones)


def test_un_solo_renglon_no_se_numera():
    productos = datos_sinteticos.catalogo_mayorista()
    assert numerar_renglones("2 shampoo keratina nordika 900", productos) == []
    assert numerar_renglones("hola\n\n", productos) == []


def test_la_numeracion_sigue_en_el_mensaje_siguiente():
    """RF-67: un segundo mensaje con más renglones no pisa los números del primero."""
    texto = "sumale\nKapris\n2 oxidante 20 vol x 90\n1 gel"
    renglones = numerar_renglones(texto, datos_sinteticos.catalogo_mayorista(), 8)
    assert [(r["n"], r["marca"]) for r in renglones] == [(8, "KAPRIS"), (9, "KAPRIS")]


def test_partir_no_corta_un_renglon():
    """CA: un mensaje más largo que el límite del canal sale en partes que terminan en un fin de línea (RF-70)."""
    lineas = [f"• {n} x Producto de prueba número {n} ($1000.00)" for n in range(1, 41)]
    texto = "Te armé el pedido.\n\n" + "\n".join(lineas)

    partes = partir(texto, 500)

    assert len(partes) > 1 and all(len(parte) <= 500 for parte in partes)
    assert "\n".join(partes).replace("\n\n", "\n") == texto.replace("\n\n", "\n")
    assert all(linea in "\n".join(partes).splitlines() for linea in lineas)
    assert partir("hola", 500) == ["hola"]
    assert partir("a" * 1200, 500) == ["a" * 500, "a" * 500, "a" * 200]
