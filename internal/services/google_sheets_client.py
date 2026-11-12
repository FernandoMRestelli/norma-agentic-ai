"""Cliente mínimo de Google Sheets: lo único de `gspread` que usa `sheets_service` (docs/spec/planillas.md §4.2).

`sheets_service` nunca importa `gspread`: llama a `get_client()`, que los tests reemplazan por un cliente falso
sin red. `gspread` se importa recién al crear el cliente real, así sin planillas configuradas no se carga.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Protocol

from internal.config import get_settings

ROOT_DIR = Path(__file__).resolve().parent.parent.parent

# Solo planillas: no hace falta habilitar ni dar permiso sobre Drive
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
TIMEOUT_SECONDS = 10  # por llamada; una búsqueda o una confirmación no pueden quedar colgadas de Google


class SheetsClient(Protocol):
    """Operaciones sobre una hoja (`worksheet`) de una planilla. `worksheet=None` es la primera hoja."""

    service_account_email: str | None

    def get_values(self, spreadsheet_id: str, worksheet: str | None) -> list[list[Any]]:
        """Todas las filas, con los números como números (sin el formato de la celda)."""
        ...

    def get_column(self, spreadsheet_id: str, worksheet: str | None, col: int) -> list[Any]:
        """Los valores de una columna (la 1 es la A), desde la fila 1."""
        ...

    def append_row(self, spreadsheet_id: str, worksheet: str | None, values: list[str]) -> None:
        """Agrega una fila debajo de la tabla, sin interpretar los valores (un `=` no es una fórmula)."""
        ...

    def update_cell(self, spreadsheet_id: str, worksheet: str | None, row: int, col: int, value: str) -> None:
        """Escribe una celda, sin interpretar el valor."""
        ...


class GspreadClient:
    """`SheetsClient` sobre `gspread`, autenticado con la cuenta de servicio de `GOOGLE_CREDENTIALS_FILE`."""

    def __init__(self, credentials_file: str | Path):
        import gspread

        self._gspread = gspread
        self._gc = gspread.service_account(filename=str(credentials_file), scopes=SCOPES)
        self._gc.set_timeout(TIMEOUT_SECONDS)

    @property
    def service_account_email(self) -> str | None:
        return getattr(self._gc.http_client.auth, "service_account_email", None)

    def _worksheet(self, spreadsheet_id: str, worksheet: str | None):
        spreadsheet = self._gc.open_by_key(spreadsheet_id)
        return spreadsheet.worksheet(worksheet) if worksheet else spreadsheet.get_worksheet(0)

    def get_values(self, spreadsheet_id: str, worksheet: str | None) -> list[list[Any]]:
        ws = self._worksheet(spreadsheet_id, worksheet)
        return ws.get_all_values(value_render_option=self._gspread.utils.ValueRenderOption.unformatted)

    def get_column(self, spreadsheet_id: str, worksheet: str | None, col: int) -> list[Any]:
        return self._worksheet(spreadsheet_id, worksheet).col_values(col)

    def append_row(self, spreadsheet_id: str, worksheet: str | None, values: list[str]) -> None:
        ws = self._worksheet(spreadsheet_id, worksheet)
        # table_range="A1": la fila va debajo de la tabla que empieza en A1, aunque haya celdas sueltas más a la derecha
        ws.append_row(values, value_input_option=self._gspread.utils.ValueInputOption.raw, table_range="A1")

    def update_cell(self, spreadsheet_id: str, worksheet: str | None, row: int, col: int, value: str) -> None:
        ws = self._worksheet(spreadsheet_id, worksheet)
        a1 = self._gspread.utils.rowcol_to_a1(row, col)
        ws.update([[value]], range_name=a1, value_input_option=self._gspread.utils.ValueInputOption.raw)


def get_credentials_path() -> Path | None:
    """Ruta absoluta al JSON de la cuenta de servicio, o None si `GOOGLE_CREDENTIALS_FILE` está vacío."""
    configured = get_settings().google_credentials_file
    if not configured:
        return None
    path = Path(configured)
    return path if path.is_absolute() else ROOT_DIR / path


_client: GspreadClient | None = None
_client_path: Path | None = None
_lock = threading.Lock()


def get_client() -> SheetsClient:
    """El cliente real, creado una sola vez por ruta de credenciales. Falla si el JSON no existe o no sirve."""
    global _client, _client_path
    path = get_credentials_path()
    if path is None:
        raise FileNotFoundError("Falta GOOGLE_CREDENTIALS_FILE para usar Google Sheets")
    with _lock:
        if _client is None or _client_path != path:
            _client = GspreadClient(path)
            _client_path = path
        return _client
