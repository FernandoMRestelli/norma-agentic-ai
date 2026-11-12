"""Búsqueda tolerante a errores (spec `docs/spec/busqueda-tolerante.md`).

El catálogo está escrito como el real: mayúsculas, la medida dentro del nombre, abreviaturas y la presentación
vacía. Los productos y los precios son inventados.
"""

import gc
import random
import time
from decimal import Decimal

import pytest

from internal.agent import tool_registry
from internal.schemas.catalog_schema import Product
from internal.schemas.order_schema import Channel
from internal.services import busqueda_service, sheets_service
from internal.tools import catalog_tools

CATALOGO = [
    # sku, nombre, marca, stock
    ("FK900", "FIDELITE KERATIN REPAIR SHAMPOO X 900 ML", "FIDELITE", 10),
    ("FK230", "FIDELITE KERATIN REPAIR SHAMPOO X 230 ML", "FIDELITE", 10),
    ("FKA230", "FIDELITE KERATIN REPAIR ACOND. X 230 ML", "FIDELITE", 10),
    ("FCS900", "FIDELITE CAVIAR SH X 900", "FIDELITE", 10),
    ("HBK5", "HAN BIDON SH KERATINA X 5L", "HAN", 10),
    ("HKN500", "HAN KERATINA NUTRITIVO SH X 500 ML", "HAN", 10),
    ("HOE500", "HAN OLIVA ENJUAGUE X 500 ML", "HAN", 10),
    ("HSH500", "HAN SHAMPOO HIDRATANTE X 500 ML", "HAN", 10),
    ("FAM1K", "FIDELITE ARGAN MASCARA X 1 KG", "FIDELITE", 10),
    ("FAM250", "FIDELITE ARGAN MASCARA X 250 G", "FIDELITE", 10),
    ("FRM1K", "FRILAYPS COCO + ARGAN MASCARA X 1K", "FRILAYP", 10),
    ("FRM240", "FRILAYPS COCO + ARGAN MASCARA X 240G", "FRILAYP", 10),
    ("IMA1000", "INOAR MASCARA ARGAN OIL X 1000ML", "INOAR", 10),
    ("IMA250", "INOAR MASCARA ARGAN OIL X 250 ML", "INOAR", 10),
    ("KP71", "KOLESTON PERFECT COLORACION 7.1 X 60G", "KOLESTON", 10),
    ("KP70", "KOLESTON PERFECT COLORACION 7.0 X 60G", "KOLESTON", 10),
    ("KP61", "KOLESTON PERFECT COLORACION 6.1 X 60G", "KOLESTON", 10),
    ("KP821", "KOLESTON PERFECT COLORACION 8.21 X 60G", "KOLESTON", 10),
    ("IR71", "IGORA ROYAL COLORACION 7.1 X 60G", "IGORA", 10),
    ("KO20", "KOLESTON OXIDANTE 20 VOL X 1L", "KOLESTON", 10),
    ("SO30", "SILKEY OXIGENADA 30 VOL X 1 L", "SILKEY SALON", 10),
    ("SO20", "SILKEY OXIGENADA 20 VOL X 900ML", "SILKEY SALON", 10),
    ("PS400", "PANTENE RESTAURACION SHAMPOO X 400 ML", "PANTENE", 10),
    ("PS200", "PANTENE RESTAURACION SHAMPOO X 200 ML", "PANTENE", 10),
    ("SC340", "SEDAL CERAMIDAS SH X 340CC", "SEDAL", 10),
    ("SCE340", "SEDAL CERAMIDAS CREMA DE ENJUAGUE X 340 CC", "SEDAL", 10),
    ("DJ90", "DOVE JABON ORIGINAL X 90 G", "DOVE", 10),
    ("VBV250", "VALMY BALSAMO VITAMINICO X 250 GR", "VALMY", 10),
    ("AAK12", "ALFAPARF AMPOLLA KERATINA X12", "ALFAPARF", 0),
    ("LBC200", "LUMINA CACAO BAÑO X 200 GR", "LUMINA", 10),
    ("NBC500", "NOVA BAÑO DE CREMA PALTA X 500 GR", "NOVA", 10),
    ("LCM130", "LUMINA CREMA DE MANOS X 130 ML", "LUMINA", 10),
]


def _productos(catalogo=CATALOGO) -> list[Product]:
    return [
        Product(sku=sku, name=nombre, brand=marca, price=Decimal(1000 + i * 37), stock=stock)
        for i, (sku, nombre, marca, stock) in enumerate(catalogo)
    ]


PRODUCTOS = _productos()


def _buscar(query: str, limit: int = 5):
    return busqueda_service.buscar(query, PRODUCTOS, limit)


def _skus(productos) -> list[str]:
    return [p.sku for p in productos]


# --- Las cuatro búsquedas del plan (§1.1) -------------------------------------------------------------------------


def test_abreviatura_y_medida_pegada_encuentran_el_producto():
    """CA-59: «sh» es shampoo, «x900» es 900 y «keratina» es «KERATIN»; los de otra medida no aparecen."""
    resultado = _buscar("sh keratina x900")
    assert resultado.matches[0].sku == "FK900"
    assert not {"FK230", "HBK5", "HKN500"} & set(_skus(resultado.matches))
    assert resultado.corregido["sh"] == "shampoo"


def test_medida_sin_unidad_en_el_catalogo_coincide_con_la_del_cliente():
    """CA-59: «X 900» sin unidad coincide con «900 ml»."""
    assert _skus(_buscar("shampoo caviar fidelite 900 ml").matches) == ["FCS900"]


@pytest.mark.parametrize("query", ["champu fidelite", "champú fidelité", "Champu FIDELITE"])
def test_variante_de_shampoo_encuentra_la_marca(query):
    """CA-60: solo shampoos Fidelite, y `corregido` dice cómo se leyó «champu»."""
    resultado = _buscar(query)
    assert resultado.matches
    assert set(_skus(resultado.matches)) <= {"FK900", "FK230", "FCS900"}
    assert resultado.corregido == {"champu": "shampoo"}


@pytest.mark.parametrize(
    "query", ["mascara argan 1k", "mascara argan 1 kg", "mascara argan 1000 g", "máscaras argan x1kg"]
)
def test_unidades_equivalentes_comparan_medida_con_medida(query):
    """CA-61: 1k = 1 kg = 1000 g; primero los de 1 kg, sin los de 250 g."""
    resultado = _buscar(query)
    assert set(_skus(resultado.matches[:2])) == {"FAM1K", "FRM1K"}
    assert not {"FAM250", "FRM240", "IMA250"} & set(_skus(resultado.matches))
    assert all(not m.aproximado for m in resultado.matches[:2])


def test_cantidad_al_principio_no_cuenta_como_medida():
    """CA-61: «2 mascara argan x 1k» da lo mismo que sin el 2, sin bajar el puntaje ni marcar aproximado."""
    con_cantidad = _buscar("2 mascara argan x 1k")
    assert _skus(con_cantidad.matches) == _skus(_buscar("mascara argan x 1k").matches)
    assert all(m.score == 1 and not m.aproximado for m in con_cantidad.matches[:2])
    assert _skus(_buscar("3 sh pantene 400").matches) == ["PS400"]
    assert _skus(_buscar("12x oxidante koleston 1 l").matches) == ["KO20"]


def test_cantidad_al_principio_sin_perder_precision():
    """CA-61 y CA-45: un número que está en el catálogo sigue filtrando; uno que no está no trae otra medida."""
    assert _skus(_buscar("6 koleston").matches) == ["KP61"]  # el tono 6, no los demás
    assert _skus(_buscar("400 pantene").matches) == ["PS400"]
    assert "KP61" in _skus(_buscar("6").matches)
    assert _skus(_buscar("2 koleston 7.1").matches) == ["KP71"]  # cantidad y tono
    sin_ese_tono = _buscar("9 tintura igora").matches  # en una coloración, el único número puede ser el tono
    assert sin_ese_tono and all(m.aproximado and m.score < 1 for m in sin_ese_tono)
    for query in ("pantene 340", "2 pantene 340"):
        resultado = _buscar(query)
        assert resultado.matches == []
        assert set(_skus(resultado.sugerencias)) == {"PS400", "PS200"}


def test_litro_es_mil_mililitros_y_mil_cc():
    """CA-61: 1 l = 1000 ml = 1000 cc; 900 ml no es 1 l."""
    for query in ("oxidante koleston 1 l", "oxidante koleston 1000 ml", "oxidante koleston 1000cc"):
        assert _skus(_buscar(query).matches) == ["KO20"]
    assert _buscar("sedal ceramidas sh 340 ml").matches[0].sku == "SC340"


@pytest.mark.parametrize(
    ("query", "tono"),
    [("tintura koleston 7.1", None), ("tinte koleston 71", "71"), ("koleston 821", "821"), ("koleston 8,21", None)],
)
def test_tintura_y_tono_sin_punto_encuentran_el_tono(query, tono):
    """CA-62: «tintura» y «tinte» son coloración; «71» es el tono 7.1 y «821» el 8.21 (V-01)."""
    resultado = _buscar(query)
    esperado = "KP821" if "8" in query else "KP71"
    assert _skus(resultado.matches) == [esperado]
    if tono:
        assert resultado.corregido[tono] == ("8.21" if tono == "821" else "7.1")
    if "tintura" in query:
        assert resultado.corregido["tintura"] == "coloracion"


# --- Tipeo y sonido -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "sku", "palabra", "interpretada"),
    [
        ("shampoo pantnee 400", "PS400", "pantnee", "pantene"),  # letras transpuestas
        ("shampoo pentene 400", "PS400", "pentene", "pantene"),  # letra cambiada
        ("shampoo fidlite keratin 900", "FK900", "fidlite", "fidelite"),  # letra faltante
        ("koleston perfect coleston 7.1", "KP71", None, None),
        ("tintura coleston 7.1", "KP71", "coleston", "koleston"),  # k/c
        ("zedal ceramidas sh", "SC340", "zedal", "sedal"),  # s/z
        ("valsamo bitaminico", "VBV250", "valsamo", "balsamo"),  # b/v
        ("ampoya queratina alfaparf", "AAK12", "ampoya", "ampolla"),  # ll/y y qu/k
        ("han shampoo idratante", "HSH500", "idratante", "hidratante"),  # h muda
        ("acondisionador fidelite keratin", "FKA230", "acondisionador", "acondicionador"),  # s/c
    ],
)
def test_errores_de_tipeo_y_de_sonido(query, sku, palabra, interpretada):
    """CA-63: el producto correcto sale primero y `corregido` dice qué se interpretó."""
    resultado = _buscar(query)
    assert resultado.matches[0].sku == sku
    if palabra:
        assert resultado.corregido[palabra] == interpretada
        assert resultado.matches[0].aproximado


def test_abreviatura_del_catalogo_coincide_con_la_palabra_entera():
    """CA-63: el catálogo escribe «ACOND.» y el cliente «acondicionador»."""
    resultado = _buscar("acondicionador fidelite keratin")
    assert _skus(resultado.matches) == ["FKA230"]
    assert not resultado.matches[0].aproximado


def test_sinonimo_sin_mezclar_lo_que_coincide_exacto():
    """CA-63: el sinónimo encuentra el producto cuando no hay otro, pero no se mezcla con lo que coincide exacto."""
    assert _skus(_buscar("acondicionador").matches) == ["FKA230"]  # sin las cremas de enjuague
    assert _skus(_buscar("acondicionador sedal").matches) == ["SCE340"]  # la crema de enjuague es lo que hay
    assert _skus(_buscar("acondicionador oliva").matches) == ["HOE500"]
    assert _skus(_buscar("oxidante silkey 20 vol").matches) == ["SO20"]


# --- Precisión (CA-64) ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "prohibidos"),
    [
        ("shampoo pantene 400", {"PS200"}),
        ("koleston 6.1", {"KP71", "KP70", "IR71"}),
        ("koleston perfect 7.1", {"KP70", "KP61", "IR71", "KP821"}),
        ("oxidante 20 vol", {"SO30"}),
        ("oxigenada 30 vol", {"SO20", "KO20"}),
        ("mascara argan 250", {"FAM1K", "FRM1K", "IMA1000"}),
        ("shampoo keratina 900", {"HBK5", "FK230", "HKN500"}),
    ],
)
def test_precision_los_numeros_no_se_aproximan(query, prohibidos):
    """CA-64: 400 no es 200, 6.1 no es 7.1, 20 vol no es 30 vol, 250 g no es 1 kg."""
    resultado = _buscar(query, limit=10)
    assert resultado.matches
    assert not prohibidos & set(_skus(resultado.matches))


@pytest.mark.parametrize("query", ["coca cola", "gillette", "shampoo dove", "head and shoulders", "999", "xyz"])
def test_precision_sin_parecido_real_no_trae_nada(query):
    """CA-64: ni matches ni sugerencias de algo que el cliente no pidió."""
    resultado = _buscar(query)
    assert resultado.matches == []
    assert resultado.sugerencias == []


@pytest.mark.parametrize("query", ["baño de crema", "baños de crema", "bano crema", "BAÑO DE CREMA"])
def test_precision_bano_de_crema_es_el_bano_del_catalogo(query):
    """CA-64: «baño de crema» es el «BAÑO» del catálogo, escrito de las dos formas; nunca la crema de manos."""
    resultado = _buscar(query)
    assert set(_skus(resultado.matches)) == {"LBC200", "NBC500"}
    assert "bano" not in resultado.corregido


def test_precision_palabra_corta_no_se_aproxima_a_otra():
    """CA-64: «baño» no es «manos»: en una palabra de 4 letras, una letra cambiada ya es otra palabra."""
    assert _skus(_buscar("baño de crema palta").matches) == ["NBC500"]
    assert _skus(_buscar("crema de manos").matches) == ["LCM130"]
    resultado = _buscar("manos nova")  # Nova solo tiene un baño: no hay que sugerirlo como si fuera «manos»
    assert resultado.matches == []
    assert resultado.sugerencias == []
    assert resultado.corregido == {}


def test_precision_el_tono_no_toma_otras_cifras():
    """CA-64: «71» es el tono 7.1, no el 7.0 ni el 8.21. Un tono que no existe no parece exacto (como en CA-45)."""
    assert _skus(_buscar("koleston perfect 71").matches) == ["KP71"]
    sin_ese_tono = _buscar("koleston perfect 72").matches
    assert all(m.aproximado and m.score < 1 for m in sin_ese_tono)


# --- Sugerencias (CA-65) -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "esperadas"),
    [
        ("pantene 340", {"PS400", "PS200"}),
        ("koleston 9.1", {"KP71", "KP70", "KP61", "KP821", "KO20"}),
        ("shampoo pntne", {"PS400", "PS200"}),
    ],
)
def test_sin_coincidencia_confiable_devuelve_sugerencias(query, esperadas):
    """CA-65: sin matches, las sugerencias traen los candidatos, todos aproximados."""
    resultado = _buscar(query)
    assert resultado.matches == []
    assert set(_skus(resultado.sugerencias)) == esperadas
    assert all(s.aproximado for s in resultado.sugerencias)
    assert all(s.score < 1 for s in resultado.sugerencias)


def test_sugerencia_dice_que_palabra_se_corrigio():
    """CA-65."""
    assert _buscar("shampoo pntne").corregido == {"pntne": "pantene"}


# --- Contrato de la tool (CA-66) -----------------------------------------------------------------------------------


@pytest.fixture
def catalogo_del_rubro(monkeypatch):
    monkeypatch.setattr(sheets_service, "get_catalog", lambda: _productos())


def _tool(query: str) -> dict:
    ctx = tool_registry.TurnContext(customer_id="c", channel=Channel.WEB, disponibles=["search_products"])
    return tool_registry.execute("search_products", {"query": query}, ctx)["data"]


def test_contrato_de_la_tool(catalogo_del_rubro):
    """CA-66: siempre `matches`, `sugerencias` y `corregido`; cada producto con score, aproximado y hay_stock."""
    con_matches = _tool("champu fidelite")
    assert con_matches.keys() == {"matches", "sugerencias", "corregido"}
    assert con_matches["sugerencias"] == []
    assert con_matches["corregido"] == {"champu": "shampoo"}

    sin_matches = _tool("pantene 340")
    assert sin_matches["matches"] == []
    assert sin_matches["sugerencias"]

    for producto in con_matches["matches"] + sin_matches["sugerencias"]:
        assert {"sku", "name", "price", "score", "aproximado", "hay_stock"} <= producto.keys()
        assert not {"stock", "min_stock", "is_low_stock"} & producto.keys()

    sin_stock = _tool("ampolla keratina alfaparf")["matches"][0]
    assert sin_stock["sku"] == "AAK12" and sin_stock["hay_stock"] is False


# --- Índice en memoria (CA-67) -------------------------------------------------------------------------------------


def test_indice_en_memoria_con_stock_fresco(monkeypatch):
    """CA-67: el stock no reconstruye el índice y sale fresco; un nombre nuevo sí lo reconstruye."""
    construidos = []
    original = busqueda_service.Indice.__init__

    def contar(self, productos):
        construidos.append(len(productos))
        original(self, productos)

    monkeypatch.setattr(busqueda_service.Indice, "__init__", contar)
    monkeypatch.setattr(busqueda_service, "_indice", None)
    catalogo = _productos()
    monkeypatch.setattr(sheets_service, "get_catalog", lambda: catalogo)

    assert catalog_tools.search_products("shampoo pantene 400")[0].stock == 10
    catalogo[catalogo.index(next(p for p in catalogo if p.sku == "PS400"))] = next(
        p for p in catalogo if p.sku == "PS400"
    ).model_copy(update={"stock": 0})
    assert catalog_tools.search_products("shampoo pantene 400")[0].stock == 0
    assert len(construidos) == 1

    catalogo.append(Product(sku="NUEVO", name="MARCA NUEVA SERUM X 50 ML", price=Decimal(1), stock=1))
    assert _skus(catalog_tools.search_products("marca nueva serum")) == ["NUEVO"]
    assert len(construidos) == 2


# --- Rendimiento (CA-68) -------------------------------------------------------------------------------------------

_MARCAS = ["FIDELITE", "HAN", "ALFAPARF", "LA PUISSANCE", "FRILAYPS", "INOAR", "SILKEY", "OPCION", "SKALA", "ISSUE"]
_LINEAS = ["KERATINA", "ARGAN", "CAVIAR", "OLIVA", "COCO", "BOTOX", "PLEX", "CURL", "SILVER", "HIALURONICO"]
_TIPOS = ["SHAMPOO", "SH", "ACOND.", "ENJUAGUE", "MASCARA", "AMPOLLA X12", "OXIDANTE 20 VOL", "SERUM", "GEL", "OLEO"]
_MEDIDAS = ["X 230 ML", "X 900", "X 1K", "X 1 KG", "X 250 G", "370CC", "X 5L", "X 1000ml", "X 60G", "X 500 ML"]


def _mil_productos() -> list[Product]:
    rng = random.Random(20261110)
    return [
        Product(
            sku=f"P{i:04d}",
            name=f"{rng.choice(_MARCAS)} {rng.choice(_LINEAS)} {rng.choice(_TIPOS)} {rng.choice(_MEDIDAS)} {i}",
            brand=None,
            price=Decimal(rng.randint(1000, 30000)),
            stock=rng.randint(0, 20),
        )
        for i in range(1000)
    ]


def test_busqueda_sobre_mil_productos_es_rapida():
    """CA-68: con el índice armado, cada búsqueda tarda menos de 50 ms, aunque sus palabras sean nuevas."""
    productos = _mil_productos()
    busqueda_service.buscar("shampoo", productos)  # arma el índice
    queries = [
        "sh keratina x900",
        "champu fidelite",
        "mascara argan 1k",
        "tintura koleston 7.1",
        "acondisionador alfaparf oliba",
        "coca cola",
    ]
    for query in queries:
        gc.collect()  # una pausa del recolector la causan los objetos de otros tests, no la búsqueda
        inicio = time.perf_counter()
        busqueda_service.buscar(query, productos)
        assert time.perf_counter() - inicio < 0.05, query
