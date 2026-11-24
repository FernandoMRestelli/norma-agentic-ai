"""Contexto del cliente: lo que el backend sabe de él, armado sin LLM una vez por día (spec del orquestador §3.2).

Se guarda en el estado del grafo y entra al prompt como un bloque de datos (RF-84, RF-85). Vence con el cambio de
fecha en la zona del negocio o al registrarse un pedido (D-31).
"""

import logging
from datetime import datetime
from typing import Any

from internal.agent import guardrails
from internal.services import order_service
from internal.services.sheets_service import AR_TZ
from internal.tools import order_tools

logger = logging.getLogger(__name__)

MAX_PEDIDOS = 3  # los mismos que devuelve `pedidos_anteriores` por defecto


def ahora() -> datetime:
    """Los tests lo reemplazan para cambiar de día."""
    return datetime.now(AR_TZ)


def hoy() -> str:
    return ahora().date().isoformat()


def vigente(contexto: dict[str, Any] | None) -> bool:
    """Sirve para este mensaje: es de hoy y se pudo leer la base. Si falló, el próximo mensaje vuelve a intentar."""
    return bool(contexto) and contexto.get("fecha") == hoy() and contexto.get("completo", False)


def armar(customer_id: str, customer_name: str | None) -> dict[str, Any]:
    """Lee la base una vez. Si falla, el turno sigue sin pedidos ni comprados y queda un warning (CA-147)."""
    contexto: dict[str, Any] = {
        "nombre": guardrails.limpiar_nombre(customer_name),
        "pedidos": [],
        "comprados": [],
        "desde": ahora().isoformat(timespec="seconds"),
        "fecha": hoy(),
        "completo": True,
    }
    try:
        contexto["pedidos"] = order_tools.pedidos_anteriores(customer_id, MAX_PEDIDOS)
        contexto["comprados"] = sorted(order_service.skus_comprados(customer_id))
    except Exception as exc:  # el mensaje de la excepción puede traer datos del cliente: solo el tipo (RNF-03)
        logger.warning("Agente: no se pudo leer el historial del cliente para su contexto (%s)", type(exc).__name__)
        contexto.update(pedidos=[], comprados=[], completo=False)
    return contexto


def comprados(contexto: dict[str, Any] | None) -> frozenset[str] | None:
    """Los SKU que ya compró, o None si no hay un contexto completo: las tools los leen de la base (RF-73)."""
    if not contexto or not contexto.get("completo"):
        return None
    return frozenset(contexto.get("comprados") or [])


def _linea(linea: dict[str, Any]) -> str:
    nota = f", {linea['nota']}" if linea.get("nota") else ""
    return f"{linea['cantidad']} x {linea['name']} ({linea['sku']}{nota})"


def bloque(contexto: dict[str, Any] | None) -> str | None:
    """El bloque del prompt: datos, no instrucciones ni una tool call puesta por el backend (RF-85, CA-35)."""
    if not contexto:
        return None
    lines = [
        "## Contexto del cliente",
        "",
        "Lo armó el sistema con su historial; no hace falta llamar a `pedidos_anteriores` para verlo.",
    ]
    if not contexto.get("completo"):
        lines.append("No se pudo leer su historial en este mensaje.")
    elif not contexto.get("pedidos"):
        lines.append("Pedidos anteriores: ninguno.")
    else:
        lines.append("Pedidos anteriores, del más nuevo al más viejo (sin precios: pueden haber cambiado):")
        lines += [f"- {pedido['fecha']}: {'; '.join(map(_linea, pedido['lineas']))}" for pedido in contexto["pedidos"]]
    return "\n".join(lines)
