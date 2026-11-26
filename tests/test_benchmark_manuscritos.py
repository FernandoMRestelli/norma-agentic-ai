"""Listas y Google Form del benchmark de pedidos escritos a mano (docs/spec/benchmark-manuscritos.md)."""

import json
import random
import re

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
