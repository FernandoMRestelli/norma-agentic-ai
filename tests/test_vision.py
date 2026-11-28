"""Lectura de imágenes a JSON: proveedores, parseo de la salida y puntaje de la comparación, sin llamar a ningún
modelo (ni API ni local)."""

import io
import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from PIL import Image

from internal import comparar_vision
from internal.config import get_settings
from internal.schemas.vision_schema import ImagenLeida, RenglonLeido
from internal.services import vision_local, vision_service

LEIDA = {
    "legible": True,
    "tipo": "lista_pedido",
    "texto": "5 x Shampoo Pantene 400 ml\n3 x Tintura Koleston 6.1",
    "renglones": [
        {"texto": "5 x Shampoo Pantene 400 ml", "cantidad": 5, "producto": "Shampoo", "marca": "Pantene",
         "presentacion": "400 ml"},
        {"texto": "3 x Tintura Koleston 6.1", "cantidad": 3, "producto": "Tintura", "marca": "Koleston",
         "presentacion": "6.1"},
    ],
    "dudas": [],
}


def png(ancho: int = 40, alto: int = 30, modo: str = "RGB") -> bytes:
    salida = io.BytesIO()
    Image.new(modo, (ancho, alto), "white").save(salida, format="PNG")
    return salida.getvalue()


@pytest.fixture
def settings(monkeypatch):
    def configurar(**values):
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()

    yield configurar
    get_settings.cache_clear()


# --- Proveedores ---


@pytest.mark.parametrize(("proveedor", "clase"), [("openai", ChatOpenAI), ("openrouter", ChatOpenAI),
                                                  ("gemini", ChatGoogleGenerativeAI)])
def test_crea_el_modelo_de_vision_por_api(proveedor, clase):
    assert isinstance(vision_service.get_vision_model(proveedor, "modelo-de-prueba", "clave-de-prueba"), clase)


def test_api_sin_clave_dice_que_falta():
    with pytest.raises(ValueError, match="LLM_VISION_API_KEY"):
        vision_service.get_vision_model("openai", "gpt-de-prueba", None)


def test_sin_proveedor_dice_que_falta(settings):
    settings()
    with pytest.raises(ValueError, match="LLM_VISION_PROVIDER"):
        vision_service.leer_imagen(png())


def test_proveedor_desconocido(settings):
    settings(LLM_VISION_PROVIDER="otro")
    with pytest.raises(ValueError, match="no soportado"):
        vision_service.leer_imagen(png())


class _ChatFalso:
    """Imita `with_structured_output(..., include_raw=True)` de LangChain y guarda lo que recibe."""

    def __init__(self, respuesta: dict):
        self.respuesta = respuesta
        self.mensajes = None
        self.esquema = None

    def with_structured_output(self, esquema, **_):
        self.esquema = esquema
        return self

    def invoke(self, mensajes):
        self.mensajes = mensajes
        return self.respuesta


def test_api_manda_la_imagen_y_devuelve_el_json(settings, monkeypatch):
    settings(LLM_VISION_PROVIDER="openai", LLM_VISION_MODEL="gpt-de-prueba", LLM_VISION_API_KEY="clave")
    crudo = AIMessage(content=json.dumps(LEIDA), usage_metadata={"input_tokens": 900, "output_tokens": 120,
                                                                 "total_tokens": 1020})
    chat = _ChatFalso({"raw": crudo, "parsed": ImagenLeida.model_validate(LEIDA), "parsing_error": None})
    monkeypatch.setattr(vision_service, "get_vision_model", lambda *_: chat)

    lectura = vision_service.leer_imagen(png())

    assert chat.esquema is ImagenLeida
    imagen = chat.mensajes[-1].content[1]
    assert imagen["type"] == "image_url" and imagen["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert lectura.resultado.renglones[0].cantidad == 5
    assert (lectura.proveedor, lectura.modelo, lectura.tokens_entrada, lectura.tokens_salida) == (
        "openai", "gpt-de-prueba", 900, 120)
    assert lectura.error is None


def test_api_con_json_invalido_no_rompe(settings, monkeypatch):
    settings(LLM_VISION_PROVIDER="openai", LLM_VISION_MODEL="gpt-de-prueba", LLM_VISION_API_KEY="clave")
    chat = _ChatFalso({"raw": AIMessage(content="no es json"), "parsed": None, "parsing_error": "boom"})
    monkeypatch.setattr(vision_service, "get_vision_model", lambda *_: chat)

    lectura = vision_service.leer_imagen(png())

    assert lectura.resultado is None and "JSON inválido" in lectura.error and lectura.crudo == "no es json"


def test_api_que_falla_devuelve_el_error(settings, monkeypatch):
    settings(LLM_VISION_PROVIDER="openai", LLM_VISION_MODEL="gpt-de-prueba", LLM_VISION_API_KEY="clave")

    class _Caido(_ChatFalso):
        def invoke(self, mensajes):
            raise TimeoutError("sin respuesta")

    monkeypatch.setattr(vision_service, "get_vision_model", lambda *_: _Caido({}))
    lectura = vision_service.leer_imagen(png())
    assert lectura.resultado is None and "TimeoutError" in lectura.error


def test_modelo_local_arma_el_json_de_lo_que_genera(settings, monkeypatch):
    settings(LLM_VISION_PROVIDER="huggingface")
    prompts = []

    def generar(imagen, modelo, prompt):
        prompts.append((modelo, prompt))
        return f"```json\n{json.dumps(LEIDA)}\n```", 700, 150

    monkeypatch.setattr(vision_local, "generar", generar)
    lectura = vision_service.leer_imagen(png())

    assert prompts[0][0] == vision_local.MODELO_POR_DEFECTO
    assert '"renglones"' in prompts[0][1]  # el esquema va en el prompt
    assert lectura.resultado.renglones[1].marca == "Koleston"
    assert (lectura.tokens_entrada, lectura.tokens_salida) == (700, 150)


# --- Imagen y parseo ---


def test_achica_las_imagenes_grandes():
    datos, mime = vision_service.preparar_imagen(png(4000, 3000))
    with Image.open(io.BytesIO(datos)) as img:
        assert max(img.size) == vision_service.MAX_LADO_PX
    assert mime == "image/jpeg"


def test_con_transparencia_queda_en_png():
    _, mime = vision_service.preparar_imagen(png(modo="RGBA"))
    assert mime == "image/png"


def test_parsea_json_con_texto_alrededor():
    leida = vision_service.parsear_json(f"Acá va:\n```json\n{json.dumps(LEIDA)}\n```\nListo.")
    assert leida.tipo == "lista_pedido"


@pytest.mark.parametrize("texto", ["sin json", '{"legible": true}', "{roto"])
def test_parsear_json_invalido_lanza_value_error(texto):
    with pytest.raises(ValueError):
        vision_service.parsear_json(texto)


def test_sin_texto_no_es_legible():
    assert not ImagenLeida(legible=True, tipo="otro", texto="  ").legible


def test_texto_para_el_agente_marca_el_origen():
    texto = ImagenLeida.model_validate(LEIDA).como_texto_para_el_agente()
    assert texto.startswith("[imagen leída: lista de pedido]\nRenglones:\n- 5 x Shampoo")


def test_texto_para_el_agente_separa_lo_que_no_es_pedido():
    """El encabezado y lo tachado van aparte, marcados como «no es pedido», antes de los renglones."""
    leida = ImagenLeida(
        legible=True,
        tipo="lista_pedido",
        texto="Peluquería Sol 25/11\n~~5 guantes~~\n3 shampoo\n2 máscara\n1 capa x7",
        encabezado={"cliente": "Peluquería Sol", "fecha": "25/11", "otros": ["hoja 1"]},
        tachados=["5 guantes nitrilo M"],
        renglones=[
            RenglonLeido(texto="3 shampoo", cantidad=3),
            RenglonLeido(texto="2 máscara", cantidad=2),
            RenglonLeido(texto="Capa x7", cantidad=7, duda="el 7 puede ser un 1"),
        ],
    )
    assert leida.como_texto_para_el_agente().splitlines() == [
        "[imagen leída: lista de pedido]",
        "No es pedido (encabezado): Peluquería Sol · 25/11 · hoja 1",
        "No es pedido (tachado en la hoja): 5 guantes nitrilo M",
        "Renglones:",
        "- 3 shampoo",
        "- 2 máscara",
        "- Capa x7 (duda de lectura: el 7 puede ser un 1)",
    ]


def test_sin_renglones_queda_el_texto_plano():
    """Sin renglones, el agente recibe lo de antes."""
    leida = ImagenLeida(legible=True, tipo="captura_chat", texto="hola\n¿abren el sábado?", tachados=["algo"])
    assert leida.como_texto_para_el_agente() == "[imagen leída]\nhola\n¿abren el sábado?"


def test_lectura_vieja_sigue_validando():
    """Un JSON sin los campos nuevos valida, con los campos nuevos vacíos."""
    leida = ImagenLeida.model_validate(LEIDA)
    assert leida.encabezado is None and leida.tachados == []
    assert all(r.variantes == [] and r.duda is None and r.sigue_a is None for r in leida.renglones)


def test_instrucciones_hablan_de_la_disposicion_de_la_hoja(settings):
    settings(VISION_MARCAS="false")
    texto = vision_service.instrucciones()
    for parte in ("`encabezado`", "`tachados`", "`variantes`", "`sigue_a`", "`cantidad_escrita`", "`duda`"):
        assert parte in texto
    assert vision_service.ENCABEZADO_MARCAS not in texto


def test_instrucciones_con_marcas_del_catalogo(settings, monkeypatch):
    """Con VISION_MARCAS, las marcas del catálogo y nada más: ni productos ni precios."""
    from internal.schemas.catalog_schema import Product
    from internal.services import inventory_service

    productos = [
        Product(sku="A1", name="SHAMPOO KERATINA X 1L", brand="LA PUISSANCE", price=100, stock=5),
        Product(sku="B1", name="OXIDANTE 20 VOL", brand="ALFAPARF", price=200, stock=5),
        Product(sku="B2", name="OXIDANTE 30 VOL", brand="ALFAPARF", price=210, stock=5),
        Product(sku="C1", name="CAPA DESCARTABLE", brand="DOMPEL", price=300, stock=5),
    ]
    monkeypatch.setattr(inventory_service, "get_catalog", lambda: productos)
    settings(VISION_MARCAS="true")
    texto = vision_service.instrucciones()
    assert texto.endswith(f"{vision_service.ENCABEZADO_MARCAS} ALFAPARF, DOMPEL, LA PUISSANCE.")
    assert "KERATINA" not in texto and "100" not in texto and "A1" not in texto


def test_instrucciones_sin_catalogo_leen_igual(settings, monkeypatch):
    from internal.services import inventory_service

    def sin_catalogo():
        raise OSError("no está")

    monkeypatch.setattr(inventory_service, "get_catalog", sin_catalogo)
    settings(VISION_MARCAS="true")
    assert vision_service.instrucciones() == vision_service.INSTRUCCIONES


# --- Comparación ---

CASO = {"id": "x", "legible": True, "renglones": [{"cantidad": 5, "palabras": ["pantene", "400"]},
                                                  {"cantidad": 3, "palabras": ["koleston", "6.1"]}]}


def test_puntaje_perfecto():
    puntaje = comparar_vision.puntuar(CASO, ImagenLeida.model_validate(LEIDA))
    assert puntaje.ok and puntaje.acertados == 2


def test_puntaje_cantidad_equivocada_y_renglon_de_mas():
    leida = ImagenLeida.model_validate(LEIDA)
    leida.renglones[1].cantidad = 8
    leida.renglones.append(RenglonLeido(texto="Gracias!"))
    puntaje = comparar_vision.puntuar(CASO, leida)
    assert not puntaje.ok
    assert (puntaje.acertados, puntaje.sobrantes, puntaje.faltantes) == (1, 2, ["3 koleston 6.1"])


def test_puntaje_ignora_tildes_y_mayusculas():
    caso = {"legible": True, "renglones": [{"cantidad": 2, "palabras": ["algodon"]}]}
    leida = ImagenLeida(legible=True, tipo="captura_chat", texto="2 ALGODÓN",
                        renglones=[RenglonLeido(texto="2 ALGODÓN Estrella", cantidad=2)])
    assert comparar_vision.puntuar(caso, leida).ok


def test_puntaje_sin_json():
    puntaje = comparar_vision.puntuar(CASO, None)
    assert not puntaje.json_valido and puntaje.faltantes == ["5 pantene 400", "3 koleston 6.1"]


CASO_TACHADOS = {
    "legible": True,
    "renglones": [{"cantidad": 3, "palabras": ["shampoo"]}, {"cantidad": None, "palabras": ["igora"]}],
    "tachados": [["guantes"], ["capas", "descartables"]],
}


def test_puntaje_cuenta_tachados():
    """Lo tachado tiene que leerse como tachado y nunca terminar en un renglón."""
    bien = ImagenLeida(
        legible=True, tipo="lista_pedido", texto="x",
        tachados=["5 guantes nitrilo M", "2 capas descartables"],
        renglones=[RenglonLeido(texto="Shampoo x3", cantidad=3), RenglonLeido(texto="Igora Royal con tonos")],
    )  # fmt: skip
    puntaje = comparar_vision.puntuar(CASO_TACHADOS, bien)
    assert puntaje.ok and (puntaje.tachados_vistos, puntaje.tachados_pedidos) == (2, 0)

    mal = bien.model_copy(
        update={"tachados": [], "renglones": [*bien.renglones, RenglonLeido(texto="5 guantes nitrilo M", cantidad=5)]}
    )
    puntaje = comparar_vision.puntuar(CASO_TACHADOS, mal)
    assert not puntaje.ok
    assert (puntaje.tachados_vistos, puntaje.tachados_pedidos, puntaje.sobrantes) == (0, 1, 1)


def test_puntaje_renglon_sin_cantidad_se_lee_sin_cantidad():
    caso = {"legible": True, "renglones": [{"cantidad": None, "palabras": ["igora"]}]}
    con_cantidad = ImagenLeida(legible=True, tipo="lista_pedido", texto="x",
                               renglones=[RenglonLeido(texto="Igora Royal", cantidad=1)])
    assert not comparar_vision.puntuar(caso, con_cantidad).ok


def test_puntaje_cuenta_las_variantes():
    """Un renglón leído como variantes vale por la suma de sus cantidades."""
    from internal.schemas.vision_schema import Variante

    caso = {"legible": True, "renglones": [{"cantidad": 2, "palabras": ["koleston"]}]}
    leida = ImagenLeida(legible=True, tipo="lista_pedido", texto="x", renglones=[
        RenglonLeido(texto="Koleston 7.1 x2", producto="Koleston",
                     variantes=[Variante(detalle="", tono="7.1", cantidad=2)])
    ])  # fmt: skip
    assert comparar_vision.puntuar(caso, leida).ok


def test_casos_de_ejemplo_tienen_su_imagen():
    casos = json.loads(comparar_vision.CASOS.read_text())
    for caso in casos:
        assert (Path(comparar_vision.CASOS).parent / caso["archivo"]).is_file(), caso["id"]
        assert caso["legible"] or not caso["renglones"]


class _Entradas(dict):
    """Lo que devuelve `apply_chat_template`: un dict con `input_ids` que se puede mover de dispositivo."""

    def to(self, _dispositivo):
        return self


class _Ids:
    def __init__(self, largo: int):
        self.shape = (1, largo)


class _RedFalsa:
    device = "cpu"

    def __init__(self):
        self.llamadas: list[dict] = []

    def generate(self, **kwargs):
        self.llamadas.append(kwargs)
        return _SalidaFalsa()


class _SalidaFalsa:
    def __getitem__(self, _indice):
        return _Ids(2)


class _ProcesadorFalso:
    def __init__(self, largo: int):
        self.largo = largo

    def apply_chat_template(self, *_, **__):
        return _Entradas(input_ids=_Ids(self.largo))

    def batch_decode(self, *_, **__):
        return ['{"legible": true, "tipo": "lista_pedido", "texto": "2 sh keratina", "renglones": []}']


@pytest.mark.parametrize("largo", [3900, 100_001])
def test_topes_de_tokens_del_modelo_local(monkeypatch, largo):
    """CA-249: el modelo local genera hasta 20.000 tokens y no lee una entrada de más de 100.000."""
    pytest.importorskip("torch")
    red = _RedFalsa()
    monkeypatch.setattr(vision_local, "cargar", lambda _modelo: (_ProcesadorFalso(largo), red))
    imagen = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(imagen, format="PNG")

    lectura = vision_local.leer(imagen.getvalue())
    if largo <= vision_service.MAX_TOKENS_ENTRADA:
        assert red.llamadas[0]["max_new_tokens"] == 20_000
        assert lectura.resultado is not None and lectura.error is None
    else:
        assert red.llamadas == []
        assert lectura.resultado is None and "100000" in lectura.error
