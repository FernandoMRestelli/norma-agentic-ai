"""Evidencia del turno: qué productos vio el LLM y cuáles quedaron definidos (RF-112 a RF-114, D-48, D-49).

Que un SKU exista en el catálogo no dice que sea lo que pidió el cliente. Antes de aceptar una propuesta, el backend
comprueba que cada producto haya salido de algo que el LLM vio y que no haya elegido por el cliente entre varias
opciones. Los resultados con `ok: false` no cuentan: pueden repetir el SKU inventado que los causó.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AnyMessage, ToolMessage

from internal.agent import guardrails
from internal.tools.catalog_tools import SCORE_SEGURO

# Las tools que muestran productos. `respond_to_customer`, `delegar_skill` y las demás no son una búsqueda
TOOLS = frozenset(
    {"search_products", "buscar_renglones", "get_product", "check_stock", "explorar_catalogo", "pedidos_anteriores"}
)


@dataclass
class Evidencia:
    vistos: set[str] = field(default_factory=set)
    ambiguos: set[str] = field(default_factory=set)  # salieron entre varios, o como aproximados
    definidos: set[str] = field(default_factory=set)  # la única coincidencia segura de una búsqueda sin renglón
    # Renglón buscado → su única coincidencia segura; vacío si se buscó y no quedó definido
    por_renglon: dict[int, set[str]] = field(default_factory=dict)
    conjuntos: list[frozenset[str]] = field(default_factory=list)  # todas las coincidencias de una búsqueda

    def sumar(self, tool: str, data: dict[str, Any]) -> None:
        """Suma el resultado de una tool que salió bien."""
        if tool not in TOOLS:
            return
        self._ver(data)
        if tool == "search_products":
            self._sumar_busqueda(data)
        elif tool == "buscar_renglones":
            for resultado in data["renglones"]:
                self._sumar_renglon(resultado)
        elif tool == "explorar_catalogo":
            muestra = [producto["sku"] for producto in data.get("muestra_productos") or []]
            if len(muestra) > 1:
                self._sumar_varias(muestra, completas=data.get("total_productos") == len(muestra))

    def sumar_nota(self, nota: dict[str, Any]) -> None:
        """Suma la nota de búsqueda de la precarga o la de un sub-loop, que ya pasó por estos controles."""
        if "resultados" in nota:
            self._ver(nota["resultados"])
            for resultado in nota["resultados"]:
                self._sumar_renglon(resultado)
            return
        entregada = nota.get("nota") or {}
        self._ver(entregada.get("resueltos") or [])
        self.vistos |= set(nota.get("faltantes") or {})
        for resuelto in entregada.get("resueltos") or []:
            if (n := resuelto.get("renglon")) is None:
                self.definidos.add(resuelto["sku"])
            else:
                self.por_renglon.setdefault(n, set()).add(resuelto["sku"])
        for pendiente in entregada.get("pendientes") or []:
            self._sumar_varias(pendiente["opciones"], completas=False)
            self.vistos |= set(pendiente["opciones"])
            if (n := pendiente.get("renglon")) is not None:
                self.por_renglon.setdefault(n, set())

    def _ver(self, datos: Any) -> None:
        turno = guardrails.ContextoTurno()
        guardrails.sumar_notas(turno, datos if isinstance(datos, list) else [datos])
        self.vistos |= turno.skus

    def _sumar_varias(self, skus: Iterable[str], completas: bool) -> None:
        opciones = frozenset(skus)
        self.ambiguos |= opciones
        if completas and len(opciones) > 1:
            self.conjuntos.append(opciones)

    def _sumar_busqueda(self, data: dict[str, Any]) -> None:
        matches = data["matches"]
        if len(matches) == data["total"] == 1 and _seguro(matches[0]):
            self.definidos.add(matches[0]["sku"])
            return
        encontrados = [producto["sku"] for producto in [*matches, *data["sugerencias"]]]
        self._sumar_varias(encontrados, completas=data["total"] == len(matches) and all(map(_seguro, matches)))

    def _sumar_renglon(self, resultado: dict[str, Any]) -> None:
        definidos = self.por_renglon.setdefault(resultado["n"], set())
        candidatos = [candidato["sku"] for candidato in resultado["candidatos"]]
        if resultado["estado"] == "unico":
            definidos.add(candidatos[0])
        elif resultado["estado"] == "sin_stock":
            definidos.add(resultado["falta"]["sku"])
        else:
            completas = resultado["estado"] == "varias" and resultado["total"] == len(candidatos)
            self._sumar_varias(candidatos, completas)

    def sin_elegir(self, lineas: list[tuple[str, int | None]], elegidos: "Elegidos") -> list[tuple[str, int | None]]:
        """Las líneas (SKU, renglón) que nadie definió: ni el cliente ni una búsqueda con una sola coincidencia."""
        propuestos = {sku for sku, _ in lineas}
        de_un_conjunto = {sku for conjunto in self.conjuntos if conjunto <= propuestos for sku in conjunto}
        definidos_en_algun_renglon = set().union(*self.por_renglon.values()) if self.por_renglon else set()

        def definida(sku: str, n: int | None) -> bool:
            if sku in self.definidos or sku in de_un_conjunto or sku in elegidos.de(n):
                return True
            if n is not None and n in self.por_renglon:
                return sku in self.por_renglon[n]
            return sku not in self.ambiguos or (n is None and sku in definidos_en_algun_renglon)

        return [(sku, n) for sku, n in lineas if not definida(sku, n)]


@dataclass
class Elegidos:
    """Lo que el cliente ya eligió o le ofrecieron: opciones anteriores, propuesta vigente y renglones del estado."""

    siempre: set[str] = field(default_factory=set)
    por_renglon: dict[int, set[str]] = field(default_factory=dict)

    def de(self, n: int | None) -> set[str]:
        if n is None:
            return self.siempre.union(*self.por_renglon.values())
        return self.siempre | self.por_renglon.get(n, set())


def de_los_renglones(renglones: list[dict[str, Any]]) -> dict[int, set[str]]:
    """Por renglón, los SKU que ya se resolvieron o se le preguntaron al cliente en un turno anterior."""
    return {
        renglon["n"]: {linea["sku"] for linea in renglon.get("lineas") or []} | set(renglon.get("opciones") or [])
        for renglon in renglones
    }


def _seguro(match: dict[str, Any]) -> bool:
    """Como en `buscar_renglones`: un error de tipeo con un puntaje alto no se pregunta (`SCORE_SEGURO`)."""
    return not match.get("aproximado") or match.get("score", 0) >= SCORE_SEGURO


def del_turno(messages: Iterable[AnyMessage], notas: Iterable[dict[str, Any]]) -> Evidencia:
    """La evidencia de los mensajes del turno y de sus notas."""
    evidencia = Evidencia()
    for nota in notas:
        evidencia.sumar_nota(nota)
    for message in messages:
        if isinstance(message, ToolMessage) and message.name in TOOLS:
            result = json.loads(message.content)
            if result.get("ok"):
                evidencia.sumar(message.name, result["data"])
    return evidencia
