"""Corre los casos de data_example/mensajes.json con el LLM real: python -m internal.evaluar_agente

Es una evaluación manual, fuera del CI (spec del agente §10). Usa una base temporal: no toca los pedidos locales.
Solo manda datos inventados. Cada caso hace varias llamadas al LLM: en el free tier conviene usar --pausa.
Además del resultado, revisa cómo suena cada respuesta (chequeos de estilo, spec de skills y respuestas §3.6).
Con --conversaciones, escribe cada caso con su conversación completa para revisarla a mano.
Con --mlflow, la corrida queda en MLflow con sus métricas, una tabla por caso y la traza de cada turno.
"""

import argparse
import contextlib
import json
import math
import os
import re
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path

CASES = Path(__file__).resolve().parent.parent / "data_example" / "mensajes.json"
KIND_POR_RESULTADO = {"borrador": "propuesta"}

# Chequeos de estilo: cada uno mira una cosa que un cliente notaría (spec de skills y respuestas §3.6)
_SKU_GENERICO = re.compile(r"\b[A-Z]{2,}-[A-Z0-9]+-[A-Z0-9]+\b")
_PRESENTACION = re.compile(r"\bpresentaci[oó]n(es)?\b", re.IGNORECASE)
_STOCK = re.compile(
    r"\b\d+\s*(?:unidades\s*)?(?:disponibles?|en stock|en dep[oó]sito)\b"
    r"|\bstock\s*(?:de\s*)?:?\s*\d+"
    r"|\bquedan?\s+(?:solo\s+|solamente\s+)?\d+",
    re.IGNORECASE,
)
_INTERNOS = re.compile(
    r"\b(?:search_products|get_product|check_stock|explorar_catalogo|respond_to_customer|armar_pedido|leer_skill"
    r"|leer_br|tomar_pedido|confirmar_pedido|skills?|tools?|system prompt|BR-\d+)\b",
    re.IGNORECASE,
)
_INGLES = re.compile(r"\b(?:items?|sizes?|delivery|order|checkout|stockear)\b", re.IGNORECASE)
_PREGUNTA = re.compile(r"¿[^¿?]*\?|[^¿?.!\n]*\?")
RENGLONES_PEDIDO_LARGO = 3


def es_pedido_largo(texto_cliente: str) -> bool:
    """Una lista de tres renglones o más: ahí las dudas van juntas y vale más de una pregunta (BR-03)."""
    return sum(bool(linea.strip()) for linea in texto_cliente.splitlines()) >= RENGLONES_PEDIDO_LARGO


def chequear_estilo(texto: str, *, skus: Iterable[str] = (), kind: str = "", pedido_largo: bool = False) -> list[str]:
    """Devuelve los chequeos que no pasa una respuesta del agente. `texto` es solo lo que escribió el LLM."""
    fallas = []
    if _SKU_GENERICO.search(texto) or any(
        re.search(rf"(?<![\w-]){re.escape(sku)}(?![\w-])", texto, re.IGNORECASE) for sku in skus
    ):
        fallas.append("sin_sku")
    if "**" in texto:
        fallas.append("sin_negrita")
    if _PRESENTACION.search(texto):
        fallas.append("sin_presentacion")
    if not pedido_largo and len(re.findall(r"\?+", texto)) > 1:
        fallas.append("una_pregunta")
    if kind != "sin_stock" and _STOCK.search(texto):
        fallas.append("sin_cantidad_de_stock")
    if _INTERNOS.search(texto):
        fallas.append("sin_nombres_internos")
    if _INGLES.search(texto):
        fallas.append("sin_ingles")
    return fallas


def cierre(texto: str) -> str | None:
    """La última pregunta de la respuesta, normalizada, o None si no pregunta nada."""
    preguntas = _PREGUNTA.findall(texto)
    if not preguntas:
        return None
    return " ".join(re.sub(r"[¿?¡!.,:;]", " ", preguntas[-1]).lower().split())


def cierres_repetidos(respuestas: list[str]) -> bool:
    """True si dos respuestas de la misma conversación terminan con la misma pregunta."""
    cierres = [c for c in map(cierre, respuestas) if c]
    return len(cierres) != len(set(cierres))


def _texto_del_agente(outcome) -> str:
    """La respuesta sin el detalle de precios que agrega el sistema debajo: ese texto no lo escribe el LLM."""
    from internal.services import validation_service

    if outcome.draft is None:
        return outcome.message
    detalle = "\n\n" + validation_service.build_proposal_detail(outcome.draft)
    return outcome.message.removesuffix(detalle)


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
    estilo_ok = sum(fila.get("estilo") == "ok" for fila in evaluadas)
    latencias = [fila["latencia_ms"] for fila in evaluadas]
    return {
        "aciertos_pct": round(100 * correctas / len(evaluadas), 1) if evaluadas else 0.0,
        "estilo_ok_pct": round(100 * estilo_ok / len(evaluadas), 1) if evaluadas else 0.0,
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
    parser.add_argument("--conversaciones", type=Path, help="escribe las conversaciones completas en este .md")
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
    from internal.services import conversation_service, sheets_service

    conversation_service.startup()
    agent = NormaAgent(get_chat_model(), InMemorySaver())
    skus = [product.sku for product in sheets_service.get_catalog()]

    cases = [case for case in json.loads(CASES.read_text(encoding="utf-8")) if args.caso in (None, case["id"])]
    corrida: contextlib.AbstractContextManager = contextlib.nullcontext()
    if args.mlflow:
        mlflow = observabilidad.cliente()
        if mlflow is None:
            print("No se pudo conectar con MLflow: revisá MLFLOW_TRACKING_URI y que el servidor esté levantado")
            return 2
        settings = get_settings()
        corrida = mlflow.start_run(run_name=f"{settings.llm_provider}/{settings.llm_model} {agent.prompt_version}")

    filas, conversaciones = [], []
    with corrida:
        for number, case in enumerate(cases):
            if number and args.pausa:
                time.sleep(args.pausa)
            fila, conversacion = _evaluar(agent, case, skus)
            filas.append(fila)
            conversaciones.append(conversacion)
        if args.mlflow:
            _registrar(mlflow, agent, cases, args.caso, filas)
    if args.conversaciones:
        escribir_conversaciones(args.conversaciones, cases, filas, conversaciones, agent.prompt_version)
        print(f"\nConversaciones en {args.conversaciones}")

    passed = sum(fila["resultado"] == "correcto" for fila in filas)
    not_evaluated = sum(fila["resultado"] == "sin_evaluar" for fila in filas)
    estilo_ok = sum(fila["estilo"] == "ok" for fila in filas if fila["resultado"] != "sin_evaluar")
    evaluated = len(cases) - not_evaluated
    print(f"\n{passed} de {evaluated} casos correctos, {estilo_ok} de {evaluated} con buen estilo, {not_evaluated} sin evaluar")
    return 0 if passed == len(cases) and estilo_ok == len(cases) else 1


def _turnos(case: dict) -> list[str]:
    """Los mensajes del cliente en orden: `previo` (un texto o una lista) y después `mensaje`."""
    previo = case.get("previo") or []
    return [*([previo] if isinstance(previo, str) else previo), case["mensaje"]["text"]]


def _evaluar(agent, case: dict, skus: Iterable[str] = ()) -> tuple[dict, list[dict]]:
    """Manda cada turno del caso, evalúa el último y chequea el estilo de cada respuesta del agente."""
    from internal.agent import observabilidad
    from internal.schemas.order_schema import Channel
    from internal.services import order_service

    message = case["mensaje"]
    skus = list(skus)
    thread_id, conversaciones_cerradas = case["id"], 0
    conversacion: list[dict] = []
    for texto in _turnos(case):
        turn = {"thread_id": thread_id, "customer_id": message["customer_id"], "channel": Channel(message["channel"])}
        inicio = time.perf_counter()
        outcome = agent.handle(text=texto, **turn)
        latencia_ms = round((time.perf_counter() - inicio) * 1000)
        # La respuesta de una orden y la de una falla las escribe el sistema: no se chequean
        propia = outcome.kind not in ("pedido", "error")
        agente = _texto_del_agente(outcome) if propia else outcome.message
        estilo = (
            chequear_estilo(agente, skus=skus, kind=outcome.kind, pedido_largo=es_pedido_largo(texto)) if propia else []
        )
        conversacion.append(
            {"cliente": texto, "kind": outcome.kind, "respuesta": outcome.message, "agente": agente, "estilo": estilo}
        )
        ultimo_thread = thread_id
        if outcome.cerrada:  # como en el canal: el mensaje siguiente abre otra conversación
            conversaciones_cerradas += 1
            thread_id = f"{case['id']}#{conversaciones_cerradas + 1}"

    state = agent.graph.get_state({"configurable": {"thread_id": ultimo_thread}}).values
    calls = [call["name"] for m in state["messages"] for call in getattr(m, "tool_calls", [])]
    resumen = observabilidad.resumir(state)
    fallas_estilo = [f"turno {n}: {falla}" for n, t in enumerate(conversacion, 1) for falla in t["estilo"]]
    if cierres_repetidos([t["agente"] for t in conversacion if t["kind"] not in ("pedido", "error")]):
        fallas_estilo.append("cierre_variado")
    fila = {
        "caso": case["id"],
        "resultado": "sin_evaluar",
        "estilo": "; ".join(fallas_estilo) or "ok",
        "kind": outcome.kind,
        "esperado": json.dumps(case["esperado"], ensure_ascii=False),
        "turnos": len(conversacion),
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
    if any(t["kind"] == "error" for t in conversacion):  # el LLM no respondió: no dice nada sobre cómo interpreta
        print(f"⚠️  {case['id']}: sin evaluar, falló el LLM")
        return fila, conversacion
    ok = _passes(case, outcome, order_items)
    fila["resultado"] = "correcto" if ok else "incorrecto"
    print(f"{'✅' if ok else '❌'} {case['id']}: {outcome.kind}, {outcome.iterations} iteraciones, estilo {fila['estilo']}")
    print(f"   tools: {', '.join(calls)}")
    for turno in conversacion:
        print(f"   cliente: {turno['cliente']}")
        print(f"   norma ({turno['kind']}): {turno['respuesta']}")
    if not ok:
        print(f"   esperado: {fila['esperado']}")
    return fila, conversacion


def escribir_conversaciones(
    path: Path, cases: list[dict], filas: list[dict], conversaciones: list[list[dict]], prompt_version: str
) -> None:
    """Un .md para leer a mano: cada caso con su conversación, el kind de cada turno y lo que falló."""
    lineas = [f"# Conversaciones de la evaluación (prompt {prompt_version})", ""]
    for case, fila, conversacion in zip(cases, filas, conversaciones, strict=True):
        lineas += [f"## {case['id']}", "", case.get("descripcion", ""), ""]
        lineas.append(f"Resultado: **{fila['resultado']}** · estilo: {fila['estilo']} · esperado: `{fila['esperado']}`")
        lineas.append("")
        for turno in conversacion:
            lineas.append(f"> **Cliente:** {turno['cliente']}".replace("\n", "\n> "))
            lineas.append(">")
            respuesta = turno["respuesta"].replace("\n", "\n> ")
            lineas.append(f"> **Norma** (`{turno['kind']}`): {respuesta}")
            if turno["estilo"]:
                lineas.append(f"> ⚠️ estilo: {', '.join(turno['estilo'])}")
            lineas.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lineas), encoding="utf-8")


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
