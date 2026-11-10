"""Convierte lo que propone el LLM ({sku, quantity}) en líneas de pedido validadas contra el catálogo."""

from internal.schemas.agent_schema import ProposedItem, ToolFailure
from internal.schemas.order_schema import OrderItem
from internal.services import inventory_service


def normalize_items(items: list[ProposedItem]) -> list[OrderItem]:
    """Completa nombre, presentación y precio desde el catálogo. Un SKU repetido se suma en una sola línea."""
    catalog = {product.sku: product for product in inventory_service.get_catalog()}
    quantities: dict[str, int] = {}
    for item in items:
        quantities[item.sku] = quantities.get(item.sku, 0) + item.quantity

    unknown = [sku for sku in quantities if sku not in catalog]
    if unknown:
        raise ToolFailure(
            "sku_inexistente",
            f"No existen estos SKU: {', '.join(unknown)}. "
            "Buscá de nuevo el producto con search_products, no pruebes SKU de memoria",
            skus=unknown,
        )

    short = [
        {"sku": sku, "requested": quantity, "available": catalog[sku].stock}
        for sku, quantity in quantities.items()
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
        )
        for sku, quantity in quantities.items()
    ]
