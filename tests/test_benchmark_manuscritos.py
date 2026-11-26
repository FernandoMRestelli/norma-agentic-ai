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
    """CA-220: núcleo en 1 a 5, 10 sorteados que cubren todos los tipos, posiciones 1 a 16 y SKU sin repetir."""
    nucleo = [renglon.id for renglon in pool if renglon.origen == "nucleo"]
    for lista in listas:
        renglones = lista["renglones"]
        assert [r["id"] for r in renglones[:5]] == nucleo
        assert [r["n"] for r in renglones[:5]] == [1, 2, 3, 4, 5]
        variables = renglones[5:]
        assert len(variables) == 10 and all(r["origen"] == "pool" for r in variables)
        cubiertos = {tipo for r in variables for tipo in r["dificultad"]}
        assert set(bm.TIPOS) <= cubiertos, lista["id"]
        posiciones = [r["n"] for r in renglones] + [lista["tachado"]["n"]]
        assert sorted(posiciones) == list(range(1, 17))
        skus = [r["sku"] for r in renglones]
        tachado_sku = next(r.sku for r in pool if r.id == lista["tachado"]["id"])
        assert len(set(skus + [tachado_sku])) == 16


def test_tachado_fuera_de_los_esperados(pool, listas):
    """CA-221: el tachado es del pool, no está entre los esperados y va entre la posición 6 y la 16."""
    sorteables = {renglon.id for renglon in pool if renglon.origen == "pool"}
    for lista in listas:
        tachado = lista["tachado"]
        assert tachado["id"] in sorteables
        assert tachado["id"] not in {r["id"] for r in lista["renglones"]}
        assert 6 <= tachado["n"] <= 16


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
    """CA-223: una sección por lista con sus 16 renglones, desplegable, salto a la final, sin mails y JSON válido."""
    listas = bm.sortear_listas(pool, 2, 116)
    listas["listas"][0]["encabezado"]["cliente"] = 'Salón "La Comilla"'
    script = bm.script_del_form(listas)

    datos = json.loads(re.search(r"^const LISTAS = (\[.*?^\]);$", script, re.S | re.M).group(1))
    assert [seccion["id"] for seccion in datos] == ["L01", "L02"]
    for seccion, lista in zip(datos, listas["listas"], strict=True):
        lineas = bm.lineas_de_la_hoja(lista)
        assert len(lineas) == 16
        assert sum("← TACHAR" in linea for linea in lineas) == 1
        assert seccion["texto"].endswith("\n".join(lineas))
        assert f"{lista['encabezado']['cliente']} — {lista['encabezado']['fecha']}" in seccion["texto"]
    assert "elegir.createChoice(lista.id, secciones[i])" in script
    assert "seccion.setGoToPage(final)" in script
    assert "setCollectEmail(false)" in script
    assert "addTextItem" not in script  # no pide nombre ni mail
