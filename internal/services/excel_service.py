"""Exportación de las órdenes a Excel para el operador (RF-54, docs/spec/planillas.md §3.4).

Dos hojas: «Órdenes», con las mismas columnas que Sheet 2 (`ORDER_COLUMNS` y `format_order_row`), y «Líneas»,
una fila por producto de cada orden, con las de `pedidos_lineas.csv` (`LINE_COLUMNS`, RF-75) y la nota de cada
línea (RF-78). Los textos se escriben como texto: un `=` del cliente no es una fórmula.
"""

from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from internal.schemas.order_schema import Order
from internal.services.sheets_service import LINE_COLUMNS, ORDER_COLUMNS, format_line_rows, format_order_row

ORDERS_SHEET_TITLE = "Órdenes"
LINES_SHEET_TITLE = "Líneas"
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


def build_orders_workbook(orders: list[Order]) -> bytes:
    """Arma el `.xlsx` con las hojas Órdenes y Líneas y lo devuelve en bytes."""
    workbook = Workbook()
    orders_ws = workbook.active
    orders_ws.title = ORDERS_SHEET_TITLE
    _write_sheet(orders_ws, list(ORDER_COLUMNS), [format_order_row(order) for order in orders])

    lines_ws = workbook.create_sheet(LINES_SHEET_TITLE)
    _write_sheet(lines_ws, list(LINE_COLUMNS), [line for order in orders for line in format_line_rows(order)])

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
