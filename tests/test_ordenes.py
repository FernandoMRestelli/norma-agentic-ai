import pytest
from pydantic import ValidationError

from internal.schemas.order_schema import Order, OrderStatus
from internal.services.order_service import (
    can_transition,
    format_order_id,
    parse_order_id,
    parse_status,
)


@pytest.mark.parametrize(
    ("numero", "esperado"),
    [
        (1, "ORD-0001"),
        (42, "ORD-0042"),
        (999, "ORD-0999"),
        (9999, "ORD-9999"),
        (10000, "ORD-10000"),
        (123456, "ORD-123456"),
    ],
)
def test_format_order_id(numero: int, esperado: str):
    """RF-17: formatea correlativo a ORD-0001 con al menos 4 dígitos y sin truncar > 9999."""
    assert format_order_id(numero) == esperado
    assert parse_order_id(esperado) == numero


@pytest.mark.parametrize("invalido", [0, -1, -42])
def test_format_order_id_rechaza_no_positivos(invalido: int):
    """RF-17: no se pueden generar identificadores para correlativos no positivos."""
    with pytest.raises(ValueError):
        format_order_id(invalido)


@pytest.mark.parametrize(
    "invalido",
    [
        "ORD-1",
        "ord-0001",
        "0001",
        "ORD-0000",
        "ORD-00042",  # ceros sobrantes para números de 2 dígitos
        "ORD-",
        "ORD-ABCD",
        "ORD-0001a",
        "",
        "   ",
    ],
)
def test_order_id_mal_formado_se_rechaza(invalido: str):
    """RF-17: rechaza formatos que no cumplan estrictamente con ORD-0001."""
    with pytest.raises(ValueError):
        parse_order_id(invalido)


@pytest.mark.parametrize(
    ("actual", "nuevo", "esperado"),
    [
        # Desde Recibido
        (OrderStatus.RECEIVED, OrderStatus.RECEIVED, False),
        (OrderStatus.RECEIVED, OrderStatus.PREPARED, True),
        (OrderStatus.RECEIVED, OrderStatus.PICKED_UP, False),
        (OrderStatus.RECEIVED, OrderStatus.CANCELLED, True),
        # Desde Preparado
        (OrderStatus.PREPARED, OrderStatus.RECEIVED, False),
        (OrderStatus.PREPARED, OrderStatus.PREPARED, False),
        (OrderStatus.PREPARED, OrderStatus.PICKED_UP, True),
        (OrderStatus.PREPARED, OrderStatus.CANCELLED, True),
        # Desde Retirado (estado terminal)
        (OrderStatus.PICKED_UP, OrderStatus.RECEIVED, False),
        (OrderStatus.PICKED_UP, OrderStatus.PREPARED, False),
        (OrderStatus.PICKED_UP, OrderStatus.PICKED_UP, False),
        (OrderStatus.PICKED_UP, OrderStatus.CANCELLED, False),
        # Desde Cancelado (estado terminal)
        (OrderStatus.CANCELLED, OrderStatus.RECEIVED, False),
        (OrderStatus.CANCELLED, OrderStatus.PREPARED, False),
        (OrderStatus.CANCELLED, OrderStatus.PICKED_UP, False),
        (OrderStatus.CANCELLED, OrderStatus.CANCELLED, False),
    ],
)
def test_transiciones_validas_e_invalidas(
    actual: OrderStatus, nuevo: OrderStatus, esperado: bool
):
    """RF-20: verifica las 16 transiciones posibles de la máquina de estados de una orden."""
    assert can_transition(actual, nuevo) is esperado


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Recibido", OrderStatus.RECEIVED),
        ("recibido", OrderStatus.RECEIVED),
        (" RECIBIDO ", OrderStatus.RECEIVED),
        ("Preparado", OrderStatus.PREPARED),
        (" preparado ", OrderStatus.PREPARED),
        ("PREPARADO", OrderStatus.PREPARED),
        ("Retirado", OrderStatus.PICKED_UP),
        ("retirado", OrderStatus.PICKED_UP),
        ("Despachado", OrderStatus.DISPATCHED),
        ("despachado", OrderStatus.DISPATCHED),
        ("Entregado", OrderStatus.DELIVERED),
        ("entregado", OrderStatus.DELIVERED),
        ("Cancelado", OrderStatus.CANCELLED),
        ("cancelado", OrderStatus.CANCELLED),
    ],
)
def test_parse_status_acepta_mayusculas_y_espacios(texto: str, esperado: OrderStatus):
    """RF-20: parse_status tolera mayúsculas y espacios en blanco."""
    assert parse_status(texto) == esperado


@pytest.mark.parametrize(
    "invalido",
    [
        "Preparada",
        "En preparación",
        "Enviado",
        "Terminado",
        "",
        "   ",
        "otro",
    ],
)
def test_parse_status_rechaza_desconocidos(invalido: str):
    """RF-20: parse_status rechaza valores que no pertenezcan al enum."""
    with pytest.raises(ValueError):
        parse_status(invalido)


def test_order_model_valida_order_id():
    """RF-17: Order rechaza un order_id mal formado al instanciarse."""
    from datetime import datetime

    from internal.schemas.order_schema import Channel, OrderItem

    item = OrderItem(sku="SH-400", product_name="Pantene", quantity=1)

    # Caso válido
    order = Order(
        order_id="ORD-0001",
        draft_id="d-1",
        created_at=datetime.now(),
        customer_id="c-1",
        channel=Channel.WEB,
        items=[item],
    )
    assert order.order_id == "ORD-0001"

    # Caso inválido
    with pytest.raises(ValidationError):
        Order(
            order_id="ORD-1",
            draft_id="d-1",
            created_at=datetime.now(),
            customer_id="c-1",
            channel=Channel.WEB,
            items=[item],
        )
