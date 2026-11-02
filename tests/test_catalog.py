from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.schemas.catalog_schema import Product, StockCheck


def test_product_rechaza_stock_negativo():
    """RF-14, RF-10: el stock de un producto no puede ser negativo."""
    with pytest.raises(ValidationError):
        Product(sku="A", name="x", price=Decimal("1"), stock=-5, min_stock=0)


@pytest.mark.parametrize("requested", [0, -3])
def test_stock_check_rechaza_cantidad_pedida_no_positiva(requested):
    """RF-10: la cantidad solicitada en StockCheck debe ser estrictamente positiva (> 0)."""
    with pytest.raises(ValidationError):
        StockCheck(sku="A", requested=requested, available=5)


@pytest.mark.parametrize("available", [-1, -9])
def test_stock_check_rechaza_disponible_negativo(available):
    """StockCheck no acepta disponibilidad negativa."""
    with pytest.raises(ValidationError):
        StockCheck(sku="A", requested=1, available=available)


def test_stock_check_is_enough():
    """Caso feliz: evalúa correctamente si hay suficiente stock disponible."""
    assert StockCheck(sku="A", requested=5, available=5).is_enough is True
    assert StockCheck(sku="A", requested=6, available=5).is_enough is False
