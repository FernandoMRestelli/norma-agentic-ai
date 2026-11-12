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
    "id_orden", "fecha_hora", "cliente", "canal", "productos", "notas", "estado", "nombre_cliente", "total"
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

    if expected["resultado"] in ("borrador", "pedido"):
        assert expected["items"]
    if expected["resultado"] in ("pedido", "sin_pedido"):
        assert case.get("previo"), "Aceptar o rechazar necesita una propuesta del turno anterior"
    if expected["resultado"] == "aclaracion":
        assert len(expected["opciones"]) > 1
    if expected["resultado"] == "sin_stock":
        assert any(item["cantidad"] > catalog[item["sku"]].stock for item in expected["items"])
