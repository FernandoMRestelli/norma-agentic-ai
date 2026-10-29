"""Propiedades que valen para todos los canales y tipos de mensaje a la vez.

Se parametrizan sobre los enums y no sobre una lista escrita a mano: un canal o tipo nuevo
queda cubierto sin tocar este archivo.
"""

import pytest

from backend.schemas.catalog_schema import StockCheck
from backend.schemas.order_schema import Channel, DraftStatus, MessageType
from backend.services.message_service import normalize_text
from backend.services.validation_service import build_summary
from tests import datos_sinteticos as ds


@pytest.mark.parametrize("canal", list(Channel))
@pytest.mark.parametrize("tipo", list(MessageType))
def test_todo_tipo_de_mensaje_es_valido_en_todo_canal(canal, tipo):
    assert ds.mensaje(tipo, canal).channel == canal


@pytest.mark.parametrize("canal", list(Channel))
def test_un_borrador_nace_sin_confirmar(canal):
    # Interpretado no es confirmado: ninguna orden se registra sin el OK explícito del cliente
    assert ds.borrador(ds.catalogo(), canal=canal).status == DraftStatus.PENDING_CONFIRMATION


@pytest.mark.parametrize("canal", list(Channel))
@pytest.mark.parametrize("items", [1, 3, 10])
def test_el_resumen_muestra_cada_item_del_borrador(canal, items):
    draft = ds.borrador(ds.catalogo(), items=items, canal=canal)
    summary = build_summary(draft)
    for item in draft.items:
        assert f"{item.quantity} x {item.product_name} {item.presentation}" in summary


def test_el_resumen_no_depende_de_productos_ajenos_al_borrador():
    catalogo = ds.catalogo()
    draft = ds.borrador(catalogo)
    ajeno = next(p for p in catalogo if p.sku not in {i.sku for i in draft.items})
    assert build_summary(draft) == build_summary(ds.borrador(ds.sin_stock(catalogo, ajeno.sku)))


def test_normalizar_texto_es_idempotente():
    texto = "  5 shampoos   Pantene\n\t por favor "
    assert normalize_text(normalize_text(texto)) == normalize_text(texto)


@pytest.mark.parametrize("producto", ds.catalogo(), ids=lambda p: p.sku)
def test_si_alcanza_el_stock_tambien_alcanza_para_menos(producto):
    for pedido in range(1, producto.stock + 1):
        assert StockCheck(sku=producto.sku, requested=pedido, available=producto.stock).is_enough
    assert not StockCheck(sku=producto.sku, requested=producto.stock + 1, available=producto.stock).is_enough


def test_sin_stock_solo_agota_el_producto_indicado():
    catalogo = ds.catalogo()
    agotado = ds.sin_stock(catalogo, catalogo[0].sku)
    assert agotado[0].stock == 0
    assert agotado[1:] == catalogo[1:]


def test_los_datos_sinteticos_son_deterministas():
    assert ds.catalogo() == ds.catalogo()
    assert ds.borrador(ds.catalogo()) == ds.borrador(ds.catalogo())
