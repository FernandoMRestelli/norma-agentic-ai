"""Evaluación con pedidos de formato real (M1 de docs/spec/toma-de-pedidos-por-texto.md): datos y métricas."""

import csv
import json
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from internal import evaluar_agente
from internal.agent.norma_agent import NormaAgent
from internal.schemas.catalog_schema import Product
from internal.schemas.order_schema import IncomingMessage
from tests import datos_sinteticos

DATOS = Path(__file__).resolve().parent.parent / "data_example"
RESULTADOS = {"borrador", "sin_stock", "pedido", "sin_pedido"}


@pytest.fixture(scope="module")
def catalogo() -> dict[str, Product]:
    return {product.sku: product for product in datos_sinteticos.catalogo_mayorista()}


def casos() -> list[dict]:
    return json.loads((DATOS / "pedidos_evaluacion.json").read_text(encoding="utf-8"))


def _renglones(caso: dict) -> int:
    turnos = evaluar_agente._turnos(caso)
    return sum(bool(linea.strip()) for turno in turnos for linea in turno.splitlines())


# --- Datos (RF-59) -----------------------------------------------------------------------------------------------


def test_catalogo_de_evaluacion_es_el_del_generador():
    """El CSV versionado es la salida de `catalogo_mayorista()`: si cambia uno, hay que regenerar el otro."""
    with open(DATOS / "catalogo_evaluacion.csv", encoding="utf-8", newline="") as archivo:
        filas = list(csv.reader(archivo))
    productos = datos_sinteticos.catalogo_mayorista()
    assert filas[0] == datos_sinteticos.COLUMNAS_CATALOGO
    assert filas[1:] == datos_sinteticos.filas_catalogo(productos)


def test_catalogo_de_evaluacion_copia_las_trampas_del_real(catalogo):
    productos = list(catalogo.values())
    assert len(productos) >= 300 and len(catalogo) == len(datos_sinteticos.catalogo_mayorista())
    assert all(product.presentation is None for product in productos)  # la medida va dentro del nombre
    coloraciones = [product for product in productos if product.category == "Coloración"]
    assert coloraciones and not any(caracter.isdigit() for p in coloraciones for caracter in p.name.split(" X ")[0])
    assert any(product.stock == 0 for product in productos)
    assert any(product.is_low_stock for product in productos)
    # Un mismo producto en varias medidas: «shampoo keratina» de una marca no alcanza para elegir
    assert len([p for p in productos if p.name.startswith("NORDIKA SHAMPOO KERATINA")]) == 3


@pytest.mark.parametrize("caso", casos(), ids=lambda caso: caso["id"])
def test_casos_de_pedidos_son_coherentes(caso, catalogo):
    IncomingMessage(**caso["mensaje"])
    esperado = caso["esperado"]
    assert esperado["resultado"] in RESULTADOS
    lineas = evaluar_agente.lineas_esperadas(esperado)
    if esperado["resultado"] == "sin_pedido":
        assert lineas is None and caso.get("previo")
        return
    assert lineas, "Un caso que espera una propuesta o un pedido dice qué líneas tiene que tener"
    for sku, cantidad in lineas.items():
        assert cantidad <= catalogo[sku].stock, f"{sku}: la propuesta esperada no puede pasar el stock"
    for duda in esperado.get("dudas", []):
        assert len(duda["opciones"]) > 1 and all(sku in catalogo for sku in duda["opciones"])
        assert not set(duda["opciones"]) & set(lineas), "Una duda no está resuelta en la propuesta esperada"
        assert duda["renglon"] in "\n".join(evaluar_agente._turnos(caso))
    for renglon in esperado.get("afuera", []):
        assert renglon in "\n".join(evaluar_agente._turnos(caso))
    if esperado["resultado"] == "sin_stock":
        assert any(item["cantidad"] > catalogo[item["sku"]].stock for item in esperado["items"])
    if esperado["resultado"] == "pedido":
        assert caso.get("previo")


def test_hay_pedidos_con_formato_real():
    """RF-59: listas largas por marca, coloración en tono/cantidad, dudas, fuera de catálogo y varios mensajes."""
    todos = casos()
    assert len({caso["id"] for caso in todos}) == len(todos) >= 10
    assert sum(_renglones(caso) >= 20 for caso in todos) >= 2
    assert any(_renglones(caso) >= 30 for caso in todos)
    assert any("7/3" in caso["mensaje"]["text"] for caso in todos)
    assert any(caso["esperado"].get("dudas") for caso in todos)
    assert any(caso["esperado"].get("afuera") for caso in todos)
    assert any(caso.get("previo") and caso["esperado"]["resultado"] == "borrador" for caso in todos)


# --- Métricas (RF-60) --------------------------------------------------------------------------------------------


def _esperado(n: int, **extra) -> dict:
    return {"resultado": "borrador", "items": [{"sku": f"SKU-{i}", "cantidad": 2} for i in range(n)], **extra}


def test_metricas_por_linea():
    """CA-113: con 28 de 30 líneas correctas el recall es 0,93, no un caso todo mal."""
    obtenidos = {f"SKU-{i}": 2 for i in range(28)} | {"SKU-28": 5}  # una con otra cantidad y una que falta
    lineas = evaluar_agente.medir_lineas(_esperado(30), obtenidos)

    assert lineas == {"lineas_esperadas": 30, "lineas_propuestas": 29, "lineas_correctas": 28, "lineas_perdidas": 1}
    fila = {"caso": "a", "resultado": "incorrecto", "iteraciones": 3, "latencia_ms": 10, "tokens_entrada": 0,
            "tokens_salida": 0, **lineas}
    metricas = evaluar_agente._metricas([fila])
    assert metricas["recall_lineas_pct"] == 93.3
    assert metricas["precision_lineas_pct"] == 96.6
    assert metricas["lineas_perdidas"] == 1


def test_una_linea_preguntada_no_esta_perdida():
    """Lo que el agente preguntó no se perdió; una duda sin ninguna opción a la vista, sí."""
    duda = {"renglon": "2 shampoo", "opciones": ["SKU-A", "SKU-B"]}
    esperado = _esperado(2, dudas=[duda])

    preguntada = evaluar_agente.medir_lineas(esperado, {"SKU-0": 2}, preguntados=["SKU-1", "SKU-A"])
    olvidada = evaluar_agente.medir_lineas(esperado, {"SKU-0": 2})

    assert preguntada["lineas_perdidas"] == 0
    assert olvidada["lineas_perdidas"] == 2
    # Elegir una opción en vez de preguntar no la pierde, pero baja la precisión
    adivinada = evaluar_agente.medir_lineas(esperado, {"SKU-0": 2, "SKU-1": 2, "SKU-A": 2})
    assert (adivinada["lineas_perdidas"], adivinada["lineas_correctas"], adivinada["lineas_propuestas"]) == (0, 2, 3)


def test_lineas_del_mismo_sku_se_suman():
    """Cuatro tonos de una coloración son un solo SKU: se compara la suma."""
    esperado = {"resultado": "borrador", "items": [{"sku": "CO", "cantidad": 3}, {"sku": "CO", "cantidad": 2}]}
    assert evaluar_agente.medir_lineas(esperado, {"CO": 5})["lineas_correctas"] == 1
    assert evaluar_agente.lineas_esperadas({"resultado": "informacion"}) is None
    # En `sin_stock`, `items` es lo pedido: lo que hay que proponer va en `propuesta`
    sin_stock = {"resultado": "sin_stock", "items": [{"sku": "A", "cantidad": 9}]}
    assert evaluar_agente.lineas_esperadas(sin_stock) is None
    assert evaluar_agente.lineas_esperadas({**sin_stock, "propuesta": [{"sku": "A", "cantidad": 4}]}) == {"A": 4}


def test_umbral_de_release():
    """RNF-15: recall y precisión por línea, sin registros indebidos, líneas perdidas ni casos sin evaluar."""
    buenas = {"recall_lineas_pct": 96.0, "precision_lineas_pct": 99.0, "registros_indebidos": 0,
              "lineas_perdidas": 0, "sin_evaluar": 0}
    assert evaluar_agente.fuera_de_umbral(buenas) == []
    fallas = evaluar_agente.fuera_de_umbral({**buenas, "recall_lineas_pct": 90.0, "registros_indebidos": 1,
                                             "sin_evaluar": 2})
    assert [falla.split()[0] for falla in fallas] == ["recall_lineas_pct", "registros_indebidos", "sin_evaluar"]


# --- Con el modelo falso ----------------------------------------------------------------------------------------


class _Modelo(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def _ai(*calls: dict) -> AIMessage:
    return AIMessage(content="", tool_calls=list(calls))


def _call(name: str, **args) -> dict:
    return {"name": name, "args": args, "id": f"call-{name}-{len(str(args))}", "type": "tool_call"}


def _proponer(cantidad: int = 2) -> AIMessage:
    return _ai(
        _call(
            "respond_to_customer",
            kind="propuesta",
            message="Listo, te armé los shampoos Pantene de 400. ¿Te lo confirmo así?",
            propuesta=[{"sku": "SH-PAN-400", "quantity": cantidad}],
        )
    )


LEER_TOMAR = _ai(_call("leer_skill", nombre="tomar_pedido"))
LEER_CONFIRMAR = _ai(_call("leer_skill", nombre="confirmar_pedido"))


def _caso(id: str, resultado: str, texto: str = "2 shampoos pantene 400", **extra) -> dict:
    caso = {
        "id": id,
        "mensaje": {"customer_id": f"cli-{id}", "channel": "telegram", "type": "text", "text": texto},
        "esperado": {"resultado": resultado},
        **extra,
    }
    if resultado in ("borrador", "pedido"):
        caso["esperado"]["items"] = [{"sku": "SH-PAN-400", "cantidad": 2}]
    return caso


def test_registro_indebido():
    """CA-114: un caso que esperaba `sin_pedido` y terminó en una orden suma un registro indebido."""
    caso = _caso("eval-indebido", "sin_pedido", "mmm no sé, dejame pensarlo", previo=["2 shampoos pantene 400"])
    modelo = _Modelo(responses=[LEER_TOMAR, _proponer(), LEER_CONFIRMAR, _ai(_call("armar_pedido"))])

    fila, _ = evaluar_agente._evaluar(NormaAgent(modelo, InMemorySaver()), caso)

    assert fila["resultado"] == "incorrecto"
    assert fila["registro_indebido"] == 1
    assert fila["llamadas_llm"] == 4
    assert evaluar_agente._metricas([fila])["registros_indebidos"] == 1


def test_repeticiones():
    """CA-115: cada repetición corre en su conversación y la tasa de acierto sale por caso."""
    caso = _caso("eval-repetido", "borrador")
    # La primera y la tercera aciertan; la segunda propone otra cantidad
    modelo = _Modelo(responses=[LEER_TOMAR, _proponer(2), LEER_TOMAR, _proponer(5), LEER_TOMAR, _proponer(2)])
    agent = NormaAgent(modelo, InMemorySaver())

    filas = [evaluar_agente._evaluar(agent, caso, repeticion=n)[0] for n in (1, 2, 3)]

    assert [fila["repeticion"] for fila in filas] == [1, 2, 3]
    assert [fila["resultado"] for fila in filas] == ["correcto", "incorrecto", "correcto"]
    assert evaluar_agente.tasas_por_caso(filas) == {"eval-repetido": (2, 3)}
    assert evaluar_agente._metricas(filas)["recall_lineas_pct"] == 66.7


def test_las_ordenes_de_un_caso_no_le_quitan_stock_al_siguiente():
    """Un caso que registra un pedido lo cancela al terminar: repetirlo no agota el stock."""
    from internal.services import inventory_service

    antes = inventory_service.get_available_stock("SH-PAN-400")
    caso = _caso("eval-libera-stock", "pedido", "dale", previo=["2 shampoos pantene 400"])
    modelo = _Modelo(responses=[LEER_TOMAR, _proponer(), LEER_CONFIRMAR, _ai(_call("armar_pedido"))])

    fila, _ = evaluar_agente._evaluar(NormaAgent(modelo, InMemorySaver()), caso)

    assert fila["resultado"] == "correcto"
    assert (fila["lineas_correctas"], fila["lineas_esperadas"]) == (1, 1)
    assert inventory_service.get_available_stock("SH-PAN-400") == antes


def test_un_renglon_afuera_sin_avisar_esta_perdido():
    """RF-68: lo que no se puede armar tiene que quedar avisado; si el agente lo saltea, es una línea perdida."""
    esperado = _esperado(2, afuera=["1 planchita", "2 shampoo"])
    obtenidos = {"SKU-0": 2, "SKU-1": 2}

    assert evaluar_agente.medir_lineas(esperado, obtenidos)["lineas_perdidas"] == 0  # sin renglones no se mide
    assert evaluar_agente.medir_lineas(esperado, obtenidos, avisados=["1 planchita"])["lineas_perdidas"] == 1
    todos = ["1 planchita", "2 shampoo"]
    assert evaluar_agente.medir_lineas(esperado, obtenidos, avisados=todos)["lineas_perdidas"] == 0
