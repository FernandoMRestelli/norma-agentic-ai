"""Tools de catálogo, stock y lectura de skills, con el catálogo de ejemplo y sin LLM."""

import pytest

from internal.agent import tool_registry
from internal.schemas.agent_schema import ToolFailure
from internal.schemas.order_schema import Channel
from internal.services import sheets_service, skill_service
from internal.services.skill_service import SkillError, load_skills
from internal.tools import catalog_tools, inventory_tools, skill_tools


@pytest.mark.parametrize("query", ["shampoo pantene 400", "Shampoo Pantene 400ML", "shampo pantne 400"])
def test_busqueda_aproximada_trae_primero_el_producto(query):
    """CA-29 / RF-07: tolera mayúsculas y errores de tipeo."""
    assert catalog_tools.search_products(query)[0].sku == "SH-PAN-400"


def test_busqueda_sin_coincidencias_no_inventa():
    assert catalog_tools.search_products("head and shoulders xyz") == []


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
    assert "read_catalogo" in skill_service.get_skills()


def test_skill_con_tool_inexistente_impide_arrancar(monkeypatch):
    """CA-40."""
    skill = skill_service.Skill(nombre="mala", cuando="x", tools=["no_existe"], instrucciones="", br={})
    monkeypatch.setattr(tool_registry, "get_skills", lambda: {"mala": skill})
    with pytest.raises(SkillError, match="no_existe"):
        tool_registry.validate_skills()


def _crear_skill(base, encabezado):
    folder = base / "mi_skill"
    folder.mkdir()
    (folder / "skill.md").write_text(encabezado, encoding="utf-8")


def test_skill_sin_encabezado_impide_arrancar(tmp_path):
    _crear_skill(tmp_path, "sin encabezado")
    with pytest.raises(SkillError):
        load_skills(tmp_path)


def test_skill_con_nombre_distinto_a_su_carpeta_impide_arrancar(tmp_path):
    _crear_skill(tmp_path, "---\nnombre: otra\ncuando: x\ntools: [leer_br]\n---\nTexto")
    with pytest.raises(SkillError, match="otra"):
        load_skills(tmp_path)


@pytest.mark.parametrize("nombre", ["../../.env", "../read_catalogo", "/etc/passwd", "read_catalogo/skill.md"])
def test_leer_skill_no_lee_rutas(nombre):
    """CA-40: el nombre solo se compara contra el índice."""
    with pytest.raises(ToolFailure) as error:
        skill_tools.leer_skill(nombre)
    assert error.value.code == "skill_inexistente"


@pytest.mark.parametrize("id_br", ["../../.env", "BR/BR-01", "/etc/passwd"])
def test_leer_br_no_lee_rutas(id_br):
    """CA-40."""
    with pytest.raises(ToolFailure) as error:
        skill_tools.leer_br(id_br, ["read_catalogo"])
    assert error.value.code == "br_inexistente"


def test_leer_br_solo_de_skills_leidas():
    assert "Falta de stock" in skill_tools.leer_br("BR-01", ["read_catalogo"])["texto"]
    with pytest.raises(ToolFailure) as error:
        skill_tools.leer_br("BR-01", [])
    assert error.value.code == "br_no_permitida"


def test_tool_no_disponible_no_se_ejecuta():
    """CA-37."""
    ctx = tool_registry.TurnContext(customer_id="c", channel=Channel.WEB, disponibles=list(tool_registry.BASE_TOOLS))
    result = tool_registry.execute("armar_pedido", {}, ctx)
    assert result["error"]["code"] == "tool_no_permitida"


def test_argumentos_invalidos_vuelven_al_llm():
    ctx = tool_registry.TurnContext(customer_id="c", channel=Channel.WEB, disponibles=["check_stock"])
    result = tool_registry.execute("check_stock", {"sku": "SH-PAN-400", "quantity": 0}, ctx)
    assert result["error"]["code"] == "argumentos_invalidos"
