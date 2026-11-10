"""Corre los casos de data_example/mensajes.json con el LLM real: python -m internal.evaluar_agente

Es una evaluación manual, fuera del CI (spec del agente §10). Usa una base temporal: no toca los pedidos locales.
Solo manda datos inventados. Cada caso hace varias llamadas al LLM: en el free tier conviene usar --pausa.
Con --mlflow, la corrida queda en MLflow con sus métricas, una tabla por caso y la traza de cada turno.
"""

import argparse
import contextlib
import json
import math
import os
import tempfile
import time
from pathlib import Path

CASES = Path(__file__).resolve().parent.parent / "data_example" / "mensajes.json"
KIND_POR_RESULTADO = {"borrador": "propuesta"}


def _items(draft_or_order: list) -> dict[str, int]:
    return {item.sku: item.quantity for item in draft_or_order}


def _passes(case: dict, outcome, order_items: dict[str, int] | None) -> bool:
    expected = case["esperado"]
    wanted = expected["resultado"]
    items = {item["sku"]: item["cantidad"] for item in expected.get("items", [])}
    if wanted == "sin_pedido":
        return order_items is None
    if wanted == "pedido":
        return order_items == items
    if outcome.kind != KIND_POR_RESULTADO.get(wanted, wanted):
        return False
    if wanted == "borrador":
        return outcome.draft is not None and _items(outcome.draft.items) == items
    if wanted == "aclaracion":
        return set(outcome.options) == set(expected["opciones"])
    return True


def _percentil(valores: list[int], p: float) -> int:
    """Percentil por rango más cercano: con pocos casos alcanza y no interpola."""
    if not valores:
        return 0
    ordenados = sorted(valores)
    return ordenados[max(math.ceil(p / 100 * len(ordenados)) - 1, 0)]


def _metricas(filas: list[dict]) -> dict[str, float]:
    evaluadas = [fila for fila in filas if fila["resultado"] != "sin_evaluar"]
    correctas = sum(fila["resultado"] == "correcto" for fila in evaluadas)
    latencias = [fila["latencia_ms"] for fila in evaluadas]
    return {
        "aciertos_pct": round(100 * correctas / len(evaluadas), 1) if evaluadas else 0.0,
        "correctos": correctas,
        "evaluados": len(evaluadas),
        "sin_evaluar": len(filas) - len(evaluadas),
        "iteraciones_promedio": round(sum(f["iteraciones"] for f in evaluadas) / len(evaluadas), 2) if evaluadas else 0,
        "tokens_entrada": sum(fila["tokens_entrada"] for fila in filas),
        "tokens_salida": sum(fila["tokens_salida"] for fila in filas),
        "latencia_p50_ms": _percentil(latencias, 50),
        "latencia_p95_ms": _percentil(latencias, 95),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pausa", type=float, default=0, help="segundos de espera entre casos")
    parser.add_argument("--caso", help="corre solo el caso con este id")
    parser.add_argument("--mlflow", action="store_true", help="guarda la corrida en MLflow (ver MLFLOW_* en .env)")
    args = parser.parse_args()

    # Antes de importar la app: la base se elige al importarla
    os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp(prefix='norma-eval-')) / 'norma.db'}"
    if args.mlflow:
        os.environ["MLFLOW_ENABLED"] = "true"

    from langgraph.checkpoint.memory import InMemorySaver

    from internal.agent import observabilidad
    from internal.agent.llm import get_chat_model
    from internal.agent.norma_agent import NormaAgent
    from internal.config import get_settings
    from internal.services import conversation_service

    conversation_service.startup()
    agent = NormaAgent(get_chat_model(), InMemorySaver())

    cases = [case for case in json.loads(CASES.read_text(encoding="utf-8")) if args.caso in (None, case["id"])]
    corrida: contextlib.AbstractContextManager = contextlib.nullcontext()
    if args.mlflow:
        mlflow = observabilidad.cliente()
        if mlflow is None:
            print("No se pudo conectar con MLflow: revisá MLFLOW_TRACKING_URI y que el servidor esté levantado")
            return 2
        settings = get_settings()
        corrida = mlflow.start_run(run_name=f"{settings.llm_provider}/{settings.llm_model} {agent.prompt_version}")

    filas = []
    with corrida:
        for number, case in enumerate(cases):
            if number and args.pausa:
                time.sleep(args.pausa)
            filas.append(_evaluar(agent, case))
        if args.mlflow:
            _registrar(mlflow, agent, cases, args.caso, filas)

    passed = sum(fila["resultado"] == "correcto" for fila in filas)
    not_evaluated = sum(fila["resultado"] == "sin_evaluar" for fila in filas)
    print(f"\n{passed} de {len(cases) - not_evaluated} casos correctos, {not_evaluated} sin evaluar")
    return 0 if passed == len(cases) else 1


def _evaluar(agent, case: dict) -> dict:
    from internal.agent import observabilidad
    from internal.schemas.order_schema import Channel
    from internal.services import order_service

    message = case["mensaje"]
    turn = {"thread_id": case["id"], "customer_id": message["customer_id"], "channel": Channel(message["channel"])}
    if case.get("previo"):
        agent.handle(text=case["previo"], **turn)
    inicio = time.perf_counter()
    outcome = agent.handle(text=message["text"], **turn)
    latencia_ms = round((time.perf_counter() - inicio) * 1000)

    state = agent.graph.get_state({"configurable": {"thread_id": case["id"]}}).values
    calls = [call["name"] for m in state["messages"] for call in getattr(m, "tool_calls", [])]
    resumen = observabilidad.resumir(state)
    fila = {
        "caso": case["id"],
        "resultado": "sin_evaluar",
        "kind": outcome.kind,
        "esperado": json.dumps(case["esperado"], ensure_ascii=False),
        "iteraciones": outcome.iterations,
        "tools": ",".join(resumen.tools),
        "skills": ",".join(resumen.skills),
        "tokens_entrada": resumen.tokens_entrada,
        "tokens_salida": resumen.tokens_salida,
        "latencia_ms": latencia_ms,
    }
    order_items = None
    if outcome.order_id:
        order_items = _items(order_service.get_order(outcome.order_id).items)
    if outcome.kind == "error":  # el LLM no respondió: no dice nada sobre cómo interpreta el agente
        print(f"⚠️  {case['id']}: sin evaluar, falló el LLM")
        return fila
    ok = _passes(case, outcome, order_items)
    fila["resultado"] = "correcto" if ok else "incorrecto"
    print(f"{'✅' if ok else '❌'} {case['id']}: {outcome.kind}, {outcome.iterations} iteraciones")
    print(f"   tools: {', '.join(calls)}")
    print(f"   respuesta: {outcome.message}")
    if not ok:
        print(f"   esperado: {fila['esperado']}")
    return fila


def _registrar(mlflow, agent, cases: list[dict], caso: str | None, filas: list[dict]) -> None:
    """Parámetros para comparar corridas, métricas agregadas y una tabla con un caso por fila."""
    from internal.config import get_settings

    settings = get_settings()
    mlflow.log_params(
        {
            "proveedor": settings.llm_provider,
            "modelo": settings.llm_model,
            "prompt_version": agent.prompt_version,
            "casos": len(cases),
            "caso": caso or "todos",
        }
    )
    mlflow.log_metrics(_metricas(filas))
    if filas:
        mlflow.log_table({columna: [fila[columna] for fila in filas] for columna in filas[0]}, "casos.json")


if __name__ == "__main__":
    raise SystemExit(main())
