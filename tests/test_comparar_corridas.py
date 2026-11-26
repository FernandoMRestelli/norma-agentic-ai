"""Comparación de dos corridas de la evaluación (RF-124, D-36), con resultados armados a mano."""

import json

import pytest

from internal import comparar_corridas, evaluar_agente

BASE = {"iteraciones": 1, "latencia_ms": 100, "tokens_entrada": 1000, "tokens_salida": 10}


def _corrida(resultados, *, orquestador=False, conjunto="pedidos", **fila):
    filas = [{**BASE, **fila, "caso": caso, "resultado": r} for caso, r in resultados]
    return {
        "conjunto": conjunto, "grupo": "todos", "repeticiones": 1, "proveedor": "falso", "modelo": "m",
        "prompt_version": "x", "orquestador": orquestador, "metricas": evaluar_agente._metricas(filas), "filas": filas,
    }  # fmt: skip


BIEN, MAL = "correcto", "incorrecto"


def test_casos_que_cambiaron():
    """CA-214: por tasa de acierto, con los que no cambiaron afuera."""
    a = _corrida([("x", BIEN), ("x", MAL), ("y", MAL), ("z", BIEN), ("solo-a", BIEN)])
    b = _corrida([("x", BIEN), ("x", BIEN), ("y", MAL), ("z", MAL)])
    assert comparar_corridas.cambios(a, b) == (["x: 1/2 → 2/2"], ["z: 1/1 → 0/1"])


def test_cuando_no_son_comparables():
    """CA-214: otro set, otros casos o casos sin evaluar no se comparan sin avisar."""
    a = _corrida([("x", BIEN)])
    assert comparar_corridas.comparables(a, _corrida([("x", MAL)])) == []
    motivos = comparar_corridas.comparables(a, _corrida([("y", "sin_evaluar")], conjunto="mensajes"))
    assert motivos == ["conjunto: pedidos y mensajes", "no corrieron los mismos casos", "B tiene casos sin evaluar"]


def test_intervalos_superpuestos():
    """CA-214: 3 de 5 contra 4 de 5 puede ser azar; 0 de 30 contra 30 de 30, no."""
    def metricas(bien, total):
        return _corrida([(str(i), BIEN if i < bien else MAL) for i in range(total)])["metricas"]

    assert comparar_corridas.se_superponen(metricas(3, 5), metricas(4, 5))
    assert not comparar_corridas.se_superponen(metricas(0, 30), metricas(30, 30))


BUENAS = {"llamadas_llm_promedio": 3, "tokens_entrada_por_llamada_sub_loop": 500, "recall_lineas_pct": 94.0}


@pytest.mark.parametrize(
    ("cambio", "fallas"),
    [
        ({}, 0),
        ({"llamadas_llm_promedio": 5}, 1),
        ({"tokens_entrada_por_llamada_sub_loop": 501}, 1),
        ({"tokens_entrada_por_llamada_sub_loop": 0}, 1),  # no delegó nunca: no hay nada que comparar
        ({"recall_lineas_pct": 93.9}, 1),
        ({"llamadas_llm_promedio": 9, "recall_lineas_pct": 50.0}, 2),
    ],
)
def test_rnf_17(cambio, fallas):
    """CA-215: menos llamadas, sub-loop con la mitad de los tokens y recall sin bajar más de 2 puntos."""
    sin = {"llamadas_llm_promedio": 5, "tokens_entrada_por_llamada": 1000, "recall_lineas_pct": 96.0}
    assert len(comparar_corridas.rnf_17(sin, {**BUENAS, **cambio})) == fallas


def test_de_punta_a_punta(tmp_path, capsys):
    """CA-215: lee lo que escribe la evaluación y el veredicto no depende del orden de los archivos."""
    sin = _corrida([("x", BIEN)], lineas_esperadas=10, lineas_correctas=10, lineas_propuestas=10, llamadas_llm=4)
    con = _corrida([("x", BIEN)], orquestador=True, lineas_esperadas=10, lineas_correctas=10, lineas_propuestas=10,
                   llamadas_llm=3, tokens_entrada_sub_loop=400, llamadas_sub_loop_ultimo_turno=1)  # fmt: skip
    rutas = []
    for nombre, corrida in (("sin", sin), ("con", con)):
        rutas.append(tmp_path / f"{nombre}.json")
        rutas[-1].write_text(json.dumps(corrida), encoding="utf-8")
    sin_path, con_path = map(str, rutas)

    assert comparar_corridas.main([sin_path, con_path]) == comparar_corridas.main([con_path, sin_path]) == 0
    assert capsys.readouterr().out.count("RNF-17: el orquestador cumple") == 2
    assert comparar_corridas.main([sin_path, sin_path]) == 0
    assert "RNF-17" not in capsys.readouterr().out
