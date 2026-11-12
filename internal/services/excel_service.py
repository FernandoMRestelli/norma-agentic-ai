"""Exportación de las órdenes a Excel para el operador (RF-54, docs/spec/planillas.md §3.4).

Dos hojas: «Órdenes», con las mismas columnas que Sheet 2 (`ORDER_COLUMNS` y `format_order_row`), y «Líneas»,
una fila por producto de cada orden. Los textos se escriben como texto: un `=` del cliente no es una fórmula.
"""

from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from internal.schemas.order_schema import Order
from internal.services.sheets_service import ORDER_COLUMNS, format_order_row

ORDERS_SHEET_TITLE = "Órdenes"
LINES_SHEET_TITLE = "Líneas"
LINE_COLUMNS = ["id_orden", "sku", "producto", "presentacion", "cantidad", "precio_unitario", "subtotal"]
MAX_COLUMN_WIDTH = 60


def _write_row(ws: Worksheet, row: int, values: list[Any]) -> None:
    for col, value in enumerate(values, start=1):
        cell = ws.cell(row=row, column=col, value=value)
        if isinstance(value, str):
            cell.data_type = "s"  # openpyxl toma como fórmula cualquier texto que empieza con «=»


def _write_sheet(ws: Worksheet, header: list[str], rows: list[list[Any]]) -> None:
    _write_row(ws, 1, header)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for number, values in enumerate(rows, start=2):
        _write_row(ws, number, values)
    ws.freeze_panes = "A2"
    for col in range(1, len(header) + 1):
        longest = max((len(str(ws.cell(row=r, column=col).value or "")) for r in range(1, len(rows) + 2)), default=0)
        ws.column_dimensions[get_column_letter(col)].width = min(MAX_COLUMN_WIDTH, longest + 2)


def _line_rows(order: Order) -> list[list[Any]]:
    return [
        [
            order.order_id,
            item.sku,
            item.product_name,
            item.presentation or "",
            item.quantity,
            item.unit_price,
            item.unit_price * item.quantity if item.unit_price is not None else None,
        ]
        for item in order.items
    ]


def build_orders_workbook(orders: list[Order]) -> bytes:
    """Arma el `.xlsx` con las hojas Órdenes y Líneas y lo devuelve en bytes."""
    workbook = Workbook()
    orders_ws = workbook.active
    orders_ws.title = ORDERS_SHEET_TITLE
    _write_sheet(orders_ws, list(ORDER_COLUMNS), [format_order_row(order) for order in orders])

    lines_ws = workbook.create_sheet(LINES_SHEET_TITLE)
    _write_sheet(lines_ws, LINE_COLUMNS, [line for order in orders for line in _line_rows(order)])

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
