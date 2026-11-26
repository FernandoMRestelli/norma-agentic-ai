"""Tools de catálogo, stock y lectura de skills, con el catálogo de ejemplo y sin LLM."""

import re

import pytest

from internal.agent import sub_loop, tool_registry
from internal.schemas.agent_schema import ToolFailure
from internal.schemas.catalog_schema import Product
from internal.schemas.order_schema import Channel, OrderDraft, OrderItem
from internal.services import inventory_service, sheets_service, skill_service
from internal.services.skill_service import SkillError, load_skills
from internal.tools import catalog_tools, inventory_tools, order_tools, skill_tools


@pytest.mark.parametrize("query", ["shampoo pantene 400", "Shampoo Pantene 400ML", "shampo pantne 400"])
def test_busqueda_aproximada_trae_primero_el_producto(query):
    """CA-29 / RF-07: tolera mayúsculas y errores de tipeo."""
    assert catalog_tools.search_products(query)[0].sku == "SH-PAN-400"


def test_busqueda_sin_coincidencias_no_inventa():
    assert catalog_tools.search_products("head and shoulders xyz") == []


@pytest.mark.parametrize(
    ("query", "esperado"),
    [
        ("pantene 340", []),  # hay Pantene y hay 340 ml, pero no juntos
        ("koleston 7.1", ["TIN-KOL-71"]),
        ("koleston 6,1", ["TIN-KOL-61"]),
        ("SH-PAN-400", ["SH-PAN-400"]),
    ],
)
def test_los_numeros_no_se_aproximan(query, esperado):
    """CA-45: un número no coincide con otro parecido."""
    assert [product.sku for product in catalog_tools.search_products(query)] == esperado


def test_presentacion_inexistente_no_parece_exacta():
    """CA-45: «300 ml» trae los shampoos Pantene para preguntar, sin marcar ninguno como exacto."""
    matches = catalog_tools.search_products("shampoo pantene 300 ml")
    assert {match.sku for match in matches} == {"SH-PAN-200", "SH-PAN-400"}
    assert all(match.score < 1 for match in matches)


@pytest.mark.parametrize("query", ["coca cola", "gillette", "shampoo dove"])
def test_sin_parecido_real_no_trae_productos(query):
    """CA-45."""
    assert catalog_tools.search_products(query) == []


@pytest.mark.parametrize(
    ("query", "sku"),
    [("acondicionador", "AC-PAN-400"), ("cremas de enjuague", "CE-SED-340"), ("jabones dove", "JAB-DOV-90")],
)
def test_busqueda_tolera_plurales_y_palabras_de_relleno(query, sku):
    assert [product.sku for product in catalog_tools.search_products(query)] == [sku]


def test_resultado_al_llm_trae_score_sin_datos_internos():
    ctx = tool_registry.TurnContext(
        customer_id="c", channel=Channel.WEB, disponibles=["search_products", "get_product"]
    )
    match = tool_registry.execute("search_products", {"query": "jabon dove"}, ctx)["data"]["matches"][0]
    assert match["score"] == 1
    assert not {"stock", "min_stock", "is_low_stock"} & match.keys()
    assert match["hay_stock"] is True
    product = tool_registry.execute("get_product", {"sku": "JAB-DOV-90"}, ctx)["data"]["product"]
    assert product["sku"] == "JAB-DOV-90"
    assert not {"stock", "min_stock", "is_low_stock"} & product.keys()
    assert product["hay_stock"] is True


def test_catalogo_de_ejemplo_se_lee_con_el_esquema():
    assert {product.sku for product in sheets_service.get_catalog()} >= {"SH-PAN-400", "CE-SED-340"}


def test_check_stock_indica_si_alcanza():
    assert inventory_tools.check_stock("CE-SED-340", 4).is_enough
    assert not inventory_tools.check_stock("CE-SED-340", 5).is_enough


def test_check_stock_con_sku_inexistente():
    with pytest.raises(ToolFailure) as error:
        inventory_tools.check_stock("XX-000", 1)
    assert error.value.code == "sku_inexistente"


def test_las_skills_reales_son_validas():
    """CA-40: todas las tools que nombran las skills existen."""
    tool_registry.validate_skills()
    assert set(skill_service.get_skills()) == {
        "confirmar_pedido",
        "informar",
        "tomar_pedido",
        "derivar_o_cancelar",
    }


def test_solo_confirmar_pedido_habilita_armar_pedido():
    """CA-37: es la única skill que puede registrar un pedido, y sus BR siguen disponibles en la skill que las usa."""
    skills = skill_service.get_skills()
    assert [skill.nombre for skill in skills.values() if "armar_pedido" in skill.tools] == ["confirmar_pedido"]
    assert sorted(skills["tomar_pedido"].br) == ["BR-01", "BR-03", "BR-04", "BR-05", "BR-09", "BR-10", "BR-11"]


def test_las_skills_solo_mencionan_br_que_existen():
    """CA-46: una BR mencionada se puede leer, y ninguna BR queda sin una skill que la nombre."""
    skills = skill_service.get_skills().values()
    existentes = {id_br for skill in skills for id_br in skill.br}
    for skill in skills:
        textos = [skill.instrucciones, *skill.br.values()]
        assert set(re.findall(r"BR-\d+", " ".join(textos))) <= existentes, skill.nombre
        for id_br in skill.br:
            assert id_br in skill.instrucciones, f"{skill.nombre} no indica cuándo leer {id_br}"


def test_las_skills_solo_nombran_tools_que_pueden_usar():
    """CA-46: el LLM no recibe instrucciones de usar una tool que su skill no le habilita."""
    for skill in skill_service.get_skills().values():
        permitidas = {*tool_registry.BASE_TOOLS, *skill.tools}
        for tool in tool_registry.TOOLS:
            assert f"`{tool}`" not in skill.instrucciones or tool in permitidas, (skill.nombre, tool)


def test_skill_con_tool_inexistente_impide_arrancar(monkeypatch):
    """CA-40."""
    skill = skill_service.Skill(nombre="mala", funcion="x", cuando="x", tools=["no_existe"], instrucciones="", br={})
    monkeypatch.setattr(tool_registry, "get_skills", lambda: {"mala": skill})
    with pytest.raises(SkillError, match="no_existe"):
        tool_registry.validate_skills()


def _crear_skill(base, encabezado):
    folder = base / "mi_skill"
    folder.mkdir()
    (folder / "skill.md").write_text(encabezado, encoding="utf-8")


def test_leer_skill_trae_el_titulo_de_cada_br():
    """El agente elige qué BR leer por su título, sin leerlas todas."""
    br = skill_tools.leer_skill("tomar_pedido")["br"]
    assert {"id": "BR-01", "titulo": "Falta de stock"} in br
    assert [regla["id"] for regla in br] == sorted(skill_service.get_skills()["tomar_pedido"].br)


@pytest.mark.parametrize("texto", ["Sin encabezado", "# BR-02: Otra BR", "# BR-01:"])
def test_br_sin_encabezado_con_titulo_impide_arrancar(tmp_path, texto):
    _crear_skill(tmp_path, "---\nnombre: mi_skill\nfuncion: x\ncuando: x\ntools: [leer_br]\n---\nLeé BR-01")
    (tmp_path / "mi_skill" / "BR").mkdir()
    (tmp_path / "mi_skill" / "BR" / "BR-01.md").write_text(texto, encoding="utf-8")
    with pytest.raises(SkillError, match="BR-01"):
        load_skills(tmp_path)


def test_skill_sin_encabezado_impide_arrancar(tmp_path):
    _crear_skill(tmp_path, "sin encabezado")
    with pytest.raises(SkillError):
        load_skills(tmp_path)


def test_skill_con_nombre_distinto_a_su_carpeta_impide_arrancar(tmp_path):
    _crear_skill(tmp_path, "---\nnombre: otra\nfuncion: x\ncuando: x\ntools: [leer_br]\n---\nTexto")
    with pytest.raises(SkillError, match="otra"):
        load_skills(tmp_path)


def test_skill_sin_funcion_impide_arrancar(tmp_path):
    """El índice muestra qué hace cada skill: una skill nueva no puede omitirlo."""
    _crear_skill(tmp_path, "---\nnombre: mi_skill\ncuando: x\ntools: [leer_br]\n---\nTexto")
    with pytest.raises(SkillError, match="funcion"):
        load_skills(tmp_path)


@pytest.mark.parametrize("nombre", ["../../.env", "../tomar_pedido", "/etc/passwd", "tomar_pedido/skill.md"])
def test_leer_skill_no_lee_rutas(nombre):
    """CA-40: el nombre solo se compara contra el índice."""
    with pytest.raises(ToolFailure) as error:
        skill_tools.leer_skill(nombre)
    assert error.value.code == "skill_inexistente"


@pytest.mark.parametrize("id_br", ["../../.env", "BR/BR-01", "/etc/passwd"])
def test_leer_br_no_lee_rutas(id_br):
    """CA-40."""
    with pytest.raises(ToolFailure) as error:
        skill_tools.leer_br(id_br, ["tomar_pedido"])
    assert error.value.code == "br_inexistente"


def test_leer_br_solo_de_skills_leidas():
    assert "Falta de stock" in skill_tools.leer_br("BR-01", ["tomar_pedido"])["texto"]
    with pytest.raises(ToolFailure) as error:
        skill_tools.leer_br("BR-01", [])
    assert error.value.code == "br_no_permitida"


def test_tool_no_disponible_no_se_ejecuta():
    """CA-37."""
    ctx = tool_registry.TurnContext(customer_id="c", channel=Channel.WEB, disponibles=list(tool_registry.BASE_TOOLS))
    result = tool_registry.execute("armar_pedido", {}, ctx)
    assert result["error"]["code"] == "tool_no_permitida"


def _error(name, arguments, **contexto):
    ctx = tool_registry.TurnContext(customer_id="c", channel=Channel.WEB, **contexto)
    return tool_registry.execute(name, arguments, ctx)["error"]


def test_tool_no_permitida_nombra_la_skill_que_la_habilita():
    """El LLM lee la skill que corresponde en vez de adivinar; sin propuesta, no le sugiere confirmar_pedido."""
    error = _error("respond_to_customer", {}, disponibles=list(tool_registry.BASE_TOOLS))
    assert error["code"] == "tool_no_permitida"
    assert "informar" in error["message"]
    assert "tomar_pedido" in error["message"]
    assert "confirmar_pedido" not in error["message"]


def test_armar_pedido_sin_el_mensaje_del_cliente_no_registra():
    """CA-188: el control falla cerrado: sin el mensaje no hay aceptación que comprobar (RF-111)."""
    propuesta = OrderDraft(
        draft_id="d",
        customer_id="c",
        channel=Channel.WEB,
        items=[OrderItem(sku="SH-PAN-400", product_name="Shampoo Pantene", quantity=1)],
    )
    error = _error("armar_pedido", {}, disponibles=["armar_pedido"], propuesta=propuesta)
    assert error["code"] == "aceptacion_no_clara"


def test_armar_pedido_sin_propuesta_dice_que_no_hay_nada_que_registrar():
    error = _error("armar_pedido", {}, disponibles=list(tool_registry.BASE_TOOLS))
    assert error["code"] == "tool_no_permitida"
    assert "propuesta vigente" in error["message"]
    assert "leer_skill" not in error["message"]


def test_en_el_cierre_no_sugiere_leer_una_skill():
    """RNF-16: en el cierre solo queda responder; mandarlo a leer_skill le hace gastar las vueltas que le quedan."""
    error = _error("search_products", {"query": "shampoo"}, disponibles=["respond_to_customer"])
    assert error["code"] == "tool_no_permitida"
    assert "leer_skill" not in error["message"]
    assert "respond_to_customer" in error["message"]


def test_tool_inexistente_no_sugiere_leer_una_skill():
    error = _error("borrar_todo", {}, disponibles=list(tool_registry.BASE_TOOLS))
    assert error["message"] == "No existe la tool 'borrar_todo': usá solo las que tenés"


def test_skill_inexistente_lista_las_del_indice():
    error = _error("leer_skill", {"nombre": "vender"}, disponibles=list(tool_registry.BASE_TOOLS))
    assert error["code"] == "skill_inexistente"
    assert "informar" in error["message"]
    assert "tomar_pedido" in error["message"]
    assert "confirmar_pedido" not in error["message"]  # sin propuesta, el índice no la muestra


def test_errores_de_br_dicen_cual_leer():
    no_permitida = _error("leer_br", {"id": "BR-01"}, disponibles=list(tool_registry.BASE_TOOLS))
    assert no_permitida["code"] == "br_no_permitida"
    assert "tomar_pedido" in no_permitida["message"]
    inexistente = _error(
        "leer_br", {"id": "BR-99"}, disponibles=list(tool_registry.BASE_TOOLS), skills_activas=["tomar_pedido"]
    )
    assert inexistente["code"] == "br_inexistente"
    assert "BR-01 (Falta de stock)" in inexistente["message"]


def test_argumentos_invalidos_vuelven_al_llm():
    ctx = tool_registry.TurnContext(customer_id="c", channel=Channel.WEB, disponibles=["check_stock"])
    result = tool_registry.execute("check_stock", {"sku": "SH-PAN-400", "quantity": 0}, ctx)
    assert result["error"]["code"] == "argumentos_invalidos"


def test_catalogo_no_disponible_le_dice_al_llm_que_no_proponga(monkeypatch):
    """El system prompt solo pide seguir `error.message`: la regla de no proponer tiene que venir en el error."""

    def sin_catalogo():
        raise OSError("sin acceso")

    monkeypatch.setattr(sheets_service, "get_catalog", sin_catalogo)
    ctx = tool_registry.TurnContext(customer_id="c", channel=Channel.WEB, disponibles=["search_products"])
    result = tool_registry.execute("search_products", {"query": "shampoo"}, ctx)
    assert result["error"]["code"] == "catalogo_no_disponible"
    assert "No propongas nada" in result["error"]["message"]


def test_check_stock_ejecuta_con_argumentos_validos():
    ctx = tool_registry.TurnContext(customer_id="c", channel=Channel.WEB, disponibles=["check_stock"])
    result = tool_registry.execute("check_stock", {"sku": "SH-PAN-400", "quantity": 2}, ctx)
    assert result["ok"] is True
    assert result["data"]["is_enough"] is True


def _ejecutar(name, **arguments):
    ctx = tool_registry.TurnContext(customer_id="c", channel=Channel.WEB, disponibles=list(tool_registry.TOOLS))
    return tool_registry.execute(name, arguments, ctx)["data"]


def test_check_stock_solo_da_el_numero_si_no_alcanza():
    """BR-01: la crema de enjuague Sedal tiene 4 unidades; el LLM solo ve cuántas quedan cuando no alcanzan."""
    assert _ejecutar("check_stock", sku="CE-SED-340", quantity=4) == {
        "sku": "CE-SED-340", "requested": 4, "is_enough": True
    }
    assert _ejecutar("check_stock", sku="CE-SED-340", quantity=5) == {
        "sku": "CE-SED-340", "requested": 5, "available": 4, "is_enough": False
    }


def test_busqueda_con_cantidad_dice_si_alcanza_sin_revelar_el_stock():
    """Con `quantity` no hace falta `check_stock` después de buscar, y el número solo aparece si no alcanza."""
    alcanza = _ejecutar("search_products", query="crema de enjuague sedal", quantity=4)["matches"][0]
    assert (alcanza["sku"], alcanza["alcanza"]) == ("CE-SED-340", True)
    assert "disponible" not in alcanza
    no_alcanza = _ejecutar("search_products", query="crema de enjuague sedal", quantity=5)["matches"][0]
    assert (no_alcanza["alcanza"], no_alcanza["disponible"]) == (False, 4)
    sin_cantidad = _ejecutar("search_products", query="crema de enjuague sedal")["matches"][0]
    assert not {"alcanza", "disponible"} & sin_cantidad.keys()


def test_explorar_catalogo_marcas_por_categoria():
    """RF-35: Consulta marcas disponibles en una categoría."""
    res = catalog_tools.explorar_catalogo(category="shampoo")
    assert res["tipo_consulta"] == "marcas_por_categoria"
    assert res["total_marcas"] > 0
    assert any(m in res["marcas"] for m in ["Pantene", "Sedal", "Elvive", "ALFAPARF", "FIDELITE"])


def test_explorar_catalogo_categorias_por_marca():
    """RF-35: Consulta resumen de categorías de una marca."""
    res = catalog_tools.explorar_catalogo(brand="pantene")
    assert res["tipo_consulta"] == "categorias_por_marca"
    assert res["total_productos"] > 0
    assert "Shampoo" in res["categorias"]


def test_explorar_catalogo_marca_no_encontrada():
    """RF-35: Devuelve mensaje claro si la marca no existe."""
    res = catalog_tools.explorar_catalogo(brand="marca_inexistente_xyz")
    assert res["tipo_consulta"] == "marca_no_encontrada"
    assert res["total_productos"] == 0


def test_explorar_catalogo_productos_por_marca_y_categoria():
    """RF-35: Devuelve muestra de productos acotada por limit sin fugar stock crudo."""
    res = catalog_tools.explorar_catalogo(brand="pantene", category="shampoo", limit=2)
    assert res["tipo_consulta"] == "productos_por_marca_y_categoria"
    assert len(res["muestra_productos"]) <= 2
    assert res["total_productos"] >= len(res["muestra_productos"])
    for prod in res["muestra_productos"]:
        assert "stock" not in prod
        assert "hay_stock" in prod
        assert isinstance(prod["hay_stock"], bool)


def test_explorar_catalogo_general():
    """RF-35: Sin parámetros devuelve categorías generales."""
    res = catalog_tools.explorar_catalogo()
    assert res["tipo_consulta"] == "categorias_generales"
    assert "Shampoo" in res["categorias"]



def _contexto(*skills: str) -> tool_registry.TurnContext:
    return tool_registry.TurnContext(
        customer_id="cli-1",
        channel=Channel.TELEGRAM,
        disponibles=tool_registry.available(list(skills)),
        skills_activas=list(skills),
    )


def test_br_junto_al_resultado():
    """CA-119: el resultado trae la BR que dispara su señal, una sola vez por turno (RF-63)."""
    ctx = _contexto("tomar_pedido")

    primera = tool_registry.execute("search_products", {"query": "shampo pantne 400"}, ctx)
    segunda = tool_registry.execute("search_products", {"query": "acondicionador pantne"}, ctx)

    assert primera["data"]["matches"][0]["aproximado"]
    assert primera["reglas"] == [{"id": "BR-09", "texto": skill_service.get_skills()["tomar_pedido"].br["BR-09"]}]
    assert "reglas" not in segunda
    assert ctx.br_entregadas == ["BR-09"]


@pytest.mark.parametrize(
    ("tool", "argumentos", "br"),
    [
        ("search_products", {"query": "shampoo pantene"}, "BR-03"),  # dos medidas
        ("search_products", {"query": "plancha de pelo titanio"}, "BR-04"),
        ("search_products", {"query": "koleston 7.1"}, "BR-01"),  # el único resultado está agotado
        ("check_stock", {"sku": "CE-SED-340", "quantity": 10}, "BR-01"),
        ("respond_to_customer",
         {"kind": "propuesta", "message": "Van las 10.", "propuesta": [{"sku": "CE-SED-340", "quantity": 10}]},
         "BR-01"),
        ("search_products", {"query": "shampoo pantene 400"}, None),  # un solo producto, con stock
        ("search_products", {"query": "crema de enjuague sedal", "quantity": 5}, "BR-01"),  # hay 4
        ("search_products", {"query": "crema de enjuague sedal", "quantity": 4}, None),
        ("get_product", {"sku": "SH-PAN-400"}, None),
    ],
)
def test_cada_senal_lleva_a_su_br(tool, argumentos, br):
    """RF-63: stock corto, varias opciones, nada parecido o una coincidencia aproximada."""
    resultado = tool_registry.execute(tool, argumentos, _contexto("tomar_pedido"))
    assert [regla["id"] for regla in resultado.get("reglas", [])] == ([br] if br else [])


def test_sin_skill_activa_no_se_adjunta_la_br():
    """RF-63: una BR solo llega si una skill leída en el turno la permite; si no, el agente la lee con `leer_br`."""
    for skills in ((), ("informar",)):
        ctx = _contexto(*skills)
        assert "reglas" not in tool_registry.execute("search_products", {"query": "shampo pantne 400"}, ctx)
        assert ctx.br_entregadas == []


def test_br_leida_por_el_agente_no_se_vuelve_a_adjuntar():
    ctx = _contexto("tomar_pedido")
    assert tool_registry.execute("leer_br", {"id": "BR-09"}, ctx)["ok"]
    assert "reglas" not in tool_registry.execute("search_products", {"query": "shampo pantne 400"}, ctx)


def test_control_de_cobertura():
    """CA-126: cada renglón numerado termina en `propuesta`, en `pendientes` o en `afuera`; si no, falla."""
    from internal.schemas.agent_schema import RespondToCustomerInput
    from internal.tools import reply_tools

    renglones = [{"n": n, "texto": f"renglón {n}", "marca": None, "estado": "sin_resolver"} for n in range(1, 11)]
    respuesta = {
        "kind": "propuesta",
        "message": "Te armé el pedido.",
        "propuesta": [{"sku": "SH-PAN-400", "quantity": 1, "renglon": n} for n in range(1, 8)],
        "pendientes": [{"renglon": 8, "opciones": ["SH-PAN-200", "SH-PAN-400"]}],
        "afuera": [{"renglon": 9, "motivo": "fuera_de_catalogo"}],
    }

    with pytest.raises(ToolFailure) as error:
        reply_tools.cubrir_renglones(RespondToCustomerInput(**respuesta), renglones)
    assert error.value.code == "renglones_sin_cuenta"
    assert error.value.details == {"renglones": [10]}

    respuesta["afuera"].append({"renglon": 10, "motivo": "no_es_producto"})
    cubiertos = reply_tools.cubrir_renglones(RespondToCustomerInput(**respuesta), renglones)
    assert [r["estado"] for r in cubiertos] == ["resuelto"] * 7 + ["pendiente", "afuera", "afuera"]
    assert all(renglon["estado"] == "sin_resolver" for renglon in renglones)  # no toca la lista que recibe


def test_mensaje_largo_solo_con_dudas():
    """RF-70: el mensaje puede pasar los 600 caracteres solo cuando junta las dudas de una lista."""
    from pydantic import ValidationError

    from internal.schemas.agent_schema import RespondToCustomerInput

    largo = "¿Cuál de las dos medidas te mando? " * 25
    with pytest.raises(ValidationError):
        RespondToCustomerInput(kind="aclaracion", message=largo)
    assert RespondToCustomerInput(kind="aclaracion", message=largo, pendientes=[{"renglon": 1, "opciones": []}])
    with pytest.raises(ValidationError):
        RespondToCustomerInput(kind="aclaracion", message=largo * 2, pendientes=[{"renglon": 1, "opciones": []}])


def test_explorar_catalogo_marca_no_encontrada_sugiere_la_parecida():
    res = catalog_tools.explorar_catalogo(brand="pantenne")
    assert res["tipo_consulta"] == "marca_no_encontrada"
    assert "Pantene" in res["_hint"]
    res = catalog_tools.explorar_catalogo(brand="marca_inexistente_xyz")
    assert "ni otra parecida" in res["_hint"]


def test_explorar_catalogo_categoria_inexistente_sugiere_la_parecida():
    res = catalog_tools.explorar_catalogo(category="champu")
    assert res["total_productos"] == 0
    assert "Shampoo" in res["_hint"]


@pytest.mark.parametrize("escrito", ["L'Oréal", "l’oreal", "LOREAL", "  Loréal  "])
def test_explorar_catalogo_ignora_tildes_y_signos(escrito):
    """«L'Oréal», como lo nombra el system prompt, encuentra la marca cargada como LOREAL."""
    assert catalog_tools._normalizar(escrito) == "loreal"


@pytest.mark.parametrize("escrito", ["Anti-Frizz", "anti frizz", "Anti / Frizz", "ANTI_FRIZZ"])
def test_los_signos_entre_palabras_separan(escrito):
    """«Anti-Frizz» no queda «antifrizz»: si no, «anti frizz» no encuentra la categoría."""
    assert catalog_tools._normalizar(escrito) == "anti frizz"


def test_explorar_catalogo_encuentra_la_marca_escrita_con_apostrofo(monkeypatch):
    loreal = Product(sku="LO-SH-1", name="Shampoo Loreal", brand="LOREAL", category="Shampoo", price=1, stock=3)
    monkeypatch.setattr(sheets_service, "get_catalog", lambda: [loreal])
    res = catalog_tools.explorar_catalogo(brand="L'Oréal")
    assert res["tipo_consulta"] == "categorias_por_marca"
    assert res["categorias"] == {"Shampoo": 1}


def test_explorar_catalogo_marca_sin_esa_categoria_ofrece_otras_marcas():
    """Elvive solo tiene shampoo: el hint dice qué tiene la marca y qué marcas tienen acondicionador."""
    res = catalog_tools.explorar_catalogo(brand="elvive", category="acondicionador")
    assert res["total_productos"] == 0
    assert "De esa marca hay: Shampoo." in res["_hint"]
    assert "Tienen esa categoría: Pantene, Sedal." in res["_hint"]


def test_pedidos_anteriores_marca_lo_que_ya_no_esta_en_el_catalogo(monkeypatch):
    """Lo que compró hace un mes puede no estar hoy: el agente no lo propone y llega BR-11 (cómo usar la tool)."""
    lineas = [
        {"sku": "SH-PAN-400", "name": "Shampoo", "cantidad": 2},
        {"sku": "XX-VIEJO", "name": "Otro", "cantidad": 1},
    ]
    monkeypatch.setattr(order_tools, "pedidos_anteriores", lambda *_: [{"fecha": "2026-10-01", "lineas": lineas}])
    resultado = tool_registry.execute("pedidos_anteriores", {}, _contexto("tomar_pedido"))
    nuevas = resultado["data"]["pedidos"][0]["lineas"]
    assert [(linea["en_catalogo"], linea.get("hay_stock")) for linea in nuevas] == [(True, True), (False, None)]
    assert "BR-04" in resultado["data"]["_hint"]
    assert [regla["id"] for regla in resultado["reglas"]] == ["BR-11", "BR-04"]


def test_pedidos_anteriores_dice_si_alcanza_para_repetirlo(monkeypatch):
    """BR-11: de la crema de enjuague Sedal quedan 4 y la otra vez llevó 10; el agente lo sabe sin check_stock."""
    lineas = [
        {"sku": "SH-PAN-400", "name": "Shampoo", "cantidad": 1},
        {"sku": "CE-SED-340", "name": "Crema de enjuague", "cantidad": 10},
    ]
    monkeypatch.setattr(order_tools, "pedidos_anteriores", lambda *_: [{"fecha": "2026-10-01", "lineas": lineas}])
    resultado = tool_registry.execute("pedidos_anteriores", {}, _contexto("tomar_pedido"))
    shampoo, crema = resultado["data"]["pedidos"][0]["lineas"]
    assert shampoo["alcanza"] and "disponible" not in shampoo
    assert (crema["alcanza"], crema["disponible"]) == (False, 4)
    assert [regla["id"] for regla in resultado["reglas"]] == ["BR-11", "BR-01"]


def test_pedidos_anteriores_vacio_lleva_a_br_11(monkeypatch):
    monkeypatch.setattr(order_tools, "pedidos_anteriores", lambda *_: [])
    data = tool_registry.execute("pedidos_anteriores", {}, _contexto())["data"]
    assert data == {"pedidos": [], "_hint": "Este cliente no tiene pedidos anteriores por este chat: seguí BR-11."}


def test_pedidos_anteriores_lee_el_catalogo_una_sola_vez(monkeypatch):
    lineas = [{"sku": "SH-PAN-400", "name": "Shampoo", "cantidad": 1}] * 5
    monkeypatch.setattr(order_tools, "pedidos_anteriores", lambda *_: [{"fecha": "2026-10-01", "lineas": lineas}] * 3)
    lecturas = []
    original = inventory_service.get_catalog
    monkeypatch.setattr(inventory_service, "get_catalog", lambda *a, **k: lecturas.append(1) or original(*a, **k))
    tool_registry.execute("pedidos_anteriores", {}, _contexto("tomar_pedido"))
    assert len(lecturas) == 1


def test_el_prompt_del_sub_loop_trae_el_titulo_de_cada_br():
    skill = skill_service.get_skills()["tomar_pedido"]
    prompt = sub_loop.construir_prompt(skill, "Resolvé el pedido", [], [], None, "", [])
    assert "BR-01 (Falta de stock)" in prompt


def test_explorar_catalogo_avisa_cuando_recorta_la_muestra():
    res = catalog_tools.explorar_catalogo(brand="pantene", category="shampoo", limit=1)
    assert len(res["muestra_productos"]) == 1
    assert "Se muestran 1 de 2 productos" in res["_hint"]
    assert "_hint" not in catalog_tools.explorar_catalogo(brand="pantene", category="shampoo", limit=2)


def test_get_product_inexistente_explica_como_seguir():
    ctx = tool_registry.TurnContext(customer_id="c", channel=Channel.WEB, disponibles=["get_product"])
    result = tool_registry.execute("get_product", {"sku": "NO-EXISTE"}, ctx)
    assert result["data"]["product"] is None
    assert "search_products" in result["data"]["_hint"]


def test_search_products_sin_coincidencias_trae_hint():
    ctx = tool_registry.TurnContext(customer_id="c", channel=Channel.WEB, disponibles=["search_products"])
    result = tool_registry.execute("search_products", {"query": "kerastase elixir"}, ctx)
    assert result["data"]["matches"] == []
    assert "«kerastase»" in result["data"]["_hint"]


def test_tool_discard_order_draft(base_de_datos_inicializada):
    """RF-26 / CA-23: discard_order_draft marca el borrador como descartado y cierra el turno."""
    ctx = tool_registry.TurnContext(
        customer_id="c1",
        channel=Channel.WEB,
        disponibles=["discard_order_draft"],
    )
    result = tool_registry.execute("discard_order_draft", {"reason": "cliente_cancelo"}, ctx)
    assert result["ok"]
    assert result["data"]["descartado"] is True
    assert ctx.kind == "descartado"
    assert ctx.resultado == "final"
    assert "cancelé" in ctx.respuesta.lower()


def test_tool_escalate_to_human():
    """RF-27 / CA-22: escalate_to_human deriva la conversación y guarda el motivo."""
    ctx = tool_registry.TurnContext(
        customer_id="c2",
        channel=Channel.TELEGRAM,
        disponibles=["escalate_to_human"],
    )
    result = tool_registry.execute(
        "escalate_to_human",
        {"reason": "condicion_comercial", "message": "Te paso con ventas para coordinar."},
        ctx,
    )
    assert result["ok"]
    assert result["data"]["derivada"] is True
    assert ctx.kind == "derivada"
    assert ctx.resultado == "final"
    assert ctx.derivation_reason == "condicion_comercial"
    assert ctx.respuesta == "Te paso con ventas para coordinar."


def test_tool_escalate_to_human_rechaza_motivo_invalido():
    """EscalateToHumanInput solo acepta los motivos tipificados en DerivationReason."""
    ctx = tool_registry.TurnContext(
        customer_id="c2",
        channel=Channel.TELEGRAM,
        disponibles=["escalate_to_human"],
    )
    result = tool_registry.execute(
        "escalate_to_human",
        {"reason": "motivo_inventado", "message": "Te paso con ventas."},
        ctx,
    )
    assert not result["ok"]
    assert ctx.resultado == "continuar"


def test_tool_escalate_y_discard_validan_guardrails():
    """El mensaje libre de escalate_to_human y discard_order_draft pasa por guardrails.validar_salida."""
    ctx_esc = tool_registry.TurnContext(
        customer_id="c2",
        channel=Channel.TELEGRAM,
        disponibles=["escalate_to_human"],
    )
    res_esc = tool_registry.execute(
        "escalate_to_human",
        {"reason": "condicion_comercial", "message": "Te hago un 20% de descuento y te paso con ventas."},
        ctx_esc,
    )
    assert not res_esc["ok"]
    assert res_esc["error"]["code"] == "respuesta_rechazada"
    assert ctx_esc.resultado == "continuar"

    ctx_disc = tool_registry.TurnContext(
        customer_id="c2",
        channel=Channel.TELEGRAM,
        disponibles=["discard_order_draft"],
    )
    res_disc = tool_registry.execute(
        "discard_order_draft",
        {"reason": "cliente_cancelo", "message": "Cancelado, la próxima te hacemos un 15% de descuento."},
        ctx_disc,
    )
    assert not res_disc["ok"]
    assert res_disc["error"]["code"] == "respuesta_rechazada"
    assert ctx_disc.resultado == "continuar"

