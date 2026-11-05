"""Aísla los tests del entorno de quien los corre: sin su .env, sin su base y sin red.

Lo de arriba de todo corre al importar este archivo, antes de que pytest importe `internal`:
`internal.database.connection` crea el engine al importarse y ya tiene que ver la base temporal.
"""

import os
import socket
import tempfile
from pathlib import Path

import pytest

from internal.config import Settings, get_settings

# Un secreto exportado en la terminal (o un .env de desarrollo) cambiaría el resultado de los tests
for campo in Settings.model_fields:
    os.environ.pop(campo.upper(), None)
Settings.model_config["env_file"] = None

DATA_DIR = Path(tempfile.mkdtemp(prefix="norma-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{DATA_DIR / 'norma.db'}"
os.environ["CHECKPOINT_DB"] = str(DATA_DIR / "checkpoints.db")
get_settings.cache_clear()


@pytest.fixture(autouse=True)
def sin_red(monkeypatch):
    """Un test que sale a internet depende de un servicio externo: falla en vez de pasar por casualidad."""

    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex

    def conectar(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) and len(address) > 0 else address
        if host in ("127.0.0.1", "localhost", "::1"):
            return orig_connect(self, address, *args, **kwargs)
        raise RuntimeError(f"Los tests no usan red (intento de conexión a {address})")

    def conectar_ex(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) and len(address) > 0 else address
        if host in ("127.0.0.1", "localhost", "::1"):
            return orig_connect_ex(self, address, *args, **kwargs)
        raise RuntimeError(f"Los tests no usan red (intento de conexión a {address})")

    monkeypatch.setattr(socket.socket, "connect", conectar)
    monkeypatch.setattr(socket.socket, "connect_ex", conectar_ex)
