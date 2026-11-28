"""Corrida y puntaje del benchmark de pedidos escritos a mano (docs/spec/benchmark-manuscritos.md, §3.3 y §3.4).

Ningún test llama a un modelo: la lectura es un `Lector` falso y la búsqueda corre contra catalogo_evaluacion.csv.
"""

import json
import socket
import string

import pytest

from internal import evaluar_manuscritos as em
from internal.schemas.vision_schema import ImagenLeida, RenglonLeido, Variante
from internal.services import vision_service
from internal.services.vision_service import Lectura


def _esperado(n, palabras, cantidad, sku, *, tono=None, unidad=None, dificultad=(), origen="pool", texto=None):
    return {
        "n": n,
        "id": f"P{n:03d}",
        "origen": origen,
        "texto": texto or " ".join(palabras),
        "sku": sku,
        "cantidad": cantidad,
        "tono": tono,
        "unidad": unidad,
        "palabras": list(palabras),
        "dificultad": list(dificultad),
    }


def _caso(foto, renglones, *, grupo="desarrollo", tachados=(), legible=True, luz="natural", papel="hoja lisa"):
    return {
        "id": foto,
        "archivo": f"imagenes/{foto}.jpg",
        "persona": foto.split("-")[0],
        "hoja": foto.split("-")[1],
        "lista": "L01",
        "grupo": grupo,
        "condiciones": {"luz": luz, "papel": papel},
        "sexo": "prefiero no responder",
        "legible": legible,
        "encabezado": "Pedido para Norma",
        "renglones": renglones,
        "tachados": [list(t) for t in tachados],
        "sin_copiar": [],
    }


def _imagen(*renglones: RenglonLeido, legible: bool = True) -> ImagenLeida:
    return ImagenLeida(legible=legible, tipo="lista_pedido", texto="Pedido para Norma", renglones=list(renglones))


# Casos con productos reales de catalogo_evaluacion.csv, para que la búsqueda del nivel (b) los encuentre
REALES = [
    _esperado(1, ["keratina", "nordika", "900"], 2, "NOR-SH-KER-900", origen="nucleo", dificultad=["abreviatura"]),
    _esperado(2, ["silkara", "protector"], 7, "SIL-CU-PRO-250", origen="nucleo", dificultad=["numeros_confusos"]),
    _esperado(3, ["belkor", "matizador", "900"], 4, "BEL-SH-MAT-900", dificultad=["casi_iguales"]),
]
LEIDOS_REALES = [
    RenglonLeido(texto="2 sh keratina Nordika x900", cantidad=2, producto="sh keratina", marca="Nordika",
                 presentacion="900"),
    RenglonLeido(texto="Silkara protector térmico (7)", cantidad=7, producto="protector termico", marca="Silkara"),
    RenglonLeido(texto="4 Belkor sh matizador 900", cantidad=4, producto="sh matizador", marca="Belkor",
                 presentacion="900"),
]  # fmt: skip


class Lector:
    """Un modelo de visión falso: cuenta sus llamadas y devuelve siempre la misma lectura."""

    def __init__(self, resultado: ImagenLeida | None = None, tokens: tuple[int, int] = (1000, 200)):
        self.resultado = _imagen(*LEIDOS_REALES) if resultado is None else resultado
        self.tokens = tokens
        self.fotos: list[str] = []
        self.devolver_invalido = False

    def __call__(self, imagen: bytes, proveedor: str, modelo: str) -> Lectura:
        self.fotos.append(imagen.decode())
        resultado = None if self.devolver_invalido else self.resultado
        crudo = "no es JSON" if resultado is None else resultado.model_dump_json()
        return Lectura(proveedor, modelo, resultado, crudo, 2.5, *self.tokens, None if resultado else "JSON inválido")


@pytest.fixture
def settings(monkeypatch):
    """Cambia variables de entorno de la configuración y limpia el caché de `get_settings`."""
    from internal.config import get_settings

    def configurar(**valores):
        for clave, valor in valores.items():
            monkeypatch.setenv(clave, valor)
        get_settings.cache_clear()

    yield configurar
    get_settings.cache_clear()


@pytest.fixture
def carpeta(tmp_path):
    (tmp_path / "imagenes").mkdir()
    return tmp_path


def _casos(carpeta, fotos, grupos=None, **kwargs):
    """Los casos y sus fotos: cada foto es un archivo con su propio id, así el `Lector` sabe cuál le pasaron."""
    casos = []
    for foto in fotos:
        (carpeta / "imagenes" / f"{foto}.jpg").write_bytes(foto.encode())
        casos.append(_caso(foto, REALES, grupo=(grupos or {}).get(foto, "desarrollo"), **kwargs))
    return casos


def test_correr_retoma_sin_repetir(carpeta):
    """CA-227: con la primera salida ya guardada, la corrida llama solo por las otras dos y deja las tres."""
    casos = _casos(carpeta, ["p01-A", "p02-A", "p03-A"])
    corrida = carpeta / "corridas" / "prueba"
    lector = Lector()
    em.correr(casos, carpeta, corrida, [("huggingface", "falso")], solo="p01-A", leer=lector)
    assert lector.fotos == ["p01-A"]

    informe = em.correr(casos, carpeta, corrida, [("huggingface", "falso")], leer=lector)
    assert lector.fotos == ["p01-A", "p02-A", "p03-A"]
    assert informe.por_modelo["huggingface:falso"] == {"nuevas": 2, "salteadas": 1, "fallidas": 0}
    salidas = sorted((corrida / "huggingface_falso").glob("*.json"))
    assert [s.stem for s in salidas] == ["p01-A", "p02-A", "p03-A"]
    salida = json.loads(salidas[0].read_text(encoding="utf-8"))
    campos = {"foto", "proveedor", "modelo", "huella", "fecha", "segundos", "tokens_entrada", "tokens_salida"}
    assert campos | {"error", "crudo", "resultado"} == set(salida)
    assert salida["foto"] == "p01-A" and salida["modelo"] == "falso" and salida["tokens_entrada"] == 1000
    assert ImagenLeida.model_validate(salida["resultado"]) == lector.resultado


def test_correr_reintenta_solo_si_se_pide(carpeta):
    """CA-228: una salida sin resultado no se vuelve a pedir, salvo con --reintentar, y ahí se reemplaza."""
    casos = _casos(carpeta, ["p01-A"])
    corrida = carpeta / "corridas" / "prueba"
    lector = Lector()
    lector.devolver_invalido = True
    em.correr(casos, carpeta, corrida, [("huggingface", "falso")], leer=lector)
    salida = corrida / "huggingface_falso" / "p01-A.json"
    assert json.loads(salida.read_text(encoding="utf-8"))["resultado"] is None

    lector.devolver_invalido = False
    em.correr(casos, carpeta, corrida, [("huggingface", "falso")], leer=lector)
    assert len(lector.fotos) == 1
    em.correr(casos, carpeta, corrida, [("huggingface", "falso")], reintentar=True, leer=lector)
    assert len(lector.fotos) == 2
    assert json.loads(salida.read_text(encoding="utf-8"))["resultado"] is not None


def test_correr_con_tope_de_llamadas(carpeta):
    """CA-229: un modelo de API sin --max-llamadas no se llama y se dice cuántas serían; con el tope, no lo pasa."""
    casos = _casos(carpeta, [f"p0{i}-A" for i in range(1, 6)])
    corrida = carpeta / "corridas" / "prueba"
    lector = Lector()
    informe = em.correr(casos, carpeta, corrida, [("openai", "falso")], leer=lector)
    assert lector.fotos == [] and informe.previstas == 5
    assert "5 llamadas" in informe.problema and "--max-llamadas" in informe.problema

    informe = em.correr(casos, carpeta, corrida, [("openai", "falso")], max_llamadas=2, leer=lector)
    assert len(lector.fotos) == 2 and informe.problema is None and informe.cortada


def test_correr_con_otro_prompt_se_niega(carpeta, monkeypatch):
    """CA-230: con otras INSTRUCCIONES, la corrida que ya tiene salidas no sigue y pide otra --corrida."""
    casos = _casos(carpeta, ["p01-A", "p02-A"])
    corrida = carpeta / "corridas" / "prueba"
    lector = Lector()
    em.correr(casos, carpeta, corrida, [("huggingface", "falso")], solo="p01-A", leer=lector)

    monkeypatch.setattr(vision_service, "INSTRUCCIONES", vision_service.INSTRUCCIONES + "\n- Una regla nueva.")
    informe = em.correr(casos, carpeta, corrida, [("huggingface", "falso")], leer=lector)
    assert lector.fotos == ["p01-A"]
    assert "--corrida" in informe.problema


def test_correr_con_otro_tope_de_tokens_se_niega(carpeta, monkeypatch):
    """CA-230: un tope de tokens de salida distinto también cambia la huella: una salida cortada no se compara."""
    casos = _casos(carpeta, ["p01-A", "p02-A"])
    corrida = carpeta / "corridas" / "prueba"
    lector = Lector()
    em.correr(casos, carpeta, corrida, [("huggingface", "falso")], solo="p01-A", leer=lector)
    monkeypatch.setattr(vision_service, "MAX_TOKENS_SALIDA", 1024)
    informe = em.correr(casos, carpeta, corrida, [("huggingface", "falso")], leer=lector)
    assert lector.fotos == ["p01-A"] and "--corrida" in informe.problema


def test_correr_solo_desarrollo(carpeta):
    """CA-231: sin --grupo solo se corren las fotos de desarrollo."""
    grupos = {"p01-A": "desarrollo", "p02-A": "reservado", "p03-A": "desarrollo"}
    casos = _casos(carpeta, list(grupos), grupos)
    lector = Lector()
    em.correr(casos, carpeta, carpeta / "corridas" / "prueba", [("huggingface", "falso")], leer=lector)
    assert lector.fotos == ["p01-A", "p03-A"]


def test_nivel_a_empareja_por_palabras():
    """CA-232: empareja por las palabras sin números (la marca con una letra de tolerancia) y prefiere el que también
    tiene la medida."""
    caso = _caso("p01-A", [_esperado(1, ["keratina", "nordika", "900"], 2, "NOR-SH-KER-900")])
    keratna = RenglonLeido(texto="2 keratna Nordika 900", cantidad=2)
    otra_medida = RenglonLeido(texto="2 sh keratina Nordica x300", cantidad=2)
    foto = em.puntuar_foto(caso, _imagen(keratna, otra_medida), "a", marcas=frozenset({"nordika"}))
    assert [par.leido.texto for par in foto.pares] == ["2 sh keratina Nordica x300"]
    assert not foto.pares[0].variante_ok and foto.pares[0].s == pytest.approx(0.7)
    assert [sobrante.leido.texto for sobrante in foto.sobrantes] == ["2 keratna Nordika 900"]

    completo = RenglonLeido(texto="2 keratina Nordika 900", cantidad=2)
    foto = em.puntuar_foto(caso, _imagen(keratna, otra_medida, completo), "a", marcas=frozenset({"nordika"}))
    assert [par.leido.texto for par in foto.pares] == ["2 keratina Nordika 900"] and foto.pares[0].s == 1
    assert len(foto.sobrantes) == 2


def test_nivel_b_empareja_por_sku():
    """CA-233: `unico` empareja sin duda, `varias` y `aproximado` con duda, y `no_encontrado` no empareja."""
    caso = _caso(
        "p01-A",
        [
            _esperado(1, ["keratina", "nordika"], 2, "NOR-SH-KER-900"),
            _esperado(2, ["kapris", "tintura"], 3, "KAP-CO-60", tono="7.1"),
            _esperado(3, ["silkara", "protector"], 7, "SIL-CU-PRO-250"),
            _esperado(4, ["belkor", "matizador"], 4, "BEL-SH-MAT-900"),
        ],
    )
    imagen = _imagen(
        RenglonLeido(texto="2 sh keratina Nordika 900", cantidad=2, producto="sh keratina Nordika 900"),
        RenglonLeido(texto="Kapris tintura 7.1 x3", producto="Kapris tintura",
                     variantes=[Variante(detalle="", tono="7.1", cantidad=3)]),
        RenglonLeido(texto="silkra protetor termco (7)", cantidad=7, producto="silkra protetor termco"),
        RenglonLeido(texto="4 zapatillas running", cantidad=4, producto="zapatillas running"),
    )  # fmt: skip
    foto = em.puntuar_foto(caso, imagen, "b")
    por_n = {par.esperado["n"]: par for par in foto.pares}
    assert set(por_n) == {1, 2, 3}
    assert not por_n[1].duda and por_n[1].s == 1
    assert por_n[2].duda and por_n[2].s == 0.8  # varias
    assert por_n[3].duda and por_n[3].s == 0.8  # aproximado
    assert [e["n"] for e in foto.omitidos] == [4]
    assert [sobrante.leido.texto for sobrante in foto.sobrantes] == ["zapatillas running"]
    assert foto.sobrantes[0].costo == pytest.approx(0.2)  # no_encontrado: el agente pregunta


def _hoja_del_diseno():
    """La hoja p03-A del diseño (§6.4): 15 esperados y lo que leyó el modelo."""
    letras = string.ascii_lowercase[:15]
    esperados = []
    for n, letra in enumerate(letras, start=1):
        cantidad = {5: 7, 9: 10}.get(n, 2)
        esperados.append(_esperado(n, [f"producto{letra}"], cantidad, f"SKU-{letra.upper()}"))
    leidos = []
    for n, letra in enumerate(letras, start=1):
        if n == 14:
            continue  # quedó en el pliegue: omitido
        cantidad, duda = {5: (1, None), 9: (1, "¿1 o 10?")}.get(n, (2, None))
        texto = f"{cantidad:g} producto{letra}"
        leidos.append(RenglonLeido(texto=texto, cantidad=cantidad, producto=f"producto{letra}", duda=duda))
    leidos.append(RenglonLeido(texto="2 belkor crema de peinar rulos", cantidad=2, producto="belkor rulos"))
    caso = _caso("p03-A", esperados, tachados=[["belkor", "rulos"]])
    return caso, _imagen(*leidos)


def _buscar_falso(renglones):
    """Una búsqueda que encuentra cada producto de `_hoja_del_diseno` por su nombre, sin catálogo."""
    return [
        {"n": r["n"], "estado": "unico", "candidatos": [{"sku": "SKU-" + r["texto"].removeprefix("producto").upper()}]}
        for r in renglones
    ]


@pytest.mark.parametrize("nivel", ["a", "b"])
def test_ejemplo_del_diseno(nivel):
    """CA-234: S = 13,2 / 16; precision y recall 0,80; tres errores silenciosos y graves, un tachado pedido."""
    caso, imagen = _hoja_del_diseno()
    foto = em.puntuar_foto(caso, imagen, nivel, buscar=_buscar_falso)
    assert foto.numerador == pytest.approx(13.2) and foto.denominador == 16
    assert foto.S == pytest.approx(0.825)
    assert foto.correctos == 12 and foto.leidos == 15 and len(foto.omitidos) == 1
    assert foto.errores_silenciosos == 3 and foto.errores_graves == 3 and foto.tachados_pedidos == 1
    assert not foto.perfecta and foto.json_valido
    resumen = em.resumir([foto], [{"proveedor": "huggingface", "segundos": 1.0}], [], None)
    assert resumen["elegible"] is False and "tachado" in resumen["motivo"]


def test_json_invalido_da_cero():
    """CA-235: una salida sin resultado de una foto legible da S = 0 con todo omitido."""
    caso, _ = _hoja_del_diseno()
    foto = em.puntuar_foto(caso, None, "b", buscar=_buscar_falso)
    assert foto.numerador == 0 and foto.denominador == 15 and len(foto.omitidos) == 15
    assert not foto.json_valido and not foto.perfecta


def test_detalle_normalizado():
    """CA-236: «7,1» es el tono «7.1»; cajas por unidades sueltas es cantidad lejos; `cada_uno` es como sin unidad."""
    caso = _caso(
        "p01-A",
        [
            _esperado(1, ["kapris", "tintura"], 3, "KAP-CO-60", tono="7.1"),
            _esperado(2, ["duomo", "sachet"], 2, "DUO-DE-50"),
            _esperado(3, ["trevia", "ampolla"], 24, "TRE-AM-15", unidad="cada_uno"),
        ],
    )
    imagen = _imagen(
        RenglonLeido(texto="Kapris tintura 7,1 x3", producto="Kapris tintura",
                     variantes=[Variante(detalle="", tono="7,1", cantidad=3)]),
        RenglonLeido(texto="2 cajas Duomo sachet", cantidad=2, producto="Duomo sachet", unidad="cajas"),
        RenglonLeido(texto="24 Trevia ampolla", cantidad=24, producto="Trevia ampolla"),
    )  # fmt: skip
    tono, cajas, cada_uno = em.puntuar_foto(caso, imagen, "a").pares
    assert tono.variante_ok and tono.s == 1
    assert cajas.cantidad == "lejos" and cajas.s == pytest.approx(0.4)
    assert cada_uno.cantidad == "ok" and cada_uno.s == 1


def _corrida_de_dos_modelos(carpeta):
    grupos = {"p01-A": "desarrollo", "p01-B": "desarrollo", "p02-A": "reservado", "p03-A": "reservado"}
    casos = _casos(carpeta, list(grupos), grupos, luz="artificial", papel="cuaderno rayado")
    corrida = carpeta / "corridas" / "prueba"
    modelos = [("huggingface", "Qwen/falso"), ("openai", "falso")]
    em.correr(casos, carpeta, corrida, modelos, grupo="todos", max_llamadas=20, leer=Lector())
    return casos, corrida


def test_resumen_por_modelo_y_grupo(carpeta):
    """CA-237: el mismo resumen dos veces, por modelo y grupo, con USD por modelo y los cortes."""
    casos, corrida = _corrida_de_dos_modelos(carpeta)
    precios = {"openai:falso": (1.0, 4.0)}
    resumen = em.puntuar_corrida(corrida, casos, precios)
    assert json.dumps(resumen) == json.dumps(em.puntuar_corrida(corrida, casos, precios))

    por_modelo = {m["modelo"]: m for m in resumen["modelos"]}
    assert set(por_modelo) == {"huggingface:Qwen/falso", "openai:falso"}
    local = por_modelo["huggingface:Qwen/falso"]["niveles"]["b"]
    api = por_modelo["openai:falso"]["niveles"]["b"]
    assert local["desarrollo"]["fotos"] == 2 and local["reservado"]["fotos"] == 2
    assert local["desarrollo"]["S"] == 1.0 and local["desarrollo"]["fotos_perfectas_pct"] == 100.0
    assert local["desarrollo"]["usd_por_foto"] == 0
    # 1000 tokens a USD 1 por millón + 200 a USD 4 por millón
    assert api["desarrollo"]["usd_por_foto"] == pytest.approx(0.0018)
    assert api["desarrollo"]["usd_por_1000_renglones"] == pytest.approx(0.6)
    assert set(api["desarrollo"]["por_dificultad"]) == {"abreviatura", "numeros_confusos", "casi_iguales"}
    assert set(api["desarrollo"]["por_origen"]) == {"nucleo", "pool"}
    assert api["desarrollo"]["por_luz"] == {"artificial": 1.0}
    assert api["desarrollo"]["por_papel"] == {"cuaderno rayado": 1.0}
    assert por_modelo["openai:falso"]["niveles"]["a"]["reservado"]["S"] == 1.0
    sin_precio = em.puntuar_corrida(corrida, casos, {})
    api_sin_precio = next(m for m in sin_precio["modelos"] if m["modelo"] == "openai:falso")
    assert api_sin_precio["niveles"]["b"]["desarrollo"]["usd_por_foto"] is None


def test_puntuar_sin_red(carpeta, monkeypatch):
    """CA-238: puntuar no llama a ningún modelo, ni a Sheets ni a la red."""
    casos, corrida = _corrida_de_dos_modelos(carpeta)

    def prohibido(*_, **__):
        raise AssertionError("puntuar no puede salir a la red ni llamar a un modelo")

    from internal.services import sheets_service

    monkeypatch.setattr(socket.socket, "connect", prohibido)
    monkeypatch.setattr(vision_service, "leer_imagen", prohibido)
    monkeypatch.setattr(sheets_service, "get_catalog", prohibido)
    resumen = em.puntuar_corrida(corrida, casos, {})
    assert len(resumen["modelos"]) == 2


def test_sin_respuesta_no_cuenta(carpeta):
    """CA-235: si el modelo no contestó (sin resultado ni crudo), la foto no entra al puntaje y se avisa."""
    casos = _casos(carpeta, ["p01-A", "p02-A"])
    corrida = carpeta / "corridas" / "prueba"
    em.correr(casos, carpeta, corrida, [("huggingface", "falso")], leer=Lector())
    salida = corrida / "huggingface_falso" / "p02-A.json"
    datos = json.loads(salida.read_text(encoding="utf-8"))
    salida.write_text(json.dumps({**datos, "resultado": None, "crudo": "", "error": "RateLimitError"}), "utf-8")

    resumen = em.puntuar_corrida(corrida, casos, {})
    desarrollo = resumen["modelos"][0]["niveles"]["b"]["desarrollo"]
    assert desarrollo["fotos"] == 1 and desarrollo["S"] == 1.0 and desarrollo["json_valido_pct"] == 100.0
    assert any("p02-A" in aviso and "--reintentar" in aviso for aviso in resumen["avisos"])


def _par(cantidad_esperada, cantidad_leida, *, tono_esperado=None, tono_leido=None, duda=None):
    """El s de un par sin duda (salvo que se pida) en el nivel (a), con palabras que siempre emparejan."""
    variantes = [Variante(detalle="", tono=tono_leido, cantidad=cantidad_leida)] if tono_leido else []
    leido = RenglonLeido(texto="producto prueba", cantidad=None if variantes else cantidad_leida,
                         variantes=variantes, duda=duda)  # fmt: skip
    caso = _caso("p01-A", [_esperado(1, ["producto", "prueba"], cantidad_esperada, "SKU-X", tono=tono_esperado)])
    (par,) = em.puntuar_foto(caso, _imagen(leido), "a", marcas=frozenset()).pares
    return par.s


def test_penalizaciones_de_cantidad_y_variante():
    """CA-239: cantidad cerca 0,6; lejos (el doble o más, o la mitad o menos) 0,4; con el tono mal, 0,3 y 0,1;
    sin cantidad, 0,8."""
    assert _par(2, 3) == pytest.approx(0.6)
    assert _par(6, 5) == pytest.approx(0.6)
    assert _par(2, 20) == pytest.approx(0.4)
    assert _par(10, 1) == pytest.approx(0.4)
    assert _par(2, 1) == pytest.approx(0.4)
    assert _par(2, 3, tono_esperado="7.1", tono_leido="7.2") == pytest.approx(0.3)
    assert _par(2, 20, tono_esperado="7.1", tono_leido="7.2") == pytest.approx(0.1)
    assert _par(2, None) == pytest.approx(0.8)
    assert _par(2, 20, duda="¿2 o 20?") == pytest.approx(0.8)


def test_costo_de_los_sobrantes():
    """CA-240: un sobrante silencioso cuesta 1, uno con duda o no encontrado 0,2, y un tachado 1 aunque tenga duda."""
    caso = _caso("p01-A", [_esperado(1, ["silkara", "protector"], 7, "SIL-CU-PRO-250")], tachados=[["belkor", "rulos"]])
    correcto = RenglonLeido(texto="7 Silkara protector", cantidad=7, producto="protector termico", marca="Silkara")
    foto = em.puntuar_foto(caso, _imagen(
        correcto,
        RenglonLeido(texto="4 Belkor sh matizador 900", cantidad=4, producto="sh matizador 900", marca="Belkor"),
        RenglonLeido(texto="1 Lumine cera", cantidad=1, producto="Lumine cera", duda="¿cera o crema?"),
        RenglonLeido(texto="2 Belkor crema rulos", cantidad=2, producto="Belkor crema rulos", duda="¿rulos?"),
    ), "a", marcas=frozenset())  # fmt: skip
    assert [sobrante.costo for sobrante in foto.sobrantes] == [1, pytest.approx(0.2), 1]
    assert foto.tachados_pedidos == 1 and foto.denominador == pytest.approx(3.2)

    foto = em.puntuar_foto(caso, _imagen(
        correcto, RenglonLeido(texto="3 zapatillas running", cantidad=3, producto="zapatillas running"),
    ), "b")  # fmt: skip
    assert foto.sobrantes[0].leido.estado == "no_encontrado" and foto.sobrantes[0].costo == pytest.approx(0.2)


# La tabla de casos ambiguos del diseño (§6.5) para «2 sh keratina Nordika x900»: (salida, S en a, S en b)
NORDIKA = _esperado(1, ["keratina", "nordika", "900"], 2, "NOR-SH-KER-900")
CORRECTO = RenglonLeido(texto="2 sh keratina Nordika x900", cantidad=2, producto="sh keratina", marca="Nordika",
                        presentacion="900")  # fmt: skip
AMBIGUOS = [
    (1, [RenglonLeido(texto="Nordika sh keratina 900", cantidad=2, producto="sh keratina", marca="Nordika",
                      presentacion="900")], 1, 1),
    (2, [RenglonLeido(texto="NORDIKA SH. KERATINA X900", cantidad=2, producto="SH. KERATINA", marca="NÓRDIKA",
                      presentacion="X900")], 1, 1),
    (3, [RenglonLeido(texto="2 sh keratina Nordica x900", cantidad=2, producto="sh keratina", marca="Nordica",
                      presentacion="900")], 1, 1),
    (4, [CORRECTO.model_copy(update={"cantidad": 3})], 0.6, 0.6),
    (5, [CORRECTO.model_copy(update={"cantidad": 20})], 0.4, 0.4),
    (6, [CORRECTO.model_copy(update={"unidad": "cajas"})], 0.4, 0.4),
    (7, [CORRECTO.model_copy(update={"duda": "¿2 o 7?"})], 0.8, 0.8),
    (8, [CORRECTO.model_copy(update={"cantidad": None})], 0.8, 0.8),
    (9, [RenglonLeido(texto="2 sh keratina Nordika x300", cantidad=2, producto="sh keratina", marca="Nordika",
                      presentacion="300")], 0.7, 0 / 2),
    (10, [RenglonLeido(texto="2 keratina Nordika", cantidad=2, producto="keratina", marca="Nordika")], 0.7, 0.8),
    (11, [RenglonLeido(texto="2 acond keratina Nordika 900", cantidad=2, producto="acond keratina", marca="Nordika",
                       presentacion="900")], 1, 0 / 2),
    (12, [], 0, 0),
    (13, [CORRECTO, RenglonLeido(texto="4 Belkor sh matizador 900", cantidad=4, producto="sh matizador",
                                 marca="Belkor", presentacion="900")], 1 / 2, 1 / 2),
    (14, [CORRECTO, RenglonLeido(texto="3 zapatillas running", cantidad=3, producto="zapatillas running",
                                 duda="¿zapatillas?")], 1 / 1.2, 1 / 1.2),
    (15, [CORRECTO, RenglonLeido(texto="2 Belkor crema de peinar rulos", cantidad=2, producto="crema de peinar rulos",
                                 marca="Belkor", duda="¿rulos?")], 1 / 2, 1 / 2),
]  # fmt: skip


@pytest.mark.parametrize(("fila", "leidos", "en_a", "en_b"), AMBIGUOS, ids=[f"fila-{f[0]}" for f in AMBIGUOS])
def test_casos_ambiguos_del_diseno(fila, leidos, en_a, en_b):
    """CA-241: cada fila de la tabla de casos ambiguos del diseño da su puntaje en (a) y en (b)."""
    caso = _caso("p01-A", [NORDIKA], tachados=[["belkor", "rulos"]])
    for nivel, esperado in (("a", en_a), ("b", en_b)):
        foto = em.puntuar_foto(caso, _imagen(*leidos), nivel)
        assert foto.S == pytest.approx(esperado), (fila, nivel)
    if fila == 15:
        assert foto.tachados_pedidos == 1


def _hoja_sintetica(foto, cuantos, *, lejos=0, tachado=False, persona=None, letra="clara", nitidez="nitida", faltan=0):
    """Una hoja de `cuantos` renglones bien leídos, `lejos` de ellos con la cantidad lejos, los últimos `faltan` sin
    leer y un tachado pedido."""
    esperados = [_esperado(n, [f"producto{n:03d}"], 2, f"SKU-{n:03d}") for n in range(1, cuantos + 1)]
    leidos = [RenglonLeido(texto=f"{20 if n <= lejos else 2} producto{n:03d}", cantidad=20 if n <= lejos else 2)
              for n in range(1, cuantos + 1 - faltan)]  # fmt: skip
    if tachado:
        leidos.append(RenglonLeido(texto="2 belkor rulos", cantidad=2))
    caso = _caso(foto, esperados, tachados=[["belkor", "rulos"]] if tachado else ())
    caso["condiciones"].update(letra=letra, nitidez=nitidez)
    if persona:
        caso["persona"] = persona
    return em.puntuar_foto(caso, _imagen(*leidos), "a", marcas=frozenset())


def _resumen(fotos):
    return em.resumir(fotos, [{"proveedor": "huggingface", "segundos": 1.0} for _ in fotos], [], None)


def test_filtro_de_elegibilidad():
    """CA-242: no es elegible si pide un tachado o si los errores graves pasan el 2 %; con 2 de 100, sí."""
    con_tachado = _resumen([_hoja_sintetica("p01-A", 100, tachado=True)])
    assert con_tachado["elegible"] is False and "1 tachado" in con_tachado["motivo"]
    tres = _resumen([_hoja_sintetica("p01-A", 100, lejos=3)])
    assert tres["elegible"] is False and tres["errores_graves_pct"] == 3.0 and "graves" in tres["motivo"]
    dos = _resumen([_hoja_sintetica("p01-A", 100, lejos=2)])
    assert dos["elegible"] is True and dos["motivo"] is None and dos["errores_graves"] == 2


def test_cortes_y_peores():
    """CA-243: S por hoja, las 5 peores hojas de menor a mayor, los peores renglones y los cortes nuevos."""
    fotos = [
        _hoja_sintetica("p01-A", 4, lejos=3, persona="p01", letra="dificil", nitidez="borrosa"),
        _hoja_sintetica("p01-B", 4, lejos=2, persona="p01", letra="dificil"),
        _hoja_sintetica("p02-A", 4, lejos=1, persona="p02"),
        _hoja_sintetica("p02-B", 4, persona="p02"),
        _hoja_sintetica("p03-A", 4, persona="p03"),
        _hoja_sintetica("p03-B", 4, lejos=4, persona="p03", nitidez="borrosa"),
    ]
    resumen = _resumen(fotos)
    assert resumen["S_por_hoja"] == pytest.approx(sum(foto.S for foto in fotos) / 6, abs=1e-3)
    assert [hoja["foto"] for hoja in resumen["peores_hojas"]] == ["p03-B", "p01-A", "p01-B", "p02-A", "p02-B"]
    primero = resumen["peores_renglones"][0]
    assert primero["id"] == "P001" and primero["apariciones"] == 6
    assert set(resumen["por_persona"]) == {"p01", "p02", "p03"}
    assert set(resumen["por_letra"]) == {"clara", "dificil"} and resumen["por_letra"]["dificil"] < 1
    assert set(resumen["por_nitidez"]) == {"nitida", "borrosa"}


def test_para_ordenar(carpeta):
    """CA-244: los mismos 15 pares las dos veces, solo de desarrollo, sin modelo ni puntaje en el documento."""
    fotos = [f"p{i:02d}-A" for i in range(1, 31)]
    grupos = {foto: "desarrollo" if i < 20 else "reservado" for i, foto in enumerate(fotos)}
    casos = _casos(carpeta, fotos, grupos)
    corrida = carpeta / "corridas" / "prueba"
    modelos = [("huggingface", "Qwen/falso"), ("openai", "falso")]
    em.correr(casos, carpeta, corrida, modelos, grupo="todos", max_llamadas=100, leer=Lector())
    puntuados: list[em.Puntuado] = []
    em.puntuar_corrida(corrida, casos, {}, detalle=puntuados)

    documento, clave, avisos = em.para_ordenar(puntuados, 15)
    assert (documento, clave) == em.para_ordenar(puntuados, 15)[:2] and avisos == []
    assert list(clave["items"]) == [f"O{i:02d}" for i in range(1, 16)]
    assert all(grupos[item["foto"]] == "desarrollo" for item in clave["items"].values())
    assert "falso" not in documento and "huggingface" not in documento and "openai" not in documento
    assert "S =" not in documento and all("S" in item for item in clave["items"].values())


def test_validar_orden():
    """CA-245: 100 % es válida; un par invertido de 0,4 no, aunque coincida el 90 %; uno de 0,05 sí; ids que faltan,
    error."""
    clave = {"items": {f"O0{i}": {"S": s} for i, s in enumerate([0.9, 0.5, 0.45, 0.4, 0.35], start=1)}}
    bien = em.validar_orden(clave, ["O01", "O02", "O03", "O04", "O05"])
    assert bien.acuerdo_pct == 100 and bien.valida
    grande = em.validar_orden(clave, ["O02", "O01", "O03", "O04", "O05"])
    assert grande.acuerdo_pct == 90 and not grande.valida and grande.invertidos == [("O02", "O01", 0.4)]
    chico = em.validar_orden(clave, ["O01", "O02", "O03", "O05", "O04"])
    assert chico.acuerdo_pct == 90 and chico.valida
    incompleto = em.validar_orden(clave, ["O01", "O02", "O03", "O04"])
    assert not incompleto.valida and any("O05" in problema for problema in incompleto.problemas)


def test_huellas_distintas_se_avisan(carpeta, monkeypatch):
    """CA-247: dos modelos leídos con prompts distintos se avisan y cada uno lleva su huella."""
    casos = _casos(carpeta, ["p01-A"])
    corrida = carpeta / "corridas" / "prueba"
    em.correr(casos, carpeta, corrida, [("huggingface", "uno")], leer=Lector())
    monkeypatch.setattr(vision_service, "INSTRUCCIONES", vision_service.INSTRUCCIONES + "\n- Otra regla.")
    em.correr(casos, carpeta, corrida, [("huggingface", "dos")], leer=Lector())
    resumen = em.puntuar_corrida(corrida, casos, {})
    huellas = {modelo["modelo"]: modelo["huella"] for modelo in resumen["modelos"]}
    assert len(set(huellas.values())) == 2
    assert any("no se comparan" in aviso for aviso in resumen["avisos"])


def test_correr_manda_las_marcas_del_catalogo_de_evaluacion(carpeta, settings, monkeypatch):
    """CA-250: con VISION_MARCAS, el prompt que recibe el modelo trae las marcas de catalogo_evaluacion.csv, no las
    del catálogo de producción, y es el mismo que firma la huella."""
    from internal.schemas.catalog_schema import Product
    from internal.services import inventory_service

    produccion = [Product(sku="R1", name="SHAMPOO X 1L", brand="MARCA DE PRODUCCION", price=100, stock=5)]
    monkeypatch.setattr(inventory_service, "get_catalog", lambda *_, **__: produccion)
    settings(VISION_MARCAS="true")
    prompts = []

    def leer(imagen, proveedor, modelo):
        prompts.append(vision_service.instrucciones())
        return Lector()(imagen, proveedor, modelo)

    casos = _casos(carpeta, ["p01-A"])
    em.correr(casos, carpeta, carpeta / "corridas" / "prueba", [("huggingface", "falso")], leer=leer)
    assert "NORDIKA" in prompts[0] and "MARCA DE PRODUCCION" not in prompts[0]
    assert inventory_service.get_catalog() == produccion  # el catálogo de producción vuelve a su lugar


def test_omitido_es_error_grave():
    """CA-252: un renglón que no aparece es un error silencioso y grave, y deja la configuración afuera."""
    resumen = _resumen([_hoja_sintetica("p01-A", 15, faltan=1)])
    assert resumen["errores_silenciosos"] == 1 and resumen["errores_graves"] == 1
    assert resumen["errores_graves_pct"] == 6.7 and resumen["elegible"] is False


def test_tachados_inventados():
    """CA-253: un tachado leído que no está en la revisión es inventado; S no cambia por eso."""
    caso = _caso("p01-A", [_esperado(1, ["silkara", "protector"], 7, "SIL-CU-PRO-250")])
    renglon = RenglonLeido(texto="7 Silkara protector", cantidad=7)
    con_tachados = _imagen(renglon).model_copy(update={"tachados": ["7 Silkara protector", "otro", "uno más"]})
    foto = em.puntuar_foto(caso, con_tachados, "a", marcas=frozenset())
    assert foto.tachados_inventados == 3
    assert foto.S == em.puntuar_foto(caso, _imagen(renglon), "a", marcas=frozenset()).S == 1

    caso_con_tachado = _caso("p01-A", caso["renglones"], tachados=[["belkor", "rulos"]])
    real = _imagen(renglon).model_copy(update={"tachados": ["2 Belkor crema de peinar rulos"]})
    assert em.puntuar_foto(caso_con_tachado, real, "a", marcas=frozenset()).tachados_inventados == 0
    assert _resumen([foto])["tachados_inventados"] == 3


def _salida_con(lineas: int, renglones: int, esperados: int = 15, encabezado: bool = False):
    """Una lectura con `lineas` líneas de producto en `texto` y `renglones` renglones, para una hoja de `esperados`."""
    texto = "\n".join([*(["Pedido para Norma", "Fecha: hoy"] if encabezado else []),
                       *(f"{n} producto{n:03d}" for n in range(1, lineas + 1))])  # fmt: skip
    imagen = ImagenLeida(legible=True, tipo="lista_pedido", texto=texto,
                         renglones=[RenglonLeido(texto=f"{n} producto{n:03d}", cantidad=n)
                                    for n in range(1, renglones + 1)])  # fmt: skip
    caso = _caso("p01-A", [_esperado(n, [f"producto{n:03d}"], n, f"SKU-{n:03d}") for n in range(1, esperados + 1)])
    return em.puntuar_foto(caso, imagen, "a", marcas=frozenset())


def test_senal_de_faltante():
    """CA-254: la señal dispara con más líneas de producto que renglones; el encabezado y la fecha no cuentan."""
    detectada, bien, no_detectada = _salida_con(15, 12), _salida_con(15, 15), _salida_con(14, 14)
    assert (detectada.senal, detectada.faltan) == (True, True)
    assert (bien.senal, bien.faltan) == (False, False)
    assert (no_detectada.senal, no_detectada.faltan) == (False, True)
    assert em.lineas_de_producto("Pedido para Norma\nFecha: hoy\n2 sh keratina") == 1
    assert _salida_con(15, 15, encabezado=True).senal is False
    falsa_alarma = _salida_con(16, 15)
    cuenta = em.senal_de_faltante([detectada, bien, no_detectada, falsa_alarma])
    assert cuenta == {"detectada": 1, "falsa_alarma": 1, "no_detectada": 1, "bien": 1}
    assert em.senal_de_faltante([detectada], para_revisar={"p01-A"})["detectada"] == 0


class LectorPorFoto(Lector):
    """Un modelo falso que devuelve una lectura distinta según la foto."""

    def __init__(self, por_foto: dict[str, ImagenLeida]):
        super().__init__()
        self.por_foto = por_foto

    def __call__(self, imagen, proveedor, modelo):
        self.resultado = self.por_foto[imagen.decode()]
        return super().__call__(imagen, proveedor, modelo)


def _leidos(cuantos: int, encabezado: bool = False) -> ImagenLeida:
    renglones = [RenglonLeido(texto=f"{n} producto{n:03d}", cantidad=n) for n in range(1, cuantos + 1)]
    if encabezado:
        renglones.insert(0, RenglonLeido(texto="Pedido para Norma"))
    return ImagenLeida(legible=True, tipo="lista_pedido", texto="Pedido para Norma", renglones=renglones)


def test_fotos_para_revisar(carpeta):
    """CA-251: si todos los modelos (al menos dos) leen renglones de menos, la foto va a revisar; el encabezado leído
    como renglón no cuenta."""
    esperados = [_esperado(n, [f"producto{n:03d}"], n, f"SKU-{n:03d}") for n in range(1, 16)]
    casos = []
    for foto in ("p01-A", "p02-A"):
        (carpeta / "imagenes" / f"{foto}.jpg").write_bytes(foto.encode())
        casos.append(_caso(foto, esperados))
    corrida = carpeta / "corridas" / "prueba"
    uno = LectorPorFoto({"p01-A": _leidos(12), "p02-A": _leidos(15)})
    dos = LectorPorFoto({"p01-A": _leidos(12, encabezado=True), "p02-A": _leidos(13)})
    em.correr(casos, carpeta, corrida, [("huggingface", "uno")], leer=uno)
    solo_uno = em.puntuar_corrida(corrida, casos, {})
    assert solo_uno["para_revisar"] == []

    em.correr(casos, carpeta, corrida, [("huggingface", "dos")], leer=dos)
    resumen = em.puntuar_corrida(corrida, casos, {})
    assert resumen["para_revisar"] == [
        {"foto": "p01-A", "esperados": 15, "leidos": {"huggingface:uno": 12, "huggingface:dos": 12}}
    ]


def _corridas_de_quince(carpeta):
    esperados = [_esperado(n, [f"producto{n:03d}"], n, f"SKU-{n:03d}") for n in range(1, 16)]
    casos = []
    for foto in ("p01-A", "p02-A"):
        (carpeta / "imagenes" / f"{foto}.jpg").write_bytes(foto.encode())
        casos.append(_caso(foto, esperados))
    return casos


def _correr_falso(casos, carpeta, corrida, modelo, por_foto, **kwargs):
    lector = LectorPorFoto({foto: _leidos(cuantos) for foto, cuantos in por_foto.items()})
    em.correr(casos, carpeta, corrida, [modelo], leer=lector, max_llamadas=10, **kwargs)


def test_resumen_markdown(carpeta):
    """CA-255: resumen.md tiene las tablas por nivel, las peores hojas y las fotos para revisar, sin datos de
    personas."""
    casos = _corridas_de_quince(carpeta)
    (carpeta / "casos.json").write_text(json.dumps(casos), encoding="utf-8")
    corrida = carpeta / "corridas" / "prueba"
    _correr_falso(casos, carpeta, corrida, ("huggingface", "uno"), {"p01-A": 12, "p02-A": 15})
    _correr_falso(casos, carpeta, corrida, ("openai", "dos"), {"p01-A": 12, "p02-A": 14})
    assert em.main(["puntuar", str(corrida), "--casos", str(carpeta / "casos.json"), "--markdown"]) == 0  # fmt: skip
    texto = (corrida / "resumen.md").read_text(encoding="utf-8")
    assert "## Nivel (a): lectura\n" in texto and "## Nivel (b): lectura + búsqueda\n" in texto
    assert (
        "| Modelo | Grupo | Fotos | S | Intervalo | Elegible | Errores graves % | Segundos p50 | USD por foto |"
        in texto
    )
    fila_dos = next(linea for linea in texto.splitlines() if linea.startswith("| openai:dos | desarrollo"))
    assert fila_dos.endswith("| ? |")
    assert "| huggingface:uno | desarrollo | 2 |" in texto and "| 0.0000 |" in texto
    peores = texto.split("## Peores hojas (nivel b)")[1].split("## Fotos para revisar")[0]
    assert "| openai:dos | desarrollo | p01-A |" in peores
    assert "| p01-A | 15 | huggingface:uno 12, openai:dos 12 |" in texto
    assert "p02-A" not in texto.split("## Fotos para revisar")[1]
    for dato in ("Pedido para Norma", "producto001", "persona"):
        assert dato not in texto


def test_comparar_corridas(carpeta):
    """CA-256: comparar dos corridas da la diferencia de S por modelo y nivel, y avisa si las fotos no coinciden o
    si falta un modelo."""
    casos = _corridas_de_quince(carpeta)
    (carpeta / "casos.json").write_text(json.dumps(casos), encoding="utf-8")
    antes, despues = carpeta / "corridas" / "antes", carpeta / "corridas" / "despues"
    _correr_falso(casos, carpeta, antes, ("huggingface", "uno"), {"p01-A": 12, "p02-A": 15})
    _correr_falso(casos, carpeta, despues, ("huggingface", "uno"), {"p01-A": 15}, solo="p01-A")
    _correr_falso(casos, carpeta, despues, ("openai", "dos"), {"p01-A": 15}, solo="p01-A")
    resumen_antes, resumen_despues = (em.puntuar_corrida(c, casos, {}) for c in (antes, despues))
    texto, avisos = em.comparar(resumen_antes, resumen_despues)
    fila = next(linea for linea in texto.splitlines() if linea.startswith("| huggingface:uno | a | desarrollo"))
    # antes: (12/15 + 15/15) / 2 = 0.9; después: 1.0
    assert fila.startswith("| huggingface:uno | a | desarrollo | 0.900 | 1.000 | +0.100 |")
    assert any("huggingface:uno" in aviso and "mismas fotos" in aviso and "p02-A" in aviso for aviso in avisos)
    assert any("openai:dos" in aviso and "solo en despues" in aviso for aviso in avisos)
    assert not any("openai:dos | " in linea for linea in texto.splitlines())

    assert em.main(["comparar", str(antes), str(despues), "--casos", str(carpeta / "casos.json"), "--markdown"]) == 0
    assert (despues / "comparacion.md").read_text(encoding="utf-8").startswith("# Comparación: antes → despues")
    assert not (antes / "comparacion.md").exists()
