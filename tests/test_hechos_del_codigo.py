"""Los hechos de la respuesta los escribe el código, no el LLM (M8-2, docs/spec/mejoras-clase8.md).

El modelo escribe el marco: qué armó y la pregunta. Debajo, el sistema arma con el catálogo y los campos de
`respond_to_customer` el detalle, las dudas con sus opciones, lo que no entra y cuánto queda.
"""

from decimal import Decimal

import pytest

from internal.schemas.catalog_schema import Product
from internal.services import sheets_service, validation_service
from tests.test_agent_graph import CLIENTE, SKU
from tests.test_guardrails import SKU_SHAMPOO, _agente, _ai, _call, _responder, _resultados


@pytest.fixture(autouse=True)
def catalogo(monkeypatch):
    productos = [
        Product(sku=SKU, name="Crema Nivea", presentation="100 ml", price=Decimal("1000"), stock=3),
        Product(sku=SKU_SHAMPOO, name="Shampoo Pantene", presentation="400 ml", price=Decimal("7350"), stock=50),
        Product(sku="NOR-SH-KER-300", name="NORDIKA SHAMPOO KERATINA X 300 ML", price=Decimal("1"), stock=9),
        Product(sku="NOR-SH-KER-900", name="NORDIKA SHAMPOO KERATINA X 900 ML", price=Decimal("1"), stock=9),
        Product(sku="NOR-SH-KER-5L", name="NORDIKA SHAMPOO KERATINA X 5 L", price=Decimal("1"), stock=9),
        Product(sku="NOR-OX-20-90", name="NORDIKA OXIDANTE 20 VOL X 90 ML", price=Decimal("1"), stock=9),
        Product(sku="NOR-OX-20-900", name="NORDIKA OXIDANTE 20 VOL X 900 ML", price=Decimal("1"), stock=9),
    ]
    monkeypatch.setattr(sheets_service, "get_catalog", lambda: productos)
    return productos


def test_las_opciones_se_nombran_por_lo_que_las_distingue():
    """Tres shampoos que solo cambian en la medida se preguntan por la medida, sin repetir el nombre tres veces."""
    dudas = validation_service.build_dudas([
        ("2 sh keratina", ["NOR-SH-KER-300", "NOR-SH-KER-900", "NOR-SH-KER-5L"]),
        ("3 oxi 20", ["NOR-OX-20-90", "NOR-OX-20-900"]),
        ("1 shampoo", [SKU_SHAMPOO, "NOR-SH-KER-300"]),  # productos distintos: van los nombres completos
        ("4 crema", [SKU]),  # una sola opción es una confirmación (BR-09)
        ("2 cosa rara", []),
    ])  # fmt: skip
    assert dudas.splitlines() == [
        "Me falta que me digas:",
        "• 2 sh keratina: ¿300 ML, 900 ML o 5 L?",
        "• 3 oxi 20: ¿90 ML o 900 ML?",
        "• 1 shampoo: ¿Shampoo Pantene 400 ml o NORDIKA SHAMPOO KERATINA X 300 ML?",
        "• 4 crema: ¿es Crema Nivea 100 ml?",
        "• 2 cosa rara: ¿me lo aclarás?",
    ]
    assert validation_service.build_dudas([]) == ""


def test_distintivos_y_conjuncion():
    assert validation_service.distintivos(["A B 1", "A B 2"]) == ["1", "2"]
    assert validation_service.distintivos(["A B", "A B"]) == ["A B", "A B"]  # no se distinguen: nombres completos
    assert validation_service.distintivos(["A", "A B"]) == ["A", "A B"]  # nunca queda una opción vacía
    assert validation_service._o(["6", "8"]) == "6 u 8"
    assert validation_service._o(["el de 250", "el de 500"]) == "el de 250 o el de 500"


def test_lo_que_no_va_dice_su_motivo():
    afuera = validation_service.build_afuera(
        [("2 shampoo", "fuera_de_catalogo"), ("1 oxi 30", "sin_stock"), ("3 gel", "lo_saco_el_cliente"),
         ("Para el salón:", "no_es_producto")],
        [("5 deco 700", 2)],
    )  # fmt: skip
    assert afuera.splitlines() == [
        "Lo que no va como lo pediste:",
        "• 2 shampoo: no lo trabajamos",
        "• 1 oxi 30: está agotado por ahora",
        "• 5 deco 700: quedan 2, te puse esas",
    ]
    assert validation_service.build_afuera([("3 gel", "lo_saco_el_cliente")], []) == ""


def test_orden_de_la_respuesta():
    """El marco del LLM, el detalle, lo que no va y al final lo que el cliente tiene que contestar."""
    texto = validation_service.armar_respuesta("Hola.\n• una lista suya", dudas="D", afuera="A", opciones="O")
    assert texto == "Hola.\n\nA\n\nD\n\nO\n¿Cuál preferís?"
    assert validation_service.armar_respuesta("• solo viñetas", dudas="D") == "• solo viñetas\n\nD"
    assert validation_service.armar_respuesta("Sin bloques.\n• queda igual") == "Sin bloques.\n• queda igual"


def test_lo_que_no_alcanza_dice_cuanto_queda():
    """M8-2 y BR-01: un renglón propuesto con menos de lo pedido porque no alcanzó dice cuánto queda."""
    propuesta = [{"sku": SKU_SHAMPOO, "quantity": 2, "renglon": 1}, {"sku": SKU, "quantity": 3, "renglon": 2}]
    agent, _ = _agente(
        _ai(_call("leer_skill", nombre="tomar_pedido"), _call("get_product", sku=SKU_SHAMPOO),
            _call("check_stock", sku=SKU, quantity=5)),
        _responder("Te armé lo que hay. ¿Lo confirmás?", kind="sin_stock", propuesta=propuesta),
    )  # fmt: skip

    resultado = agent.handle("1", text="2 shampoo pantene\n5 crema nivea", **CLIENTE)

    assert [r["ok"] for r in _resultados(agent, "respond_to_customer")] == [True]
    assert resultado.message.endswith("Lo que no va como lo pediste:\n• 5 crema nivea: quedan 3, te puse esas")
    assert resultado.mensaje_modelo == "Te armé lo que hay. ¿Lo confirmás?"
