"""Conexión a la base interna (SQLite por defecto, configurable con DATABASE_URL)."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config import get_settings

engine = create_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False)


def get_session() -> Iterator[Session]:
    """Dependencia de FastAPI: `session: Session = Depends(get_session)`."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
