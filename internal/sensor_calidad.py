"""Sensor de calidad en uso: python -m internal.sensor_calidad --dias 7 (RF-123)

Un sensor no impide nada: mide lo que ya pasó y avisa. Lee las trazas de MLflow (cada turno) y la base interna
(propuestas y pedidos), solo con números y códigos: nunca el texto de un mensaje ni un cliente (RNF-03).

Sale con 1 si algo pasó un umbral, 0 si está todo bien y 2 si no pudo leer las trazas. No escribe en ningún lado.
"""

import argparse
import math
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from internal.config import get_settings
from internal.database.connection import SessionLocal
from internal.database.models import DraftRecord, OrderRecord
from internal.schemas.order_schema import DraftStatus, OrderStatus

MIN_TURNOS = 20  # con menos, un porcentaje salta por un solo turno: se informa sin avisar
# Punto de partida, para ajustar con uso real (D-52). Lo que pasa de acá es un aviso
UMBRALES = {
    "error_pct": 5.0,
    "mensaje_fijo_pct": 2.0,
    "cierre_pct": 2.0,
    "tolerado_pct": 5.0,
    "derivada_pct": 15.0,
    "aceptacion_no_clara_pct": 5.0,
    "latencia_p95_ms": 30_000,
    "correcciones_por_pedido": 1.0,
    "cancelados_pct": 10.0,
}


def _percentil(valores: Iterable[float], p: float) -> float:
    ordenados = sorted(valores)
    return ordenados[max(math.ceil(p / 100 * len(ordenados)) - 1, 0)] if ordenados else 0


def _pct(parte: int, total: int) -> float:
    return round(100 * parte / total, 1) if total else 0.0


def medir_turnos(turnos: list[dict[str, Any]], precio: tuple[float, float] | None = None) -> dict[str, Any]:
    """Los números de los turnos. Cada uno trae los atributos que `observabilidad` deja en su traza."""
    total = len(turnos)
    kinds = Counter(turno.get("kind") for turno in turnos)
    rescates = [turno.get("rescates") or [] for turno in turnos]
    errores = Counter(codigo for turno in turnos for codigo in turno.get("errores") or [])

    def con_rescate(prefijo: str) -> float:
        return _pct(sum(any(r.startswith(prefijo) for r in del_turno) for del_turno in rescates), total)

    entrada = sum((t.get("tokens_entrada") or 0) + (t.get("tokens_entrada_sub_loop") or 0) for t in turnos)
    salida = sum((t.get("tokens_salida") or 0) + (t.get("tokens_salida_sub_loop") or 0) for t in turnos)
    medidas: dict[str, Any] = {
        "turnos": total,
        "error_pct": _pct(kinds["error"], total),
        "derivada_pct": _pct(kinds["derivada"], total),
        "mensaje_fijo_pct": con_rescate("mensaje_fijo"),
        "cierre_pct": con_rescate("cierre"),
        "tolerado_pct": con_rescate("tolerado:"),
        "aceptacion_no_clara_pct": _pct(
            sum("aceptacion_no_clara" in (t.get("errores") or []) for t in turnos), total
        ),
        "iteraciones_p50": _percentil((t.get("iteraciones") or 0 for t in turnos), 50),
        "iteraciones_p95": _percentil((t.get("iteraciones") or 0 for t in turnos), 95),
        "latencia_p95_ms": _percentil((t.get("latencia_ms") or 0 for t in turnos), 95),
        "tokens_por_turno": round((entrada + salida) / total) if total else 0,
        "kinds": dict(kinds.most_common()),
        "errores": dict(errores.most_common()),
    }
    if precio:
        medidas["costo_usd"] = round((entrada * precio[0] + salida * precio[1]) / 1e6, 4)
    return medidas


def medir_pedidos(drafts: list[tuple[str, int, str]], pedidos: list[str]) -> dict[str, Any]:
    """Los números de las propuestas (`draft_id`, versión, estado) y de los pedidos (estado)."""
    versiones: dict[str, int] = {}
    finales: dict[str, str] = {}
    for draft_id, version, estado in sorted(drafts):
        versiones[draft_id], finales[draft_id] = version, estado
    confirmados = [d for d, estado in finales.items() if estado == DraftStatus.CONFIRMED.value]
    return {
        "propuestas": len(finales),
        "confirmadas_pct": _pct(len(confirmados), len(finales)),
        # Cada versión de más es una vez que hubo que volver a armarla: por un error o porque el cliente sumó algo
        "correcciones_por_pedido": (
            round(sum(versiones[d] - 1 for d in confirmados) / len(confirmados), 2) if confirmados else 0.0
        ),
        "pedidos": len(pedidos),
        "cancelados_pct": _pct(pedidos.count(OrderStatus.CANCELLED.value), len(pedidos)),
    }


def avisos(medidas: dict[str, Any]) -> list[str]:
    """Qué pasó su umbral. Los turnos y los pedidos necesitan su propio mínimo para avisar."""
    de_pedidos = {"correcciones_por_pedido", "cancelados_pct"}
    salida = []
    for nombre, umbral in UMBRALES.items():
        cuantos = medidas.get("pedidos" if nombre in de_pedidos else "turnos", 0)
        if cuantos >= MIN_TURNOS and medidas.get(nombre, 0) > umbral:
            salida.append(f"{nombre} {medidas[nombre]} > {umbral}")
    return salida


def leer_turnos(desde: datetime) -> list[dict[str, Any]] | None:
    """Los atributos de cada turno desde esa fecha, o None si no hay experimento o MLflow no responde."""
    import os

    os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "1")
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", "5")
    try:
        from mlflow import MlflowClient

        settings = get_settings()
        cliente = MlflowClient(tracking_uri=settings.mlflow_tracking_uri)
        experimento = cliente.get_experiment_by_name(settings.mlflow_experiment)
        if experimento is None:
            return None
        turnos, token = [], None
        while True:
            pagina = cliente.search_traces(
                locations=[experimento.experiment_id],
                filter_string=f"timestamp_ms > {int(desde.timestamp() * 1000)}",
                max_results=100,
                page_token=token,
            )
            raices = (traza.data.spans[0] for traza in pagina if traza.data.spans)
            turnos += [dict(raiz.attributes) for raiz in raices if raiz.name == "turno"]
            if not (token := pagina.token):
                return turnos
    except Exception as exc:  # la URI puede llevar credenciales: no se muestra (RNF-14)
        print(f"No se pudieron leer las trazas de MLflow ({type(exc).__name__})")
        return None


def leer_pedidos(desde: datetime) -> dict[str, Any]:
    columnas = (DraftRecord.draft_id, DraftRecord.version, DraftRecord.status)
    try:
        with SessionLocal() as session:
            drafts = session.execute(select(*columnas).where(DraftRecord.created_at > desde)).all()
            pedidos = session.scalars(select(OrderRecord.status).where(OrderRecord.created_at > desde)).all()
    except OperationalError:  # sin base o con una vieja: el sensor no migra ni crea nada
        print("No se pudo leer la base interna: las medidas de pedidos quedan en cero")
        return medir_pedidos([], [])
    return medir_pedidos([tuple(fila) for fila in drafts], list(pedidos))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dias", type=float, default=7, help="cuántos días hacia atrás")
    parser.add_argument("--precio", help="USD por millón de tokens de entrada y de salida, por ejemplo 0.10,0.50")
    args = parser.parse_args(argv)
    precio = None
    if args.precio:
        entrada, salida = (float(parte) for parte in args.precio.split(","))
        precio = (entrada, salida)
    desde = datetime.now(UTC) - timedelta(days=args.dias)
    turnos = leer_turnos(desde)
    medidas = {**(medir_turnos(turnos, precio) if turnos is not None else {}), **leer_pedidos(desde)}
    for nombre, valor in medidas.items():
        print(f"{nombre:26}{valor}")
    if encontrados := avisos(medidas):
        print("\n" + "\n".join(f"⚠️  {aviso}" for aviso in encontrados))
        return 1
    if turnos is None:
        return 2
    minimo = min(medidas["turnos"], medidas["pedidos"])
    print("\nSin avisos" + (f" (hacen falta {MIN_TURNOS} turnos y pedidos para avisar)" if minimo < MIN_TURNOS else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
