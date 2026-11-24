"""Renglones del pedido desde la lectura de una imagen: encabezado, tachados, variantes y dudas de lectura."""

import pytest

from internal.agent import guardrails, nota
from internal.schemas.agent_schema import EntregarNotaInput, RespondToCustomerInput, ToolFailure
from internal.schemas.vision_schema import ImagenLeida, RenglonLeido, Variante, renglones_desde_lectura
from internal.tools import reply_tools


def _lectura(*renglones: RenglonLeido, **extra) -> ImagenLeida:
    return ImagenLeida(legible=True, tipo="lista_pedido", texto="x", renglones=list(renglones), **extra)


def test_renglones_salen_de_la_lectura():
    """Numerados desde `desde`, con producto y cantidad ya separados para la precarga."""
    lectura = _lectura(
        RenglonLeido(texto="Shampoo keratina La Puissance 1L x3", producto="Shampoo keratina", marca="La Puissance",
                     presentacion="1L", cantidad=3, cantidad_escrita="x3"),
        RenglonLeido(texto="Igora Royal con tonos", producto="Igora Royal"),
    )  # fmt: skip
    primero, segundo = renglones_desde_lectura(lectura, desde=4)
    assert (primero["n"], primero["marca"], primero["estado"]) == (4, "La Puissance", "sin_resolver")
    assert primero["buscar"] == {"texto": "Shampoo keratina 1L", "cantidad": 3, "tono": None}
    assert primero["cantidad_escrita"] == "x3"
    assert segundo["n"] == 5 and "buscar" not in segundo  # sin cantidad: queda para el LLM


def test_variantes_dan_un_renglon_cada_una():
    """Un renglón con varios colores da un renglón por color, con su cantidad."""
    lectura = _lectura(
        RenglonLeido(texto="Henna x90 - negro (2) - marrón (3)", producto="Henna", presentacion="x90",
                     variantes=[Variante(detalle="negro", cantidad=2), Variante(detalle="marrón", cantidad=3)]),
    )  # fmt: skip
    renglones = renglones_desde_lectura(lectura)
    assert [(r["texto"], r["buscar"]["cantidad"]) for r in renglones] == [("Henna x90 negro x2", 2),
                                                                          ("Henna x90 marrón x3", 3)]


def test_tabla_de_tonos():
    """Una tabla de tonos da un renglón por tono, con su cantidad."""
    lectura = _lectura(
        RenglonLeido(texto="Igora Royal 7.1/2 8.0/1", producto="Igora Royal",
                     variantes=[Variante(detalle="", tono="7.1", cantidad=2),
                                Variante(detalle="", tono="8.0", cantidad=1)]),
    )  # fmt: skip
    assert [(r["buscar"]["tono"], r["buscar"]["cantidad"]) for r in renglones_desde_lectura(lectura)] == [
        ("7.1", 2),
        ("8.0", 1),
    ]


def test_linea_que_sigue_hereda_producto_y_marca():
    """La línea que sigue a la de arriba hereda su producto y su marca."""
    lectura = _lectura(
        RenglonLeido(texto="Cepillo Dompel Enlace verde (3)", producto="Cepillo", marca="Dompel", cantidad=3),
        RenglonLeido(texto="Maya negro (2)", producto="Maya negro", cantidad=2, sigue_a=1),
        RenglonLeido(texto="Algo", producto="Algo", cantidad=1, sigue_a=9),  # no existe: se ignora
    )
    _, maya, algo = renglones_desde_lectura(lectura)
    assert maya["marca"] == "Dompel" and maya["buscar"]["texto"] == "Cepillo Maya negro"
    assert algo["buscar"]["texto"] == "Algo"


def test_linea_que_sigue_es_otro_modelo():
    """Hereda el tipo de producto y la marca, no el modelo de la línea de arriba."""
    lectura = _lectura(
        RenglonLeido(texto="Cepillo Dompel Enlace verde (3)", producto="Cepillo Enlace", marca="Dompel", cantidad=3),
        RenglonLeido(texto="Maya negro (2)", producto="Maya", sigue_a=1,
                     variantes=[Variante(detalle="negro", cantidad=2)]),
    )  # fmt: skip
    _, maya = renglones_desde_lectura(lectura)
    assert (maya["buscar"]["texto"], maya["marca"]) == ("Cepillo Maya", "Dompel")


def test_linea_que_sigue_no_repite_el_producto():
    """Si la línea repite el producto de arriba («Cepillos»), no queda «Cepillos Cepillos»."""
    lectura = _lectura(
        RenglonLeido(texto="Cepillos Dompel verde (3)", producto="Cepillos", marca="Dompel", cantidad=3),
        RenglonLeido(texto="Maya - fucsia (2)", producto="Cepillos Maya", sigue_a=1,
                     variantes=[Variante(detalle="fucsia", cantidad=2)]),
    )  # fmt: skip
    _, fucsia = renglones_desde_lectura(lectura)
    assert fucsia["buscar"]["texto"] == "Cepillos Maya" and fucsia["texto"] == "Cepillos Maya fucsia x2"


def test_encabezado_y_tachados_no_son_renglones():
    """El encabezado y lo tachado no dan renglones."""
    lectura = _lectura(
        RenglonLeido(texto="3 shampoo", producto="shampoo", cantidad=3),
        encabezado={"cliente": "Peluquería Sol", "fecha": "25/11"},
        tachados=["5 guantes nitrilo M"],
    )
    assert [r["texto"] for r in renglones_desde_lectura(lectura)] == ["3 shampoo"]


def test_palabra_tachada_y_reescrita_no_tacha_el_renglon():
    """«Capa [tachado] descartable x1»: el agente no tiene que ver «[tachado]» y sacar el renglón."""
    lectura = _lectura(
        RenglonLeido(texto="Capa [tachado] descartable x1", producto="Capa [tachado] descartable", cantidad=1)
    )
    (renglon,) = renglones_desde_lectura(lectura)
    assert renglon["texto"] == "Capa descartable x1" and renglon["buscar"]["texto"] == "Capa descartable"
    assert "tachado" not in lectura.como_texto_para_el_agente().split("Renglones:")[1]


def test_cantidad_escrita_y_unidad_quedan_en_el_renglon():
    """La lectura conserva cómo está escrita la cantidad, para dudar entre unidades y cajas."""
    (renglon,) = renglones_desde_lectura(
        _lectura(RenglonLeido(texto="Ampollas caviar x24", producto="Ampollas caviar", cantidad=24,
                              cantidad_escrita="x24"))
    )  # fmt: skip
    assert renglon["cantidad_escrita"] == "x24" and "unidad" not in renglon


def _respuesta(**datos) -> RespondToCustomerInput:
    return RespondToCustomerInput.model_validate({"kind": "propuesta", "message": "Te armé lo pedido.", **datos})


RENGLONES = [
    {"n": 1, "texto": "Shampoo x3", "marca": None, "estado": "sin_resolver"},
    {"n": 2, "texto": "Capa x7", "marca": None, "estado": "sin_resolver", "duda": "el 7 puede ser un 1"},
]


def test_duda_de_lectura_se_confirma():
    """Un renglón con duda no entra a la propuesta sin preguntarle al cliente."""
    proponer = _respuesta(propuesta=[{"sku": "SH", "quantity": 3, "renglon": 1}, {"sku": "CAPA", "quantity": 7,
                                                                                  "renglon": 2}])
    with pytest.raises(ToolFailure) as falla:
        reply_tools.cubrir_renglones(proponer, RENGLONES)
    assert falla.value.code == "duda_de_lectura"
    # Con los intentos agotados se tolera, como los renglones sin cuenta
    assert reply_tools.cubrir_renglones(proponer, RENGLONES, tolerar=True)[1]["estado"] == "resuelto"

    preguntar = _respuesta(
        propuesta=[{"sku": "SH", "quantity": 3, "renglon": 1}], pendientes=[{"renglon": 2, "opciones": ["CAPA"]}]
    )
    capa = reply_tools.cubrir_renglones(preguntar, RENGLONES)[1]
    assert capa["estado"] == "pendiente" and "duda" not in capa  # ya se le preguntó


def test_la_nota_del_sub_loop_no_resuelve_un_renglon_dudoso():
    data = EntregarNotaInput.model_validate(
        {"resueltos": [{"renglon": 2, "sku": "CAPA", "cantidad": 7}], "pendientes": [], "afuera": [],
         "observaciones": ""}
    )  # fmt: skip
    error = nota.validar(data, [2], {"CAPA"}, con_duda={2})
    assert error["code"] == "duda_de_lectura"
    assert nota.validar(data, [2], {"CAPA"}) is None


@pytest.mark.parametrize(
    ("mensaje", "rechaza"),
    [
        ("Te armé lo claro.\n• Renglón 9: ¿de 1 L o de 90 ml?", True),
        ("Me quedan dudas:\n• 1 y 2: ¿qué producto dice?", True),
        ("Me quedan dudas:\n• Las ampollas caviar x24: ¿24 ampollas o 24 cajas?", False),
    ],
)
def test_la_respuesta_no_nombra_numeros_de_renglon(mensaje, rechaza):
    """La respuesta no nombra números de renglón: el cliente no los tiene."""
    reglas = [r.regla for r in guardrails.validar_salida(mensaje, "aclaracion")]
    assert ("numero_de_renglon" in reglas) is rechaza


# --- Un turno completo con la lectura (modelo guionado) ---

from decimal import Decimal  # noqa: E402

from internal.schemas.catalog_schema import Product  # noqa: E402
from internal.services import interpretation_service, sheets_service  # noqa: E402
from tests.test_agent_graph import CLIENTE, _agente, _ai, _call, _resultados, base_limpia  # noqa: E402,F401

MASCARAS = ["MAS-ARG-250", "MAS-ARG-1K"]


@pytest.fixture
def catalogo(monkeypatch):
    productos = [
        Product(sku="SH-KER-900", name="Shampoo Keratina", brand="Marca Uno", presentation="900 ml",
                price=Decimal("4500"), stock=20),
        Product(sku="MAS-ARG-250", name="Máscara Argan", brand="Marca Uno", presentation="250 g",
                price=Decimal("3900"), stock=20),
        Product(sku="MAS-ARG-1K", name="Máscara Argan", brand="Marca Uno", presentation="1 kg",
                price=Decimal("11200"), stock=20),
        Product(sku="GUA-NIT-M", name="Guantes Nitrilo", brand="Marca Dos", presentation="M",
                price=Decimal("700"), stock=20),
    ]  # fmt: skip
    monkeypatch.setattr(sheets_service, "get_catalog", lambda: productos)
    return productos


def _hoja(duda: str | None = None) -> ImagenLeida:
    return _lectura(
        RenglonLeido(texto="Shampoo keratina 900 x2", producto="Shampoo keratina", presentacion="900", cantidad=2,
                     cantidad_escrita="x2", duda=duda),
        RenglonLeido(texto="Mascara argan x3", producto="Mascara argan", cantidad=3, cantidad_escrita="x3"),
        encabezado={"cliente": "Peluquería Sol", "fecha": "25/11"},
        tachados=["5 guantes nitrilo M"],
    )  # fmt: skip


def _responder(propuesta=((1, "SH-KER-900", 2),), pendientes=((2, MASCARAS),), n=""):
    return _ai(
        {
            **_call(
                "respond_to_customer",
                kind="propuesta" if propuesta else "aclaracion",
                message="Te armé lo que estaba claro. ¿La máscara argan la querés de 250 o de 1 kilo?",
                propuesta=[{"sku": sku, "quantity": c, "renglon": r} for r, sku, c in propuesta] or None,
                pendientes=[{"renglon": r, "opciones": list(o)} for r, o in pendientes],
            ),
            "id": f"call-respond{n}",
        }
    )


def _turno_con_imagen(agent, lectura: ImagenLeida | dict):
    datos = lectura.model_dump() if isinstance(lectura, ImagenLeida) else lectura
    texto = lectura.como_texto_para_el_agente() if isinstance(lectura, ImagenLeida) else "2 shampoo\n3 mascara"
    return agent.handle("1", text=texto, origen="imagen", lectura=datos, **CLIENTE)


def test_turno_con_imagen_arma_los_renglones_desde_la_lectura(catalogo, monkeypatch):
    """Sin volver a partir el texto, sin el encabezado ni lo tachado, y con la precarga."""

    def no_numerar(*args, **kwargs):
        raise AssertionError("un mensaje con lectura no se vuelve a partir")

    monkeypatch.setattr(interpretation_service, "numerar_renglones", no_numerar)
    agent, modelo = _agente(_responder())

    resultado = _turno_con_imagen(agent, _hoja())

    assert resultado.kind == "propuesta"
    estado = agent.graph.get_state({"configurable": {"thread_id": "1"}}).values
    assert [(r["n"], r["texto"], r["estado"]) for r in estado["renglones"]] == [
        (1, "Shampoo keratina 900 x2", "resuelto"),
        (2, "Mascara argan x3", "pendiente"),
    ]
    assert estado["lectura"] is None  # vive solo en el turno
    (busqueda,) = [n for n in estado["notas_turno"] if n["tipo"] == "busqueda"]
    assert [(b["n"], b["cantidad"]) for b in busqueda["buscados"]] == [(1, 2), (2, 3)]
    prompt = "\n".join(str(m.content) for m in modelo.recibidos[0])
    assert "No es pedido (tachado en la hoja): 5 guantes nitrilo M" in prompt


def test_duda_de_lectura_vuelve_al_modelo_en_el_turno(catalogo):
    """Si propone un renglón dudoso, la tool le pide preguntar; la segunda respuesta lo pregunta."""
    agent, _ = _agente(
        _responder(),
        _responder(propuesta=(), pendientes=((1, ["SH-KER-900"]), (2, MASCARAS)), n="-2"),
    )

    resultado = _turno_con_imagen(agent, _hoja(duda="el 2 puede ser un 3"))

    assert resultado.kind == "aclaracion"
    primero, segundo = _resultados(agent, "respond_to_customer")
    assert primero["error"]["code"] == "duda_de_lectura" and segundo["ok"]


def test_lectura_que_no_valida_usa_el_camino_del_texto(catalogo):
    """Si la lectura no valida, los renglones salen del texto, como antes."""
    agent, _ = _agente(_responder())

    _turno_con_imagen(agent, {"renglones": "no es una lista"})

    estado = agent.graph.get_state({"configurable": {"thread_id": "1"}}).values
    assert [r["texto"] for r in estado["renglones"]] == ["2 shampoo", "3 mascara"]


# --- Cajas o unidades ---

from internal.tools import catalog_tools  # noqa: E402

AMPOLLA = Product(sku="AMP-CAV", name="FIDELITE CAVIAR AMPOLLA 15ML X 12 UNID", brand="FIDELITE",
                  price=Decimal("10000"), stock=80)  # fmt: skip


@pytest.mark.parametrize(
    ("texto", "cantidad", "unidad", "pregunta"),
    [
        ("ampollas caviar", 24, None, True),  # 24 cajas o 2 cajas: no se sabe
        ("ampollas caviar", 2, None, False),  # menos que una caja: son cajas
        ("ampollas caviar", 30, None, False),  # no es múltiplo de 12: son cajas
        ("ampollas caviar 24 u", 24, None, False),  # el cliente dijo unidades
        ("ampollas caviar", 24, "cajas", False),  # la lectura dice la unidad
    ],
)
def test_cantidad_que_puede_ser_de_cajas_o_de_unidades(monkeypatch, texto, cantidad, unidad, pregunta):
    monkeypatch.setattr(sheets_service, "get_catalog", lambda: [AMPOLLA])
    (resultado,) = catalog_tools.buscar_renglones(
        [{"n": 1, "texto": texto, "marca": None, "cantidad": cantidad, "unidad": unidad}]
    )
    assert (resultado["estado"] == "varias") is pregunta
    if pregunta:
        assert resultado["difieren_en"] == ["cajas o unidades"] and "caja de 12" in resultado["_hint"]
