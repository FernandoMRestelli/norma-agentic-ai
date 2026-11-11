"""Tool para responderle al cliente. Cierra el turno y, si lleva una propuesta, la deja validada y guardada."""

from uuid import uuid4

from internal.schemas.agent_schema import RespondToCustomerInput, ToolFailure
from internal.schemas.order_schema import Channel, OrderDraft
from internal.services import order_service
from internal.services.interpretation_service import normalize_items
from internal.tools.catalog_tools import get_product


def respond_to_customer(
    data: RespondToCustomerInput,
    customer_id: str,
    channel: Channel,
    vigente: OrderDraft | None,
    customer_name: str | None = None,
    conversation_id: int | None = None,
) -> OrderDraft | None:
    """Valida la respuesta y devuelve la propuesta vigente después de ella.

    Sin `propuesta`, no queda ninguna: solo sigue vigente lo último que el bot le ofreció al cliente. Cada versión
    propuesta, y el descarte de la anterior, quedan registrados en la tabla `drafts` (RF-46).
    """
    unknown = [sku for sku in data.options if get_product(sku) is None]
    if unknown:
        raise ToolFailure(
            "sku_inexistente",
            f"No existen estos SKU: {', '.join(unknown)}. "
            "Buscá de nuevo el producto con search_products, no pruebes SKU de memoria",
            skus=unknown,
        )
    if not data.propuesta:
        if vigente:
            order_service.discard_draft(vigente)
        return None
    draft = OrderDraft(
        draft_id=vigente.draft_id if vigente else uuid4().hex,
        customer_id=customer_id,
        customer_name=customer_name,
        channel=channel,
        conversation_id=conversation_id,
        items=normalize_items(data.propuesta),
        notes=data.notes,
        version=vigente.version + 1 if vigente else 1,
    )
    order_service.record_draft(draft)
    return draft
