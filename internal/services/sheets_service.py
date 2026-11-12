"""Acceso a Google Sheets: Sheet 1 (catálogo y stock, lectura) y Sheet 2 (órdenes, escritura).

Es la única capa que conoce la fuente del catálogo y el destino de las órdenes;
al migrar a la API del sistema de gestión real se reemplaza este servicio sin tocar agente ni tools.

- Catálogo: con `CATALOG_SHEET_ID` y `GOOGLE_CREDENTIALS_FILE` se lee de Sheet 1, con caché (RNF-13, RF-50, RF-51).
  Sin ellos, del archivo de `Settings.catalog_file` (por defecto `data_example/catalogo.csv`), con fallback a
  este último si el archivo configurado no existe (CA-53).
- Órdenes: se vuelcan en `Settings.orders_file` (por defecto `data/pedidos.csv`) y, con `ORDERS_SHEET_ID`,
  también en Sheet 2, bajo un bloqueo de archivo que comparten la API y el bot (RF-19, RF-52).

Detalle en docs/spec/planillas.md.
"""

import csv
import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from internal.config import get_settings
from internal.schemas.catalog_schema import Product
from internal.schemas.order_schema import Order, OrderItem
from internal.services import google_sheets_client

try:  # no existe en Windows: ahí el bloqueo de pedidos es solo entre hilos del mismo proceso
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
FALLBACK_CATALOG_CSV = ROOT_DIR / "data_example" / "catalogo.csv"

# Las columnas nuevas van al final: un pedidos.csv de la 0.1.1 sigue alineado con su encabezado
ORDER_COLUMNS = [
    "id_orden", "fecha_hora", "cliente", "canal", "productos", "notas", "estado", "nombre_cliente", "total"
]
AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")

# Caché del catálogo de Sheet 1 (RNF-13, RF-51). Son parámetros, no secretos: van en el código (RNF-10)
CATALOG_TTL_SECONDS = 5 * 60  # una copia sirve 5 minutos para buscar
CATALOG_MAX_STALE_SECONDS = 60 * 60  # si Google no responde, la última copia buena sirve hasta 1 hora
CATALOG_RETRY_AFTER_FAILURE_SECONDS = 60  # tras una falla, no se vuelve a esperar a Google en cada búsqueda
ORDERS_PAUSE_AFTER_FAILURE_SECONDS = 60  # tras una falla de Sheet 2, la confirmación no espera a Google

CATALOG_REQUIRED_COLUMNS = ("sku", "nombre", "precio", "stock")


class CatalogoNoDisponibleError(OSError):
    """No se pudo leer Sheet 1 y no hay una copia buena de la última hora.

    Es un `OSError` para que la tool responda `catalogo_no_disponible`, como con cualquier falla de lectura.
    """


def get_catalog_path() -> Path:
    """Devuelve la ruta al catálogo configurado, o el fallback si no existe."""
    configured = Path(get_settings().catalog_file)
    if not configured.is_absolute():
        configured = ROOT_DIR / configured
    if configured.is_file():
        return configured
    if FALLBACK_CATALOG_CSV.is_file():
        return FALLBACK_CATALOG_CSV
    raise FileNotFoundError(f"No se encontró el catálogo en {configured} ni en {FALLBACK_CATALOG_CSV}")


def catalog_from_sheet() -> bool:
    """El catálogo sale de Sheet 1 solo con `CATALOG_SHEET_ID` y `GOOGLE_CREDENTIALS_FILE` (RF-50)."""
    settings = get_settings()
    return bool(settings.catalog_sheet_id and settings.google_credentials_file)


def orders_to_sheet() -> bool:
    """Las órdenes van a Sheet 2 solo con `ORDERS_SHEET_ID` y `GOOGLE_CREDENTIALS_FILE` (RF-52)."""
    settings = get_settings()
    return bool(settings.orders_sheet_id and settings.google_credentials_file)


def _product_from_row(row: dict[str, Any]) -> Product:
    return Product(
        sku=row["sku"],
        name=row["nombre"],
        brand=row.get("marca") or None,
        presentation=row.get("presentacion") or None,
        category=row.get("categoria") or None,
        price=row["precio"],
        stock=row.get("stock") or 0,
        min_stock=row.get("stock_minimo") or 0,
    )


def _read_catalog_csv() -> list[Product]:
    path = get_catalog_path()
    with open(path, encoding="utf-8", newline="") as file:
        return [_product_from_row(row) for row in csv.DictReader(file)]


def _cell_text(value: Any) -> str:
    """Valor de una celda leída sin formato: `4200.0` -> `"4200"`, `None` -> `""`."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def read_catalog_sheet() -> list[Product]:
    """Lee Sheet 1 sin caché. Las filas vacías se ignoran y las inválidas se saltean con un aviso en el log."""
    settings = get_settings()
    rows = google_sheets_client.get_client().get_values(settings.catalog_sheet_id, settings.catalog_worksheet)
    if not rows:
        raise ValueError("Sheet 1 está vacía: falta el encabezado")
    header = [_cell_text(cell).lower() for cell in rows[0]]
    missing = [column for column in CATALOG_REQUIRED_COLUMNS if column not in header]
    if missing:
        raise ValueError(f"A Sheet 1 le faltan las columnas {missing}")

    products = []
    for number, values in enumerate(rows[1:], start=2):
        texts = [_cell_text(cell) for cell in values]
        if not any(texts):
            continue
        row = dict(zip(header, texts + [""] * (len(header) - len(texts)), strict=False))
        try:
            products.append(_product_from_row(row))
        except ValidationError:
            # Sin los valores: el log no lleva datos del catálogo, solo dónde mirar
            logger.warning("Sheet 1: la fila %d tiene un precio o un stock inválido y se saltea", number)
    return products


@dataclass
class _CatalogCopy:
    key: tuple[str | None, str | None]  # planilla y hoja: si cambia la configuración, la copia no sirve
    products: list[Product]
    read_at: float


_copy: _CatalogCopy | None = None
_last_failure_at: float | None = None
_catalog_lock = threading.Lock()
_clock = time.monotonic  # los tests lo reemplazan para no esperar
_fresh_read: ContextVar[bool] = ContextVar("lectura_fresca_del_catalogo", default=False)


def reset_catalog_cache() -> None:
    """Descarta la copia del catálogo (tests y cambios de configuración)."""
    global _copy, _last_failure_at
    with _catalog_lock:
        _copy = None
        _last_failure_at = None


@contextmanager
def lectura_fresca(activa: bool = True) -> Iterator[None]:
    """Dentro del bloque, `get_catalog()` lee Sheet 1 aunque la copia sea reciente (RNF-13: al confirmar)."""
    token = _fresh_read.set(activa)
    try:
        yield
    finally:
        _fresh_read.reset(token)


def _catalog_from_sheet_cached(fresh: bool) -> list[Product]:
    global _copy, _last_failure_at
    settings = get_settings()
    key = (settings.catalog_sheet_id, settings.catalog_worksheet)
    with _catalog_lock:  # una sola lectura a la vez: diez búsquedas juntas no son diez llamadas a Google
        now = _clock()
        copy = _copy if _copy and _copy.key == key else None
        age = now - copy.read_at if copy else None
        if copy and not fresh:
            since_failure = now - _last_failure_at if _last_failure_at is not None else None
            recently_failed = since_failure is not None and since_failure < CATALOG_RETRY_AFTER_FAILURE_SECONDS
            if age < CATALOG_TTL_SECONDS or (recently_failed and age < CATALOG_MAX_STALE_SECONDS):
                return list(copy.products)
        try:
            products = read_catalog_sheet()
        except Exception as exc:
            _last_failure_at = now
            if copy and age < CATALOG_MAX_STALE_SECONDS:
                logger.warning(
                    "Sheet 1 no responde (%s): se usa la copia del catálogo de hace %d s",
                    type(exc).__name__,
                    age,
                )
                return list(copy.products)
            raise CatalogoNoDisponibleError(
                f"No se pudo leer Sheet 1 ({type(exc).__name__}: {exc}) y no hay una copia de la última hora"
            ) from exc
        _copy = _CatalogCopy(key=key, products=products, read_at=now)
        _last_failure_at = None
        return list(products)


def get_catalog() -> list[Product]:
    """Catálogo base: Sheet 1 con caché si está configurada (RF-50, RF-51), o el CSV de siempre."""
    if not catalog_from_sheet():
        return _read_catalog_csv()
    return _catalog_from_sheet_cached(fresh=_fresh_read.get())


def get_orders_path() -> Path:
    """Devuelve la ruta al archivo de órdenes configurado."""
    configured = Path(get_settings().orders_file)
    if not configured.is_absolute():
        configured = ROOT_DIR / configured
    return configured


def _product_line(item: OrderItem) -> str:
    label = " ".join(filter(None, (item.product_name, item.presentation)))
    # `; ` separa las líneas: dentro de una nota se escribe `,` para no partirla
    note = f" [{item.note.replace(';', ',')}]" if item.note else ""
    return f"{item.quantity} x {label} ({item.sku}){note}"


def format_order_row(order: Order) -> list[str]:
    """Formatea una orden según las columnas del Sheet 2 (`ORDER_COLUMNS`, docs/spec/guardado-de-pedidos.md §4.5)."""
    created_at = order.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    fecha_hora = created_at.astimezone(AR_TZ).isoformat()

    canal = order.channel.value if hasattr(order.channel, "value") else str(order.channel)
    estado = order.status.value if hasattr(order.status, "value") else str(order.status)

    return [
        order.order_id,
        fecha_hora,
        order.customer_id,
        canal,
        "; ".join(_product_line(item) for item in order.items),
        order.notes or "",
        estado,
        order.customer_name or "",
        f"{order.total:.2f}",
    ]


_orders_thread_lock = threading.Lock()


@contextmanager
def _orders_lock() -> Iterator[None]:
    """Bloqueo de pedidos: entre hilos y, con `fcntl`, entre la API y el bot, que comparten `data/` (RF-52)."""
    path = get_orders_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _orders_thread_lock, open(path.with_name(path.name + ".lock"), "a") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file, fcntl.LOCK_EX)  # se suelta al cerrar el archivo
        yield


def _append_order_csv(order: Order) -> None:
    path = get_orders_path()
    file_exists = path.is_file() and path.stat().st_size > 0
    if file_exists:
        with open(path, mode="r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            _header = next(reader, None)
            for row in reader:
                if row and row[0] == order.order_id:
                    return

    with open(path, mode="a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(ORDER_COLUMNS)
        writer.writerow(format_order_row(order))


def _append_order_sheet(order: Order) -> None:
    settings = get_settings()
    client = google_sheets_client.get_client()
    sheet_id, worksheet = settings.orders_sheet_id, settings.orders_worksheet
    ids = [_cell_text(value) for value in client.get_column(sheet_id, worksheet, 1)]
    if order.order_id in ids:  # RNF-04: un reintento después de una escritura que sí llegó no la repite
        return
    if not any(ids):
        client.append_row(sheet_id, worksheet, list(ORDER_COLUMNS))
    client.append_row(sheet_id, worksheet, format_order_row(order))


_orders_failure_at: float | None = None  # se lee y escribe con el bloqueo de pedidos tomado


class Sheet2EnPausaError(OSError):
    """Sheet 2 falló hace menos de `ORDERS_PAUSE_AFTER_FAILURE_SECONDS`: la orden queda para el reintento."""


def reset_orders_pause() -> None:
    """Olvida la última falla de Sheet 2 (tests)."""
    global _orders_failure_at
    with _orders_thread_lock:
        _orders_failure_at = None


def append_order(order: Order, *, reintento: bool = False) -> None:
    """Agrega una fila por pedido en el archivo local y, si está configurada, en Sheet 2 (RF-19, RF-52, RNF-04).

    Es idempotente: si la orden ya está, no la duplica. Si Sheet 2 falla, lanza la excepción y la orden queda
    pendiente para `order_service.sync_pending_orders()`; la fila del CSV ya escrita no se repite al reintentar.

    Tras una falla de Sheet 2, durante `ORDERS_PAUSE_AFTER_FAILURE_SECONDS` la confirmación no llama a Google:
    escribe el CSV y lanza `Sheet2EnPausaError`, así el cliente no espera el timeout en cada pedido. El reintento
    (`reintento=True`) prueba siempre: si anda, se levanta la pausa; si falla, empieza otra.
    """
    global _orders_failure_at
    with _orders_lock():
        _append_order_csv(order)
        if not orders_to_sheet():
            return
        now = _clock()
        if not reintento and _orders_failure_at is not None:
            since_failure = now - _orders_failure_at
            if since_failure < ORDERS_PAUSE_AFTER_FAILURE_SECONDS:
                raise Sheet2EnPausaError(
                    f"Sheet 2 falló hace {since_failure:.0f} s: la orden {order.order_id} queda para el reintento"
                )
        try:
            _append_order_sheet(order)
        except Exception:
            _orders_failure_at = now
            raise
        _orders_failure_at = None


def check_orders_sheet() -> int:
    """Prueba leer y editar Sheet 2 sin tocar los pedidos; devuelve cuántos tiene (`verificar_claves`).

    Si la hoja está vacía escribe el encabezado; si ya lo tiene, reescribe `id_orden` en A1 con el mismo valor.
    """
    settings = get_settings()
    client = google_sheets_client.get_client()
    sheet_id, worksheet = settings.orders_sheet_id, settings.orders_worksheet
    ids = [_cell_text(value) for value in client.get_column(sheet_id, worksheet, 1)]
    if not any(ids):
        client.append_row(sheet_id, worksheet, list(ORDER_COLUMNS))
        return 0
    if ids[0] != ORDER_COLUMNS[0]:
        raise ValueError(
            f"La celda A1 de Sheet 2 no es {ORDER_COLUMNS[0]!r}: ¿ORDERS_SHEET_ID es la planilla de pedidos?"
        )
    client.update_cell(sheet_id, worksheet, 1, 1, ORDER_COLUMNS[0])
    return len([value for value in ids[1:] if value])
