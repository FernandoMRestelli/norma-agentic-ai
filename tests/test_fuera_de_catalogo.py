"""Lo que no está en el catálogo: del rubro, fuera del rubro y stock 0 (RF-154 a RF-157, BR-04).

La búsqueda da `no_encontrado` igual para una tintura de otra marca que para un kilo de yerba: el rubro lo decide el
LLM siguiendo la skill. Acá se controla, sin LLM, lo que la skill le dice y lo que el código hace con su decisión.
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import get_args

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langgraph.checkpoint.memory import InMemorySaver

from internal import evaluar_agente
from internal.agent import nota
from internal.agent.norma_agent import NormaAgent
from internal.schemas.agent_schema import EntregarNotaInput, MotivoAfuera, RespondToCustomerInput
from internal.schemas.catalog_schema import Product
from internal.schemas.order_schema import Channel
from internal.services import order_service, sheets_service, skill_service
from internal.tools import catalog_tools, skill_tools
from tests.test_agent_graph import ModeloGuionado, _ai, _call

RAIZ = Path(__file__).resolve().parent.parent
SKU = "SH-SINT-1"
CLIENTE = {"customer_id": "telegram:154", "channel": Channel.TELEGRAM}
MOTIVOS_NUEVOS = ("a_consultar", "fuera_del_rubro")


@pytest.fixture
def catalogo(monkeypatch):
    productos = [
        Product(sku=SKU, name="Shampoo Sintético", presentation="400 ml", price=Decimal("250"), stock=10),
    ]
    monkeypatch.setattr(sheets_service, "get_catalog", lambda: productos)
    return productos


def _br(id: str) -> str:
    return skill_tools.leer_br(id, ["tomar_pedido"])["texto"]


def test_br04_separa_los_tres_casos():
    """CA-257: del rubro, fuera del rubro y stock 0, cada uno con su motivo, y ante la duda es del rubro."""
    br04 = _br("BR-04")
    for motivo in (*MOTIVOS_NUEVOS, "sin_stock"):
        assert f"`{motivo}`" in br04, motivo
    assert "BR-01" in br04 and "kind = fuera_de_catalogo" in br04
    assert "Ante la duda, es del rubro" in br04 and "explorar_catalogo" in br04
    for categoria in ("shampoo", "acondicionador", "máscara", "coloración", "oxidante", "decolorante", "peinado",
                      "cosmética", "accesorios"):  # fmt: skip
        assert categoria in br04, categoria
    assert "no afirmes que no lo trabajan" in br04

    skill = skill_service.get_skills()["tomar_pedido"]
    for situacion in ("Del rubro, no cargado", "Fuera del rubro", "Agotado"):
        assert f"**{situacion}:**" in skill.instrucciones, situacion
    for texto in (skill.instrucciones, skill.respuesta):
        assert "no lo trabajamos" not in texto and "no la trabajamos" not in texto
    for motivo in MOTIVOS_NUEVOS:
        assert f"`{motivo}`" in skill.respuesta and f"`{motivo}`" in skill.sub_loop


def test_br01_ofrece_anotarlo():
    """CA-264 / RF-156: con stock 0 ofrece anotarlo, sin dar por anotado lo que no tiene pedido."""
    br01 = _br("BR-01")
    assert "anotárselo para cuando llegue" in br01
    assert "`notes`" in br01 and "no digas que ya quedó anotado" in br01
    assert "la pregunta que cierra sigue siendo si la confirma" in br01


@pytest.mark.parametrize("motivo", MOTIVOS_NUEVOS)
def test_los_motivos_validan_en_el_esquema(motivo):
    """CA-258: el motivo vale en la respuesta y en la nota de un sub-loop, y el LLM sabe cuándo usarlo."""
    respuesta = RespondToCustomerInput.model_validate(
        {"kind": "fuera_de_catalogo", "message": "Eso no va.", "afuera": [{"renglon": 1, "motivo": motivo}]}
    )
    entrega = EntregarNotaInput.model_validate({"afuera": [{"renglon": 1, "motivo": motivo}]})
    assert respuesta.afuera[0].motivo == entrega.afuera[0].motivo == motivo
    assert motivo in get_args(MotivoAfuera)
    for esquema in (RespondToCustomerInput, EntregarNotaInput):
        definiciones = esquema.model_json_schema()["$defs"].values()
        propiedades = [d.get("properties", {}) for d in definiciones]
        (descripcion,) = [p["motivo"]["description"] for p in propiedades if "motivo" in p]
        assert f"{motivo}: " in descripcion
        assert "no lo trabajan" not in descripcion


def test_los_motivos_llegan_al_pedido_guardado(catalogo):
    """CA-259 / RF-155: se propone lo que está; cada renglón que no entra se avisa y se guarda con su motivo."""
    proponer = _call(
        "respond_to_customer",
        kind="propuesta",
        message="Te armé los shampoos y hay dos cosas que no van. ¿Lo confirmo?",
        propuesta=[{"sku": SKU, "quantity": 2, "renglon": 1}],
        afuera=[{"renglon": 2, "motivo": "a_consultar"}, {"renglon": 3, "motivo": "fuera_del_rubro"}],
    )
    modelo = ModeloGuionado(_ai(_call("leer_skill", nombre="tomar_pedido"), _call("get_product", sku=SKU)),
                            _ai(proponer))  # fmt: skip
    agent = NormaAgent(modelo, InMemorySaver())

    propuesta = agent.handle("154", text="2 shampoo sintetico\n1 planchita\n1 pizza", **CLIENTE)
    final = agent.handle("154", text="dale", **CLIENTE)

    assert propuesta.message.endswith(
        "Lo que no va como lo pediste:\n"
        "• 1 planchita: no lo tengo cargado, lo consulto con el equipo\n"
        "• 1 pizza: eso no lo vendemos, somos de perfumería y peluquería 😄"
    )
    order = order_service.get_order(final.order_id)
    assert [(left.source_text, left.reason) for left in order.leftovers] == [
        ("1 planchita", "a_consultar"),
        ("1 pizza", "fuera_del_rubro"),
    ]
    fila = dict(zip(sheets_service.ORDER_COLUMNS, sheets_service.format_order_row(order), strict=True))
    assert fila["fuera_del_pedido"] == "1 planchita (a_consultar); 1 pizza (fuera_del_rubro)"
    order_service.cancel_order(final.order_id, source=order_service.EventSource.AGENT)


def test_la_nota_del_sistema_lo_manda_a_consultar():
    """CA-261: sin el LLM no se sabe si es del rubro, y ante la duda lo es."""
    buscado = {1: ({"n": 1, "texto": "algo"}, {"estado": "no_encontrado", "candidatos": []})}
    assert nota.con_lo_encontrado([1], buscado)["afuera"] == [{"renglon": 1, "motivo": "a_consultar"}]


def test_las_pistas_no_dicen_que_no_se_trabaja(catalogo):
    """CA-262: el `_hint` manda a BR-04, y el system prompt aclara que pedir algo de otro rubro es un pedido."""
    hints = [
        catalog_tools.hint_busqueda(catalog_tools.buscar_productos("yerba", 5)),
        catalog_tools.hint_busqueda(catalog_tools.buscar_productos("shampoo zzzzmarca", 5)),
        catalog_tools.explorar_catalogo(brand="zzzzmarca")["_hint"],
        catalog_tools.explorar_catalogo(category="ferreteria")["_hint"],
    ]
    for hint in hints:
        assert "BR-04" in hint, hint
        assert "no lo trabaj" not in hint and "no la trabaj" not in hint and "no se trabaja" not in hint, hint

    system_prompt = (RAIZ / "internal" / "agent" / "system_prompt.md").read_text(encoding="utf-8")
    assert "algo que no se vende acá (comida, bebida, ferretería) no es un tema ajeno" in system_prompt


def test_fuera_del_rubro_pasa_los_guardrails(catalogo):
    """CA-263: la respuesta con buena onda llega al cliente tal cual, sin propuesta."""
    mensaje = "Somos una distribuidora de perfumería y peluquería, eso no lo vendemos 😄"
    modelo = ModeloGuionado(
        _ai(_call("leer_skill", nombre="tomar_pedido"), _call("search_products", query="yerba")),
        _ai(_call("respond_to_customer", kind="fuera_de_catalogo", message=mensaje)),
    )

    resultado = NormaAgent(modelo, InMemorySaver()).handle("263", text="un kilo de yerba", **CLIENTE)

    assert (resultado.kind, resultado.message, resultado.draft) == ("fuera_de_catalogo", mensaje, None)


# --- Evaluación (RF-157) -----------------------------------------------------------------------------------------


def _del_rubro(grupo: str) -> list[dict]:
    casos = json.loads((RAIZ / "data_example" / "adversariales.json").read_text(encoding="utf-8"))
    return [c for c in evaluar_agente.del_tema(casos, "rubro") if c["grupo"] == grupo]


@pytest.mark.parametrize("grupo", evaluar_agente.GRUPOS)
def test_casos_del_rubro_en_los_dos_grupos(grupo):
    """CA-265: fuera del rubro solo y mezclado, del rubro sin cargar y un producto con stock 0."""
    casos = _del_rubro(grupo)
    mensajes = {c["mensaje"]["text"] for c in casos}
    esperados = [c["esperado"] for c in casos]
    motivos = [set(e["motivos"].values()) for e in esperados if "motivos" in e]
    assert {"fuera_del_rubro"} in motivos and {"a_consultar"} in motivos
    assert any(e["resultado"] == "sin_pedido" and "perfumer" in "".join(e.get("dice", [])) for e in esperados)
    assert any(e["resultado"] == "sin_pedido" and "consult" in e.get("dice", []) for e in esperados)
    assert any(e["resultado"] == "sin_stock" for e in esperados)
    otro = {"desarrollo": "reservado", "reservado": "desarrollo"}[grupo]
    assert not mensajes & {c["mensaje"]["text"] for c in _del_rubro(otro)}


class _Modelo(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@pytest.mark.parametrize(
    ("motivo", "correcto"), [("fuera_del_rubro", True), ("a_consultar", False)], ids=["igual", "distinto"]
)
def test_un_motivo_distinto_es_incorrecto(catalogo, motivo, correcto):
    """CA-265: la propuesta puede estar bien y el renglón de afuera, mal clasificado."""
    caso = {
        "id": f"eval-motivo-{motivo}",
        "mensaje": {"customer_id": f"cli-{motivo}", "channel": "telegram", "type": "text",
                    "text": "2 shampoo sintetico\n1 pizza"},
        "esperado": {"resultado": "borrador", "items": [{"sku": SKU, "cantidad": 2}],
                     "motivos": {"1 pizza": "fuera_del_rubro"}},
    }  # fmt: skip
    responder = _call(
        "respond_to_customer",
        kind="propuesta",
        message="Te armé los shampoos. ¿Lo confirmo?",
        propuesta=[{"sku": SKU, "quantity": 2, "renglon": 1}],
        afuera=[{"renglon": 2, "motivo": motivo}],
    )
    modelo = _Modelo(responses=[_ai(_call("leer_skill", nombre="tomar_pedido"), _call("get_product", sku=SKU)),
                                _ai(responder)])  # fmt: skip

    fila, _ = evaluar_agente._evaluar(NormaAgent(modelo, InMemorySaver()), caso)

    assert fila["resultado"] == ("correcto" if correcto else "incorrecto"), fila
    assert fila["motivos_mal"] == ("" if correcto else "1 pizza: a_consultar")


def test_elegir_tema():
    """CA-265: `--tema` corre un tema solo; sin él, todos."""
    casos = [{"id": "a", "tema": "rubro"}, {"id": "b", "tema": "negocio"}, {"id": "c"}]
    assert [c["id"] for c in evaluar_agente.del_tema(casos, "rubro")] == ["a"]
    assert evaluar_agente.del_tema(casos, None) == casos
