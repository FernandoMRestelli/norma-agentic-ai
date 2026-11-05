"""Corre los casos de datos_ejemplo/mensajes.json con el LLM real: python -m internal.evaluar_agente

Es una evaluación manual, fuera del CI (spec del agente §10). Usa una base temporal: no toca los pedidos locales.
Solo manda datos inventados. Cada caso hace varias llamadas al LLM: en el free tier conviene usar --pausa.
"""

import argparse
import json
import os
import tempfile
import time
from pathlib import Path

CASES = Path(__file__).resolve().parent.parent / "datos_ejemplo" / "mensajes.json"
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pausa", type=float, default=0, help="segundos de espera entre casos")
    parser.add_argument("--caso", help="corre solo el caso con este id")
    args = parser.parse_args()

    # Antes de importar la app: la base se elige al importarla
    os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp(prefix='norma-eval-')) / 'norma.db'}"

    from langgraph.checkpoint.memory import InMemorySaver

    from internal.agent.llm import get_chat_model
    from internal.agent.norma_agent import NormaAgent
    from internal.schemas.order_schema import Channel
    from internal.services import conversation_service, order_service

    conversation_service.startup()
    agent = NormaAgent(get_chat_model(), InMemorySaver())

    cases = [case for case in json.loads(CASES.read_text(encoding="utf-8")) if args.caso in (None, case["id"])]
    passed = not_evaluated = 0
    for number, case in enumerate(cases):
        if number and args.pausa:
            time.sleep(args.pausa)
        message = case["mensaje"]
        turn = {"thread_id": case["id"], "customer_id": message["customer_id"], "channel": Channel(message["channel"])}
        if case.get("previo"):
            agent.handle(text=case["previo"], **turn)
        outcome = agent.handle(text=message["text"], **turn)

        state = agent.graph.get_state({"configurable": {"thread_id": case["id"]}}).values
        calls = [call["name"] for m in state["messages"] for call in getattr(m, "tool_calls", [])]
        order_items = None
        if outcome.order_id:
            order_items = _items(order_service.get_order(outcome.order_id).items)
        if outcome.kind == "error":  # el LLM no respondió: no dice nada sobre cómo interpreta el agente
            not_evaluated += 1
            print(f"⚠️  {case['id']}: sin evaluar, falló el LLM")
            continue
        ok = _passes(case, outcome, order_items)
        passed += ok
        print(f"{'✅' if ok else '❌'} {case['id']}: {outcome.kind}, {outcome.iterations} iteraciones")
        print(f"   tools: {', '.join(calls)}")
        if not ok:
            print(f"   esperado: {json.dumps(case['esperado'], ensure_ascii=False)}")
            print(f"   respuesta: {outcome.message}")
    print(f"\n{passed} de {len(cases) - not_evaluated} casos correctos, {not_evaluated} sin evaluar")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
