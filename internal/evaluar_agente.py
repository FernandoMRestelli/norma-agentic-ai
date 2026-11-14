"""Corre los casos de evaluación con el LLM real: python -m internal.evaluar_agente

Es una evaluación manual, fuera del CI (spec del agente §10). Usa una base, un catálogo y un archivo de pedidos
propios: no toca los pedidos locales ni las planillas, aunque estén configuradas en `.env`.
Solo manda datos inventados. Cada caso hace varias llamadas al LLM: en el free tier conviene usar --pausa.

Hay dos conjuntos de casos (--set):
- `mensajes` (por defecto): `data_example/mensajes.json` contra `data_example/catalogo.csv`, de 15 productos.
- `pedidos`: `data_example/pedidos_evaluacion.json` contra `data_example/catalogo_evaluacion.csv`, de unos 400.
  Son listas largas con el formato de los pedidos reales (RF-59); la corrida se compara con el umbral de RNF-15.

Además del resultado de cada caso, mide las líneas una por una (RF-60) y revisa cómo suena cada respuesta
(chequeos de estilo, spec de skills y respuestas §3.6).
Con --repeticiones N, corre cada caso N veces y muestra la tasa de acierto de cada uno.
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

DATOS = Path(__file__).resolve().parent.parent / "data_example"
# Cada conjunto corre contra su catálogo: los SKU esperados salen de ahí
CONJUNTOS = {
    "mensajes": (DATOS / "mensajes.json", DATOS / "catalogo.csv"),
    "pedidos": (DATOS / "pedidos_evaluacion.json", DATOS / "catalogo_evaluacion.csv"),
}
CASES = CONJUNTOS["mensajes"][0]
KIND_POR_RESULTADO = {"borrador": "propuesta"}

# Umbral de release sobre el conjunto `pedidos` (RNF-15). Son un punto de partida: se ajustan con la primera
# corrida completa (D-28, docs/spec/toma-de-pedidos-por-texto.md)
UMBRAL_MINIMOS = {"recall_lineas_pct": 95.0, "precision_lineas_pct": 98.0}
UMBRAL_MAXIMOS = {"registros_indebidos": 0, "lineas_perdidas": 0}

# Chequeos de estilo: cada uno mira una cosa que un cliente notaría (spec de skills y respuestas §3.6)
_SKU_GENERICO = re.compile(r"\b[A-Z]{2,}-[A-Z0-9]+-[A-Z0-9]+\b")
_PRESENTACION = re.compile(r"\bpresentaci[oó]n(es)?\b", re.IGNORECASE)
_STOCK = re.compile(
    r"\b\d+\s*(?:unidades\s*)?(?:disponibles?|en stock|en dep[oó]sito)\b"
    r"|\bstock\s*(?:disponible\s*)?(?:de\s*)?:?\s*\d+"
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
    """Unidades por SKU. Un SKU puede venir en varias líneas, una por nota (los tonos de una coloración)."""
    cantidades: dict[str, int] = {}
    for item in draft_or_order:
        cantidades[item.sku] = cantidades.get(item.sku, 0) + item.quantity
    return cantidades


def _esperados(items: list[dict]) -> dict[str, int]:
    cantidades: dict[str, int] = {}
    for item in items:
        cantidades[item["sku"]] = cantidades.get(item["sku"], 0) + item["cantidad"]
    return cantidades


def lineas_esperadas(esperado: dict) -> dict[str, int] | None:
    """Lo que tiene que quedar propuesto o registrado, por SKU. None si el caso no espera líneas.

    En `sin_stock`, `items` es lo que pidió el cliente, que no alcanza: lo que hay que proponer va en `propuesta`.
    """
    if esperado["resultado"] in ("borrador", "pedido"):
        return _esperados(esperado.get("items", []))
    if esperado["resultado"] == "sin_stock" and "propuesta" in esperado:
        return _esperados(esperado["propuesta"])
    return None


def medir_lineas(
    esperado: dict, obtenidos: dict[str, int], preguntados: Iterable[str] = (), avisados: Iterable[str] | None = None
) -> dict[str, int]:
    """Compara línea por línea lo esperado con lo que quedó propuesto o registrado (RF-60).

    Una línea es correcta si coinciden el SKU y la cantidad. Una línea esperada está perdida si no quedó en el
    pedido y el agente tampoco preguntó por ella; lo mismo una duda (`dudas`) de la que no ofreció ninguna opción.
    Con `avisados` (los renglones que el agente dejó afuera o pendientes, RF-68), también se pierde un renglón de
    `afuera` que no está entre ellos.
    """
    esperados = lineas_esperadas(esperado)
    if esperados is None:
        return {"lineas_esperadas": 0, "lineas_propuestas": 0, "lineas_correctas": 0, "lineas_perdidas": 0}
    a_la_vista = set(obtenidos) | set(preguntados)
    perdidas = sum(1 for sku in esperados if sku not in a_la_vista)
    perdidas += sum(1 for duda in esperado.get("dudas", []) if not a_la_vista & set(duda["opciones"]))
    if avisados is not None:
        perdidas += len(set(esperado.get("afuera", [])) - set(avisados))
    return {
        "lineas_esperadas": len(esperados),
        "lineas_propuestas": len(obtenidos),
        "lineas_correctas": sum(1 for sku, cantidad in obtenidos.items() if esperados.get(sku) == cantidad),
        "lineas_perdidas": perdidas,
    }


def _passes(case: dict, outcome, order_items: dict[str, int] | None) -> bool:
    expected = case["esperado"]
    wanted = expected["resultado"]
    items = _esperados(expected.get("items", []))
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


def _pct(parte: int, total: int) -> float:
    return round(100 * parte / total, 1) if total else 0.0


def _metricas(filas: list[dict]) -> dict[str, float]:
    evaluadas = [fila for fila in filas if fila["resultado"] != "sin_evaluar"]
    correctas = sum(fila["resultado"] == "correcto" for fila in evaluadas)
    estilo_ok = sum(fila.get("estilo") == "ok" for fila in evaluadas)
    latencias = [fila["latencia_ms"] for fila in evaluadas]

    def suma(columna: str) -> int:
        return sum(fila.get(columna, 0) for fila in evaluadas)

    llamadas = sum(fila.get("llamadas_llm", fila["iteraciones"]) for fila in evaluadas)
    return {
        "aciertos_pct": _pct(correctas, len(evaluadas)),
        "estilo_ok_pct": _pct(estilo_ok, len(evaluadas)),
        "correctos": correctas,
        "evaluados": len(evaluadas),
        "sin_evaluar": len(filas) - len(evaluadas),
        # Por línea y sobre todos los casos juntos: un pedido de 30 renglones pesa más que uno de 2 (RF-60)
        "recall_lineas_pct": _pct(suma("lineas_correctas"), suma("lineas_esperadas")),
        "precision_lineas_pct": _pct(suma("lineas_correctas"), suma("lineas_propuestas")),
        "lineas_perdidas": suma("lineas_perdidas"),
        "registros_indebidos": suma("registro_indebido"),
        "aclaraciones_de_mas": suma("aclaracion_de_mas"),
        "llamadas_llm_promedio": round(llamadas / len(evaluadas), 2) if evaluadas else 0,
        "iteraciones_promedio": round(sum(f["iteraciones"] for f in evaluadas) / len(evaluadas), 2) if evaluadas else 0,
        "tokens_entrada": sum(fila["tokens_entrada"] for fila in filas),
        "tokens_salida": sum(fila["tokens_salida"] for fila in filas),
        "latencia_p50_ms": _percentil(latencias, 50),
        "latencia_p95_ms": _percentil(latencias, 95),
    }


def fuera_de_umbral(metricas: dict[str, float]) -> list[str]:
    """Qué métricas no llegan al umbral de release (RNF-15). Una corrida con casos sin evaluar no cuenta."""
    fallas = [f"{nombre} {metricas[nombre]} < {minimo}" for nombre, minimo in UMBRAL_MINIMOS.items()
              if metricas[nombre] < minimo]
    fallas += [f"{nombre} {metricas[nombre]} > {maximo}" for nombre, maximo in UMBRAL_MAXIMOS.items()
               if metricas[nombre] > maximo]
    if metricas["sin_evaluar"]:
        fallas.append(f"sin_evaluar {metricas['sin_evaluar']} > 0")
    return fallas


def tasas_por_caso(filas: list[dict]) -> dict[str, tuple[int, int]]:
    """Por caso, cuántas repeticiones acertó y cuántas se evaluaron: separa un caso firme de uno con suerte."""
    tasas: dict[str, tuple[int, int]] = {}
    for fila in filas:
        if fila["resultado"] == "sin_evaluar":
            continue
        correctas, evaluadas = tasas.get(fila["caso"], (0, 0))
        tasas[fila["caso"]] = (correctas + (fila["resultado"] == "correcto"), evaluadas + 1)
    return tasas


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", dest="conjunto", choices=sorted(CONJUNTOS), default="mensajes", help="casos a correr")
    parser.add_argument("--repeticiones", type=int, default=1, help="veces que corre cada caso")
    parser.add_argument("--pausa", type=float, default=0, help="segundos de espera entre casos")
    parser.add_argument("--caso", help="corre solo el caso con este id")
    parser.add_argument("--conversaciones", type=Path, help="escribe las conversaciones completas en este .md")
    parser.add_argument("--mlflow", action="store_true", help="guarda la corrida en MLflow (ver MLFLOW_* en .env)")
    args = parser.parse_args()

    if args.repeticiones < 1:
        parser.error("--repeticiones tiene que ser 1 o más")
    archivo_casos, catalogo = CONJUNTOS[args.conjunto]
    # Antes de importar la app: la base se elige al importarla. Los pedidos de la evaluación van a una carpeta
    # temporal y el catálogo es el del conjunto, aunque `.env` apunte a las planillas o a otros archivos
    temporal = Path(tempfile.mkdtemp(prefix="norma-eval-"))
    os.environ["DATABASE_URL"] = f"sqlite:///{temporal / 'norma.db'}"
    os.environ["ORDERS_FILE"] = str(temporal / "pedidos.csv")
    os.environ["CATALOG_FILE"] = str(catalogo)
    os.environ["CATALOG_SHEET_ID"] = ""
    os.environ["ORDERS_SHEET_ID"] = ""
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

    cases = [case for case in json.loads(archivo_casos.read_text(encoding="utf-8")) if args.caso in (None, case["id"])]
    corridas = [(case, repeticion) for case in cases for repeticion in range(1, args.repeticiones + 1)]
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
        for number, (case, repeticion) in enumerate(corridas):
            if number and args.pausa:
                time.sleep(args.pausa)
            fila, conversacion = _evaluar(agent, case, skus, repeticion)
            filas.append(fila)
            conversaciones.append(conversacion)
        if args.mlflow:
            _registrar(mlflow, agent, cases, args.caso, filas, args.conjunto, args.repeticiones)
    if args.conversaciones:
        corridos = [case for case, _ in corridas]
        escribir_conversaciones(args.conversaciones, corridos, filas, conversaciones, agent.prompt_version)
        print(f"\nConversaciones en {args.conversaciones}")

    passed = sum(fila["resultado"] == "correcto" for fila in filas)
    not_evaluated = sum(fila["resultado"] == "sin_evaluar" for fila in filas)
    estilo_ok = sum(fila["estilo"] == "ok" for fila in filas if fila["resultado"] != "sin_evaluar")
    evaluated = len(filas) - not_evaluated
    print(f"\n{passed} de {evaluated} corridas correctas, {estilo_ok} con buen estilo, {not_evaluated} sin evaluar")
    metricas = _metricas(filas)
    print(
        f"Líneas: recall {metricas['recall_lineas_pct']} %, precisión {metricas['precision_lineas_pct']} %, "
        f"{metricas['lineas_perdidas']} perdidas, {metricas['registros_indebidos']} registros indebidos, "
        f"{metricas['aclaraciones_de_mas']} aclaraciones de más, "
        f"{metricas['llamadas_llm_promedio']} llamadas al LLM por caso"
    )
    if args.repeticiones > 1:
        for caso, (correctas, evaluadas) in tasas_por_caso(filas).items():
            print(f"   {caso}: {correctas}/{evaluadas}")
    if args.conjunto == "pedidos":
        fallas = fuera_de_umbral(metricas)
        print("RNF-15: cumple el umbral de release" if not fallas else f"RNF-15: no cumple ({'; '.join(fallas)})")
        return 1 if fallas else 0
    return 0 if passed == len(filas) and estilo_ok == len(filas) else 1


def _turnos(case: dict) -> list[str]:
    """Los mensajes del cliente en orden: `previo` (un texto o una lista) y después `mensaje`."""
    previo = case.get("previo") or []
    return [*([previo] if isinstance(previo, str) else previo), case["mensaje"]["text"]]


def _evaluar(agent, case: dict, skus: Iterable[str] = (), repeticion: int = 1) -> tuple[dict, list[dict]]:
    """Manda cada turno del caso, evalúa el último y chequea el estilo de cada respuesta del agente.

    Al terminar cancela las órdenes que creó el caso: liberan su stock y los casos no dependen del orden ni de
    cuántas veces se repiten.
    """
    from internal.agent import observabilidad
    from internal.schemas.order_schema import Channel
    from internal.services import order_service

    message = case["mensaje"]
    skus = list(skus)
    corrida = case["id"] if repeticion == 1 else f"{case['id']}@{repeticion}"
    thread_id, conversaciones_cerradas = corrida, 0
    conversacion: list[dict] = []
    llamadas_llm, ordenes = 0, []
    for texto in _turnos(case):
        turn = {"thread_id": thread_id, "customer_id": message["customer_id"], "channel": Channel(message["channel"])}
        inicio = time.perf_counter()
        outcome = agent.handle(text=texto, **turn)
        latencia_ms = round((time.perf_counter() - inicio) * 1000)
        llamadas_llm += outcome.iterations
        if outcome.order_id:
            ordenes.append(outcome.order_id)
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
            thread_id = f"{corrida}#{conversaciones_cerradas + 1}"

    state = agent.graph.get_state({"configurable": {"thread_id": ultimo_thread}}).values
    calls = [call["name"] for m in state["messages"] for call in getattr(m, "tool_calls", [])]
    resumen = observabilidad.resumir(state)
    fallas_estilo = [f"turno {n}: {falla}" for n, t in enumerate(conversacion, 1) for falla in t["estilo"]]
    if cierres_repetidos([t["agente"] for t in conversacion if t["kind"] not in ("pedido", "error")]):
        fallas_estilo.append("cierre_variado")
    fila = {
        "caso": case["id"],
        "repeticion": repeticion,
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
        "llamadas_llm": llamadas_llm,
    }
    order_items = None
    if outcome.order_id:
        order_items = _items(order_service.get_order(outcome.order_id).items)
    for order_id in ordenes:
        with contextlib.suppress(ValueError):  # ya cancelada o inexistente: no hay stock que liberar
            order_service.cancel_order(order_id, source=order_service.EventSource.AGENT)
    if any(t["kind"] == "error" for t in conversacion):  # el LLM no respondió: no dice nada sobre cómo interpreta
        print(f"⚠️  {corrida}: sin evaluar, falló el LLM")
        return fila, conversacion
    esperado = case["esperado"]
    obtenidos = order_items if order_items is not None else _items(outcome.draft.items if outcome.draft else [])
    # Los renglones que el agente declaró afuera o dejó pendientes, si el pedido vino numerado (RF-68)
    renglones = state.get("renglones") or []
    avisados = [r["texto"] for r in renglones if r["estado"] in ("afuera", "pendiente")] if renglones else None
    fila.update(medir_lineas(esperado, obtenidos, outcome.options, avisados))
    fila["registro_indebido"] = int(esperado["resultado"] == "sin_pedido" and order_items is not None)
    fila["aclaracion_de_mas"] = int(
        esperado["resultado"] in ("borrador", "pedido") and outcome.kind == "aclaracion" and not esperado.get("dudas")
    )
    ok = _passes(case, outcome, order_items)
    fila["resultado"] = "correcto" if ok else "incorrecto"
    marca = "✅" if ok else "❌"
    lineas = f", líneas {fila['lineas_correctas']}/{fila['lineas_esperadas']}" if fila["lineas_esperadas"] else ""
    print(f"{marca} {corrida}: {outcome.kind}, {outcome.iterations} iteraciones{lineas}, estilo {fila['estilo']}")
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
        repeticion = f" (repetición {fila['repeticion']})" if fila.get("repeticion", 1) > 1 else ""
        lineas += [f"## {case['id']}{repeticion}", "", case.get("descripcion", ""), ""]
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


def _registrar(
    mlflow, agent, cases: list[dict], caso: str | None, filas: list[dict], conjunto: str = "mensajes",
    repeticiones: int = 1,
) -> None:
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
            "conjunto": conjunto,
            "repeticiones": repeticiones,
        }
    )
    mlflow.log_metrics(_metricas(filas))
    if filas:
        mlflow.log_table({columna: [fila[columna] for fila in filas] for columna in filas[0]}, "casos.json")


if __name__ == "__main__":
    raise SystemExit(main())
