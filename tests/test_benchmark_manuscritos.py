"""Listas y Google Form del benchmark de pedidos escritos a mano (docs/spec/benchmark-manuscritos.md)."""

import csv
import json
import random
import re
from collections import Counter

import pytest

from internal import benchmark_manuscritos as bm


@pytest.fixture(scope="module")
def pool():
    return bm.leer_pool()


@pytest.fixture(scope="module")
def listas(pool):
    return bm.sortear_listas(pool, 30, 116)["listas"]


def test_pool_coherente_con_el_catalogo(pool):
    """CA-218: el pool versionado no tiene problemas contra catalogo_evaluacion.csv."""
    assert bm.validar_pool(pool, bm.leer_skus()) == []


def test_listas_reproducibles(pool):
    """CA-219: la misma semilla da las mismas listas y otra semilla da otras."""
    assert bm.sortear_listas(pool, 30, 116) == bm.sortear_listas(pool, 30, 116)
    assert bm.sortear_listas(pool, 30, 116)["listas"] != bm.sortear_listas(pool, 30, 117)["listas"]


def test_lista_cubre_las_dificultades(pool, listas):
    """CA-220: núcleo en 1 a 5, 10 sorteados que cubren todos los tipos, posiciones 1 a 15 y SKU sin repetir."""
    nucleo = [renglon.id for renglon in pool if renglon.origen == "nucleo"]
    for lista in listas:
        renglones = lista["renglones"]
        assert [r["id"] for r in renglones[:5]] == nucleo
        assert [r["n"] for r in renglones[:5]] == [1, 2, 3, 4, 5]
        variables = renglones[5:]
        assert len(variables) == 10 and all(r["origen"] == "pool" for r in variables)
        cubiertos = {tipo for r in variables for tipo in r["dificultad"]}
        assert set(bm.TIPOS) <= cubiertos, lista["id"]
        assert [r["n"] for r in renglones] == list(range(1, 16))
        assert len({r["sku"] for r in renglones}) == 15


def test_sin_renglon_para_tachar(listas):
    """CA-221: ninguna lista asigna un renglón para tachar, y el inicio dice que se puede tachar si se equivocan."""
    for lista in listas:
        assert "tachado" not in lista
        assert not any("TACHAR" in linea.upper() for linea in bm.lineas_de_la_hoja(lista))
    assert "Si te equivocás al escribir, tachalo" in bm.INICIO


def _renglon(id_: str, origen: str, sku: str, dificultad: tuple[str, ...]) -> bm.Renglon:
    return bm.Renglon(id_, origen, f"2 {id_}", sku, 2, None, None, (id_.lower(),), dificultad)


def test_pool_sin_un_tipo_falla():
    """CA-222: un pool sintético sin renglones de «tono» no da una lista incompleta: falla nombrando el tipo."""
    otros = tuple(tipo for tipo in bm.TIPOS if tipo != "tono")
    pool = [_renglon(f"N{i}", "nucleo", f"SKU-N{i}", ()) for i in range(1, 6)]
    pool += [_renglon(f"P{i:03d}", "pool", f"SKU-P{i}", otros) for i in range(1, 40)]
    with pytest.raises(ValueError, match="tono"):
        bm.sortear_lista(pool, 1, random.Random(1))


def test_script_del_form(pool):
    """CA-223: una sección por lista con el encabezado fijo y sus 15 renglones, desplegable, salto a la final,
    sin mails ni ángulo, JSON válido y solo ASCII."""
    listas = bm.sortear_listas(pool, 2, 116)
    listas["listas"][0]["renglones"][5]["texto"] = 'Kapris sh "matizador" 300'
    script = bm.script_del_form(listas)

    datos = json.loads(re.search(r"^const LISTAS = (\[.*?^\]);$", script, re.S | re.M).group(1))
    assert [seccion["id"] for seccion in datos] == ["L01", "L02"]
    for seccion, lista in zip(datos, listas["listas"], strict=True):
        lineas = bm.lineas_de_la_hoja(lista)
        assert len(lineas) == 15
        assert seccion["texto"].endswith("\n".join(lineas))
        assert "\n\nPedido para Norma\n\n" in seccion["texto"]
    assert [seccion["nombre"] for seccion in datos] == ["Pedido 01", "Pedido 02"]
    assert "elegir.createChoice(lista.nombre, secciones[i])" in script
    assert "seccion.setGoToPage(final)" in script
    assert "setCollectEmail(false)" in script
    assert "addTextItem" not in script  # no pide nombre ni mail
    # Solo ASCII, para que copiar y pegar no rompa los acentos; el JSON los recupera.
    assert script.isascii()
    textos = json.loads(re.search(r"^const TEXTOS = (\{.*?^\});$", script, re.S | re.M).group(1))
    assert textos["elegir_lista"] == "Elegí un pedido"
    assert datos[0]["texto"].startswith("Escribí este pedido en un papel e incluí el encabezado.")
    assert textos["sexos"] == ["Masculino", "Femenino", "Prefiero no responder"]
    assert "setTitle(TEXTOS.sexo)" in script
    assert "ngulo" not in script  # la pregunta del ángulo se sacó


T = bm.TEXTOS


def _respuesta(foto: str, pedido: str, copio: str = "Sí", luz: str = "Natural", papel: str = "Hoja lisa") -> dict:
    """Una fila de respuestas.csv como la deja quien revisa: el export del Form más la columna `foto`."""
    return {
        "Marca temporal": "2026/12/01 10:00:00",
        T["consentimiento_titulo"]: T["consentimiento"],
        T["elegir_lista"]: pedido,
        T["copio_todo"]: copio,
        T["cambios"]: "",
        T["luz"]: luz,
        T["papel"]: papel,
        T["sexo"]: "Prefiero no responder",
        "foto": foto,
    }


@pytest.fixture
def carpeta(tmp_path):
    (tmp_path / "imagenes").mkdir()
    for foto in ("p01-A.jpg", "p01-B.png", "p02-A.jpg"):
        (tmp_path / "imagenes" / foto).write_bytes(b"foto")
    return tmp_path


def test_casos_desde_respuestas(pool, carpeta):
    """CA-224: un caso por foto no descartada, con los renglones de su lista menos los salteados, los tachados de
    la revisión y las condiciones del Form."""
    listas = bm.sortear_listas(pool, 7, 116)
    respuestas = [
        _respuesta("p01-A", "Pedido 07", copio="No", luz="Artificial", papel="Cuaderno rayado"),
        _respuesta("p01-B", "Pedido 02"),
        _respuesta("p02-A", "Pedido 03"),
    ]
    revision = {
        "p01-A": {"sin_copiar": [11], "tachados": [["belkor", "rulos"]], "notas": "cambió el 11"},
        "p02-A": {"descartar": "la foto corta la mitad de la hoja"},
    }
    armado = bm.armar_casos(respuestas, revision, listas, carpeta)

    assert armado.problemas == []
    assert armado.descartadas == {"p02-A": "la foto corta la mitad de la hoja"}
    primero, segundo = armado.casos
    assert primero["id"] == "p01-A" and primero["lista"] == "L07" and primero["archivo"] == "imagenes/p01-A.jpg"
    assert primero["persona"] == "p01" and primero["hoja"] == "A"
    assert [r["n"] for r in primero["renglones"]] == [n for n in range(1, 16) if n != 11]
    assert primero["renglones"][0] == listas["listas"][6]["renglones"][0]
    assert primero["tachados"] == [["belkor", "rulos"]] and primero["sin_copiar"] == [11]
    assert primero["condiciones"] == {"luz": "artificial", "papel": "cuaderno rayado"}
    assert primero["sexo"] == "prefiero no responder"
    assert primero["legible"] is True and primero["encabezado"] == "Pedido para Norma"
    assert segundo["lista"] == "L02" and segundo["archivo"] == "imagenes/p01-B.png"
    assert len(segundo["renglones"]) == 15 and segundo["tachados"] == [] and segundo["sin_copiar"] == []
    assert primero["grupo"] == segundo["grupo"]  # las dos hojas de la misma persona


def test_grupos_por_persona():
    """CA-225: el 40 % de las personas a desarrollo, reproducible, y quien ya tenía grupo lo conserva."""
    personas = [f"p{i:02d}" for i in range(1, 11)]
    grupos = bm.asignar_grupos(personas, semilla=130)
    assert grupos == bm.asignar_grupos(personas, semilla=130)
    assert Counter(grupos.values()) == {"desarrollo": 4, "reservado": 6}

    con_nuevas = bm.asignar_grupos([*personas, *(f"p{i:02d}" for i in range(11, 16))], semilla=130, anteriores=grupos)
    assert {p: con_nuevas[p] for p in personas} == grupos
    assert Counter(con_nuevas.values()) == {"desarrollo": 6, "reservado": 9}


def test_grupos_comparten_las_hojas(pool, carpeta):
    """CA-225: las hojas de una persona van al mismo grupo aunque haya varias personas."""
    for i in range(3, 11):
        (carpeta / "imagenes" / f"p{i:02d}-A.jpg").write_bytes(b"foto")
    respuestas = [_respuesta("p01-A", "Pedido 01"), _respuesta("p01-B", "Pedido 02")]
    respuestas += [_respuesta(f"p{i:02d}-A", "Pedido 01") for i in range(3, 11)]
    respuestas.append(_respuesta("p02-A", "Pedido 01"))
    armado = bm.armar_casos(respuestas, {}, bm.sortear_listas(pool, 2, 116), carpeta)
    assert armado.problemas == []
    por_persona: dict[str, set[str]] = {}
    for caso in armado.casos:
        por_persona.setdefault(caso["persona"], set()).add(caso["grupo"])
    assert len(por_persona) == 10 and all(len(grupos) == 1 for grupos in por_persona.values())


def test_casos_con_problemas(pool, carpeta):
    """CA-226: cada problema sale con su foto y no se arma ningún caso."""
    listas = bm.sortear_listas(pool, 2, 116)
    respuestas = [
        _respuesta("p01-A", "Pedido 31"),
        _respuesta("p01-B", "Pedido 01", copio="No"),
        _respuesta("p02-A", "Pedido 01"),
        _respuesta("p02-A", "Pedido 02"),
        _respuesta("p09-A", "Pedido 01"),
        _respuesta("persona-3", "Pedido 01"),
        _respuesta("p01-C", "Pedido 01"),
    ]
    (carpeta / "imagenes" / "p03-A.jpg").write_bytes(b"foto")
    respuestas.append(_respuesta("p03-A", "Pedido 02"))
    revision = {"p03-A": {"sin_copiar": [16]}, "p07-B": {"tachados": []}}
    armado = bm.armar_casos(respuestas, revision, listas, carpeta)

    assert armado.casos == []
    esperados = {
        "p01-A": "Pedido 31",
        "p01-B": "No",
        "p02-A": "repetida",
        "p09-A": "archivo",
        "persona-3": "pNN",
        "p07-B": "revisión",
    }
    for foto, texto in esperados.items():
        assert any(foto in p and texto in p for p in armado.problemas), (foto, armado.problemas)
    assert any("p01-C" in p and "pNN" in p for p in armado.problemas)
    assert any("p03-A" in p and "16" in p for p in armado.problemas)


def test_casos_sin_datos_de_la_persona(pool, carpeta):
    """CA-226: una columna de mail o un link de Drive frena el armado antes de mirar las fotos, y nombra la columna."""
    listas = bm.sortear_listas(pool, 1, 116)
    con_mail = _respuesta("p01-A", "Pedido 31")
    con_mail["Dirección de correo electrónico"] = ""
    armado = bm.armar_casos([con_mail], {}, listas, carpeta)
    assert armado.casos == []
    assert armado.problemas == [bm.problema_de_privacidad("Dirección de correo electrónico")]

    con_link = _respuesta("p01-A", "Pedido 01")
    con_link["La foto de la hoja"] = "https://drive.google.com/open?id=abc"
    armado = bm.armar_casos([con_link], {}, listas, carpeta)
    assert armado.casos == [] and armado.problemas == [bm.problema_de_privacidad("La foto de la hoja")]


def test_comando_casos_conserva_los_grupos(pool, carpeta):
    """CA-225: correr `casos` otra vez con una persona más no le cambia el grupo a las que ya estaban."""
    listas_json = carpeta / "listas.json"
    listas_json.write_text(json.dumps(bm.sortear_listas(pool, 2, 116)), encoding="utf-8")
    respuestas_csv = carpeta / "respuestas.csv"
    salida = carpeta / "casos.json"

    def correr(filas):
        with open(respuestas_csv, "w", encoding="utf-8", newline="") as archivo:
            escritor = csv.DictWriter(archivo, fieldnames=list(filas[0]))
            escritor.writeheader()
            escritor.writerows(filas)
        argumentos = ["casos", "--respuestas", str(respuestas_csv), "--revision", str(carpeta / "revision.json")]
        assert bm.main([*argumentos, "--listas", str(listas_json), "--salida", str(salida)]) == 0
        return {caso["persona"]: caso["grupo"] for caso in json.loads(salida.read_text(encoding="utf-8"))}

    antes = correr([_respuesta("p01-A", "Pedido 01")])
    despues = correr([_respuesta("p01-A", "Pedido 01"), _respuesta("p02-A", "Pedido 02")])
    assert despues["p01"] == antes["p01"] and set(despues) == {"p01", "p02"}
