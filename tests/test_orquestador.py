"""Orquestador de skills con sub-loops y notas, con un modelo guionado (spec del orquestador §3 a §7)."""

import json
from decimal import Decimal

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from internal.agent import nota, sub_loop
from internal.config import get_settings
from internal.schemas import agent_schema
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
    assert isinstance(cliente, HumanMessage) and cliente.content.endswith(LISTA)  # solo el último mensaje
    assert cliente.content.startswith("Resolvé solo los renglones 1, 2 del encargo")
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
    assert estado["iteraciones_sub_loop"] == 1  # la llamada que falló también cuenta (RF-94)


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


def test_delegar_un_pedido_sin_renglones(catalogo):
    """Un pedido escrito en una línea no se numera: el sub-loop entrega sus líneas sin `renglon` y el orquestador
    propone con ellas. Con un encargo que sí trae renglones, una línea sin número se rechaza."""
    agent, modelo = _agente(
        _ai(_call_id("delegar_skill", 1, skill="tomar_pedido", encargo="Resolvé el pedido", renglones=[])),
        _ai(_call_id("search_products", 1, query="jabon glicerina")),
        _ai(_call_id("entregar_nota", 1, resueltos=[{"sku": "JAB-GLI", "cantidad": 3}])),
        _ai(_call("respond_to_customer", kind="propuesta", message="Te armé los 3 jabones de glicerina. ¿Lo confirmo?",
                  propuesta=[{"sku": "JAB-GLI", "quantity": 3}])),
    )  # fmt: skip

    resultado = agent.handle("1", text="quiero 3 jabones de glicerina", **CLIENTE)

    (delegacion,) = _resultados(agent, "delegar_skill")
    assert delegacion["data"]["resueltos"] == [{"sku": "JAB-GLI", "cantidad": 3}]
    assert delegacion["data"]["completa"]
    assert [(item.sku, item.quantity) for item in resultado.draft.items] == [("JAB-GLI", 3)]

    sin_numero = agent_schema.EntregarNotaInput.model_validate({"resueltos": [{"sku": "JAB-GLI", "cantidad": 3}]})
    assert nota.validar(sin_numero, [1], {"JAB-GLI"})["code"] == "renglon_faltante"
    completa = nota.completar(sin_numero, [1], {"JAB-GLI"})
    assert completa["resueltos"] == [] and completa["pendientes"][0]["renglon"] == 1


def test_un_sku_del_contexto_no_llega_al_cliente(catalogo, monkeypatch):
    """Los SKU del contexto del cliente están en el prompt aunque ninguna tool los devolvió: el validador de salida
    también los conoce y rechaza un mensaje que los nombra."""
    anterior = {"fecha": "2026-11-02", "lineas": [{"sku": "SH-KER-900", "name": "Shampoo Keratina", "cantidad": 4}]}
    monkeypatch.setattr(order_tools, "pedidos_anteriores", lambda *a, **k: [anterior])
    monkeypatch.setattr(order_service, "skus_comprados", lambda *a, **k: frozenset({"SH-KER-900"}))
    agent, _ = _agente(
        _ai(_call("leer_skill", nombre="informar")),
        _ai(_call_id("respond_to_customer", 1, kind="informacion", message="¡Hola! La otra vez llevaste SH-KER-900.")),
        _ai(_call_id("respond_to_customer", 2, kind="informacion", message="¡Hola! ¿Qué te preparo hoy?")),
    )

    resultado = agent.handle("1", text="hola", **CLIENTE)

    rechazo, aceptada = _resultados(agent, "respond_to_customer")
    assert rechazo["error"]["code"] == "respuesta_rechazada"
    assert "dato_interno" in json.dumps(rechazo["error"], ensure_ascii=False)
    assert aceptada["ok"] and "SH-KER-900" not in resultado.message


def _aclarar(*pendientes):
    return _ai(
        _call(
            "respond_to_customer",
            kind="aclaracion",
            message="Me quedan unas dudas antes de armarlo. ¿Cuál te mando de cada uno?",
            pendientes=[{"renglon": n, "opciones": list(o)} for n, o in pendientes],
        )
    )


@pytest.fixture
def busquedas_del_catalogo(monkeypatch):
    """Cuenta cada búsqueda real en el catálogo: la de la precarga y las de las tools."""
    from internal.tools import catalog_tools

    llamadas: list[list[int]] = []
    original = catalog_tools.buscar_renglones

    def espiar(renglones, preferidos=frozenset()):
        llamadas.append([renglon["n"] for renglon in renglones])
        return original(renglones, preferidos)

    monkeypatch.setattr(catalog_tools, "buscar_renglones", espiar)
    return llamadas


def test_el_sub_loop_no_repite_lo_que_busco_la_precarga(catalogo, busquedas_del_catalogo):
    """Una búsqueda igual a la de la precarga devuelve el resultado de antes, sin volver al catálogo."""
    repetida = _ai(
        _call_id(
            "buscar_renglones",
            1,
            renglones=[
                {"n": 1, "texto": "Shampoo  Keratina", "cantidad": 2},
                {"n": 2, "texto": "máscara argan", "cantidad": 3},
            ],
        )
    )
    agent, modelo = _agente(_delegar(), repetida, _nota(), _responder())

    agent.handle("1", text=LISTA, **CLIENTE)

    assert busquedas_del_catalogo == [[1, 2]]  # solo la precarga
    resultado = json.loads(modelo.recibidos[2][-1].content)
    assert [(r["n"], r["estado"], r["ya_buscado"]) for r in resultado["data"]["renglones"]] == [
        (1, "varias", True),
        (2, "varias", True),
    ]
    assert "ya estaban buscados" in resultado["data"]["_hint"]


def test_el_sub_loop_no_busca_fuera_del_encargo(catalogo, busquedas_del_catalogo):
    """El mensaje del cliente trae renglones que no se delegaron: el sub-loop no los busca y se le avisa."""
    otra = _ai(
        _call_id(
            "buscar_renglones",
            1,
            renglones=[
                {"n": 1, "texto": "shampoo keratina 900", "cantidad": 2},
                {"n": 2, "texto": "mascara argan 1 kg", "cantidad": 3},
            ],
        )
    )
    agent, modelo = _agente(
        _delegar(renglones=(2,)),
        otra,
        _nota(resueltos=((2, "MAS-ARG-1K", 3),), pendientes=()),
        _aclarar((1, ["SH-KER-900", "SH-KER-300"])),
        _responder(resueltos=((2, "MAS-ARG-1K", 3),), pendientes=((1, ["SH-KER-900", "SH-KER-300"]),)),
    )

    agent.handle("1", text=LISTA, **CLIENTE)

    assert busquedas_del_catalogo == [[1, 2], [2]]  # la precarga y solo el renglón 2 con el texto nuevo
    resultado = json.loads(modelo.recibidos[2][-1].content)
    assert [r["n"] for r in resultado["data"]["renglones"]] == [2]
    assert "renglones 1 no son de tu encargo" in resultado["data"]["_hint"]


def test_stock_parcial_no_va_afuera(catalogo):
    """BR-01: si de un renglón queda algo, la nota no lo puede dejar afuera como agotado."""
    lista = "25 jabon glicerina\n2 shampoo keratina"
    agent, modelo = _agente(
        _delegar(),
        _nota(1, resueltos=(), pendientes=((2, ["SH-KER-900", "SH-KER-300"]),), afuera=((1, "sin_stock"),)),
        _nota(2, resueltos=(), pendientes=((1, ["JAB-GLI"]), (2, ["SH-KER-900", "SH-KER-300"]))),
        _aclarar((1, ["JAB-GLI"]), (2, ["SH-KER-900", "SH-KER-300"])),
    )

    agent.handle("1", text=lista, **CLIENTE)

    rechazo = json.loads(modelo.recibidos[2][-1].content)
    assert rechazo["error"]["code"] == "stock_parcial"
    assert rechazo["error"]["details"]["renglones"] == [1]
    assert "del 1 quedan 20" in rechazo["error"]["message"]
    assert _notas(agent)[0]["nota"]["afuera"] == []


def test_stock_parcial_de_search_products_no_va_afuera(catalogo):
    """BR-01: con `quantity`, search_products dice cuánto queda aunque la búsqueda no sea de un renglón."""
    agent, modelo = _agente(
        _ai(_call_id("delegar_skill", 1, skill="tomar_pedido", encargo="Resolvé el pedido", renglones=[])),
        _ai(_call_id("search_products", 1, query="jabon glicerina", quantity=25)),
        _ai(_call_id("entregar_nota", 1, afuera=[{"motivo": "sin_stock"}])),
        _ai(_call_id("entregar_nota", 2, pendientes=[{"pregunta": "¿te mando las que hay?", "opciones": ["JAB-GLI"]}])),
        _ai(_call("respond_to_customer", kind="aclaracion", message="Del jabón de glicerina me quedan 20, no llego a "
                  "los 25. ¿Te mando esas 20?", pendientes=[{"opciones": ["JAB-GLI"]}])),
    )  # fmt: skip

    agent.handle("1", text="quiero 25 jabones de glicerina", **CLIENTE)

    rechazo = json.loads(modelo.recibidos[3][-1].content)
    assert rechazo["error"]["code"] == "stock_parcial"
    assert rechazo["error"]["details"]["skus"] == ["JAB-GLI"]
    assert "de JAB-GLI quedan 20" in rechazo["error"]["message"]
    assert _notas(agent)[0]["nota"]["afuera"] == []


def test_el_aviso_de_stock_parcial_sin_renglon_va_una_vez():
    """Puede ser otro producto el agotado: la misma nota entregada de nuevo se acepta."""
    agotado = agent_schema.EntregarNotaInput.model_validate({"afuera": [{"motivo": "sin_stock"}]})
    avisados: set[str] = set()
    assert sub_loop._stock_sin_renglon(agotado, {}, {"JAB-GLI": 20}, avisados)["code"] == "stock_parcial"
    assert sub_loop._stock_sin_renglon(agotado, {}, {"JAB-GLI": 20}, avisados) is None
    # Lo que se buscó por renglón ya lo controla `stock_parcial` de la nota, con el número del renglón
    busqueda = {1: ({"texto": "jabon"}, {"estado": "sin_stock", "candidatos": [{"sku": "JAB-GLI"}]})}
    assert sub_loop._stock_sin_renglon(agotado, busqueda, {"JAB-GLI": 20}, set()) is None
    # Agotado del todo no es parcial
    assert sub_loop._stock_sin_renglon(agotado, {}, {"JAB-GLI": 0}, set()) is None


def test_las_dudas_de_la_nota_traen_el_nombre_de_sus_opciones(catalogo):
    """Con solo los SKU de las opciones, el orquestador los copiaba en el mensaje al cliente."""
    agent, _ = _agente(_delegar(), _nota(), _responder())

    agent.handle("1", text=LISTA, **CLIENTE)

    (delegacion,) = _resultados(agent, "delegar_skill")
    (duda,) = delegacion["data"]["pendientes"]
    assert duda["opciones"] == MASCARAS
    assert duda["nombres"] == ["Máscara Argan 250 g", "Máscara Argan 1 kg"]
