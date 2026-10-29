"""Normaliza lo que interpretó el LLM (nombres, unidades, cantidades) a líneas de pedido."""

from typing import Any

from backend.schemas.order_schema import OrderItem


def normalize_items(raw_items: list[dict[str, Any]]) -> list[OrderItem]:
    """Convierte la salida cruda del agente en `OrderItem` validados contra el catálogo.

    Ej.: "media docena" -> 6, "pantene 400" -> SKU de Shampoo Pantene 400 ml.
    """
    raise NotImplementedError
