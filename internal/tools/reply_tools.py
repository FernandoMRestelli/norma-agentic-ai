"""Tool para responderle al cliente. Cierra el turno y, si lleva una propuesta, la deja validada."""

from typing import Any
from uuid import uuid4

from internal.schemas.agent_schema import RespondToCustomerInput, ToolFailure
from internal.schemas.order_schema import Channel, Leftover, OrderDraft
from internal.services.interpretation_service import normalize_items
from internal.tools.catalog_tools import get_product


def respond_to_customer(
    data: RespondToCustomerInput,
    customer_id: str,
    channel: Channel,
    vigente: OrderDraft | None,
    customer_name: str | None = None,
    conversation_id: int | None = None,
    renglones: list[dict[str, Any]] | None = None,
    origen: str = "texto",
) -> OrderDraft | None:
    """Valida la respuesta y devuelve la propuesta vigente después de ella.

    Sin `propuesta`, no queda ninguna: solo sigue vigente lo último que el bot le ofreció al cliente. No escribe en
    `drafts`: eso lo hace `tool_registry` cuando la respuesta ya quedó aceptada (RF-46).
    """
    ofrecidos = [*data.options, *(sku for pendiente in data.pendientes for sku in pendiente.opciones)]
    unknown = [sku for sku in dict.fromkeys(ofrecidos) if get_product(sku) is None]
    if unknown:
        raise ToolFailure(
            "sku_inexistente",
            f"No existen estos SKU: {', '.join(unknown)}. "
            "Buscá de nuevo el producto con search_products, no pruebes SKU de memoria",
            skus=unknown,
        )
    if not data.propuesta:
        return None
    return OrderDraft(
        draft_id=vigente.draft_id if vigente else uuid4().hex,
        customer_id=customer_id,
        customer_name=customer_name,
        channel=channel,
        conversation_id=conversation_id,
        items=normalize_items(data.propuesta, renglones),
        notes=data.notes,
        version=vigente.version + 1 if vigente else 1,
        source=origen,
    )


def fuera_del_pedido(renglones: list[dict[str, Any]]) -> list[Leftover]:
    """Lo que el cliente pidió y no entra a esta propuesta: afuera con su motivo, o una duda sin resolver (RF-76).

    Una línea que no pedía nada (`no_es_producto`) no es una venta perdida y no se guarda.
    """
    afuera = []
    for renglon in renglones:
        if renglon["estado"] == "afuera" and renglon.get("motivo") != "no_es_producto":
            afuera.append(Leftover(source_text=renglon["texto"], reason=renglon.get("motivo") or "afuera"))
        elif renglon["estado"] == "pendiente":
            afuera.append(Leftover(source_text=renglon["texto"], reason="duda_sin_resolver"))
    return afuera


def con_tonos(data: RespondToCustomerInput, renglones: list[dict[str, Any]]) -> RespondToCustomerInput:
    """Una línea que resuelve un renglón de coloración y no trae nota lleva su tono (RF-69, V-01)."""
    tonos = {renglon["n"]: renglon["tono"] for renglon in renglones if renglon.get("tono")}
    if not data.propuesta or not tonos:
        return data
    items = [
        item.model_copy(update={"note": f"tono {tonos[item.renglon]}"})
        if item.renglon in tonos and not item.note
        else item
        for item in data.propuesta
    ]
    return data.model_copy(update={"propuesta": items})


def cubrir_renglones(
    data: RespondToCustomerInput, renglones: list[dict[str, Any]], tolerar: bool = False
) -> list[dict[str, Any]]:
    """Devuelve los renglones como quedan después de esta respuesta, o falla si alguno quedó sin cuenta (RF-68).

    Cada renglón numerado del pedido tiene que terminar en `propuesta` (con su `renglon`), en `pendientes` o en
    `afuera`. Con `tolerar`, los que faltan quedan pendientes en vez de fallar: el modelo ya tuvo sus intentos.
    Un renglón resuelto cuyo producto ya no está en la propuesta nueva pasa a `afuera`: lo sacó el cliente.
    """
    por_n = {renglon["n"]: dict(renglon) for renglon in renglones}
    nombrados = [
        *(item.renglon for item in data.propuesta or [] if item.renglon is not None),
        *(pendiente.renglon for pendiente in data.pendientes),
        *(afuera.renglon for afuera in data.afuera),
    ]
    desconocidos = sorted({n for n in nombrados if n not in por_n})
    if desconocidos:
        raise ToolFailure(
            "renglon_inexistente",
            f"No existen los renglones {desconocidos}: usá los números del estado actual, o no mandes `renglon`",
            renglones=desconocidos,
        )

    if data.propuesta is not None:
        vigentes = {item.sku for item in data.propuesta}
        for renglon in por_n.values():
            if renglon["estado"] == "resuelto" and not {linea["sku"] for linea in renglon["lineas"]} <= vigentes:
                renglon.update(estado="afuera", motivo="lo_saco_el_cliente")
    resueltos: dict[int, list[dict[str, Any]]] = {}
    for item in data.propuesta or []:
        if item.renglon is not None:
            resueltos.setdefault(item.renglon, []).append({"sku": item.sku, "cantidad": item.quantity})
    for n, lineas in resueltos.items():
        por_n[n].update(estado="resuelto", lineas=lineas)
    for pendiente in data.pendientes:
        por_n[pendiente.renglon].update(estado="pendiente", opciones=pendiente.opciones)
    for afuera in data.afuera:
        por_n[afuera.renglon].update(estado="afuera", motivo=afuera.motivo)

    sin_cuenta = [n for n, renglon in por_n.items() if renglon["estado"] == "sin_resolver"]
    if sin_cuenta and not tolerar:
        raise ToolFailure(
            "renglones_sin_cuenta",
            f"Faltan los renglones {sin_cuenta}: cada renglón del pedido va en `propuesta` (con su número en "
            "`renglon`), en `pendientes` si le preguntás al cliente, o en `afuera` con el motivo. Volvé a responder "
            "con todos",
            renglones=sin_cuenta,
        )
    for n in sin_cuenta:
        por_n[n].update(estado="pendiente", opciones=[])
    return list(por_n.values())
