"""Orquestador de skills con sub-loops y notas, con un modelo guionado (spec del orquestador §3 a §7)."""

import json
from decimal import Decimal

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from internal.agent import nota
from internal.config import get_settings
from internal.schemas.catalog_schema import Product
from internal.services import order_service, sheets_service, skill_service
from internal.tools import order_tools
from tests.test_agent_graph import CLIENTE, _agente, _ai, _call, _resultados, base_limpia  # noqa: F401

LISTA = "2 shampoo keratina\n3 mascara argan"
MASCARAS = ["MAS-ARG-250", "MAS-ARG-1K"]


@pytest.fixture(autouse=True)
def orquestador(monkeypatch):
    monkeypatch.setenv("AGENT_ORQUESTADOR", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def catalogo(monkeypatch):
    productos = [
        Product(sku="SH-KER-900", name="Shampoo Keratina", brand="Marca Uno", presentation="900 ml",
                price=Decimal("4500"), stock=20),
        Product(sku="SH-KER-300", name="Shampoo Keratina", brand="Marca Uno", presentation="300 ml",
                price=Decimal("2500"), stock=20),
        Product(sku="MAS-ARG-250", name="Máscara Argan", brand="Marca Uno", presentation="250 g",
                price=Decimal("3900"), stock=20),
        Product(sku="MAS-ARG-1K", name="Máscara Argan", brand="Marca Uno", presentation="1 kg",
                price=Decimal("11200"), stock=20),
        Product(sku="JAB-GLI", name="Jabón Glicerina", brand="Marca Dos", presentation="100 g",
                price=Decimal("800"), stock=20),
    ]  # fmt: skip
    monkeypatch.setattr(sheets_service, "get_catalog", lambda: productos)
    return productos


def _call_id(name, n, **args):
    """Como `_call`, con un id propio: un turno puede pedir la misma tool varias veces."""
    return {"name": name, "args": args, "id": f"call-{name}-{n}", "type": "tool_call"}


def _delegar(renglones=(1, 2), n=1, encargo="Resolvé producto, medida y cantidad"):
    return _ai(_call_id("delegar_skill", n, skill="tomar_pedido", encargo=encargo, renglones=list(renglones)))


def _nota(n=1, resueltos=((1, "SH-KER-900", 2),), pendientes=((2, MASCARAS),), afuera=(), observaciones=""):
    return _ai(
        _call_id(
            "entregar_nota",
            n,
            resueltos=[{"renglon": r, "sku": sku, "cantidad": c} for r, sku, c in resueltos],
            pendientes=[{"renglon": r, "pregunta": "¿qué medida?", "opciones": list(o)} for r, o in pendientes],
            afuera=[{"renglon": r, "motivo": m} for r, m in afuera],
            observaciones=observaciones,
        )
    )


def _responder(resueltos=((1, "SH-KER-900", 2),), pendientes=((2, MASCARAS),)):
    return _ai(
        _call(
            "respond_to_customer",
            kind="propuesta",
            message="Te armé el shampoo de 900. La máscara argan, ¿la querés de 250 o de 1 kilo?",
            propuesta=[{"sku": sku, "quantity": c, "renglon": r} for r, sku, c in resueltos],
            pendientes=[{"renglon": r, "opciones": list(o)} for r, o in pendientes],
        )
    )


def _estado(agent, thread="1"):
    return agent.graph.get_state({"configurable": {"thread_id": thread}}).values


def _notas(agent):
    return [entrada for entrada in _estado(agent)["notas_turno"] if entrada["tipo"] == nota.TIPO]


def _es_sub_loop(mensajes) -> bool:
    return isinstance(mensajes[0], SystemMessage) and mensajes[0].content.startswith("# Sub-loop de Norma")


def test_pedido_delegado_en_un_sub_loop(catalogo):
    """El camino feliz: el orquestador delega, el sub-loop entrega la nota y el orquestador responde con ella."""
    agent, modelo = _agente(_delegar(), _nota(), _responder())

    resultado = agent.handle("1", text=LISTA, **CLIENTE)

    assert resultado.kind == "propuesta"
    assert [(item.sku, item.quantity) for item in resultado.draft.items] == [("SH-KER-900", 2)]
    assert (resultado.iterations, resultado.llamadas_sub_loop) == (2, 1)
    assert "delegar_skill" in modelo.ofrecidas[0]
    (delegacion,) = _resultados(agent, "delegar_skill")
    assert delegacion["ok"] and delegacion["data"]["completa"]
    assert delegacion["data"]["productos"]["SH-KER-900"] == "Shampoo Keratina 900 ml"
    renglones = {r["n"]: r for r in _estado(agent)["renglones"]}
    assert renglones[1]["estado"] == "resuelto" and renglones[2]["estado"] == "pendiente"


def test_no_se_delega_una_skill_inline(catalogo):
    """CA-152: delegar una skill inline devuelve `skill_no_delegable` y el orquestador sigue."""
    agent, _ = _agente(
        _ai(_call("delegar_skill", skill="informar", encargo="saludá")),
        _ai(_call("leer_skill", nombre="informar")),
        _ai(_call("respond_to_customer", kind="informacion", message="¡Hola! ¿Qué te preparo?")),
    )

    resultado = agent.handle("1", text="hola", **CLIENTE)

    (delegacion,) = _resultados(agent, "delegar_skill")
    assert delegacion["error"]["code"] == "skill_no_delegable"
    assert resultado.kind == "informacion"
    assert resultado.llamadas_sub_loop == 0


def test_el_sub_loop_tiene_contexto_propio(catalogo):
    """CA-153: el sub-loop no ve la charla anterior ni otras notas, y no tiene tools que le hablen al cliente."""
    agent, modelo = _agente(
        _ai(_call("leer_skill", nombre="informar")),
        _ai(_call("respond_to_customer", kind="informacion", message="¡Hola! ¿Qué te preparo hoy?")),
        _delegar(),
        _nota(),
        _responder(),
    )
    agent.handle("1", text="hola", **CLIENTE)
    agent.handle("1", text=LISTA, **CLIENTE)

    (indice,) = [i for i, mensajes in enumerate(modelo.recibidos) if _es_sub_loop(mensajes)]
    sistema, cliente = modelo.recibidos[indice]
    assert isinstance(cliente, HumanMessage) and cliente.content == LISTA  # solo el último mensaje
    assert "Qué te preparo hoy" not in sistema.content
    assert "## Nota de búsqueda" not in sistema.content  # la precarga entra como «ya buscados» de su encargo
    assert "## Ya buscados por el sistema" in sistema.content
    assert "1. 2 shampoo keratina" in sistema.content and "Resolvé producto" in sistema.content
    tools = modelo.ofrecidas[indice]
    assert "entregar_nota" in tools
    assert not {"respond_to_customer", "armar_pedido", "delegar_skill", "leer_skill"} & set(tools)


def test_nota_con_sku_inventado_se_rechaza(catalogo):
    """CA-154: una nota con un SKU que no salió de ninguna búsqueda vuelve al sub-loop con el error."""
    agent, modelo = _agente(
        _delegar(),
        _nota(1, resueltos=((1, "SH-KER-500", 2),)),
        _nota(2),
        _responder(),
    )

    resultado = agent.handle("1", text=LISTA, **CLIENTE)

    rechazo = json.loads(modelo.recibidos[2][-1].content)
    assert rechazo["error"]["code"] == "sku_no_buscado"
    assert rechazo["error"]["details"]["skus"] == ["SH-KER-500"]
    assert resultado.kind == "propuesta" and resultado.llamadas_sub_loop == 2
    assert _notas(agent)[0]["cerrada_por"] == "nota"


def test_renglon_sin_cuenta_sale_como_pendiente(catalogo):
    """CA-155: al tercer intento con un renglón sin cuenta, la nota sale con ese renglón en `pendientes`."""
    sin_el_2 = {"pendientes": ()}
    agent, modelo = _agente(
        _delegar(),
        _nota(1, **sin_el_2),
        _nota(2, **sin_el_2),
        _nota(3, **sin_el_2),
        _responder(pendientes=((2, []),)),
    )

    resultado = agent.handle("1", text=LISTA, **CLIENTE)

    (entrada,) = _notas(agent)
    assert entrada["cerrada_por"] == "forzada"
    assert entrada["nota"]["pendientes"] == [{"renglon": 2, "pregunta": nota.PREGUNTA_SIN_CUENTA, "opciones": []}]
    assert resultado.kind == "propuesta" and resultado.llamadas_sub_loop == 3


def test_sub_loop_al_tope_entrega_lo_encontrado(catalogo, monkeypatch):
    """CA-156: un sub-loop que no entrega la nota llega a su tope, el backend la arma y el turno no falla."""
    skills = dict(skill_service.get_skills())
    skills["tomar_pedido"] = skills["tomar_pedido"].model_copy(update={"max_iteraciones": 2})
    monkeypatch.setattr(skill_service, "get_skills", lambda: skills)
    for modulo in ("internal.agent.tool_registry", "internal.agent.context", "internal.tools.skill_tools"):
        monkeypatch.setattr(f"{modulo}.get_skills", lambda: skills)
    agent, _ = _agente(
        _delegar(),
        _ai(_call_id("search_products", 1, query="shampoo keratina")),
        _ai(_call_id("search_products", 2, query="mascara argan")),
        _ai(_call("respond_to_customer", kind="aclaracion", message="¿Qué shampoo y qué máscara querés?",
                  pendientes=[{"renglon": 1, "opciones": ["SH-KER-900", "SH-KER-300"]},
                              {"renglon": 2, "opciones": MASCARAS}])),
    )  # fmt: skip

    resultado = agent.handle("1", text=LISTA, **CLIENTE)

    (entrada,) = _notas(agent)
    assert entrada["cerrada_por"] == "tope" and entrada["llamadas"] == 2
    assert {p["renglon"] for p in entrada["nota"]["pendientes"]} == {1, 2}  # «varias» en la precarga
    assert resultado.kind == "aclaracion"
    assert _estado(agent)["fallas"] == 0


def test_las_notas_no_pasan_al_turno_siguiente(catalogo):
    """CA-157: las observaciones de una nota no llegan al turno siguiente; los renglones resueltos y pendientes sí."""
    observacion = "OBSERVACION-INTERNA: el cliente suele llevar 900"
    agent, modelo = _agente(
        _delegar(),
        _nota(observaciones=observacion),
        _responder(),
        _ai(_call("leer_skill", nombre="informar")),
        _ai(_call("respond_to_customer", kind="propuesta", message="Dale, te lo dejo así. ¿Lo confirmo?",
                  propuesta=[{"sku": "SH-KER-900", "quantity": 2}])),
    )  # fmt: skip
    agent.handle("1", text=LISTA, **CLIENTE)

    agent.handle("1", text="¿cuánto tarda en llegar?", **CLIENTE)

    recibidos = modelo.recibidos[-2]
    assert not any(observacion in str(m.content) for m in recibidos)
    assert not any(isinstance(m, ToolMessage) for m in recibidos)
    sistema = recibidos[0].content
    assert "1. 2 shampoo keratina → resuelto: 2 x SH-KER-900" in sistema
    assert "2. 3 mascara argan → pendiente" in sistema
    assert _estado(agent)["notas_turno"] == []


def test_pedido_en_dos_mensajes_del_mismo_dia(catalogo, monkeypatch):
    """CA-158: el segundo mensaje no rehace el contexto del cliente y su respuesta trae los jabones y la duda del
    shampoo, que sigue en las opciones previas."""
    lecturas = []
    monkeypatch.setattr(order_tools, "pedidos_anteriores", lambda *a, **k: lecturas.append(1) or [])
    monkeypatch.setattr(order_service, "skus_comprados", lambda *a, **k: frozenset())
    shampoos = ["SH-KER-900", "SH-KER-300"]
    agent, modelo = _agente(
        _ai(_call("leer_skill", nombre="tomar_pedido"), _call("search_products", query="shampoo")),
        _ai(_call("respond_to_customer", kind="aclaracion", message="Tengo shampoo de 900 y de 300. ¿Cuál querés?",
                  options=shampoos)),
        _ai(_call("search_products", query="jabon glicerina")),
        _ai(_call("respond_to_customer", kind="propuesta",
                  message="Te sumé los 3 jabones. Del shampoo, ¿el de 900 o el de 300?",
                  propuesta=[{"sku": "JAB-GLI", "quantity": 3}], options=shampoos)),
    )  # fmt: skip

    primero = agent.handle("1", text="Hola, ¿tenés shampoo?", **CLIENTE)
    segundo = agent.handle("1", text="Además quiero 3 jabones", **CLIENTE)

    assert primero.iterations == 2 and segundo.iterations == 2
    assert lecturas == [1]
    assert [(item.sku, item.quantity) for item in segundo.draft.items] == [("JAB-GLI", 3)]
    assert segundo.options == shampoos
    assert "SH-KER-900 (Shampoo Keratina 900 ml)" in modelo.recibidos[2][0].content  # las opciones previas
    assert segundo.llamadas_sub_loop == 0  # consultas cortas: no hizo falta delegar


def test_sub_loop_con_texto_suelto_recibe_aviso(catalogo):
    """CA-159: el texto sin tool del sub-loop no llega a nadie, recibe un aviso y la vuelta cuenta."""
    suelto = "TEXTO-SUELTO: ya casi lo tengo"
    agent, modelo = _agente(_delegar(), AIMessage(content=suelto), _nota(), _responder())

    resultado = agent.handle("1", text=LISTA, **CLIENTE)

    segunda = modelo.recibidos[2]
    assert _es_sub_loop(segunda)
    assert isinstance(segunda[-1], HumanMessage) and "entregar_nota" in segunda[-1].content
    assert suelto not in resultado.message
    orquestador = modelo.recibidos[-1]
    assert not any(suelto in str(m.content) for m in orquestador)
    assert resultado.llamadas_sub_loop == 2
    assert _notas(agent)[0]["cerrada_por"] == "nota"


def test_redelegar_trae_los_intentos_anteriores(catalogo):
    """CA-160: si el orquestador vuelve a delegar un renglón, el sub-loop nuevo recibe la nota anterior y sus
    intentos, con los argumentos de cada búsqueda."""
    agent, modelo = _agente(
        _delegar(renglones=(2,)),
        _ai(_call_id("search_products", 1, query="mascara argan rulos")),
        _nota(1, resueltos=(), pendientes=((2, MASCARAS),)),
        _delegar(renglones=(2,), n=2, encargo="Probá por la medida que suele llevar"),
        _nota(2, resueltos=((2, "MAS-ARG-1K", 3),), pendientes=()),
        _responder(resueltos=((1, "SH-KER-900", 2), (2, "MAS-ARG-1K", 3)), pendientes=()),
    )

    agent.handle("1", text=LISTA, **CLIENTE)

    sub_loops = [mensajes for mensajes in modelo.recibidos if _es_sub_loop(mensajes)]
    segundo = sub_loops[-1][0].content
    assert "## Nota anterior de estos renglones" in segundo
    assert '"query": "mascara argan rulos"' in segundo
    primera, segunda = _notas(agent)
    assert primera["intentos"] == [{"tool": "search_products", "args": {"query": "mascara argan rulos"}}]
    assert segunda["nota"]["resueltos"] == [{"renglon": 2, "sku": "MAS-ARG-1K", "cantidad": 3}]


def test_el_orquestador_ve_solo_la_nota(catalogo):
    """CA-161: después de un sub-loop con tres búsquedas, el orquestador ve `delegar_skill` y la nota, no las
    búsquedas ni los intentos."""
    agent, modelo = _agente(
        _delegar(),
        _ai(_call_id("search_products", 1, query="shampoo keratina")),
        _ai(_call_id("search_products", 2, query="mascara argan")),
        _ai(_call_id("check_stock", 1, sku="SH-KER-900", quantity=2)),
        _nota(),
        _responder(),
    )

    agent.handle("1", text=LISTA, **CLIENTE)

    orquestador = modelo.recibidos[-1]
    assert not _es_sub_loop(orquestador)
    pedidas = [call["name"] for m in orquestador if isinstance(m, AIMessage) for call in m.tool_calls]
    resultados = [m.name for m in orquestador if isinstance(m, ToolMessage)]
    assert pedidas == ["delegar_skill"] and resultados == ["delegar_skill"]
    nota_vista = json.loads(orquestador[-1].content)["data"]
    assert "intentos" not in nota_vista
    assert len(_notas(agent)[0]["intentos"]) == 3


def test_si_falla_el_llm_del_sub_loop_falla_el_turno(catalogo):
    """§7: una falla del LLM dentro del sub-loop es una falla del turno y la propuesta vigente no cambia."""
    agent, _ = _agente(_delegar(), RuntimeError("se cayó el proveedor"))

    resultado = agent.handle("1", text=LISTA, **CLIENTE)

    assert resultado.kind == "error"
    estado = _estado(agent)
    assert estado["fallas"] == 1 and estado["propuesta"] is None


def test_las_vueltas_del_sub_loop_cuentan_para_el_tope(catalogo, monkeypatch):
    """RF-93: con el tope del turno casi agotado, el sub-loop corre solo lo que queda y el orquestador cierra."""
    monkeypatch.setenv("AGENT_MAX_ITERATIONS", "3")
    get_settings.cache_clear()
    agent, modelo = _agente(
        _delegar(),
        _ai(_call_id("search_products", 1, query="shampoo keratina")),
        _ai(_call_id("search_products", 2, query="mascara argan")),
        _ai(_call("respond_to_customer", kind="aclaracion", message="¿Qué shampoo y qué máscara querés?",
                  pendientes=[{"renglon": 1, "opciones": ["SH-KER-900", "SH-KER-300"]},
                              {"renglon": 2, "opciones": MASCARAS}])),
    )  # fmt: skip

    resultado = agent.handle("1", text=LISTA, **CLIENTE)

    assert _notas(agent)[0]["cerrada_por"] == "tope"
    assert resultado.llamadas_sub_loop == 2
    assert modelo.ofrecidas[-1] == ["respond_to_customer"]  # el cierre de hoy
    assert resultado.kind == "aclaracion"


def test_el_orquestador_puede_decir_lo_que_quedo(catalogo):
    """BR-01 con el orquestador: el sub-loop vio que no alcanza, la nota trae lo que queda y el validador de salida
    deja que el orquestador lo diga, aunque la búsqueda no esté en su turno."""
    catalogo[2] = catalogo[2].model_copy(update={"stock": 2})  # MAS-ARG-250
    agent, _ = _agente(
        _delegar(renglones=(2,)),
        _ai(_call_id("check_stock", 1, sku="MAS-ARG-250", quantity=3)),
        _nota(1, resueltos=((2, "MAS-ARG-250", 2),), pendientes=()),
        _ai(_call("respond_to_customer", kind="sin_stock",
                  message="De la máscara de 250 me quedan 2, no llego a las 3. El shampoo, ¿de 900 o de 300?",
                  propuesta=[{"sku": "MAS-ARG-250", "quantity": 2, "renglon": 2}],
                  pendientes=[{"renglon": 1, "opciones": ["SH-KER-900", "SH-KER-300"]}])),
    )  # fmt: skip

    resultado = agent.handle("1", text=LISTA, **CLIENTE)

    (delegacion,) = _resultados(agent, "delegar_skill")
    assert delegacion["data"]["disponibles"] == {"MAS-ARG-250": 2}
    assert resultado.kind == "sin_stock"
    assert resultado.message.startswith("De la máscara de 250 me quedan 2")


def test_lo_de_siempre_usa_los_pedidos_del_contexto(catalogo, monkeypatch):
    """BR-11: el sub-loop resuelve «lo de siempre» con los pedidos anteriores del contexto, sin buscar, y la nota
    se acepta aunque ese SKU no salió de una búsqueda."""
    anterior = {"fecha": "2026-11-02", "lineas": [{"sku": "JAB-GLI", "name": "Jabón Glicerina", "cantidad": 4}]}
    monkeypatch.setattr(order_tools, "pedidos_anteriores", lambda *a, **k: [anterior])
    monkeypatch.setattr(order_service, "skus_comprados", lambda *a, **k: frozenset({"JAB-GLI"}))
    agent, modelo = _agente(
        _delegar(renglones=(2,)),
        _nota(1, resueltos=((2, "JAB-GLI", 4),), pendientes=()),
        _responder(resueltos=((2, "JAB-GLI", 4),), pendientes=((1, ["SH-KER-900", "SH-KER-300"]),)),
    )

    agent.handle("1", text="2 shampoo keratina\nlo de siempre de jabon", **CLIENTE)

    (entrada,) = _notas(agent)
    assert entrada["cerrada_por"] == "nota"  # sin rechazos
    sub_loop = next(mensajes for mensajes in modelo.recibidos if _es_sub_loop(mensajes))
    assert "4 x Jabón Glicerina (JAB-GLI)" in sub_loop[0].content


def test_el_sub_loop_sabe_que_tools_tiene(catalogo):
    """Una tool que el sub-loop no tiene vuelve con un error que le dice cuáles usar, no que lea una skill."""
    agent, modelo = _agente(
        _delegar(),
        _ai(_call_id("respond_to_customer", 1, kind="informacion", message="hola")),
        _nota(),
        _responder(),
    )

    agent.handle("1", text=LISTA, **CLIENTE)

    error = json.loads(modelo.recibidos[2][-1].content)["error"]
    assert error["code"] == "tool_no_permitida"
    assert "entregar_nota" in error["message"] and "leer_skill" not in error["message"]
    assert _notas(agent)[0]["intentos"] == []


def test_los_skus_de_explorar_catalogo_valen_para_la_nota():
    """Cualquier producto que devuelve una tool vale para la nota; un error no aporta su SKU."""
    from internal.agent import sub_loop

    explorado = {"ok": True, "data": {"productos": [{"sku": "JAB-GLI", "name": "Jabón"}]}}
    assert sub_loop._vistos(explorado) == {"JAB-GLI"}
    error = {"ok": False, "error": {"code": "sku_inexistente", "details": {"sku": "INVENTADO"}}}
    assert sub_loop._vistos(error) == set()
