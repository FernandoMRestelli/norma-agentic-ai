"""Tests de inventario, stock disponible y descuento transaccional (RF-14, CA-16)."""

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete

from internal.database.connection import SessionLocal, init_db
from internal.database.models import OrderRecord
from internal.schemas.catalog_schema import Product
from internal.schemas.order_schema import Channel, OrderDraft, OrderItem, OrderStatus
from internal.services import inventory_service, order_service, sheets_service
from internal.tools import catalog_tools, inventory_tools

SKU_PANTENE = "SH-PAN-400"  # En catalogo.csv tiene stock base = 24


@pytest.fixture(autouse=True)
def clean_orders():
    """Limpia la tabla orders antes y después de cada test."""
    init_db()
    with SessionLocal() as session:
        session.execute(delete(OrderRecord))
        session.commit()
    yield
    with SessionLocal() as session:
        session.execute(delete(OrderRecord))
        session.commit()


def _make_draft(items: list[tuple[str, int]]) -> OrderDraft:
    return OrderDraft(
        draft_id=uuid4().hex,
        customer_id="cust-1",
        channel=Channel.WEB,
        items=[
            OrderItem(
                sku=sku,
                product_name=f"Producto {sku}",
                presentation="Unidad",
                quantity=qty,
                unit_price=None,  # sin precio: estos tests miran el stock, y al confirmar el precio se revalida
            )
            for sku, qty in items
        ],
    )


def test_stock_disponible_sin_ordenes():
    """Sin órdenes previas, el stock disponible coincide con el stock base del catálogo."""
    base_stock = inventory_service.get_available_stock(SKU_PANTENE)
    assert base_stock == 24
    assert inventory_service.get_committed_stock() == {}

    prod = inventory_service.get_product(SKU_PANTENE)
    assert prod is not None
    assert prod.stock == 24


def test_confirm_draft_descuenta_stock():
    """CA-16: confirmar una orden descuenta unidades del stock disponible en catálogo y tools."""
    draft = _make_draft([(SKU_PANTENE, 10)])
    order = order_service.confirm_draft(draft)
    assert order.status == OrderStatus.RECEIVED

    # El stock disponible ahora debe ser 24 - 10 = 14
    assert inventory_service.get_committed_stock() == {SKU_PANTENE: 10}
    assert inventory_service.get_available_stock(SKU_PANTENE) == 14

    # get_product y catalog_tools reflejan el stock disponible neto
    prod = catalog_tools.get_product(SKU_PANTENE)
    assert prod is not None
    assert prod.stock == 14

    # check_stock valida contra el disponible real
    check_ok = inventory_tools.check_stock(SKU_PANTENE, 14)
    assert check_ok.is_enough is True
    assert check_ok.available == 14

    check_insuficiente = inventory_tools.check_stock(SKU_PANTENE, 15)
    assert check_insuficiente.is_enough is False
    assert check_insuficiente.available == 14


def test_segunda_orden_excede_stock_disponible_falla():
    """CA-16 / RF-14: una segunda orden que excede el remanente disponible es rechazada."""
    # Primera orden: consume 20 de 24
    draft_1 = _make_draft([(SKU_PANTENE, 20)])
    order_1 = order_service.confirm_draft(draft_1)
    assert order_1.order_id is not None
    assert inventory_service.get_available_stock(SKU_PANTENE) == 4

    # Segunda orden: pide 10 (alcanzaría si fuera 24, pero solo quedan 4)
    draft_2 = _make_draft([(SKU_PANTENE, 10)])
    with pytest.raises(order_service.InsufficientStockError) as exc_info:
        order_service.confirm_draft(draft_2)

    assert exc_info.value.lines == [{"sku": SKU_PANTENE, "requested": 10, "available": 4}]
    # El stock sigue siendo 4 y no se creó la segunda orden
    assert inventory_service.get_available_stock(SKU_PANTENE) == 4


def test_confirm_draft_concurrente_evita_sobreventa():
    """CA-16 / RF-14: dos confirmaciones en paralelo compitiendo por el stock solo crean 1 orden."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    # SKU_PANTENE tiene 24 unidades base. Creamos un draft de 20 y otro de 10.
    draft_a = _make_draft([(SKU_PANTENE, 20)])
    draft_b = _make_draft([(SKU_PANTENE, 10)])
    barrier = Barrier(2)

    def _worker(draft: OrderDraft):
        barrier.wait()
        return order_service.confirm_draft(draft)

    with ThreadPoolExecutor(max_workers=2) as executor:
        f_a = executor.submit(_worker, draft_a)
        f_b = executor.submit(_worker, draft_b)

    results = []
    errors = []
    for f in (f_a, f_b):
        try:
            results.append(f.result())
        except order_service.InsufficientStockError as exc:
            errors.append(exc)

    # Exactamente una orden se crea y la otra es rechazada por falta de stock
    assert len(results) == 1
    assert len(errors) == 1
    # El stock restante debe ser 4 o 14 según cuál ganó la carrera
    assert inventory_service.get_available_stock(SKU_PANTENE) in (4, 14)
    with SessionLocal() as session:
        orders = session.query(OrderRecord).all()
        assert len(orders) == 1
        assert orders[0].order_id == results[0].order_id


def test_cancel_order_libera_stock():
    """RF-20: cancelar una orden libera sus unidades y restaura el stock disponible."""
    draft = _make_draft([(SKU_PANTENE, 20)])
    order = order_service.confirm_draft(draft)
    assert inventory_service.get_available_stock(SKU_PANTENE) == 4

    # Cancelamos la orden
    cancelled = order_service.cancel_order(order.order_id)
    assert cancelled.status == OrderStatus.CANCELLED

    # El stock debe volver a 24
    assert inventory_service.get_committed_stock() == {}
    assert inventory_service.get_available_stock(SKU_PANTENE) == 24

    # Ahora una orden por 20 vuelve a pasar exitosamente
    draft_nuevo = _make_draft([(SKU_PANTENE, 20)])
    order_nueva = order_service.confirm_draft(draft_nuevo)
    assert order_nueva.order_id != order.order_id
    assert inventory_service.get_available_stock(SKU_PANTENE) == 4


def test_cancel_order_validaciones():
    """No se puede cancelar una orden inexistente o en estado terminal."""
    with pytest.raises(ValueError, match="Orden no encontrada"):
        order_service.cancel_order("ORD-9999")

    # Si ya está cancelada, no se puede volver a cancelar
    draft = _make_draft([(SKU_PANTENE, 5)])
    order = order_service.confirm_draft(draft)
    order_service.cancel_order(order.order_id)

    with pytest.raises(ValueError, match="No se puede cancelar una orden en estado"):
        order_service.cancel_order(order.order_id)


def test_idempotencia_no_duplica_descuento():
    """CA-15: confirmar el mismo draft_id dos veces no duplica el descuento de stock."""
    draft = _make_draft([(SKU_PANTENE, 5)])
    order_1 = order_service.confirm_draft(draft)
    assert inventory_service.get_available_stock(SKU_PANTENE) == 19

    order_2 = order_service.confirm_draft(draft)
    assert order_2.order_id == order_1.order_id
    assert inventory_service.get_available_stock(SKU_PANTENE) == 19


def test_stock_disponible_no_negativo(monkeypatch):
    """El stock disponible nunca es inferior a cero."""
    draft = _make_draft([(SKU_PANTENE, 5)])
    order_service.confirm_draft(draft)

    # Si el catálogo base se reduce externamente a 2 (menos que los 5 comprometidos)
    monkeypatch.setattr(
        sheets_service,
        "get_catalog",
        lambda: [
            Product(
                sku=SKU_PANTENE,
                name="Shampoo",
                presentation="400 ml",
                price=Decimal("100"),
                stock=2,
            )
        ],
    )
    # Debe ser 0, no -3
    assert inventory_service.get_available_stock(SKU_PANTENE) == 0
