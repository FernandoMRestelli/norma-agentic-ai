"""Precarga de la búsqueda: el backend busca los renglones inequívocos antes de la primera llamada al LLM (RF-86).

El resultado entra al contexto como **nota de búsqueda**, un bloque de datos y no una tool call inventada (CA-35):
el modelo lo usa como si hubiera llamado a `buscar_renglones`, sin gastar una vuelta en eso.
"""

import json
import logging
from collections import Counter
from typing import Any

from internal.agent import tool_registry
from internal.services import interpretation_service
from internal.tools import catalog_tools

logger = logging.getLogger(__name__)

TIPO = "busqueda"


def precargar(
    renglones: list[dict[str, Any]],
    comprados: frozenset[str] | None,
    skills_activas: list[str],
    br_entregadas: list[str],
) -> dict[str, Any] | None:
    """Busca los renglones `sin_resolver` de forma inequívoca. Devuelve la nota, o None si no buscó nada.

    Cambia `renglones` (tono y coincidencia, como `buscar_renglones`) y suma a `br_entregadas` las BR que adjunta.
    Un renglón sin forma inequívoca queda `sin_resolver` y sin buscar (CA-149).
    """
    buscados = []
    for renglon in renglones:
        if renglon["estado"] != "sin_resolver":
            continue
        # Un renglón de una foto trae el producto y la cantidad ya separados por la lectura (RF-104)
        if (separado := renglon.get("buscar") or interpretation_service.separar_renglon(renglon["texto"])) is None:
            continue
        buscados.append({"n": renglon["n"], "marca": renglon.get("marca"), **separado})
    if not buscados:
        return None
    try:
        resultados = catalog_tools.buscar_renglones(buscados, comprados or frozenset())
    # Sin catálogo, o con una falla de Sheets, no precarga: el LLM busca como hoy y las tools le avisan (§7).
    # Una falla acá nunca corta el turno, igual que una tool que falla
    except Exception as exc:
        logger.warning("Agente: no se pudo hacer la precarga de la búsqueda (%s)", type(exc).__name__)
        return None
    tool_registry.anotar_busqueda(renglones, buscados, resultados)
    sobre = {"ok": True, "data": {"renglones": resultados}}
    reglas = tool_registry.reglas_que_dispara("buscar_renglones", sobre, skills_activas, br_entregadas)
    return {
        "tipo": TIPO,
        "renglones": [buscado["n"] for buscado in buscados],
        "buscados": buscados,
        "resultados": resultados,
        "reglas": reglas,
    }


def cuentas(nota: dict[str, Any] | None, sin_resolver: int) -> dict[str, int]:
    """Para la traza: cuántos renglones buscó, cuántos quedaron para el LLM y cuántos dieron cada estado."""
    buscados = len(nota["renglones"]) if nota else 0
    estados = Counter(resultado["estado"] for resultado in (nota or {}).get("resultados") or [])
    return {"buscados": buscados, "sin_separar": sin_resolver - buscados, **estados}


def precargados(notas: list[dict[str, Any]]) -> set[int]:
    """Los renglones que la precarga ya buscó en este turno."""
    return {n for nota in notas if nota.get("tipo") == TIPO for n in nota["renglones"]}


def skus(nota: dict[str, Any]) -> set[str]:
    """Los SKU que trajo la nota: cuentan como buscados para validar la nota de un sub-loop (RF-91)."""
    return {candidato["sku"] for resultado in nota["resultados"] for candidato in resultado["candidatos"]}


def bloque(notas: list[dict[str, Any]]) -> str | None:
    """La nota de búsqueda en el prompt: cada renglón con lo que se buscó y lo que volvió, y las BR que dispara."""
    nota = next((nota for nota in notas if nota.get("tipo") == TIPO), None)
    if nota is None:
        return None
    filas = []
    for buscado, resultado in zip(nota["buscados"], nota["resultados"], strict=True):
        fila = {"buscado": {clave: valor for clave, valor in buscado.items() if valor is not None}, **resultado}
        filas.append(json.dumps(fila, ensure_ascii=False))
    lines = [
        "## Nota de búsqueda",
        "",
        "El sistema ya buscó estos renglones con `buscar_renglones`, con la cantidad y el tono de `buscado`. Usá "
        "estos resultados como si los hubieras pedido vos: no los vuelvas a buscar igual. Si la cantidad o el tono "
        "no son los que pidió el cliente, corregilos al responder.",
        "",
        *filas,
    ]
    for regla in nota["reglas"]:  # las BR que dispara el resultado, como las adjunta la tool (RF-63)
        lines += ["", f"### {regla['id']}", "", regla["texto"].strip()]
    return "\n".join(lines)
