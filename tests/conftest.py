"""Aísla los tests del entorno de quien los corre: sin su .env, sin su base y sin red.

Lo de arriba de todo corre al importar este archivo, antes de que pytest importe `backend`:
`backend.database.connection` crea el engine al importarse y ya tiene que ver la base temporal.
"""

import os
import socket
import tempfile
from pathlib import Path

import pytest

from backend.config import Settings, get_settings

# Un secreto exportado en la terminal (o un .env de desarrollo) cambiaría el resultado de los tests
for campo in Settings.model_fields:
    os.environ.pop(campo.upper(), None)
Settings.model_config["env_file"] = None

DATA_DIR = Path(tempfile.mkdtemp(prefix="norma-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{DATA_DIR / 'norma.db'}"
get_settings.cache_clear()


@pytest.fixture(autouse=True)
def sin_red(monkeypatch):
    """Un test que sale a internet depende de un servicio externo: falla en vez de pasar por casualidad."""

    def conectar(self, address, *args, **kwargs):
        raise RuntimeError(f"Los tests no usan red (intento de conexión a {address})")

    monkeypatch.setattr(socket.socket, "connect", conectar)
    monkeypatch.setattr(socket.socket, "connect_ex", conectar)
