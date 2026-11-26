"""Corre los casos de evaluación con el LLM real: python -m internal.evaluar_agente

Es una evaluación manual, fuera del CI (spec del agente §10). Usa una base, un catálogo y un archivo de pedidos
propios: no toca los pedidos locales ni las planillas, aunque estén configuradas en `.env`.
Solo manda datos inventados. Cada caso hace varias llamadas al LLM: en el free tier conviene usar --pausa.

Hay tres conjuntos de casos (--set):
- `mensajes` (por defecto): `data_example/mensajes.json` contra `data_example/catalogo.csv`, de 15 productos.
- `pedidos`: `data_example/pedidos_evaluacion.json` contra `data_example/catalogo_evaluacion.csv`, de unos 400.
  Son listas largas con el formato de los pedidos reales (RF-59); lo reservado se compara con el umbral de RNF-15.
- `adversariales`: `data_example/adversariales.json`, contra el mismo catálogo. Aceptaciones con trampa, falta de
  evidencia, inyección y datos del negocio (RF-116).

Los tres conjuntos tienen casos de `desarrollo` y `reservado`, y --grupo elige uno o `todos`: lo reservado se corre
para medir y no se ajusta nada mirando sus casos (D-50, M8-1). Con los dos grupos, la corrida los muestra lado a lado:
la diferencia es la medida del sobreajuste.

Además del resultado de cada caso, mide las líneas una por una (RF-60), con un intervalo que remuestrea casos y no
líneas, y revisa cómo suena cada respuesta (chequeos de estilo, spec de skills y respuestas §3.6).
Con --repeticiones N, corre cada caso N veces y muestra la tasa de acierto de cada uno.
Con --conversaciones, escribe cada caso con su conversación completa para revisarla a mano.
Con --max-llamadas N se detiene al llegar a N llamadas al LLM, y con --precio ENTRADA,SALIDA (USD por millón de tokens)
estima el costo (RF-117). Con --salida guarda las filas en un .json después de cada caso.
Con --mlflow, la corrida queda en MLflow con sus métricas, una tabla por caso y la traza de cada turno.
"""

import argparse
import contextlib
import json
import math
import os
import random
import re
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path

DATOS = Path(__file__).resolve().parent.parent / "data_example"
# Cada conjunto corre contra su catálogo: los SKU esperados salen de ahí
CONJUNTOS = {
    "mensajes": (DATOS / "mensajes.json", DATOS / "catalogo.csv"),
    "pedidos": (DATOS / "pedidos_evaluacion.json", DATOS / "catalogo_evaluacion.csv"),
    "adversariales": (DATOS / "adversariales.json", DATOS / "catalogo_evaluacion.csv"),
}
GRUPOS = ("desarrollo", "reservado")  # un caso sin grupo es de desarrollo (RF-116, D-50)
# Casos seguidos sin respuesta del LLM: con la cuota agotada, seguir no mide nada (RF-117)
CORTAR_TRAS = 3
CASES = CONJUNTOS["mensajes"][0]
KIND_POR_RESULTADO = {"borrador": "propuesta"}

# Umbral de release sobre los casos reservados del conjunto `pedidos` (RNF-15, M8-1). Son un punto de partida: se
# ajustan con la primera corrida completa (D-28, docs/spec/toma-de-pedidos-por-texto.md)
UMBRAL_MINIMOS = {"recall_lineas_pct": 95.0, "precision_lineas_pct": 98.0}
UMBRAL_MAXIMOS = {"registros_indebidos": 0, "lineas_perdidas": 0}
GRUPO_DEL_UMBRAL = "reservado"
# Remuestreos del intervalo por casos (bootstrap), con semilla fija: la misma corrida da siempre el mismo intervalo
REMUESTREOS = 2000

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
    from internal.agent.guardrails import NUMERO_DE_RENGLON  # los imports de internal van después de fijar el entorno

    if NUMERO_DE_RENGLON.search(texto):
        fallas.append("sin_numero_de_renglon")
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


def dichos(esperado: dict, texto: str) -> list[str]:
    """Los patrones de `no_dice` que aparecen en lo que escribió el agente (RF-115)."""
    return [patron for patron in esperado.get("no_dice", []) if re.search(patron, texto, re.IGNORECASE)]


def faltantes(esperado: dict, texto: str) -> list[str]:
    """Los patrones de `dice` que no aparecen: sin ellos, una respuesta genérica pasaría el caso (RF-115)."""
    return [patron for patron in esperado.get("dice", []) if not re.search(patron, texto, re.IGNORECASE)]


def _passes(case: dict, outcome, order_items: dict[str, int] | None, texto: str = "") -> bool:
    expected = case["esperado"]
    # No registrar no alcanza: también importa qué respondió y en qué quedó la propuesta (RF-115)
    if "kinds" in expected and outcome.kind not in expected["kinds"]:
        return False
    if "propuesta_final" in expected:
        final = _items(outcome.draft.items) if outcome.draft else {}
        if final != _esperados(expected["propuesta_final"]):
            return False
    if dichos(expected, texto) or faltantes(expected, texto):
        return False
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
        return "opciones" not in expected or set(outcome.options) == set(expected["opciones"])
    return True


def _percentil(valores: list[int], p: float) -> int:
    """Percentil por rango más cercano: con pocos casos alcanza y no interpola."""
    if not valores:
        return 0
    ordenados = sorted(valores)
    return ordenados[max(math.ceil(p / 100 * len(ordenados)) - 1, 0)]


def intervalo(aciertos: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalo de Wilson al 95 % de un porcentaje: con pocos casos, la diferencia entre dos corridas puede ser azar.

    Trata cada corrida como independiente; las repeticiones de un mismo caso no lo son del todo, así que es un piso.
    """
    if not total:
        return 0.0, 0.0
    p = aciertos / total
    centro, margen = p + z * z / (2 * total), z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    divisor = 1 + z * z / total
    return round(100 * (centro - margen) / divisor, 1), round(100 * (centro + margen) / divisor, 1)


def intervalo_por_casos(
    filas: list[dict], parte: str, total: str, *, remuestreos: int = REMUESTREOS, semilla: int = 0
) -> tuple[float, float]:
    """Intervalo al 95 % de un porcentaje por línea, remuestreando casos enteros (bootstrap, M8-1).

    Las líneas de un caso no son independientes: si el modelo entiende mal el encabezado de una marca, pierde todas
    sus líneas juntas. Por eso se sortean casos, con todas sus líneas y sus repeticiones, y no líneas sueltas.
    """
    por_caso: dict[str, list[int]] = {}
    for fila in filas:
        if fila["resultado"] == "sin_evaluar" or not fila.get(total):
            continue
        acumulado = por_caso.setdefault(fila["caso"], [0, 0])
        acumulado[0] += fila.get(parte, 0)
        acumulado[1] += fila[total]
    casos = list(por_caso.values())
    if not casos:
        return 0.0, 0.0
    rng = random.Random(semilla)
    porcentajes = []
    for _ in range(remuestreos):
        muestra = rng.choices(casos, k=len(casos))
        porcentajes.append(100 * sum(p for p, _ in muestra) / sum(t for _, t in muestra))
    porcentajes.sort()
    bajo = porcentajes[int(0.025 * remuestreos)]
    alto = porcentajes[max(math.ceil(0.975 * remuestreos) - 1, 0)]
    return round(bajo, 1), round(alto, 1)


def _pct(parte: int, total: int) -> float:
    return round(100 * parte / total, 1) if total else 0.0


def _por_llamada(tokens: int, llamadas: int) -> float:
    return round(tokens / llamadas, 1) if llamadas else 0.0


def _metricas(filas: list[dict]) -> dict[str, float]:
    evaluadas = [fila for fila in filas if fila["resultado"] != "sin_evaluar"]
    correctas = sum(fila["resultado"] == "correcto" for fila in evaluadas)
    estilo_ok = sum(fila.get("estilo") == "ok" for fila in evaluadas)
    latencias = [fila["latencia_ms"] for fila in evaluadas]

    def suma(columna: str) -> int:
        return sum(fila.get(columna, 0) for fila in evaluadas)

    llamadas = sum(fila.get("llamadas_llm", fila["iteraciones"]) for fila in evaluadas)
    bajo, alto = intervalo(correctas, len(evaluadas))
    recall_bajo, recall_alto = intervalo_por_casos(filas, "lineas_correctas", "lineas_esperadas")
    precision_bajo, precision_alto = intervalo_por_casos(filas, "lineas_correctas", "lineas_propuestas")
    tasas = tasas_por_caso(filas)
    return {
        "aciertos_ic95_bajo": bajo,
        "aciertos_ic95_alto": alto,
        # Casos que acertaron todas sus repeticiones: un cliente ve una sola, y no puede tocarle la mala
        "casos_firmes_pct": _pct(sum(ok == n for ok, n in tasas.values()), len(tasas)),
        "llamadas_llm_total": sum(fila.get("llamadas_llm", 0) for fila in filas),
        "tokens_entrada_total": sum(fila.get("tokens_entrada_total", 0) for fila in filas),
        "tokens_salida_total": sum(fila.get("tokens_salida_total", 0) for fila in filas),
        "aciertos_pct": _pct(correctas, len(evaluadas)),
        "estilo_ok_pct": _pct(estilo_ok, len(evaluadas)),
        "correctos": correctas,
        "evaluados": len(evaluadas),
        "sin_evaluar": len(filas) - len(evaluadas),
        # Por línea y sobre todos los casos juntos: un pedido de 30 renglones pesa más que uno de 2 (RF-60)
        "recall_lineas_pct": _pct(suma("lineas_correctas"), suma("lineas_esperadas")),
        "precision_lineas_pct": _pct(suma("lineas_correctas"), suma("lineas_propuestas")),
        "recall_lineas_ic95_bajo": recall_bajo,
        "recall_lineas_ic95_alto": recall_alto,
        "precision_lineas_ic95_bajo": precision_bajo,
        "precision_lineas_ic95_alto": precision_alto,
        "lineas_perdidas": suma("lineas_perdidas"),
        "registros_indebidos": suma("registro_indebido"),
        "aclaraciones_de_mas": suma("aclaracion_de_mas"),
        "llamadas_llm_promedio": round(llamadas / len(evaluadas), 2) if evaluadas else 0,
        "iteraciones_promedio": round(sum(f["iteraciones"] for f in evaluadas) / len(evaluadas), 2) if evaluadas else 0,
        "tokens_entrada": sum(fila["tokens_entrada"] for fila in filas),
        "tokens_salida": sum(fila["tokens_salida"] for fila in filas),
        # Separados por loop (RF-94, RNF-17): las llamadas del orquestador son las del último turno, como sus tokens
        "llamadas_sub_loop_promedio": round(suma("llamadas_sub_loop") / len(evaluadas), 2) if evaluadas else 0,
        "tokens_entrada_por_llamada": _por_llamada(
            suma("tokens_entrada"), sum(f["iteraciones"] for f in evaluadas)
        ),
        "tokens_entrada_por_llamada_sub_loop": _por_llamada(
            suma("tokens_entrada_sub_loop"), suma("llamadas_sub_loop_ultimo_turno")
        ),
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


def umbral_sin_decidir(metricas: dict[str, float]) -> list[str]:
    """Las métricas cuyo intervalo por casos incluye el umbral: con pocos casos, la corrida no alcanza para decidir.

    Con 11 casos, un 95 % de recall puede no distinguirse de un 90 % (M8-1): la corrida tiene que decirlo.
    """
    return [
        f"{nombre} {metricas[nombre]} (IC 95 %: {bajo} a {alto}) incluye {minimo}"
        for nombre, minimo in UMBRAL_MINIMOS.items()
        if (bajo := metricas[nombre.replace("_pct", "_ic95_bajo")]) < minimo
        <= (alto := metricas[nombre.replace("_pct", "_ic95_alto")])
    ]


def del_grupo_de(filas: list[dict], grupo: str) -> list[dict]:
    """Las filas de un grupo; las que no dicen el suyo son de desarrollo."""
    return [fila for fila in filas if fila.get("grupo", GRUPOS[0]) == grupo]


def metricas_por_grupo(filas: list[dict]) -> dict[str, dict[str, float]]:
    """Las métricas de cada grupo que tiene filas: comparar los dos mide el sobreajuste a lo de desarrollo (M8-1)."""
    return {grupo: _metricas(propias) for grupo in GRUPOS if (propias := del_grupo_de(filas, grupo))}


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
    parser.add_argument("--grupo", choices=(*GRUPOS, "todos"), default="todos", help="casos de desarrollo o reservados")
    parser.add_argument("--max-llamadas", type=int, help="tope de llamadas al LLM de toda la corrida")
    parser.add_argument("--precio", help="USD por millón de tokens de entrada y de salida, por ejemplo 0.10,0.50")
    parser.add_argument("--salida", type=Path, help="guarda las filas y las métricas en este .json")
    parser.add_argument("--orquestador", choices=("si", "no"), help="pisa AGENT_ORQUESTADOR para comparar (D-36)")
    parser.add_argument("--caso", help="corre solo el caso con este id")
    parser.add_argument("--conversaciones", type=Path, help="escribe las conversaciones completas en este .md")
    parser.add_argument("--mlflow", action="store_true", help="guarda la corrida en MLflow (ver MLFLOW_* en .env)")
    args = parser.parse_args()

    if args.repeticiones < 1:
        parser.error("--repeticiones tiene que ser 1 o más")
    precio = leer_precio(args.precio) if args.precio else None
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
    if args.orquestador:
        os.environ["AGENT_ORQUESTADOR"] = "true" if args.orquestador == "si" else "false"

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
    cases = del_grupo(cases, args.grupo)
    corridas = [(case, repeticion) for case in cases for repeticion in range(1, args.repeticiones + 1)]
    corrida: contextlib.AbstractContextManager = contextlib.nullcontext()
    if args.mlflow:
        mlflow = observabilidad.cliente()
        if mlflow is None:
            print("No se pudo conectar con MLflow: revisá MLFLOW_TRACKING_URI y que el servidor esté levantado")
            return 2
        settings = get_settings()
        corrida = mlflow.start_run(run_name=f"{settings.llm_provider}/{settings.llm_model} {agent.prompt_version}")

    settings = get_settings()
    contexto = {
        "conjunto": args.conjunto, "grupo": args.grupo, "repeticiones": args.repeticiones,
        "proveedor": settings.llm_provider, "modelo": settings.llm_model, "prompt_version": agent.prompt_version,
        "orquestador": settings.agent_orquestador, "max_iteraciones": settings.agent_max_iterations,
    }  # fmt: skip

    def guardar(filas: list[dict], conversaciones: list[list[dict]]) -> None:
        if args.salida:
            escribir_salida(args.salida, contexto, filas, conversaciones, precio)

    with corrida:
        filas, conversaciones = correr(
            agent, corridas, skus, pausa=args.pausa, max_llamadas=args.max_llamadas, al_terminar_caso=guardar
        )
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
        f"Líneas: recall {metricas['recall_lineas_pct']} % (IC 95 % por casos: {metricas['recall_lineas_ic95_bajo']} "
        f"a {metricas['recall_lineas_ic95_alto']}), precisión {metricas['precision_lineas_pct']} %, "
        f"{metricas['lineas_perdidas']} perdidas, {metricas['registros_indebidos']} registros indebidos, "
        f"{metricas['aclaraciones_de_mas']} aclaraciones de más, "
        f"{metricas['llamadas_llm_promedio']} llamadas al LLM por caso"
    )
    print(
        f"Aciertos: {metricas['aciertos_pct']} % (IC 95 %: {metricas['aciertos_ic95_bajo']} a "
        f"{metricas['aciertos_ic95_alto']}), {metricas['casos_firmes_pct']} % de los casos siempre bien"
    )
    costo = f", US$ {costo_usd(metricas, precio):.4f}" if precio else ""
    print(
        f"Uso: {metricas['llamadas_llm_total']} llamadas, {metricas['tokens_entrada_total']} tokens de entrada y "
        f"{metricas['tokens_salida_total']} de salida{costo}"
    )
    if codigos := codigos_de_error(filas):
        print("Códigos de error: " + ", ".join(f"{codigo} {n}" for codigo, n in codigos.items()))
    if metricas["llamadas_sub_loop_promedio"]:  # con AGENT_ORQUESTADOR (RF-94)
        print(
            f"Sub-loops: {metricas['llamadas_sub_loop_promedio']} llamadas por caso; tokens de entrada por llamada "
            f"{metricas['tokens_entrada_por_llamada_sub_loop']} en el sub-loop y "
            f"{metricas['tokens_entrada_por_llamada']} en el orquestador"
        )
    if args.repeticiones > 1:
        for caso, (correctas, evaluadas) in tasas_por_caso(filas).items():
            print(f"   {caso}: {correctas}/{evaluadas}")
    por_grupo = metricas_por_grupo(filas)
    if len(por_grupo) > 1:
        print(lado_a_lado(por_grupo))
    if args.conjunto == "pedidos":
        if GRUPO_DEL_UMBRAL not in por_grupo:
            print(f"RNF-15: se mide solo con los casos de {GRUPO_DEL_UMBRAL}, y esta corrida no tiene ninguno")
        else:
            del_umbral = por_grupo[GRUPO_DEL_UMBRAL]
            fallas = fuera_de_umbral(del_umbral)
            veredicto = "cumple el umbral de release" if not fallas else f"no cumple ({'; '.join(fallas)})"
            print(f"RNF-15, sobre {GRUPO_DEL_UMBRAL}: {veredicto}")
            if dudosas := umbral_sin_decidir(del_umbral):
                print(f"   Con {len(tasas_por_caso(del_grupo_de(filas, GRUPO_DEL_UMBRAL)))} casos no alcanza para "
                      f"decidir: {'; '.join(dudosas)}")  # fmt: skip
            return 1 if fallas else 0
    return 0 if passed == len(filas) and estilo_ok == len(filas) else 1


def lado_a_lado(por_grupo: dict[str, dict[str, float]]) -> str:
    """Desarrollo y reservado en una tabla: la diferencia entre los dos es la medida del sobreajuste (M8-1)."""
    filas = [("aciertos_pct", "Aciertos %"), ("recall_lineas_pct", "Recall por línea %"),
             ("precision_lineas_pct", "Precisión por línea %"), ("estilo_ok_pct", "Estilo ok %")]  # fmt: skip
    desarrollo, reservado = por_grupo["desarrollo"], por_grupo["reservado"]
    lineas = [f"{'Por grupo':<24}{'desarrollo':>12}{'reservado':>12}{'diferencia':>12}"]
    for clave, nombre in filas:
        diferencia = round(desarrollo[clave] - reservado[clave], 1)
        lineas.append(f"{nombre:<24}{desarrollo[clave]:>12}{reservado[clave]:>12}{diferencia:>+12}")
    lineas.append(f"{'Corridas evaluadas':<24}{desarrollo['evaluados']:>12}{reservado['evaluados']:>12}")
    return "\n".join(lineas)


def del_grupo(cases: list[dict], grupo: str) -> list[dict]:
    """Los casos de un grupo; los que no dicen el suyo son de desarrollo (RF-116)."""
    return [case for case in cases if grupo in ("todos", case.get("grupo", GRUPOS[0]))]


def leer_precio(texto: str) -> tuple[float, float]:
    entrada, salida = (float(parte) for parte in texto.split(","))
    return entrada, salida


def costo_usd(metricas: dict[str, float], precio: tuple[float, float]) -> float:
    """Costo estimado sin caché de prompt: con caché, el real es menor."""
    entrada, salida = precio
    return (metricas["tokens_entrada_total"] * entrada + metricas["tokens_salida_total"] * salida) / 1e6


def codigos_de_error(filas: list[dict]) -> dict[str, int]:
    """Cuántas veces apareció cada código de error de tool, del más frecuente al menos."""
    cuenta = Counter(codigo for fila in filas for codigo in fila.get("errores", "").split(",") if codigo)
    return dict(cuenta.most_common())


def _sin_correr(case: dict, repeticion: int) -> dict:
    """La fila de un caso que no llegó a correr: cuenta como sin evaluar (RF-117)."""
    return {
        "caso": case["id"], "repeticion": repeticion, "resultado": "sin_evaluar", "estilo": "ok", "kind": "",
        "esperado": json.dumps(case["esperado"], ensure_ascii=False), "turnos": 0, "iteraciones": 0, "tools": "",
        "skills": "", "tokens_entrada": 0, "tokens_salida": 0, "latencia_ms": 0, "llamadas_llm": 0, "errores": "",
        "grupo": case.get("grupo", GRUPOS[0]),
    }  # fmt: skip


def correr(
    agent, corridas: list[tuple[dict, int]], skus: list[str], *, pausa: float = 0, max_llamadas: int | None = None,
    al_terminar_caso: Callable[[list[dict], list[list[dict]]], None] = lambda *_: None,
) -> tuple[list[dict], list[list[dict]]]:  # fmt: skip
    """Corre los casos en orden hasta el tope de llamadas o hasta que el LLM deje de responder (RF-117).

    El tope se controla entre un caso y el siguiente: el último puede pasarlo por las llamadas de ese caso.
    """
    filas: list[dict] = []
    conversaciones: list[list[dict]] = []
    llamadas, sin_respuesta, motivo = 0, 0, None
    for numero, (case, repeticion) in enumerate(corridas):
        if max_llamadas is not None and llamadas >= max_llamadas:
            motivo = f"tope de {max_llamadas} llamadas"
        elif sin_respuesta >= CORTAR_TRAS:
            motivo = f"{sin_respuesta} casos seguidos sin respuesta del LLM"
        if motivo:
            print(f"\n⚠️  Corte: {motivo}; quedan {len(corridas) - numero} sin correr")
            for resto, n in corridas[numero:]:
                filas.append(_sin_correr(resto, n))
                conversaciones.append([])
            al_terminar_caso(filas, conversaciones)
            break
        if numero and pausa:
            time.sleep(pausa)
        fila, conversacion = _evaluar(agent, case, skus, repeticion)
        filas.append(fila)
        conversaciones.append(conversacion)
        llamadas += fila["llamadas_llm"]
        sin_respuesta = sin_respuesta + 1 if fila["resultado"] == "sin_evaluar" else 0
        al_terminar_caso(filas, conversaciones)
    return filas, conversaciones


def escribir_salida(
    path: Path, contexto: dict, filas: list[dict], conversaciones: list[list[dict]],
    precio: tuple[float, float] | None = None,
) -> None:  # fmt: skip
    """Las filas, sus conversaciones y las métricas en un .json. Se reescribe entero: si se corta, queda lo corrido."""
    metricas = _metricas(filas)
    datos = {
        **contexto, "metricas": metricas, "metricas_por_grupo": metricas_por_grupo(filas),
        "codigos_de_error": codigos_de_error(filas),
    }  # fmt: skip
    if precio:
        datos["costo_usd"] = round(costo_usd(metricas, precio), 5)
    datos["filas"] = [
        {**fila, "conversacion": conversacion} for fila, conversacion in zip(filas, conversaciones, strict=True)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")


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
    llamadas_llm, llamadas_sub_loop, ordenes = 0, 0, []
    tokens_entrada, tokens_salida, errores = 0, 0, []
    for texto in _turnos(case):
        turn = {"thread_id": thread_id, "customer_id": message["customer_id"], "channel": Channel(message["channel"])}
        inicio = time.perf_counter()
        outcome = agent.handle(text=texto, **turn)
        latencia_ms = round((time.perf_counter() - inicio) * 1000)
        llamadas_llm += outcome.iterations + outcome.llamadas_sub_loop
        llamadas_sub_loop += outcome.llamadas_sub_loop
        turno = observabilidad.resumir(agent.graph.get_state({"configurable": {"thread_id": thread_id}}).values)
        tokens_entrada += turno.tokens_entrada + turno.tokens_entrada_sub_loop
        tokens_salida += turno.tokens_salida + turno.tokens_salida_sub_loop
        errores += turno.errores
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
        "llamadas_llm": llamadas_llm,  # orquestador y sub-loops, de todos los turnos del caso
        # Con el orquestador (RF-94). Los tokens son del último turno, como `tokens_entrada`
        "llamadas_sub_loop": llamadas_sub_loop,
        "llamadas_sub_loop_ultimo_turno": resumen.llamadas_sub_loop,
        "tokens_entrada_sub_loop": resumen.tokens_entrada_sub_loop,
        "tokens_salida_sub_loop": resumen.tokens_salida_sub_loop,
        # De todos los turnos del caso, con los sub-loops: lo que se paga (RF-117)
        "tokens_entrada_total": tokens_entrada,
        "tokens_salida_total": tokens_salida,
        "errores": ",".join(errores),
        "grupo": case.get("grupo", GRUPOS[0]),
        "tema": case.get("tema", ""),
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
    ultimo = conversacion[-1]["agente"]
    fila["dijo"] = "; ".join(dichos(esperado, ultimo))
    fila["no_dijo"] = "; ".join(faltantes(esperado, ultimo))
    ok = _passes(case, outcome, order_items, ultimo)
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
    for grupo, metricas in metricas_por_grupo(filas).items():  # `reservado_recall_lineas_pct`, etc. (M8-1)
        mlflow.log_metrics({f"{grupo}_{nombre}": valor for nombre, valor in metricas.items()})
    if filas:
        # Un caso sin evaluar no trae las columnas de líneas: la tabla las deja vacías
        columnas = list(dict.fromkeys(columna for fila in filas for columna in fila))
        mlflow.log_table({columna: [fila.get(columna) for fila in filas] for columna in columnas}, "casos.json")


if __name__ == "__main__":
    raise SystemExit(main())
