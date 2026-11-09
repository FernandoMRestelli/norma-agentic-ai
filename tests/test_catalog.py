from decimal import Decimal

import pytest
from pydantic import ValidationError

from internal.schemas.catalog_schema import Product, StockCheck


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


def test_get_catalog_configurable_y_fallback(monkeypatch, tmp_path):
    """RF-07, RNF-13: sheets_service carga el catálogo del archivo configurado o fallback."""
    from internal.config import get_settings
    from internal.services import sheets_service

    # Crear catálogo temporal
    custom_csv = tmp_path / "custom_catalogo.csv"
    custom_csv.write_text(
        "sku,nombre,marca,presentacion,precio,stock,stock_minimo\n"
        "CUSTOM-01,Producto Custom,MarcaX,100 ml,500.00,10,2\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("CATALOG_FILE", str(custom_csv))
    get_settings.cache_clear()
    products = sheets_service.get_catalog()
    assert len(products) == 1
    assert products[0].sku == "CUSTOM-01"

    # Archivo inexistente -> fallback al catálogo de ejemplo
    monkeypatch.setenv("CATALOG_FILE", str(tmp_path / "inexistente.csv"))
    get_settings.cache_clear()
    fallback_products = sheets_service.get_catalog()
    assert any(p.sku == "SH-PAN-400" for p in fallback_products)

    get_settings.cache_clear()

def test_get_catalog_stock_vacio_default_cero(monkeypatch, tmp_path):
    """RF-07: stock y stock_mínimo vacíos en el CSV se cargan como 0."""
    from internal.config import get_settings
    from internal.services import sheets_service

    custom_csv = tmp_path / "custom_stock_vacio.csv"
    custom_csv.write_text(
        "sku,nombre,marca,presentacion,precio,stock,stock_minimo\n"
        "EMPTY-01,Producto Vacio,MarcaX,100 ml,500.00,,\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("CATALOG_FILE", str(custom_csv))
    get_settings.cache_clear()
    products = sheets_service.get_catalog()
    assert len(products) == 1
    assert products[0].sku == "EMPTY-01"
    assert products[0].stock == 0
    assert products[0].min_stock == 0

    get_settings.cache_clear()


def test_get_catalog_path_not_found(monkeypatch, tmp_path):
    """RF-07: si ni el archivo configurado ni el fallback existen, lanza FileNotFoundError."""
    import pytest

    from internal.config import get_settings
    from internal.services import sheets_service

    monkeypatch.setenv("CATALOG_FILE", str(tmp_path / "inexistente.csv"))
    monkeypatch.setattr(sheets_service, "FALLBACK_CATALOG_CSV", tmp_path / "tampoco_existe.csv")
    get_settings.cache_clear()

    with pytest.raises(FileNotFoundError):
        sheets_service.get_catalog_path()

    get_settings.cache_clear()

