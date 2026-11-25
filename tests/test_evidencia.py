"""La propuesta sale de lo que el LLM vio, sin elegir por el cliente ni cambiar sus cantidades (RF-112 a RF-114)."""

import json
from decimal import Decimal

import pytest
from langchain_core.messages import ToolMessage

from internal.agent import evidencia, sub_loop, tool_registry
from internal.agent.evidencia import Evidencia
from internal.config import get_settings
from internal.schemas.catalog_schema import Product
from internal.schemas.order_schema import Channel, OrderDraft, OrderItem
from internal.services import interpretation_service, order_service, sheets_service
from internal.tools import order_tools
from tests.test_agent_graph import CLIENTE, _agente, _ai, _call, _resultados, base_limpia  # noqa: F401

SH_300, SH_900, MAS_250, MAS_1K = "SH-KER-300", "SH-KER-900", "MAS-ARG-250", "MAS-ARG-1K"
JABON, AMPOLLA, GEL = "JAB-GLI", "AMP-REP-12", "GEL-FIJ-500"


@pytest.fixture(autouse=True)
def catalogo(monkeypatch):
    productos = [
        Product(sku=sku, name=nombre, brand="Marca Uno", presentation=medida, price=Decimal("1000"), stock=stock)
        for sku, nombre, medida, stock in (
            (SH_300, "Shampoo Keratina", "300 ml", 50),
            (SH_900, "Shampoo Keratina", "900 ml", 50),
            (MAS_250, "Máscara Argan", "250 g", 2),
            (MAS_1K, "Máscara Argan", "1 kg", 50),
            (JABON, "Jabón Glicerina", "100 g", 50),
            (AMPOLLA, "Ampolla Reparadora 15ml x 12 unid", None, 50),
            (GEL, "Gel Fijador", "500 g", 50),
        )
    ]
    monkeypatch.setattr(sheets_service, "get_catalog", lambda: productos)
    return productos


def _proponer(*lineas, mensaje="Te lo armé. ¿Lo confirmo?", kind="propuesta", **extra):
    """Cada línea es (sku, cantidad) o (sku, cantidad, renglón)."""
    propuesta = [{"sku": sku, "quantity": q, **({"renglon": n[0]} if n else {})} for sku, q, *n in lineas]
    return _ai(_call("respond_to_customer", kind=kind, message=mensaje, propuesta=propuesta or None, **extra))


def _preguntar(*skus, mensaje="¿Cuál te mando, la chica o la grande?", **extra):
    return _ai(_call("respond_to_customer", kind="aclaracion", message=mensaje, options=list(skus), **extra))


def _buscar(query, **extra):
    return _ai(_call("leer_skill", nombre="tomar_pedido"), _call("search_products", query=query, **extra))


def _codigos(agent) -> list[str | None]:
    """El código de error de cada respuesta del turno, o None si se aceptó."""
    return [None if r["ok"] else r["error"]["code"] for r in _resultados(agent, "respond_to_customer")]


# --- RF-112: lo que no se vio no se propone -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mala",
    [
        lambda: _proponer((GEL, 7)),
        lambda: _proponer((SH_900, 2), (GEL, 1)),  # uno bueno no tapa al otro
        lambda: _preguntar(GEL),
        lambda: _proponer((SH_900, 2), options=[GEL]),
    ],
)
def test_sku_que_no_se_busco(mala):
    """CA-189: existe y tiene stock, pero nadie lo buscó: no llega al cliente ni a la base, aunque insista."""
    agent, _ = _agente(_buscar("shampoo keratina 900"), mala(), mala(), mala(), _proponer((SH_900, 2)))

    resultado = agent.handle("1", text="2 shampoo keratina 900", **CLIENTE)

    assert _codigos(agent) == ["sku_no_buscado"] * 3 + [None]
    assert _resultados(agent, "respond_to_customer")[0]["error"]["details"]["skus"] == [GEL]
    assert [item.sku for item in resultado.draft.items] == [SH_900]


def test_sku_no_buscado_en_pendientes_de_un_pedido_numerado():
    """CA-189: lo que se le ofrece al cliente en un renglón también sale de una búsqueda."""
    agent, _ = _agente(
        _proponer((SH_900, 2, 1), pendientes=[{"renglon": 2, "opciones": [GEL]}]),
        _proponer((SH_900, 2, 1), pendientes=[{"renglon": 2, "opciones": [MAS_250, MAS_1K]}]),
    )
    agent.handle("1", text="2 shampoo keratina 900\n1 mascara argan", **CLIENTE)
    assert _codigos(agent) == ["sku_no_buscado", None]


def test_un_rechazo_no_cambia_la_propuesta_vigente():
    """CA-189: la propuesta vigente y `drafts` quedan como estaban."""
    agent, _ = _agente(
        _buscar("shampoo keratina 900"),
        _proponer((SH_900, 2)),
        _ai(_call("leer_skill", nombre="tomar_pedido")),
        _proponer((GEL, 1)),
        _ai(_call("respond_to_customer", kind="informacion", message="¿Qué más te sumo?")),
    )
    vigente = agent.handle("1", text="2 shampoo keratina 900", **CLIENTE).draft
    assert agent.handle("1", text="y sumale algo", **CLIENTE).draft == vigente


@pytest.mark.parametrize("tool", ["opciones", "vigente", "get_product", "check_stock"])
def test_sku_que_el_modelo_ya_tenia(tool):
    """CA-190: lo que está en su prompt o devolvió una tool no se vuelve a buscar."""
    previo = {
        "opciones": (_buscar("shampoo keratina"), _preguntar(SH_300, SH_900)),
        "vigente": (_buscar("shampoo keratina 900"), _proponer((SH_900, 1))),
        "get_product": (_buscar("gel fijador"), _proponer((GEL, 1))),
        "check_stock": (_buscar("gel fijador"), _proponer((GEL, 1))),
    }[tool]
    leer = [_call("leer_skill", nombre="tomar_pedido")]
    if tool == "get_product":
        leer.append(_call("get_product", sku=SH_900))
    if tool == "check_stock":
        leer.append(_call("check_stock", sku=SH_900, quantity=2))
    agent, _ = _agente(*previo, _ai(*leer), _proponer((SH_900, 2)))
    agent.handle("1", text="hola, te hago un pedido", **CLIENTE)

    resultado = agent.handle("1", text="mandame dos del de novecientos", **CLIENTE)

    assert _codigos(agent)[-1] is None
    assert [(item.sku, item.quantity) for item in resultado.draft.items] == [(SH_900, 2)]


def test_los_pedidos_anteriores_son_evidencia(monkeypatch):
    """CA-190: BR-11, «lo de siempre» sale del historial que ve en el prompt, sin buscar."""
    anterior = {"fecha": "2026-11-02", "lineas": [{"sku": JABON, "name": "Jabón Glicerina", "cantidad": 4}]}
    monkeypatch.setattr(order_tools, "pedidos_anteriores", lambda *a, **k: [anterior])
    monkeypatch.setattr(order_service, "skus_comprados", lambda *a, **k: frozenset({JABON}))
    agent, _ = _agente(_ai(_call("leer_skill", nombre="tomar_pedido")), _proponer((JABON, 4)))
    assert agent.handle("1", text="mandame lo de siempre", **CLIENTE).kind == "propuesta"


def test_un_error_no_es_evidencia():
    """CA-191: `sku_inexistente` y `stock_insuficiente` repiten el SKU que mandó el modelo; eso no lo vuelve visto."""
    agent, _ = _agente(
        _ai(_call("leer_skill", nombre="tomar_pedido")),
        _proponer((GEL, 999)),  # stock_insuficiente, con el SKU en `lineas`
        _proponer((GEL, 1)),
        _preguntar(mensaje="¿Qué producto buscás?"),
    )
    agent.handle("1", text="quiero algo lindo", **CLIENTE)
    assert _codigos(agent) == ["stock_insuficiente", "sku_no_buscado", None]


def test_lo_visto_en_otro_turno_no_alcanza():
    """CA-189: los resultados de turnos anteriores ya no están en el contexto: proponerlos es de memoria."""
    agent, _ = _agente(
        _buscar("gel fijador"),
        _ai(_call("respond_to_customer", kind="informacion", message="Sí, lo trabajamos. ¿Te lo sumo?")),
        _ai(_call("leer_skill", nombre="tomar_pedido")),
        _proponer((GEL, 1)),
        _buscar("gel fijador"),
        _proponer((GEL, 1), mensaje="Ahí va. ¿Lo confirmo?"),
    )
    agent.handle("1", text="¿trabajan gel fijador?", **CLIENTE)
    agent.handle("1", text="bueno, mandame uno", **CLIENTE)
    assert _codigos(agent) == [None, "sku_no_buscado", None]


def test_sin_evidencia_falla_cerrado():
    """CA-189: un contexto sin evidencia no propone nada."""
    ctx = tool_registry.TurnContext(customer_id="c", channel=Channel.WEB, disponibles=["respond_to_customer"])
    args = {"kind": "propuesta", "message": "¿Va?", "propuesta": [{"sku": GEL, "quantity": 1}]}
    assert tool_registry.execute("respond_to_customer", args, ctx)["error"]["code"] == "sku_no_buscado"


# --- RF-113: entre varias opciones elige el cliente -----------------------------------------------------------------


def test_varias_opciones_las_elige_el_cliente():
    """CA-192: en un pedido de una línea, el modelo no elige la medida; cuando el cliente contesta, sí se propone."""
    agent, _ = _agente(
        _buscar("shampoo keratina", quantity=2),
        _proponer((SH_900, 2)),
        _preguntar(SH_300, SH_900),
        _ai(_call("leer_skill", nombre="tomar_pedido")),
        _proponer((SH_900, 2), mensaje="Van los 2 de 900. ¿Lo confirmo?"),
    )
    primero = agent.handle("1", text="2 shampoo keratina", **CLIENTE)
    assert _codigos(agent) == ["opcion_sin_elegir", None]
    assert primero.draft is None

    segundo = agent.handle("1", text="el de 900", **CLIENTE)
    assert [item.sku for item in segundo.draft.items] == [SH_900]


def test_varias_en_un_renglon():
    """CA-192: en un pedido numerado el error nombra el renglón, y preguntarlo lo resuelve."""
    agent, _ = _agente(
        _proponer((SH_900, 2, 1), (MAS_1K, 1, 2)),
        _proponer((SH_900, 2, 1), pendientes=[{"renglon": 2, "opciones": [MAS_250, MAS_1K]}]),
    )
    agent.handle("1", text="2 shampoo keratina 900\n1 mascara argan", **CLIENTE)
    assert _codigos(agent) == ["opcion_sin_elegir", None]
    error = _resultados(agent, "respond_to_customer")[0]["error"]
    assert error["details"]["skus"] == [MAS_1K] and "renglón 2" in error["message"]


@pytest.mark.parametrize(
    "precisa",
    [
        lambda: _call("search_products", query="mascara argan 1 kg"),
        lambda: _call("buscar_renglones", renglones=[{"n": 2, "texto": "mascara argan 1 kg", "cantidad": 1}]),
    ],
)
def test_una_busqueda_mas_precisa_define(precisa):
    """CA-192: si el cliente dio más datos que los de la primera búsqueda, buscar con todos alcanza."""
    agent, _ = _agente(_ai(precisa()), _proponer((SH_900, 2, 1), (MAS_1K, 1, 2)))
    agent.handle("1", text="2 shampoo keratina 900\n1 mascara argan la grande", **CLIENTE)
    assert _codigos(agent) == [None]


def test_un_aproximado_se_confirma():
    """CA-193: «shampoo keratina 500» no existe; lo parecido se pregunta antes de proponerlo."""
    agent, _ = _agente(_buscar("shampoo keratina 500"), _proponer((SH_900, 2)), _preguntar(SH_300, SH_900))
    agent.handle("1", text="2 shampoo keratina 500", **CLIENTE)
    assert _codigos(agent) == ["opcion_sin_elegir", None]


def test_el_producto_de_otro_renglon():
    """CA-194: los dos productos se vieron, pero cruzados: el renglón 1 pidió shampoo y el 2, jabón."""
    agent, _ = _agente(_proponer((JABON, 2, 1), (SH_900, 1, 2)), _proponer((SH_900, 2, 1), (JABON, 1, 2)))
    resultado = agent.handle("1", text="2 shampoo keratina 900\n1 jabon glicerina", **CLIENTE)
    assert _codigos(agent) == ["opcion_sin_elegir", None]
    assert {item.sku: item.quantity for item in resultado.draft.items} == {SH_900: 2, JABON: 1}


def test_uno_de_cada():
    """CA-195: proponer todas las coincidencias no es elegir; proponer algunas, sí."""
    agent, _ = _agente(_buscar("shampoo keratina"), _proponer((SH_300, 1), (SH_900, 1)))
    agent.handle("1", text="mandame uno de cada shampoo keratina", **CLIENTE)
    assert _codigos(agent) == [None]


def test_lo_de_siempre_entre_varias(monkeypatch):
    """BR-11: con varias medidas, «el de siempre» es la que el cliente ya llevó; sin decirlo, se pregunta."""
    anterior = {"fecha": "2026-11-02", "lineas": [{"sku": SH_900, "name": "Shampoo Keratina", "cantidad": 2}]}
    monkeypatch.setattr(order_tools, "pedidos_anteriores", lambda *a, **k: [anterior])
    monkeypatch.setattr(order_service, "skus_comprados", lambda *a, **k: frozenset({SH_900}))
    for texto, esperado in (("el shampoo keratina de siempre", [None]), ("un shampoo keratina", ["opcion_sin_elegir"])):
        agent, _ = _agente(_buscar("shampoo keratina"), _proponer((SH_900, 2)), _preguntar(SH_300, SH_900))
        agent.handle("1", text=texto, **CLIENTE)
        assert _codigos(agent)[:1] == esperado


def test_insistir_se_acepta_y_queda_registrado(caplog):
    """CA-196: a la tercera se acepta, para no cortar un turno por una regla que puede no entender al cliente."""
    agent, _ = _agente(_buscar("shampoo keratina"), *[_proponer((SH_900, 2)) for _ in range(3)])
    resultado = agent.handle("1", text="2 shampoo keratina", **CLIENTE)
    assert _codigos(agent) == ["opcion_sin_elegir", "opcion_sin_elegir", None]
    assert resultado.kind == "propuesta"
    assert "después de insistir (motivo=opcion_sin_elegir)" in caplog.text


def test_el_freno_vale_por_turno():
    """CA-196: los rechazos de un turno anterior no cuentan en el siguiente."""
    agent, _ = _agente(
        _buscar("shampoo keratina"), _proponer((SH_900, 2)), _proponer((SH_900, 2)),
        _ai(_call("respond_to_customer", kind="informacion", message="¿Qué medida buscás?")),
        _buscar("mascara argan"), _proponer((MAS_1K, 1)), _preguntar(MAS_250, MAS_1K),
    )  # fmt: skip
    agent.handle("1", text="2 shampoo keratina", **CLIENTE)
    agent.handle("1", text="mejor una mascara argan", **CLIENTE)
    assert _codigos(agent) == ["opcion_sin_elegir"] * 2 + [None, "opcion_sin_elegir", None]


def test_en_el_cierre_se_acepta():
    """RNF-16: sin presupuesto el modelo ya no puede buscar; no se gasta el cierre en pedirle que busque."""
    ctx = tool_registry.TurnContext(
        customer_id="c", channel=Channel.WEB, disponibles=["respond_to_customer"], cierre=True,
        evidencia=Evidencia(vistos={SH_900}, ambiguos={SH_900}),
    )  # fmt: skip
    args = {"kind": "propuesta", "message": "¿Va?", "propuesta": [{"sku": SH_900, "quantity": 1}]}
    assert tool_registry.execute("respond_to_customer", args, ctx)["ok"]


def test_la_nota_de_un_sub_loop_tampoco_elige(monkeypatch):
    """CA-198: el sub-loop recibe el error y deja el renglón en `pendientes`."""
    monkeypatch.setenv("AGENT_ORQUESTADOR", "true")
    get_settings.cache_clear()
    resuelto = {"renglon": 2, "sku": MAS_1K, "cantidad": 1}
    pendiente = {"renglon": 2, "pregunta": "¿qué medida?", "opciones": [MAS_250, MAS_1K]}
    agent, modelo = _agente(
        _ai(_call("delegar_skill", skill="tomar_pedido", encargo="resolvé", renglones=[2])),
        _ai(_call("entregar_nota", resueltos=[resuelto])),
        _ai(_call("entregar_nota", pendientes=[pendiente])),
        _proponer((SH_900, 2, 1), pendientes=[{"renglon": 2, "opciones": [MAS_250, MAS_1K]}]),
    )
    try:
        resultado = agent.handle("1", text="2 shampoo keratina 900\n1 mascara argan", **CLIENTE)
    finally:
        get_settings.cache_clear()
    assert json.loads(modelo.recibidos[2][-1].content)["error"]["code"] == "opcion_sin_elegir"
    assert resultado.kind == "propuesta" and resultado.llamadas_sub_loop == 2


def test_sub_loop_sin_contexto_no_controla():
    """`_entregar` sin contexto valida la nota como hasta ahora."""
    args = {"resueltos": [{"renglon": 1, "sku": SH_900, "cantidad": 1}]}
    _, aceptada, _ = sub_loop._entregar(args, [1], {SH_900}, 0, {})
    assert aceptada is not None


# --- RF-114: la cantidad del renglón --------------------------------------------------------------------------------


def test_cantidad_del_renglon():
    """CA-197: «1 jabón» no se propone por 9; es un aviso, y la misma respuesta enviada de nuevo se acepta."""
    def mala():
        return _proponer((SH_900, 2, 1), (JABON, 9, 2))

    agent, _ = _agente(mala(), _proponer((SH_900, 2, 1), (JABON, 1, 2)))
    agent.handle("1", text="2 shampoo keratina 900\n1 jabon glicerina", **CLIENTE)
    assert _codigos(agent) == ["cantidad_distinta", None]
    assert "el renglón 2 pide 1 y propusiste 9" in _resultados(agent, "respond_to_customer")[0]["error"]["message"]

    otro, _ = _agente(mala(), mala())
    otro.handle("1", text="2 shampoo keratina 900\n1 jabon glicerina", **CLIENTE)
    assert _codigos(otro) == ["cantidad_distinta", None]


def test_cantidades_que_no_se_controlan():
    """CA-197: falta de stock, un renglón ya tratado y las formas de escribir que no son inequívocas."""
    agent, _ = _agente(
        _proponer((SH_900, 2, 1), (MAS_250, 2, 2), kind="sin_stock", mensaje="De la máscara me quedan 2. ¿Va así?"),
        _ai(_call("leer_skill", nombre="tomar_pedido")),
        _proponer((SH_900, 5, 1), (MAS_250, 2, 2), mensaje="Ahora son 5 shampoos. ¿Lo dejamos así?"),
    )
    agent.handle("1", text="2 shampoo keratina 900\n3 mascara argan 250", **CLIENTE)
    agent.handle("1", text="que sean cinco shampoos", **CLIENTE)
    assert _codigos(agent) == [None, None]


@pytest.mark.parametrize(
    ("texto", "cantidad"),
    [
        *(("2 shampoo keratina 900", 2), ("- 6 jabon glicerina", 6), ("tinte 8.1/2", 2), ("3 x gel fijador", 3)),
        *((t, None) for t in ("1 docena de jabon", "media docena de jabon", "2 cajas ampolla", "24 unid ampolla")),
        *((t, None) for t in ("3 o 4 jabones", "2 x 6 jabon", "1 de cada jabon", "500 ml shampoo", "jabon glicerina")),
        *((t, None) for t in ("2 del shampoo de siempre", "7/3", "1 par de guantes", "2 packs de jabon")),
    ],
)
def test_cantidad_inequivoca(texto, cantidad):
    """CA-197: solo se controla lo que se puede leer de una sola forma."""
    assert interpretation_service.cantidad_inequivoca({"texto": texto}) == cantidad


def test_cantidad_de_una_foto():
    """CA-197: la lectura ya separó la cantidad; con unidad escrita (cajas) no se controla."""
    buscar = {"texto": "ampolla reparadora", "cantidad": 24}
    assert interpretation_service.cantidad_inequivoca({"texto": "ampolla x24", "buscar": buscar}) == 24
    con_unidad = {"texto": "ampolla x24", "buscar": buscar, "unidad": "cajas"}
    assert interpretation_service.cantidad_inequivoca(con_unidad) is None


def test_dos_tonos_de_un_renglon_se_suman():
    """CA-197: un renglón en dos líneas del mismo SKU vale por la suma; en dos productos, no se controla."""
    def ctx():
        return tool_registry.TurnContext(
            customer_id="c", channel=Channel.WEB, disponibles=["respond_to_customer"],
            renglones=[{"n": 1, "texto": "3 gel fijador", "marca": None, "estado": "sin_resolver"}],
            evidencia=Evidencia(vistos={GEL, JABON}, por_renglon={1: {GEL, JABON}}),
        )  # fmt: skip

    def codigo(*lineas):
        propuesta = [{"sku": sku, "quantity": q, "renglon": 1, "note": nota} for sku, q, nota in lineas]
        args = {"kind": "propuesta", "message": "¿Va?", "propuesta": propuesta}
        resultado = tool_registry.execute("respond_to_customer", args, ctx())
        return None if resultado["ok"] else resultado["error"]["code"]

    assert codigo((GEL, 2, "tono 7"), (GEL, 1, "tono 8")) is None
    assert codigo((GEL, 2, "tono 7"), (GEL, 2, "tono 8")) == "cantidad_distinta"
    assert codigo((GEL, 2, None), (JABON, 2, None)) is None


# --- La evidencia ---------------------------------------------------------------------------------------------------


def test_del_turno_ignora_los_errores_y_las_respuestas():
    """CA-191: solo cuentan los resultados `ok` de las tools que muestran productos."""
    def mensaje(nombre, resultado):
        return ToolMessage(content=json.dumps(resultado), name=nombre, tool_call_id="x")

    ev = evidencia.del_turno(
        [
            mensaje("get_product", {"ok": True, "data": {"product": {"sku": JABON}}}),
            mensaje("get_product", {"ok": True, "data": {"product": None, "_hint": "no existe"}}),
            mensaje("check_stock", {"ok": False, "error": {"code": "sku_inexistente", "details": {"sku": GEL}}}),
            mensaje("respond_to_customer", {"ok": True, "data": {"sku": MAS_1K}}),
        ],
        [],
    )
    assert ev.vistos == {JABON}


def test_una_nota_sin_renglones_define_sus_resueltos():
    """Un pedido de una línea delegado: la nota no trae números y sus resueltos valen para la respuesta."""
    ev = Evidencia()
    ev.sumar_nota({"tipo": "sub_loop", "nota": {"resueltos": [{"sku": SH_900, "cantidad": 2}], "pendientes": []}})
    assert ev.sin_elegir([(SH_900, None)], evidencia.Elegidos()) == []
    assert SH_900 in ev.vistos


def test_lo_elegido_en_un_renglon_no_vale_para_otro():
    """CA-194: las opciones que se preguntaron para el renglón 1 no definen el renglón 2."""
    ev = Evidencia(vistos={SH_900, SH_300}, ambiguos={SH_900, SH_300}, por_renglon={2: set()})
    elegidos = evidencia.Elegidos(por_renglon=evidencia.de_los_renglones([{"n": 1, "opciones": [SH_900]}, {"n": 2}]))
    assert ev.sin_elegir([(SH_900, 1)], elegidos) == []
    assert ev.sin_elegir([(SH_900, 2)], elegidos) == [(SH_900, 2)]


def test_la_propuesta_vigente_cuenta_como_elegida():
    """CA-190: cambiar una cantidad no vuelve a pedir que el cliente elija."""
    vigente = OrderDraft(
        draft_id="d", customer_id="c", channel=Channel.WEB,
        items=[OrderItem(sku=SH_900, product_name="Shampoo", quantity=1)],
    )  # fmt: skip
    ctx = tool_registry.TurnContext(
        customer_id="c", channel=Channel.WEB, disponibles=["respond_to_customer"], propuesta=vigente,
        evidencia=Evidencia(vistos={SH_900}, ambiguos={SH_900}),
    )  # fmt: skip
    args = {"kind": "propuesta", "message": "¿Va?", "propuesta": [{"sku": SH_900, "quantity": 3}]}
    assert tool_registry.execute("respond_to_customer", args, ctx)["ok"]
