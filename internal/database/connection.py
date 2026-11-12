"""Conexión a la base interna (SQLite por defecto, configurable con DATABASE_URL)."""

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from internal.config import get_settings
from internal.database import migrations


def make_engine(url: str) -> Engine:
    """Engine con las claves foráneas activas en SQLite: sin eso, borrar una orden deja sus líneas sueltas."""
    new_engine = create_engine(url)
    if new_engine.dialect.name == "sqlite":

        @event.listens_for(new_engine, "connect")
        def _foreign_keys(dbapi_connection, _record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.close()

    return new_engine


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False)


def get_session() -> Iterator[Session]:
    """Dependencia de FastAPI: `session: Session = Depends(get_session)`."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db(target: Engine | None = None) -> int:
    """Crea la carpeta de la base, las tablas que falten y aplica las migraciones pendientes (RF-49)."""
    target = target or engine
    if target.url.get_backend_name() == "sqlite" and target.url.database:
        Path(target.url.database).parent.mkdir(parents=True, exist_ok=True)
    return migrations.migrate(target)
