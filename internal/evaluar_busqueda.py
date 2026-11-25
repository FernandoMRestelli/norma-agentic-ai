"""Evalúa el buscador solo, sin LLM ni red: python -m internal.evaluar_busqueda (RF-119)

Corre las consultas de `data_example/consultas_busqueda.json` contra `data_example/catalogo_evaluacion.csv`. Un buen
buscador no garantiza un buen agente, pero si acá no aparece lo relevante, el agente no lo puede proponer.

Cada consulta dice qué SKU son relevantes (`relevantes`, con comodines; vacío: no hay respuesta en el catálogo):
son los juicios de relevancia, o *qrels*. Con una sola respuesta posible importa la posición (MRR, acierto@1); con
varias, que las primeras lo sean (recall@k, sobre lo que entra en k); y sin respuesta, que no invente una.
"""

import argparse
import csv
import json
from collections import Counter
from decimal import Decimal
from fnmatch import fnmatchcase
from pathlib import Path

from internal.schemas.catalog_schema import Product
from internal.services import busqueda_service
from internal.tools import catalog_tools

DATOS = Path(__file__).resolve().parent.parent / "data_example"
CONSULTAS = DATOS / "consultas_busqueda.json"
CATALOGO = DATOS / "catalogo_evaluacion.csv"
K = 3  # los candidatos que ve el LLM en cada renglón (catalog_tools.MAX_CANDIDATOS)


def leer_catalogo(path: Path = CATALOGO) -> list[Product]:
    with open(path, encoding="utf-8", newline="") as archivo:
        return [
            # El stock no cambia el orden de la búsqueda: con todo disponible, el estado dice solo qué se encontró
            Product(
                sku=fila["sku"], name=fila["nombre"], brand=fila["marca"] or None,
                presentation=fila["presentacion"] or None, category=fila["categoria"] or None,
                price=Decimal(fila["precio"]), stock=99,
            )
            for fila in csv.DictReader(archivo)
        ]  # fmt: skip


def leer_consultas(path: Path = CONSULTAS, grupo: str = "todos") -> list[dict]:
    consultas = json.loads(path.read_text(encoding="utf-8"))
    return [c for c in consultas if grupo in ("todos", c["grupo"])]


def relevantes(consulta: dict, productos: list[Product]) -> set[str]:
    return {p.sku for p in productos if any(fnmatchcase(p.sku, patron) for patron in consulta["relevantes"])}


def evaluar(consulta: dict, productos: list[Product]) -> dict:
    """Qué devolvió la búsqueda para una consulta, comparado con sus juicios de relevancia."""
    correctos = relevantes(consulta, productos)
    resultado = busqueda_service.buscar(consulta["consulta"], productos, limit=10)
    lista = [p.sku for p in (resultado.matches or resultado.sugerencias)]
    renglon = catalog_tools.buscar_renglones([{"n": 1, "texto": consulta["consulta"], "cantidad": 1}], frozenset())[0]
    posicion = next((i for i, sku in enumerate(lista, 1) if sku in correctos), 0)
    unico = renglon["estado"] == "unico"
    acierto_unico = unico and len(correctos) == 1 and renglon["candidatos"][0]["sku"] in correctos
    return {
        "id": consulta["id"], "tipo": consulta["tipo"], "grupo": consulta["grupo"], "consulta": consulta["consulta"],
        "con_respuesta": bool(correctos), "una_respuesta": len(correctos) == 1,
        "estado": renglon["estado"], "primeros": lista[:K],
        "rr": 1 / posicion if posicion else 0.0,
        "acierto_1": posicion == 1,
        "recall_k": len(correctos & set(lista[:K])) / min(K, len(correctos)) if correctos else 0.0,
        "unico_correcto": acierto_unico,
        # El error caro: el LLM lo propone sin preguntar
        "falso_unico": unico and not acierto_unico,
        # Lo hay, pero la búsqueda vuelve vacía: el agente le dice al cliente que no se trabaja
        "negado": bool(correctos) and not lista,
    }  # fmt: skip


def _promedio(valores: list[float]) -> float:
    return round(sum(valores) / len(valores), 3) if valores else 0.0


def metricas(filas: list[dict]) -> dict[str, float]:
    con = [f for f in filas if f["con_respuesta"]]
    sin = [f for f in filas if not f["con_respuesta"]]
    return {
        "consultas": len(filas),
        f"recall_{K}": _promedio([f["recall_k"] for f in con]),
        "mrr": _promedio([f["rr"] for f in con]),
        "acierto_1": _promedio([f["acierto_1"] for f in con]),
        "unicos_correctos": _promedio([f["unico_correcto"] for f in con if f["una_respuesta"]]),
        "abstencion": _promedio([f["estado"] in ("no_encontrado", "aproximado") for f in sin]),
        "falsos_unicos": sum(f["falso_unico"] for f in filas),
        "negados": sum(f["negado"] for f in filas),
    }


def correr(grupo: str = "todos", productos: list[Product] | None = None) -> list[dict]:
    from internal.services import inventory_service

    productos = productos or leer_catalogo()
    original = inventory_service.get_catalog
    inventory_service.get_catalog = lambda *_, **__: productos  # `buscar_renglones` lo lee de ahí
    try:
        return [evaluar(consulta, productos) for consulta in leer_consultas(grupo=grupo)]
    finally:
        inventory_service.get_catalog = original


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--grupo", choices=("desarrollo", "reservado", "todos"), default="todos")
    parser.add_argument("--fallas", action="store_true", help="lista las consultas que no salieron bien")
    args = parser.parse_args()
    filas = correr(args.grupo)
    for nombre in ("total", *dict.fromkeys(f["tipo"] for f in filas)):
        grupo = filas if nombre == "total" else [f for f in filas if f["tipo"] == nombre]
        print(f"{nombre:22}" + "  ".join(f"{clave} {valor}" for clave, valor in metricas(grupo).items()))
    print("estados:", dict(Counter(f["estado"] for f in filas)))
    if args.fallas:
        for f in filas:
            mal = f["falso_unico"] or f["negado"] or (f["con_respuesta"] and not f["acierto_1"])
            if mal or (not f["con_respuesta"] and f["estado"] not in ("no_encontrado", "aproximado")):
                print(f"  {f['id']}: «{f['consulta']}» → {f['estado']} {f['primeros']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
