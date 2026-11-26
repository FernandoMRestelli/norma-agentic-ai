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


def test_correr_solo_desarrollo(carpeta):
    """CA-231: sin --grupo solo se corren las fotos de desarrollo."""
    grupos = {"p01-A": "desarrollo", "p02-A": "reservado", "p03-A": "desarrollo"}
    casos = _casos(carpeta, list(grupos), grupos)
    lector = Lector()
    em.correr(casos, carpeta, carpeta / "corridas" / "prueba", [("huggingface", "falso")], leer=lector)
    assert lector.fotos == ["p01-A", "p03-A"]


def test_nivel_a_empareja_por_palabras():
    """CA-232: el esperado toma el primer leído con sus palabras; la marca acepta una letra cambiada, lo demás no."""
    caso = _caso("p01-A", [_esperado(1, ["keratina", "nordika", "900"], 2, "NOR-SH-KER-900")])
    imagen = _imagen(
        RenglonLeido(texto="2 keratna Nordika 900", cantidad=2),
        RenglonLeido(texto="2 sh keratina Nordica x900", cantidad=2),
        RenglonLeido(texto="1 sh keratina Nordika 900", cantidad=1),
    )
    foto = em.puntuar_foto(caso, imagen, "a", marcas=frozenset({"nordika"}))
    assert [par.leido.texto for par in foto.pares] == ["2 sh keratina Nordica x900"]
    assert foto.pares[0].s == 1
    assert [leido.texto for leido in foto.sobrantes] == ["2 keratna Nordika 900", "1 sh keratina Nordika 900"]


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
    assert [leido.texto for leido in foto.sobrantes] == ["zapatillas running"]


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
    """CA-234: S = 13,4 / 16; precision y recall 0,80; dos errores silenciosos y un tachado pedido."""
    caso, imagen = _hoja_del_diseno()
    foto = em.puntuar_foto(caso, imagen, nivel, buscar=_buscar_falso)
    assert foto.numerador == pytest.approx(13.4) and foto.denominador == 16
    assert foto.numerador / foto.denominador == pytest.approx(0.8375)
    assert foto.correctos == 12 and foto.leidos == 15 and len(foto.omitidos) == 1
    assert foto.errores_silenciosos == 2 and foto.tachados_pedidos == 1
    assert not foto.perfecta and foto.json_valido


def test_json_invalido_da_cero():
    """CA-235: una salida sin resultado de una foto legible da S = 0 con todo omitido."""
    caso, _ = _hoja_del_diseno()
    foto = em.puntuar_foto(caso, None, "b", buscar=_buscar_falso)
    assert foto.numerador == 0 and foto.denominador == 15 and len(foto.omitidos) == 15
    assert not foto.json_valido and not foto.perfecta


def test_detalle_normalizado():
    """CA-236: «7,1» es el tono «7.1»; «cajas» no es «unidades»."""
    caso = _caso(
        "p01-A",
        [
            _esperado(1, ["kapris", "tintura"], 3, "KAP-CO-60", tono="7.1"),
            _esperado(2, ["duomo", "sachet"], 2, "DUO-DE-50", unidad="unidades"),
        ],
    )
    imagen = _imagen(
        RenglonLeido(texto="Kapris tintura 7,1 x3", producto="Kapris tintura",
                     variantes=[Variante(detalle="", tono="7,1", cantidad=3)]),
        RenglonLeido(texto="2 cajas Duomo sachet", cantidad=2, producto="Duomo sachet", unidad="cajas"),
    )  # fmt: skip
    foto = em.puntuar_foto(caso, imagen, "a")
    primero, segundo = foto.pares
    assert primero.detalle_ok and primero.s == 1
    assert not segundo.detalle_ok and segundo.s == pytest.approx(0.9)


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
