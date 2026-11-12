"""Convierte lo que propone el LLM ({sku, quantity, note}) en líneas de pedido validadas contra el catálogo."""

from internal.schemas.agent_schema import ProposedItem, ToolFailure
from internal.schemas.order_schema import OrderItem, clean_note
from internal.services import inventory_service


def normalize_items(items: list[ProposedItem]) -> list[OrderItem]:
    """Completa nombre, presentación y precio desde el catálogo.

    Las líneas con el mismo SKU y la misma nota se suman en una; con notas distintas quedan separadas (dos tonos
    de una coloración que es un solo SKU, V-01). El stock se controla por SKU, con la suma de todas sus líneas.
    """
    catalog = {product.sku: product for product in inventory_service.get_catalog()}
    lines: dict[tuple[str, str | None], int] = {}
    for item in items:
        key = (item.sku, clean_note(item.note))
        lines[key] = lines.get(key, 0) + item.quantity

    per_sku: dict[str, int] = {}
    for (sku, _), quantity in lines.items():
        per_sku[sku] = per_sku.get(sku, 0) + quantity

    unknown = [sku for sku in per_sku if sku not in catalog]
    if unknown:
        raise ToolFailure(
            "sku_inexistente",
            f"No existen estos SKU: {', '.join(unknown)}. "
            "Buscá de nuevo el producto con search_products, no pruebes SKU de memoria",
            skus=unknown,
        )

    short = [
        {"sku": sku, "requested": quantity, "available": catalog[sku].stock}
        for sku, quantity in per_sku.items()
        if quantity > catalog[sku].stock
    ]
    if short:
        raise ToolFailure(
            "stock_insuficiente",
            "No alcanza el stock de alguna línea: "
            "usá las cantidades de `lineas` y seguí BR-01, de la skill tomar_pedido",
            lineas=short,
        )

    return [
        OrderItem(
            sku=sku,
            product_name=catalog[sku].name,
            presentation=catalog[sku].presentation,
            quantity=quantity,
            unit_price=catalog[sku].price,
            note=note,
        )
        for (sku, note), quantity in lines.items()
    ]
