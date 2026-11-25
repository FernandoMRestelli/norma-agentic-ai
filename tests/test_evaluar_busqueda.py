"""Evaluación del buscador solo, sin LLM (RF-119): datos, métricas y piso de calidad."""

from decimal import Decimal

import pytest

from internal import evaluar_busqueda
from internal.schemas.catalog_schema import Product

GRUPOS = ("desarrollo", "reservado")
# Un poco por debajo de lo medido (D-A5): avisa si un cambio empeora la búsqueda, no si se mueve un caso
PISOS = {"recall_3": 0.90, "mrr": 0.89, "acierto_1": 0.88, "unicos_correctos": 0.86, "abstencion": 1.0}
MAX_NEGADOS = 3


@pytest.fixture(scope="module")
def filas():
    return evaluar_busqueda.correr()


def test_consultas_coherentes():
    """RF-119: ids únicos, cada comodín encuentra algo y los dos grupos cubren los mismos tipos."""
    productos = evaluar_busqueda.leer_catalogo()
    consultas = evaluar_busqueda.leer_consultas()
    assert len({c["id"] for c in consultas}) == len({c["consulta"] for c in consultas}) == len(consultas) >= 100
    for consulta in consultas:
        for patron in consulta["relevantes"]:
            assert evaluar_busqueda.relevantes({"relevantes": [patron]}, productos), consulta
    tipos = {grupo: {c["tipo"] for c in consultas if c["grupo"] == grupo} for grupo in GRUPOS}
    assert tipos["desarrollo"] == tipos["reservado"]
    assert sum(not c["relevantes"] for c in consultas) >= 20  # sin respuesta en el catálogo


@pytest.mark.parametrize("grupo", GRUPOS)
def test_piso_de_calidad(filas, grupo):
    """CA-206: un cambio en la búsqueda que empeora los resultados no entra."""
    metricas = evaluar_busqueda.metricas([f for f in filas if f["grupo"] == grupo])
    bajas = {nombre: (metricas[nombre], piso) for nombre, piso in PISOS.items() if metricas[nombre] < piso}
    assert not bajas
    assert metricas["negados"] <= MAX_NEGADOS


def test_ningun_falso_unico(filas):
    """CA-206: lo que sale como `unico` se propone sin preguntar: ahí no hay margen."""
    assert [f["consulta"] for f in filas if f["falso_unico"]] == []


def _producto(sku, nombre):
    return Product(sku=sku, name=nombre, brand="UNO", price=Decimal("1"), stock=5)


def _evaluar(consulta, relevantes):
    productos = [_producto("A-1", "UNO SHAMPOO COCO"), _producto("A-2", "UNO MASCARA PALTA"), _producto("B-1", "GEL")]
    caso = {"id": "x", "tipo": "t", "grupo": "desarrollo", "consulta": consulta, "relevantes": relevantes}
    evaluar_busqueda_leer = evaluar_busqueda.leer_consultas
    try:
        evaluar_busqueda.leer_consultas = lambda **_: [caso]
        (fila,) = evaluar_busqueda.correr(productos=productos)
    finally:
        evaluar_busqueda.leer_consultas = evaluar_busqueda_leer
    return fila


def test_metricas_de_una_consulta():
    """RF-119: acierto, falso único, negado y abstención, con resultados que se pueden hacer a mano."""
    bien = _evaluar("shampoo coco", ["A-1"])
    assert (bien["rr"], bien["recall_k"], bien["unico_correcto"], bien["falso_unico"]) == (1.0, 1.0, True, False)
    otro = _evaluar("shampoo coco", ["A-2"])
    assert (otro["rr"], otro["falso_unico"]) == (0.0, True)
    varios = _evaluar("shampoo coco", ["A-*"])  # hay dos respuestas y la búsqueda dio una como segura
    assert varios["falso_unico"] and varios["recall_k"] == 0.5
    negado = _evaluar("xqzw", ["A-1"])
    assert negado["negado"] and not negado["falso_unico"]
    nada = _evaluar("xqzw", [])
    assert not nada["negado"] and nada["estado"] == "no_encontrado"
    resumen = evaluar_busqueda.metricas([bien, otro, negado, nada])
    assert (resumen["mrr"], resumen["abstencion"], resumen["falsos_unicos"], resumen["negados"]) == (0.333, 1.0, 1, 1)
