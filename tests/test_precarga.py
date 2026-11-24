"""Precarga de la búsqueda: el backend busca la lista antes del LLM (spec del orquestador §3.2, RF-86)."""

from decimal import Decimal

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from internal.agent.norma_agent import NormaAgent
from internal.schemas.catalog_schema import Product
from internal.services import sheets_service
from internal.services.interpretation_service import separar_renglon
from internal.tools import catalog_tools
from tests.test_agent_graph import CLIENTE, ModeloGuionado, _agente, _ai, _call, base_limpia  # noqa: F401

LISTA = "Hola, te paso\n2 shampoo x900\n3 jabon glicerina"


@pytest.fixture
def catalogo(monkeypatch):
    productos = [
        Product(sku="SH-900", name="Shampoo Keratina", brand="Marca Uno", presentation="900 ml",
                price=Decimal("4500"), stock=20),
        Product(sku="JAB-GLI", name="Jabón Glicerina", brand="Marca Dos", presentation="100 g",
                price=Decimal("800"), stock=20),
        Product(sku="SH-KER-300", name="Shampoo Keratina", brand="Marca Tres", presentation="300 ml",
                price=Decimal("2500"), stock=20),
    ]  # fmt: skip
    monkeypatch.setattr(sheets_service, "get_catalog", lambda: productos)
    return productos


@pytest.fixture
def busquedas(monkeypatch):
    """Registra cada llamada a `buscar_renglones` del backend o de la tool."""
    llamadas: list[list[dict]] = []
    original = catalog_tools.buscar_renglones

    def espiar(renglones, preferidos=frozenset()):
        llamadas.append([dict(renglon) for renglon in renglones])
        return original(renglones, preferidos)

    monkeypatch.setattr(catalog_tools, "buscar_renglones", espiar)
    return llamadas


def _responder(*lineas, pendientes=()):
    propuesta = [{"sku": sku, "quantity": cantidad, "renglon": n} for n, sku, cantidad in lineas]
    return _ai(
        _call(
            "respond_to_customer",
            kind="propuesta",
            message="Te armé el pedido. ¿Lo confirmo así?",
            propuesta=propuesta,
            pendientes=[{"renglon": n, "opciones": opciones} for n, opciones in pendientes],
        )
    )


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("2 mascara argan x 1k", {"texto": "mascara argan x 1k", "cantidad": 2, "tono": None}),
        ("tinte 8.1/2", {"texto": "tinte", "cantidad": 2, "tono": "8.1"}),
        ("3 Jabón Glicerina", {"texto": "jabon glicerina", "cantidad": 3, "tono": None}),
        ("2 unidades de sh keratina", {"texto": "sh keratina", "cantidad": 2, "tono": None}),
        ("12 oxi 20 vol", {"texto": "oxi 20 vol", "cantidad": 12, "tono": None}),
        # La viñeta o el número de orden de una lista no son parte del renglón
        ("- 6 shampoo argan 900", {"texto": "shampoo argan 900", "cantidad": 6, "tono": None}),
        ("• 2 tinte 8.1", {"texto": "tinte 8.1", "cantidad": 2, "tono": None}),
        ("* tinte 7.0/3", {"texto": "tinte", "cantidad": 3, "tono": "7.0"}),
        ("1) 6 shampoo", {"texto": "shampoo", "cantidad": 6, "tono": None}),
        ("2. 3 jabon glicerina", {"texto": "jabon glicerina", "cantidad": 3, "tono": None}),
    ],
)
def test_separa_las_formas_inequivocas(texto, esperado):
    assert separar_renglon(texto) == esperado


@pytest.mark.parametrize(
    "texto",
    [
        "lo de siempre de keratina",  # depende del historial
        "sh keratina x900",  # sin cantidad
        "500 ml shampoo",  # el número es una medida
        "250 shampoo",  # demasiado para ser una cantidad
        "821/2",  # 8.21 o 82.1
        "koleston 7/3",  # tono sin punto
        "2 tinte 8.1/2",  # dos cantidades
        "el mismo shampoo de antes",
        "1) shampoo keratina",  # el 1 es el orden de la lista, no una cantidad
        "- 500 ml shampoo",  # con viñeta, el número sigue siendo una medida
    ],
)
def test_lo_que_admite_dudas_no_se_separa(texto):
    assert separar_renglon(texto) is None


def test_la_lista_se_busca_antes_del_llm(catalogo, busquedas):
    """CA-148: `buscar_renglones` corre antes del LLM con cantidades 2 y 3, y el modelo recibe la nota de búsqueda
    en el contexto, sin tool calls inventadas."""

    class Modelo(ModeloGuionado):
        def invoke(self, messages):
            self.busquedas_antes = len(busquedas)
            return super().invoke(messages)

    modelo = Modelo(_responder((1, "SH-900", 2), (2, "JAB-GLI", 3)))
    agent = NormaAgent(modelo, InMemorySaver())
    agent.handle("1", text=LISTA, **CLIENTE)

    assert modelo.busquedas_antes == 1
    (lote,) = busquedas
    assert [(r["n"], r["texto"], r["cantidad"]) for r in lote] == [(1, "shampoo x900", 2), (2, "jabon glicerina", 3)]
    recibidos = modelo.recibidos[0]
    sistema = recibidos[0].content
    assert "## Nota de búsqueda" in sistema
    assert '"cantidad": 2' in sistema and '"cantidad": 3' in sistema
    assert not any(isinstance(m, ToolMessage) or (isinstance(m, AIMessage) and m.tool_calls) for m in recibidos)


def test_renglon_ambiguo_queda_para_el_llm(catalogo, busquedas):
    """CA-149: un renglón sin forma inequívoca no se busca en la precarga y queda `sin_resolver`."""
    agent, modelo = _agente(
        _ai(_call("buscar_renglones", renglones=[{"n": 2, "texto": "shampoo keratina", "cantidad": 1}])),
        _responder((1, "SH-900", 2), pendientes=((2, ["SH-900", "SH-KER-300"]),)),
    )

    agent.handle("1", text="2 shampoo x900\nlo de siempre de keratina", **CLIENTE)

    precarga, del_modelo = busquedas
    assert [r["n"] for r in precarga] == [1]
    assert [r["n"] for r in del_modelo] == [2]
    sistema = modelo.recibidos[0][0].content
    assert "2. lo de siempre de keratina" in sistema.split("## Estado actual")[1]
    assert "Los que no figuran en la nota de búsqueda" in sistema


def test_lista_exacta_cierra_en_una_llamada(catalogo, busquedas):
    """CA-150: si la precarga encuentra todo exacto y con stock, el turno cierra con una sola llamada al LLM."""
    agent, modelo = _agente(_responder((1, "SH-900", 2), (2, "JAB-GLI", 3)))

    resultado = agent.handle("1", text=LISTA, **CLIENTE)

    assert (resultado.kind, resultado.iterations) == ("propuesta", 1)
    assert [(item.sku, item.quantity) for item in resultado.draft.items] == [("SH-900", 2), ("JAB-GLI", 3)]
    assert len(modelo.recibidos) == 1


def test_sin_catalogo_no_precarga(catalogo, monkeypatch):
    """§7: si el catálogo no se puede leer, no hay nota de búsqueda y el turno sigue como hoy."""

    def sin_catalogo(*args, **kwargs):
        raise OSError("catálogo no disponible")

    monkeypatch.setattr(catalog_tools, "buscar_renglones", sin_catalogo)
    agent, modelo = _agente(
        _ai(_call("leer_skill", nombre="informar")),
        _ai(_call("respond_to_customer", kind="informacion", message="No pude consultar el catálogo.")),
    )
    agent.handle("1", text=LISTA, **CLIENTE)
    assert "## Nota de búsqueda" not in modelo.recibidos[0][0].content


def test_el_faltante_de_la_precarga_se_puede_decir(catalogo):
    """BR-01 con la precarga: el número que quedó lo encontró el sistema, no una tool del turno, y aun así el
    validador de salida deja decirlo («me queda 1») junto a la propuesta de lo demás."""
    catalogo[1] = catalogo[1].model_copy(update={"stock": 1})
    agent, _ = _agente(
        _ai(
            _call(
                "respond_to_customer",
                kind="propuesta",
                message="Te armé los 2 shampoos. Del jabón de glicerina me queda 1, no llego a los 3: ¿te lo sumo?",
                propuesta=[{"sku": "SH-900", "quantity": 2, "renglon": 1}],
                pendientes=[{"renglon": 2, "opciones": ["JAB-GLI"]}],
            )
        )
    )

    resultado = agent.handle("1", text=LISTA, **CLIENTE)

    assert resultado.kind == "propuesta"
    assert resultado.message.startswith("Te armé los 2 shampoos. Del jabón de glicerina me queda 1")


def test_una_falla_inesperada_del_catalogo_no_corta_el_turno(catalogo, monkeypatch):
    """§7: la precarga nunca hace fallar el turno, aunque el catálogo falle con otro error que no sea de lectura."""

    def falla(*args, **kwargs):
        raise ValueError("respuesta rara de Sheets")

    monkeypatch.setattr(catalog_tools, "buscar_renglones", falla)
    pendientes = [{"renglon": 1, "opciones": []}, {"renglon": 2, "opciones": []}]
    agent, modelo = _agente(
        _ai(_call("respond_to_customer", kind="aclaracion", message="¿Qué shampoo y qué jabón querés?",
                  pendientes=pendientes)),
    )  # fmt: skip
    resultado = agent.handle("1", text=LISTA, **CLIENTE)
    assert resultado.kind == "aclaracion"
    assert "## Nota de búsqueda" not in modelo.recibidos[0][0].content
