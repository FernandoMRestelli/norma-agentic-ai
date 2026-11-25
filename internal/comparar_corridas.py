"""Compara dos corridas de la evaluación: python -m internal.comparar_corridas antes.json después.json

Cada archivo sale de `evaluar_agente --salida`. Muestra las métricas lado a lado, los casos que cambiaron y, si una
corrió con el orquestador y la otra no, si cumple RNF-17 (D-36). Una diferencia de aciertos con los intervalos
superpuestos puede ser azar: lo dice.
"""

import argparse
import json
from pathlib import Path
from typing import Any

METRICAS = (
    "aciertos_pct",
    "casos_firmes_pct",
    "recall_lineas_pct",
    "precision_lineas_pct",
    "lineas_perdidas",
    "registros_indebidos",
    "aclaraciones_de_mas",
    "estilo_ok_pct",
    "llamadas_llm_promedio",
    "tokens_entrada_por_llamada",
    "tokens_entrada_por_llamada_sub_loop",
    "latencia_p50_ms",
    "latencia_p95_ms",
    "sin_evaluar",
    "tokens_entrada_total",
)
# RNF-17: cuánto puede bajar el recall, y qué parte de los tokens por llamada puede gastar el sub-loop
MAX_BAJA_RECALL = 2.0
MAX_TOKENS_SUB_LOOP = 0.5


def se_superponen(a: dict[str, float], b: dict[str, float]) -> bool:
    return a["aciertos_ic95_bajo"] <= b["aciertos_ic95_alto"] and b["aciertos_ic95_bajo"] <= a["aciertos_ic95_alto"]


def comparables(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """Por qué dos corridas no miden lo mismo, además de lo que se quiso cambiar."""
    motivos = [
        f"{campo}: {a.get(campo)} y {b.get(campo)}"
        for campo in ("conjunto", "grupo", "repeticiones")
        if a.get(campo) != b.get(campo)
    ]
    if {f["caso"] for f in a["filas"]} != {f["caso"] for f in b["filas"]}:
        motivos.append("no corrieron los mismos casos")
    return motivos + [f"{n} tiene casos sin evaluar" for n, c in (("A", a), ("B", b)) if c["metricas"]["sin_evaluar"]]


def tasas(corrida: dict[str, Any]) -> dict[str, tuple[int, int]]:
    """Por caso, cuántas repeticiones acertó y cuántas se evaluaron."""
    resultado: dict[str, tuple[int, int]] = {}
    for fila in corrida["filas"]:
        if fila["resultado"] != "sin_evaluar":
            bien, total = resultado.get(fila["caso"], (0, 0))
            resultado[fila["caso"]] = (bien + (fila["resultado"] == "correcto"), total + 1)
    return resultado


def cambios(a: dict[str, Any], b: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Los casos que mejoraron y los que empeoraron, por su tasa de acierto."""
    antes, despues = tasas(a), tasas(b)
    mejoran, empeoran = [], []
    for caso in antes.keys() & despues.keys():
        (bien_a, total_a), (bien_b, total_b) = antes[caso], despues[caso]
        texto = f"{caso}: {bien_a}/{total_a} → {bien_b}/{total_b}"
        if bien_b / total_b > bien_a / total_a:
            mejoran.append(texto)
        elif bien_b / total_b < bien_a / total_a:
            empeoran.append(texto)
    return sorted(mejoran), sorted(empeoran)


def rnf_17(sin: dict[str, float], con: dict[str, float]) -> list[str]:
    """Qué no cumple el orquestador frente al loop de hoy. Vacía si cumple."""
    fallas = []
    if con["llamadas_llm_promedio"] >= sin["llamadas_llm_promedio"]:
        fallas.append(f"llamadas por caso: {con['llamadas_llm_promedio']}, no baja de {sin['llamadas_llm_promedio']}")
    tope = sin["tokens_entrada_por_llamada"] * MAX_TOKENS_SUB_LOOP
    if not 0 < con["tokens_entrada_por_llamada_sub_loop"] <= tope:
        fallas.append(f"tokens por llamada del sub-loop: {con['tokens_entrada_por_llamada_sub_loop']}, tope {tope}")
    if con["recall_lineas_pct"] < sin["recall_lineas_pct"] - MAX_BAJA_RECALL:
        fallas.append(f"recall por línea: {con['recall_lineas_pct']} contra {sin['recall_lineas_pct']}")
    return fallas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("a", type=Path)
    parser.add_argument("b", type=Path)
    args = parser.parse_args(argv)
    a, b = (json.loads(path.read_text(encoding="utf-8")) for path in (args.a, args.b))

    for nombre, corrida in (("A", a), ("B", b)):
        print(
            f"{nombre}: {corrida['proveedor']}/{corrida['modelo']}, prompt {corrida['prompt_version']}, "
            f"orquestador {'sí' if corrida['orquestador'] else 'no'}, {corrida['conjunto']} × {corrida['repeticiones']}"
        )
    for motivo in comparables(a, b):
        print(f"⚠️  No son del todo comparables: {motivo}")
    print(f"\n{'':38}{'A':>12}{'B':>12}")
    for metrica in METRICAS:
        print(f"{metrica:38}{a['metricas'][metrica]:>12}{b['metricas'][metrica]:>12}")
    if se_superponen(a["metricas"], b["metricas"]):
        print("\nLos intervalos de aciertos se superponen: la diferencia puede ser azar")
    mejoran, empeoran = cambios(a, b)
    for titulo, casos in (("Mejoran", mejoran), ("Empeoran", empeoran)):
        if casos:
            print(f"\n{titulo}:\n  " + "\n  ".join(casos))
    if a["orquestador"] == b["orquestador"]:
        return 0
    sin, con = (a, b) if b["orquestador"] else (b, a)
    fallas = rnf_17(sin["metricas"], con["metricas"])
    print("\nRNF-17: el orquestador " + (f"no cumple ({'; '.join(fallas)})" if fallas else "cumple"))
    return 1 if fallas else 0


if __name__ == "__main__":
    raise SystemExit(main())
