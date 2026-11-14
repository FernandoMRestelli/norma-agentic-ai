"""Actualiza el esquema de una base existente al arrancar (RF-49, docs/spec/guardado-de-pedidos.md §3.3).

Mecanismo propio y no Alembic (D-C1): una tabla `schema_version` y pasos numerados que corren en orden dentro de
una sola transacción `BEGIN IMMEDIATE`. Si la API y el bot arrancan a la vez, el segundo espera y encuentra la base
ya migrada. Si un paso falla, se deshace todo y la base queda como estaba.

Para cambiar el esquema de una tabla existente: agregar un paso al final de `STEPS`, idempotente (que mire qué ya
existe antes de agregarlo), y su test con una base de la versión anterior.
"""

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Connection, Engine, MetaData, Table, func, insert, inspect, select, update

from internal.database.models import Base, OrderEventRecord, OrderItemRecord

logger = logging.getLogger(__name__)

RECEIVED = "Recibido"  # el estado inicial de una orden (OrderStatus.RECEIVED); acá sin importar los esquemas


def _columns(conn: Connection, table: str) -> set[str]:
    return {column["name"] for column in inspect(conn).get_columns(table)}


def _add_column(conn: Connection, table: str, name: str, ddl: str) -> None:
    if name not in _columns(conn, table):
        conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def _total(items: list[dict]) -> Decimal:
    priced = [item for item in items if item.get("unit_price") is not None]
    return sum((Decimal(str(item["unit_price"])) * int(item["quantity"]) for item in priced), Decimal(0))


def _step_1_detalle_de_ordenes(conn: Connection) -> None:
    """0.1.1 → 1: líneas a `order_items`, total, nombre, conversación e historial conocido; se quita `orders.items`."""
    _add_column(conn, "orders", "customer_name", "VARCHAR(64)")
    _add_column(conn, "orders", "conversation_id", "INTEGER")
    _add_column(conn, "orders", "total", "VARCHAR")
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_orders_conversation_id ON orders (conversation_id)")

    if "items" not in _columns(conn, "orders"):
        return
    orders = Table("orders", MetaData(), autoload_with=conn)
    lines = OrderItemRecord.__table__
    events = OrderEventRecord.__table__
    now = datetime.now(UTC)
    expected_lines = 0
    # `orders.c.items` es un método de la colección de columnas: la columna se pide por nombre
    query = select(orders.c.id, orders.c["items"].label("lineas"), orders.c.status, orders.c.created_at)
    for row in conn.execute(query).all():
        items = json.loads(row.lineas) if isinstance(row.lineas, str) else (row.lineas or [])
        expected_lines += len(items)
        already = conn.scalar(select(func.count()).select_from(lines).where(lines.c.order_pk == row.id))
        if not already:
            for position, item in enumerate(items, start=1):
                price = item.get("unit_price")
                conn.execute(
                    insert(lines).values(
                        order_pk=row.id,
                        position=position,
                        sku=item["sku"],
                        product_name=item["product_name"],
                        presentation=item.get("presentation"),
                        quantity=int(item["quantity"]),
                        unit_price=None if price is None else Decimal(str(price)),
                        note=item.get("note"),
                    )
                )
        conn.execute(
            update(orders).where(orders.c.id == row.id, orders.c.total.is_(None)).values(total=str(_total(items)))
        )
        if not conn.scalar(select(func.count()).select_from(events).where(events.c.order_pk == row.id)):
            conn.execute(
                insert(events).values(
                    order_pk=row.id, from_status=None, to_status=RECEIVED, source="migracion", created_at=row.created_at
                )
            )
            if row.status != RECEIVED:
                conn.execute(
                    insert(events).values(
                        order_pk=row.id, from_status=RECEIVED, to_status=row.status, source="migracion", created_at=now
                    )
                )

    copied = conn.scalar(select(func.count()).select_from(lines).where(lines.c.order_pk.in_(select(orders.c.id))))
    if copied != expected_lines:
        raise RuntimeError(f"Migración 1: se esperaban {expected_lines} líneas en order_items y hay {copied}")
    conn.exec_driver_sql("ALTER TABLE orders DROP COLUMN items")


def _step_2_lineas_para_preparar(conn: Connection) -> None:
    """1 → 2: marca, categoría, coincidencia y renglón original en cada línea; origen y lo que quedó afuera.

    Solo agrega columnas que aceptan nulos: las órdenes que ya estaban no cambian. `order_leftovers` es una tabla
    nueva y la crea `create_all`.
    """
    for name, ddl in (("brand", "VARCHAR"), ("category", "VARCHAR"), ("match", "VARCHAR"), ("source_text", "TEXT")):
        _add_column(conn, "order_items", name, ddl)
    _add_column(conn, "orders", "source", "VARCHAR")
    _add_column(conn, "drafts", "leftovers", "JSON")


STEPS: list[tuple[int, Callable[[Connection], None]]] = [
    (1, _step_1_detalle_de_ordenes),
    (2, _step_2_lineas_para_preparar),
]
LATEST = STEPS[-1][0]


def _current_version(conn: Connection, tables: set[str]) -> int:
    if "schema_version" in tables:
        version = conn.exec_driver_sql("SELECT MAX(version) FROM schema_version").scalar()
        if version is not None:
            return version
    # Sin versión: con `orders` es una base de la 0.1.1; sin tablas, una base nueva que create_all deja al día
    return 0 if "orders" in tables else LATEST


def migrate(engine: Engine) -> int:
    """Crea las tablas que falten y corre los pasos pendientes. Devuelve la versión en la que queda la base."""
    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        tables = set(inspect(conn).get_table_names())
        current = _current_version(conn, tables)
        if current > LATEST:
            logger.warning("Base en la versión %d, más nueva que este código (%d): no se toca", current, LATEST)
            conn.rollback()
            return current

        Base.metadata.create_all(conn)  # tablas nuevas; no cambia las existentes
        conn.exec_driver_sql("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        for version, step in STEPS:
            if version > current:
                logger.info("Base: aplicando la migración %d (%s)", version, step.__name__)
                step(conn)
        if current != LATEST or "schema_version" not in tables:
            conn.exec_driver_sql("DELETE FROM schema_version")
            conn.exec_driver_sql(f"INSERT INTO schema_version (version) VALUES ({LATEST})")
        conn.commit()
        return LATEST
