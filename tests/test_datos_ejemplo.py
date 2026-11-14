"""Verifica que los datos de ejemplo respeten los esquemas y sean coherentes entre sí."""

import csv
import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from internal.schemas.catalog_schema import Product
from internal.schemas.order_schema import IncomingMessage, Order, OrderItem

DATA_DIR = Path(__file__).resolve().parent.parent / "data_example"

# Columnas de la planilla -> campos del esquema
CATALOG_COLUMNS = {
    "sku": "sku",
    "nombre": "name",
    "marca": "brand",
    "presentacion": "presentation",
    "categoria": "category",
    "precio": "price",
    "stock": "stock",
    "stock_minimo": "min_stock",
}
ORDER_COLUMNS = [
    "id_orden", "fecha_hora", "cliente", "canal", "productos", "notas", "estado", "nombre_cliente", "total",
    "lineas", "unidades", "fuera_del_pedido", "origen", "draft_id", "actualizado",
]
LINE_COLUMNS = [
    "id_orden", "renglon", "sku", "producto", "presentacion", "marca", "categoria", "cantidad", "precio_unitario",
    "subtotal", "nota", "coincidencia", "pedido_como",
]
ORDER_LINE = re.compile(r"^(?P<quantity>\d+) x (?P<label>.+) \((?P<sku>[A-Z0-9-]+)\)( \[(?P<note>[^\]]+)\])?$")
EXPECTED_RESULTS = {"borrador", "aclaracion", "sin_stock", "fuera_de_catalogo", "informacion", "pedido", "sin_pedido"}


def read_csv(name: str) -> list[dict[str, str]]:
    with open(DATA_DIR / name, encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


@pytest.fixture(scope="module")
def catalog() -> dict[str, Product]:
    products = [
        Product(**{field: row[column] or None for column, field in CATALOG_COLUMNS.items()})
        for row in read_csv("catalogo.csv")
    ]
    return {product.sku: product for product in products}


def parse_order(row: dict[str, str], catalog: dict[str, Product]) -> Order:
    items = []
    for line in row["productos"].split(";"):
        match = ORDER_LINE.match(line.strip())
        assert match, f"Línea de producto mal formada: {line!r}"
        product = catalog[match["sku"]]
        items.append(
            OrderItem(
                sku=product.sku,
                product_name=product.name,
                presentation=product.presentation,
                quantity=int(match["quantity"]),
                unit_price=product.price,
                note=match["note"],
            )
        )
    return Order(
        order_id=row["id_orden"],
        draft_id=f"draft-{row['id_orden']}",
        created_at=datetime.fromisoformat(row["fecha_hora"]),
        customer_id=row["cliente"],
        channel=row["canal"],
        items=items,
        notes=row["notas"] or None,
        status=row["estado"],
        customer_name=row["nombre_cliente"] or None,
    )


def test_catalog_has_expected_columns():
    with open(DATA_DIR / "catalogo.csv", encoding="utf-8", newline="") as file:
        assert next(csv.reader(file)) == list(CATALOG_COLUMNS)


def test_catalog_skus_are_unique(catalog):
    assert len(catalog) == len(read_csv("catalogo.csv"))


def test_catalog_covers_stock_edge_cases(catalog):
    assert any(product.stock == 0 for product in catalog.values())
    assert any(product.is_low_stock for product in catalog.values())


def test_orders_match_schema_and_catalog(catalog):
    with open(DATA_DIR / "pedidos.csv", encoding="utf-8", newline="") as file:
        assert next(csv.reader(file)) == ORDER_COLUMNS

    rows = read_csv("pedidos.csv")
    orders = [parse_order(row, catalog) for row in rows]
    assert len({order.order_id for order in orders}) == len(orders)
    assert all(order.created_at.tzinfo is not None for order in orders)
    # El total de la planilla es el de las líneas con los precios del catálogo
    assert [row["total"] for row in rows] == [f"{order.total:.2f}" for order in orders]
    assert [int(row["lineas"]) for row in rows] == [len(order.items) for order in orders]
    assert [int(row["unidades"]) for row in rows] == [sum(i.quantity for i in order.items) for order in orders]
    assert all(row["origen"] in ("texto", "audio", "imagen") for row in rows)
    actualizados = [datetime.fromisoformat(row["actualizado"]) for row in rows]
    assert all(momento >= order.created_at for momento, order in zip(actualizados, orders, strict=True))


def test_order_lines_match_orders_and_catalog(catalog):
    """`pedidos_lineas.csv` tiene una fila por línea de cada orden de `pedidos.csv`, con los datos del catálogo."""
    from decimal import Decimal

    with open(DATA_DIR / "pedidos_lineas.csv", encoding="utf-8", newline="") as file:
        assert next(csv.reader(file)) == LINE_COLUMNS
    lineas = read_csv("pedidos_lineas.csv")
    orders = {row["id_orden"]: parse_order(row, catalog) for row in read_csv("pedidos.csv")}

    assert {linea["id_orden"] for linea in lineas} == set(orders)
    for order_id, order in orders.items():
        propias = [linea for linea in lineas if linea["id_orden"] == order_id]
        assert [int(linea["renglon"]) for linea in propias] == list(range(1, len(order.items) + 1))
        for linea, item in zip(propias, order.items, strict=True):
            product = catalog[linea["sku"]]
            assert (linea["sku"], int(linea["cantidad"]), linea["nota"] or None) == (item.sku, item.quantity, item.note)
            assert (linea["producto"], linea["marca"], linea["categoria"]) == (
                product.name, product.brand or "", product.category or ""
            )
            assert Decimal(linea["subtotal"]) == item.quantity * product.price
            assert linea["coincidencia"] in ("exacta", "aproximada")
        assert sum(Decimal(linea["subtotal"]) for linea in propias) == order.total


def load_cases() -> list[dict]:
    return json.loads((DATA_DIR / "mensajes.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", load_cases(), ids=lambda case: case["id"])
def test_message_cases_are_consistent(case, catalog):
    IncomingMessage(**case["mensaje"])
    expected = case["esperado"]
    assert expected["resultado"] in EXPECTED_RESULTS

    for sku in expected.get("opciones", []):
        assert sku in catalog
    for item in expected.get("items", []):
        assert item["sku"] in catalog
        assert item["cantidad"] > 0

    previo = case.get("previo")
    if previo is not None:  # un texto o una lista de textos, en orden (CA-112)
        turnos = [previo] if isinstance(previo, str) else previo
        assert turnos and all(isinstance(t, str) and t.strip() for t in turnos)

    if expected["resultado"] in ("borrador", "pedido"):
        assert expected["items"]
    if expected["resultado"] in ("pedido", "sin_pedido"):
        assert case.get("previo"), "Aceptar o rechazar necesita una propuesta del turno anterior"
    if expected["resultado"] == "aclaracion":
        assert len(expected["opciones"]) > 1
    if expected["resultado"] == "sin_stock":
        assert any(item["cantidad"] > catalog[item["sku"]].stock for item in expected["items"])


def test_hay_casos_de_varios_turnos():
    """CA-112: la evaluación tiene conversaciones, no solo mensajes sueltos."""
    listas = [case["previo"] for case in load_cases() if isinstance(case.get("previo"), list)]
    assert len(listas) >= 5
    assert any(len(previo) >= 3 for previo in listas)
